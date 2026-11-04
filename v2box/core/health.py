"""连通性探针 — 纯 Python 实现，跨平台、零 shell 依赖。

故障模型（实测确认的坏死签名）:
    sing-box 的 direct 出站/底层 DNS 在网络变更后进入僵死状态，
    进程不退出、systemd 无法感知。此时:
      - dns-direct 链路（系统解析 → TUN → hijack-dns → 直连 DNS）全部超时
      - 节点域名无法解析 → 代理链路死
      - Clash API 可能仍然响应（进程活着，只是通道死了）

探针设计:
    - probe_direct_dns: 用系统解析器解析一个应走 dns-direct 的域名。
      链路断 = 目标故障；节点全灭时它仍然通 → 不会误报。
    - probe_clash_api: sing-box 进程活性。
    - probe_node_delay: 区分"通道死(需重启)"和"节点死(重启没用)"。
"""

import socket
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from v2box.core.config import CLASH_API_PORT, CLASH_API_SECRET

# 探针默认目标: 应命中 geosite-cn → dns-direct 的国内域名
DEFAULT_PROBE_DOMAIN = "www.baidu.com"

# 健康状态
HEALTHY = "healthy"          # 一切正常
STOPPED = "stopped"          # sing-box 未运行（不算故障，看门狗跳过）
UNDERLAY_DEAD = "underlay"   # direct/DNS 链路僵死 → 需重启修复
NODES_DEAD = "nodes"         # DNS 通但代理链路死 → 重启无意义，提示换节点
API_DEAD = "api_dead"        # 进程在但 Clash API 无响应 → 视为僵死

CLASH_API_BASE = f"http://127.0.0.1:{CLASH_API_PORT}"


def _api_get(path: str, timeout: float = 3.0) -> dict | None:
    req = urllib.request.Request(
        f"{CLASH_API_BASE}{path}",
        headers={"Authorization": f"Bearer {CLASH_API_SECRET}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def probe_clash_api(timeout: float = 3.0) -> bool:
    """Clash API 是否可用（sing-box 进程活性）。"""
    return _api_get("/version", timeout=timeout) is not None


def probe_direct_dns(domain: str = DEFAULT_PROBE_DOMAIN,
                     timeout: float = 6.0) -> float | None:
    """通过系统解析器解析域名，验证 direct DNS 链路。

    返回延迟 ms；超时/失败返回 None。
    socket.getaddrinfo 走系统解析器（Linux: systemd-resolved → TUN
    hijack → sing-box dns-direct），三个桌面平台行为一致。
    """
    def _resolve():
        t = time.monotonic()
        socket.getaddrinfo(domain, 443, proto=socket.IPPROTO_TCP)
        return (time.monotonic() - t) * 1000

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_resolve)
        try:
            return round(future.result(timeout=timeout), 1)
        except Exception:
            return None


def probe_node_delay(timeout_ms: int = 5000) -> int | None:
    """通过 Clash API 测当前生效节点的延迟，区分节点死与通道死。"""
    url = urllib.parse.quote("https://cp.cloudflare.com/generate_204", safe="")
    data = _api_get(f"/proxies/proxy/delay?url={url}&timeout={timeout_ms}",
                    timeout=timeout_ms / 1000 + 2)
    if not data:
        return None
    delay = data.get("delay")
    return delay if delay and delay > 0 else None


def cache_db_hint() -> str | None:
    """cache.db 属主与服务用户不一致时给出修复提示（Linux/systemd）。

    以其他用户身份前台跑过 sing-box 会把 /var/lib/sing-box/cache.db 重写为
    非服务用户属主，导致服务启动 FATAL（permission denied）——restart 无用，
    这是已实测发生过的故障形态。
    """
    if sys.platform != "linux" or not shutil.which("systemctl"):
        return None
    import os
    import pwd
    db = Path("/var/lib/sing-box/cache.db")
    if not db.exists():
        return None
    r = subprocess.run(["systemctl", "show", "sing-box", "-p", "User"],
                       capture_output=True, text=True)
    svc_user = r.stdout.strip().rsplit("=", 1)[-1].strip() or "root"
    try:
        owner = pwd.getpwuid(db.stat().st_uid).pw_name
    except KeyError:
        owner = str(db.stat().st_uid)
    if owner != svc_user:
        return (f"cache.db 属主 {owner} ≠ 服务用户 {svc_user}，服务会启动 FATAL："
                f"sudo chown {svc_user}:{svc_user} {db}")
    return None


def diagnose(probe_domain: str = DEFAULT_PROBE_DOMAIN,
             check_nodes: bool = True) -> dict:
    """执行完整诊断，返回结构化报告。

    verdict 含义见模块头的状态常量。
    """
    from v2box.core import service

    # 三项无依赖的探针并行：is_active(systemd) + clash_api(HTTP) + dns(解析)
    report = {"verdict": HEALTHY, "dns_direct_ms": None, "node_delay_ms": None}
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_active = pool.submit(service.is_active)
        f_api = pool.submit(probe_clash_api)
        f_dns = pool.submit(probe_direct_dns, probe_domain)
        report["service_active"] = f_active.result()
        report["clash_api"] = f_api.result()
        report["dns_direct_ms"] = f_dns.result()

    if not report["service_active"] and not report["clash_api"]:
        report["verdict"] = STOPPED
        return report

    if report["dns_direct_ms"] is not None:
        if check_nodes:
            report["node_delay_ms"] = probe_node_delay()
            if report["node_delay_ms"] is None:
                # direct 通、代理链路死 —— 重启大概率无用
                report["verdict"] = NODES_DEAD
        return report

    # DNS 链路死：进一步区分是进程僵死还是通道僵死
    if not report["clash_api"]:
        report["verdict"] = API_DEAD
    else:
        report["verdict"] = UNDERLAY_DEAD
    return report
