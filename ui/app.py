"""
主TUI界面
构建主界面交互流程，串联所有模块
使用自定义数字选择菜单
"""

import os
import time
from rich.console import Console
from rich.table import Table

from core.adapter_manager import get_network_adapters, select_adapter
from core.config_manager import (
    load_config, save_config, get_devices, set_last_adapter_mac,
    get_last_adapter_mac, export_config, import_config, get_adapter_backup
)
from core.network_utils import resolve_adapter_ip, resolve_management_url, ping_host
from core.ip_configurator import set_static_ip, set_dhcp, get_current_ip_config
from core.browser_launcher import open_management_page, open_url
from utils.backup import backup_adapter_config, restore_adapter_config, has_backup
from utils.logger import log_operation, log_error
from ui.device_manager import show_device_manager
from ui.header import show_header

console = Console()


def _pick_option(options: list, title: str, default_index: int = 0,
                 allow_back: bool = True) -> int:
    """
    自定义数字选择菜单。
    显示选项列表，用户输入数字选择，返回选择索引。
    自动在末尾添加 "0. 返回" 选项。
    返回 -1 表示用户选择返回。
    """
    all_options = list(options)
    if allow_back:
        all_options.append("<-- 返回上一页")

    while True:
        console.print(f"\n[bold cyan]{title}[/bold cyan]")
        console.print("-" * 55)
        for i, opt in enumerate(all_options):
            num = i + 1
            if allow_back and i == len(all_options) - 1:
                # 返回选项用 0 作为快捷键
                console.print(f"   0. {opt}")
            else:
                marker = " > " if i == default_index else "   "
                console.print(f"{marker}{num}. {opt}")
        console.print("-" * 55)

        try:
            prompt = f"请输入序号 (默认 {default_index + 1}"
            if allow_back:
                prompt += ", 0=返回"
            prompt += "): "
            choice = input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            return -1

        if not choice:
            return default_index

        # 处理返回
        if allow_back and choice == "0":
            return -1

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return idx
            else:
                max_num = len(options)
                hint = f"1-{max_num}"
                if allow_back:
                    hint += " 或 0 返回"
                console.print(f"[red]无效输入，请输入 {hint}[/red]")
        except ValueError:
            console.print("[red]请输入数字[/red]")


def show_main_menu(config: dict = None) -> str:
    """显示主菜单，返回用户选择"""
    show_header(config)

    options = [
        "配置IP - 选择网卡和设备，一键配置",
        "恢复IP - 恢复网卡原始IP配置",
        "管理设备 - 添加/编辑/删除设备",
        "导出配置",
        "导入配置",
        "退出",
    ]

    idx = _pick_option(options, "请选择操作", allow_back=False)
    actions = ["configure", "restore", "manage", "export", "import", "exit"]
    if idx < 0:
        return "exit"
    return actions[idx]


