import json
import os
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
