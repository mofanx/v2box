"""watchdog 状态机回归测试 — mock service/health 驱动各判定分支。

运行: python -m unittest discover tests -v
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from v2box.core import watchdog, health, service


def _report(verdict, dns_ms=10.0, node_ms=100, api=True, active=True):
    return {"verdict": verdict, "service_active": active, "clash_api": api,
            "dns_direct_ms": dns_ms, "node_delay_ms": node_ms}


class WatchdogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["V2BOX_WATCHDOG_STATE"] = str(
            Path(self.tmp.name) / "watchdog.json")
        self.calls = {"reload": 0, "restart": 0}
        self._patches = [
            patch.object(watchdog, "CONFIRM_GAP_SEC", 0),
            patch.object(watchdog, "physical_link_up", return_value=True),
            patch.object(service, "supports_reload", return_value=True),
            patch.object(service, "reload",
                         side_effect=lambda: self._bump("reload")),
            patch.object(service, "restart",
                         side_effect=lambda: self._bump("restart")),
        ]
        for p in self._patches:
            p.start()

    def _bump(self, name):
        self.calls[name] += 1
        return True, f"{name} ok"

    def tearDown(self):
        for p in self._patches:
            p.stop()
        os.environ.pop("V2BOX_WATCHDOG_STATE", None)
        self.tmp.cleanup()

    def _verdict(self, v):
        return patch.object(health, "diagnose", return_value=_report(v))

    def _reset_repair_ts(self):
        st = watchdog.load_state()
        st["last_repair_ts"] = 0
        watchdog._save_state(st)

    def test_healthy_resets(self):
        with self._verdict(health.HEALTHY):
            r = watchdog.check_once()
        self.assertEqual(r["verdict"], health.HEALTHY)
        self.assertEqual(r["action"], "none")
        self.assertEqual(watchdog.load_state()["fail_count"], 0)

    def test_stopped_and_nodes_skip_repair(self):
        for v in (health.STOPPED, health.NODES_DEAD):
            with self._verdict(v):
                r = watchdog.check_once()
            self.assertNotIn(r["action"], ("reload", "restart"),
                             f"{v} 不应触发修复")
        self.assertEqual(self.calls, {"reload": 0, "restart": 0})

    def test_escalation_reload_then_restart(self):
        # FAIL_THRESHOLD=2：连失 2 轮 → reload；仍死 → 升级 restart
        with self._verdict(health.UNDERLAY_DEAD):
            r1 = watchdog.check_once()
            self.assertEqual(r1["action"], "wait")
            r2 = watchdog.check_once()
            self.assertEqual(r2["action"], "reload")
            self.assertEqual(self.calls["reload"], 1)

            self._reset_repair_ts()  # 模拟冷却已过
            watchdog.check_once()    # fail_count 1 → wait
            r4 = watchdog.check_once()  # fail_count 2 → restart
            self.assertEqual(r4["action"], "restart")
            self.assertEqual(self.calls["restart"], 1)

    def test_dry_run_never_repairs(self):
        with self._verdict(health.UNDERLAY_DEAD):
            watchdog.check_once()
            r = watchdog.check_once(dry_run=True)
        self.assertEqual(r["action"], "would-reload")
        self.assertEqual(self.calls, {"reload": 0, "restart": 0})

    def test_no_physical_link_skips(self):
        with patch.object(watchdog, "physical_link_up", return_value=False), \
             self._verdict(health.UNDERLAY_DEAD):
            watchdog.check_once()
            r = watchdog.check_once()
        self.assertEqual(r["action"], "skip")

    def test_restart_cooldown(self):
        with self._verdict(health.UNDERLAY_DEAD):
            watchdog.check_once()
            watchdog.check_once()          # reload
            self._reset_repair_ts()
            watchdog.check_once()
            watchdog.check_once()          # restart → fail_count 复位
            r = watchdog.check_once()      # 再故障也要重新累计
            self.assertEqual(r["action"], "wait")


class NethookScriptTest(unittest.TestCase):
    def test_hook_script_syntax(self):
        import subprocess
        r = subprocess.run(["sh", "-n"], input=watchdog._HOOK_SCRIPT,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
