"""看门狗状态机回归测试（mock 后端与探针，不触碰真实服务）。"""
import pytest

import v2box.core.health as health
import v2box.core.watchdog as watchdog


class FakeClock:
    now = 0.0


@pytest.fixture()
def wd(monkeypatch, tmp_path):
    """隔离的 watchdog 环境：状态文件、时钟、后端全 mock。"""
    monkeypatch.setattr(watchdog, "_state_path", lambda: tmp_path / "watchdog.json")
    monkeypatch.setattr(watchdog, "CONFIRM_GAP_SEC", 0)
    monkeypatch.setattr(watchdog, "physical_link_up", lambda: True)
    monkeypatch.setattr(watchdog.time, "sleep", lambda s: None)

    calls = []
    monkeypatch.setattr(watchdog.service, "reload",
                        lambda: (calls.append("reload") or (True, "ok")))
    monkeypatch.setattr(watchdog.service, "restart",
                        lambda: (calls.append("restart") or (True, "ok")))
    monkeypatch.setattr(watchdog.service, "supports_reload", lambda: True)
    monkeypatch.setattr(watchdog.service, "status", lambda: (True, ""))

    def set_verdict(v, **kw):
        rep = {"verdict": v, "service_active": True, "clash_api": True,
               "dns_direct_ms": kw.get("dns", 5.0),
               "node_delay_ms": kw.get("node", 300)}
        monkeypatch.setattr(watchdog.health, "diagnose", lambda d: rep)

    return calls, set_verdict


def test_healthy_no_action(wd):
    calls, set_verdict = wd
    set_verdict(health.HEALTHY)
    r = watchdog.check_once()
    assert r["verdict"] == health.HEALTHY and r["action"] == "none" and not calls


def test_dead_escalates_reload_then_restart(wd):
    calls, set_verdict = wd
    set_verdict(health.UNDERLAY_DEAD, dns=None)
    r1 = watchdog.check_once()
    assert r1["action"] == "wait" and r1["fail_count"] == 1 and not calls
    r2 = watchdog.check_once()
    assert r2["action"] == "reload" and calls == ["reload"]
    # 仍未恢复 → 下次升级 restart（绕过冷却）
    state = watchdog.load_state()
    state["last_repair_ts"] = 0
    watchdog._save_state(state)
    r3 = watchdog.check_once()
    assert r3["action"] == "wait" or r3["action"] == "restart"
    if r3["action"] == "wait":
        state = watchdog.load_state(); state["last_repair_ts"] = 0
        watchdog._save_state(state)
        r4 = watchdog.check_once()
        assert r4["action"] == "restart"
    assert "restart" in calls


def test_dry_run_never_touches_backend(wd):
    calls, set_verdict = wd
    set_verdict(health.UNDERLAY_DEAD, dns=None)
    watchdog.check_once(dry_run=True)
    r = watchdog.check_once(dry_run=True)
    assert r["action"].startswith("would-") and not calls


def test_no_link_skips_repair(wd, monkeypatch):
    calls, set_verdict = wd
    set_verdict(health.UNDERLAY_DEAD, dns=None)
    monkeypatch.setattr(watchdog, "physical_link_up", lambda: False)
    watchdog.check_once()
    r = watchdog.check_once()
    assert r["action"] == "skip" and not calls


def test_stopped_respects_manual_stop(wd):
    calls, set_verdict = wd
    set_verdict(health.STOPPED, dns=None)
    r = watchdog.check_once()
    assert r["action"] == "skip" and r["fail_count"] == 0 and not calls


def test_nodes_dead_never_repairs(wd):
    calls, set_verdict = wd
    set_verdict(health.NODES_DEAD, dns=5.0)
    r = watchdog.check_once()
    assert r["action"] == "none" and not calls


def test_repair_rate_limit(wd, monkeypatch):
    calls, set_verdict = wd
    set_verdict(health.UNDERLAY_DEAD, dns=None)
    state = watchdog.load_state()
    state["repairs"] = [watchdog.time.time()] * 6
    watchdog._save_state(state)
    watchdog.check_once()
    r = watchdog.check_once()
    assert r["action"] in ("throttled", "cooldown")
