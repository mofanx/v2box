"""客户端连接链接生成器 — 从 outbound 字典生成分享链接。"""

import base64
from urllib.parse import quote, urlencode


def build_vless_link(outbound: dict) -> str:
    """从 VLESS outbound 字典生成 vless:// 链接。"""
    uuid = outbound["uuid"]
    server = outbound["server"]
    port = outbound["server_port"]
    tag = outbound.get("tag", "vless")

    params = {}

    # flow
    flow = outbound.get("flow")
    if flow:
        params["flow"] = flow

    # TLS
    tls = outbound.get("tls", {})
    if tls.get("enabled"):
        sni = tls.get("server_name", server)

        # Reality
        reality = tls.get("reality", {})
        if reality.get("enabled"):
            params["security"] = "reality"
            params["sni"] = sni
            params["pbk"] = reality["public_key"]
            params["sid"] = reality.get("short_id", "")
            # uTLS fingerprint
            utls = tls.get("utls", {})
            if utls.get("fingerprint"):
                params["fp"] = utls["fingerprint"]
        else:
            params["security"] = "tls"
            params["sni"] = sni
            utls = tls.get("utls", {})
            if utls.get("fingerprint"):
                params["fp"] = utls["fingerprint"]

    # Transport
    transport = outbound.get("transport", {})
    transport_type = transport.get("type")
    if transport_type:
        params["type"] = transport_type
        if transport_type == "ws":
            path = transport.get("path", "/")
            params["path"] = path
            headers = transport.get("headers", {})
            host = headers.get("Host")
            if host:
                params["host"] = host
        elif transport_type == "grpc":
            service_name = transport.get("service_name")
            if service_name:
                params["serviceName"] = service_name
    else:
        params["type"] = "tcp"

    fragment = quote(tag, safe="")
    query = urlencode(params, safe="/:@")

    return f"vless://{uuid}@{server}:{port}?{query}#{fragment}"


def build_socks_link(outbound: dict) -> str:
    """从 SOCKS outbound 字典生成 socks:// 链接。

    格式: socks://base64(user:pass)@host:port#tag
    """
    server = outbound["server"]
    port = outbound["server_port"]
    tag = outbound.get("tag", "socks")

    username = outbound.get("username", "")
    password = outbound.get("password", "")

    fragment = quote(str(tag), safe="")

    if username:
        cred = f"{username}:{password}"
        encoded = base64.urlsafe_b64encode(cred.encode("utf-8")).decode("ascii").rstrip("=")
        return f"socks://{encoded}@{server}:{port}#{fragment}"
    else:
        return f"socks://{server}:{port}#{fragment}"
