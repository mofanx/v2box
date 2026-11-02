"""看门狗 — 探测代理链路健康，确认僵死后自动修复 sing-box。

设计（探针测症状，不限定触发原因）:
- verdict 由 health.diagnose 判定：underlay/api_dead → 需修复；
  stopped → 尊重手动停机；nodes → 重启无意义，不修复
- 先探后修：单次 check 内二次确认 + 跨调用连续失败计数，避免误修与抖动风暴
- 分级修复：优先 reload（SIGHUP 热加载重建 TUN，比重启轻），
  同一故障窗口内再次修复自动升级为 restart
- 修复限流：最小间隔 + 每小时上限；restart 后更长冷却
- 状态持久化：watchdog.json 记录失败计数与修复历史，
  check（定时器）/run（常驻）共用同一状态机
- 常驻模式额外启用 `ip monitor` 链路事件监听（Linux），
  网络变更可秒级触发探测；钩子缺失时周期探测保底
"""

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from v2box.core import service, health
from v2box.core.store import DATA_DIR

def _state_path() -> Path:
    """看门狗状态文件路径 — root（systemd timer）与普通用户读写同一份。

    优先级: V2BOX_WATCHDOG_STATE > /run/v2box（unit 的 RuntimeDirectory 保证存在，
    root 写入、用户可读）> 用户数据目录。
    """
    import os
    if env := os.environ.get("V2BOX_WATCHDOG_STATE"):
        return Path(env)
    run_dir = Path("/run/v2box")
    if run_dir.is_dir() or (hasattr(os, "geteuid") and os.geteuid() == 0):
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            return run_dir / "watchdog.json"
        except OSError:
            pass
    return DATA_DIR / "watchdog.json"

FAIL_THRESHOLD = 2          # 连续失败事件数达到才修复
CONFIRM_GAP_SEC = 8         # 单次 check 内二次确认间隔
MIN_REPAIR_INTERVAL = 90    # 两次修复最小间隔（秒）
MAX_REPAIRS_PER_HOUR = 6    # 每小时修复次数上限（防抖动风暴）
RESTART_COOLDOWN = 900      # restart 之后的冷却（秒）

# 视为非物理链路的接口前缀（/sys/class/net）
VIRTUAL_IFACE_PREFIX = (
    "lo", "tun", "utun", "veth", "docker", "br-", "virbr",
    "vboxnet", "vmnet", "zt", "tailscale", "podman", "wg",
)

WATCHDOG_UNIT = "v2box-watchdog"


# ── 状态持久化 ────────────────────────────────────────────────

def load_state() -> dict:
    path = _state_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"fail_count": 0, "last_verdict": None, "last_repair_ts": 0,
            "last_repair_kind": None, "repairs": []}


def _save_state(state: dict):
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        path.write_text(json.dumps(state, indent=2, ensure_ascii=False))
        # root 写入后放开权限，用户态 check/status 才能续写
        try:
            path.chmod(0o666)
        except OSError:
            pass
    except PermissionError:
        # 非 root 上下文写不了共享路径，回落到用户数据目录
        fb = DATA_DIR / "watchdog.json"
        fb.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def _repairs_last_hour(state: dict) -> list:
    cutoff = time.time() - 3600
    return [t for t in state.get("repairs", []) if t > cutoff]


# ── 环境感知 ──────────────────────────────────────────────────

def physical_link_up() -> bool:
    """是否存在可用的物理链路。

    Linux 下读 /sys/class/net 的 operstate（纯文件读，无 shell 依赖）；
    其他平台保守返回 True，交给探针判定。
    """
    if sys.platform != "linux":
        return True
    try:
        for iface in Path("/sys/class/net").iterdir():
            if iface.name.startswith(VIRTUAL_IFACE_PREFIX):
                continue
            try:
                if (iface / "operstate").read_text().strip() in ("up", "unknown"):
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return False


# ── 修复 ─────────────────────────────────────────────────────

def _choose_repair(state: dict) -> str:
    """选择修复动作：首次优先 reload（轻量重建 TUN），其后升级 restart。"""
    if state.get("last_repair_kind") in (None, "start") and service.supports_reload():
        return "reload"
    return "restart"


