#!/usr/bin/env python3
"""
将 vless:// 分享链接批量转换为 sing-box outbound JSON 配置。

用法:
  # 从文件读取（每行一个链接）
  python3 vless2singbox.py nodes.txt
  python3 vless2singbox.py nodes.txt --outbounds-only
  python3 vless2singbox.py nodes.txt | sudo tee /etc/sing-box/config.json
  python3 vless2singbox.py "vless://..." | sudo tee /etc/sing-box/config.json


  # 从 stdin 读取
  echo "vless://..." | python3 vless2singbox.py

  # 直接传链接作为参数
  python3 vless2singbox.py "vless://..." "vless://..."

输出: 完整的 sing-box config.json（含 selector/urltest 节点组和分流规则）
"""

import sys
import json
import os
from urllib.parse import urlparse, parse_qs, unquote


def parse_vless_link(link: str) -> dict:
    """解析单条 vless:// 链接，返回 sing-box outbound 字典。"""
    link = link.strip()
    if not link.startswith("vless://"):
        return None

    # 提取 fragment 作为节点名称
    tag = ""
    if "#" in link:
        link, tag = link.rsplit("#", 1)
        tag = unquote(tag)

    # 去掉 vless:// 前缀后解析
    rest = link[len("vless://"):]

    # uuid@server:port?params
    user_info, _, host_and_params = rest.partition("@")
    uuid = user_info

    host_port, _, query_string = host_and_params.partition("?")
    server, _, port_str = host_port.rpartition(":")
    port = int(port_str)

    params = parse_qs(query_string, keep_blank_values=True)
    # parse_qs 返回列表，取最后一个值（处理重复参数）
    def get(key, default=""):
        vals = params.get(key, [default])
        # 过滤空值，取最后一个非空值
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

    # VLESS + TLS (包括 WS+TLS)
    elif security == "tls":
        host = get("host")
        outbound["tls"] = {
            "enabled": True,
            "server_name": host or sni or server,
        }

    # WS 传输层
    if node_type == "ws":
        path = get("path", "/")
        path = unquote(path)
        transport = {
            "type": "ws",
        }
        # 解析 early data: ?ed=2048 应拆分为 max_early_data + early_data_header_name
        if "?ed=" in path:
            real_path, _, ed_value = path.partition("?ed=")
            transport["path"] = real_path or "/"
            try:
                transport["max_early_data"] = int(ed_value)
            except ValueError:
                transport["max_early_data"] = 2048
            # Xray 兼容模式，使用 Sec-WebSocket-Protocol 头传递 early data
            transport["early_data_header_name"] = "Sec-WebSocket-Protocol"
        else:
            transport["path"] = path
        # 如果 host 与 server 不同，需要设置 headers
        host = get("host")
        if host and host != server:
            transport["headers"] = {"Host": host}
        outbound["transport"] = transport

    return outbound


def build_full_config(outbounds: list) -> dict:
    """根据解析出的节点列表，生成完整 sing-box 配置。"""
    tags = [o["tag"] for o in outbounds]

    # 节点组
    urltest_group = {
        "type": "urltest",
        "tag": "auto",
        "outbounds": tags,
        "url": "https://cp.cloudflare.com/generate_204",
        "interval": "5m",
        "tolerance": 50,
    }

    selector_group = {
        "type": "selector",
        "tag": "proxy",
        "outbounds": ["auto"] + tags,
        "default": "auto",
    }

    all_outbounds = [
        selector_group,
        urltest_group,
        *outbounds,
        {"type": "direct", "tag": "direct"},
    ]

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
        },
        "inbounds": [
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": "127.0.0.1",
                "listen_port": 10808,
            }
        ],
        "outbounds": all_outbounds,
        "route": {
            "default_domain_resolver": {"server": "dns-direct"},
            "rules": [
                {"action": "sniff"},
                {"protocol": "dns", "action": "hijack-dns"},
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
                "external_controller": "127.0.0.1:9090",
                "secret": "singbox",
            }
        },
    }
    return config


def main():
    links = []

    args = sys.argv[1:]

    if not args and not sys.stdin.isatty():
        # 从 stdin 读取
        links = sys.stdin.read().strip().splitlines()
    elif args:
        for arg in args:
            if os.path.isfile(arg):
                with open(arg) as f:
                    links.extend(f.read().strip().splitlines())
            elif arg.startswith("vless://"):
                links.append(arg)
            else:
                print(f"跳过无法识别的参数: {arg}", file=sys.stderr)
    else:
        print(__doc__)
        sys.exit(0)

    # 过滤空行
    links = [l.strip() for l in links if l.strip()]

    if not links:
        print("未找到任何 vless:// 链接", file=sys.stderr)
        sys.exit(1)

    outbounds = []
    for link in links:
        ob = parse_vless_link(link)
        if ob:
            outbounds.append(ob)
            print(f"✓ 已解析: {ob['tag']}", file=sys.stderr)
        else:
            print(f"✗ 跳过非 vless 链接: {link[:50]}...", file=sys.stderr)

    if not outbounds:
        print("没有成功解析任何节点", file=sys.stderr)
        sys.exit(1)

    config = build_full_config(outbounds)

    # 输出 JSON（仅 outbounds 部分）或完整配置
    if "--outbounds-only" in sys.argv:
        print(json.dumps(outbounds, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(config, indent=2, ensure_ascii=False))

    print(f"\n共解析 {len(outbounds)} 个节点", file=sys.stderr)


if __name__ == "__main__":
    main()

