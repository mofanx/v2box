"""配置生成与硬化回归测试。"""
import sys
import pytest

from v2box.core.config import (build_config, update_nodes_in_config,
                               _tun_inbound, _dns_rules,
                               LOCAL_DOMAIN_SUFFIXES)

NODES = [
    {"type": "vless", "tag": "n1", "server": "a.example.com", "server_port": 443},
    {"type": "vless", "tag": "n2", "server": "1.2.3.4", "server_port": 443},
]


def test_build_config_basic():
    cfg = build_config(NODES)
    assert cfg["route"]["final"] == "proxy"
    assert cfg["log"]["level"] == "warn"
    tags = [o["tag"] for o in cfg["outbounds"]]
    assert tags[0] == "proxy" and tags[1] == "auto" and "direct" in tags
    for rs in cfg["route"]["rule_set"]:
        assert rs["update_interval"] == "24h"


def test_dns_rules_local_suffix_and_node_domains():
    cfg = build_config(NODES)
    rules = cfg["dns"]["rules"]
    assert rules[0]["action"] == "reject"
    assert set(rules[0]["domain_suffix"]) == set(LOCAL_DOMAIN_SUFFIXES)
    # 节点域名（跳过 IP）走 dns-direct
    node_rule = [r for r in rules if r.get("server") == "dns-direct"
                 and "domain_suffix" in r]
    assert node_rule and "a.example.com" in node_rule[0]["domain_suffix"]
    assert "1.2.3.4" not in str(node_rule)


def test_tun_inbound_platform_fields():
    linux_tun = _tun_inbound(platform_name="linux")
    assert linux_tun["stack"] == "system"
    assert linux_tun["strict_route"] is True
    assert linux_tun["auto_redirect"] is True

    mac_tun = _tun_inbound(platform_name="macos")
    assert "strict_route" not in mac_tun
    assert "auto_redirect" not in mac_tun
    assert mac_tun["stack"] == "system"

    win_tun = _tun_inbound(platform_name="windows")
    assert "strict_route" not in win_tun


def test_strict_route_option():
    tun_on = _tun_inbound(platform_name="linux", strict_route=True)
    tun_off = _tun_inbound(platform_name="linux", strict_route=False)
    assert tun_on["strict_route"] is True
    assert tun_off["strict_route"] is False


def test_harden_user_config():
    # 模拟旧版生成的用户配置（mixed 栈、info 日志、无 update_interval、带 detour）
    uc = {
        "log": {"level": "info"},
        "inbounds": [
            {"type": "mixed", "listen": "127.0.0.1", "listen_port": 10808},
            {"type": "tun", "stack": "mixed", "strict_route": True,
             "auto_redirect": True},
        ],
        "dns": {"servers": [
            {"tag": "dns-proxy", "type": "https", "server": "8.8.8.8",
             "detour": "proxy"},
            {"tag": "dns-direct", "type": "udp", "server": "223.5.5.5",
             "detour": "direct"},
        ], "rules": [{"rule_set": "geosite-cn", "server": "dns-direct"}]},
        "route": {"rule_set": [
            {"tag": "geoip-cn", "type": "remote", "url": "x"},
        ]},
    }
    out = update_nodes_in_config(uc, NODES)
    tun = [i for i in out["inbounds"] if i["type"] == "tun"][0]
    assert tun["stack"] == "system"
    if sys.platform == "linux":
        assert tun["strict_route"] is True
    else:
        assert "strict_route" not in tun
    assert out["log"]["level"] == "warn"
    dns_direct = [s for s in out["dns"]["servers"] if s["tag"] == "dns-direct"][0]
    assert "detour" not in dns_direct  # detour: direct 运行期 FATAL，必须删除
    assert out["route"]["rule_set"][0]["update_interval"] == "24h"
    assert out["dns"]["rules"][0]["action"] == "reject"


def test_strict_route_override_on_update():
    uc = {"inbounds": [{"type": "tun", "stack": "system", "strict_route": True}],
          "dns": {"servers": [], "rules": []}, "route": {"rule_set": []}}
    out = update_nodes_in_config(uc, NODES, strict_route=False)
    tun = [i for i in out["inbounds"] if i["type"] == "tun"][0]
    if sys.platform == "linux":
        assert tun["strict_route"] is False
