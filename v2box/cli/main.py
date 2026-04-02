"""v2box CLI — 一键将代理节点导入 sing-box，轻松管理、测速、切换节点。

常用命令:
  v2box add <链接或文件>     导入节点
  v2box ls                  查看节点列表
  v2box test                测速所有节点
  v2box use <节点名>         手动选择节点
  v2box auto                自动选择最快节点
  v2box start / stop / restart   管理 sing-box 服务
"""

import sys

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from v2box import __version__
from v2box.parsers import parse_link, supported_protocols
from v2box.core.store import (
    load_nodes, add_nodes, remove_node, clear_nodes,
    load_state, set_selected_node, set_mode, set_lan, set_port,
)
from v2box.core.config import (
    build_config, write_config, config_to_json, SINGBOX_CONFIG_PATH,
    MIXED_LISTEN_PORT,
)
from v2box.core import service

console = Console()

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])
BANNER = r"""[bold cyan]
 ╦  ╦╔═╗╔╗ ╔═╗═╗ ╦
 ╚╗╔╝╠═╝╠╩╗║ ║╔╩╦╝
  ╚╝ ╩  ╚═╝╚═╝╩ ╚═[/bold cyan]  [dim]v{ver}[/dim]
"""


@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.version_option(__version__, "-V", "--version", prog_name="v2box")
@click.pass_context
def cli(ctx):
    """v2box — 一键将代理节点导入 sing-box，轻松管理、测速、切换。

    \b
    快速开始:
      1. 导入节点:  v2box add "vless://..." 或 v2box add nodes.txt
      2. 应用配置:  v2box apply
      3. 启动服务:  v2box start
      4. 测速:      v2box test
      5. 选节点:    v2box use <节点名>

    \b
    支持的协议: vless, vmess, shadowsocks, trojan, hysteria2
    """
    if ctx.invoked_subcommand is None:
        console.print(BANNER.format(ver=__version__))
        console.print(ctx.get_help())


# ── add: 导入节点 ─────────────────────────────────────────────

@cli.command("add")
@click.argument("sources", nargs=-1)
@click.option("-u", "--url", help="从订阅 URL 获取节点（base64 编码）")
def cmd_add(sources, url):
    """导入代理节点。

    \b
    SOURCES 可以是:
      - 一个或多个节点链接 (vless://, vmess://, ss://, trojan://, hy2://)
      - 一个包含节点链接的文本文件路径（每行一个链接）
      - 多种来源可混合使用

    \b
    示例:
      v2box add "vless://uuid@server:443?..."
      v2box add nodes.txt
      v2box add nodes.txt "vmess://..."
      v2box add -u https://example.com/sub
    """
    import os
    import base64
    import urllib.request

    links = []

    # 处理订阅 URL
    if url:
        console.print(f"[dim]正在获取订阅: {url}[/dim]")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "v2box"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
            try:
                decoded = base64.b64decode(raw).decode("utf-8")
            except Exception:
                decoded = raw.decode("utf-8")
            links.extend(decoded.strip().splitlines())
            console.print(f"[green]✓[/green] 从订阅获取到 {len(links)} 行")
        except Exception as e:
            console.print(f"[red]✗[/red] 获取订阅失败: {e}")
            return

    # 处理参数
    if not sources and not url:
        # 尝试从 stdin 读取
        if not sys.stdin.isatty():
            stdin_data = sys.stdin.read().strip()
            if stdin_data:
                links.extend(stdin_data.splitlines())
        else:
            console.print("[yellow]请提供节点链接、文件路径或订阅 URL[/yellow]")
            console.print("用法: v2box add <链接或文件> 或 v2box add -u <订阅URL>")
            return

    for source in sources:
        if os.path.isfile(source):
            with open(source, "r", encoding="utf-8") as f:
                file_links = f.read().strip().splitlines()
            links.extend(file_links)
            console.print(f"[dim]从文件 {source} 读取 {len(file_links)} 行[/dim]")
        else:
            links.append(source)

    # 过滤空行
    links = [l.strip() for l in links if l.strip()]
    if not links:
        console.print("[yellow]未找到任何链接[/yellow]")
        return

    # 解析
    parsed = []
    for link in links:
        node = parse_link(link)
        if node:
            parsed.append(node)
            console.print(f"  [green]✓[/green] {node['type']:12s} {node['tag']}")
        else:
            short = link[:60] + "..." if len(link) > 60 else link
            console.print(f"  [red]✗[/red] 无法解析: {short}")

    if not parsed:
        console.print("[red]没有成功解析任何节点[/red]")
        return

    added, skipped = add_nodes(parsed)
    console.print(
        f"\n[bold green]完成![/bold green] 新增 {added} 个节点"
        + (f"，跳过 {skipped} 个重复" if skipped else "")
    )


