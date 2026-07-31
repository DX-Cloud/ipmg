"""
主TUI界面
构建主界面交互流程，串联所有模块。
交互组件统一使用 ui.widgets，动作入口统一使用 ui.actions 注册表，
核心业务步骤统一使用 core.operations（与展示解耦）。
"""

import os
import time

from rich.console import Console

from core.adapter_manager import get_network_adapters, select_adapter
from core import config_manager as cm
from core import operations
from core.config_manager import (
    get_devices, get_last_adapter_mac, get_ip_history, get_adapter_backup_stack,
    export_config, import_config,
)
from core.network_utils import resolve_adapter_ip, resolve_management_url
from core.browser_launcher import open_management_page
from utils.logger import log_operation, log_error
from ui import widgets, status
from ui.actions import Action, register
from ui.device_manager import show_device_manager
from ui.header import show_header
from ui.settings import show_settings

console = Console()


# ============== 设备选择 ==============

def _build_device_menu(config: dict, keyword: str = ""):
    """
    构建设备选择菜单（分组展示 + 关键字过滤）。
    返回 (options, index_map, non_selectable, first_selectable)。
    index_map: 选项索引 -> 设备 dict。
    """
    devices = get_devices(config)
    if keyword:
        k = keyword.lower()
        devices = [
            d for d in devices
            if k in d.get("name", "").lower()
            or k in d.get("device_ip", "").lower()
            or k in d.get("group", "").lower()
        ]

    by_group = {}
    for d in devices:
        by_group.setdefault(d.get("group", ""), []).append(d)
    groups = sorted(g for g in by_group if g) + ([""] if "" in by_group else [])

    options = []
    index_map = {}
    non_selectable = set()
    opt = 0
    for g in groups:
        label = f"── [{g}] ──" if g else "── [未分组] ──"
        options.append(label)
        non_selectable.add(opt)
        opt += 1
        for d in by_group[g]:
            fav = "*" if d.get("favorite") else " "
            try:
                adapter_ip = resolve_adapter_ip(d)
                ip_preview = f"-> 网卡IP: {adapter_ip}"
            except Exception:
                ip_preview = "-> 网卡IP: 计算失败"
            mgmt_url = resolve_management_url(d)
            url_status = " [Web]" if mgmt_url else ""
            options.append(
                f"[{fav}] {d.get('name', '')} | 设备: {d.get('device_ip', '')} | {ip_preview}{url_status}"
            )
            index_map[opt] = d
            opt += 1

    first = 1 if groups else 0
    return options, index_map, non_selectable, first


def _pick_device(config: dict, adapter) -> dict:
    """
    选择设备：分组展示，支持关键字即时过滤与 c<编号> 复制。
    返回设备 dict，取消返回 None。
    """
    state = {"keyword": ""}

    def refilter(keyword: str):
        state["keyword"] = keyword
        opts, imap, ns, first = _build_device_menu(config, keyword)
        state["options"] = opts
        state["index_map"], state["non_selectable"], state["first"] = imap, ns, first
        return opts

    def note():
        return f"过滤: {state['keyword']}" if state["keyword"] else ""

    def copy_fn(idx: int) -> str:
        d = state.get("index_map", {}).get(idx)
        if not d:
            return ""
        url = resolve_management_url(d)
        text = f"{d.get('device_ip', '')}  {d.get('name', '')}"
        return f"{text}  {url}" if url else text

    refilter("")
    while True:
        idx = widgets.pick_option(
            state["options"],
            f"已选网卡: {adapter.name} | 选择设备",
            default_index=state["first"],
            non_selectable=state["non_selectable"],
            filterable=True,
            filter_fn=refilter,
            note_fn=note,
            copy_text=copy_fn,
        )
        if idx < 0:
            return None
        if idx in state["index_map"]:
            return state["index_map"][idx]


# ============== 主菜单 ==============

def show_main_menu(config: dict = None) -> str:
    """显示主菜单（基于动作注册表），返回动作 key。"""
    show_header(config)

    from ui.actions import get_actions
    actions = get_actions()
    options = [a.label for a in actions]
    hotkeys = {a.hotkey.lower(): i for i, a in enumerate(actions) if a.hotkey}

    default_idx = 0
    if config:
        last = cm.get_setting(config, "settings.last_action", "configure")
        default_idx = next((i for i, a in enumerate(actions) if a.key == last), 0)

    idx = widgets.pick_option(
        options, "请选择操作", default_index=default_idx,
        allow_back=False, hotkeys=hotkeys,
    )
    if idx < 0:
        return "exit"
    return actions[idx].key


