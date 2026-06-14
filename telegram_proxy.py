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
        raise ValueError("代理只支持 socks5/socks4/http")
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
