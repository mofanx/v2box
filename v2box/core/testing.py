"""节点延迟测试模块 — 通过 sing-box Clash API 或直接 TCP 握手测试节点延迟。"""

import json
import socket
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

from v2box.core.config import CLASH_API_PORT, CLASH_API_SECRET

# Clash API 基地址
CLASH_API_BASE = f"http://127.0.0.1:{CLASH_API_PORT}"


def _api_request(path: str, method: str = "GET", data: dict | None = None,
                 timeout: float = 5.0) -> dict | None:
    """向 Clash API 发送请求。"""
    url = f"{CLASH_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {CLASH_API_SECRET}"}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None


def test_via_clash_api(tag: str, test_url: str = "https://cp.cloudflare.com/generate_204",
                       timeout: int = 5000) -> int | None:
    """通过 Clash API 测试单个节点延迟 (ms)，失败返回 None。"""
    result = _api_request(
        f"/proxies/{tag}/delay",
        method="GET",
        data=None,
        timeout=timeout / 1000 + 2,
    )
    # Clash API 用 GET /proxies/:name/delay?url=...&timeout=...
    url = f"{CLASH_API_BASE}/proxies/{tag}/delay?url={test_url}&timeout={timeout}"
    headers = {"Authorization": f"Bearer {CLASH_API_SECRET}"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout / 1000 + 2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            delay = data.get("delay")
            return delay if delay and delay > 0 else None
    except Exception:
        return None


def test_tcp_latency(server: str, port: int, timeout: float = 5.0) -> float | None:
    """直接 TCP 握手测试延迟 (ms)，失败返回 None。"""
    try:
        start = time.monotonic()
        sock = socket.create_connection((server, port), timeout=timeout)
        elapsed = (time.monotonic() - start) * 1000
        sock.close()
        return round(elapsed, 1)
    except (OSError, socket.timeout):
        return None


def test_all_nodes_tcp(nodes: list[dict], timeout: float = 5.0,
                       max_workers: int = 10) -> list[dict]:
    """并发 TCP 测试所有节点，返回带延迟信息的结果列表。

    返回: [{"tag": ..., "server": ..., "port": ..., "latency_ms": ... | None}, ...]
    按延迟升序排列，超时的排在最后。
    """
    results = []

    def _test_one(node):
        tag = node["tag"]
        server = node["server"]
        port = node["server_port"]
        latency = test_tcp_latency(server, port, timeout)
        return {"tag": tag, "server": server, "port": port, "latency_ms": latency}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_test_one, n): n for n in nodes}
        for future in as_completed(futures):
            results.append(future.result())

    # 排序：有延迟的按延迟升序，None 排最后
    results.sort(key=lambda r: (r["latency_ms"] is None, r["latency_ms"] or 99999))
    return results


def test_all_nodes_api(nodes: list[dict], timeout: int = 5000) -> list[dict]:
    """通过 Clash API 并发测试所有节点延迟。

    返回: [{"tag": ..., "latency_ms": ... | None}, ...]
    """
    results = []

    def _test_one(node):
        tag = node["tag"]
        latency = test_via_clash_api(tag, timeout=timeout)
        return {"tag": tag, "latency_ms": latency}

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_test_one, n): n for n in nodes}
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda r: (r["latency_ms"] is None, r["latency_ms"] or 99999))
    return results


def select_node_via_api(tag: str) -> bool:
    """通过 Clash API 切换 proxy selector 到指定节点。"""
    url = f"{CLASH_API_BASE}/proxies/proxy"
    headers = {
        "Authorization": f"Bearer {CLASH_API_SECRET}",
        "Content-Type": "application/json",
    }
    body = json.dumps({"name": tag}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 204 or resp.status == 200
    except Exception:
        return False


def is_clash_api_available() -> bool:
    """检查 Clash API 是否可用。"""
    result = _api_request("/version", timeout=2)
    return result is not None
