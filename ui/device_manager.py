"""
设备管理TUI界面
在TUI内提供设备的增删改功能，包含网卡IP策略和管理URL配置
使用自定义数字选择菜单替代pick，避免curses中文兼容问题
"""

from rich.console import Console
from rich.table import Table

from core.config_manager import (
    add_device, update_device, delete_device,
    save_config, validate_device, get_device_groups, get_devices_by_group
)
from core.network_utils import (
    calculate_adapter_ip_auto, validate_ip, validate_subnet_mask
)
from ui import widgets
from ui.header import show_header

console = Console()


def show_device_manager(config: dict) -> dict:
    """设备管理主菜单 - 按分组展示（分组标题嵌入选项列表）"""
    while True:
        show_header(config)

        by_group = get_devices_by_group(config)
        groups = get_device_groups(config)

        options = []
        index_map = {}
        non_selectable = set()
        opt_idx = 0
        raw_devices = config.get("devices", [])

        if by_group:
            for g in groups:
                label = f"── [{g}] ──" if g else "── [未分组] ──"
                options.append(label)
                non_selectable.add(opt_idx)
                opt_idx += 1

                devices = by_group.get(g, [])
                for d in devices:
                    fav = "*" if d.get("favorite") else " "
                    ip_mode = "自动" if d.get("ip_mode") == "auto" else "手动"
                    group_tag = d.get("group", "")
                    display = f"  [{fav}] {d['name']} | {d['device_ip']} | {ip_mode}"
                    if group_tag:
                        display += f" | [{group_tag}]"
                    options.append(display)
                    for ri, rd in enumerate(raw_devices):
                        if rd is d:
                            index_map[opt_idx] = ri
                            break
                    opt_idx += 1
        else:
            options.append("[yellow]当前没有设备配置[/yellow]")

        # 默认索引指向第一个可选项（分组标题不可选）
        first_selectable = 1 if by_group else 0
        idx = widgets.pick_option(
            options, "设备管理 - 请选择设备或操作",
            page_size=8, default_index=first_selectable,
            fixed_tail=["[+] 添加新设备"],
            non_selectable=non_selectable,
        )
        if idx < 0:
            return config

        if idx >= len(options):
            result = _add_device_ui(config)
            if result:
                config = result
                save_config(config)
                console.print("[green][OK] 设备添加成功[/green]")
                input("\n按回车键继续...")
        elif idx in index_map:
            config = _device_action_menu(config, index_map[idx])

    return config


def _device_action_menu(config: dict, device_idx: int) -> dict:
    """设备操作子菜单（编辑/删除/收藏）"""
    show_header(config)

    raw_devices = config.get("devices", [])
    if device_idx >= len(raw_devices):
        return config

    device = raw_devices[device_idx]

    options = [
        "[E] 编辑设备",
        "[D] 删除设备",
        "[G] 设置分组",
        f"{'[*] 取消收藏' if device.get('favorite') else '[*] 设为收藏'}",
    ]

    idx = widgets.pick_option(
        options,
        f"设备: {device['name']} ({device['device_ip']}) - 请选择操作"
    )
    if idx < 0:
        return config

    if idx == 0:
        result = _edit_device_ui(config, device_idx)
        if result:
            config = result
            save_config(config)
            console.print("[green][OK] 设备修改成功[/green]")
            input("\n按回车键继续...")

    elif idx == 1:
        if widgets.confirm(f"确认删除设备 '{device['name']}'?", default=False):
            delete_device(config, device_idx)
            save_config(config)
            console.print("[green][OK] 设备已删除[/green]")
            input("\n按回车键继续...")

    elif idx == 2:
        config = _set_device_group_ui(config, device_idx)

    elif idx == 3:
        device["favorite"] = not device.get("favorite", False)
        update_device(config, device_idx, device)
        save_config(config)
        status = "已收藏" if device["favorite"] else "已取消收藏"
        console.print(f"[green][OK] {status}[/green]")
        input("\n按回车键继续...")

    return config


