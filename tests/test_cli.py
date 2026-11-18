"""CLI 入口层测试 — Windows 控制台编码修正。"""
import sys


def test_fix_windows_console_encoding_nonwin32_noop():
    """非 Windows 平台调用是 no-op，不抛异常。"""
    from v2box.cli.main import _fix_windows_console_encoding
    if sys.platform == "win32":
        import pytest
        pytest.skip("非 win32 分支断言")
    _fix_windows_console_encoding()


def test_fix_windows_console_encoding_reconfigures(monkeypatch):
    """win32 分支会尝试 reconfigure stdout/stderr 为 utf-8。"""
    from v2box.cli import main
    calls = []

    class FakeStream:
        def reconfigure(self, **kw):
            calls.append(kw)

    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.setattr(main.sys, "stdout", FakeStream())
    monkeypatch.setattr(main.sys, "stderr", FakeStream())
    main._fix_windows_console_encoding()
    assert calls == [{"encoding": "utf-8", "errors": "replace"}] * 2


def test_fix_windows_console_encoding_survives_bad_stream(monkeypatch):
    """流不支持 reconfigure（如已关闭/自定义流）时静默降级。"""
    from v2box.cli import main
    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.setattr(main.sys, "stdout", object())
    monkeypatch.setattr(main.sys, "stderr", object())
    main._fix_windows_console_encoding()  # 不抛异常
