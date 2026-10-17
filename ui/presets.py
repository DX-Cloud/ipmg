# -*- coding: utf-8 -*-
"""
快捷预设管理页
==============

常驻后台时用全局热键直接应用某个预设（设备 + 网卡 + 覆盖/追加）。

- 列表展示 ``config["presets"]``（字段见 ``core.config_manager.PRESET_DEFAULTS``）
- 支持：新增 / 改名 / 启用停用 / 切换模式 / 绑定（重绑、清除）热键 / 删除
- **本页只改 config，不负责落盘**：动作注册表带 ``needs_save=True``，由 main 循环统一保存
- 交互组件统一走 ``ui.widgets``，标题栏统一走 ``ui.header.show_header``
- 热键捕获用 Win32 ``GetAsyncKeyState`` 轮询（纯 ctypes，零第三方依赖），
  合法性交给 ``core.win32host.parse_hotkey``，展示用 ``core.win32host.format_hotkey``

测试接缝（``tests/test_presets.py`` 使用，生产路径不受影响）：
- ``_get_async_key_state(vk)``：唯一 Win32 调用入口
- ``_capture_supported()``：当前环境能否捕获按键（非 Windows 返回 False）
- ``_sleep(seconds)``：轮询间隔（测试置空，避免真实等待）
- ``capture_hotkey`` / ``get_devices`` / ``get_network_adapters`` / ``show_header``
  都是模块级名字，测试可直接打桩
"""

import sys
import time
from typing import Dict, List, Optional, Set, Tuple

from rich.console import Console

from core import config_manager as cm
from core import win32host
from core.adapter_manager import get_network_adapters
from core.config_manager import get_devices
from ui import widgets
from ui.header import show_header

console = Console()


# ============== 按键捕获（GetAsyncKeyState 轮询） ==============

CAPTURE_PROMPT = "请按下要绑定的组合键（Esc 取消）"
CAPTURE_TIMEOUT_S = 10.0   # 捕获超时：避免界面无限等待
POLL_INTERVAL_S = 0.02     # 轮询间隔：20ms 跟手，又不会把 CPU 拉满

_DOWN_MASK = 0x8000        # GetAsyncKeyState 返回值高位 = 当前按下

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12             # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_ESCAPE = 0x1B

_MODIFIER_VKS = (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN)
# 主键集合与 core.win32host._KEY_TO_VK 完全一致：A-Z / 0-9 / F1-F24
_PRIMARY_VKS = tuple(range(0x41, 0x5B)) + tuple(range(0x30, 0x3A)) + tuple(range(0x70, 0x88))
# 每轮扫描严格按这个顺序各查询一次 GetAsyncKeyState，
# 测试因此可以用"调用次数 // 每轮次数"来驱动按键序列（见 tests/test_presets.py）
_CAPTURE_VK_ORDER = _MODIFIER_VKS + (VK_ESCAPE,) + _PRIMARY_VKS


def _get_async_key_state(vk: int) -> int:
    """GetAsyncKeyState 的唯一入口（测试对本函数打桩，不碰真实键盘）。"""
    try:
        import ctypes
        return int(ctypes.windll.user32.GetAsyncKeyState(int(vk)))
    except Exception:
        return 0


def _capture_supported() -> bool:
    """按键捕获依赖 Win32 GetAsyncKeyState（非 Windows 直接判不支持）。"""
    return sys.platform == "win32"


def _sleep(seconds: float) -> None:
    """轮询间隔（测试打桩为空操作）。"""
    time.sleep(seconds)


def _scan_pressed() -> Set[int]:
    """
    扫描一轮当前按下的按键集合。

    每轮严格按 ``_CAPTURE_VK_ORDER`` 顺序各查询一次 ``_get_async_key_state``
    （集合推导不会短路），保证"一轮扫描 = 固定次数的底层查询"。
    """
    return {vk for vk in _CAPTURE_VK_ORDER if _get_async_key_state(vk) & _DOWN_MASK}


def _mods_from_pressed(pressed: Set[int]) -> int:
    """把按下的修饰键映射为 RegisterHotKey 的 MOD_* 位。"""
    mods = 0
    if VK_CONTROL in pressed:
        mods |= win32host.MOD_CONTROL
    if VK_MENU in pressed:
        mods |= win32host.MOD_ALT
    if VK_SHIFT in pressed:
        mods |= win32host.MOD_SHIFT
    if VK_LWIN in pressed or VK_RWIN in pressed:
        mods |= win32host.MOD_WIN
    return mods


