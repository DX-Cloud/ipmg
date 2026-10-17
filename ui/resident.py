"""
常驻后台控制器
==============
把「托盘图标 + 全局热键 + 快捷键应用预设 + 操作闸门」串成一个整体：

- 常驻态下 TUI 仍在跑（可能被隐藏），主线程每 200ms 轮询一次按键；
  热键唤出/后台结果只投递事件 + :func:`ui.console.wake`，由主线程重绘，避免跨线程写坏画面。
- 快捷键应用预设走完整业务管线（与手动操作同一套 core.operations），并强制：
  抢不到操作闸门就拒绝、备份失败即中止、按预设的覆盖/追加模式写入。
- 所有对外反馈走三条路：标题栏状态（ui.status）、托盘气泡、日志；**不在后台线程 print**。

热键 id 约定：100 = 唤出/收起主界面；200 + i = 第 i 个预设。
"""

import os
import sys
import threading
from typing import List, Optional, Tuple

from core import config_manager as cm
from core import operations
from core.adapter_manager import get_network_adapters
from core.config_manager import get_devices
from core.opgate import gate
from core.win32host import Win32Host, confirm_dialog, info_dialog, parse_hotkey
from utils.logger import log_error, log_operation

from ui import console as uconsole
from ui import status

HOTKEY_ID_SUMMON = 100
HOTKEY_ID_PRESET_BASE = 200

# 进程内单例：TUI 与 main.py 通过它共享同一个控制器
_controller: Optional["ResidentController"] = None


def get_controller() -> Optional["ResidentController"]:
    return _controller


def set_controller(controller: Optional["ResidentController"]) -> None:
    global _controller
    _controller = controller


def icon_path() -> str:
    """定位托盘图标（兼容 PyInstaller onefile 的 _MEIPASS）。"""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "assets", "app.ico"))
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(here, "assets", "app.ico"))
    for path in candidates:
        if os.path.exists(path):
            return path
    return ""  # 交给 Win32Host 用系统默认图标