def _set_device_group_ui(config: dict, device_idx: int) -> dict:
    """设置设备分组（选择已有分组或新建分组）"""
    raw_devices = config.get("devices", [])
    if device_idx >= len(raw_devices):
        return config

    device = raw_devices[device_idx]
    current_group = device.get("group", "") or "未分组"

    while True:
        show_header(config)
        console.print(f"\n[bold cyan]设备: {device['name']} | 当前分组: {current_group}[/bold cyan]")

        mode_options = [
            "选择已有分组",
            "新建分组",
        ]
        mode_idx = widgets.pick_option(mode_options, "请选择操作")
        if mode_idx < 0:
            return config

        if mode_idx == 0:
            # 选择已有分组
            groups = get_device_groups(config)
            group_options = []
            group_map = {}
            for i, g in enumerate(groups):
                label = g if g else "未分组"
                group_options.append(label)
                group_map[i] = g

            if not group_options:
                console.print("[yellow][!] 暂无分组，请先新建[/yellow]")
                input("\n按回车键继续...")
                continue

            g_idx = widgets.pick_option(group_options, "请选择分组", page_size=8)
            if g_idx < 0:
                continue

            selected_group = group_map.get(g_idx, "")
            device["group"] = selected_group
            update_device(config, device_idx, device)
            save_config(config)
            old_label = current_group
            new_label = selected_group if selected_group else "未分组"
            console.print(f"[green][OK] 分组已更改: {old_label} -> {new_label}[/green]")
            input("\n按回车键返回...")
            return config

        else:
            # 新建分组
            new_group = input("请输入新分组名称: ").strip()
            if not new_group:
                console.print("[red]分组名称不能为空[/red]")
                continue

            if not widgets.confirm(f"确认将设备 '{device['name']}' 加入分组 '{new_group}'?", default=True):
                continue

            device["group"] = new_group
            update_device(config, device_idx, device)
            save_config(config)
            console.print(f"[green][OK] 设备已加入分组: {new_group}[/green]")
            input("\n按回车键返回...")
            return config


def _add_device_ui(config: dict) -> dict:
    """添加设备（交互式输入）"""
    try:
        device = {}

        # 设备名称
        name = input("设备名称: ").strip()
        if not name:
            console.print("[red]设备名称不能为空[/red]")
            return None
        device["name"] = name

        # 设备分组
        group = input("设备分组/站点 (可选，如 Site-A): ").strip()
        device["group"] = group

        # 设备默认IP
        device_ip = input("设备默认IP (如 10.251.251.251): ").strip()
        if not device_ip or not validate_ip(device_ip):
            console.print("[red]IP地址格式无效[/red]")
            return None
        device["device_ip"] = device_ip

        # 子网掩码
        mask = input("子网掩码 (默认 255.255.255.0): ").strip()
        if not mask:
            mask = "255.255.255.0"
        if not validate_subnet_mask(mask):
            console.print("[red]子网掩码格式无效[/red]")
            return None
        device["subnet_mask"] = mask

        # 网卡IP策略
        ip_mode_options = [
            "自动计算（取网段最后可用IP）",
            "手动指定网卡IP",
        ]
        mode_idx = widgets.pick_option(ip_mode_options, "选择网卡IP策略")
        if mode_idx < 0:
            return None

        if mode_idx == 0:
            device["ip_mode"] = "auto"
            device["adapter_ip"] = ""
            try:
                preview_ip = calculate_adapter_ip_auto(device_ip, mask)
                console.print(f"[cyan]-> 自动计算网卡IP: {preview_ip}[/cyan]")
            except ValueError as e:
                console.print(f"[red]自动计算失败: {e}[/red]")
                return None
        else:
            device["ip_mode"] = "manual"
            adapter_ip = input("网卡IP (如 10.251.251.250): ").strip()
            if not adapter_ip or not validate_ip(adapter_ip):
                console.print("[red]网卡IP格式无效[/red]")
                return None
            device["adapter_ip"] = adapter_ip

        # 网关
        gateway = input("网关 (可选，回车跳过): ").strip()
        device["gateway"] = gateway if gateway and validate_ip(gateway) else ""

        # 管理页面URL
        mgmt_url = input(f"管理页面URL (默认 https://{device_ip}): ").strip()
        if not mgmt_url:
            mgmt_url = f"https://{device_ip}"
        device["management_url"] = mgmt_url

        # 收藏
        device["favorite"] = False

        # 验证
        errors = validate_device(device)
        if errors:
            for err in errors:
                console.print(f"[red][X] {err}[/red]")
            return None

        # 确认
        console.print()
        _display_device_summary(device)

        if not widgets.confirm("确认保存?", default=True):
            return None

        add_device(config, device)
        return config

    except (KeyboardInterrupt, EOFError):
        return None


