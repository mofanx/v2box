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
import time
import urllib.request
import zipfile

from v2box.core import backend

API = "https://api.github.com/repos/SagerNet/sing-box/releases/latest"


def _get(url: str, timeout: int = 15) -> bytes:
    """GitHub API/下载请求。GH_TOKEN/GITHUB_TOKEN 环境变量存在时带认证
    （未认证共享 IP 只有 60 次/h，CI 或重度使用必撞限流）。"""
    headers = {"User-Agent": "v2box-updater"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout).read()


def current_version() -> str | None:
    exe = shutil.which("sing-box")
    if not exe:
        return None
    r = subprocess.run([exe, "version"], capture_output=True, text=True)
    # 首行 "sing-box version 1.13.14" → 1.13.14（末行是 build tags，不能取）
    first = r.stdout.strip().split("\n")[0].split()
    return first[-1] if first else None


def _release_suffix(plat: str, machine: str) -> str:
    """平台对应的 release 资产文件名后缀，如 linux-amd64.tar.gz / windows-amd64.zip。"""
    m = machine.lower()
    arch = "amd64" if m in ("x86_64", "amd64") else "arm64"
    os_part = {"windows": "windows", "macos": "darwin"}.get(plat, plat)
    ext = "zip" if plat == "windows" else "tar.gz"
    return f"{os_part}-{arch}.{ext}"


def _select_asset(assets: list[dict], plat: str, machine: str) -> str:
    """从 release assets 里挑出本平台的下载 url，找不到返回空串。"""
    want = _release_suffix(plat, machine)
    for a in assets:
        if a["name"].endswith(want):
            return a["browser_download_url"]
    return ""


def latest_release() -> tuple[str, str] | None:
    """返回 (tag, 资产下载url)。找不到对应平台资产返回 None。"""
    try:
        data = json.loads(_get(API))
    except Exception:
        return None
    tag = data.get("tag_name", "")
    url = _select_asset(data.get("assets", []),
                        backend.detect_platform(), platform.machine())
    return tag, url


def _extract_binary(pkg: str, dest: str) -> str:
    """从 tar.gz/zip 包里抽出 sing-box 可执行文件到 dest，返回路径。"""
    os.makedirs(dest, exist_ok=True)
    if pkg.endswith(".zip"):
        with zipfile.ZipFile(pkg) as zf:
            name = next((n for n in zf.namelist()
                         if not n.endswith("/")
                         and n.endswith(("sing-box", "sing-box.exe"))), None)
            if name is None:
                raise FileNotFoundError("压缩包内未找到 sing-box 二进制")
            target = os.path.join(dest, os.path.basename(name))
            with zf.open(name) as src, open(target, "wb") as dst:
                dst.write(src.read())
            return target
    with tarfile.open(pkg) as tf:
        member = next((m for m in tf.getmembers()
                       if m.name.endswith("/sing-box") or m.name == "sing-box"),
                      None)
        if member is None:
            raise FileNotFoundError("压缩包内未找到 sing-box 二进制")
        member.name = "sing-box"
        tf.extract(member, dest, filter="data")
        return os.path.join(dest, "sing-box")


def _stop_service() -> bool:
    ok, _ = backend.get_backend().stop()
    return ok


def _start_service() -> bool:
    ok, _ = backend.get_backend().start()
    return ok


def _install_binary(new_bin: str, exe: str, plat: str) -> None:
    """用 new_bin 替换 exe，旧版本备份为 exe.bak。失败抛异常。

    Windows：运行中的 exe 被锁定不可覆盖，先停服→替换→起服，全程无需 sudo。
    POSIX：sudo cp/install/mv 序列，rename 替换不受 ETXTBSY 限制。
    """
    bak = exe + ".bak"
    if plat == "windows":
        _stop_service()
        shutil.copy2(exe, bak)
        shutil.copy2(new_bin, exe)
        _start_service()
        return
    for cmd in (["sudo", "cp", exe, bak],
                ["sudo", "install", "-m", "755", new_bin, exe + ".new"],
                ["sudo", "mv", "-f", exe + ".new", exe]):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd[2:])} 失败: {r.stderr.strip()}")


def _install_target(plat: str) -> str:
    """首次安装的目标路径。

    Windows：管理员 → ProgramData（与服务配置同目录约定）；
             普通用户 → LOCALAPPDATA\\Programs（无需提权）。
    POSIX：/usr/local/bin（官方安装约定）。
    """
    if plat == "windows":
        exe = "sing-box.exe"
        if backend.is_admin():
            root = os.environ.get("ProgramData", r"C:\ProgramData")
            return os.path.join(root, "sing-box", exe)
        root = os.environ.get(
            "LOCALAPPDATA",
            os.path.join(os.path.expanduser("~"), "AppData", "Local"))
        return os.path.join(root, "Programs", "sing-box", exe)
    return "/usr/local/bin/sing-box"