# ── ls: 列出节点 ──────────────────────────────────────────────

@cli.command("ls")
@click.option("-a", "--all", "show_all", is_flag=True, help="显示详细信息")
def cmd_ls(show_all):
    """列出所有已导入的节点。

    \b
    示例:
      v2box ls
      v2box ls -a
    """
    nodes = load_nodes()
    if not nodes:
        console.print("[yellow]暂无节点，请先用 v2box add 导入[/yellow]")
        return

    state = load_state()
    selected = state.get("selected")
    mode = state.get("mode", "auto")

    table = Table(title=f"节点列表 ({len(nodes)} 个)  模式: {mode}")
    table.add_column("#", style="dim", width=4)
    table.add_column("状态", width=4)
    table.add_column("名称", style="bold")
    table.add_column("协议", style="cyan")
    table.add_column("服务器", style="dim")
    if show_all:
        table.add_column("端口", style="dim")

    for i, node in enumerate(nodes, 1):
        marker = "[green]●[/green]" if node["tag"] == selected else "[dim]○[/dim]"
        row = [
            str(i), marker, node["tag"], node["type"],
            node.get("server", ""),
        ]
        if show_all:
            row.append(str(node.get("server_port", "")))
        table.add_row(*row)

    console.print(table)


# ── rm: 删除节点 ──────────────────────────────────────────────

@cli.command("rm")
@click.argument("name")
def cmd_rm(name):
    """删除指定节点。

    \b
    NAME 可以是节点名称或序号（v2box ls 中的 # 列）。

    \b
    示例:
      v2box rm "我的节点"
      v2box rm 3
    """
    nodes = load_nodes()

    # 支持按序号删除
    try:
        idx = int(name) - 1
        if 0 <= idx < len(nodes):
            name = nodes[idx]["tag"]
    except ValueError:
        pass

    if remove_node(name):
        console.print(f"[green]✓[/green] 已删除节点: {name}")
    else:
        console.print(f"[red]✗[/red] 未找到节点: {name}")


# ── clear: 清空节点 ───────────────────────────────────────────

@cli.command("clear")
@click.confirmation_option(prompt="确定要清空所有节点吗?")
def cmd_clear():
    """清空所有已导入的节点。

    \b
    示例:
      v2box clear
    """
    clear_nodes()
    console.print("[green]✓[/green] 已清空所有节点")


# ── test: 测速 ────────────────────────────────────────────────

@cli.command("test")
@click.option("-t", "--timeout", default=5.0, help="超时时间（秒），默认 5")
@click.option("--api", is_flag=True, help="通过 Clash API 测试（需 sing-box 运行中）")
def cmd_test(timeout, api):
    """测试所有节点延迟。

    \b
    默认使用 TCP 握手测试，不需要 sing-box 运行。
    使用 --api 可通过 Clash API 测试真实代理延迟（需 sing-box 运行中）。

    \b
    示例:
      v2box test
      v2box test -t 3
      v2box test --api
    """
    from v2box.core.testing import (
        test_all_nodes_tcp, test_all_nodes_api, is_clash_api_available,
    )

    nodes = load_nodes()
    if not nodes:
        console.print("[yellow]暂无节点，请先用 v2box add 导入[/yellow]")
        return

    if api:
        if not is_clash_api_available():
            console.print("[red]Clash API 不可用，请确保 sing-box 正在运行[/red]")
            console.print("[dim]提示: 先运行 v2box apply && v2box start[/dim]")
            return
        console.print("[dim]正在通过 Clash API 测试节点延迟...[/dim]\n")
        results = test_all_nodes_api(nodes, timeout=int(timeout * 1000))
    else:
        console.print("[dim]正在测试节点 TCP 握手延迟...[/dim]\n")
        results = test_all_nodes_tcp(nodes, timeout=timeout)

    table = Table(title="测速结果")
    table.add_column("#", style="dim", width=4)
    table.add_column("名称", style="bold")
    table.add_column("延迟", justify="right")
    if not api:
        table.add_column("服务器", style="dim")

    for i, r in enumerate(results, 1):
        latency = r["latency_ms"]
        if latency is None:
            delay_str = "[red]超时[/red]"
        elif latency < 100:
            delay_str = f"[green]{latency:.0f} ms[/green]"
        elif latency < 300:
            delay_str = f"[yellow]{latency:.0f} ms[/yellow]"
        else:
            delay_str = f"[red]{latency:.0f} ms[/red]"

        row = [str(i), r["tag"], delay_str]
        if not api:
            row.append(f"{r.get('server', '')}:{r.get('port', '')}")
        table.add_row(*row)

    console.print(table)

    # 显示最快节点
    fastest = [r for r in results if r["latency_ms"] is not None]
    if fastest:
        best = fastest[0]
        console.print(
            f"\n[bold green]最快节点:[/bold green] {best['tag']}  "
            f"({best['latency_ms']:.0f} ms)"
        )
        console.print(f"[dim]提示: 运行 v2box use \"{best['tag']}\" 切换到该节点[/dim]")