def run_configure_flow(config: dict) -> dict:
    """配置IP完整流程"""
    try:
        show_header(config)

        # Step 1: 获取网卡列表
        console.print("\n[bold cyan]正在获取网卡列表...[/bold cyan]")
        adapters = get_network_adapters()

        if not adapters:
            console.print("[red][X] 未检测到网卡[/red]")
            input("\n按回车键返回...")
            return config

        # Step 2: 选择网卡
        last_mac = get_last_adapter_mac(config)
        default_idx = select_adapter(adapters, last_mac)

        adapter_options = []
        for i, a in enumerate(adapters):
            status = "已连接" if a.is_up else "未连接"
            ip_str = f"IP: {a.display_ip}" if a.display_ip else "无IP"
            mark = " <-- 上次" if i == default_idx else ""
            adapter_options.append(
                f"[{a.type_name}] {a.name} | {status} | {ip_str} | MAC: {a.mac}{mark}"
            )

        adapter_idx = _pick_option(
            adapter_options, "请选择网卡", default_index=default_idx
        )
        if adapter_idx < 0:
            return config

        selected_adapter = adapters[adapter_idx]

        # Step 3: 获取设备列表
        devices = get_devices(config)
        if not devices:
            console.print("[yellow][!] 没有可用的设备配置，请先添加设备[/yellow]")
            input("\n按回车键返回...")
            return config

        # Step 4: 选择设备
        device_options = []
        for d in devices:
            fav = "*" if d.get("favorite") else " "
            try:
                adapter_ip = resolve_adapter_ip(d)
                ip_preview = f"-> 网卡IP: {adapter_ip}"
            except Exception:
                ip_preview = "-> 网卡IP: 计算失败"

            mgmt_url = resolve_management_url(d)
            url_status = " [Web]" if mgmt_url else ""

            device_options.append(
                f"[{fav}] {d['name']} | 设备: {d['device_ip']} | {ip_preview}{url_status}"
            )

        device_idx = _pick_option(
            device_options,
            f"已选网卡: {selected_adapter.name} | 请选择设备"
        )
        if device_idx < 0:
            return config

        selected_device = devices[device_idx]

        # Step 5: 解析网卡IP
        try:
            adapter_ip = resolve_adapter_ip(selected_device)
        except ValueError as e:
            console.print(f"[red][X] 网卡IP计算失败: {e}[/red]")
            input("\n按回车键返回...")
            return config

        # Step 6: 确认配置
        mask = selected_device.get("subnet_mask", "255.255.255.0")
        gateway = selected_device.get("gateway", "")

        console.print("\n[bold]--- 配置确认 ---[/bold]")
        console.print(f"  网卡:     {selected_adapter.name} ({selected_adapter.mac})")
        console.print(f"  设备名称: {selected_device['name']}")
        console.print(f"  设备IP:   {selected_device['device_ip']}")
        console.print(f"  网卡将配置IP: [bold green]{adapter_ip}[/bold green]")
        console.print(f"  子网掩码: {mask}")
        if gateway:
            console.print(f"  网关:     {gateway}")

        confirm = input("\n确认执行配置？(Y/n): ").strip().lower()
        if confirm == "n":
            console.print("[yellow]已取消[/yellow]")
            return config

        # Step 7: 备份当前网卡IP
        console.print("\n[bold cyan]正在备份网卡IP...[/bold cyan]")
        backup_ok = backup_adapter_config(
            selected_adapter.name, selected_adapter.mac, config
        )
        if backup_ok:
            console.print("[green][OK] 网卡IP备份成功[/green]")
        else:
            console.print("[yellow][!] 网卡IP备份失败（继续执行）[/yellow]")

        # Step 8: 执行IP配置
        console.print(f"[bold cyan]正在配置网卡IP为 {adapter_ip}...[/bold cyan]")
        try:
            success = set_static_ip(
                selected_adapter.name,
                adapter_ip,
                mask,
                gateway if gateway else None,
            )
        except Exception as e:
            console.print(f"[red][X] IP配置失败: {e}[/red]")
            log_operation("配置IP", selected_adapter.name, selected_device["name"], f"失败: {e}")
            input("\n按回车键返回...")
            return config

        if not success:
            console.print("[red][X] IP配置失败（WMI返回失败）[/red]")
            log_operation("配置IP", selected_adapter.name, selected_device["name"], "失败: WMI返回失败")
            input("\n按回车键返回...")
            return config

        console.print("[green][OK] IP配置成功[/green]")
        log_operation("配置IP", selected_adapter.name, selected_device["name"], "成功")

        # 保存配置（备份+MAC记忆）
        set_last_adapter_mac(config, selected_adapter.mac)
        save_config(config)

        # Step 9: Ping验证
        console.print(f"\n[bold cyan]正在Ping设备 {selected_device['device_ip']} 验证连通性...[/bold cyan]")
        time.sleep(1)
        if ping_host(selected_device["device_ip"], timeout=3):
            console.print(f"[green][OK] 设备 {selected_device['device_ip']} 可达[/green]")
        else:
            console.print(f"[yellow][!] 设备 {selected_device['device_ip']} 暂不可达（可能设备未开机或需要等待）[/yellow]")

        # Step 10: 提示打开管理页面
        mgmt_url = resolve_management_url(selected_device)
        if mgmt_url:
            console.print(f"\n[cyan]管理页面: {mgmt_url}[/cyan]")
            open_now = input("按 1 打开管理页面，按其他键返回: ").strip()
            if open_now == "1":
                console.print("[cyan]正在打开浏览器...[/cyan]")
                result = open_management_page(selected_device)
                if result:
                    console.print("[green][OK] 管理页面已打开[/green]")
                else:
                    console.print(f"[yellow][!] 浏览器打开失败，请手动访问: {mgmt_url}[/yellow]")
                input("\n按回车键返回...")

        return config

    except Exception as e:
        import traceback
        traceback.print_exc()
        log_error("配置IP流程", str(e))
        input("\n按回车键返回...")
        return config


