"""sing-box 系统服务管理 — 启动、停止、重启、查看状态。"""

import shutil
import subprocess
import sys


def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    """运行系统命令。"""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def singbox_binary() -> str | None:
    """检测 sing-box 可执行文件路径。"""
    return shutil.which("sing-box")


def is_installed() -> bool:
    """检查 sing-box 是否已安装。"""
    return singbox_binary() is not None


def get_version() -> str | None:
    """获取 sing-box 版本号。"""
    binary = singbox_binary()
    if not binary:
        return None
    try:
        result = _run([binary, "version"])
        if result.returncode == 0:
            # 第一行通常是 "sing-box version 1.x.x"
            first_line = result.stdout.strip().split("\n")[0]
            return first_line
        return None
    except Exception:
        return None


def check_config(config_path: str = "/etc/sing-box/config.json") -> tuple[bool, str]:
    """检查配置文件是否合法。返回 (是否合法, 输出信息)。"""
    binary = singbox_binary()
    if not binary:
        return False, "sing-box 未安装"
    result = _run([binary, "check", "-c", config_path])
    if result.returncode == 0:
        return True, "配置文件格式正确"
    return False, result.stderr.strip() or result.stdout.strip()


def start() -> tuple[bool, str]:
    """启动 sing-box 服务。"""
    result = _run(["sudo", "systemctl", "start", "sing-box"])
    if result.returncode == 0:
        return True, "sing-box 已启动"
    return False, result.stderr.strip()


def stop() -> tuple[bool, str]:
    """停止 sing-box 服务。"""
    result = _run(["sudo", "systemctl", "stop", "sing-box"])
    if result.returncode == 0:
        return True, "sing-box 已停止"
    return False, result.stderr.strip()


def restart() -> tuple[bool, str]:
    """重启 sing-box 服务。"""
    result = _run(["sudo", "systemctl", "restart", "sing-box"])
    if result.returncode == 0:
        return True, "sing-box 已重启"
    return False, result.stderr.strip()


def status() -> tuple[bool, str]:
    """获取 sing-box 服务状态。"""
    result = _run(["systemctl", "is-active", "sing-box"])
    is_active = result.stdout.strip() == "active"

    # 获取更详细的状态
    detail = _run(["systemctl", "status", "sing-box", "--no-pager", "-l"])
    return is_active, detail.stdout.strip() or detail.stderr.strip()


def enable() -> tuple[bool, str]:
    """设置 sing-box 开机自启。"""
    result = _run(["sudo", "systemctl", "enable", "sing-box"])
    if result.returncode == 0:
        return True, "sing-box 已设置开机自启"
    return False, result.stderr.strip()


def disable() -> tuple[bool, str]:
    """取消 sing-box 开机自启。"""
    result = _run(["sudo", "systemctl", "disable", "sing-box"])
    if result.returncode == 0:
        return True, "sing-box 已取消开机自启"
    return False, result.stderr.strip()
