"""服务端 sing-box 配置生成器 — 支持 VLESS+Reality 和 VLESS+WS 方案。"""

import json
import subprocess
import secrets


def _generate_uuid() -> str:
    """生成 UUID，优先使用 sing-box，回退到 Python 标准库。"""
    try:
        result = subprocess.run(
            ["sing-box", "generate", "uuid"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    import uuid
    return str(uuid.uuid4())


def _generate_reality_keypair() -> tuple[str, str]:
    """生成 Reality 密钥对，返回 (private_key, public_key)。"""
    result = subprocess.run(
        ["sing-box", "generate", "reality-keypair"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(f"生成 Reality 密钥对失败: {result.stderr.strip()}")
    private_key = ""
    public_key = ""
    for line in result.stdout.strip().splitlines():
        if "PrivateKey" in line:
            private_key = line.split(":")[-1].strip()
        elif "PublicKey" in line:
            public_key = line.split(":")[-1].strip()
    if not private_key or not public_key:
        raise RuntimeError(f"解析 Reality 密钥对失败: {result.stdout}")
    return private_key, public_key


def _generate_short_id() -> str:
    """生成 Reality short_id（8 位十六进制）。"""
    return secrets.token_hex(4)


def create_vless_reality(
    name: str,
    port: int = 443,
    sni: str = "www.microsoft.com",
    users: list[dict] | None = None,
) -> dict:
    """创建 VLESS+Reality 服务端配置。

    Returns:
        包含 server_config, client_outbound, meta 的字典
    """
    uuid = _generate_uuid()
    private_key, public_key = _generate_reality_keypair()
    short_id = _generate_short_id()

    if not users:
        users = [{"name": "default", "uuid": uuid, "flow": "xtls-rprx-vision"}]

    server_config = {
        "log": {"level": "info"},
        "inbounds": [
            {
                "type": "vless",
                "tag": "vless-reality-in",
                "listen": "::",
                "listen_port": port,
                "users": users,
                "tls": {
                    "enabled": True,
                    "server_name": sni,
                    "reality": {
                        "enabled": True,
                        "handshake": {
                            "server": sni,
                            "server_port": 443,
                        },
                        "private_key": private_key,
                        "short_id": [short_id],
                        "max_time_difference": "1m",
                    },
                },
            }
        ],
        "outbounds": [{"type": "direct", "tag": "direct"}],
    }

    # 客户端 outbound 模板（server_ip 需要后续填入）
    client_outbound = {
        "type": "vless",
        "tag": name,
        "server": "{server_ip}",
        "server_port": port,
        "uuid": uuid,
        "flow": "xtls-rprx-vision",
        "tls": {
            "enabled": True,
            "server_name": sni,
            "utls": {"enabled": True, "fingerprint": "chrome"},
            "reality": {
                "enabled": True,
                "public_key": public_key,
                "short_id": short_id,
            },
        },
    }

    meta = {
        "name": name,
        "type": "vless-reality",
        "port": port,
        "sni": sni,
        "uuid": uuid,
        "private_key": private_key,
        "public_key": public_key,
        "short_id": short_id,
    }

    return {
        "server_config": server_config,
        "client_outbound": client_outbound,
        "meta": meta,
    }


def create_vless_ws(
    name: str,
    listen_port: int = 10001,
    ws_path: str = "/vless-ws",
    users: list[dict] | None = None,
) -> dict:
    """创建 VLESS+WS 服务端配置（不含 TLS，由 nginx 反代）。

    Returns:
        包含 server_config, client_outbound, meta, nginx_snippet 的字典
    """
    uuid = _generate_uuid()

    if not users:
        users = [{"name": "default", "uuid": uuid}]

    # 确保 path 以 / 开头
    if not ws_path.startswith("/"):
        ws_path = "/" + ws_path

    server_config = {
        "log": {"level": "info"},
        "inbounds": [
            {
                "type": "vless",
                "tag": "vless-ws-in",
                "listen": "127.0.0.1",
                "listen_port": listen_port,
                "users": users,
                "transport": {
                    "type": "ws",
                    "path": ws_path,
                },
            }
        ],
        "outbounds": [{"type": "direct", "tag": "direct"}],
    }

    # 客户端 outbound 模板
    client_outbound = {
        "type": "vless",
        "tag": name,
        "server": "{domain}",
        "server_port": 443,
        "uuid": uuid,
        "tls": {
            "enabled": True,
            "server_name": "{domain}",
            "utls": {"enabled": True, "fingerprint": "chrome"},
        },
        "transport": {
            "type": "ws",
            "path": ws_path,
        },
    }

    # nginx 配置片段
    nginx_snippet = f"""\
# sing-box VLESS+WS reverse proxy
# 添加到 nginx server {{ }} 块中（443 HTTPS）

location {ws_path} {{
    proxy_pass http://127.0.0.1:{listen_port};
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
}}"""

    meta = {
        "name": name,
        "type": "vless-ws",
        "listen_port": listen_port,
        "ws_path": ws_path,
        "uuid": uuid,
    }

    return {
        "server_config": server_config,
        "client_outbound": client_outbound,
        "meta": meta,
        "nginx_snippet": nginx_snippet,
    }


def _generate_password(length: int = 16) -> str:
    """生成随机密码。"""
    import string
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_socks(
    name: str,
    listen_port: int = 10808,
    username: str | None = None,
    password: str | None = None,
) -> dict:
    """创建 SOCKS5 服务端配置（适合 FRP 内网穿透场景）。

    Returns:
        包含 server_config, client_outbound, meta, frp_snippet 的字典
    """
    if not username:
        username = "v2box"
    if not password:
        password = _generate_password()

    server_config = {
        "log": {"level": "info"},
        "inbounds": [
            {
                "type": "socks",
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "listen_port": listen_port,
                "users": [
                    {
                        "username": username,
                        "password": password,
                    }
                ],
            }
        ],
        "outbounds": [{"type": "direct", "tag": "direct"}],
    }

    # 客户端 outbound 模板
    client_outbound = {
        "type": "socks",
        "tag": name,
        "server": "{server_ip}",
        "server_port": "{frp_remote_port}",
        "version": "5",
        "username": username,
        "password": password,
    }

    # FRP 配置片段（TOML 格式，frpc.toml）
    frp_snippet = f"""\
# FRP 客户端配置片段（添加到 frpc.toml）
# 将容器内的 SOCKS5 端口穿透到 FRP 服务器

[[proxies]]
name = "{name}"
type = "tcp"
localIP = "127.0.0.1"
localPort = {listen_port}
remotePort = 0    # 设为 0 由服务端自动分配，或指定固定端口如 16808"""

    meta = {
        "name": name,
        "type": "socks",
        "listen_port": listen_port,
        "username": username,
        "password": password,
    }

    return {
        "server_config": server_config,
        "client_outbound": client_outbound,
        "meta": meta,
        "frp_snippet": frp_snippet,
    }


def server_config_to_json(config: dict) -> str:
    """将服务端配置转为格式化 JSON。"""
    return json.dumps(config, indent=2, ensure_ascii=False)