def _edit_device_ui(config: dict, device_idx: int) -> dict:
    """编辑已有设备（同添加流程，预填充已有值）"""
    raw_devices = config.get("devices", [])
    if device_idx >= len(raw_devices):
        return None

    old_device = raw_devices[device_idx]

    try:
        device = {}

        # 设备名称
        name = input(f"设备名称 [{old_device['name']}]: ").strip()
        device["name"] = name if name else old_device["name"]

        # 设备分组
        old_group = old_device.get("group", "")
        group = input(f"设备分组/站点 [{old_group}]: ").strip()
        device["group"] = group if group else old_group

        # 设备IP
        device_ip = input(f"设备默认IP [{old_device['device_ip']}]: ").strip()
        device_ip = device_ip if device_ip else old_device["device_ip"]
        if not validate_ip(device_ip):
            console.print("[red]IP地址格式无效[/red]")
            return None
        device["device_ip"] = device_ip

        # 子网掩码
        mask = input(f"子网掩码 [{old_device.get('subnet_mask', '255.255.255.0')}]: ").strip()
        mask = mask if mask else old_device.get("subnet_mask", "255.255.255.0")
        if not validate_subnet_mask(mask):
            console.print("[red]子网掩码格式无效[/red]")
            return None
        device["subnet_mask"] = mask

        # 网卡IP策略
        current_mode = "自动" if old_device.get("ip_mode") == "auto" else "手动"
        ip_mode_options = [
            f"自动计算（当前: {current_mode}）",
            "手动指定网卡IP",
        ]
        mode_idx = widgets.pick_option(
            ip_mode_options,
            f"选择网卡IP策略 (当前: {current_mode})"
        )
        if mode_idx < 0:
            return None

        if mode_idx == 0:
            device["ip_mode"] = "auto"
            device["adapter_ip"] = ""
            try:
                preview_ip = calculate_adapter_ip_auto(device_ip, mask)
                console.print(f"[cyan]-> 自动计算网卡IP: {preview_ip}[/cyan]")
            except ValueError as e:
                console.print(f"[red]自动计算失败: {e}[/red]")
                return None
        else:
            device["ip_mode"] = "manual"
            old_adapter_ip = old_device.get("adapter_ip", "")
            adapter_ip = input(f"网卡IP [{old_adapter_ip}]: ").strip()
            adapter_ip = adapter_ip if adapter_ip else old_adapter_ip
            if not validate_ip(adapter_ip):
                console.print("[red]网卡IP格式无效[/red]")
                return None
            device["adapter_ip"] = adapter_ip

        # 网关
        old_gw = old_device.get("gateway", "")
        gateway = input(f"网关 [{old_gw}]: ").strip()
        device["gateway"] = gateway if gateway and validate_ip(gateway) else old_gw

        # 管理URL
        old_url = old_device.get("management_url", f"https://{device_ip}")
        mgmt_url = input(f"管理页面URL [{old_url}]: ").strip()
        device["management_url"] = mgmt_url if mgmt_url else old_url

        # 保留收藏状态
        device["favorite"] = old_device.get("favorite", False)

        # 验证
        errors = validate_device(device)
        if errors:
            for err in errors:
                console.print(f"[red][X] {err}[/red]")
            return None

        console.print()
        _display_device_summary(device)

        if not widgets.confirm("确认保存修改?", default=True):
            return None

        update_device(config, device_idx, device)
        return config

    except (KeyboardInterrupt, EOFError):
        return None


def _display_device_summary(device: dict):
    """显示设备配置摘要"""
    table = Table(title="设备配置摘要", show_header=False)
    table.add_column("字段", style="cyan")
    table.add_column("值", style="white")

    table.add_row("设备名称", device.get("name", ""))
    group = device.get("group", "")
    table.add_row("分组/站点", group if group else "[未分组]")
    table.add_row("设备IP", device.get("device_ip", ""))
    table.add_row("IP策略", "自动计算" if device.get("ip_mode") == "auto" else f"手动: {device.get('adapter_ip', '')}")
    table.add_row("子网掩码", device.get("subnet_mask", ""))
    table.add_row("网关", device.get("gateway", "无"))
    table.add_row("管理URL", device.get("management_url", "无"))

    console.print(table)