# ============== 配置IP流程 ==============

def _maybe_jump_restore(config: dict) -> dict:
    """配置失败后提供快捷入口直接进入恢复流程。"""
    try:
        again = input("按 r 进入恢复IP，按其他键返回主菜单: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return config
    if again == "r":
        return run_restore_flow(config)
    return config


def run_configure_flow(config: dict) -> dict:
    """配置IP完整流程（选网卡 -> 选设备 -> 确认 -> 备份 -> 配置 -> 验证）。"""
    try:
        show_header(config)

        console.print("\n[bold cyan]正在获取网卡列表...[/bold cyan]")
        filter_virtual = cm.get_setting(config, "settings.filter_virtual_adapters", True)
        adapters = get_network_adapters(filter_virtual=filter_virtual)
        if not adapters:
            console.print("[red][X] 未检测到网卡[/red]")
            input("\n按回车键返回...")
            return config

        last_mac = get_last_adapter_mac(config)
        default_idx = select_adapter(adapters, last_mac)
        adapter_options = []
        for i, a in enumerate(adapters):
            state_str = "已连接" if a.is_up else "未连接"
            ip_str = f"IP: {a.display_ip}" if a.display_ip else "无IP"
            mark = " <-- 上次" if i == default_idx else ""
            adapter_options.append(
                f"[{a.type_name}] {a.name} | {state_str} | {ip_str} | MAC: {a.mac}{mark}"
            )

        adapter_idx = widgets.pick_option(
            adapter_options, "请选择网卡", default_index=default_idx
        )
        if adapter_idx < 0:
            return config
        selected_adapter = adapters[adapter_idx]

        # 首次使用引导
        devices = get_devices(config)
        if not devices:
            console.print("[yellow][!] 没有可用的设备配置[/yellow]")
            if widgets.confirm("是否现在添加第一个设备？", default=True):
                config = show_device_manager(config)
            devices = get_devices(config)
            if not devices:
                return config

        selected_device = _pick_device(config, selected_adapter)
        if selected_device is None:
            return config

        try:
            plan = operations.build_configure_plan(selected_device)
        except ValueError as e:
            console.print(f"[red][X] 网卡IP计算失败: {e}[/red]")
            input("\n按回车键返回...")
            return config

        # 简化确认：信息已在列表行展示，仅高风险字段（网关/手动IP）补充显示
        summary = (
            f"网卡 {selected_adapter.name} -> {plan['adapter_ip']}/{plan['mask']}"
            + (f"，网关 {plan['gateway']}" if plan.get("gateway") else "")
            + f"（设备 {selected_device.get('name', '')} {selected_device.get('device_ip', '')}）"
        )
        if not widgets.confirm(f"确认配置: {summary}?", default=True):
            console.print("[yellow]已取消[/yellow]")
            return config

        console.print("\n[bold cyan]正在备份网卡IP...[/bold cyan]")
        if operations.backup_current(config, selected_adapter):
            console.print("[green][OK] 网卡IP备份成功[/green]")
        else:
            console.print("[yellow][!] 网卡IP备份失败（继续执行）[/yellow]")

        console.print(f"[bold cyan]正在配置网卡IP为 {plan['adapter_ip']}...[/bold cyan]")
        try:
            success = operations.apply_static(selected_adapter.name, plan)
        except RuntimeError as e:
            console.print(f"[red][X] IP配置失败: {e}[/red]")
            log_operation("配置IP", selected_adapter.name, selected_device.get("name", ""), f"失败: {e}")
            return _maybe_jump_restore(config)

        if not success:
            console.print("[red][X] IP配置失败（WMI返回失败）[/red]")
            log_operation("配置IP", selected_adapter.name, selected_device.get("name", ""), "失败: WMI返回失败")
            return _maybe_jump_restore(config)

        console.print("[green][OK] IP配置成功[/green]")
        log_operation("配置IP", selected_adapter.name, selected_device.get("name", ""), "成功")

        try:
            operations.save_state(config, selected_adapter.mac)
        except Exception as e:
            console.print(f"[yellow][!] 配置保存失败（不影响本次IP配置）: {e}[/yellow]")

        console.print(f"\n[bold cyan]正在Ping设备 {selected_device.get('device_ip')} 验证连通性...[/bold cyan]")
        time.sleep(1)
        if operations.verify_device(selected_device.get("device_ip", ""), timeout=3):
            console.print(f"[green][OK] 设备 {selected_device.get('device_ip')} 可达[/green]")
        else:
            console.print(
                f"[yellow][!] 设备 {selected_device.get('device_ip')} 暂不可达"
                "（可能设备未开机或需要等待）[/yellow]"
            )

        operations.run_hooks(
            config, "after_configure",
            device=selected_device.get("name", ""),
            adapter=selected_adapter.name,
        )

        mgmt_url = plan.get("mgmt_url")
        if mgmt_url:
            if cm.get_setting(config, "settings.auto_open_page", False):
                console.print(f"[cyan]管理页面: {mgmt_url}[/cyan]")
                if open_management_page(selected_device):
                    console.print("[green][OK] 已自动打开管理页面[/green]")
                else:
                    console.print(f"[yellow][!] 浏览器打开失败，请手动访问: {mgmt_url}[/yellow]")
            else:
                console.print(f"\n[cyan]管理页面: {mgmt_url}[/cyan]")
                try:
                    open_now = input("按 1 打开管理页面，按其他键返回: ").strip()
                except (KeyboardInterrupt, EOFError):
                    open_now = ""
                if open_now == "1":
                    if open_management_page(selected_device):
                        console.print("[green][OK] 管理页面已打开[/green]")
                    else:
                        console.print(f"[yellow][!] 浏览器打开失败，请手动访问: {mgmt_url}[/yellow]")

        status.set_last_result(f"[OK] 配置完成: {selected_adapter.name} -> {plan['adapter_ip']}")
        return config

    except Exception as e:
        import traceback
        traceback.print_exc()
        log_error("配置IP流程", str(e))
        input("\n按回车键返回...")
        return config


# ============== 恢复IP流程 ==============

def _adapter_options(adapters, default_idx: int) -> list:
    options = []
    for i, a in enumerate(adapters):
        state_str = "已连接" if a.is_up else "未连接"
        ip_str = f"IP: {a.display_ip}" if a.display_ip else "无IP"
        mark = " <-- 上次" if i == default_idx else ""
        options.append(
            f"[{a.type_name}] {a.name} | {state_str} | {ip_str} | MAC: {a.mac}{mark}"
        )
    return options


def run_restore_flow(config: dict) -> dict:
    """恢复IP流程（DHCP + 多级撤销栈 + 历史记录）。"""
    try:
        show_header(config)

        console.print("\n[bold cyan]正在获取网卡列表...[/bold cyan]")
        filter_virtual = cm.get_setting(config, "settings.filter_virtual_adapters", True)
        adapters = get_network_adapters(filter_virtual=filter_virtual)
        if not adapters:
            console.print("[red][X] 未检测到网卡[/red]")
            input("\n按回车键返回...")
            return config

        last_mac = get_last_adapter_mac(config)
        default_idx = select_adapter(adapters, last_mac)
        adapter_idx = widgets.pick_option(
            _adapter_options(adapters, default_idx),
            "请选择要恢复IP的网卡",
            default_index=default_idx,
        )
        if adapter_idx < 0:
            return config
        selected_adapter = adapters[adapter_idx]

        history = get_ip_history(config, selected_adapter.mac)
        backup_stack = get_adapter_backup_stack(config, selected_adapter.mac)

        options = ["恢复为 DHCP（自动获取）"]
        option_sources = []

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

        console.print(f"\n[bold cyan]已选网卡: {selected_adapter.name}[/bold cyan]")
        # 默认高亮撤销栈顶（最近一次备份），一步恢复
        default_restore_idx = 1 if backup_stack else 0
        idx = widgets.pick_option(
            options, "请选择要恢复的IP配置", default_index=default_restore_idx
        )
        if idx < 0:
            return config

        if idx == 0:
            return _do_restore_dhcp(config, selected_adapter)

        source_type, source = option_sources[idx - 1]
        if source_type == "undo":
            return _do_undo_restore(config, selected_adapter, source)
        return _do_history_restore(config, selected_adapter, source)

    except Exception as e:
        log_error("恢复IP流程", str(e))
        console.print(f"[red][X] 发生错误: {e}[/red]")
        input("\n按回车键返回...")
        return config


def _do_restore_dhcp(config: dict, adapter) -> dict:
    """执行DHCP恢复。"""
    console.print(f"\n将恢复网卡 [cyan]{adapter.name}[/cyan] 为 DHCP 自动获取")
    if not widgets.confirm("确认恢复?", default=True):
        return config
    try:
        ok, message, config = operations.restore_to_dhcp(config, adapter)
        if ok:
            console.print("[green][OK] 已恢复为 DHCP 自动获取[/green]")
            log_operation("恢复IP", adapter.name, result="成功 -> DHCP")
            operations.run_hooks(config, "after_restore", adapter=adapter.name)
            status.set_last_result(f"[OK] 恢复完成: {adapter.name} -> DHCP")
        else:
            console.print(f"[red][X] {message}[/red]")
    except RuntimeError as e:
        console.print(f"[red][X] {e}[/red]")
    return config


def _do_undo_restore(config: dict, adapter, backup: dict) -> dict:
    """执行撤销恢复（先应用成功再弹出，失败保留备份栈）。"""
    mode_label = "DHCP" if backup.get("is_dhcp") else f"静态IP: {backup.get('ip', '')}"
    console.print(f"\n[bold]--- 撤销确认 ---[/bold]")
    console.print(f"  网卡:     {adapter.name}")
    console.print(f"  恢复到:   {mode_label}")
    if not widgets.confirm("确认执行撤销?", default=True):
        console.print("[yellow]已取消[/yellow]")
        return config

    ok, message, config = operations.undo_to_backup(config, adapter, backup)
    if ok:
        console.print(f"[green][OK] 已恢复为: {message}[/green]")
        log_operation("撤销IP", adapter.name, result=f"撤销到 {message}")
        operations.run_hooks(config, "after_restore", adapter=adapter.name)
        status.set_last_result(f"[OK] 撤销完成: {adapter.name} -> {message}")
    else:
        console.print(f"[red][X] {message}[/red]")
    return config


def _do_history_restore(config: dict, adapter, record: dict) -> dict:
    """执行历史记录恢复。"""
    ip = record.get("ip", "")
    mask = record.get("mask", "255.255.255.0")
    gateway = record.get("gateway", "")

    console.print(f"\n将恢复网卡 [cyan]{adapter.name}[/cyan] 的IP配置:")
    console.print(f"  -> IP: {ip} / {mask}")
    if gateway:
        console.print(f"  -> 网关: {gateway}")
    if not widgets.confirm("确认恢复?", default=True):
        return config

    try:
        ok, message, config = operations.history_restore(config, adapter, record)
        if ok:
            console.print(f"[green][OK] 已恢复为静态IP: {message}[/green]")
            log_operation("恢复IP", adapter.name, result=f"成功 -> {message}")
            operations.run_hooks(config, "after_restore", adapter=adapter.name)
            status.set_last_result(f"[OK] 恢复完成: {adapter.name} -> {message}")
        else:
            console.print(f"[red][X] {message}[/red]")
    except RuntimeError as e:
        console.print(f"[red][X] {e}[/red]")
    return config


# ============== 导入导出 ==============

def run_export_import_flow(config: dict, mode: str) -> dict:
    """配置导入导出流程。"""
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
            status.set_last_result("[OK] 配置已导出")

        elif mode == "import":
            path = input("导入路径 (如 D:\\config_backup.yaml): ").strip()
            if not path:
                console.print("[yellow]已取消[/yellow]")
                return config
            if not os.path.exists(path):
                console.print(f"[red][X] 文件不存在: {path}[/red]")
                input("\n按回车键返回...")
                return config

            if not widgets.confirm("导入将覆盖当前配置，确认?", default=False):
                return config

            try:
                new_config = import_config(path)
            except ValueError as e:
                console.print(f"[red][X] 导入失败: {e}[/red]")
                input("\n按回车键返回...")
                return config
            cm.save_config(new_config)
            console.print("[green][OK] 配置导入成功[/green]")
            log_operation("导入配置", result=f"路径={path}")
            status.set_last_result("[OK] 配置导入成功")
            return new_config

    except Exception as e:
        log_error(f"{'导出' if mode == 'export' else '导入'}配置", str(e))
        console.print(f"[red][X] 操作失败: {e}[/red]")
        input("\n按回车键返回...")

    return config


# ============== 动作注册（主菜单入口的唯一来源） ==============

register(Action("configure", "配置IP - 选择网卡和设备，一键配置", run_configure_flow))
register(Action("restore", "恢复IP - 恢复网卡IP配置（含历史记录与撤销）", run_restore_flow))
register(Action("manage", "管理设备 - 添加/编辑/删除设备（支持分组）", show_device_manager, needs_save=True))
register(Action("export", "导出配置", lambda c: run_export_import_flow(c, "export")))
register(Action("import", "导入配置", lambda c: run_export_import_flow(c, "import")))
register(Action("settings", "设置 - 交互偏好与钩子", show_settings, hotkey="s", needs_save=True))
register(Action("exit", "退出", lambda c: c, hotkey="q"))