def _ensure_on_path(dir_path: str, plat: str, log=print) -> None:
    """Windows：把安装目录写入 PATH（管理员→Machine；普通→User，注册表级，新终端生效）。
    CI 环境额外追加到 GITHUB_PATH 让后续步骤可见。"""
    if plat != "windows":
        return
    scope = "Machine" if backend.is_admin() else "User"
    ps = (
        f'$p=[Environment]::GetEnvironmentVariable("Path","{scope}");'
        f'if($p -notlike "*{dir_path}*"){{'
        f'[Environment]::SetEnvironmentVariable("Path","$p;{dir_path}","{scope}")}}'
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True)
    if r.returncode == 0:
        log(f"已将 {dir_path} 写入 {scope} PATH（新开终端生效）")
    else:
        log(f"提示: 请手动将 {dir_path} 加入 PATH")
    gh_path = os.environ.get("GITHUB_PATH")
    if gh_path:
        try:
            with open(gh_path, "a", encoding="utf-8") as f:
                f.write(dir_path + "\n")
        except OSError:
            pass


def install(log=print, with_service: bool = False) -> bool:
    """首次安装 sing-box 二进制。已安装返回 False 并提示走 upgrade。"""
    existing = shutil.which("sing-box")
    if existing:
        log(f"sing-box 已安装: {existing}（升级请用 v2box upgrade）")
        return False
    plat = backend.detect_platform()
    if plat not in ("linux", "macos", "windows"):
        log(f"✗ 不支持的平台: {plat}")
        return False
    rel = latest_release()
    if not rel or not rel[1]:
        log("✗ 获取最新版本失败")
        return False
    tag, url = rel
    target = _install_target(plat)
    log(f"下载 {tag} → {target}")

    suffix = _release_suffix(plat, platform.machine())
    with tempfile.TemporaryDirectory() as td:
        pkg = os.path.join(td, "sb." + suffix.split(".", 1)[1])
        try:
            open(pkg, "wb").write(_get(url, timeout=60))
            new_bin = _extract_binary(pkg, td)
        except Exception as e:
            log(f"✗ 下载/解压失败: {e}")
            return False
        if plat != "windows":
            os.chmod(new_bin, 0o755)
        r = subprocess.run([new_bin, "version"], capture_output=True, text=True)
        if r.returncode != 0:
            log("✗ 二进制自检失败")
            return False
        if plat == "windows":
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(new_bin, target)
        else:
            r = subprocess.run(["sudo", "install", "-m", "755", new_bin, target],
                               capture_output=True, text=True)
            if r.returncode != 0:
                log(f"✗ 安装失败: {r.stderr.strip()}")
                return False
    log(f"✓ sing-box {tag} 已安装到 {target}")
    _ensure_on_path(os.path.dirname(target), plat, log)

    if with_service:
        cfg = str(backend.default_config_path())
        ok, msg = backend.get_backend().install_service(cfg)
        log(("✓ " if ok else "✗ ") + msg)
        return ok
    return True


def upgrade(log=print) -> bool:
    """执行升级。返回是否成功。"""
    exe = shutil.which("sing-box")
    if not exe:
        log("✗ 未找到 sing-box 二进制")
        return False
    if "\\scoop\\shims\\" in exe.replace("/", "\\").lower():
        log("检测到 scoop 安装的 sing-box——请用 `scoop update sing-box` 升级"
            "（直接替换二进制会破坏 scoop 包管理）")
        return False
    plat = backend.detect_platform()
    if plat == "windows" and not backend.is_admin():
        log("✗ Windows 下升级需要管理员权限，请以管理员身份重开终端")
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

    suffix = _release_suffix(plat, platform.machine())
    with tempfile.TemporaryDirectory() as td:
        pkg = os.path.join(td, "sb." + suffix.split(".", 1)[1])
        try:
            open(pkg, "wb").write(_get(url, timeout=60))
            new_bin = _extract_binary(pkg, td)
        except Exception as e:
            log(f"✗ 下载/解压失败: {e}")
            return False
        if plat != "windows":
            os.chmod(new_bin, 0o755)
        # 先校验新二进制可运行
        r = subprocess.run([new_bin, "version"], capture_output=True, text=True)
        if r.returncode != 0:
            log("✗ 新二进制自检失败")
            return False

        try:
            _install_binary(new_bin, exe, plat)
        except Exception as e:
            log(f"✗ 安装失败: {e}")
            return False
        log(f"✓ 已替换 {exe}（旧版备份 {exe}.bak）")

    # 重启验证，失败回滚
    b = backend.get_backend()
    b.restart()
    time.sleep(3)
    active, _ = b.status()
    if active:
        log(f"✓ sing-box {ver} 已启动运行")
        return True
    log("✗ 新版本启动失败，回滚旧二进制")
    bak = exe + ".bak"
    if plat == "windows":
        _stop_service()
        shutil.copy2(bak, exe)
        _start_service()
    else:
        subprocess.run(["sudo", "cp", bak, exe], capture_output=True)
        b.restart()
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
