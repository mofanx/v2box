"""平台服务后端 — 抽象 sing-box 服务在各操作系统上的生命周期管理。

设计目标：
- watchdog/doctor 等上层逻辑只面向 ServiceBackend 接口，与 init 系统解耦
- Linux(systemd) 为完整实现；macOS(launchd)/Windows(sc) 提供同接口实现
- 无匹配后端时返回 UnsupportedBackend，优雅降级而非崩溃
"""

import os
import platform
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path


def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


class ServiceBackend(ABC):
    """sing-box 服务生命周期后端接口。"""

    #: 后端名称，用于日志和状态展示
    name: str = "abstract"

    @abstractmethod
    def status(self) -> tuple[bool, str]:
        """返回 (是否运行中, 详情文本)。"""

    @abstractmethod
    def start(self) -> tuple[bool, str]:
        """启动服务。"""

    @abstractmethod
    def stop(self) -> tuple[bool, str]:
        """停止服务。"""

    @abstractmethod
    def restart(self) -> tuple[bool, str]:
        """重启服务。"""

    @abstractmethod
    def enable(self) -> tuple[bool, str]:
        """设置开机自启。"""

    @abstractmethod
    def disable(self) -> tuple[bool, str]:
        """取消开机自启。"""

    def supports_reload(self) -> bool:
        """是否支持不重载进程的热加载（如 SIGHUP）。"""
        return False

    def reload(self) -> tuple[bool, str]:
        """热加载配置（重建 inbound/TUN）。默认不支持。"""
        return False, f"{self.name} 后端不支持热加载"

    def install_watchdog(self, v2box_bin: str, interval: int = 60) -> tuple[bool, str]:
        """安装看门狗常驻任务。interval 为探测周期（秒）。"""
        return False, f"{self.name} 后端暂不支持自动安装看门狗，请手动配置 v2box watchdog run"

    def uninstall_watchdog(self) -> tuple[bool, str]:
        """卸载看门狗常驻任务。"""
        return False, f"{self.name} 后端不支持卸载看门狗"

    def install_nethook(self) -> tuple[bool, str]:
        """安装网络变更钩子（可选加速层，装不了不影响看门狗）。"""
        return False, f"{self.name} 后端无网络钩子机制"

    def uninstall_nethook(self) -> tuple[bool, str]:
        return False, f"{self.name} 后端无网络钩子机制"

    def nethook_installed(self) -> bool:
        return False


