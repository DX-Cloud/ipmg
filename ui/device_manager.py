"""
设备管理TUI界面
在TUI内提供设备的增删改功能，包含网卡IP策略和管理URL配置
使用自定义数字选择菜单替代pick，避免curses中文兼容问题
"""

from rich.console import Console
from rich.table import Table

from core.config_manager import (
    get_devices, add_device, update_device, delete_device,
    save_config, validate_device, get_device_groups, get_devices_by_group
)
from core.network_utils import (
    calculate_adapter_ip_auto, validate_ip, validate_subnet_mask
)
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


def show_device_manager(config: dict) -> dict:
    """设备管理主菜单 - 按分组展示"""
    while True:
        show_header(config)

        all_devices = get_devices(config)
        by_group = get_devices_by_group(config)
        groups = get_device_groups(config)

        # 构建分组显示的选项列表 + 索引映射
        options = []
        index_map = {}  # options索引 -> config设备列表索引
        opt_idx = 0

        if by_group:
            for g in groups:
                label = f"── [分组: {g}] ──" if g else "── [未分组] ──"
                options.append(label)
                opt_idx += 1
                devices = by_group.get(g, [])
                for d in devices:
                    fav = "*" if d.get("favorite") else " "
                    ip_mode = "自动" if d.get("ip_mode") == "auto" else "手动"
                    options.append(
                        f"  [{fav}] {d['name']} | 设备IP: {d['device_ip']} | 模式: {ip_mode}"
                    )
                    # 找到该设备在全局设备列表中的索引
                    for gi, gd in enumerate(all_devices):
                        if gd is d:
                            index_map[opt_idx] = gi
                            break
                    opt_idx += 1
        else:
            console.print("[yellow]当前没有设备配置[/yellow]")

        options.append("[+] 添加新设备")

        idx = _pick_option(options, "设备管理 - 请选择设备或操作")
        if idx < 0:
            return config

        if idx == len(options) - 1:
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

    devices = get_devices(config)
    if device_idx >= len(devices):
        return config

    device = devices[device_idx]

    options = [
        "[E] 编辑设备",
        "[D] 删除设备",
        f"{'[*] 取消收藏' if device.get('favorite') else '[*] 设为收藏'}",
    ]

    idx = _pick_option(
        options,
        f"设备: {device['name']} ({device['device_ip']}) - 请选择操作"
    )
    if idx < 0:
        return config

    if idx == 0:
        # 编辑
        result = _edit_device_ui(config, device_idx)
        if result:
            config = result
            save_config(config)
            console.print("[green][OK] 设备修改成功[/green]")
            input("\n按回车键继续...")

    elif idx == 1:
        # 删除
        confirm = input(f"确认删除设备 '{device['name']}'？(y/N): ").strip().lower()
        if confirm == "y":
            delete_device(config, device_idx)
            save_config(config)
            console.print("[green][OK] 设备已删除[/green]")
            input("\n按回车键继续...")

    elif idx == 2:
        # 收藏
        device["favorite"] = not device.get("favorite", False)
        update_device(config, device_idx, device)
        save_config(config)
        status = "已收藏" if device["favorite"] else "已取消收藏"
        console.print(f"[green][OK] {status}[/green]")
        input("\n按回车键继续...")

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
        mode_idx = _pick_option(ip_mode_options, "选择网卡IP策略")
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

        confirm = input("\n确认保存？(Y/n): ").strip().lower()
        if confirm == "n":
            return None

        add_device(config, device)
        return config

    except (KeyboardInterrupt, EOFError):
        return None


def _edit_device_ui(config: dict, device_idx: int) -> dict:
    """编辑已有设备（同添加流程，预填充已有值）"""
    devices = get_devices(config)
    if device_idx >= len(devices):
        return None

    old_device = devices[device_idx]

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
        mode_idx = _pick_option(
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
                console.print(f"[yellow][!] 自动计算警告: {e}[/yellow]")
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

        confirm = input("\n确认保存修改？(Y/n): ").strip().lower()
        if confirm == "n":
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
