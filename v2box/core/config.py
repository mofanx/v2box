"""sing-box 配置生成器 — 根据节点列表和当前模式生成完整配置。"""
import json
from pathlib import Path
import os
from typing import Any
import copy

# 默认 sing-box 配置路径
SINGBOX_CONFIG_PATH = Path("/etc/sing-box/config.json")
MIXED_LISTEN_PORT = 10808
CLASH_API_PORT = 9090
CLASH_API_SECRET = "v2box"

# 数据目录
DATA_DIR = Path(os.environ.get("V2BOX_DATA_DIR", Path.home() / ".config" / "v2box"))
USER_CONFIG_PATH = DATA_DIR / "config.json"

# 默认 sing-box 配置路径
SINGBOX_CONFIG_PATH = Path("/etc/sing-box/config.json")
MIXED_LISTEN_PORT = 10808
CLASH_API_PORT = 9090
CLASH_API_SECRET = "v2box"


def build_config(outbounds: list[dict], mode: str = "auto",
                 selected: str | None = None, lan: bool = False,
                 port: int = MIXED_LISTEN_PORT,
                 download_detour: str | None = None) -> dict:
    """根据节点列表生成完整 sing-box 配置。

    Args:
        outbounds: sing-box outbound 列表
        mode: "auto" 或 "manual"
        selected: manual 模式下选中的节点 tag
        lan: 是否开启局域网代理（监听 0.0.0.0）
        port: 代理监听端口
    """
    # 清理内部字段，不写入 sing-box 配置
    outbounds = [{k: v for k, v in o.items() if not k.startswith("_")} for o in outbounds]
    tags = [o["tag"] for o in outbounds]

    # 规则集下载路径：默认 direct 避免启动时依赖 proxy 造成循环依赖；
    # 手动模式下优先用用户选中的节点，确保能穿过 GFW 拉取规则集。
    if download_detour:
        detour = download_detour
    elif mode == "manual" and selected and selected in tags:
        detour = selected
    else:
        detour = "direct"

    # urltest 自动选择组
    urltest_group = {
        "type": "urltest",
        "tag": "auto",
        "outbounds": tags,
        "url": "https://cp.cloudflare.com/generate_204",
        "interval": "5m",
        "tolerance": 50,
    }

    # selector 手动选择组
    default_out = "auto"
    if mode == "manual" and selected and selected in tags:
        default_out = selected

    selector_group = {
        "type": "selector",
        "tag": "proxy",
        "outbounds": ["auto"] + tags,
        "default": default_out,
    }

    all_outbounds = [
        selector_group,
        urltest_group,
        *outbounds,
        {"type": "direct", "tag": "direct"},
    ]

    listen_addr = "0.0.0.0" if lan else "127.0.0.1"

    config = {
        "log": {"level": "info"},
        "dns": {
            "servers": [
                {
                    "tag": "dns-proxy",
                    "type": "https",
                    "server": "8.8.8.8",
                    "detour": "proxy",
                },
                {
                    "tag": "dns-direct",
                    "type": "udp",
                    "server": "223.5.5.5",
                },
            ],
            "rules": [
                {"rule_set": "geosite-cn", "server": "dns-direct"},
            ],
            "final": "dns-proxy",
            "strategy": "ipv4_only",
            "independent_cache": True,
        },
        "inbounds": [
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": listen_addr,
                "listen_port": port,
            },
            {
                "type": "tun",
                "tag": "tun-in",
                "address": ["172.19.0.1/30", "fdfe:dcba:9876::1/126"],
                "auto_route": True,
                "auto_redirect": True,
                "strict_route": True,
                "stack": "mixed",
            },
        ],
        "outbounds": all_outbounds,
        "route": {
            "auto_detect_interface": True,
            "default_domain_resolver": {"server": "dns-direct"},
            "rules": [
                {"action": "sniff"},
                {"protocol": "dns", "action": "hijack-dns"},
                {"protocol": "quic", "action": "reject"},
                {"ip_is_private": True, "outbound": "direct"},
                {"rule_set": "geosite-cn", "outbound": "direct"},
                {"rule_set": "geoip-cn", "outbound": "direct"},
            ],
            "rule_set": [
                {
                    "tag": "geoip-cn",
                    "type": "remote",
                    "format": "binary",
                    "url": "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-cn.srs",
                    "download_detour": detour,
                },
                {
                    "tag": "geosite-cn",
                    "type": "remote",
                    "format": "binary",
                    "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-cn.srs",
                    "download_detour": detour,
                },
            ],
            "final": "proxy",
        },
        "experimental": {
            "cache_file": {
                "enabled": True,
            },
            "clash_api": {
                "external_controller": f"127.0.0.1:{CLASH_API_PORT}",
                "secret": CLASH_API_SECRET,
            },
        },
    }
    return config