class SystemdBackend(ServiceBackend):
    """Linux systemd 后端。"""

    name = "systemd"
    UNIT = "sing-box"
    WATCHDOG_UNIT = "v2box-daemon"

    def _is_active(self) -> bool:
        result = _run(["systemctl", "is-active", self.UNIT])
        # activating 计入运行中：重启窗口期误判 stopped 会重置失败计数
        return result.stdout.strip() in ("active", "activating")

    def _status_detail(self) -> str:
        result = _run(["systemctl", "status", self.UNIT, "--no-pager", "-l"])
        return result.stdout.strip() or result.stderr.strip()

    def _wait_active(self, tries: int = 5, interval: float = 0.5) -> bool:
        import time
        for _ in range(tries):
            time.sleep(interval)
            if self._is_active():
                return True
        return False

    def status(self) -> tuple[bool, str]:
        return self._is_active(), self._status_detail()

    def start(self) -> tuple[bool, str]:
        result = _run(["sudo", "systemctl", "start", self.UNIT])
        if result.returncode != 0:
            return False, result.stderr.strip()
        if self._wait_active():
            return True, "sing-box 已启动"
        return False, f"服务启动后未能保持运行，请检查日志:\n{self._status_detail()}"

    def stop(self) -> tuple[bool, str]:
        result = _run(["sudo", "systemctl", "stop", self.UNIT])
        if result.returncode == 0:
            return True, "sing-box 已停止"
        return False, result.stderr.strip()

    def restart(self) -> tuple[bool, str]:
        result = _run(["sudo", "systemctl", "restart", self.UNIT])
        if result.returncode != 0:
            return False, result.stderr.strip()
        if self._wait_active():
            return True, "sing-box 已重启"
        return False, f"服务重启后未能保持运行，请检查日志:\n{self._status_detail()}"

    def supports_reload(self) -> bool:
        # 需要单元定义 ExecReload（官方 deb 包已提供 kill -HUP）
        result = _run(["systemctl", "show", self.UNIT, "-p", "ExecReload"])
        return "kill" in result.stdout or "HUP" in result.stdout

    def reload(self) -> tuple[bool, str]:
        result = _run(["sudo", "systemctl", "reload", self.UNIT])
        if result.returncode != 0:
            return False, result.stderr.strip()
        if self._wait_active():
            return True, "sing-box 已热加载"
        return False, f"热加载后服务异常:\n{self._status_detail()}"

    def enable(self) -> tuple[bool, str]:
        result = _run(["sudo", "systemctl", "enable", self.UNIT])
        if result.returncode == 0:
            return True, "sing-box 已设置开机自启"
        return False, result.stderr.strip()

    def disable(self) -> tuple[bool, str]:
        result = _run(["sudo", "systemctl", "disable", self.UNIT])
        if result.returncode == 0:
            return True, "sing-box 已取消开机自启"
        return False, result.stderr.strip()

    # ── 看门狗安装 ──────────────────────────────────────────

    def _watchdog_unit_text(self, v2box_bin: str, interval: int) -> str:
        return f"""[Unit]
Description=v2box watchdog — sing-box 代理链路健康守护
After=network-online.target sing-box.service
Wants=network-online.target

[Service]
Type=simple
ExecStart={v2box_bin} watchdog run --interval {interval}
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
"""

    def install_watchdog(self, v2box_bin: str, interval: int = 60) -> tuple[bool, str]:
        unit_path = Path(f"/etc/systemd/system/{self.WATCHDOG_UNIT}.service")
        text = self._watchdog_unit_text(v2box_bin, interval)
        result = subprocess.run(
            ["sudo", "tee", str(unit_path)],
            input=text, capture_output=True, text=True,
        )
        if result.returncode != 0:
            return False, f"写入单元文件失败: {result.stderr.strip()}"
        result = _run([
            "sudo", "systemctl", "daemon-reload",
        ])
        if result.returncode != 0:
            return False, f"daemon-reload 失败: {result.stderr.strip()}"
        result = _run([
            "sudo", "systemctl", "enable", "--now", self.WATCHDOG_UNIT,
        ])
        if result.returncode != 0:
            return False, f"启用失败: {result.stderr.strip()}"
        return True, f"看门狗已安装并启动 ({unit_path})"

    def uninstall_watchdog(self) -> tuple[bool, str]:
        _run(["sudo", "systemctl", "disable", "--now", self.WATCHDOG_UNIT])
        unit_path = Path(f"/etc/systemd/system/{self.WATCHDOG_UNIT}.service")
        result = _run(["sudo", "rm", "-f", str(unit_path)])
        _run(["sudo", "systemctl", "daemon-reload"])
        if result.returncode != 0:
            return False, result.stderr.strip()
        return True, "看门狗已卸载"

    def watchdog_installed(self) -> bool:
        return Path(f"/etc/systemd/system/{self.WATCHDOG_UNIT}.service").exists()

    def watchdog_status(self) -> tuple[bool, str]:
        result = _run(["systemctl", "is-active", self.WATCHDOG_UNIT])
        return result.stdout.strip() == "active", result.stdout.strip()

    # ── 网络变更钩子（可选加速层：链路事件秒级自愈，看门狗兜底不变）──

    # 只响应物理网卡事件，过滤 tun/docker 等虚拟接口，60s 防抖防重启风暴
    _HOOK_SCRIPT = r"""#!/bin/sh
# v2box nethook - restart sing-box on physical link changes (watchdog fast path)
IFACE="$1"; EVENT="$2"
case "$IFACE" in tun*|utun*|lo|docker*|veth*|br-*|virbr*|podman*|wg*|zt*|tailscale*) exit 0 ;; esac
case "$EVENT" in up|dhcp4-change|dhcp6-change|connectivity-change|routable) ;; *) exit 0 ;; esac
STAMP=/run/v2box-nethook.last
NOW=$(date +%s); LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
[ $((NOW - LAST)) -lt 60 ] && exit 0
echo "$NOW" > "$STAMP"
systemctl try-restart sing-box
"""

    def _nethook_targets(self) -> list[Path]:
        """检测本机可用的钩子机制（NM dispatcher / networkd-dispatcher）。"""
        targets = []
        if Path("/etc/NetworkManager/dispatcher.d").is_dir():
            targets.append(Path("/etc/NetworkManager/dispatcher.d/90-v2box-singbox"))
        if Path("/etc/networkd-dispatcher/routable.d").is_dir():
            targets.append(Path("/etc/networkd-dispatcher/routable.d/90-v2box-singbox"))
        return targets

    def install_nethook(self) -> tuple[bool, str]:
        targets = self._nethook_targets()
        if not targets:
            return False, "未检测到 NetworkManager/networkd-dispatcher 钩子目录（看门狗仍可兜底）"
        installed = []
        for t in targets:
            r = _run(["sudo", "tee", str(t)], input=self._HOOK_SCRIPT)
            if r.returncode != 0:
                return False, f"写入 {t} 失败: {r.stderr.strip()}"
            _run(["sudo", "chmod", "755", str(t)])
            installed.append(str(t))
        return True, "网络变更钩子已安装: " + ", ".join(installed)

    def uninstall_nethook(self) -> tuple[bool, str]:
        for t in self._nethook_targets():
            _run(["sudo", "rm", "-f", str(t)])
        return True, "网络变更钩子已卸载"

    def nethook_installed(self) -> bool:
        return any(t.exists() for t in self._nethook_targets())


