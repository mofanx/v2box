"""sing-box 配置生成器 — 根据节点列表和当前模式生成完整配置。"""
import json
from pathlib import Path
import os
from typing import Any
import copy

from v2box.core.backend import default_config_path, detect_platform

# 默认 sing-box 配置路径（按平台探测，可用 V2BOX_SINGBOX_CONFIG 覆盖）
SINGBOX_CONFIG_PATH = default_config_path()
MIXED_LISTEN_PORT = 10808
CLASH_API_PORT = 9090
CLASH_API_SECRET = "v2box"

# 数据目录
DATA_DIR = Path(os.environ.get("V2BOX_DATA_DIR", Path.home() / ".config" / "v2box"))
USER_CONFIG_PATH = DATA_DIR / "config.json"

# 本地/搜索域后缀 — 这些域名不应走外部 DNS，直接 NXDOMAIN 拒绝
# （否则每个解析失败的名字都会被搜索域补全后再查一遍，白白浪费往返）
LOCAL_DOMAIN_SUFFIXES = [
    "lan", "local", "internal", "localdomain",
    "home.arpa", "localhost", "invalid", "test", "example",
]


def _node_server_domains(outbounds: list[dict]) -> list[str]:
    """收集节点服务器的域名（跳过 IP 字面量），用于显式 DNS 直连规则。"""
    import ipaddress
    domains = []
    for o in outbounds:
        server = o.get("server", "")
        if not server:
            continue
        try:
            ipaddress.ip_address(server)
        except ValueError:
            if server not in domains:
                domains.append(server)
    return domains


def _dns_rules(node_domains: list[str] | None = None,
               extra_rules: list[dict] | None = None) -> list[dict]:
    """生成 DNS 规则：本地域拒绝 → 节点域名直连 → geosite-cn → 自定义规则。"""
    rules = [
        {"domain_suffix": LOCAL_DOMAIN_SUFFIXES,
         "action": "reject", "rcode": "NAME_ERROR"},
        {"rule_set": "geosite-cn", "server": "dns-direct"},
    ]
    if node_domains:
        rules.append({"domain_suffix": node_domains, "server": "dns-direct"})
    if extra_rules:
        rules.extend(extra_rules)
    return rules


def _tun_inbound(platform_name: str | None = None, strict_route: bool = True) -> dict:
    """TUN inbound — 按平台裁剪能力字段。

    stack 统一用 "system"：gVisor(mixed) 仅 Linux 可用且长跑有已知问题；
    auto_redirect(nftables) / strict_route(策略路由) 为 Linux 专属字段。
    """
    p = platform_name or detect_platform()
    tun = {
        "type": "tun",
        "tag": "tun-in",
        "address": ["172.19.0.1/30", "fdfe:dcba:9876::1/126"],
        "auto_route": True,
        "stack": "system",
    }
    if p == "linux":
        tun["auto_redirect"] = True
        tun["strict_route"] = strict_route
    return tun


