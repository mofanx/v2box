"""updater 跨平台升级逻辑测试 — 资产选择 / 解压 / 管理员检测 / 安装路径。"""
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from v2box.core import backend, updater


# ── 资产选择 ──────────────────────────────────────────────────

ASSETS = [
    {"name": "sing-box-1.14.5-linux-amd64.tar.gz",
     "browser_download_url": "https://x/linux-amd64.tar.gz"},
    {"name": "sing-box-1.14.5-linux-arm64.tar.gz",
     "browser_download_url": "https://x/linux-arm64.tar.gz"},
    {"name": "sing-box-1.14.5-windows-amd64.zip",
     "browser_download_url": "https://x/windows-amd64.zip"},
    {"name": "sing-box-1.14.5-windows-arm64.zip",
     "browser_download_url": "https://x/windows-arm64.zip"},
    {"name": "sing-box-1.14.5-darwin-amd64.tar.gz",
     "browser_download_url": "https://x/darwin-amd64.tar.gz"},
    {"name": "sing-box-1.14.5-darwin-arm64.tar.gz",
     "browser_download_url": "https://x/darwin-arm64.tar.gz"},
]


@pytest.mark.parametrize("plat,machine,want", [
    ("linux", "x86_64", "linux-amd64.tar.gz"),
    ("linux", "aarch64", "linux-arm64.tar.gz"),
    ("windows", "AMD64", "windows-amd64.zip"),
    ("windows", "ARM64", "windows-arm64.zip"),
    ("macos", "x86_64", "darwin-amd64.tar.gz"),
    ("macos", "arm64", "darwin-arm64.tar.gz"),
])
def test_release_suffix(plat, machine, want):
    assert updater._release_suffix(plat, machine) == want


def test_select_asset_windows_picks_zip():
    url = updater._select_asset(ASSETS, "windows", "AMD64")
    assert url.endswith("windows-amd64.zip")


def test_select_asset_missing_returns_empty():
    # freebsd 不在 release 资产里（非 amd64/arm64 兜底仍找不到匹配）
    assert updater._select_asset(ASSETS, "freebsd", "x86_64") == ""


def test_latest_release_uses_platform(monkeypatch):
    payload = json.dumps({"tag_name": "v1.14.5", "assets": ASSETS}).encode()
    monkeypatch.setattr(updater, "_get", lambda *a, **k: payload)
    monkeypatch.setattr(updater.platform, "system", lambda: "Windows")
    monkeypatch.setattr(updater.platform, "machine", lambda: "AMD64")
    tag, url = updater.latest_release()
    assert tag == "v1.14.5"
    assert url.endswith("windows-amd64.zip")


# ── 解压 ─────────────────────────────────────────────────────

def _make_targz(path: Path, inner_name: str):
    with tarfile.open(path, "w:gz") as tf:
        data = b"#!/bin/sh\necho sing-box\n"
        ti = tarfile.TarInfo(f"sing-box-1.14.5-linux-amd64/{inner_name}")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))


def _make_zip(path: Path, inner_name: str):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"sing-box-1.14.5-windows-amd64/{inner_name}", b"MZ")


def test_extract_binary_tar(tmp_path):
    pkg = tmp_path / "sb.tar.gz"
    _make_targz(pkg, "sing-box")
    out = updater._extract_binary(str(pkg), str(tmp_path / "out"))
    assert Path(out).name == "sing-box"
    assert Path(out).read_bytes().startswith(b"#!/bin/sh")


def test_extract_binary_zip_windows(tmp_path):
    pkg = tmp_path / "sb.zip"
    _make_zip(pkg, "sing-box.exe")
    out = updater._extract_binary(str(pkg), str(tmp_path / "out"))
    assert Path(out).name == "sing-box.exe"


def test_extract_binary_missing_raises(tmp_path):
    pkg = tmp_path / "sb.tar.gz"
    _make_targz(pkg, "not-singbox")
    with pytest.raises(FileNotFoundError):
        updater._extract_binary(str(pkg), str(tmp_path / "out"))


# ── 安装目标与权限 ────────────────────────────────────────────

def test_is_admin_posix(monkeypatch):
    monkeypatch.setattr(backend, "detect_platform", lambda: "linux")
    # raising=False：Windows 的 os 模块无 geteuid，CI 三平台矩阵下不能硬依赖存在
    monkeypatch.setattr(backend.os, "geteuid", lambda: 0, raising=False)
    assert backend.is_admin() is True
    monkeypatch.setattr(backend.os, "geteuid", lambda: 1000, raising=False)
    assert backend.is_admin() is False


