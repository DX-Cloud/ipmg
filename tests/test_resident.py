# -*- coding: utf-8 -*-
"""
常驻控制器与命令行参数的测试。

全部使用 fake Win32Host 与 fake 网卡/设备，**不触碰真实托盘、热键、网卡与计划任务**。

运行：python -m unittest discover -s tests -p "test_resident.py" -t . -v
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as entry  # noqa: E402
from core.opgate import OperationGate  # noqa: E402
from ui import resident  # noqa: E402


class FakeHost:
    """记录调用的假 Win32 事件线程宿主。"""

    instances = []
    fail_combos = {}        # 类级：{组合键小写: 错误说明}，便于在 start() 之前配置

    def __init__(self, on_hotkey=None, on_tray=None):
        self.on_hotkey = on_hotkey
        self.on_tray = on_tray
        self.hotkeys = {}
        self.registrations = []      # [(id, combo, ok, err)]
        self.tray = None
        self.notifications = []
        self.console_hidden = False
        self.started = False
        self.stopped = False
        FakeHost.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def register_hotkey(self, hotkey_id, combo):
        err = self.fail_combos.get(combo.lower(), "")
        ok = not err
        self.registrations.append((hotkey_id, combo, ok, err))
        if ok:
            self.hotkeys[hotkey_id] = combo
        return ok, err

    def unregister_hotkey(self, hotkey_id):
        self.hotkeys.pop(hotkey_id, None)

    def set_tray(self, icon_path, tooltip):
        self.tray = (icon_path, tooltip)
        return True

    def remove_tray(self):
        self.tray = None

    def notify(self, title, text):
        self.notifications.append((title, text))
        return True

    def hide_console(self):
        self.console_hidden = True
        return True

    def show_console(self):
        self.console_hidden = False
        return True


class FakeAdapter:
    def __init__(self, name="以太网", mac="AA:BB:CC:DD:EE:FF"):
        self.name = name
        self.mac = mac
        self.type_name = "有线"
        self.is_up = True
        self.display_ip = "10.0.0.5"


DEVICE = {"name": "测试设备", "device_ip": "192.0.2.11", "ip_mode": "auto",
          "subnet_mask": "255.255.255.0", "gateway": "192.0.2.1"}


def make_config(presets=None, summon="Ctrl+Alt+I"):
    return {
        "devices": [dict(DEVICE)],
        "presets": presets if presets is not None else [],
        "settings": {
            "hotkeys": {"summon": summon},
            "notify": {"balloon": True},
            "resident": {"enabled": True, "close_to_tray": True, "autostart": False},
        },
    }


def preset(name="预设1", enabled=True, hotkey="Ctrl+Alt+1", mode="overwrite",
           mac="AA:BB:CC:DD:EE:FF", device_ip="192.0.2.11"):
    return {"name": name, "enabled": enabled, "adapter_mac": mac,
            "device_name": "", "device_ip": device_ip, "mode": mode, "hotkey": hotkey}


class ResidentTestCase(unittest.TestCase):
    def setUp(self):
        self._saved = []
        self.gate = OperationGate()
        self._patch(resident, "Win32Host", FakeHost)
        self._patch(resident, "gate", self.gate)
        self._patch(resident, "get_devices", lambda config: [dict(DEVICE)])
        self._patch(resident, "get_network_adapters",
                    lambda filter_virtual=True: [FakeAdapter()])
        self._patch(resident, "log_operation", lambda *a, **k: None)
        self._patch(resident, "log_error", lambda *a, **k: None)
        self._patch(resident.uconsole, "wake", lambda: None)
        self.status_calls = []
        self._patch(resident.status, "set_last_result",
                    lambda text: self.status_calls.append(text))
        self.ops_calls = {"plan": 0, "backup": 0, "apply": [], "save": 0, "hooks": 0}
        self._patch(resident.operations, "build_configure_plan",
                    lambda device: self._plan())
        self._patch(resident.operations, "backup_current",
                    lambda config, adapter: self._backup())
        self._patch(resident.operations, "apply_static", self._apply)
        self._patch(resident.operations, "save_state", lambda config, mac: None)
        self._patch(resident.operations, "run_hooks", lambda *a, **k: None)
        self.backup_result = True

    def tearDown(self):
        for obj, name, value in reversed(self._saved):
            setattr(obj, name, value)
        FakeHost.fail_combos.clear()
        FakeHost.instances.clear()
        if self.gate.busy:
            self.gate.release()

    def _patch(self, obj, name, value):
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def _plan(self):
        self.ops_calls["plan"] += 1
        return {"adapter_ip": "192.0.2.254", "mask": "255.255.255.0",
                "gateway": "192.0.2.1", "mgmt_url": None}

    def _backup(self):
        self.ops_calls["backup"] += 1
        return self.backup_result

    def _apply(self, adapter_name, plan, mode="overwrite"):
        self.ops_calls["apply"].append((adapter_name, mode))
        return True

    def controller(self, config=None):
        return resident.ResidentController(config or make_config())


class TestStartAndHotkeys(ResidentTestCase):
    def test_start_registers_summon_and_preset_hotkeys(self):
        config = make_config([preset(hotkey="Ctrl+Alt+1"), preset(name="预设2", hotkey="")])
        c = self.controller(config)
        ok, message = c.start()
        self.assertTrue(ok)
        self.assertIn("常驻已启动", message)
        self.assertEqual(c.host.hotkeys.get(resident.HOTKEY_ID_SUMMON), "Ctrl+Alt+I")
        self.assertEqual(c.host.hotkeys.get(resident.HOTKEY_ID_PRESET_BASE), "Ctrl+Alt+1")
        self.assertNotIn(resident.HOTKEY_ID_PRESET_BASE + 1, c.host.hotkeys,
                         "未绑定热键的预设不应注册")
        self.assertIsNotNone(c.host.tray, "托盘应已就绪")

    def test_disabled_preset_hotkey_not_registered(self):
        c = self.controller(make_config([preset(enabled=False, hotkey="Ctrl+Alt+2")]))
        c.start()
        self.assertNotIn(resident.HOTKEY_ID_PRESET_BASE, c.host.hotkeys)

    def test_hotkey_conflict_is_reported_not_fatal(self):
        config = make_config([preset(hotkey="Ctrl+Alt+1")])
        FakeHost.fail_combos["ctrl+alt+i"] = "该组合键已被其他程序占用(1409)"
        c = self.controller(config)
        ok, message = c.start()
        self.assertTrue(ok, "热键冲突不应导致常驻启动失败")
        self.assertTrue(c.hotkey_errors)
        self.assertIn("Ctrl+Alt+I", message)

    def test_duplicate_hotkey_detected(self):
        config = make_config([preset(hotkey="Ctrl+Alt+I")])   # 与唤出热键重复
        c = self.controller(config)
        c.host = FakeHost()
        c.hotkey_errors = c._register_hotkeys()
        self.assertTrue(any("重复" in err for _combo, err in c.hotkey_errors))
        c.host = None

    def test_reload_hotkeys_unregisters_then_registers(self):
        c = self.controller(make_config([preset(hotkey="Ctrl+Alt+1")]))
        c.start()
        c.config["presets"][0]["hotkey"] = "Ctrl+Alt+9"
        c.reload_hotkeys()
        self.assertEqual(c.host.hotkeys.get(resident.HOTKEY_ID_PRESET_BASE), "Ctrl+Alt+9")


class TestConsoleVisibility(ResidentTestCase):
    def test_toggle_hides_then_shows(self):
        c = self.controller()
        c.start()
        c.toggle_console()
        self.assertTrue(c.console_hidden)
        c.toggle_console()
        self.assertFalse(c.console_hidden)

    def test_tray_click_summons(self):
        c = self.controller()
        c.start()
        c.hide_console()
        c._on_tray("left_click")
        self.assertFalse(c.console_hidden)


class TestApplyPreset(ResidentTestCase):
    def test_apply_overwrite_success(self):
        c = self.controller(make_config([preset(mode="overwrite")]))
        c.start()
        ok, text = c.apply_preset(0)
        self.assertTrue(ok, text)
        self.assertEqual(self.ops_calls["apply"], [("以太网", "overwrite")])
        self.assertEqual(self.ops_calls["backup"], 1)
        self.assertIn("[OK]", text)
        self.assertTrue(self.status_calls, "结果要写进标题栏状态")
        self.assertTrue(c.host.notifications, "应发出托盘气泡")
        self.assertFalse(self.gate.busy, "完成后必须释放操作闸门")

    def test_apply_append_mode_used(self):
        c = self.controller(make_config([preset(mode="append")]))
        c.start()
        ok, _ = c.apply_preset(0)
        self.assertTrue(ok)
        self.assertEqual(self.ops_calls["apply"], [("以太网", "append")])

    def test_disabled_preset_refused(self):
        c = self.controller(make_config([preset(enabled=False)]))
        c.start()
        ok, text = c.apply_preset(0)
        self.assertFalse(ok)
        self.assertIn("未启用", text)
        self.assertEqual(self.ops_calls["apply"], [])

    def test_busy_gate_refuses(self):
        c = self.controller(make_config([preset()]))
        c.start()
        self.gate.acquire("TUI 正在配置")
        ok, text = c.apply_preset(0)
        self.assertFalse(ok)
        self.assertIn("操作进行中", text)
        self.assertEqual(self.ops_calls["apply"], [], "闸门被占时绝不能写网卡")

    def test_backup_failure_aborts(self):
        self.backup_result = False
        c = self.controller(make_config([preset()]))
        c.start()
        ok, text = c.apply_preset(0)
        self.assertFalse(ok)
        self.assertIn("备份", text)
        self.assertEqual(self.ops_calls["apply"], [], "备份失败必须中止，不能改网卡")

    def test_device_not_found(self):
        p = preset(device_ip="10.9.9.9")
        c = self.controller(make_config([p]))
        c.start()
        ok, text = c.apply_preset(0)
        self.assertFalse(ok)
        self.assertIn("找不到设备", text)

    def test_adapter_not_found(self):
        c = self.controller(make_config([preset(mac="11:22:33:44:55:66")]))
        c.start()
        ok, text = c.apply_preset(0)
        self.assertFalse(ok)
        self.assertIn("找不到网卡", text)

    def test_out_of_range_index(self):
        c = self.controller(make_config([]))
        c.start()
        ok, text = c.apply_preset(3)
        self.assertFalse(ok)
        self.assertIn("不存在", text)

    def test_hotkey_dispatch_uses_worker_thread(self):
        """热键回调必须立刻返回：应用预设放到工作线程里跑。"""
        import threading
        c = self.controller(make_config([preset()]))
        c.start()
        done = threading.Event()
        original = c.apply_preset

        def wrapped(index):
            result = original(index)
            done.set()
            return result

        c.apply_preset = wrapped
        c._on_hotkey(resident.HOTKEY_ID_PRESET_BASE)
        self.assertTrue(done.wait(3), "工作线程应在 3 秒内完成")


class TestParseArgs(unittest.TestCase):
    def test_defaults(self):
        flags = entry.parse_args([])
        self.assertEqual(flags, {"resident": False, "autostart_min": False,
                                 "no_singleton": False, "selfcheck": False,
                                 "help": False, "version": False, "error": ""})

    def test_resident_and_min(self):
        flags = entry.parse_args(["--resident"])
        self.assertTrue(flags["resident"])
        flags = entry.parse_args(["--autostart-min"])
        self.assertTrue(flags["resident"], "--autostart-min 隐含常驻")
        self.assertTrue(flags["autostart_min"])

    def test_selfcheck_and_no_singleton(self):
        flags = entry.parse_args(["--selfcheck", "--no-singleton"])
        self.assertTrue(flags["selfcheck"])
        self.assertTrue(flags["no_singleton"])

    def test_help_and_version(self):
        for arg in ("--help", "-h"):
            self.assertTrue(entry.parse_args([arg])["help"], f"{arg} 应触发帮助")
        for arg in ("--version", "-V"):
            self.assertTrue(entry.parse_args([arg])["version"], f"{arg} 应显示版本")

    def test_unknown_option_reports_error(self):
        """未知选项要报错（以前是静默忽略，用户拼错了也不知道）。"""
        flags = entry.parse_args(["--whatever"])
        self.assertIn("--whatever", flags["error"])
        self.assertFalse(flags["resident"])

    def test_positional_args_ignored(self):
        flags = entry.parse_args(["somefile.yaml"])
        self.assertEqual(flags["error"], "")
        self.assertFalse(flags["resident"])


class TestApplyConfirm(ResidentTestCase):
    """设置项 notify.apply_requires_confirm：应用前必须真的有二次确认。"""

    def _config_with_confirm(self):
        config = make_config([preset()])
        config["settings"]["notify"]["apply_requires_confirm"] = True
        return config

    def test_confirm_declined_aborts(self):
        c = self.controller(self._config_with_confirm())
        c.start()
        self._patch(resident, "confirm_dialog", lambda title, text: False)
        ok, text = c.apply_preset(0)
        self.assertFalse(ok)
        self.assertIn("已取消", text)
        self.assertEqual(self.ops_calls["apply"], [], "用户没确认就不能改网卡")
        self.assertEqual(self.ops_calls["backup"], 0, "取消时不应产生备份")

    def test_confirm_accepted_applies(self):
        c = self.controller(self._config_with_confirm())
        c.start()
        seen = []

        def fake_confirm(title, text):
            seen.append((title, text))
            return True

        self._patch(resident, "confirm_dialog", fake_confirm)
        ok, _ = c.apply_preset(0)
        self.assertTrue(ok)
        self.assertEqual(self.ops_calls["apply"], [("以太网", "overwrite")])
        self.assertTrue(seen, "确认框必须真的弹了")
        self.assertIn("预设1", seen[0][1], "确认框里要写清应用的是哪个预设")

    def test_no_dialog_when_setting_off(self):
        c = self.controller(make_config([preset()]))   # 默认 False
        c.start()
        called = []
        self._patch(resident, "confirm_dialog", lambda t, x: called.append(1) or True)
        ok, _ = c.apply_preset(0)
        self.assertTrue(ok)
        self.assertEqual(called, [], "未开启二次确认时不应弹框")

    def test_result_falls_back_to_dialog_when_balloon_fails(self):
        c = self.controller(make_config([preset()]))
        c.start()
        c.console_hidden = True
        c.host.notify = lambda *a, **k: False      # 模拟气泡发不出去
        dialogs = []
        self._patch(resident, "info_dialog",
                    lambda title, text, warning=False: dialogs.append(text))
        ok, _ = c.apply_preset(0)
        self.assertTrue(ok)
        self.assertTrue(dialogs, "气泡失败且控制台隐藏时，必须用弹窗兜底，否则用户什么都看不到")

    def test_no_dialog_when_console_visible(self):
        c = self.controller(make_config([preset()]))
        c.start()
        c.console_hidden = False
        c.host.notify = lambda *a, **k: False
        dialogs = []
        self._patch(resident, "info_dialog",
                    lambda title, text, warning=False: dialogs.append(text))
        c.apply_preset(0)
        self.assertEqual(dialogs, [], "控制台可见时结果已在界面上，不必再弹框")

    def test_interactive_mode_always_shows_result_dialog_when_hidden(self):
        """开启二次确认（=用户要交互式反馈）时，结果必须有可见回执，不能只靠气泡。"""
        config = self._config_with_confirm()
        c = self.controller(config)
        c.start()
        c.console_hidden = True
        self._patch(resident, "confirm_dialog", lambda t, x: True)
        dialogs = []
        self._patch(resident, "info_dialog",
                    lambda title, text, warning=False: dialogs.append(text))
        ok, _ = c.apply_preset(0)
        self.assertTrue(ok)
        self.assertTrue(dialogs, "即使气泡发送成功，也要给出可见结果回执")

    def test_disabled_preset_with_hotkey_is_reported(self):
        config = make_config([preset(enabled=False, hotkey="Ctrl+Alt+3")])
        c = self.controller(config)
        c.start()
        self.assertTrue(c.skipped_hotkeys, "绑了热键却没启用，必须在启动信息里说明")
        self.assertIn("未启用", c.skipped_hotkeys[0][1])


class TestSummonWatcher(unittest.TestCase):
    """
    重复启动时的"唤出既有窗口"链路。

    历史问题：singleton.signal_existing() 一直被调用，但没有任何生产代码等待
    ``Local\\ipmg.activate`` 事件，于是第二个实例提示"已请求显示"却没人响应。
    """

    def _run_watcher(self, controller):
        from core import singleton, win32host
        from ui import console as uconsole
        import ui.resident as resident_module

        calls = {"raise": 0, "wake": 0, "show": 0}
        saved = [
            (singleton, "wait_activate", singleton.wait_activate),
            (win32host, "raise_console", win32host.raise_console),
            (uconsole, "wake", uconsole.wake),
            (resident_module, "get_controller", resident_module.get_controller),
        ]

        signals = [True]

        def fake_wait(name, timeout_ms):
            # 第一次返回"收到信号"；随后抛 SystemExit 让监听线程干净退出
            # （监听线程只捕获 Exception，SystemExit 会终止该 daemon 线程，
            #   避免测试进程里残留一个 2Hz 空转线程）
            if signals:
                return signals.pop(0)
            raise SystemExit

        singleton.wait_activate = fake_wait
        win32host.raise_console = lambda: calls.__setitem__("raise", calls["raise"] + 1) or True
        uconsole.wake = lambda: calls.__setitem__("wake", calls["wake"] + 1)
        resident_module.get_controller = lambda: controller

        try:
            entry._start_summon_watcher()
            deadline = time.time() + 2
            while time.time() < deadline and not calls["wake"]:
                time.sleep(0.02)
        finally:
            for module, name, value in saved:
                setattr(module, name, value)
        return calls

    def test_resident_instance_uses_controller_show_console(self):
        class FakeController:
            def __init__(self):
                self.shown = 0

            def show_console(self):
                self.shown += 1
                return True

        controller = FakeController()
        calls = self._run_watcher(controller)
        self.assertEqual(controller.shown, 1, "常驻实例应通过控制器显示控制台")
        self.assertGreaterEqual(calls["wake"], 1, "唤出后要唤醒主线程重绘")

    def test_non_resident_instance_raises_console(self):
        calls = self._run_watcher(None)
        self.assertEqual(calls["raise"], 1, "非常驻实例应直接前置控制台窗口")
        self.assertGreaterEqual(calls["wake"], 1, "唤出后要唤醒主线程重绘")


class TestExitToTray(unittest.TestCase):
    """常驻状态下点"退出"应当默认最小化到托盘。"""

    def _ask(self, close_to_tray, answer):
        from core import config_manager as cm
        from ui import widgets

        saved = (cm.get_setting, widgets.pick_option)
        captured = {}

        cm.get_setting = lambda config, key, default=None: close_to_tray

        def fake_pick(options, title, default_index=0, **kw):
            captured["default"] = default_index
            captured["options"] = list(options)
            return answer

        widgets.pick_option = fake_pick
        try:
            result = entry._ask_exit_resident({})
        finally:
            cm.get_setting, widgets.pick_option = saved
        return result, captured

    def test_tray_is_default_when_close_to_tray_on(self):
        result, captured = self._ask(True, 0)
        self.assertEqual(result, "tray")
        self.assertEqual(captured["default"], 0, "开启'保留常驻'时默认最小化到托盘")
        self.assertIn("托盘", captured["options"][0])

    def test_quit_is_default_when_close_to_tray_off(self):
        result, captured = self._ask(False, 1)
        self.assertEqual(result, "quit")
        self.assertEqual(captured["default"], 1)

    def test_cancel_keeps_running(self):
        result, _ = self._ask(True, -1)
        self.assertEqual(result, "cancel")


if __name__ == "__main__":
    unittest.main(verbosity=2)