# ── use: 手动选择节点 ─────────────────────────────────────────

@cli.command("use")
@click.argument("name")
def cmd_use(name):
    """手动选择要使用的节点。

    \b
    NAME 可以是节点名称或序号（v2box ls 中的 # 列）。
    选择后会自动切换到手动模式，并尝试通过 Clash API 立即切换。

    \b
    示例:
      v2box use "我的节点"
      v2box use 1
    """
    from v2box.core.testing import select_node_via_api, is_clash_api_available

    nodes = load_nodes()
    if not nodes:
        console.print("[yellow]暂无节点，请先用 v2box add 导入[/yellow]")
        return

    # 支持按序号选择
    try:
        idx = int(name) - 1
        if 0 <= idx < len(nodes):
            name = nodes[idx]["tag"]
    except ValueError:
        pass

    tags = [n["tag"] for n in nodes]
    if name not in tags:
        console.print(f"[red]✗[/red] 未找到节点: {name}")
        console.print("[dim]运行 v2box ls 查看可用节点[/dim]")
        return

    set_selected_node(name)
    set_mode("manual")
    console.print(f"[green]✓[/green] 已选择节点: [bold]{name}[/bold] (手动模式)")

    # 尝试通过 Clash API 立即切换
    if is_clash_api_available():
        if select_node_via_api(name):
            console.print("[green]✓[/green] 已通过 Clash API 切换节点（立即生效）")
        else:
            console.print("[yellow]⚠[/yellow] Clash API 切换失败，请运行 v2box apply && v2box restart")
    else:
        console.print("[dim]提示: 运行 v2box apply && v2box restart 使配置生效[/dim]")


# ── auto: 自动模式 ────────────────────────────────────────────

@cli.command("auto")
def cmd_auto():
    """切换到自动模式（自动选择最快节点）。

    \b
    自动模式下，sing-box 的 urltest 组会每 5 分钟自动测试，
    并自动切换到延迟最低的节点。

    \b
    示例:
      v2box auto
    """
    from v2box.core.testing import select_node_via_api, is_clash_api_available

    set_mode("auto")
    set_selected_node(None)
    console.print("[green]✓[/green] 已切换到 [bold]自动模式[/bold]")
    console.print("[dim]sing-box 将每 5 分钟自动测试并选择最快节点[/dim]")

    if is_clash_api_available():
        if select_node_via_api("auto"):
            console.print("[green]✓[/green] 已通过 Clash API 切换到自动选择（立即生效）")
    else:
        console.print("[dim]提示: 运行 v2box apply && v2box restart 使配置生效[/dim]")


# ── apply: 应用配置 ───────────────────────────────────────────