def build_config(outbounds: list[dict], mode: str = "auto",
                 selected: str | None = None, lan: bool = False,
                 port: int = MIXED_LISTEN_PORT,
                 download_detour: str | None = None,
                 strict_route: bool | None = None,
                 mux: bool | None = None) -> dict:
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
    node_domains = _node_server_domains(outbounds)

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
        "interval": "2m",
        "tolerance": 50,
        # false：不掐断存量连接，新连接走更优节点 —— 避免每 5min 测速
        # 换节点时正在进行的流量被强杀（网页/视频周期性断连）
        "interrupt_exist_connections": False,
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

    node_obs = []
    for ob in outbounds:
        ob = dict(ob)
        if mux and ob.get("type") != "direct" and "multiplex" not in ob:
            ob["multiplex"] = {
                "enabled": True,
                "protocol": "smux",
                "max_connections": 8,
                "padding": False,
            }
        node_obs.append(ob)

    all_outbounds = [
        selector_group,
        urltest_group,
        *node_obs,
        {"type": "direct", "tag": "direct"},
    ]

    listen_addr = "0.0.0.0" if lan else "127.0.0.1"

    config = {
        "log": {"level": "warn"},
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
            "rules": _dns_rules(node_domains),
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
            _tun_inbound(strict_route=True if strict_route is None else strict_route),
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
                    "update_interval": "24h",
                },
                {
                    "tag": "geosite-cn",
                    "type": "remote",
                    "format": "binary",
                    "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-cn.srs",
                    "download_detour": detour,
                    "update_interval": "24h",
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

def _harden_user_config(user_config: dict, outbounds: list[dict],
                        strict_route: bool | None = None):
    """对用户自定义配置应用安全加固（保留用户自定义项的前提下）。

    - tun 栈统一为 system（mixed 的 gVisor UDP 在网卡抖动后更易僵死）
    - dns-direct 若被错误加上 detour: direct 会拒绝（sing-box 启动即 FATAL）
    - 远程规则集补 update_interval（长期运行不更新会过期）
    - DNS 规则补本地域拒绝和节点域名直连
    """
    for inbound in user_config.get("inbounds", []):
        if inbound.get("type") == "tun":
            inbound["stack"] = "system"
            if detect_platform() != "linux":
                # auto_redirect(nftables)/strict_route(策略路由) 为 Linux 专属
                inbound.pop("auto_redirect", None)
                inbound.pop("strict_route", None)
            elif strict_route is not None:
                inbound["strict_route"] = strict_route
    # 旧版默认 info 每条连接写两行日志，僵死时数万条/小时 → 降为 warn
    log = user_config.setdefault("log", {})
    if log.get("level") in (None, "info"):
        log["level"] = "warn"
    dns = user_config.get("dns", {})
    for server in dns.get("servers", []):
        if server.get("tag") == "dns-direct" and server.get("detour") == "direct":
            del server["detour"]
    dns_rules = dns.setdefault("rules", [])
    if not any(r.get("action") == "reject" for r in dns_rules):
        dns_rules.insert(0, {"domain_suffix": LOCAL_DOMAIN_SUFFIXES,
                             "action": "reject", "rcode": "NAME_ERROR"})
    node_domains = _node_server_domains(outbounds)
    if node_domains and not any("domain_suffix" in r and r.get("server") == "dns-direct"
                                and set(node_domains) & set(r["domain_suffix"])
                                for r in dns_rules):
        dns_rules.insert(1, {"domain_suffix": node_domains, "server": "dns-direct"})
    for rule_set in user_config.get("route", {}).get("rule_set", []):
        if rule_set.get("type") == "remote" and "update_interval" not in rule_set:
            rule_set["update_interval"] = "24h"


def update_nodes_in_config(user_config: dict, nodes: list[dict], mode: str = "auto", selected: str | None = None, lan: bool = False, port: int = MIXED_LISTEN_PORT, download_detour: str | None = None, strict_route: bool | None = None) -> dict:
    outbounds = [{k: v for k, v in o.items() if not k.startswith("_")} for o in nodes]
    tags = [o["tag"] for o in outbounds]
    if download_detour:
        detour = download_detour
    elif mode == "manual" and selected and selected in tags:
        detour = selected
    else:
        detour = "direct"
    urltest_group = {"type": "urltest", "tag": "auto", "outbounds": tags, "url": "https://cp.cloudflare.com/generate_204", "interval": "2m", "tolerance": 50, "interrupt_exist_connections": False}
    default_out = "auto"
    if mode == "manual" and selected and selected in tags:
        default_out = selected
    selector_group = {"type": "selector", "tag": "proxy", "outbounds": ["auto"] + tags, "default": default_out}
    user_config["outbounds"] = [selector_group, urltest_group, *outbounds, {"type": "direct", "tag": "direct"}]
    if "inbounds" not in user_config:
        listen_addr = "0.0.0.0" if lan else "127.0.0.1"
        user_config["inbounds"] = [{"type": "mixed", "tag": "mixed-in", "listen": listen_addr, "listen_port": port}, _tun_inbound()]
    else:
        for inbound in user_config["inbounds"]:
            if inbound.get("type") == "mixed":
                inbound["listen"] = "0.0.0.0" if lan else "127.0.0.1"
                inbound["listen_port"] = port
    if "route" in user_config and "rule_set" in user_config["route"]:
        for rule_set in user_config["route"]["rule_set"]:
            if rule_set.get("type") == "remote":
                rule_set["download_detour"] = detour
    _harden_user_config(user_config, outbounds, strict_route=strict_route)
    return user_config

def apply_config(nodes: list[dict], mode: str = "auto", selected: str | None = None, lan: bool = False, port: int = MIXED_LISTEN_PORT, download_detour: str | None = None, strict_route: bool | None = None, mux: bool | None = None) -> dict:
    if mux:
        _mx = {"enabled": True, "protocol": "smux", "max_connections": 8, "padding": False}
        nodes = [dict(n, multiplex=_mx)
                 if n.get("type") != "direct" and "multiplex" not in n else n
                 for n in nodes]
    user_config = load_user_config()
    if user_config is None:
        config = build_config(nodes, mode=mode, selected=selected, lan=lan, port=port, download_detour=download_detour, strict_route=strict_route)
        save_user_config(config)
    else:
        config = update_nodes_in_config(user_config, nodes, mode=mode, selected=selected, lan=lan, port=port, download_detour=download_detour, strict_route=strict_route)
        save_user_config(config)
    return config

def reset_user_config() -> Path:
    if USER_CONFIG_PATH.exists():
        USER_CONFIG_PATH.unlink()
    return USER_CONFIG_PATH