def _repair(state: dict, kind: str, log) -> dict:
    log(f"链路僵死（{state.get('last_verdict')}），执行修复: {kind}")
    if kind == "reload":
        ok, msg = service.reload()
        if not ok:
            # reload 不支持/失败 → 立即升级 restart
            log(f"reload 失败或不可用 ({msg[:100]})，升级为 restart")
            kind = "restart"
            ok, msg = service.restart()
    else:
        ok, msg = service.restart()

    now = time.time()
    state["last_repair_ts"] = now
    state["last_repair_kind"] = kind
    state.setdefault("repairs", []).append(now)
    state["repairs"] = _repairs_last_hour(state)
    state["fail_count"] = 0
    _save_state(state)
    return {"kind": kind, "ok": ok, "message": msg}


# ── 一轮探测 ──────────────────────────────────────────────────

def check_once(probe_domain: str | None = None, dry_run: bool = False,
               log=lambda m: None) -> dict:
    """执行一轮探测 + 判定 + 按需修复。供 watchdog check / 定时器调用。

    Returns:
        {"verdict", "clash_api", "dns_direct_ms", "node_delay_ms",
         "fail_count", "action", "detail"}
    """
    domain = probe_domain or health.DEFAULT_PROBE_DOMAIN
    report = health.diagnose(domain)
    result = {
        "verdict": report["verdict"],
        "clash_api": report["clash_api"],
        "dns_direct_ms": report["dns_direct_ms"],
        "node_delay_ms": report["node_delay_ms"],
        "fail_count": 0,
        "action": "none",
        "detail": "",
    }
    state = load_state()
    state["last_verdict"] = report["verdict"]

    if report["verdict"] == health.HEALTHY:
        state["fail_count"] = 0
        _save_state(state)
        return result

    if report["verdict"] == health.STOPPED:
        state["fail_count"] = 0
        _save_state(state)
        result["action"] = "skip"
        result["detail"] = "服务未运行，尊重手动停机"
        return result

    if report["verdict"] == health.NODES_DEAD:
        state["fail_count"] = 0
        _save_state(state)
        result["detail"] = "DNS 通但代理链路死，重启无意义（需检查节点/订阅）"
        return result

    # underlay / api_dead：二次确认，避免把瞬时抖动计为故障
    time.sleep(CONFIRM_GAP_SEC)
    report2 = health.diagnose(domain)
    if report2["verdict"] in (health.HEALTHY, health.STOPPED):
        state["fail_count"] = 0
        state["last_verdict"] = report2["verdict"]
        _save_state(state)
        result["verdict"] = report2["verdict"]
        result["detail"] = "二次确认通过，判定为瞬时抖动"
        return result

    state["fail_count"] = state.get("fail_count", 0) + 1
    result["fail_count"] = state["fail_count"]
    result["verdict"] = report2["verdict"]
    state["last_verdict"] = report2["verdict"]
    _save_state(state)

    if state["fail_count"] < FAIL_THRESHOLD:
        result["action"] = "wait"
        result["detail"] = f"失败 {state['fail_count']}/{FAIL_THRESHOLD}，继续观察"
        return result

    if not physical_link_up():
        result["action"] = "skip"
        result["detail"] = "无可用物理链路，跳过修复（本地断网重启无意义）"
        return result

    # 修复限流
    now = time.time()
    last_kind = state.get("last_repair_kind")
    min_interval = RESTART_COOLDOWN if last_kind == "restart" else MIN_REPAIR_INTERVAL
    elapsed = now - state.get("last_repair_ts", 0)
    if elapsed < min_interval:
        result["action"] = "cooldown"
        result["detail"] = f"修复冷却中（还需 {int(min_interval - elapsed)}s）"
        return result
    if len(_repairs_last_hour(state)) >= MAX_REPAIRS_PER_HOUR:
        result["action"] = "throttled"
        result["detail"] = "近 1 小时修复次数已达上限，暂停自动修复"
        return result

    kind = _choose_repair(state)
    if dry_run:
        result["action"] = f"would-{kind}"
        result["detail"] = f"将执行 {kind}（dry-run 未执行）"
        return result

    repair = _repair(state, kind, log)
    result["action"] = repair["kind"]
    result["detail"] = repair["message"]
    return result


# ── 常驻循环 ──────────────────────────────────────────────────

