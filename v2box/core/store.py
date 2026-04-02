"""节点存储管理 — 使用 JSON 文件持久化节点列表和当前选中的节点。"""

import hashlib
import json
import os
from pathlib import Path

# 默认数据目录
DATA_DIR = Path(os.environ.get("V2BOX_DATA_DIR", Path.home() / ".config" / "v2box"))
NODES_FILE = DATA_DIR / "nodes.json"
STATE_FILE = DATA_DIR / "state.json"


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── 节点列表 CRUD ──────────────────────────────────────────────

def load_nodes() -> list[dict]:
    """加载已保存的节点列表。"""
    if not NODES_FILE.exists():
        return []
    try:
        return json.loads(NODES_FILE.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_nodes(nodes: list[dict]):
    """保存节点列表到磁盘。"""
    _ensure_dir()
    NODES_FILE.write_text(json.dumps(nodes, indent=2, ensure_ascii=False), "utf-8")


def _node_fingerprint(node: dict) -> str:
    """根据节点的实际配置内容生成指纹（排除 tag），用于判断是否真正重复。"""
    config = {k: v for k, v in sorted(node.items()) if k != "tag"}
    raw = json.dumps(config, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _unique_tag(tag: str, existing_tags: set[str]) -> str:
    """如果 tag 已存在，自动添加数字后缀使其唯一。"""
    if tag not in existing_tags:
        return tag
    i = 2
    while f"{tag} ({i})" in existing_tags:
        i += 1
    return f"{tag} ({i})"


def add_nodes(new_nodes: list[dict]) -> tuple[int, int]:
    """追加节点（按配置内容去重），返回 (新增数, 跳过数)。

    同名但配置不同的节点会自动加数字后缀区分。
    配置完全相同的节点（即使名称不同）才视为重复并跳过。
    """
    existing = load_nodes()
    existing_tags = {n["tag"] for n in existing}
    existing_fps = {_node_fingerprint(n) for n in existing}
    added = 0
    skipped = 0
    for node in new_nodes:
        fp = _node_fingerprint(node)
        if fp in existing_fps:
            skipped += 1
            continue
        # 配置不同但 tag 可能重复，需要去重 tag
        node["tag"] = _unique_tag(node["tag"], existing_tags)
        existing.append(node)
        existing_tags.add(node["tag"])
        existing_fps.add(fp)
        added += 1
    save_nodes(existing)
    return added, skipped


def remove_node(tag: str) -> bool:
    """按 tag 删除节点，返回是否成功删除。"""
    nodes = load_nodes()
    new_nodes = [n for n in nodes if n["tag"] != tag]
    if len(new_nodes) == len(nodes):
        return False
    save_nodes(new_nodes)
    return True


def clear_nodes():
    """清空所有节点。"""
    save_nodes([])


# ── 状态管理 ─────────────────────────────────────────────────

def load_state() -> dict:
    """加载状态（当前选中节点、模式等）。"""
    if not STATE_FILE.exists():
        return {"mode": "auto", "selected": None}
    try:
        return json.loads(STATE_FILE.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"mode": "auto", "selected": None}


def save_state(state: dict):
    """保存状态。"""
    _ensure_dir()
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), "utf-8")


def set_selected_node(tag: str | None):
    """设置当前选中的节点。"""
    state = load_state()
    state["selected"] = tag
    save_state(state)


def set_mode(mode: str):
    """设置模式 (auto / manual)。"""
    state = load_state()
    state["mode"] = mode
    save_state(state)


def set_lan(enabled: bool):
    """设置是否开启局域网代理。"""
    state = load_state()
    state["lan"] = enabled
    save_state(state)


def set_port(port: int):
    """设置代理监听端口。"""
    state = load_state()
    state["port"] = port
    save_state(state)


def get_data_dir() -> Path:
    """返回数据目录路径。"""
    return DATA_DIR