class LaunchdBackend(ServiceBackend):
    """macOS launchd 后端（适配实现，未真机验证）。"""

    name = "launchd"
    # homebrew services 默认 label；官方 pkg 安装可能不同
    CANDIDATE_LABELS = ["homebrew.mxcl.sing-box", "org.sagernet.sing-box"]
    WATCHDOG_LABEL = "com.v2box.daemon"

    def _label(self) -> str:
        result = _run(["launchctl", "list"])
        for label in self.CANDIDATE_LABELS:
            if label in result.stdout:
                return label
        return self.CANDIDATE_LABELS[0]

    def _brew_services(self, *args) -> subprocess.CompletedProcess:
        return _run(["brew", "services", *args])

    def status(self) -> tuple[bool, str]:
        result = _run(["launchctl", "list"])
        label = self._label()
        running = label in result.stdout
        return running, f"label={label} running={running}"

    def start(self) -> tuple[bool, str]:
        if shutil.which("brew"):
            r = self._brew_services("start", "sing-box")
            return (r.returncode == 0), r.stderr.strip() or "sing-box 已启动"
        r = _run(["sudo", "launchctl", "kickstart", f"system/{self._label()}"])
        return (r.returncode == 0), r.stderr.strip() or "sing-box 已启动"

    def stop(self) -> tuple[bool, str]:
        if shutil.which("brew"):
            r = self._brew_services("stop", "sing-box")
            return (r.returncode == 0), r.stderr.strip() or "sing-box 已停止"
        r = _run(["sudo", "launchctl", "kill", "SIGTERM", f"system/{self._label()}"])
        return (r.returncode == 0), r.stderr.strip() or "sing-box 已停止"

    def restart(self) -> tuple[bool, str]:
        if shutil.which("brew"):
            r = self._brew_services("restart", "sing-box")
            return (r.returncode == 0), r.stderr.strip() or "sing-box 已重启"
        r = _run(["sudo", "launchctl", "kickstart", "-k", f"system/{self._label()}"])
        return (r.returncode == 0), r.stderr.strip() or "sing-box 已重启"

    def enable(self) -> tuple[bool, str]:
        return False, "macOS 下由 launchd plist 管理自启，请在安装时勾选"

    def disable(self) -> tuple[bool, str]:
        return False, "macOS 下由 launchd plist 管理自启"

    def install_watchdog(self, v2box_bin: str, interval: int = 60) -> tuple[bool, str]:
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{self.WATCHDOG_LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{v2box_bin}</string><string>watchdog</string><string>run</string><string>--interval</string><string>{interval}</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
"""
        path = Path(f"/Library/LaunchDaemons/{self.WATCHDOG_LABEL}.plist")
        r = subprocess.run(["sudo", "tee", str(path)], input=plist,
                           capture_output=True, text=True)
        if r.returncode != 0:
            return False, r.stderr.strip()
        r = _run(["sudo", "launchctl", "load", "-w", str(path)])
        if r.returncode != 0:
            return False, r.stderr.strip()
        return True, f"看门狗已安装并加载 ({path})"

    def uninstall_watchdog(self) -> tuple[bool, str]:
        path = Path(f"/Library/LaunchDaemons/{self.WATCHDOG_LABEL}.plist")
        _run(["sudo", "launchctl", "unload", "-w", str(path)])
        r = _run(["sudo", "rm", "-f", str(path)])
        return (r.returncode == 0), r.stderr.strip() or "看门狗已卸载"


class WindowsBackend(ServiceBackend):
    """Windows 服务后端（适配实现，未真机验证，依赖 sing-box 已注册为 Windows 服务）。"""

    name = "windows-service"
    SERVICE = "sing-box"
    TASK_NAME = "v2box-daemon"

    def _query_state(self) -> str:
        r = _run(["sc", "query", self.SERVICE])
        return r.stdout

    def status(self) -> tuple[bool, str]:
        out = self._query_state()
        return "RUNNING" in out, out.strip()

    def start(self) -> tuple[bool, str]:
        r = _run(["net", "start", self.SERVICE])
        return (r.returncode == 0), r.stderr.strip() or r.stdout.strip()

    def stop(self) -> tuple[bool, str]:
        r = _run(["net", "stop", self.SERVICE])
        return (r.returncode == 0), r.stderr.strip() or r.stdout.strip()

    def restart(self) -> tuple[bool, str]:
        ok1, msg1 = self.stop()
        ok2, msg2 = self.start()
        return ok2, msg2 if ok2 else f"stop: {msg1}\nstart: {msg2}"

    def enable(self) -> tuple[bool, str]:
        r = _run(["sc", "config", self.SERVICE, "start=", "auto"])
        return (r.returncode == 0), r.stderr.strip() or r.stdout.strip()

    def disable(self) -> tuple[bool, str]:
        r = _run(["sc", "config", self.SERVICE, "start=", "demand"])
        return (r.returncode == 0), r.stderr.strip() or r.stdout.strip()

    def install_watchdog(self, v2box_bin: str, interval: int = 60) -> tuple[bool, str]:
        # schtasks 最小粒度为 1 分钟
        minutes = max(1, interval // 60)
        r = _run([
            "schtasks", "/create", "/f", "/tn", self.TASK_NAME,
            "/sc", "minute", "/mo", str(minutes),
            "/tr", f'"{v2box_bin}" watchdog check',
            "/rl", "highest",
        ])
        return (r.returncode == 0), r.stderr.strip() or r.stdout.strip()

    def uninstall_watchdog(self) -> tuple[bool, str]:
        r = _run(["schtasks", "/delete", "/f", "/tn", self.TASK_NAME])
        return (r.returncode == 0), r.stderr.strip() or r.stdout.strip()


class UnsupportedBackend(ServiceBackend):
    """无匹配后端时的降级实现。"""

    name = "unsupported"

    def _na(self, action: str) -> tuple[bool, str]:
        return False, f"当前平台 ({platform.system()}) 不支持 {action}，请手动管理 sing-box"

    def status(self) -> tuple[bool, str]:
        return False, f"当前平台 ({platform.system()}) 无可用服务后端"

    def start(self):
        return self._na("启动")

    def stop(self):
        return self._na("停止")

    def restart(self):
        return self._na("重启")

    def enable(self):
        return self._na("自启设置")

    def disable(self):
        return self._na("自启设置")


def get_backend() -> ServiceBackend:
    """根据当前平台选择服务后端。"""
    system = platform.system()
    if system == "Linux":
        if shutil.which("systemctl") and Path("/run/systemd/system").exists():
            return SystemdBackend()
        return UnsupportedBackend()
    if system == "Darwin":
        return LaunchdBackend()
    if system == "Windows":
        return WindowsBackend()
    return UnsupportedBackend()


def detect_platform() -> str:
    """规范化平台名: linux / macos / windows。"""
    system = platform.system()
    return {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}.get(
        system, system.lower())


def default_config_path() -> Path:
    """sing-box 配置文件默认路径（V2BOX_SINGBOX_CONFIG 环境变量可覆盖）。"""
    env = os.environ.get("V2BOX_SINGBOX_CONFIG")
    if env:
        return Path(env)
    p = detect_platform()
    if p == "macos":
        prefix = Path("/opt/homebrew") if platform.machine() == "arm64" else Path("/usr/local")
        return prefix / "etc" / "sing-box" / "config.json"
    if p == "windows":
        progdata = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        p_progdata = progdata / "sing-box" / "config.json"
        if p_progdata.exists():
            return p_progdata
        exe = shutil.which("sing-box")
        return Path(exe).parent / "config.json" if exe else p_progdata
    return Path("/etc/sing-box/config.json")
