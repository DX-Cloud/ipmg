# -*- coding: utf-8 -*-
"""
常驻控制器与命令行参数的测试。

全部使用 fake Win32Host 与 fake 网卡/设备，**不触碰真实托盘、热键、网卡与计划任务**。

运行：python -m unittest discover -s tests -p "test_resident.py" -t . -v
"""
import os
import sys
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
                                 "no_singleton": False, "selfcheck": False})

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

    def test_unknown_flags_ignored(self):
        flags = entry.parse_args(["--whatever", "-x"])
        self.assertEqual(flags["resident"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
