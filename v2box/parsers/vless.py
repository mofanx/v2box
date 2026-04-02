"""VLESS 链接解析器"""

from urllib.parse import parse_qs, unquote


def parse_vless(link: str) -> dict | None:
    """解析 vless:// 链接，返回 sing-box outbound 字典。"""
    link = link.strip()
    if not link.lower().startswith("vless://"):
        return None

    # 提取 fragment 作为节点名称
    tag = ""
    if "#" in link:
        link, tag = link.rsplit("#", 1)
        tag = unquote(tag)

    rest = link[len("vless://"):]

    user_info, _, host_and_params = rest.partition("@")
    uuid = user_info

    host_port, _, query_string = host_and_params.partition("?")
    server, _, port_str = host_port.rpartition(":")
    port = int(port_str)

    params = parse_qs(query_string, keep_blank_values=True)

    def get(key, default=""):
        vals = params.get(key, [default])
        non_empty = [v for v in vals if v] or [default]
        return non_empty[-1]

    security = get("security")
    node_type = get("type", "tcp")
    flow = get("flow")
    sni = get("sni")
    fp = get("fp", "chrome")

    outbound = {
        "type": "vless",
        "tag": tag or f"vless-{server}",
        "server": server,
        "server_port": port,
        "uuid": uuid,
    }

    # VLESS + Reality
    if security == "reality":
        pbk = get("pbk")
        sid = get("sid")
        if flow:
            outbound["flow"] = flow
        outbound["tls"] = {
            "enabled": True,
            "server_name": sni,
            "reality": {
                "enabled": True,
                "public_key": pbk,
                "short_id": sid,
            },
            "utls": {"enabled": True, "fingerprint": fp},
        }
    # VLESS + TLS
    elif security == "tls":
        host = get("host")
        outbound["tls"] = {
            "enabled": True,
            "server_name": host or sni or server,
        }

    # gRPC 传输层
    if node_type == "grpc":
        service_name = get("serviceName")
        outbound["transport"] = {
            "type": "grpc",
            "service_name": service_name,
        }
    # WS 传输层
    elif node_type == "ws":
        path = unquote(get("path", "/"))
        transport = {"type": "ws"}
        if "?ed=" in path:
            real_path, _, ed_value = path.partition("?ed=")
            transport["path"] = real_path or "/"
            try:
                transport["max_early_data"] = int(ed_value)
            except ValueError:
                transport["max_early_data"] = 2048
            transport["early_data_header_name"] = "Sec-WebSocket-Protocol"
        else:
            transport["path"] = path
        host = get("host")
        if host and host != server:
            transport["headers"] = {"Host": host}
        outbound["transport"] = transport
    # HTTP 传输层
    elif node_type == "http":
        path = unquote(get("path", "/"))
        host = get("host")
        transport = {"type": "http", "path": path}
        if host:
            transport["host"] = [host]
        outbound["transport"] = transport

    return outbound
