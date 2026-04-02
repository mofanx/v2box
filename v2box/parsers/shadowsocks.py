"""Shadowsocks (ss://) 链接解析器"""

import base64
from urllib.parse import urlparse, unquote


def _b64_decode(s: str) -> str:
    """兼容 URL-safe 和标准 base64 解码。"""
    s = s.replace("-", "+").replace("_", "/")
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.b64decode(s).decode("utf-8")


def parse_ss(link: str) -> dict | None:
    """解析 ss:// 链接，返回 sing-box outbound 字典。

    支持两种常见格式:
      1. ss://base64(method:password)@server:port#tag
      2. ss://base64(method:password@server:port)#tag  (SIP002)
    """
    link = link.strip()
    if not link.lower().startswith("ss://"):
        return None

    # 提取 fragment
    tag = ""
    if "#" in link:
        link, tag = link.rsplit("#", 1)
        tag = unquote(tag)

    body = link[len("ss://"):]

    method = password = server = ""
    port = 0

    if "@" in body:
        # 格式 1: base64(method:password)@server:port
        user_part, _, host_part = body.rpartition("@")
        try:
            decoded = _b64_decode(user_part)
        except Exception:
            decoded = user_part
        if ":" in decoded:
            method, _, password = decoded.partition(":")
        server, _, port_str = host_part.rpartition(":")
        port = int(port_str)
    else:
        # 格式 2: 整体 base64
        try:
            decoded = _b64_decode(body)
        except Exception:
            return None
        # method:password@server:port
        if "@" in decoded:
            user_part, _, host_part = decoded.rpartition("@")
            method, _, password = user_part.partition(":")
            server, _, port_str = host_part.rpartition(":")
            port = int(port_str)
        else:
            return None

    outbound = {
        "type": "shadowsocks",
        "tag": tag or f"ss-{server}",
        "server": server,
        "server_port": port,
        "method": method,
        "password": password,
    }
    return outbound