def _wait_release(deadline: float) -> None:
    """等待所有按键松开：避免同一次按键被连续捕获两次（重试循环里尤其重要）。"""
    while time.monotonic() < deadline:
        if not _scan_pressed():
            return
        _sleep(POLL_INTERVAL_S)


def capture_hotkey(prompt: str = CAPTURE_PROMPT,
                   timeout_s: float = CAPTURE_TIMEOUT_S) -> str:
    """
    按键捕获：返回 ``'Ctrl+Alt+1'`` 形式的组合键；Esc 取消返回 ``''``。

    要求至少一个修饰键 + 单个主键（与 ``core.win32host.parse_hotkey`` 的规则一致），
    非法组合提示后重试；超时或非 Windows 环境返回 ``''``。
    """
    console.print(f"[cyan]{prompt}[/cyan]")
    if not _capture_supported():
        console.print("[yellow][!] 当前环境不支持按键捕获（需要 Windows），已跳过[/yellow]")
        return ""

    deadline = time.monotonic() + max(0.0, float(timeout_s))
    warned_modifier = False

    while time.monotonic() < deadline:
        pressed = _scan_pressed()
        if not pressed:
            _sleep(POLL_INTERVAL_S)
            continue

        primary = [vk for vk in _PRIMARY_VKS if vk in pressed]
        mods = _mods_from_pressed(pressed)

        if not primary:
            if VK_ESCAPE in pressed and mods == 0:
                console.print("[yellow]已取消热键绑定[/yellow]")
                return ""
            if mods == 0 and not warned_modifier:
                console.print("[yellow][!] 需要至少一个修饰键（Ctrl/Alt/Shift/Win）[/yellow]")
                warned_modifier = True
            _sleep(POLL_INTERVAL_S)
            continue

        vk = primary[0]
        if mods == 0:
            # 只有主键：提示后继续等待（按住不放也只提示一次），不消耗这次按键
            if not warned_modifier:
                console.print("[yellow][!] 需要至少一个修饰键（Ctrl/Alt/Shift/Win），请重新按下[/yellow]")
                warned_modifier = True
            _sleep(POLL_INTERVAL_S)
            continue

        combo = win32host.format_hotkey(mods, vk)
        if win32host.parse_hotkey(combo) is None:  # 兜底校验：注册前必须合法
            console.print(f"[red][X] 组合键不受支持: {combo}，请换一个[/red]")
            _wait_release(deadline)
            continue

        _wait_release(deadline)
        console.print(f"[green][OK] 已捕获: {combo}[/green]")
        return combo

    console.print("[yellow][!] 等待超时，已取消热键绑定[/yellow]")
    return ""


def _hotkey_conflict(config: dict, combo: str,
                     exclude_index: Optional[int] = None) -> Optional[int]:
    """
    检查组合键是否已被其它预设占用；返回冲突预设的下标，无冲突返回 None。

    用 ``parse_hotkey`` 的 ``(mods, vk)`` 结果比较，因此
    ``Control+Alt+1`` 与 ``Ctrl+Alt+1`` 视为同一个组合。
    """
    signature = win32host.parse_hotkey(combo) if combo else None
    if signature is None:
        return None
    presets = config.get("presets")
    if not isinstance(presets, list):
        return None
    for i, preset in enumerate(presets):
        if i == exclude_index or not isinstance(preset, dict):
            continue
        other = str(preset.get("hotkey") or "").strip()
        if other and win32host.parse_hotkey(other) == signature:
            return i
    return None


def _choose_hotkey(config: dict, exclude_index: Optional[int] = None) -> str:
    """
    捕获热键并去重。返回组合键；用户取消返回 ``''``。

    与已有预设重复时提示并**重新捕获**（同一组合不能绑两次）。
    """
    while True:
        combo = capture_hotkey()
        if not combo:
            return ""
        if win32host.parse_hotkey(combo) is None:
            console.print(f"[red][X] 组合键不受支持: {combo}，请重新按下[/red]")
            continue
        conflict = _hotkey_conflict(config, combo, exclude_index)
        if conflict is None:
            return combo
        presets = config.get("presets") or []
        name = str(presets[conflict].get("name") or "（未命名）")
        console.print(f"[yellow][!] 热键 {combo} 已绑定给预设 '{name}'，"
                      "同一组合不能绑两次，请换一个[/yellow]")