def _ip_monitor(trigger: threading.Event, stop: threading.Event):
    """监听内核地址/路由变化，有事件即触发一轮探测（Linux 加速器）。"""
    ip_bin = shutil.which("ip")
    if not ip_bin:
        return
    while not stop.is_set():
        try:
            proc = subprocess.Popen(
                [ip_bin, "monitor", "address", "route"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            return
        for line in proc.stdout:
            if stop.is_set():
                break
            if line.strip():
                trigger.set()
        proc.wait()
        time.sleep(2)


def run_loop(interval: int = 120, probe_domain: str | None = None):
    """常驻看门狗循环：周期探测 + 链路事件即时探测。"""
    def log(msg):
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

    log(f"v2box watchdog 启动 (interval={interval}s, backend={service.backend_name()})")

    trigger, stop = threading.Event(), threading.Event()
    if sys.platform == "linux":
        threading.Thread(target=_ip_monitor, args=(trigger, stop),
                         daemon=True).start()
        log("已启用链路变化监听 (ip monitor)")

    while True:
        trigger.wait(timeout=interval)
        by_event = trigger.is_set()
        trigger.clear()
        try:
            r = check_once(probe_domain=probe_domain, log=log)
            v, act = r["verdict"], r["action"]
            if v != health.HEALTHY or act not in ("none", "skip"):
                log(f"verdict={v} action={act} "
                    f"dns={r['dns_direct_ms']}ms node={r['node_delay_ms']}ms "
                    f"{r['detail']}")
            elif by_event:
                log(f"链路事件探测: 正常 ({r['dns_direct_ms']}ms)")
        except Exception as e:
            log(f"探测异常: {e}")
        if by_event:
            time.sleep(1)


# ── systemd 定时器安装 ────────────────────────────────────────

def _v2box_bin() -> str:
    return shutil.which("v2box") or str(Path(sys.executable).parent / "v2box")


def install_timer(interval_min: int = 2) -> tuple[bool, list[str]]:
    """安装 systemd 定时器，每 interval_min 分钟执行一轮探测（仅 Linux/systemd）。"""
    msgs = []
    if sys.platform != "linux" or not shutil.which("systemctl"):
        return False, [
            "当前平台不支持 systemd 定时器",
            "替代方案: v2box watchdog run（前台常驻），"
            "或交给 launchd / Task Scheduler / supervisord 托管",
        ]

    unit_dir = Path("/etc/systemd/system")
    service_text = f"""[Unit]
Description=v2box watchdog check — sing-box 链路僵死自愈

[Service]
Type=oneshot
RuntimeDirectory=v2box
ExecStart={_v2box_bin()} watchdog check
"""
    timer_text = f"""[Unit]
Description=v2box watchdog timer — 每 {interval_min} 分钟探测 sing-box 链路

[Timer]
OnBootSec=1min
OnUnitActiveSec={interval_min}min

[Install]
WantedBy=timers.target
"""
    for name, text in ((f"{WATCHDOG_UNIT}.service", service_text),
                       (f"{WATCHDOG_UNIT}.timer", timer_text)):
        r = subprocess.run(["sudo", "tee", str(unit_dir / name)],
                           input=text, capture_output=True, text=True)
        if r.returncode != 0:
            msgs.append(f"写入 {name} 失败: {r.stderr.strip()}")
            return False, msgs
        msgs.append(f"已写入 {unit_dir / name}")

    for cmd in (["sudo", "systemctl", "daemon-reload"],
                ["sudo", "systemctl", "enable", "--now", f"{WATCHDOG_UNIT}.timer"]):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            msgs.append(f"{' '.join(cmd[2:])} 失败: {r.stderr.strip()}")
            return False, msgs
    msgs.append(f"已启用 {WATCHDOG_UNIT}.timer")
    return True, msgs


def uninstall_timer() -> tuple[bool, list[str]]:
    """卸载看门狗定时器。"""
    msgs = []
    if sys.platform != "linux" or not shutil.which("systemctl"):
        return False, ["当前平台不支持 systemd 定时器"]
    r = subprocess.run(["sudo", "systemctl", "disable", "--now",
                        f"{WATCHDOG_UNIT}.timer"], capture_output=True, text=True)
    if r.returncode == 0:
        msgs.append("已停用定时器")
    for name in (f"{WATCHDOG_UNIT}.timer", f"{WATCHDOG_UNIT}.service"):
        p = Path("/etc/systemd/system") / name
        if p.exists():
            subprocess.run(["sudo", "rm", "-f", str(p)],
                           capture_output=True, text=True)
            msgs.append(f"已删除 {p}")
    subprocess.run(["sudo", "systemctl", "daemon-reload"],
                   capture_output=True, text=True)
    return True, msgs


# ── 统一安装入口 ──────────────────────────────────────────────

def install(interval_min: int = 2, with_nethook: bool = False) -> tuple[bool, list[str]]:
    """安装看门狗常驻任务：Linux+systemd 用定时器，其他平台委托服务后端。"""
    if sys.platform == "linux" and shutil.which("systemctl"):
        ok, msgs = install_timer(interval_min)
        if ok and with_nethook:
            nok, nmsgs = install_nethook()
            msgs += nmsgs
            ok = ok and nok
        return ok, msgs
    ok, msg = service.backend().install_watchdog(_v2box_bin(), interval_min * 60)
    return ok, [msg]


def uninstall() -> tuple[bool, list[str]]:
    """卸载看门狗常驻任务（含网络变更钩子）。"""
    if sys.platform == "linux" and shutil.which("systemctl"):
        ok, msgs = uninstall_timer()
        nok, nmsgs = uninstall_nethook()
        return ok and nok, msgs + nmsgs
    ok, msg = service.backend().uninstall_watchdog()
    return ok, [msg]


def timer_status() -> tuple[bool, str]:
    """定时器是否激活。"""
    if sys.platform != "linux" or not shutil.which("systemctl"):
        return False, "非 systemd 平台"
    r = subprocess.run(["systemctl", "is-active", f"{WATCHDOG_UNIT}.timer"],
                       capture_output=True, text=True)
    return r.stdout.strip() == "active", r.stdout.strip()


# ── 网络变更钩子（可选加速层）─────────────────────────────────
#
# 钩子本身不直接重启 sing-box，而是触发一次看门狗探测 —— 是否修复
# 由状态机的二次确认/连续阈值/限流决定，天然免疫重启风暴。
_HOOK_SCRIPT = r"""#!/bin/sh
# v2box nethook — 物理链路事件触发看门狗探测（秒级加速层）
IFACE="$1"; EVENT="$2"
case "$IFACE" in
  lo|tun*|utun*|veth*|docker*|br-*|virbr*|vboxnet*|vmnet*|zt*|tailscale*|podman*|wg*) exit 0 ;;
esac
case "$EVENT" in
  up|dhcp4-change|dhcp6-change|connectivity-change|routable|carrier) ;;
  *) exit 0 ;;
esac
systemctl start v2box-watchdog.service 2>/dev/null || v2box watchdog check >/dev/null 2>&1 &
exit 0
"""


def _nethook_targets() -> list[Path]:
    """检测本机可用的网络钩子机制目录，返回可安装的脚本路径。"""
    targets = []
    if Path("/etc/NetworkManager/dispatcher.d").is_dir():
        targets.append(Path("/etc/NetworkManager/dispatcher.d/90-v2box-watchdog"))
    if Path("/etc/networkd-dispatcher/routable.d").is_dir():
        targets.append(Path("/etc/networkd-dispatcher/routable.d/90-v2box-watchdog"))
    return targets


def install_nethook() -> tuple[bool, list[str]]:
    """安装网络变更钩子。检测不到钩子机制时跳过（看门狗周期探测仍兜底）。"""
    targets = _nethook_targets()
    if not targets:
        return False, ["未检测到 NetworkManager/networkd-dispatcher 钩子目录",
                       "跳过（看门狗周期探测仍可兜底）"]
    msgs = []
    for t in targets:
        r = subprocess.run(["sudo", "tee", str(t)], input=_HOOK_SCRIPT,
                           capture_output=True, text=True)
        if r.returncode != 0:
            msgs.append(f"写入 {t} 失败: {r.stderr.strip()}")
            return False, msgs
        subprocess.run(["sudo", "chmod", "755", str(t)], capture_output=True)
        msgs.append(f"已安装钩子 {t}")
    return True, msgs


def uninstall_nethook() -> tuple[bool, list[str]]:
    """卸载网络变更钩子。"""
    msgs = []
    for t in _nethook_targets():
        if t.exists():
            r = subprocess.run(["sudo", "rm", "-f", str(t)],
                               capture_output=True, text=True)
            msgs.append(f"已删除 {t}" if r.returncode == 0
                        else f"删除 {t} 失败: {r.stderr.strip()}")
    if not msgs:
        msgs.append("未发现已安装的钩子")
    return True, msgs


def nethook_installed() -> bool:
    return any(t.exists() for t in _nethook_targets())


def summary() -> dict:
    """供外部展示的状态摘要。"""
    return {**load_state(), "state_file": str(_state_path()),
            "backend": service.backend_name(),
            "supports_reload": service.supports_reload(),
            "physical_link": physical_link_up(),
            "timer_active": timer_status()[0]}
