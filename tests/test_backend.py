"""平台后端选择与接口一致性测试。"""
import pytest
from v2box.core import backend


def test_all_backends_share_interface():
    for cls in (backend.SystemdBackend, backend.LaunchdBackend,
                backend.WindowsBackend, backend.UnsupportedBackend):
        b = cls()
        for m in ("status", "start", "stop", "restart", "enable",
                  "disable", "supports_reload", "reload",
                  "install_watchdog", "uninstall_watchdog"):
            assert callable(getattr(b, m)), f"{cls.__name__}.{m} missing"


def test_get_backend_linux():
    import sys
    if sys.platform != "linux":
        pytest.skip("linux only")
    b = backend.get_backend()
    assert b.name in ("systemd", "unsupported")


def test_unsupported_backend_no_crash():
    b = backend.UnsupportedBackend()
    assert b.start()[0] is False
    assert b.restart()[0] is False
    assert b.install_watchdog("x")[0] is False


def test_detect_platform_and_config_path():
    p = backend.detect_platform()
    assert p in ("linux", "macos", "windows") or p
    path = backend.default_config_path()
    assert path.name == "config.json"


# ── Windows 适配 ──────────────────────────────────────────────

def test_windows_config_path_progdata_admin(monkeypatch):
    """Windows 管理员 → ProgramData 路径。"""
    monkeypatch.setattr(backend, "detect_platform", lambda: "windows")
    monkeypatch.setattr(backend, "is_admin", lambda: True)
    monkeypatch.setenv("ProgramData", r"C:\ProgramData")
    p = backend.default_config_path()
    assert str(p).replace("\\", "/").endswith("ProgramData/sing-box/config.json")


def test_windows_config_path_fallback_user(monkeypatch, tmp_path):
    """Windows 非管理员且 exe 目录不可写 → 用户目录兜底。"""
    monkeypatch.setattr(backend, "detect_platform", lambda: "windows")
    monkeypatch.setattr(backend, "is_admin", lambda: False)
    monkeypatch.setenv("ProgramData", str(tmp_path / "pd"))
    monkeypatch.setattr(backend.shutil, "which", lambda n: None)
    monkeypatch.setattr(backend.Path, "home", classmethod(lambda cls: tmp_path))
    p = backend.default_config_path()
    assert str(p).replace("\\", "/").endswith(".config/sing-box/config.json")


def test_windows_install_service_cmd(monkeypatch):
    """install_service 生成 ONSTART+SYSTEM 计划任务（sing-box 无 SCM 协议，不能用 sc）。"""
    calls = []
    monkeypatch.setattr(backend, "_run",
                        lambda cmd, **k: calls.append(cmd)
                        or backend.subprocess.CompletedProcess(cmd, 0, "ok", ""))
    monkeypatch.setattr(backend.shutil, "which",
                        lambda n: r"C:\tools\sing-box.exe")
    monkeypatch.setattr(backend, "is_admin", lambda: True)
    b = backend.WindowsBackend()
    ok, _ = b.install_service(r"C:\ProgramData\sing-box\config.json")
    assert ok
    cmd = calls[0]
    assert cmd[:4] == ["schtasks", "/create", "/f", "/tn"]
    assert "onstart" in cmd and "SYSTEM" in cmd
    tr = cmd[cmd.index("/tr") + 1]
    assert "sing-box.exe" in tr and "run -c" in tr and "config.json" in tr


def test_windows_uninstall_service_cmd(monkeypatch):
    calls = []
    monkeypatch.setattr(backend, "_run",
                        lambda cmd, **k: calls.append(cmd)
                        or backend.subprocess.CompletedProcess(cmd, 0, "ok", ""))
    monkeypatch.setattr(backend, "is_admin", lambda: True)
    monkeypatch.setattr(backend.WindowsBackend, "stop",
                        lambda self: (True, ""))
    b = backend.WindowsBackend()
    ok, _ = b.uninstall_service()
    assert ok
    assert calls[0][:4] == ["schtasks", "/delete", "/f", "/tn"]


def test_windows_status_parses_task_state(monkeypatch):
    """status 解析 schtasks csv 输出的 Running 状态。"""
    csv = '"RUNNER","sing-box","N/A","Running"\r\n'
    monkeypatch.setattr(backend, "_run",
                        lambda cmd, **k: backend.subprocess.CompletedProcess(
                            cmd, 0, csv, ""))
    b = backend.WindowsBackend()
    running, _ = b.status()
    assert running is True


def test_windows_status_not_installed(monkeypatch):
    monkeypatch.setattr(backend, "_run",
                        lambda cmd, **k: backend.subprocess.CompletedProcess(
                            cmd, 1, "", "ERROR"))
    b = backend.WindowsBackend()
    running, _ = b.status()
    assert running is False


def test_systemd_install_service_writes_unit(monkeypatch):
    """systemd 后端 install_service 写入标准 unit 文件。"""
    calls = []
    written = {}

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["sudo", "tee"]:
            written["unit"] = kw["input"]
            written["target"] = str(cmd[2])
        return backend.subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(backend, "_run", fake_run)
    monkeypatch.setattr(backend.shutil, "which",
                        lambda n: "/usr/bin/sing-box" if n == "sing-box" else n)
    b = backend.SystemdBackend()
    ok, msg = b.install_service("/etc/sing-box/config.json")
    assert ok, msg
    assert written["target"].endswith("sing-box.service")
    assert "sing-box" in written["unit"]
    assert "run -c /etc/sing-box/config.json" in written["unit"]
    assert ["sudo", "systemctl", "enable", "--now", "sing-box"] in calls