# ============== 列表渲染 ==============

def _mode_label(mode: str) -> str:
    return "追加" if mode == "append" else "覆盖"


def _device_text(preset: dict) -> str:
    name = str(preset.get("device_name") or "")
    ip = str(preset.get("device_ip") or "")
    return " ".join(x for x in (name, ip) if x) or "（未指定）"


def _preset_line(preset: dict) -> str:
    """一行展示一个预设的全部字段（状态/名称/设备/网卡/模式/热键）。"""
    state = "启用" if preset.get("enabled") else "停用"
    name = str(preset.get("name") or "（未命名）")
    mac = str(preset.get("adapter_mac") or "未指定网卡")
    hotkey = str(preset.get("hotkey") or "未绑定")
    return (f"[{state}] {name} | 设备: {_device_text(preset)} | 网卡: {mac}"
            f" | {_mode_label(str(preset.get('mode') or ''))} | 热键: {hotkey}")


def _build_options(presets: list) -> Tuple[List[str], Dict[int, int], Set[int]]:
    """
    构建列表选项：返回 ``(options, index_map, non_selectable)``。

    index_map 为"选项下标 → 预设下标"，与 device_manager 的分组列表保持同形状，
    以后要做分组展示时不用改调用方。
    """
    options: List[str] = []
    index_map: Dict[int, int] = {}
    non_selectable: Set[int] = set()
    if not presets:
        options.append("[dim]（还没有预设，选择下方「新增预设」开始创建）[/dim]")
        non_selectable.add(0)  # 空态占位文案不可选，保证 fixed_tail 仍可点
        return options, index_map, non_selectable
    for i, preset in enumerate(presets):
        options.append(_preset_line(preset))
        index_map[i] = i
    return options, index_map, non_selectable


def _pause(text: str = "\n按回车键继续...") -> None:
    """等待回车（EOF/Ctrl+C 静默返回，避免非交互环境把提示变成崩溃点）。"""
    try:
        input(text)
    except (KeyboardInterrupt, EOFError):
        pass


# ============== 数据修改（只改 config，不落盘） ==============

def _rename_preset_ui(preset: dict) -> None:
    """修改预设名称（空输入 = 保持不变）。"""
    current = str(preset.get("name") or "")
    try:
        raw = input(f"预设名称 [{current}]: ").strip()
    except (KeyboardInterrupt, EOFError):
        console.print("[yellow]已取消[/yellow]")
        _pause()
        return
    if not raw:
        console.print("[dim]名称未更改[/dim]")
        _pause()
        return
    preset["name"] = raw
    console.print(f"[green][OK] 名称已改为: {raw}[/green]")
    _pause()


def _switch_mode_ui(preset: dict) -> None:
    """切换配置模式（覆盖/追加），取值只在 cm.PRESET_MODES 白名单内。"""
    current = str(preset.get("mode") or "")
    if current not in cm.PRESET_MODES:
        current = cm.PRESET_MODES[0]
    labels = [
        "覆盖（overwrite）: 用预设地址替换网卡当前地址",
        "追加（append）: 保留网卡当前地址并追加预设地址",
    ]
    idx = widgets.pick_option(labels, f"选择配置模式（当前: {_mode_label(current)}）",
                              default_index=1 if current == "append" else 0)
    if idx < 0 or idx >= len(cm.PRESET_MODES):
        console.print("[yellow]已取消[/yellow]")
        _pause()
        return
    preset["mode"] = cm.PRESET_MODES[idx]
    console.print(f"[green][OK] 模式已改为: {_mode_label(preset['mode'])}[/green]")
    _pause()


def _rebind_hotkey_ui(config: dict, preset: dict, preset_idx: int) -> None:
    """重新绑定热键（取消 = 保持原值，清除用单独的菜单项）。"""
    combo = _choose_hotkey(config, exclude_index=preset_idx)
    if not combo:
        console.print("[yellow]已取消，热键未更改[/yellow]")
        _pause()
        return
    preset["hotkey"] = combo
    console.print(f"[green][OK] 已绑定热键: {combo}[/green]")
    _pause()