@cli.command("apply")
@click.option("-o", "--output", type=click.Path(), help="输出路径（默认写入 sing-box 配置）")
@click.option("--dry-run", is_flag=True, help="仅输出 JSON，不写入文件")
@click.option("--no-tun", is_flag=True, help="不使用 TUN 模式（仅 HTTP/SOCKS 代理）")
@click.option("--lan", is_flag=True, default=None, help="开启局域网代理（监听 0.0.0.0）")
@click.option("--no-lan", is_flag=True, default=None, help="关闭局域网代理（仅本机）")
@click.option("-p", "--port", "listen_port", type=int, default=None, help="代理监听端口")
def cmd_apply(output, dry_run, no_tun, lan, no_lan, listen_port):
    """根据已导入的节点生成并应用 sing-box 配置。

    \b
    示例:
      v2box apply                  # 写入 /etc/sing-box/config.json（需 sudo）
      v2box apply --dry-run        # 仅打印配置 JSON
      v2box apply -o config.json   # 输出到指定文件
      v2box apply --no-tun         # 不使用 TUN 模式
      v2box apply --lan            # 开启局域网代理
      v2box apply --lan -p 7890    # 局域网代理 + 自定义端口
    """
    from pathlib import Path

    nodes = load_nodes()
    if not nodes:
        console.print("[yellow]暂无节点，请先用 v2box add 导入[/yellow]")
        return

    state = load_state()
    mode = state.get("mode", "auto")
    selected = state.get("selected")

    # 确定 LAN 和端口：命令行参数 > state 保存的值 > 默认值
    use_lan = state.get("lan", False)
    if lan:
        use_lan = True
    elif no_lan:
        use_lan = False
    use_port = listen_port or state.get("port", MIXED_LISTEN_PORT)

    config = build_config(nodes, mode=mode, selected=selected,
                          lan=use_lan, port=use_port)

    # 移除 TUN
    if no_tun:
        config["inbounds"] = [ib for ib in config["inbounds"] if ib.get("type") != "tun"]

    if dry_run:
        console.print(config_to_json(config))
        return

    target = Path(output) if output else SINGBOX_CONFIG_PATH

    # 写入系统路径需要 sudo
    if not output and str(target).startswith("/etc/"):
        import subprocess
        import json
        json_str = config_to_json(config)
        result = subprocess.run(
            ["sudo", "tee", str(target)],
            input=json_str, capture_output=True, text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]✗[/red] 写入失败: {result.stderr.strip()}")
            return
    else:
        write_config(config, target)

    listen_info = f"{'0.0.0.0' if use_lan else '127.0.0.1'}:{use_port}"
    console.print(f"[green]✓[/green] 配置已写入: {target}")
    console.print(f"[dim]  节点数: {len(nodes)}  模式: {mode}  监听: {listen_info}[/dim]")

    # 检查配置
    ok, msg = service.check_config(str(target))
    if ok:
        console.print(f"[green]✓[/green] {msg}")
    else:
        console.print(f"[red]✗[/red] {msg}")


# ── start / stop / restart / status ──────────────────────────

@cli.command("start")
def cmd_start():
    """启动 sing-box 服务。

    \b
    示例:
      v2box start
    """
    if not service.is_installed():
        console.print("[red]✗ sing-box 未安装，请先安装[/red]")
        console.print("[dim]参考: https://sing-box.sagernet.org/installation/[/dim]")
        return
    ok, msg = service.start()
    console.print(f"[green]✓[/green] {msg}" if ok else f"[red]✗[/red] {msg}")


@cli.command("stop")
def cmd_stop():
    """停止 sing-box 服务。

    \b
    示例:
      v2box stop
    """
    ok, msg = service.stop()
    console.print(f"[green]✓[/green] {msg}" if ok else f"[red]✗[/red] {msg}")


@cli.command("restart")
def cmd_restart():
    """重启 sing-box 服务。

    \b
    示例:
      v2box restart
    """
    ok, msg = service.restart()
    console.print(f"[green]✓[/green] {msg}" if ok else f"[red]✗[/red] {msg}")


@cli.command("status")
def cmd_status():
    """查看 sing-box 运行状态和当前节点信息。

    \b
    示例:
      v2box status
    """
    # sing-box 安装信息
    if service.is_installed():
        ver = service.get_version() or "未知版本"
        console.print(f"[green]✓[/green] sing-box 已安装  ({ver})")
    else:
        console.print("[red]✗[/red] sing-box 未安装")
        return

    # 服务状态
    is_active, detail = service.status()
    if is_active:
        console.print("[green]✓[/green] 服务运行中")
    else:
        console.print("[yellow]○[/yellow] 服务未运行")

    # 节点信息
    nodes = load_nodes()
    state = load_state()
    mode = state.get("mode", "auto")
    selected = state.get("selected")

    use_lan = state.get("lan", False)
    use_port = state.get("port", MIXED_LISTEN_PORT)
    listen_addr = "0.0.0.0" if use_lan else "127.0.0.1"

    console.print(f"\n[bold]节点:[/bold] {len(nodes)} 个已导入")
    console.print(f"[bold]模式:[/bold] {'自动' if mode == 'auto' else '手动'}")
    if mode == "manual" and selected:
        console.print(f"[bold]当前节点:[/bold] {selected}")
    console.print(f"[bold]代理监听:[/bold] {listen_addr}:{use_port}")
    if use_lan:
        console.print("[green]✓[/green] 局域网代理已开启")

    # Clash API
    from v2box.core.testing import is_clash_api_available
    if is_clash_api_available():
        console.print("[green]✓[/green] Clash API 可用 (127.0.0.1:9090)")
    elif is_active:
        console.print("[yellow]⚠[/yellow] Clash API 不可用")


