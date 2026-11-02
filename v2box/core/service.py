"""sing-box 服务管理 — 启动、停止、重启、查看状态。

所有服务生命周期操作委托给平台后端（见 backend.py），
本模块保留原有函数签名以兼容调用方。
"""

import shutil
import subprocess

from v2box.core.backend import ServiceBackend, get_backend

_backend: ServiceBackend | None = None


def backend() -> ServiceBackend:
    """获取当前平台的服务后端（惰性初始化）。"""
    global _backend
    if _backend is None:
        _backend = get_backend()
    return _backend


def backend_name() -> str:
    """当前服务后端名称（systemd / launchd / windows-service / unsupported）。"""
    return backend().name


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
        result = subprocess.run([binary, "version"], capture_output=True, text=True)
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
    result = subprocess.run(
        [binary, "check", "-c", config_path], capture_output=True, text=True)
    if result.returncode == 0:
        return True, "配置文件格式正确"
    return False, result.stderr.strip() or result.stdout.strip()


def start() -> tuple[bool, str]:
    """启动 sing-box 服务。"""
    return backend().start()


def stop() -> tuple[bool, str]:
    """停止 sing-box 服务。"""
    return backend().stop()


def restart() -> tuple[bool, str]:
    """重启 sing-box 服务。"""
    return backend().restart()


def reload() -> tuple[bool, str]:
    """热加载 sing-box（SIGHUP，重建 inbound/TUN，比重启更轻）。

    不支持热加载的后端返回 (False, 原因)，调用方应回退到 restart。
    """
    return backend().reload()


def supports_reload() -> bool:
    return backend().supports_reload()


def status() -> tuple[bool, str]:
    """获取 sing-box 服务状态。"""
    return backend().status()


def is_active() -> bool:
    """sing-box 服务是否运行中。"""
    return backend().status()[0]


def enable() -> tuple[bool, str]:
    """设置 sing-box 开机自启。"""
    return backend().enable()


def disable() -> tuple[bool, str]:
    """取消 sing-box 开机自启。"""
    return backend().disable()