def _delete_preset_ui(config: dict, preset_idx: int) -> None:
    """删除预设（二次确认，默认拒绝）。"""
    presets = config.get("presets") or []
    if not (0 <= preset_idx < len(presets)):
        return
    name = str(presets[preset_idx].get("name") or "（未命名）")
    if not widgets.confirm(f"确认删除预设 '{name}'?", default=False):
        console.print("[yellow]已取消[/yellow]")
        _pause()
        return
    presets.pop(preset_idx)
    console.print(f"[green][OK] 预设已删除: {name}[/green]")
    _pause()


def _ask_name(prompt: str, default: str) -> Optional[str]:
    """读取名称；返回 None 表示取消（Ctrl+C / EOF），空输入用默认值。"""
    try:
        raw = input(prompt).strip()
    except (KeyboardInterrupt, EOFError):
        console.print("[yellow]已取消[/yellow]")
        return None
    return raw or default


def _pick_mode(prompt: str = "选择配置模式", default_mode: str = "overwrite") -> Optional[str]:
    """选择配置模式；取消返回 None。"""
    labels = [
        "覆盖（overwrite）: 用预设地址替换网卡当前地址",
        "追加（append）: 保留网卡当前地址并追加预设地址",
    ]
    idx = widgets.pick_option(labels, prompt,
                              default_index=1 if default_mode == "append" else 0)
    if idx < 0 or idx >= len(cm.PRESET_MODES):
        return None
    return cm.PRESET_MODES[idx]


def _add_preset_ui(config: dict) -> Optional[dict]:
    """
    新增预设：设备 → 网卡 → 模式 → 名称 → 可选热键。取消返回 None。

    写入前统一过 ``cm._normalize_presets``（MAC 大写、mode 白名单、
    必须有设备名或设备IP），保证落库数据与 schema 完全一致。
    """
    presets = config.get("presets")
    if not isinstance(presets, list):
        presets = []
        config["presets"] = presets
    if len(presets) >= cm.MAX_PRESETS:
        console.print(f"[yellow][!] 预设数量已达上限（{cm.MAX_PRESETS} 条），"
                      "请先删除不用的预设[/yellow]")
        _pause()
        return None

    devices = get_devices(config)
    if not devices:
        console.print("[yellow][!] 还没有设备配置，请先到「管理设备」添加设备[/yellow]")
        _pause()
        return None

    dev_options = [
        f"{d.get('name', '')} | 设备IP: {d.get('device_ip', '')} | 分组: {d.get('group') or '未分组'}"
        for d in devices
    ]
    dev_idx = widgets.pick_option(dev_options, "选择要绑定的设备")
    if dev_idx < 0:
        return None
    device = devices[dev_idx]

    filter_virtual = bool(cm.get_setting(config, "settings.filter_virtual_adapters", True))
    try:
        adapters = get_network_adapters(filter_virtual=filter_virtual)
    except Exception as e:  # WMI 异常不应把用户卡死在页面里
        console.print(f"[red][X] 网卡枚举失败: {e}[/red]")
        _pause()
        return None
    if not adapters:
        console.print("[red][X] 未检测到网卡[/red]")
        _pause()
        return None

    adapter_options = []
    for a in adapters:
        state_str = "已连接" if getattr(a, "is_up", False) else "未连接"
        ip_str = getattr(a, "display_ip", "") or "无IP"
        adapter_options.append(
            f"[{getattr(a, 'type_name', '')}] {getattr(a, 'name', '')} | {state_str}"
            f" | IP: {ip_str} | MAC: {getattr(a, 'mac', '')}"
        )
    ad_idx = widgets.pick_option(adapter_options, "选择要绑定的网卡")
    if ad_idx < 0:
        return None
    adapter = adapters[ad_idx]

    mode = _pick_mode()
    if mode is None:
        return None

    default_name = str(device.get("name") or device.get("device_ip") or "预设")
    name = _ask_name(f"预设名称 [{default_name}]: ", default_name)
    if name is None:
        return None

    hotkey = ""
    if widgets.confirm("现在绑定全局热键?", default=False):
        hotkey = _choose_hotkey(config)
        if not hotkey:
            console.print("[dim]未绑定热键（可稍后在列表里重新绑定）[/dim]")

    preset = {
        "name": name,
        # 新增默认停用：启用时由运行时做一次干跑校验（见 PRESET_DEFAULTS 注释）
        "enabled": False,
        "adapter_mac": str(getattr(adapter, "mac", "") or "").strip().upper(),
        "device_name": str(device.get("name") or "").strip(),
        "device_ip": str(device.get("device_ip") or "").strip(),
        "mode": mode,
        "hotkey": hotkey,
    }
    normalized = cm._normalize_presets([preset])
    if not normalized:
        console.print("[red][X] 预设无效：至少需要设备名或设备IP[/red]")
        _pause()
        return None

    presets.append(normalized[0])
    console.print(f"[green][OK] 预设已添加: {name}[/green]")
    if hotkey:
        console.print(f"[yellow][!] 已绑定热键 {hotkey}，"
                      "需在列表中启用该预设后才会生效[/yellow]")
    console.print("[dim]提示：选中该预设可按 [T] 启用、[H] 重绑热键[/dim]")
    _pause()
    return config