def test_is_admin_windows_nonadmin(monkeypatch):
    """Windows 非管理员返回 False（不抛异常）。"""
    monkeypatch.setattr(backend, "detect_platform", lambda: "windows")
    import sys, types
    fake_ctypes = types.SimpleNamespace(
        windll=types.SimpleNamespace(
            shell32=types.SimpleNamespace(IsUserAnAdmin=lambda: 0)))
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    assert backend.is_admin() is False


def test_install_binary_windows_no_sudo(tmp_path, monkeypatch):
    """Windows 分支不用 sudo：停服 → copy → 起服。"""
    calls = []
    monkeypatch.setattr(backend, "detect_platform", lambda: "windows")
    monkeypatch.setattr(updater, "_stop_service",
                        lambda: calls.append("stop") or True)
    monkeypatch.setattr(updater, "_start_service",
                        lambda: calls.append("start") or True)
    new_bin = tmp_path / "new" / "sing-box.exe"
    new_bin.parent.mkdir()
    new_bin.write_bytes(b"MZnew")
    exe = tmp_path / "dir" / "sing-box.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZold")
    updater._install_binary(str(new_bin), str(exe), "windows")
    assert exe.read_bytes() == b"MZnew"
    assert (tmp_path / "dir" / "sing-box.exe.bak").read_bytes() == b"MZold"
    assert calls == ["stop", "start"]


def test_install_binary_linux_uses_sudo(tmp_path, monkeypatch):
    """Linux 分支走 sudo cp/install/mv 序列。"""
    ran = []
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda cmd, **k: ran.append(cmd[1])
                        or updater.subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(updater.os, "chmod", lambda *a: None)
    new_bin = tmp_path / "sing-box"
    new_bin.write_bytes(b"bin")
    exe = tmp_path / "usr" / "bin" / "sing-box"
    updater._install_binary(str(new_bin), str(exe), "linux")
    assert ran == ["cp", "install", "mv"]


# ── 首次安装 ──────────────────────────────────────────────────

def test_install_target_posix():
    assert updater._install_target("linux") == "/usr/local/bin/sing-box"
    assert updater._install_target("macos") == "/usr/local/bin/sing-box"


def test_install_target_windows_admin(monkeypatch):
    monkeypatch.setattr(backend, "is_admin", lambda: True)
    monkeypatch.setenv("ProgramData", r"C:\ProgramData")
    t = updater._install_target("windows")
    assert t.replace("\\", "/").endswith("ProgramData/sing-box/sing-box.exe")


def test_install_target_windows_user(monkeypatch):
    monkeypatch.setattr(backend, "is_admin", lambda: False)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\u\AppData\Local")
    t = updater._install_target("windows")
    assert t.replace("\\", "/").endswith(
        "AppData/Local/Programs/sing-box/sing-box.exe")


def test_install_refuses_when_present(monkeypatch):
    """已安装时不覆盖，提示走 upgrade。"""
    monkeypatch.setattr(updater.shutil, "which", lambda n: "/usr/bin/sing-box")
    logs = []
    assert updater.install(log=logs.append) is False
    assert any("已安装" in m for m in logs)


def test_install_flow_windows_user_dir(tmp_path, monkeypatch):
    """Windows 非管理员端到端：下载 zip → 解压 → 装到 LOCALAPPDATA。"""
    monkeypatch.setattr(updater.shutil, "which", lambda n: None)
    monkeypatch.setattr(backend, "detect_platform", lambda: "windows")
    monkeypatch.setattr(backend, "is_admin", lambda: False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(updater.platform, "machine", lambda: "AMD64")
    payload = json.dumps({"tag_name": "v1.14.5", "assets": ASSETS}).encode()

    def fake_get(url, timeout=15):
        if url == updater.API:
            return payload
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("sing-box-1.14.5-windows-amd64/sing-box.exe", b"MZ")
        return buf.getvalue()

    monkeypatch.setattr(updater, "_get", fake_get)
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda cmd, **k: updater.subprocess.CompletedProcess(
                            cmd, 0, "sing-box version 1.14.5", ""))
    logs = []
    assert updater.install(log=logs.append) is True
    target = tmp_path / "Programs" / "sing-box" / "sing-box.exe"
    assert target.read_bytes() == b"MZ"
    assert any("已安装到" in m for m in logs)


