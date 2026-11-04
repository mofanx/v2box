"""本地 Web 面板 — 托管 metacubexd 静态资源，直连本机 Clash API。

用法：v2box ui [--port 9099] [--web]
  默认：下载 metacubexd 静态包到 ~/.config/v2box/ui/ 后本地 serve，
  浏览器打开 http://127.0.0.1:9099/?hostname=..&port=..&secret=..#/proxies
  --web：跳过本地资源，直接打开 CDN 版面板（需联网，面板会存你的 API 配置）
"""

import io
import os
import shutil
import threading
import urllib.request
import webbrowser
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from v2box.core.config import CLASH_API_PORT, CLASH_API_SECRET
from v2box.core.store import DATA_DIR

UI_DIR = DATA_DIR / "ui"
UI_PORT = 9099
# metacubexd gh-pages 静态包（无 CORS 问题——面板与 API 都在 localhost）
PANEL_ZIP_URL = "https://github.com/MetaCubeX/metacubexd/archive/refs/heads/gh-pages.zip"
CDN_PANEL_URL = "https://metacubexd.pages.dev/"


def _panel_url(api_host: str = "127.0.0.1") -> str:
    """面板 URL，带 Clash API 连接参数（metacubexd 读取 query 自动配置后端）。"""
    q = f"hostname={api_host}&port={CLASH_API_PORT}&secret={CLASH_API_SECRET}"
    return f"{CDN_PANEL_URL}?{q}#/proxies" if _is_cdn() else \
        f"http://127.0.0.1:{_ui_port}/?{q}#/proxies"


_ui_port = UI_PORT
_is_cdn_flag = False


def _is_cdn() -> bool:
    return _is_cdn_flag


def ensure_assets(log=print) -> Path | None:
    """确保本地面板资源存在：缺失则下载 metacubexd gh-pages zip 解压到 UI_DIR。

    返回 index.html 所在目录；下载失败返回 None。
    """
    idx_dir = UI_DIR / "metacubexd-gh-pages"
    if (idx_dir / "index.html").exists():
        return idx_dir

    log("首次使用，正在下载面板静态资源 (~2MB)...")
    try:
        req = urllib.request.Request(PANEL_ZIP_URL, headers={"User-Agent": "v2box/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            blob = r.read()
        zf = zipfile.ZipFile(io.BytesIO(blob))
        tmp = UI_DIR.parent / "ui_tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        zf.extractall(tmp)
        src = next(tmp.glob("metacubexd-*/"))
        if idx_dir.exists():
            shutil.rmtree(idx_dir)
        UI_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(idx_dir))
        shutil.rmtree(tmp)
        log(f"面板已就绪: {idx_dir}")
        return idx_dir
    except Exception as e:
        log(f"下载面板失败: {e}")
        return None


def open_ui(port: int = UI_PORT, web: bool = False,
            block: bool = True, log=print) -> str | None:
    """打开面板。web=True 走 CDN 版；否则本地 serve + 浏览器打开。

    block=True（默认）时前台 serve 直到 Ctrl+C；block=False 仅启动后台
    serve 后返回（供测试/脚本用，进程退出即停）。
    """
    global _is_cdn_flag, _ui_port
    _is_cdn_flag = web
    _ui_port = port

    if web:
        url = _panel_url()
        webbrowser.open(url)
        return url

    idx_dir = ensure_assets(log)
    if idx_dir is None:
        log("本地资源不可用，回退到 CDN 面板")
        return open_ui(port=port, web=True, block=block, log=log)

    handler = partial(SimpleHTTPRequestHandler, directory=str(idx_dir))
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = _panel_url()
    if block:
        log(f"面板服务: {url}")
        log("按 Ctrl+C 停止服务")
        webbrowser.open(url)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            srv.shutdown()
        return None

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    webbrowser.open(url)
    return url