# ============== 页面 ==============

def _preset_action_menu(config: dict, preset_idx: int) -> None:
    """单个预设的操作子菜单（改名/启用/模式/热键/删除）。"""
    presets = config.get("presets") or []
    if not (0 <= preset_idx < len(presets)):
        return
    preset = presets[preset_idx]

    show_header(config)
    console.print(f"\n[bold cyan]预设: {preset.get('name') or '（未命名）'}[/bold cyan]")
    console.print(f"  设备: {_device_text(preset)}    网卡: {preset.get('adapter_mac') or '未指定'}")
    console.print(f"  模式: {_mode_label(str(preset.get('mode') or ''))}"
                  f"    热键: {preset.get('hotkey') or '未绑定'}"
                  f"    状态: {'启用' if preset.get('enabled') else '停用'}")

    options = [
        "[E] 修改名称",
        f"[T] {'停用' if preset.get('enabled') else '启用'}",
        "[M] 切换配置模式（覆盖/追加）",
        "[H] 重新绑定热键",
        "[C] 清除热键",
        "[D] 删除预设",
    ]
    idx = widgets.pick_option(options, "请选择操作")
    if idx < 0:
        return

    if idx == 0:
        _rename_preset_ui(preset)
    elif idx == 1:
        preset["enabled"] = not bool(preset.get("enabled"))
        console.print(f"[green][OK] 已{'启用' if preset['enabled'] else '停用'}预设[/green]")
        _pause()
    elif idx == 2:
        _switch_mode_ui(preset)
    elif idx == 3:
        _rebind_hotkey_ui(config, preset, preset_idx)
    elif idx == 4:
        preset["hotkey"] = ""
        console.print("[green][OK] 已清除热键[/green]")
        _pause()
    elif idx == 5:
        _delete_preset_ui(config, preset_idx)


def show_presets(config: dict) -> dict:
    """
    快捷预设管理页。

    返回（可能被修改的）config；**不负责保存**，由动作注册表的
    ``needs_save=True`` 在 main 循环里统一落盘。
    """
    if not isinstance(config, dict):
        return config
    presets = config.get("presets")
    if not isinstance(presets, list):  # 兼容手工编辑过的配置
        presets = []
        config["presets"] = presets

    while True:
        show_header(config)
        if not presets and not get_devices(config):
            console.print("[yellow][!] 还没有设备配置，请先在「管理设备」中添加设备[/yellow]")

        options, index_map, non_selectable = _build_options(presets)
        idx = widgets.pick_option(
            options,
            "快捷预设 - 请选择要管理的预设",
            page_size=8,
            default_index=0,
            fixed_tail=["[+] 新增预设"],
            non_selectable=non_selectable,
            hotkeys={"a": len(options)},  # a = 新增（fixed_tail 第 0 项）
        )
        if idx < 0:
            return config

        if idx >= len(options):
            result = _add_preset_ui(config)
            if result:
                config = result
            continue

        preset_idx = index_map.get(idx)
        if preset_idx is None:
            continue
        _preset_action_menu(config, preset_idx)
