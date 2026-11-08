"""v2box CLI — 一键将代理节点导入 sing-box，轻松管理、测速、切换节点。

常用命令:
  v2box add <链接或文件>           导入节点
  v2box sub add <名称> <URL>      添加订阅
  v2box server create <方案>      创建服务端节点
  v2box ls                        查看节点列表
  v2box test                      测速所有节点
  v2box use <节点名>               手动选择节点
  v2box auto                      自动选择最快节点
  v2box start / stop / restart    管理 sing-box 服务
"""

import os
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
    load_state, set_selected_node, set_mode, set_lan, set_port, set_strict_route, set_mux, set_pin_ip, set_tfo,
    load_subs, add_sub, remove_sub, update_sub_meta,
    remove_nodes_by_source,
)
from v2box.core.config import (
    apply_config,
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
    更多功能:
      订阅管理:    v2box sub add/update/ls/rm
      服务端管理:  v2box server create/ls/export/link/rm

    \b
    支持的协议: vless, vmess, shadowsocks, trojan, hysteria2
    """
    if ctx.invoked_subcommand is None:
        console.print(BANNER.format(ver=__version__))
        console.print(ctx.get_help())


# ── add: 导入节点 ─────────────────────────────────────────────

@cli.command("add")
@click.argument("sources", nargs=-1)
def cmd_add(sources):
    """导入代理节点。

    \b
    SOURCES 可以是:
      - 一个或多个节点链接 (vless://, vmess://, ss://, trojan://, hy2://)
      - 一个包含节点链接的文本文件路径（每行一个链接）
      - 多种来源可混合使用

    \b
    订阅导入请使用: v2box sub add <名称> <URL>

    \b
    示例:
      v2box add "vless://uuid@server:443?..."
      v2box add nodes.txt
      v2box add nodes.txt "vmess://..."
    """
    import os

    links = []

    # 处理参数
    if not sources:
        # 尝试从 stdin 读取
        if not sys.stdin.isatty():
            stdin_data = sys.stdin.read().strip()
            if stdin_data:
                links.extend(stdin_data.splitlines())
        else:
            console.print("[yellow]请提供节点链接或文件路径[/yellow]")
            console.print("用法: v2box add <链接或文件>")
            console.print("[dim]订阅导入请使用: v2box sub add <名称> <URL>[/dim]")
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
        f"\n[bold green]完成！[/bold green] 新增 {added} 个节点"
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
        console.print("[dim]提示: 运行 v2box apply && v2box reload 使配置生效[/dim]")


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
        console.print("[dim]提示: 运行 v2box apply && v2box reload 使配置生效[/dim]")


# ── apply: 应用配置 ───────────────────────────────────────────

@cli.command("apply")
@click.option("-o", "--output", type=click.Path(), help="输出路径（默认写入 sing-box 配置）")
@click.option("--dry-run", is_flag=True, help="仅输出 JSON，不写入文件")
@click.option("--no-tun", is_flag=True, help="不使用 TUN 模式（仅 HTTP/SOCKS 代理）")
@click.option("--lan", is_flag=True, default=None, help="开启局域网代理（监听 0.0.0.0）")
@click.option("--no-lan", is_flag=True, default=None, help="关闭局域网代理（仅本机）")
@click.option("-p", "--port", "listen_port", type=int, default=None, help="代理监听端口")
@click.option("--download-detour", "download_detour", default=None, help="规则集下载出站，如 direct 或节点名（默认 auto/direct）")
@click.option("--strict-route/--no-strict-route", "strict_route", default=None, help="启用/关闭 TUN strict_route（默认启用；网络频繁漫游可用 --no-strict-route 降低失效面，设置会被记住）")
@click.option("--mux/--no-mux", "mux", default=None, help="启用/关闭节点 multiplex 连接复用（需服务端支持 mux.cool；高并发减少握手开销，设置会被记住）")
@click.option("--pin-ip/--no-pin-ip", "pin_ip", default=None, help="启用/关闭节点域名预解析固化（免疫域名 DNS 波动；域名换 IP 需重新 apply，设置会被记住）")
@click.option("--tfo/--no-tfo", "tfo", default=None, help="启用/关闭 TCP Fast Open（高延迟节点握手省 1 RTT；需内核+服务端支持，设置会被记住）")
def cmd_apply(output, dry_run, no_tun, lan, no_lan, listen_port, download_detour, strict_route, mux, pin_ip, tfo):
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

    if strict_route is not None:
        set_strict_route(strict_route)
    resolved_strict = strict_route if strict_route is not None else state.get("strict_route")
    if mux is not None:
        set_mux(mux)
    resolved_mux = mux if mux is not None else state.get("mux")
    if pin_ip is not None:
        set_pin_ip(pin_ip)
    resolved_pin = pin_ip if pin_ip is not None else state.get("pin_ip")
    if tfo is not None:
        set_tfo(tfo)
    resolved_tfo = tfo if tfo is not None else state.get("tfo")

    config = apply_config(nodes, mode=mode, selected=selected,
                          lan=use_lan, port=use_port,
                          download_detour=download_detour,
                          strict_route=resolved_strict,
                          mux=resolved_mux,
                          pin_ip=resolved_pin,
                          tfo=resolved_tfo)

    # 移除 TUN
    if no_tun:
        config["inbounds"] = [ib for ib in config["inbounds"] if ib.get("type") != "tun"]

    if dry_run:
        # 用内置 print 而非 rich console —— 管道输出时 rich 会按宽度折行破坏 JSON
        print(config_to_json(config))
        return

    target = Path(output) if output else SINGBOX_CONFIG_PATH

    import subprocess
    backup = target.with_suffix(target.suffix + ".bak")
    if target.exists():
        if str(target).startswith("/etc/"):
            subprocess.run(["sudo", "cp", str(target), str(backup)],
                           capture_output=True)
        else:
            import shutil as _shutil
            _shutil.copy2(target, backup)

    # 写入系统路径需要 sudo
    if not output and str(target).startswith("/etc/"):
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
        if backup.exists():
            if str(target).startswith("/etc/"):
                subprocess.run(["sudo", "cp", str(backup), str(target)],
                               capture_output=True)
            else:
                import shutil as _shutil
                _shutil.copy2(backup, target)
            console.print(f"[yellow]已自动回滚到 {backup}，坏配置未残留[/yellow]")


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


@cli.command("reload")
def cmd_reload():
    """热加载配置（SIGHUP，进程不断，零断连切换）。

    \b
    适用: apply/sub update 之后让新配置生效，比重启更快更稳。
    """
    ok, msg = service.reload()
    if ok:
        console.print(f"[green]✓[/green] {msg}")
    else:
        console.print(f"[red]✗[/red] {msg}")
        console.print("[dim]当前平台不支持热加载，请用 v2box restart[/dim]")


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

    # Clash API + 实时节点查询
    from v2box.core.testing import is_clash_api_available, get_current_node_via_api
    if is_clash_api_available():
        console.print("[green]✓[/green] Clash API 可用 (127.0.0.1:9090)")
        current = get_current_node_via_api()
        if current:
            console.print(f"[bold green]当前连接:[/bold green] {current['now']}")
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

    console.print("[dim]提示: 运行 v2box apply && v2box reload 使配置生效[/dim]")


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
    console.print("[dim]提示: 运行 v2box apply && v2box reload 使配置生效[/dim]")


# ── sub: 订阅管理 ──────────────────────────────────────────

@cli.group("sub")
def cmd_sub():
    """订阅管理：添加、更新、查看和删除订阅。

    \b
    示例:
      v2box sub add “我的机场” https://example.com/sub/token
      v2box sub ls
      v2box sub update
      v2box sub rm “我的机场”
    """
    pass


@cmd_sub.command("add")
@click.argument("name")
@click.argument("url")
def cmd_sub_add(name, url):
    """添加订阅并立即拉取节点。

    \b
    NAME  订阅名称（自定义，用于标识）
    URL   订阅链接（支持 Base64 编码）

    \b
    示例:
      v2box sub add “我的机场” https://provider.example/sub/token
    """
    from v2box.core.subscription import fetch_subscription, parse_subscription

    if not add_sub(name, url):
        console.print(f"[red]✗[/red] 订阅名称 [bold]{name}[/bold] 已存在")
        return

    console.print(f"[dim]正在获取订阅: {name}...[/dim]")
    try:
        content = fetch_subscription(url)
    except Exception as e:
        console.print(f"[red]✗[/red] 获取失败: {e}")
        remove_sub(name)
        return

    source_tag = f"sub:{name}"
    nodes, failed = parse_subscription(content, source=source_tag)

    if not nodes:
        console.print("[red]✗[/red] 未解析到任何有效节点")
        remove_sub(name)
        return

    added, skipped = add_nodes(nodes)
    update_sub_meta(name, added)

    console.print(
        f"[green]✓[/green] 订阅 [bold]{name}[/bold] 添加成功\n"
        f"  节点: {added} 个新增"
        + (f", {skipped} 个重复跳过" if skipped else "")
        + (f", {failed} 个解析失败" if failed else "")
    )


@cmd_sub.command("update")
@click.argument("name", required=False)
def cmd_sub_update(name):
    """更新订阅（重新拉取并替换节点）。

    \b
    NAME  要更新的订阅名称（省略则更新所有订阅）

    \b
    示例:
      v2box sub update              # 更新所有订阅
      v2box sub update “我的机场”   # 更新指定订阅
    """
    from v2box.core.subscription import fetch_subscription, parse_subscription

    subs = load_subs()
    if not subs:
        console.print("[yellow]暂无订阅，请先用 v2box sub add 添加[/yellow]")
        return

    targets = subs
    if name:
        targets = [s for s in subs if s["name"] == name]
        if not targets:
            console.print(f"[red]✗[/red] 未找到订阅: {name}")
            return

    total_added = 0
    for sub in targets:
        sname = sub["name"]
        console.print(f"\n[dim]正在更新: {sname}...[/dim]")
        try:
            content = fetch_subscription(sub["url"])
        except Exception as e:
            console.print(f"  [red]✗[/red] 获取失败: {e}")
            continue

        source_tag = f"sub:{sname}"
        nodes, failed = parse_subscription(content, source=source_tag)

        if not nodes:
            console.print(f"  [yellow]⚠[/yellow] 未解析到有效节点")
            continue

        # 先删除该订阅的旧节点，再添加新节点
        removed = remove_nodes_by_source(source_tag)
        added, skipped = add_nodes(nodes)
        update_sub_meta(sname, added)
        total_added += added

        console.print(
            f"  [green]✓[/green] {sname}: {added} 个节点"
            + (f" (替换了 {removed} 个旧节点)" if removed else "")
            + (f", {failed} 个解析失败" if failed else "")
        )

    if total_added:
        console.print(f"\n[bold green]完成！[/bold green] 共更新 {total_added} 个节点")
        console.print("[dim]提示: 运行 v2box apply && v2box reload 使配置生效[/dim]")


@cmd_sub.command("auto")
@click.argument("state_", required=False)
@click.option("--hours", "hours_", type=int, default=None, help="刷新间隔小时数（默认 24）")
def cmd_sub_auto(state_, hours_):
    """订阅自动更新开关 — watchdog 守护进程按周期静默刷新订阅。

    \b
    示例:
      v2box sub auto on           # 开启（每 24h 自动更新全部订阅+应用+reload）
      v2box sub auto on --hours 6
      v2box sub auto off
    """
    from v2box.core import store
    state = store.load_state()
    if state_ not in ("on", "off"):
        en = state.get("sub_auto")
        h = state.get("sub_auto_hours") or 24
        console.print(f"订阅自动更新: {'[green]开[/green]' if en else '[dim]关[/dim]'} (间隔 {h}h)")
        console.print("[dim]用法: v2box sub auto on|off [--hours N][/dim]")
        return
    en = state_ == "on"
    store.set_sub_auto(en, hours_)
    if en:
        h = hours_ or state.get("sub_auto_hours") or 24
        console.print(f"[green]✓[/green] 订阅自动更新已开启，每 {h}h 由看门狗静默刷新并应用")
        console.print("[dim]需 v2box watchdog install 的常驻守护进程在跑[/dim]")
    else:
        console.print("[green]✓[/green] 订阅自动更新已关闭")


@cmd_sub.command("ls")
def cmd_sub_ls():
    """查看已添加的订阅列表。

    \b
    示例:
      v2box sub ls
    """
    subs = load_subs()
    if not subs:
        console.print("[yellow]暂无订阅，请先用 v2box sub add 添加[/yellow]")
        return

    table = Table(title=f"订阅列表 ({len(subs)} 个)")
    table.add_column("#", style="dim", width=4)
    table.add_column("名称", style="bold")
    table.add_column("节点数", justify="right")
    table.add_column("最后更新", style="dim")
    table.add_column("URL", style="dim", max_width=50, overflow="ellipsis")

    for i, sub in enumerate(subs, 1):
        updated = sub.get("updated_at") or "未更新"
        table.add_row(
            str(i), sub["name"], str(sub.get("node_count", 0)),
            updated, sub["url"],
        )

    console.print(table)


@cmd_sub.command("rm")
@click.argument("name")
def cmd_sub_rm(name):
    """删除订阅及其关联的所有节点。

    \b
    NAME  订阅名称或序号（v2box sub ls 中的 # 列）

    \b
    示例:
      v2box sub rm “我的机场”
      v2box sub rm 1
    """
    # 支持按序号删除
    subs = load_subs()
    try:
        idx = int(name) - 1
        if 0 <= idx < len(subs):
            name = subs[idx]["name"]
    except ValueError:
        pass

    if remove_sub(name):
        console.print(f"[green]✓[/green] 已删除订阅: [bold]{name}[/bold]（关联节点已清除）")
    else:
        console.print(f"[red]✗[/red] 未找到订阅: {name}")


# ── server: 服务端节点管理 ─────────────────────────────────────

@cli.group("server")
def cmd_server():
    """服务端节点管理：创建、查看、导出和删除服务端配置。

    \b
    支持方案:
      vless-reality   VLESS+Reality（免域名免证书，推荐）
      vless-ws        VLESS+WebSocket（配合 nginx 反代使用）
      socks           SOCKS5（适合 FRP 内网穿透场景）

    \b
    示例:
      v2box server create vless-reality -n "我的VPS"
      v2box server create vless-ws -n "WS节点" --path /secret
      v2box server create socks -n "内网节点"
      v2box server ls
      v2box server export "我的VPS" -o config.json
      v2box server link "我的VPS" --ip 1.2.3.4
      v2box server rm "我的VPS"
    """
    pass


@cmd_server.command("create")
@click.argument("scheme", type=click.Choice(["vless-reality", "vless-ws", "socks"]))
@click.option("-n", "--name", required=True, help="节点名称（用于标识）")
@click.option("-p", "--port", type=int, default=None, help="监听端口（reality 默认 443，ws 默认 10001，socks 默认 10808）")
@click.option("--sni", default="www.microsoft.com", help="Reality SNI 伪装域名（默认 www.microsoft.com）")
@click.option("--path", default="/vless-ws", help="WebSocket 路径（默认 /vless-ws）")
@click.option("--user", default=None, help="SOCKS5 用户名（默认 v2box）")
@click.option("--password", "pwd", default=None, help="SOCKS5 密码（默认随机生成）")
def cmd_server_create(scheme, name, port, sni, path, user, pwd):
    """创建服务端节点配置。

    \b
    SCHEME 方案类型:
      vless-reality   VLESS+Reality，免域名免证书，直接监听端口
      vless-ws        VLESS+WebSocket，监听本地端口，由 nginx 反代
      socks           SOCKS5，适合 FRP 内网穿透场景

    \b
    示例:
      v2box server create vless-reality -n "我的VPS"
      v2box server create vless-reality -n "日本节点" -p 8443 --sni www.apple.com
      v2box server create vless-ws -n "WS节点" -p 10002 --path /my-ws
      v2box server create socks -n "内网节点"
      v2box server create socks -n "内网节点" -p 10809 --user myuser --password mypass
    """
    from v2box.core.server_config import create_vless_reality, create_vless_ws, create_socks
    from v2box.core.store import add_server

    if scheme == "vless-reality":
        p = port or 443
        console.print(f"[dim]正在生成 VLESS+Reality 配置...[/dim]")
        data = create_vless_reality(name=name, port=p, sni=sni)
    elif scheme == "vless-ws":
        p = port or 10001
        console.print(f"[dim]正在生成 VLESS+WS 配置...[/dim]")
        data = create_vless_ws(name=name, listen_port=p, ws_path=path)
    else:
        p = port or 10808
        console.print(f"[dim]正在生成 SOCKS5 配置...[/dim]")
        data = create_socks(name=name, listen_port=p, username=user, password=pwd)

    if not add_server(data):
        console.print(f"[red]✗[/red] 名称 [bold]{name}[/bold] 已存在")
        return

    meta = data["meta"]
    console.print(f"[green]✓[/green] 服务端配置已创建: [bold]{name}[/bold]")
    console.print(f"  [bold]方案:[/bold]  {scheme}")

    if scheme == "vless-reality":
        console.print(f"  [bold]端口:[/bold]  {meta['port']}")
        console.print(f"  [bold]SNI:[/bold]   {meta['sni']}")
        console.print(f"  [bold]UUID:[/bold]  {meta['uuid']}")
    elif scheme == "vless-ws":
        console.print(f"  [bold]监听:[/bold]  127.0.0.1:{meta['listen_port']}")
        console.print(f"  [bold]路径:[/bold]  {meta['ws_path']}")
        console.print(f"  [bold]UUID:[/bold]  {meta['uuid']}")
    elif scheme == "socks":
        console.print(f"  [bold]监听:[/bold]  127.0.0.1:{meta['listen_port']}")
        console.print(f"  [bold]用户:[/bold]  {meta['username']}")
        console.print(f"  [bold]密码:[/bold]  {meta['password']}")

    console.print(f"\n[dim]下一步:[/dim]")
    console.print(f"  [dim]导出配置:  v2box server export \"{name}\" -o config.json[/dim]")
    console.print(f"  [dim]生成链接:  v2box server link \"{name}\" --ip <服务器IP>[/dim]")

    if scheme == "vless-ws" and "nginx_snippet" in data:
        console.print(f"\n[bold yellow]nginx 配置片段:[/bold yellow]")
        console.print(Panel(data["nginx_snippet"], title="nginx.conf", border_style="dim"))

    if scheme == "socks" and "frp_snippet" in data:
        console.print(f"\n[bold yellow]FRP 客户端配置片段:[/bold yellow]")
        console.print(Panel(data["frp_snippet"], title="frpc.toml", border_style="dim"))


@cmd_server.command("ls")
def cmd_server_ls():
    """查看已创建的服务端配置列表。

    \b
    示例:
      v2box server ls
    """
    from v2box.core.store import load_servers

    servers = load_servers()
    if not servers:
        console.print("[yellow]暂无服务端配置，请先用 v2box server create 创建[/yellow]")
        return

    table = Table(title=f"服务端配置 ({len(servers)} 个)")
    table.add_column("#", style="dim", width=4)
    table.add_column("名称", style="bold")
    table.add_column("方案", style="cyan")
    table.add_column("端口", justify="right")
    table.add_column("标识", style="dim", max_width=24, overflow="ellipsis")

    for i, s in enumerate(servers, 1):
        meta = s["meta"]
        p = str(meta.get("port", meta.get("listen_port", "")))
        identity = meta.get("uuid", meta.get("username", ""))
        table.add_row(str(i), meta["name"], meta["type"], p, identity)

    console.print(table)


@cmd_server.command("export")
@click.argument("name")
@click.option("-o", "--output", type=click.Path(), default=None, help="输出文件路径")
def cmd_server_export(name, output):
    """导出服务端 sing-box 配置文件。

    \b
    NAME  配置名称或序号（v2box server ls 中的 #）

    \b
    示例:
      v2box server export "我的VPS" -o config.json
      v2box server export 1 -o /etc/sing-box/config.json
      v2box server export "我的VPS"          # 输出到终端
    """
    from v2box.core.store import load_servers, get_server
    from v2box.core.server_config import server_config_to_json

    # 支持按序号
    servers = load_servers()
    try:
        idx = int(name) - 1
        if 0 <= idx < len(servers):
            name = servers[idx]["meta"]["name"]
    except ValueError:
        pass

    data = get_server(name)
    if not data:
        console.print(f"[red]✗[/red] 未找到配置: {name}")
        return

    json_str = server_config_to_json(data["server_config"])

    if output:
        from pathlib import Path
        Path(output).write_text(json_str, "utf-8")
        console.print(f"[green]✓[/green] 配置已导出: {output}")
    else:
        console.print(json_str)


@cmd_server.command("link")
@click.argument("name")
@click.option("--ip", "server_ip", required=True, help="服务器公网 IP 地址（或 FRP 服务器地址）")
@click.option("-p", "--port", "remote_port", type=int, default=None, help="远端端口（SOCKS+FRP 穿透后的端口）")
@click.option("-d", "--domain", default=None, help="域名（VLESS+WS 方案需要）")
@click.option("--import", "do_import", is_flag=True, help="同时导入到本地节点列表")
def cmd_server_link(name, server_ip, remote_port, domain, do_import):
    """生成客户端连接链接。

    \b
    NAME  配置名称或序号

    \b
    示例:
      v2box server link "我的VPS" --ip 1.2.3.4
      v2box server link "WS节点" --ip 1.2.3.4 -d example.com
      v2box server link "内网节点" --ip frp.example.com -p 16808
      v2box server link "我的VPS" --ip 1.2.3.4 --import
    """
    from v2box.core.store import load_servers, get_server
    from v2box.core.link_builder import build_vless_link, build_socks_link
    import copy

    # 支持按序号
    servers = load_servers()
    try:
        idx = int(name) - 1
        if 0 <= idx < len(servers):
            name = servers[idx]["meta"]["name"]
    except ValueError:
        pass

    data = get_server(name)
    if not data:
        console.print(f"[red]✗[/red] 未找到配置: {name}")
        return

    meta = data["meta"]
    outbound = copy.deepcopy(data["client_outbound"])

    # 填充实际地址
    if meta["type"] == "vless-reality":
        outbound["server"] = server_ip
    elif meta["type"] == "vless-ws":
        if not domain:
            console.print("[red]✗[/red] VLESS+WS 方案需要指定域名: --domain / -d")
            return
        outbound["server"] = domain
        outbound["server_port"] = 443
        outbound["tls"]["server_name"] = domain
    elif meta["type"] == "socks":
        outbound["server"] = server_ip
        if remote_port:
            outbound["server_port"] = remote_port
        else:
            console.print("[red]✗[/red] SOCKS 方案需要指定远端端口（FRP 穿透端口）: --port / -p")
            return

    # 根据类型选择链接生成器
    if meta["type"] == "socks":
        link = build_socks_link(outbound)
    else:
        link = build_vless_link(outbound)

    console.print(f"\n[bold green]客户端连接链接:[/bold green]")
    console.print(f"  {link}")

    if do_import:
        from v2box.parsers import parse_link
        node = parse_link(link)
        if node:
            added, skipped = add_nodes([node])
            if added:
                console.print(f"\n[green]✓[/green] 已导入到本地节点列表")
            else:
                console.print(f"\n[yellow]⚠[/yellow] 节点已存在，跳过导入")
        else:
            console.print(f"\n[red]✗[/red] 链接解析失败，未能导入")


@cmd_server.command("rm")
@click.argument("name")
def cmd_server_rm(name):
    """删除服务端配置。

    \b
    NAME  配置名称或序号

    \b
    示例:
      v2box server rm "我的VPS"
      v2box server rm 1
    """
    from v2box.core.store import load_servers, remove_server

    # 支持按序号
    servers = load_servers()
    try:
        idx = int(name) - 1
        if 0 <= idx < len(servers):
            name = servers[idx]["meta"]["name"]
    except ValueError:
        pass

    if remove_server(name):
        console.print(f"[green]✓[/green] 已删除: [bold]{name}[/bold]")
    else:
        console.print(f"[red]✗[/red] 未找到配置: {name}")


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

    subs = load_subs()
    nodes = load_nodes()

    console.print(Panel(
        f"[bold]v2box[/bold] {__version__}\n\n"
        f"[bold]数据目录:[/bold]   {get_data_dir()}\n"
        f"[bold]配置路径:[/bold]   {SINGBOX_CONFIG_PATH}\n"
        f"[bold]代理端口:[/bold]   HTTP/SOCKS5 → {listen_addr}:{use_port}\n"
        f"[bold]局域网:[/bold]     {'已开启' if use_lan else '已关闭（仅本机）'}\n"
        f"[bold]API 端口:[/bold]   Clash API   → 127.0.0.1:9090\n"
        f"[bold]节点数:[/bold]     {len(nodes)} 个\n"
        f"[bold]订阅数:[/bold]     {len(subs)} 个\n"
        f"[bold]支持协议:[/bold]   {', '.join(supported_protocols())}",
        title="环境信息",
    ))


@cli.group()
def config():
    """管理用户自定义配置。

    \b
    用户配置允许自定义路由规则、DNS 等设置，而节点配置由 v2box 自动管理。

    \b
    示例:
      v2box config show     # 查看当前用户配置
      v2box config edit     # 编辑用户配置
      v2box config reset    # 重置为默认配置
    """
    pass


@config.command("show")
def show():
    """显示当前用户配置。

    \b
    示例:
      v2box config show
    """
    from v2box.core.config import load_user_config, USER_CONFIG_PATH

    user_config = load_user_config()
    if user_config is None:
        console.print(f"[yellow]用户配置不存在: {USER_CONFIG_PATH}[/yellow]")
        console.print("[dim]首次运行 v2box apply 时会自动生成默认配置[/dim]")
        return

    console.print(config_to_json(user_config))


@config.command("edit")
def edit():
    """编辑用户配置。

    \b
    示例:
      v2box config edit
    """
    from v2box.core.config import load_user_config, USER_CONFIG_PATH, save_user_config
    import subprocess

    user_config = load_user_config()
    if user_config is None:
        console.print(f"[yellow]用户配置不存在，先运行 v2box apply 生成默认配置[/yellow]")
        return

    # 使用系统默认编辑器
    editor = os.environ.get("EDITOR", "vim")
    subprocess.run([editor, str(USER_CONFIG_PATH)])
    console.print(f"[green]✓[/green] 配置已编辑: {USER_CONFIG_PATH}")
    console.print("[dim]提示: 运行 v2box apply 使配置生效[/dim]")


@config.command("reset")
@click.confirmation_option(prompt="确定要重置用户配置为默认吗？")
def reset():
    """重置用户配置为默认配置。

    \b
    示例:
      v2box config reset
    """
    from v2box.core.config import reset_user_config, USER_CONFIG_PATH

    reset_user_config()
    console.print(f"[green]✓[/green] 用户配置已删除: {USER_CONFIG_PATH}")
    console.print("[dim]提示: 运行 v2box apply 会重新生成默认配置[/dim]")


# ── watchdog: 自愈看门狗 ────────────────────────────────────────

@cli.group("watchdog")
def cmd_watchdog():
    """看门狗：探测 sing-box 数据通道，确认僵死后自动修复。

    \b
    探测逻辑（症状驱动，不限定触发原因）:
      用系统解析器验证 direct DNS 链路 —— 网卡漫游/休眠唤醒/
      路由表损坏等导致 sing-box 出站僵死时该链路必然超时，
      连续多次失败后先热加载、再升级重启。

    \b
    示例:
      v2box watchdog check          # 执行一轮探测（供定时器调用）
      v2box watchdog run            # 前台常驻看门狗（任意平台可用）
      v2box watchdog install        # 安装 systemd 定时器（每 2 分钟探测）
      v2box watchdog status         # 查看定时器和看门狗状态
      v2box watchdog uninstall      # 卸载定时器
    """
    pass


@cmd_watchdog.command("check")
@click.option("--domain", "probe_domain", default=None,
              help="探测用域名（默认 www.baidu.com，应命中 dns-direct）")
@click.option("--dry-run", is_flag=True, help="只探测判定，不执行修复动作")
def cmd_watchdog_check(probe_domain, dry_run):
    """执行一轮看门狗探测。

    \b
    判定结果:
      healthy    通道正常
      stopped    sing-box 未运行（跳过，尊重手动停止）
      nodes      节点链路死（DNS 通，重启无意义，不修复）
      underlay   direct/DNS 通道僵死 → 触发修复（先 reload 再 restart）
      api_dead   进程僵死（API 无响应）→ 触发修复

    \b
    示例:
      v2box watchdog check
      v2box watchdog check --dry-run
      v2box watchdog check --domain www.aliyun.com
    """
    from v2box.core import watchdog, health

    domain = probe_domain or health.DEFAULT_PROBE_DOMAIN
    r = watchdog.check_once(probe_domain=domain, dry_run=dry_run)

    verdict = r["verdict"]
    style = {
        health.HEALTHY: "green", health.STOPPED: "dim",
        health.NODES_DEAD: "yellow",
        health.UNDERLAY_DEAD: "red", health.API_DEAD: "red",
    }.get(verdict, "white")

    dns_str = f"{r['dns_direct_ms']:.0f}ms" if r["dns_direct_ms"] is not None else "超时"
    node_str = f"{r['node_delay_ms']}ms" if r["node_delay_ms"] is not None else "-"
    # 异常/修复走 stderr：配合 unit 的 LogLevelMax=warning，healthy 行不进 journal，
    # 但 underlay/reload/restart 等关键事件仍然可见（stderr 优先级不被滤掉）
    out = (Console(stderr=True)
           if verdict != health.HEALTHY or r["action"] not in ("none", "wait")
           else console)
    out.print(
        f"[{style}]{verdict}[/{style}]  "
        f"api={'✓' if r['clash_api'] else '✗'}  "
        f"dns={dns_str}  node={node_str}  "
        f"fail_count={r['fail_count']}  action={r['action']}"
    )


@cmd_watchdog.command("run")
@click.option("--interval", default=120, show_default=True,
              help="探测间隔（秒）")
@click.option("--domain", "probe_domain", default=None, help="探测用域名")
def cmd_watchdog_run(interval, probe_domain):
    """前台常驻看门狗循环 — 任何 OS / 任何 supervisor 均可用。

    \b
    非 systemd 环境（容器、Alpine、其他 init）或手动托管时使用。
    也可以交给任意进程管理器（supervisord/runit/launchd）运行。

    \b
    示例:
      v2box watchdog run
      v2box watchdog run --interval 60
    """
    from v2box.core import watchdog, health
    console.print(f"[dim]看门狗已启动，每 {interval}s 探测一次 (Ctrl+C 退出)[/dim]")
    try:
        watchdog.run_loop(interval=interval,
                          probe_domain=probe_domain or health.DEFAULT_PROBE_DOMAIN)
    except KeyboardInterrupt:
        console.print("\n[dim]看门狗已退出[/dim]")


@cmd_watchdog.command("install")
@click.option("--with-nethook", is_flag=True,
              help="同时安装 NetworkManager 网络变更钩子（秒级恢复，Linux+NM 可选）")
def cmd_watchdog_install(with_nethook):
    """安装看门狗常驻任务（Linux: systemd 常驻服务；macOS/Windows 委托服务后端）。

    \b
    Linux 安装后:
      /etc/systemd/system/v2box-watchdog.service  (常驻探测)
      

    \b
    示例:
      v2box watchdog install
      v2box watchdog install --with-nethook   # 附带网络变更秒级恢复钩子
    """
    from v2box.core import watchdog
    ok, msgs = watchdog.install(with_nethook=with_nethook)
    for m in msgs:
        console.print(("  [green]✓[/green] " if ok or "已" in m or "ok" in m
                       else "  [red]✗[/red] ") + m)
    if ok:
        console.print("\n[bold green]✓[/bold green] 看门狗已启用，"
                      "僵死故障将自动修复")
        console.print("[dim]查看: v2box watchdog status | 卸载: v2box watchdog uninstall[/dim]")


@cmd_watchdog.command("uninstall")
def cmd_watchdog_uninstall():
    """卸载看门狗（含定时器和网络变更钩子）。

    \b
    示例:
      v2box watchdog uninstall
    """
    from v2box.core import watchdog
    ok, msgs = watchdog.uninstall()
    for m in msgs:
        console.print("  " + m)


@cmd_watchdog.command("status")
def cmd_watchdog_status():
    """查看看门狗定时器和最近状态。

    \b
    示例:
      v2box watchdog status
    """
    from v2box.core import watchdog
    active, detail = watchdog.timer_status()
    console.print(f"[bold]守护服务:[/bold] {'[green]运行中[/green]' if active else '[dim]未安装/未激活[/dim]'}")
    if watchdog.nethook_installed():
        console.print("[bold]网络钩子:[/bold] [green]已安装[/green]（链路变化秒级探测）")
    state = watchdog.load_state()
    console.print(f"[bold]失败计数:[/bold] {state.get('fail_count', 0)}")
    if state.get("last_repair_ts"):
        import datetime
        ts = datetime.datetime.fromtimestamp(state["last_repair_ts"])
        console.print(f"[bold]上次修复:[/bold] {ts:%F %T} ({state.get('last_repair_kind', '?')})")
    recent = watchdog._repairs_last_hour(state)
    console.print(f"[bold]近1h修复:[/bold] {len(recent)} 次")
    console.print(f"[bold]网络钩子:[/bold] {'[green]已安装[/green]' if watchdog.nethook_installed() else '[dim]未安装[/dim]'}")


# ── doctor: 连通性诊断 ──────────────────────────────────────────

@cli.command("doctor")
@click.option("--domain", "probe_domain", default=None, help="探测用域名")
def cmd_doctor(probe_domain):
    """全面诊断 sing-box 通道健康状态并给出修复建议。

    \b
    检查项: 服务活性 / Clash API / direct DNS 链路 / 节点延迟，
    区分"通道僵死(需重启)"与"节点死(换节点)"。

    \b
    示例:
      v2box doctor
      v2box doctor --domain www.aliyun.com
    """
    from v2box.core import health, watchdog

    domain = probe_domain or health.DEFAULT_PROBE_DOMAIN
    console.print(f"[dim]正在诊断 (探针域名: {domain})...[/dim]\n")

    if service.is_installed():
        console.print(f"[green]✓[/green] sing-box 已安装 ({service.get_version()})")
    else:
        console.print("[red]✗[/red] sing-box 未安装")
        return
    console.print(f"[bold]服务后端:[/bold] {service.backend().name}")

    report = health.diagnose(domain)

    console.print(f"[bold]服务状态:[/bold] {'运行中' if report['service_active'] else '未运行'}")
    console.print(f"[bold]Clash API:[/bold] {'✓ 可用' if report['clash_api'] else '✗ 无响应'}")
    dns_ms = report["dns_direct_ms"]
    console.print(f"[bold]direct DNS:[/bold] "
                  + (f"[green]{dns_ms:.0f}ms[/green]" if dns_ms is not None
                     else "[red]超时（direct 链路僵死）[/red]"))
    node_ms = report["node_delay_ms"]
    console.print(f"[bold]节点延迟:[/bold] "
                  + (f"{node_ms}ms" if node_ms is not None else "不可用"))

    verdict = report["verdict"]
    advice = {
        health.HEALTHY: ("green", "通道健康，无需处理"),
        health.STOPPED: ("dim", "服务未运行；如需启动: v2box start"),
        health.UNDERLAY_DEAD: ("red", "direct/DNS 通道僵死 → 建议 v2box restart；"
                                     "安装 v2box watchdog install 可自动修复"),
        health.API_DEAD: ("red", "进程僵死 → 建议 v2box restart"),
        health.NODES_DEAD: ("yellow", "DNS 正常但代理链路死 → 重启无用；"
                                     "请检查节点/订阅（v2box test / v2box use 切换）"),
    }.get(verdict, ("white", ""))

    console.print(f"\n[bold]结论:[/bold] [{advice[0]}]{verdict}[/{advice[0]}] — {advice[1]}")

    hint = health.cache_db_hint()
    if hint:
        console.print(f"[bold yellow]⚠ 状态目录异常:[/bold yellow] {hint}")

    t_active, _ = watchdog.timer_status()
    console.print(f"[bold]看门狗:[/bold] "
                  + ("[green]已启用[/green]" if t_active
                     else "[dim]未启用（v2box watchdog install 开启自动自愈）[/dim]"))



if __name__ == "__main__":
    cli()


@cli.command("upgrade")
@click.option("--yes", is_flag=True, help="跳过确认直接升级")
def cmd_upgrade(yes):
    """升级 sing-box 到最新正式版（下载 → 校验 → 安装 → 热加载）。

    \b
    上游修复（如接口翻转不自愈）通过升级 sing-box 二进制获取，
    无需手动替换。只支持 Linux amd64。
    """
    import json as _json
    import shutil as _sh
    import tempfile as _tf
    import urllib.request as _ur
    from pathlib import Path

    if not _sh.which("sing-box"):
        console.print("[red]✗[/red] 未安装 sing-box")
        return
    cur = subprocess.run(["sing-box", "version"], capture_output=True,
                         text=True).stdout.split()[1] if True else "?"
    console.print(f"当前版本: sing-box {cur}")

    try:
        with _ur.urlopen("https://api.github.com/repos/SagerNet/sing-box/releases/latest",
                         timeout=15) as resp:
            rel = _json.loads(resp.read())
        tag = rel["tag_name"]
        url = next(a["browser_download_url"] for a in rel["assets"]
                   if "linux-amd64.tar.gz" in a["name"] and "pro" not in a["name"])
    except Exception as e:
        console.print(f"[red]✗[/red] 查询最新版本失败: {e}")
        return
    console.print(f"最新版本: {tag}")
    if tag.lstrip("v") == cur.lstrip("v"):
        console.print("[green]已是最新[/green]")
        return
    if not yes and not click.confirm("确认升级？"):
        return

    with _tf.TemporaryDirectory() as td:
        tgz = Path(td) / "sb.tgz"
        try:
            _ur.urlretrieve(url, tgz)
        except Exception as e:
            console.print(f"[red]✗[/red] 下载失败: {e}")
            return
        subprocess.run(["tar", "xzf", str(tgz), "-C", td], check=True)
        bin_path = next(Path(td).glob("sing-box-*/sing-box"))
        # 新版本自检
        r = subprocess.run([str(bin_path), "version"], capture_output=True, text=True)
        if r.returncode != 0:
            console.print("[red]✗[/red] 下载的二进制无法运行")
            return
        console.print(f"下载版本验证: {r.stdout.strip()}")
        r = subprocess.run(["sudo", "install", "-m755", str(bin_path), "/usr/bin/sing-box"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            console.print(f"[red]✗[/red] 安装失败: {r.stderr.strip()}")
            return
    console.print(f"[green]✓[/green] sing-box 已升级到 {tag}")
    ok, msg = service.reload()
    console.print(("  [green]✓[/green] 已热加载" if ok else f"  [yellow]○[/yellow] reload: {msg}"))


@cli.command("upgrade")
def cmd_upgrade():
    """升级 sing-box 二进制到最新 release（自动备份+失败回滚）。

    \b
    示例:
      v2box upgrade
    """
    from v2box.core import updater
    updater.upgrade(log=lambda m: console.print(f"  {m}"))


@cli.command("rollback")
def cmd_rollback():
    """回滚到上一次 apply 前的配置（config.json.bak）。

    \b
    示例:
      v2box rollback && v2box restart
    """
    from v2box.core import updater
    ok, msg = updater.rollback_config(str(SINGBOX_CONFIG_PATH))
    console.print(f"[green]✓[/green] {msg}" if ok else f"[red]✗[/red] {msg}")


@cli.command("ui")
@click.option("-p", "--port", default=9099, show_default=True, help="本地面板服务端口")
@click.option("--web", is_flag=True, help="使用 CDN 面板（无需下载本地资源）")
def cmd_ui(port, web):
    """打开 Web 面板（metacubexd）— 可视化管理节点/流量/连接。

    \b
    默认本地托管面板资源（首次自动下载 ~2MB），浏览器打开后按 Ctrl+C 停止。
    --web 直接打开 CDN 版面板，无需本地资源。

    \b
    示例:
      v2box ui            # 本地面板 http://127.0.0.1:9099
      v2box ui --web      # CDN 面板（联网）
      v2box ui -p 8080    # 自定义端口
    """
    from v2box.core import ui
    url = ui.open_ui(port=port, web=web,
                     log=lambda m: console.print(f"  {m}"))
    if url:
        console.print(f"[green]✓[/green] 面板已打开: {url}")
