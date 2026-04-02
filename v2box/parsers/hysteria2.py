"""Hysteria2 链接解析器"""

from urllib.parse import parse_qs, unquote


def parse_hysteria2(link: str) -> dict | None:
    """解析 hysteria2:// 或 hy2:// 链接，返回 sing-box outbound 字典。"""
    link = link.strip()
    lower = link.lower()
    if lower.startswith("hysteria2://"):
        rest = link[len("hysteria2://"):]
    elif lower.startswith("hy2://"):
        rest = link[len("hy2://"):]
    else:
        return None

    # 提取 fragment
    tag = ""
    if "#" in rest:
        rest, tag = rest.rsplit("#", 1)
        tag = unquote(tag)

    password, _, host_and_params = rest.partition("@")
    host_port, _, query_string = host_and_params.partition("?")
    server, _, port_str = host_port.rpartition(":")
    port = int(port_str)

    params = parse_qs(query_string, keep_blank_values=True)

    def get(key, default=""):
        vals = params.get(key, [default])
        non_empty = [v for v in vals if v] or [default]
        return non_empty[-1]

    sni = get("sni")
    insecure = get("insecure", "0")
    obfs = get("obfs")
    obfs_password = get("obfs-password")

    outbound = {
        "type": "hysteria2",
        "tag": tag or f"hy2-{server}",
        "server": server,
        "server_port": port,
        "password": password,
        "tls": {
            "enabled": True,
            "server_name": sni or server,
        },
    }

    if insecure == "1":
        outbound["tls"]["insecure"] = True

    if obfs and obfs_password:
        outbound["obfs"] = {
            "type": obfs,
            "password": obfs_password,
        }

    return outbound