def test_ensure_on_path_admin_machine_scope(tmp_path, monkeypatch):
    """Windows 管理员 → 写 Machine PATH + GITHUB_PATH 文件。"""
    calls = []
    monkeypatch.setattr(backend, "is_admin", lambda: True)
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda cmd, **k: calls.append(cmd)
                        or updater.subprocess.CompletedProcess(cmd, 0, "", ""))
    gh_path = tmp_path / "github_path"
    monkeypatch.setenv("GITHUB_PATH", str(gh_path))
    logs = []
    updater._ensure_on_path(r"C:\ProgramData\sing-box", "windows", log=logs.append)
    ps = next(c for c in calls if c[0] == "powershell")
    assert "Machine" in ps[-1] and "ProgramData" in ps[-1]
    assert "sing-box" in gh_path.read_text()


def test_ensure_on_path_nonadmin_user_scope(monkeypatch):
    monkeypatch.setattr(backend, "is_admin", lambda: False)
    calls = []
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda cmd, **k: calls.append(cmd)
                        or updater.subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.delenv("GITHUB_PATH", raising=False)
    updater._ensure_on_path(r"C:\u\AppData\Local\Programs\sing-box", "windows")
    ps = next(c for c in calls if c[0] == "powershell")
    assert '"User"' in ps[-1]


def test_get_sends_auth_when_token_env(monkeypatch):
    """GH_TOKEN/GITHUB_TOKEN 存在时 _get 带 Authorization 头（CI 限流规避）。"""
    captured = {}

    class FakeResp:
        def read(self):
            return b"{}"
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return FakeResp()

    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("GH_TOKEN", "tok123")
    updater._get("https://api.github.com/x")
    assert captured["headers"].get("Authorization") == "Bearer tok123"

    captured.clear()
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    updater._get("https://api.github.com/x")
    assert "Authorization" not in captured["headers"]


def test_ensure_on_path_skips_posix(monkeypatch):
    calls = []
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda cmd, **k: calls.append(cmd)
                        or updater.subprocess.CompletedProcess(cmd, 0, "", ""))
    updater._ensure_on_path("/usr/local/bin", "linux")
    assert calls == []


def test_upgrade_refuses_scoop_shim(monkeypatch):
    """scoop 的 sing-box.exe 是 shim 壳——直接覆盖会破坏 scoop 管理，拒绝并提示。"""
    monkeypatch.setattr(updater.shutil, "which",
                        lambda n: r"C:\Users\u\scoop\shims\sing-box.exe")
    monkeypatch.setattr(backend, "detect_platform", lambda: "windows")
    monkeypatch.setattr(backend, "is_admin", lambda: True)
    logs = []
    assert updater.upgrade(log=logs.append) is False
    assert any("scoop" in m.lower() for m in logs)


def test_resolve_scoop_shim(tmp_path):
    """shim 路径解析到 apps\\sing-box\\current\\ 真实二进制；真身不存在则原样返回。"""
    shim = tmp_path / "scoop" / "shims" / "sing-box.exe"
    real = tmp_path / "scoop" / "apps" / "sing-box" / "current" / "sing-box.exe"
    shim.parent.mkdir(parents=True)
    shim.write_bytes(b"shim")
    real.parent.mkdir(parents=True)
    real.write_bytes(b"real")
    assert backend._resolve_binary(str(shim)) == str(real)
    # 真身缺失 → 原样
    real.unlink()
    assert backend._resolve_binary(str(shim)) == str(shim)
    # 非 scoop 路径 → 原样
    assert backend._resolve_binary(r"C:\tools\sing-box.exe") == r"C:\tools\sing-box.exe"


def test_install_flow_linux_sudo_install(tmp_path, monkeypatch):
    """Linux 端到端：下载 tar.gz → sudo install 到 /usr/local/bin。"""
    monkeypatch.setattr(updater.shutil, "which", lambda n: None)
    monkeypatch.setattr(backend, "detect_platform", lambda: "linux")
    monkeypatch.setattr(updater.platform, "machine", lambda: "x86_64")
    payload = json.dumps({"tag_name": "v1.14.5", "assets": ASSETS}).encode()

    def fake_get(url, timeout=15):
        if url == updater.API:
            return payload
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            data = b"#!/bin/sh\n"
            ti = tarfile.TarInfo("sing-box-1.14.5-linux-amd64/sing-box")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        return buf.getvalue()

    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        return updater.subprocess.CompletedProcess(
            cmd, 0, "sing-box version 1.14.5", "")

    monkeypatch.setattr(updater, "_get", fake_get)
    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    logs = []
    assert updater.install(log=logs.append) is True
    install_cmd = next(c for c in ran if c[:2] == ["sudo", "install"])
    assert install_cmd[-1] == "/usr/local/bin/sing-box"
    assert any("已安装到" in m for m in logs)
