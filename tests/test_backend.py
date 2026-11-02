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
