"""
固定标题栏模块
提供清屏 + 重绘标题栏功能，所有页面共用
网卡状态带 3 秒缓存，避免每次渲染都触发 WMI 查询导致卡顿
"""

import os
import time
from rich.console import Console

from core.adapter_manager import get_network_adapters, get_adapter_by_mac
from core.ip_configurator import get_current_ip_config
from core.config_manager import get_last_adapter_mac
from core.version import APP_VERSION_DISPLAY

console = Console()

# 网卡状态缓存（3 秒 TTL）
CACHE_TTL = 3.0
_cache = {"ts": 0.0, "mac": "", "adapter": None, "ip_config": None}


def _get_cached_adapter_status(mac: str):
    """带缓存的网卡状态查询。"""
    now = time.time()
    if mac and mac == _cache.get("mac") and now - _cache.get("ts", 0) < CACHE_TTL:
        return _cache.get("adapter"), _cache.get("ip_config")

    adapters = get_network_adapters()
    adapter = get_adapter_by_mac(adapters, mac) if mac else None
    ip_config = get_current_ip_config(adapter.name) if adapter else None
    _cache.update({"ts": now, "mac": mac, "adapter": adapter, "ip_config": ip_config})
    return adapter, ip_config


def show_header(config: dict = None):
    """
    清屏并绘制固定标题栏。
    config 为 None 时只显示标题不显示网卡状态。
    同步加载网卡信息，直接显示。
    """
    os.system('cls')

    console.print("[bold]========================================[/bold]")
    console.print(f"[bold]     网络设备IP一键配置工具 {APP_VERSION_DISPLAY}[/bold]")
    console.print("[bold]========================================[/bold]")

    if config is not None:
        try:
            last_mac = get_last_adapter_mac(config)
            adapter, ip_config = _get_cached_adapter_status(last_mac)
            if last_mac and adapter:
                console.print(f"  当前网卡: {adapter.name} ({adapter.mac})")
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
            elif last_mac:
                console.print("  [dim]上次选择的网卡已不可用[/dim]")
            else:
                console.print("  [dim]尚未选择网卡[/dim]")
        except Exception:
            console.print("  [dim]网卡状态获取失败[/dim]")

    # 上次操作结果（成功路径不再强制等待回车，结果在此展示）
    try:
        from ui import status
        last_result = status.get_last_result()
        if last_result:
            console.print(f"  {last_result}")
    except Exception:
        pass

    # 新版本检测提示（后台线程结果，非阻塞）
    try:
        from core import update_check
        result = update_check.default_checker.get_result()
        if result:
            latest = result.get("version", "")
            if update_check.is_newer(latest):
                console.print(
                    f"  [yellow]发现新版本 {latest}（当前 {APP_VERSION_DISPLAY}），主菜单按 u 检查更新[/yellow]"
                )
    except Exception:
        pass

    console.print("[bold]----------------------------------------[/bold]")
