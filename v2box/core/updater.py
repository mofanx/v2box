"""v2box 升级器：sing-box 二进制在线升级与回滚。

拉 GitHub 最新 release → 校验 → 备份旧二进制 → 替换 → 重启验证。
任何一步失败自动还原旧二进制，保证服务可用性。
"""
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

API = "https://api.github.com/repos/SagerNet/sing-box/releases/latest"


def _get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "v2box-updater"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def current_version() -> str | None:
    exe = shutil.which("sing-box")
    if not exe:
        return None
    r = subprocess.run([exe, "version"], capture_output=True, text=True)
    # 首行 "sing-box version 1.13.14" → 1.13.14（末行是 build tags，不能取）
    first = r.stdout.strip().split("\n")[0].split()
    return first[-1] if first else None


def latest_release() -> tuple[str, str] | None:
    """返回 (tag, 资产下载url)。找不到对应平台资产返回 None。"""
    try:
        data = json.loads(_get(API))
    except Exception:
        return None
    tag = data.get("tag_name", "")
    machine = platform.machine().lower()
    arch = "amd64" if machine in ("x86_64", "amd64") else "arm64"
    want = f"linux-{arch}.tar.gz"
    for a in data.get("assets", []):
        if a["name"].endswith(want):
            return tag, a["browser_download_url"]
    return tag, ""


def upgrade(log=print) -> bool:
    """执行升级。返回是否成功。"""
    exe = shutil.which("sing-box")
    if not exe:
        log("✗ 未找到 sing-box 二进制")
        return False
    cur = current_version() or "?"
    rel = latest_release()
    if not rel or not rel[1]:
        log("✗ 获取最新版本失败")
        return False
    tag, url = rel
    ver = tag.lstrip("v")
    if cur == ver:
        log(f"已是最新版本 {ver}，无需升级")
        return True
    log(f"升级 {cur} → {ver}")

    with tempfile.TemporaryDirectory() as td:
        pkg = os.path.join(td, "sb.tgz")
        try:
            open(pkg, "wb").write(_get(url, timeout=60))
            with tarfile.open(pkg) as tf:
                member = [m for m in tf.getmembers() if m.name.endswith("/sing-box") or m.name == "sing-box"][0]
                member.name = "sing-box"
                tf.extract(member, td, filter="data")
        except Exception as e:
            log(f"✗ 下载/解压失败: {e}")
            return False
        new_bin = os.path.join(td, "sing-box")
        os.chmod(new_bin, 0o755)
        # 先校验新二进制可运行
        r = subprocess.run([new_bin, "version"], capture_output=True, text=True)
        if r.returncode != 0:
            log("✗ 新二进制自检失败")
            return False

        # 备份 + 替换（需 sudo）——必须在临时目录存活期内完成
        bak = exe + ".bak"
        # rename 替换不受运行中二进制 ETXTBSY 限制
        for cmd in (["sudo", "cp", exe, bak],
                    ["sudo", "install", "-m", "755", new_bin, exe + ".new"],
                    ["sudo", "mv", "-f", exe + ".new", exe]):
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                log(f"✗ {' '.join(cmd[2:])} 失败: {r.stderr.strip()}")
                return False
        log(f"✓ 已替换 {exe}（旧版备份 {bak}）")

    # 重启验证，失败回滚
    subprocess.run(["sudo", "systemctl", "restart", "sing-box"], capture_output=True)
    import time as _t
    _t.sleep(3)
    r = subprocess.run(["systemctl", "is-active", "sing-box"],
                       capture_output=True, text=True)
    if r.stdout.strip() == "active":
        log(f"✓ sing-box {ver} 已启动运行")
        return True
    log("✗ 新版本启动失败，回滚旧二进制")
    subprocess.run(["sudo", "cp", bak, exe], capture_output=True)
    subprocess.run(["sudo", "systemctl", "restart", "sing-box"], capture_output=True)
    return False


def rollback_config(config_path: str) -> tuple[bool, str]:
    """把 config.json.bak 还原为正式配置。"""
    import shutil as s
    p = f"{config_path}.bak"
    if not os.path.exists(p):
        return False, f"备份不存在: {p}"
    if config_path.startswith("/etc/"):
        r = subprocess.run(["sudo", "cp", p, config_path], capture_output=True, text=True)
        return (r.returncode == 0,
                "已回滚" if r.returncode == 0 else f"回滚失败: {r.stderr.strip()}")
    s.copy2(p, config_path)
    return True, "已回滚"
