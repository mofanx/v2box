"""协议链接解析模块，支持 vless / vmess / ss / trojan / hysteria2"""

from v2box.parsers.vless import parse_vless
from v2box.parsers.vmess import parse_vmess
from v2box.parsers.shadowsocks import parse_ss
from v2box.parsers.trojan import parse_trojan
from v2box.parsers.hysteria2 import parse_hysteria2

# 协议前缀 -> 解析函数 的映射
PARSERS = {
    "vless://": parse_vless,
    "vmess://": parse_vmess,
    "ss://": parse_ss,
    "trojan://": parse_trojan,
    "hysteria2://": parse_hysteria2,
    "hy2://": parse_hysteria2,
}


def parse_link(link: str) -> dict | None:
    """自动识别协议并解析单条链接，返回 sing-box outbound 字典，失败返回 None。"""
    link = link.strip()
    for prefix, parser in PARSERS.items():
        if link.lower().startswith(prefix):
            try:
                return parser(link)
            except Exception:
                return None
    return None


def supported_protocols() -> list[str]:
    """返回支持的协议列表。"""
    return ["vless", "vmess", "ss (Shadowsocks)", "trojan", "hysteria2 / hy2"]
