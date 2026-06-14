import asyncio
import hashlib
import json
import os
import ssl
import threading
from urllib.parse import unquote, urlparse


SUPPORTED_PROXY_SCHEMES = {
    "socks5": "socks5",
    "socks5h": "socks5",
    "socks4": "socks4",
    "http": "http",
}


def parse_telegram_proxy_url(proxy_url):
    raw = str(proxy_url or "").strip()
    if not raw:
        return None

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    proxy_type = SUPPORTED_PROXY_SCHEMES.get(scheme)
    if not proxy_type:
        raise ValueError("代理只支持 socks5/socks4/http，Trojan/Vmess/Vless 需要先转成本地 SOCKS5")
    if not parsed.hostname or not parsed.port:
        raise ValueError("代理地址格式应为 socks5://host:port 或 http://host:port")

    proxy = {
        "proxy_type": proxy_type,
        "addr": parsed.hostname,
        "port": int(parsed.port),
        "rdns": scheme in ("socks5", "socks5h", "socks4"),
    }
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy


def telegram_proxy_client_kwargs(proxy_url=None):
    proxy = parse_telegram_proxy_url(proxy_url if proxy_url is not None else os.environ.get("TG_PROXY_URL", ""))
    return {"proxy": proxy} if proxy else {}


def proxy_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "开", "开启", "启用")


def parse_trojan_node_json(raw_node):
    if isinstance(raw_node, dict):
        node = dict(raw_node)
    else:
        raw_text = str(raw_node or "").strip()
        if not raw_text:
            raise ValueError("Trojan JSON 不能为空")
        try:
            node = json.loads(raw_text)
        except Exception as exc:
            raise ValueError(f"Trojan JSON 格式不正确: {exc}") from exc

    node_type = str(node.get("type") or "").strip().lower()
    if node_type != "trojan":
        raise ValueError("当前只支持 Trojan 节点 JSON")

    server = str(node.get("host") or node.get("server") or node.get("address") or "").strip()
    port_raw = node.get("port") or node.get("server_port")
    password = str(node.get("password") or "").strip()
    if not server:
        raise ValueError("Trojan JSON 缺少 host/server")
    try:
        port = int(port_raw)
    except Exception as exc:
        raise ValueError("Trojan JSON 端口不正确") from exc
    if port <= 0 or port > 65535:
        raise ValueError("Trojan JSON 端口不正确")
    if not password:
        raise ValueError("Trojan JSON 缺少 password")

    sni = str(node.get("sni") or node.get("peer") or node.get("servername") or server).strip()
    title = str(node.get("title") or node.get("name") or node.get("tag") or "Trojan 节点").strip()
    alpn_raw = node.get("alpn")
    if isinstance(alpn_raw, str):
        alpn = [item.strip() for item in alpn_raw.replace("|", ",").split(",") if item.strip()]
    elif isinstance(alpn_raw, (list, tuple)):
        alpn = [str(item).strip() for item in alpn_raw if str(item).strip()]
    else:
        alpn = []

    return {
        "title": title,
        "server": server,
        "port": port,
        "password": password,
        "sni": sni,
        "alpn": alpn,
        "insecure": proxy_bool(node.get("allowInsecure") or node.get("insecure")),
    }


def trojan_node_summary(raw_node):
    node = parse_trojan_node_json(raw_node)
    return f"{node['title']} | {node['server']}:{node['port']}"


def build_sing_box_config_from_trojan_json(raw_node, listen_host="127.0.0.1", listen_port=1080):
    node = parse_trojan_node_json(raw_node)
    tls = {
        "enabled": True,
        "server_name": node["sni"],
        "insecure": node["insecure"],
    }
    if node["alpn"]:
        tls["alpn"] = node["alpn"]

    return {
        "log": {"level": os.environ.get("SING_BOX_LOG_LEVEL", "warn")},
        "inbounds": [
            {
                "type": "socks",
                "tag": "telegram-socks-in",
                "listen": listen_host,
                "listen_port": int(listen_port),
            }
        ],
        "outbounds": [
            {
                "type": "trojan",
                "tag": "telegram-trojan-out",
                "server": node["server"],
                "server_port": node["port"],
                "password": node["password"],
                "tls": tls,
            }
        ],
        "route": {"final": "telegram-trojan-out"},
    }


