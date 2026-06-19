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
    get_last_adapter_mac, export_config, import_config, get_adapter_backup,
    get_ip_history, pop_adapter_backup
)
from core.network_utils import resolve_adapter_ip, resolve_management_url, ping_host
from core.ip_configurator import set_static_ip, set_dhcp, get_current_ip_config
from core.browser_launcher import open_management_page, open_url
from utils.backup import backup_adapter_config
from utils.logger import log_operation, log_error
from ui.device_manager import show_device_manager
from ui.header import show_header

console = Console()


def _pick_option(options: list, title: str, default_index: int = 0,
                 allow_back: bool = True, page_size: int = 9,
                 fixed_tail: list = None) -> int:
    """
    自定义数字选择菜单，支持自动分页和固定尾部选项。
    fixed_tail: 始终显示在底部的选项，按当前页末尾编号。
    返回 -1 表示返回，>=0 表示选项索引。
    """
    if not options:
        return -1

    fixed_tail = fixed_tail or []
    total = len(options)
    total_pages = max(1, (total + page_size - 1) // page_size)
    current_page = 0

    while True:
        start = current_page * page_size
        end = min(start + page_size, total)
        page_items = options[start:end]
        fixed_base = min(start + page_size, total)

        console.print(f"\n[bold cyan]{title}[/bold cyan]")
        console.print("-" * 55)
        for i, opt in enumerate(page_items):
            num = start + i + 1
            marker = " > " if (start + i) == default_index else "   "
            console.print(f"{marker}{num}. {opt}")

        # 分页导航
        if total_pages > 1:
            bottom_options = []
            if current_page > 0:
                bottom_options.append("↑ 上一页")
            if current_page < total_pages - 1:
                bottom_options.append("↓ 下一页")
            nav_prompt = f" (第{current_page + 1}/{total_pages}页)"
            console.print(f"  [dim]{' | '.join(bottom_options)}{nav_prompt}[/dim]")

        # 固定尾部选项（按当前页末尾编号显示，返回总偏移索引）
        for i, ft in enumerate(fixed_tail):
            display_num = fixed_base + i + 1
            console.print(f"  {display_num}. {ft}")

        if allow_back:
            console.print(f"   0. <-- 返回上一页")
        console.print("-" * 55)

        max_valid = max(total, fixed_base + len(fixed_tail))
        try:
            prompt = f"请输入序号 (1-{max_valid}"
            if allow_back:
                prompt += ", 0=返回"
            if total_pages > 1:
                prompt += ", n/p 翻页"
            prompt += "): "
            choice = input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            return -1

        if not choice:
            return default_index if default_index < total else 0

        if allow_back and choice == "0":
            return -1

        # 翻页
        if total_pages > 1:
            if choice.lower() in ("n", "next", ">", "."):
                if current_page < total_pages - 1:
                    current_page += 1
                continue
            if choice.lower() in ("p", "prev", "<", ","):
                if current_page > 0:
                    current_page -= 1
                continue

        try:
            idx = int(choice) - 1
            # 固定尾部选项：按位置映射回 total 偏移返回
            if idx >= fixed_base and idx < fixed_base + len(fixed_tail):
                return total + (idx - fixed_base)
            if 0 <= idx < total:
                return idx
            hint = f"1-{max_valid}"
            if allow_back:
                hint += " 或 0 返回"
            if total_pages > 1:
                hint += ", n/p 翻页"
            console.print(f"[red]无效输入，请输入 {hint}[/red]")
        except ValueError:
            console.print("[red]请输入数字[/red]")
def _search_devices(devices: list, keyword: str) -> list:
    """
    搜索设备列表，支持名称和IP模糊匹配。
    不区分大小写，部分匹配即可。
    返回匹配的设备列表。
    """
    if not keyword:
        return devices

    keyword_lower = keyword.lower()
    matched = []

    for d in devices:
        name = d.get("name", "").lower()
        device_ip = d.get("device_ip", "").lower()

        # 搜索设备名称或设备IP
        if keyword_lower in name or keyword_lower in device_ip:
            matched.append(d)

    return matched


def _build_device_options(devices: list) -> list:
    """构建设备选项显示列表（扁平列表，无分组）"""
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
    return device_options


def _pick_grouped_device(config: dict, title: str) -> tuple:
    """
    按分组显示设备列表供用户选择。
    返回 (selected_device, selected_device_index_in_config)，返回 None 表示取消。
    """
    from core.config_manager import get_devices_by_group, get_device_groups
    by_group = get_devices_by_group(config)
    groups = get_device_groups(config)
    all_devices = get_devices(config)

    if not all_devices:
        return (None, -1)

    # 先打印分组标题，再只把设备项交给 _pick_option
    console.print(f"\n[bold cyan]{title}[/bold cyan]")
    console.print("-" * 55)

    pick_options = []
    index_map = {}
    pick_idx = 0

    for g in groups:
        label = f"── [{g}] ──" if g else "── [未分组] ──"
        console.print(f"  [bold]{label}[/bold]")
        devices = by_group.get(g, [])
        for d in devices:
            fav = "*" if d.get("favorite") else " "
            try:
                adapter_ip = resolve_adapter_ip(d)
                ip_preview = f"-> 网卡IP: {adapter_ip}"
            except Exception:
                ip_preview = "-> 网卡IP: 计算失败"
            mgmt_url = resolve_management_url(d)
            url_status = " [Web]" if mgmt_url else ""
            line = f"  [{fav}] {d['name']} | 设备: {d['device_ip']} | {ip_preview}{url_status}"
            console.print(f"  {pick_idx + 1}. {line}")
            pick_options.append(line)
            for gi, gd in enumerate(all_devices):
                if gd is d:
                    index_map[pick_idx] = gi
                    break
            pick_idx += 1

    console.print("-" * 55)
    console.print("  0. <-- 返回上一页")
    console.print("-" * 55)

    # 让用户选择
    while True:
        try:
            prompt = f"请输入序号 (1-{len(pick_options)}, 0=返回): "
            choice = input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            return (None, -1)

        if not choice:
            continue
        if choice == "0":
            return (None, -1)
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(pick_options):
                if idx in index_map:
                    return (all_devices[index_map[idx]], index_map[idx])
                return (None, -1)
            console.print(f"[red]无效输入，请输入 1-{len(pick_options)} 或 0 返回[/red]")
        except ValueError:
            console.print("[red]请输入数字[/red]")


def show_main_menu(config: dict = None) -> str:
    """显示主菜单，返回用户选择"""
    show_header(config)

    options = [
        "配置IP - 选择网卡和设备，一键配置",
        "恢复IP - 恢复网卡IP配置（含历史记录与撤销）",
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

        # Step 4: 选择设备（支持搜索，全部设备按分组展示）
        while True:
            mode_options = [
                "显示全部设备（按分组）",
                "搜索设备（输入关键词）",
            ]
            mode_idx = _pick_option(
                mode_options,
                f"已选网卡: {selected_adapter.name} | 请选择设备"
            )
            if mode_idx < 0:
                return config

            if mode_idx == 0:
                selected_device, _ = _pick_grouped_device(
                    config,
                    f"已选网卡: {selected_adapter.name} | 选择设备"
                )
                if selected_device is None:
                    return config
                break
            else:
                # 搜索设备
                console.print("\n[cyan]请输入搜索关键词（支持设备名称、IP模糊匹配）:[/cyan]")
                keyword = input("关键词: ").strip()
                if not keyword:
                    console.print("[yellow][!] 关键词为空，显示全部设备[/yellow]")
                    display_devices = devices
                else:
                    display_devices = _search_devices(devices, keyword)
                    if not display_devices:
                        console.print(f"[yellow][!] 无匹配结果（关键词: {keyword}）[/yellow]")
                        retry = input("按 1 重新搜索，按其他键返回: ").strip()
                        if retry == "1":
                            continue
                        else:
                            return config
                    else:
                        console.print(f"[green]搜索结果: {len(display_devices)} 条匹配[/green]")

                # 搜索结果是扁平列表
                device_options = _build_device_options(display_devices)
                device_idx = _pick_option(
                    device_options,
                    f"已选网卡: {selected_adapter.name} | 选择设备"
                )
                if device_idx < 0:
                    continue
                selected_device = display_devices[device_idx]
                break

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
    """恢复网卡IP流程 - 含历史记录 + 撤销栈"""
    try:
        show_header(config)

        console.print("\n[bold cyan]正在获取网卡列表...[/bold cyan]")
        adapters = get_network_adapters()

        if not adapters:
            console.print("[red][X] 未检测到网卡[/red]")
            input("\n按回车键返回...")
            return config

        # Step 1: 选择网卡
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
            adapter_options, "请选择要恢复IP的网卡", default_index=default_idx
        )
        if adapter_idx < 0:
            return config

        selected_adapter = adapters[adapter_idx]

        # Step 2: 获取历史记录 + 撤销栈
        history = get_ip_history(config, selected_adapter.mac)
        from core.config_manager import get_adapter_backup_stack
        backup_stack = get_adapter_backup_stack(config, selected_adapter.mac)

        # 构建选项列表：DHCP + 撤销栈 + 历史记录
        options = ["恢复为 DHCP（自动获取）"]
        option_sources = []  # 记录每条选项的来源和对应数据

        # 添加撤销栈条目
        if backup_stack:
            for bk in backup_stack:
                ip = bk.get("ip", "")
                mask = bk.get("mask", "")
                gw = bk.get("gateway", "")
                ts = bk.get("timestamp", "")
                mode = "DHCP" if bk.get("is_dhcp") else ip
                gw_str = f"  网关: {gw}" if gw else ""
                options.append(f"[撤销] {mode}/{mask}{gw_str}  [{ts}]")
                option_sources.append(("undo", bk))

        # 添加历史记录条目
        if history:
            for record in history:
                ip = record.get("ip", "")
                mask = record.get("mask", "")
                gateway = record.get("gateway", "")
                timestamp = record.get("timestamp", "")
                gw_str = f"  网关: {gateway}" if gateway else "  网关: 无"
                options.append(f"[历史] {ip} / {mask}  {gw_str}  [{timestamp}]")
                option_sources.append(("history", record))

        if not backup_stack and not history:
            console.print("\n[yellow][!] 该网卡暂无历史记录，仅可选择恢复为DHCP[/yellow]")

        # 显示选择界面
        console.print(f"\n[bold cyan]已选网卡: {selected_adapter.name}[/bold cyan]")
        idx = _pick_option(options, "请选择要恢复的IP配置")

        if idx < 0:
            return config

        # Step 3: 执行恢复/撤销
        if idx == 0:
            return _do_restore_dhcp(config, selected_adapter)

        source_type = option_sources[idx - 1][0]
        if source_type == "undo":
            return _do_undo_restore(config, selected_adapter, option_sources[idx - 1][1])
        else:
            return _do_history_restore(config, selected_adapter, option_sources[idx - 1][1])

    except Exception as e:
        log_error("恢复IP流程", str(e))
        console.print(f"[red][X] 发生错误: {e}[/red]")
        input("\n按回车键返回...")
        return config


def _do_restore_dhcp(config: dict, adapter) -> dict:
    """执行DHCP恢复"""
    console.print(f"\n将恢复网卡 [cyan]{adapter.name}[/cyan] 为 DHCP 自动获取")
    confirm = input("\n确认恢复？(Y/n): ").strip().lower()
    if confirm == "n":
        return config
    try:
        success = set_dhcp(adapter.name)
        if success:
            console.print("[green][OK] 已恢复为 DHCP 自动获取[/green]")
            log_operation("恢复IP", adapter.name, result="成功 -> DHCP")
        else:
            console.print("[red][X] 恢复DHCP失败[/red]")
    except RuntimeError as e:
        console.print(f"[red][X] {e}[/red]")
    save_config(config)
    input("\n按回车键返回...")
    return config


def _do_undo_restore(config: dict, adapter, backup: dict) -> dict:
    """
    执行撤销恢复（从备份栈弹出并应用）。
    非目标版本自动跳过，直到应用到选中版本。
    """
    console.print(f"\n[bold]--- 撤销确认 ---[/bold]")
    console.print(f"  网卡:     {adapter.name}")
    mode_label = "DHCP" if backup.get("is_dhcp") else f"静态IP: {backup.get('ip', '')}"
    console.print(f"  恢复到:   {mode_label}")

    confirm = input("\n确认执行撤销？(Y/n): ").strip().lower()
    if confirm == "n":
        console.print("[yellow]已取消[/yellow]")
        input("\n按回车键返回...")
        return config

    # 弹出直到目标版本
    skipped = 0
    while True:
        bk, config = pop_adapter_backup(config, adapter.mac)
        if bk is None:
            console.print("[red][X] 备份栈为空，无法撤销[/red]")
            break

        if bk is backup:
            try:
                if bk.get("is_dhcp", True):
                    result = set_dhcp(adapter.name)
                    if not result:
                        console.print("[red][X] 撤销DHCP失败[/red]")
                        break
                    console.print("[green][OK] 已恢复为 DHCP 自动获取[/green]")
                else:
                    result = set_static_ip(
                        adapter.name,
                        bk.get("ip", ""),
                        bk.get("mask", "255.255.255.0"),
                        bk.get("gateway", "") or None,
                    )
                    if not result:
                        console.print("[red][X] 撤销静态IP失败[/red]")
                        break
                    console.print(f"[green][OK] 已恢复为静态IP: {bk.get('ip', '')}[/green]")

                log_operation("撤销IP", adapter.name, result=f"撤销到 {bk.get('ip', 'DHCP')}")
                if skipped > 0:
                    console.print(f"[dim]跳过 {skipped} 级中间版本[/dim]")
            except RuntimeError as e:
                console.print(f"[red][X] 撤销失败: {e}[/red]")
            break

        skipped += 1
        console.print(f"  [dim]跳过中间版本: {bk.get('ip', 'DHCP')}[/dim]")

    save_config(config)
    input("\n按回车键返回...")
    return config


def _do_history_restore(config: dict, adapter, record: dict) -> dict:
    """执行历史记录恢复"""
    ip = record.get("ip", "")
    mask = record.get("mask", "255.255.255.0")
    gateway = record.get("gateway", "")

    console.print(f"\n将恢复网卡 [cyan]{adapter.name}[/cyan] 的IP配置:")
    console.print(f"  -> IP: {ip} / {mask}")
    if gateway:
        console.print(f"  -> 网关: {gateway}")

    confirm = input("\n确认恢复？(Y/n): ").strip().lower()
    if confirm == "n":
        return config

    try:
        success = set_static_ip(adapter.name, ip, mask, gateway if gateway else None)
        if success:
            console.print(f"[green][OK] 已恢复为静态IP: {ip}[/green]")
            log_operation("恢复IP", adapter.name, result=f"成功 -> {ip}")
        else:
            console.print("[red][X] 恢复静态IP失败[/red]")
    except RuntimeError as e:
        console.print(f"[red][X] {e}[/red]")

    save_config(config)
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
