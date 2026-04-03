"""订阅获取与解析 — 从订阅 URL 拉取节点列表。"""

import base64
import urllib.request

from v2box.parsers import parse_link


def fetch_subscription(url: str, timeout: int = 15) -> str:
    """从 URL 获取订阅内容，自动尝试 Base64 解码。返回解码后的文本。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": "v2box/1.0",
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()

    # 尝试 Base64 解码
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        # 验证解码结果包含有效协议链接
        if any(p in decoded for p in ("://",)):
            return decoded
    except Exception:
        pass

    # 非 Base64，直接返回原文
    return raw.decode("utf-8")


def parse_subscription(content: str, source: str | None = None) -> tuple[list[dict], int]:
    """解析订阅内容中的所有节点链接。

    Args:
        content: 订阅文本内容（每行一个链接）
        source: 可选的来源标识，会写入节点的 _source 字段

    Returns:
        (成功解析的节点列表, 解析失败的行数)
    """
    lines = [l.strip() for l in content.strip().splitlines() if l.strip()]
    nodes = []
    failed = 0

    for line in lines:
        node = parse_link(line)
        if node:
            if source:
                node["_source"] = source
            nodes.append(node)
        else:
            failed += 1

    return nodes, failed