class TrojanSocksProxyRuntime:
    def __init__(self, raw_node, listen_host="127.0.0.1", listen_port=1080, logger=None):
        self.node = parse_trojan_node_json(raw_node)
        self.listen_host = listen_host
        self.listen_port = int(listen_port)
        self.logger = logger
        self.loop = None
        self.server = None
        self.thread = None
        self.ready = threading.Event()
        self.error = None

    def log(self, message):
        if self.logger:
            self.logger.info(message)

    def start(self, timeout=5):
        if self.thread and self.thread.is_alive():
            return True
        self.thread = threading.Thread(target=self._thread_main, name="telegram-trojan-socks", daemon=True)
        self.thread.start()
        self.ready.wait(timeout)
        if self.error:
            raise self.error
        return bool(self.server)

    def stop(self):
        if not self.loop:
            return
        loop = self.loop
        server = self.server

        async def shutdown():
            if server:
                server.close()
                await server.wait_closed()

        try:
            future = asyncio.run_coroutine_threadsafe(shutdown(), loop)
            future.result(timeout=3)
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass

    def _thread_main(self):
        loop = asyncio.new_event_loop()
        self.loop = loop
        asyncio.set_event_loop(loop)
        try:
            self.server = loop.run_until_complete(asyncio.start_server(self.handle_client, self.listen_host, self.listen_port))
            self.ready.set()
            loop.run_forever()
        except Exception as exc:
            self.error = exc
            self.ready.set()
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def handle_client(self, reader, writer):
        remote_reader = None
        remote_writer = None
        try:
            trojan_request = await self._read_socks_request(reader, writer)
            remote_reader, remote_writer = await self._open_trojan_connection(trojan_request)
            writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            await asyncio.gather(
                self._relay(reader, remote_writer),
                self._relay(remote_reader, writer),
                return_exceptions=True,
            )
        except Exception:
            try:
                writer.write(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
                await writer.drain()
            except Exception:
                pass
        finally:
            for item in (remote_writer, writer):
                if item:
                    try:
                        item.close()
                        await item.wait_closed()
                    except Exception:
                        pass

    async def _read_socks_request(self, reader, writer):
        header = await reader.readexactly(2)
        if header[0] != 5:
            raise ValueError("SOCKS version must be 5")
        methods = await reader.readexactly(header[1])
        if 0 not in methods:
            writer.write(b"\x05\xff")
            await writer.drain()
            raise ValueError("SOCKS no-auth method is required")
        writer.write(b"\x05\x00")
        await writer.drain()

        req = await reader.readexactly(4)
        version, cmd, _reserved, atyp = req
        if version != 5 or cmd != 1:
            raise ValueError("Only SOCKS5 CONNECT is supported")
        if atyp == 1:
            address = await reader.readexactly(4)
        elif atyp == 3:
            length = await reader.readexactly(1)
            address = length + await reader.readexactly(length[0])
        elif atyp == 4:
            address = await reader.readexactly(16)
        else:
            raise ValueError("Unknown SOCKS address type")
        port = await reader.readexactly(2)
        return bytes([cmd, atyp]) + address + port + b"\r\n"

    async def _open_trojan_connection(self, trojan_request):
        context = ssl.create_default_context()
        if self.node["insecure"]:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        if self.node["alpn"]:
            context.set_alpn_protocols(self.node["alpn"])

        remote_reader, remote_writer = await asyncio.open_connection(
            self.node["server"],
            self.node["port"],
            ssl=context,
            server_hostname=self.node["sni"] or self.node["server"],
        )
        password_hash = hashlib.sha224(self.node["password"].encode("utf-8")).hexdigest().encode("ascii")
        remote_writer.write(password_hash + b"\r\n" + trojan_request)
        await remote_writer.drain()
        return remote_reader, remote_writer

    async def _relay(self, reader, writer):
        try:
            while True:
                data = await reader.read(32768)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        finally:
            try:
                writer.close()
            except Exception:
                pass
