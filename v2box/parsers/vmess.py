"""VMess 链接解析器"""

import base64
import json


def parse_vmess(link: str) -> dict | None:
    """解析 vmess:// 链接（v2rayN 格式），返回 sing-box outbound 字典。"""
    link = link.strip()
    if not link.lower().startswith("vmess://"):
        return None

    encoded = link[len("vmess://"):]
    # 补齐 base64 padding
    padding = 4 - len(encoded) % 4
    if padding != 4:
        encoded += "=" * padding

    try:
        raw = base64.b64decode(encoded).decode("utf-8")
        info = json.loads(raw)
    except Exception:
        return None

    server = info.get("add", "")
    port = int(info.get("port", 0))
    uuid = info.get("id", "")
    aid = int(info.get("aid", 0))
    tag = info.get("ps", "") or f"vmess-{server}"
    net = info.get("net", "tcp")
    tls_val = info.get("tls", "")
    sni = info.get("sni", "")
    host = info.get("host", "")
    path = info.get("path", "")

    outbound = {
        "type": "vmess",
        "tag": tag,
        "server": server,
        "server_port": port,
        "uuid": uuid,
        "alter_id": aid,
        "security": "auto",
    }

    # TLS
    if tls_val == "tls":
        outbound["tls"] = {
            "enabled": True,
            "server_name": sni or host or server,
        }

    # 传输层
    if net == "ws":
        transport = {"type": "ws", "path": path or "/"}
        if host and host != server:
            transport["headers"] = {"Host": host}
        outbound["transport"] = transport
    elif net == "grpc":
        service_name = info.get("path", "") or info.get("serviceName", "")
        outbound["transport"] = {
            "type": "grpc",
            "service_name": service_name,
        }
    elif net == "h2":
        transport = {"type": "http", "path": path or "/"}
        if host:
            transport["host"] = [host]
        outbound["transport"] = transport

    return outbound
