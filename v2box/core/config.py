"""sing-box 配置生成器 — 根据节点列表和当前模式生成完整配置。"""

import json
from pathlib import Path

# 默认 sing-box 配置路径
SINGBOX_CONFIG_PATH = Path("/etc/sing-box/config.json")
MIXED_LISTEN_PORT = 10808
CLASH_API_PORT = 9090
CLASH_API_SECRET = "v2box"


def build_config(outbounds: list[dict], mode: str = "auto",
                 selected: str | None = None, lan: bool = False,
                 port: int = MIXED_LISTEN_PORT) -> dict:
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
                    "download_detour": "proxy",
                },
                {
                    "tag": "geosite-cn",
                    "type": "remote",
                    "format": "binary",
                    "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-cn.srs",
                    "download_detour": "proxy",
                },
            ],
            "final": "proxy",
        },
        "experimental": {
            "clash_api": {
                "external_controller": f"127.0.0.1:{CLASH_API_PORT}",
                "secret": CLASH_API_SECRET,
            }
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