class ResidentController:
    """常驻控制器。构造后调用 :meth:`start` 启动 Win32 事件线程并注册热键。"""

    def __init__(self, config: dict, icon: Optional[str] = None) -> None:
        self.config = config
        self.icon = icon if icon is not None else icon_path()
        self.host: Optional[Win32Host] = None
        self.console_hidden = False
        self.hotkey_errors: List[Tuple[str, str]] = []   # [(组合键, 失败原因)]
        self.skipped_hotkeys: List[Tuple[str, str]] = []  # [(预设名, 为何没注册)]
        self.last_message = ""

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> Tuple[bool, str]:
        """启动事件线程、托盘图标与热键。返回 (ok, 说明)。"""
        if self.host is not None:
            return True, "常驻已在运行"
        try:
            self.host = Win32Host(on_hotkey=self._on_hotkey, on_tray=self._on_tray)
            self.host.start()
        except Exception as e:                     # pragma: no cover - 平台相关
            self.host = None
            return False, f"启动常驻事件线程失败: {e}"

        tray_ok = False
        try:
            tray_ok = bool(self.host.set_tray(self.icon or None, "ipmg 常驻中"))
        except Exception as e:                     # pragma: no cover
            log_error("常驻托盘", str(e))

        self.hotkey_errors = self._register_hotkeys()
        parts = ["常驻已启动"]
        parts.append("托盘已就绪" if tray_ok else "托盘不可用（仅热键）")
        if self.hotkey_errors:
            parts.append("热键注册失败: " + "; ".join(
                f"{combo}（{err}）" for combo, err in self.hotkey_errors))
        return True, "，".join(parts)

    def stop(self) -> None:
        """退出常驻：移除托盘、注销热键、结束事件线程。"""
        host, self.host = self.host, None
        if host is None:
            return
        try:
            host.remove_tray()
        except Exception:
            pass
        try:
            host.stop()
        except Exception:
            pass
        self.console_hidden = False

    @property
    def running(self) -> bool:
        return self.host is not None

    # ------------------------------------------------------------------ 热键
    def _register_hotkeys(self) -> List[Tuple[str, str]]:
        """按当前配置注册热键，返回失败列表。"""
        errors: List[Tuple[str, str]] = []
        self.skipped_hotkeys = []
        if self.host is None:
            return errors

        summon = str(cm.get_setting(self.config, "settings.hotkeys.summon", "") or "").strip()
        if summon:
            ok, err = self.host.register_hotkey(HOTKEY_ID_SUMMON, summon)
            if not ok:
                errors.append((summon, err))

        used = {summon.lower()} if summon else set()
        for index, preset in enumerate(self.config.get("presets") or []):
            combo = str(preset.get("hotkey") or "").strip()
            if not preset.get("enabled"):
                # 绑了热键但没启用：不注册，但要在启动信息里说清楚，否则用户会以为热键坏了
                if combo:
                    self.skipped_hotkeys.append(
                        (preset.get("name") or f"预设{index + 1}", "预设未启用"))
                continue
            if not combo:
                continue
            if parse_hotkey(combo) is None:
                errors.append((combo, "组合键格式不合法"))
                continue
            if combo.lower() in used:
                errors.append((combo, "与其它热键重复"))
                continue
            ok, err = self.host.register_hotkey(HOTKEY_ID_PRESET_BASE + index, combo)
            if ok:
                used.add(combo.lower())
            else:
                errors.append((combo, err))
        return errors

    def reload_hotkeys(self) -> List[Tuple[str, str]]:
        """配置（设置页/预设页）改动后重新注册热键。"""
        if self.host is None:
            return []
        self.host.unregister_hotkey(HOTKEY_ID_SUMMON)
        for index in range(len(self.config.get("presets") or [])):
            self.host.unregister_hotkey(HOTKEY_ID_PRESET_BASE + index)
        self.hotkey_errors = self._register_hotkeys()
        return self.hotkey_errors

    # ------------------------------------------------------------------ 宿主线程回调
    def _on_hotkey(self, hotkey_id: int) -> None:
        """热键回调（在宿主线程执行，必须立刻返回）。"""
        if hotkey_id == HOTKEY_ID_SUMMON:
            self.toggle_console()
            return
        index = hotkey_id - HOTKEY_ID_PRESET_BASE
        if index < 0:
            return
        # 应用预设会写网卡（耗时），放到工作线程，别堵住消息循环
        worker = threading.Thread(target=self.apply_preset, args=(index,),
                                  name="ipmg-preset", daemon=True)
        worker.start()

    def _on_tray(self, action: str) -> None:
        """托盘回调（在宿主线程执行）。左/右键都视为唤出主界面。"""
        if action in ("left_click", "double_click", "right_click", "select"):
            self.toggle_console()

    # ------------------------------------------------------------------ 窗口显隐
    def toggle_console(self) -> None:
        if self.host is None:
            return
        if self.console_hidden:
            self.show_console()
        else:
            self.hide_console()

    def show_console(self) -> bool:
        if self.host is None:
            return False
        ok = bool(self.host.show_console())
        self.console_hidden = False
        uconsole.wake()          # 让 TUI 立刻重绘一帧
        return ok

    def hide_console(self) -> bool:
        if self.host is None:
            return False
        ok = bool(self.host.hide_console())
        self.console_hidden = ok
        return ok

    # ------------------------------------------------------------------ 快捷应用
    def _find_device(self, preset: dict) -> Optional[dict]:
        name = str(preset.get("device_name") or "").strip()
        ip = str(preset.get("device_ip") or "").strip()
        for device in get_devices(self.config):
            if ip and str(device.get("device_ip") or "") == ip:
                return device
            if name and str(device.get("name") or "") == name:
                return device
        return None

    def _find_adapter(self, preset: dict):
        mac = str(preset.get("adapter_mac") or "").strip().upper()
        adapters = get_network_adapters(filter_virtual=False)
        if not mac:
            return adapters[0] if adapters else None
        for adapter in adapters:
            if str(getattr(adapter, "mac", "") or "").upper() == mac:
                return adapter
        return None

    def apply_preset(self, index: int) -> Tuple[bool, str]:
        """
        应用第 index 个预设（热键路径）。返回 (ok, 说明)。
        安全护栏：抢不到闸门拒绝；备份失败中止；预设未启用/设备或网卡找不到直接报错。
        """
        presets = self.config.get("presets") or []
        if not (0 <= index < len(presets)):
            return self._fail("预设不存在")
        preset = presets[index]
        label = preset.get("name") or f"预设{index + 1}"

        if not preset.get("enabled"):
            return self._fail(f"{label}: 预设未启用")

        if gate.busy:
            return self._fail(gate.busy_reason)

        # 应用前二次确认（设置项 notify.apply_requires_confirm）：
        # 用原生对话框而不是托盘气泡/控制台提示——控制台可能处于隐藏状态，
        # 气泡又可能被系统通知设置抑制，而改 IP 是有副作用的操作，必须让用户看到。
        if bool(cm.get_setting(self.config, "settings.notify.apply_requires_confirm", False)):
            device_text = preset.get("device_name") or preset.get("device_ip") or "（未指定）"
            mode_text = "追加 IP（保留现有地址）" if preset.get("mode") == "append" else "覆盖 IP"
            detail = (f"预设：{label}\n"
                      f"网卡：{preset.get('adapter_mac') or '（未指定，将用第一块网卡）'}\n"
                      f"设备：{device_text}\n"
                      f"模式：{mode_text}\n\n"
                      f"确定现在应用到网卡吗？")
            if not confirm_dialog("ipmg - 确认应用配置", detail):
                log_operation("热键应用", preset.get("adapter_mac", ""), device_text,
                              "已取消（用户未确认）")
                return False, f"{label}: 已取消"

        try:
            with gate.hold("热键应用", timeout=0):
                return self._apply_locked(preset, label)
        except Exception as e:                       # 闸门或业务异常
            return self._fail(f"{label}: {e}")

    def _apply_locked(self, preset: dict, label: str) -> Tuple[bool, str]:
        device = self._find_device(preset)
        if device is None:
            return self._fail(f"{label}: 找不到设备（{preset.get('device_name') or preset.get('device_ip')}）")
        adapter = self._find_adapter(preset)
        if adapter is None:
            return self._fail(f"{label}: 找不到网卡（{preset.get('adapter_mac') or '未指定'}）")

        try:
            plan = operations.build_configure_plan(device)
        except ValueError as e:
            return self._fail(f"{label}: 计算网卡IP失败（{e}）")

        # 备份失败即中止：没有回滚点就不动网卡
        if not operations.backup_current(self.config, adapter):
            return self._fail(f"{label}: 备份网卡IP失败，已中止")

        mode = preset.get("mode") or "overwrite"
        try:
            ok = operations.apply_static(adapter.name, plan, mode=mode)
        except (RuntimeError, ValueError) as e:
            log_operation("热键应用", adapter.name, device.get("name", ""), f"失败: {e}")
            return self._fail(f"{label}: {e}")

        if not ok:
            log_operation("热键应用", adapter.name, device.get("name", ""), "失败: WMI返回失败")
            return self._fail(f"{label}: WMI 返回失败")

        try:
            operations.save_state(self.config, adapter.mac)
        except Exception as e:
            log_error("热键应用保存", str(e))

        operations.run_hooks(self.config, "after_configure",
                             device=device.get("name", ""), adapter=adapter.name)
        log_operation("热键应用", adapter.name, device.get("name", ""),
                      f"成功（{mode}）{plan.get('adapter_ip')}")
        text = (f"[OK] {label}: {adapter.name} -> {plan.get('adapter_ip')}"
                f"（{'追加' if mode == 'append' else '覆盖'}）")
        self._report(text)
        return True, text

    # ------------------------------------------------------------------ 反馈
    def _fail(self, text: str) -> Tuple[bool, str]:
        self._report(f"[X] {text}")
        return False, text

    def _report(self, text: str) -> None:
        """统一反馈：标题栏状态 + 托盘气泡 + 唤醒重绘（不直接 print）。"""
        self.last_message = text
        try:
            status.set_last_result(text)
        except Exception:
            pass
        shown = self.notify("ipmg", text)

        # 控制台隐藏时，气泡是唯一"看得见"的通道，但它受系统通知设置影响
        # （专注助手/关闭通知会静默抑制）。因此：
        #   - 用户开启了"应用前二次确认"（明确要交互式反馈）→ 一律补一个结果弹窗
        #   - 否则只在气泡确实没发出去时兜底弹窗
        interactive = bool(cm.get_setting(
            self.config, "settings.notify.apply_requires_confirm", False))
        if self.console_hidden and (interactive or not shown):
            info_dialog("ipmg", text, warning=text.startswith("[X]"))
        uconsole.wake()

    def notify(self, title: str, text: str) -> bool:
        """发托盘气泡，返回是否成功发出（供调用方决定要不要兜底）。"""
        if self.host is None:
            return False
        if not bool(cm.get_setting(self.config, "settings.notify.balloon", True)):
            return False
        try:
            return bool(self.host.notify(title, text))
        except Exception:
            return False