# ── info: 查看配置信息 ────────────────────────────────────────

# ── lan: 局域网代理设置 ───────────────────────────────────────

@cli.command("lan")
@click.argument("action", type=click.Choice(["on", "off", "status"]), default="status")
@click.option("-p", "--port", type=int, default=None, help="自定义代理端口")
def cmd_lan(action, port):
    """管理局域网代理。

    \b
    ACTION:
      on      开启局域网代理（监听 0.0.0.0，局域网设备可用）
      off     关闭局域网代理（仅本机 127.0.0.1）
      status  查看当前局域网代理状态（默认）

    \b
    开启后，局域网内其他设备可设置 HTTP/SOCKS5 代理指向本机 IP。
    全局模式（TUN）和非全局模式均支持局域网代理。

    \b
    示例:
      v2box lan on              # 开启局域网代理
      v2box lan on -p 7890      # 开启并指定端口为 7890
      v2box lan off             # 关闭局域网代理
      v2box lan                 # 查看状态
    """
    state = load_state()
    current_lan = state.get("lan", False)
    current_port = state.get("port", MIXED_LISTEN_PORT)

    if action == "status":
        if current_lan:
            console.print(f"[green]✓[/green] 局域网代理: [bold]已开启[/bold]")
        else:
            console.print(f"[dim]○[/dim] 局域网代理: [bold]已关闭[/bold]（仅本机）")
        console.print(f"[bold]代理端口:[/bold] {current_port}")
        console.print(f"\n[dim]提示: v2box lan on 开启 | v2box lan off 关闭[/dim]")
        return

    if action == "on":
        set_lan(True)
        if port:
            set_port(port)
            current_port = port
        console.print(f"[green]✓[/green] 局域网代理已开启 (0.0.0.0:{current_port})")
        console.print(f"[dim]局域网设备可设置代理: http://本机IP:{current_port}[/dim]")
    elif action == "off":
        set_lan(False)
        if port:
            set_port(port)
            current_port = port
        console.print(f"[green]✓[/green] 局域网代理已关闭 (127.0.0.1:{current_port})")

    console.print("[dim]提示: 运行 v2box apply && v2box restart 使配置生效[/dim]")


# ── port: 快捷设置端口 ────────────────────────────────────────

@cli.command("port")
@click.argument("port_num", type=int)
def cmd_port(port_num):
    """设置代理监听端口。

    \b
    示例:
      v2box port 7890       # 将端口改为 7890
      v2box port 10808      # 恢复默认端口
    """
    if port_num < 1 or port_num > 65535:
        console.print("[red]✗[/red] 端口范围: 1-65535")
        return
    set_port(port_num)
    console.print(f"[green]✓[/green] 代理端口已设置为: {port_num}")
    console.print("[dim]提示: 运行 v2box apply && v2box restart 使配置生效[/dim]")


@cli.command("info")
def cmd_info():
    """显示 v2box 配置和环境信息。

    \b
    示例:
      v2box info
    """
    from v2box.core.store import get_data_dir

    state = load_state()
    use_lan = state.get("lan", False)
    use_port = state.get("port", MIXED_LISTEN_PORT)
    listen_addr = "0.0.0.0" if use_lan else "127.0.0.1"

    console.print(Panel(
        f"[bold]v2box[/bold] {__version__}\n\n"
        f"[bold]数据目录:[/bold]   {get_data_dir()}\n"
        f"[bold]配置路径:[/bold]   {SINGBOX_CONFIG_PATH}\n"
        f"[bold]代理端口:[/bold]   HTTP/SOCKS5 → {listen_addr}:{use_port}\n"
        f"[bold]局域网:[/bold]     {'已开启' if use_lan else '已关闭（仅本机）'}\n"
        f"[bold]API 端口:[/bold]   Clash API   → 127.0.0.1:9090\n"
        f"[bold]支持协议:[/bold]   {', '.join(supported_protocols())}",
        title="环境信息",
    ))


if __name__ == "__main__":
    cli()