def write_config(config: dict, path: Path | None = None) -> Path:
    """将配置写入文件，返回写入路径。"""
    target = path or SINGBOX_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2, ensure_ascii=False), "utf-8")
    return target


def config_to_json(config: dict) -> str:
    """将配置转为格式化 JSON 字符串。"""
    return json.dumps(config, indent=2, ensure_ascii=False)

def load_user_config() -> dict | None:
    if not USER_CONFIG_PATH.exists():
        return None
    try:
        return json.loads(USER_CONFIG_PATH.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

def save_user_config(config: dict) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USER_CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), "utf-8")
    return USER_CONFIG_PATH

def update_nodes_in_config(user_config: dict, nodes: list[dict], mode: str = "auto", selected: str | None = None, lan: bool = False, port: int = MIXED_LISTEN_PORT, download_detour: str | None = None) -> dict:
    outbounds = [{k: v for k, v in o.items() if not k.startswith("_")} for o in nodes]
    tags = [o["tag"] for o in outbounds]
    if download_detour:
        detour = download_detour
    elif mode == "manual" and selected and selected in tags:
        detour = selected
    else:
        detour = "direct"
    urltest_group = {"type": "urltest", "tag": "auto", "outbounds": tags, "url": "https://cp.cloudflare.com/generate_204", "interval": "5m", "tolerance": 50}
    default_out = "auto"
    if mode == "manual" and selected and selected in tags:
        default_out = selected
    selector_group = {"type": "selector", "tag": "proxy", "outbounds": ["auto"] + tags, "default": default_out}
    user_config["outbounds"] = [selector_group, urltest_group, *outbounds, {"type": "direct", "tag": "direct"}]
    if "inbounds" not in user_config:
        listen_addr = "0.0.0.0" if lan else "127.0.0.1"
        user_config["inbounds"] = [{"type": "mixed", "tag": "mixed-in", "listen": listen_addr, "listen_port": port}, {"type": "tun", "tag": "tun-in", "address": ["172.19.0.1/30", "fdfe:dcba:9876::1/126"], "auto_route": True, "auto_redirect": True, "strict_route": True, "stack": "mixed"}]
    else:
        for inbound in user_config["inbounds"]:
            if inbound.get("type") == "mixed":
                inbound["listen"] = "0.0.0.0" if lan else "127.0.0.1"
                inbound["listen_port"] = port
    if "route" in user_config and "rule_set" in user_config["route"]:
        for rule_set in user_config["route"]["rule_set"]:
            if rule_set.get("type") == "remote":
                rule_set["download_detour"] = detour
    return user_config

def apply_config(nodes: list[dict], mode: str = "auto", selected: str | None = None, lan: bool = False, port: int = MIXED_LISTEN_PORT, download_detour: str | None = None) -> dict:
    user_config = load_user_config()
    if user_config is None:
        config = build_config(nodes, mode=mode, selected=selected, lan=lan, port=port, download_detour=download_detour)
        save_user_config(config)
    else:
        config = update_nodes_in_config(user_config, nodes, mode=mode, selected=selected, lan=lan, port=port, download_detour=download_detour)
        save_user_config(config)
    return config

def reset_user_config() -> Path:
    if USER_CONFIG_PATH.exists():
        USER_CONFIG_PATH.unlink()
    return USER_CONFIG_PATH
