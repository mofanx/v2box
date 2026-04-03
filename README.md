# v2box

**一键将代理节点链接导入 [sing-box](https://sing-box.sagernet.org/)，轻松管理、测速、切换节点。**

专为 Linux 桌面用户设计，让小白也能轻松使用 sing-box 科学上网。

---

## ✨ 功能特性

- **多协议支持** — VLESS、VMess、Shadowsocks、Trojan、Hysteria2
- **一键导入** — 支持单条链接、文件批量导入
- **订阅管理** — 添加/更新/删除订阅，一键拉取节点，支持 Base64 自动解码
- **智能去重** — 基于配置内容去重，同名不同配置的节点自动区分
- **节点测速** — TCP 握手测速 / Clash API 真实代理测速
- **手动/自动模式** — 手动选择指定节点（支持序号），或自动切换到最快节点
- **局域网代理** — 一键开启局域网共享，支持自定义端口
- **服务管理** — 启动、停止、重启 sing-box，一条命令搞定
- **简洁 CLI** — 命令短小好记，带中文帮助和示例
- **TUN 模式** — 默认开启全局透明代理（可关闭）

## 📦 安装

### 前置条件

1. **安装 sing-box**（必须）：

```bash
# Ubuntu / Debian
bash <(curl -fsSL https://sing-box.app/deb-install.sh)

# 或参考官方文档: https://sing-box.sagernet.org/installation/
```

2. **Python 3.10+**

### 安装 v2box

```bash
# 从源码安装（推荐）
cd /path/to/this/repo
pip install .

# 开发模式安装
pip install -e .
```

安装后即可使用 `v2box` 命令。

---

## 🚀 快速开始

```bash
# 1. 导入节点
v2box add "vless://uuid@server:443?security=reality&..."    # 直接粘贴链接
v2box add nodes.txt                                          # 从文件导入
v2box sub add "我的机场" https://example.com/subscribe       # 从订阅导入

# 2. 生成并应用 sing-box 配置
v2box apply

# 3. 启动 sing-box
v2box start

# 4. 测试节点延迟
v2box test

# 5. 选择最快的节点
v2box use "节点名称"   # 手动选择
v2box auto             # 切回自动模式
```

**就这么简单！** 🎉

---

## 📖 命令详解

### `v2box add` — 导入节点

```bash
# 从链接导入（支持 vless/vmess/ss/trojan/hy2）
v2box add "vless://uuid@server:443?type=ws&security=tls#我的节点"
v2box add "vmess://eyJhZGQiOi..."
v2box add "ss://..."
v2box add "trojan://password@server:443#节点名"
v2box add "hy2://password@server:443#节点名"

# 从文件批量导入（每行一个链接）
v2box add nodes.txt

# 从管道导入
cat nodes.txt | v2box add
```

### `v2box ls` — 查看节点

```bash
v2box ls        # 列出所有节点
v2box ls -a     # 显示详细信息（含端口）
```

输出示例：
```
         节点列表 (3 个)  模式: auto
┌───┬────┬──────────────┬──────┬─────────────┐
│ # │ 状 │ 名称         │ 协议 │ 服务器      │
├───┼────┼──────────────┼──────┼─────────────┤
│ 1 │ ● │ 日本节点      │ vless│ jp.example  │
│ 2 │ ○ │ 美国节点      │ vmess│ us.example  │
│ 3 │ ○ │ 香港节点      │ ss   │ hk.example  │
└───┴────┴──────────────┴──────┴─────────────┘
```

### `v2box test` — 测速

```bash
v2box test          # TCP 握手测速（不需要 sing-box 运行）
v2box test -t 3     # 设置超时为 3 秒
v2box test --api    # 通过 Clash API 测真实代理延迟（需 sing-box 运行）
```

### `v2box use` — 手动选节点

```bash
v2box use "日本节点"    # 按名称选择
v2box use 1            # 按序号选择（v2box ls 中的 #）
```

选择后自动切换到**手动模式**。如果 sing-box 正在运行，会通过 Clash API **立即切换**，无需重启。

### `v2box auto` — 自动模式

```bash
v2box auto    # 切换到自动模式
```

自动模式下，sing-box 的 `urltest` 组会每 5 分钟自动测试所有节点，并切换到延迟最低的节点。

### `v2box apply` — 生成配置

```bash
v2box apply                  # 写入 /etc/sing-box/config.json（需 sudo）
v2box apply --dry-run        # 仅打印配置，不写入文件
v2box apply -o my.json       # 输出到指定文件
v2box apply --no-tun         # 不使用 TUN 模式（仅 HTTP/SOCKS 代理）
v2box apply --lan            # 开启局域网代理
v2box apply --lan -p 7890    # 局域网代理 + 自定义端口
v2box apply --no-lan         # 关闭局域网代理
```

### `v2box start / stop / restart` — 服务管理

```bash
v2box start      # 启动 sing-box
v2box stop       # 停止 sing-box
v2box restart    # 重启 sing-box
```

### `v2box status` — 查看状态

```bash
v2box status
```

输出示例：
```
✓ sing-box 已安装  (sing-box version 1.11.0)
✓ 服务运行中
节点: 3 个已导入
模式: 自动
代理监听: 0.0.0.0:7890
✓ 局域网代理已开启
✓ Clash API 可用 (127.0.0.1:9090)
```

### `v2box lan` — 局域网代理

```bash
v2box lan                 # 查看局域网代理状态
v2box lan on              # 开启局域网代理（监听 0.0.0.0）
v2box lan on -p 7890      # 开启并指定端口为 7890
v2box lan off             # 关闭局域网代理（仅本机）
```

开启后，局域网内其他设备可将 HTTP/SOCKS5 代理指向本机 IP + 端口。TUN 全局模式和非全局模式均支持。

### `v2box port` — 设置端口

```bash
v2box port 7890       # 将代理端口改为 7890
v2box port 10808      # 恢复默认端口
```

### `v2box rm / clear` — 删除节点

```bash
v2box rm "节点名称"    # 删除指定节点
v2box rm 2            # 按序号删除
v2box clear           # 清空所有节点（需确认）
```

### `v2box sub` — 订阅管理

```bash
# 添加订阅并立即拉取节点
v2box sub add "我的机场" https://provider.example/sub/token

# 查看订阅列表
v2box sub ls

# 更新所有订阅（重新拉取并替换旧节点）
v2box sub update

# 更新指定订阅
v2box sub update "我的机场"

# 删除订阅及其关联节点
v2box sub rm "我的机场"
v2box sub rm 1                    # 按序号删除
```

订阅支持 Base64 编码自动解码，兼容主流机场订阅格式。更新订阅时会自动替换该订阅的旧节点。

### `v2box info` — 环境信息

```bash
v2box info
```

---

## 🔧 代理设置

### TUN 模式（全局透明代理）

默认配置包含 TUN 入站，sing-box 以 root 运行时会自动接管系统流量，无需手动设置代理环境变量。

如果不需要 TUN 模式：

```bash
v2box apply --no-tun
```

### HTTP / SOCKS5 代理（非 TUN 模式）

v2box 默认在 `127.0.0.1:10808` 启动 HTTP/SOCKS5 混合代理端口：

```bash
# 临时设置代理
export http_proxy=http://127.0.0.1:10808
export https_proxy=http://127.0.0.1:10808

# 或在 .bashrc / .zshrc 中添加
```

### 局域网代理

开启局域网代理后，同一网络下的其他设备（手机、平板等）也可以通过本机代理访问外网：

```bash
# 开启局域网代理
v2box lan on

# 开启并指定端口
v2box lan on -p 7890

# 应用并重启
v2box apply && v2box restart

# 其他设备设置代理: http://本机IP:7890
```

全局模式（TUN）和非全局模式均支持局域网代理。

---

## 📁 数据存储

| 文件 | 路径 | 说明 |
|------|------|------|
| 节点数据 | `~/.config/v2box/nodes.json` | 已导入的节点列表 |
| 订阅数据 | `~/.config/v2box/subs.json` | 已添加的订阅列表 |
| 状态信息 | `~/.config/v2box/state.json` | 模式、选中节点、端口、LAN 设置 |
| sing-box 配置 | `/etc/sing-box/config.json` | 生成的 sing-box 配置 |

可通过环境变量 `V2BOX_DATA_DIR` 自定义数据目录。

---

## 🔄 典型工作流

### 新手首次使用

```bash
# 安装 v2box
pip install .

# 导入节点（找机场要一个订阅链接，或者一些节点链接）
v2box sub add "我的机场" https://your-subscription-url

# 应用配置并启动
v2box apply
v2box start

# 搞定！现在可以科学上网了
```

### 日常使用

```bash
# 觉得网速慢？测一下
v2box test

# 切到最快的
v2box use 1

# 或者懒得管，交给自动
v2box auto
```

### 更新节点

```bash
# 更新所有订阅（自动替换旧节点）
v2box sub update

# 重新应用并重启
v2box apply && v2box restart
```

---

## ❓ 常见问题

**Q: apply 时提示权限不足？**
> sing-box 配置在 `/etc/sing-box/` 下，需要 sudo 权限。v2box 会自动调用 sudo tee 写入。

**Q: test 和 test --api 有什么区别？**
> `v2box test` 仅测 TCP 握手延迟，不需要 sing-box 运行，速度快但不代表真实代理速度。
> `v2box test --api` 通过 sing-box 的 Clash API 发送真实 HTTP 请求测试，更准确但需要服务运行中。

**Q: 手动模式和自动模式有什么区别？**
> 自动模式：sing-box 自动每 5 分钟测试并选最快节点。
> 手动模式：固定使用你指定的节点，不会自动切换。

**Q: 如何设置开机自启？**
> ```bash
> sudo systemctl enable sing-box
> ```

**Q: 如何让局域网内其他设备也能用代理？**
> ```bash
> v2box lan on -p 7890
> v2box apply && v2box restart
> # 其他设备设置 HTTP 代理: 本机 IP:7890
> ```

**Q: 导入重复节点会怎样？**
> v2box 基于节点的实际配置内容去重（而非仅按名称）。配置完全相同的节点会被跳过；
> 同名但配置不同的节点会自动加后缀区分，如 `节点名 (2)`。

**Q: 订阅更新后旧节点怎么处理？**
> `v2box sub update` 会自动删除该订阅的旧节点，然后导入新节点。手动添加的节点不受影响。

---

## 📄 License

MIT
