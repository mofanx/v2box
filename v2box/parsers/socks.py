"""SOCKS5 链接解析器

链接格式:
  socks://base64(user:pass)@host:port#tag     （有认证）
  socks://host:port#tag                       （无认证）
  socks5://...                                （同上，别名）
"""

import base64
from urllib.parse import unquote


def parse_socks(link: str) -> dict | None:
    """解析 socks:// 或 socks5:// 链接，返回 sing-box outbound 字典。"""
    link = link.strip()
    lower = link.lower()
    if lower.startswith("socks5://"):
        rest = link[len("socks5://"):]
    elif lower.startswith("socks://"):
        rest = link[len("socks://"):]
    else:
        return None

    # 提取 fragment（节点名）
    tag = ""
    if "#" in rest:
        rest, tag = rest.rsplit("#", 1)
        tag = unquote(tag)

    username = ""
    password = ""

    if "@" in rest:
        user_part, host_part = rest.rsplit("@", 1)
        # 尝试 Base64 解码 user:pass
        try:
            decoded = base64.urlsafe_b64decode(user_part + "==").decode("utf-8")
            if ":" in decoded:
                username, password = decoded.split(":", 1)
            else:
                username = decoded
        except Exception:
            # 非 Base64，直接作为 user:pass
            if ":" in user_part:
                username, password = user_part.split(":", 1)
                username = unquote(username)
                password = unquote(password)
            else:
                username = unquote(user_part)
    else:
        host_part = rest

    server, _, port_str = host_part.rpartition(":")
    if not server or not port_str:
        return None
    # 处理 IPv6 [::1] 格式
    server = server.strip("[]")
    port = int(port_str)

    outbound = {
        "type": "socks",
        "tag": tag or f"socks-{server}:{port}",
        "server": server,
        "server_port": port,
        "version": "5",
    }

    if username:
        outbound["username"] = username
    if password:
        outbound["password"] = password

    return outbound
