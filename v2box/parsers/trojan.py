"""Trojan 链接解析器"""

from urllib.parse import parse_qs, unquote


def parse_trojan(link: str) -> dict | None:
    """解析 trojan:// 链接，返回 sing-box outbound 字典。"""
    link = link.strip()
    if not link.lower().startswith("trojan://"):
        return None

    # 提取 fragment
    tag = ""
    if "#" in link:
        link, tag = link.rsplit("#", 1)
        tag = unquote(tag)

    rest = link[len("trojan://"):]

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
    net_type = get("type", "tcp")
    host = get("host")

    outbound = {
        "type": "trojan",
        "tag": tag or f"trojan-{server}",
        "server": server,
        "server_port": port,
        "password": password,
        "tls": {
            "enabled": True,
            "server_name": sni or host or server,
        },
    }

    # 传输层
    if net_type == "ws":
        path = unquote(get("path", "/"))
        transport = {"type": "ws", "path": path}
        if host and host != server:
            transport["headers"] = {"Host": host}
        outbound["transport"] = transport
    elif net_type == "grpc":
        service_name = get("serviceName")
        outbound["transport"] = {
            "type": "grpc",
            "service_name": service_name,
        }

    return outbound
