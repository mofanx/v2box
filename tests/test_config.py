"""config.py 配置硬化回归测试。

运行: python -m unittest discover tests -v
"""
import unittest
from unittest.mock import patch

from v2box.core import config

NODE = {"type": "vless", "tag": "n1", "server": "real.aibix.top", "server_port": 443}


class TunInboundTest(unittest.TestCase):
    def test_linux_fields(self):
        tun = config._tun_inbound("linux")
        self.assertEqual(tun["stack"], "system")
        self.assertTrue(tun["auto_redirect"])
        self.assertTrue(tun["strict_route"])

    def test_nonlinux_strips_linux_only_fields(self):
        for p in ("macos", "windows"):
            tun = config._tun_inbound(p)
            self.assertEqual(tun["stack"], "system")
            self.assertNotIn("auto_redirect", tun)
            self.assertNotIn("strict_route", tun)


class DnsRulesTest(unittest.TestCase):
    def test_reject_local_first(self):
        rules = config._dns_rules()
        self.assertEqual(rules[0]["action"], "reject")
        self.assertIn("lan", rules[0]["domain_suffix"])

    def test_node_domains_direct(self):
        rules = config._dns_rules(["real.aibix.top"])
        hit = any(r.get("server") == "dns-direct"
                  and "real.aibix.top" in r.get("domain_suffix", [])
                  for r in rules)
        self.assertTrue(hit)


class HardenUserConfigTest(unittest.TestCase):
    def _user_cfg(self):
        return {
            "inbounds": [{"type": "tun", "tag": "tun-in",
                          "stack": "mixed", "auto_redirect": True,
                          "strict_route": True}],
            "dns": {"servers": [{"tag": "dns-direct", "type": "udp",
                                 "server": "223.5.5.5",
                                 "detour": "direct"}],
                    "rules": []},
            "route": {"rule_set": [{"tag": "geosite-cn", "type": "remote",
                                    "format": "binary", "url": "u"}]},
        }

    def test_harden(self):
        cfg = self._user_cfg()
        with patch.object(config, "detect_platform", return_value="linux"):
            config._harden_user_config(cfg, [NODE])
        self.assertEqual(cfg["inbounds"][0]["stack"], "system")
        # detour: direct 会让 sing-box FATAL，必须删除（实测结论）
        self.assertNotIn("detour", cfg["dns"]["servers"][0])
        self.assertEqual(cfg["route"]["rule_set"][0]["update_interval"], "24h")
        rules = cfg["dns"]["rules"]
        self.assertTrue(any(r.get("action") == "reject" for r in rules))
        self.assertTrue(any("real.aibix.top" in r.get("domain_suffix", [])
                            for r in rules))

    def test_harden_strips_tun_fields_on_macos(self):
        cfg = self._user_cfg()
        with patch.object(config, "detect_platform", return_value="macos"):
            config._harden_user_config(cfg, [NODE])
        tun = cfg["inbounds"][0]
        self.assertNotIn("auto_redirect", tun)
        self.assertNotIn("strict_route", tun)


class BuildConfigTest(unittest.TestCase):
    def test_structure(self):
        cfg = config.build_config([dict(NODE)])
        self.assertEqual(cfg["log"]["level"], "warn")
        self.assertEqual(cfg["outbounds"][0]["tag"], "proxy")
        self.assertEqual(cfg["outbounds"][1]["tag"], "auto")
        self.assertTrue(cfg["outbounds"][1]["interrupt_exist_connections"])
        self.assertEqual(cfg["outbounds"][-1]["tag"], "direct")
        for rs in cfg["route"]["rule_set"]:
            self.assertEqual(rs["update_interval"], "24h")
        self.assertIn("tun-in", [i["tag"] for i in cfg["inbounds"]])


if __name__ == "__main__":
    unittest.main()
