"""
固定标题栏模块
提供清屏 + 重绘标题栏功能，所有页面共用
"""

import os
from rich.console import Console

from core.adapter_manager import get_network_adapters, get_adapter_by_mac
from core.ip_configurator import get_current_ip_config
from core.config_manager import get_last_adapter_mac

console = Console()


def show_header(config: dict = None):
    """
    清屏并绘制固定标题栏。
    config 为 None 时只显示标题不显示网卡状态。
    同步加载网卡信息，直接显示。
    """
    os.system('cls')

    console.print("[bold]========================================[/bold]")
    console.print("[bold]     网络设备IP一键配置工具 v1.1[/bold]")
    console.print("[bold]========================================[/bold]")

    if config is not None:
        try:
            last_mac = get_last_adapter_mac(config)
            if last_mac:
                adapters = get_network_adapters()
                adapter = get_adapter_by_mac(adapters, last_mac)
                if adapter:
                    console.print(f"  当前网卡: {adapter.name} ({adapter.mac})")
                    ip_config = get_current_ip_config(adapter.name)
                    if ip_config:
                        mode = "DHCP" if ip_config.get("is_dhcp") else "静态"
                        ip_str = ip_config.get("ip", "无") or "无"
                        mask_str = ip_config.get("mask", "无") or "无"
                        gw_str = ip_config.get("gateway", "")
                        ip_line = f"  IP 地址 : {ip_str} / {mask_str}  [{mode}]"
                        if gw_str:
                            ip_line += f"  网关: {gw_str}"
                        console.print(ip_line)
                    else:
                        console.print("  [dim]IP 地址 : 未获取[/dim]")
                else:
                    console.print("  [dim]上次选择的网卡已不可用[/dim]")
            else:
                console.print("  [dim]尚未选择网卡[/dim]")
        except Exception:
            console.print("  [dim]网卡状态获取失败[/dim]")

    console.print("[bold]----------------------------------------[/bold]")