def run_restore_flow(config: dict) -> dict:
    """恢复网卡IP流程"""
    try:
        show_header(config)

        console.print("\n[bold cyan]正在获取网卡列表...[/bold cyan]")
        adapters = get_network_adapters()

        if not adapters:
            console.print("[red][X] 未检测到网卡[/red]")
            input("\n按回车键返回...")
            return config

        # 筛选有备份的网卡
        backup_adapters = []
        for a in adapters:
            if has_backup(a.mac, config):
                backup = get_adapter_backup(config, a.mac)
                backup_adapters.append((a, backup))

        if not backup_adapters:
            console.print("[yellow][!] 没有找到可恢复的网卡备份[/yellow]")
            input("\n按回车键返回...")
            return config

        # 显示备份信息并选择
        options = []
        for a, backup in backup_adapters:
            ip_info = f"备份IP: {backup.get('ip', 'DHCP')}"
            mode_info = "DHCP" if backup.get("is_dhcp") else "静态IP"
            options.append(f"{a.name} | {ip_info} | {mode_info} | MAC: {a.mac}")

        idx = _pick_option(options, "请选择要恢复的网卡")
        if idx < 0:
            return config

        selected_adapter, backup = backup_adapters[idx]

        # 确认恢复
        console.print(f"\n将恢复网卡 [cyan]{selected_adapter.name}[/cyan] 的IP配置:")
        if backup.get("is_dhcp"):
            console.print("  -> 恢复为 DHCP 自动获取")
        else:
            console.print(f"  -> 恢复为静态IP: {backup.get('ip', '')} / {backup.get('mask', '')}")

        confirm = input("\n确认恢复？(Y/n): ").strip().lower()
        if confirm == "n":
            return config

        # 执行恢复
        success, error_msg = restore_adapter_config(selected_adapter.name, selected_adapter.mac, config)
        if success:
            console.print("[green][OK] 网卡IP恢复成功[/green]")
            log_operation("恢复IP", selected_adapter.name, result="成功")
        else:
            console.print(f"[red][X] 网卡IP恢复失败: {error_msg}[/red]")
            log_operation("恢复IP", selected_adapter.name, result=f"失败: {error_msg}")

        input("\n按回车键返回...")
        return config

    except Exception as e:
        log_error("恢复IP流程", str(e))
        console.print(f"[red][X] 发生错误: {e}[/red]")
        input("\n按回车键返回...")
        return config


def run_export_import_flow(config: dict, mode: str) -> dict:
    """配置导入导出流程"""
    try:
        show_header(config)

        if mode == "export":
            path = input("导出路径 (如 D:\\config_backup.yaml): ").strip()
            if not path:
                console.print("[yellow]已取消[/yellow]")
                return config

            export_config(config, path)
            console.print(f"[green][OK] 配置已导出到: {path}[/green]")
            log_operation("导出配置", result=f"路径={path}")
            input("\n按回车键返回...")

        elif mode == "import":
            path = input("导入路径 (如 D:\\config_backup.yaml): ").strip()
            if not path:
                console.print("[yellow]已取消[/yellow]")
                return config

            if not os.path.exists(path):
                console.print(f"[red][X] 文件不存在: {path}[/red]")
                input("\n按回车键返回...")
                return config

            confirm = input("导入将覆盖当前配置，确认？(y/N): ").strip().lower()
            if confirm != "y":
                return config

            new_config = import_config(path)
            save_config(new_config)
            console.print("[green][OK] 配置导入成功[/green]")
            log_operation("导入配置", result=f"路径={path}")
            input("\n按回车键返回...")
            return new_config

    except Exception as e:
        log_error(f"{'导出' if mode == 'export' else '导入'}配置", str(e))
        console.print(f"[red][X] 操作失败: {e}[/red]")
        input("\n按回车键返回...")

    return config
