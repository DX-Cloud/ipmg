# -*- coding: utf-8 -*-
"""
快捷预设管理页（ui/presets.py）的单元测试。

覆盖：
- 新增预设写入的字段完整性（MAC 统一大写、mode 在白名单内、设备定位字段齐全）
- 热键捕获：mock GetAsyncKeyState 的按键序列，断言 Ctrl+Alt+1 / Ctrl+Shift+F5 / Esc 取消
- 重复热键被拒绝（同一组合不能绑两次）
- 删除后列表为空；取消删除则保留
- 空 config / 非法 presets 值不崩溃
- 本页不落盘（needs_save 由动作注册表统一负责）

硬约束：不产生真实键盘输入（GetAsyncKeyState 全部打桩）、不触碰真实 WMI
（网卡枚举与标题栏全部打桩）。

运行：python -B -m unittest discover -s tests -p "test_presets.py" -t .
"""
import builtins
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config_manager as cm      # noqa: E402
from core import win32host                 # noqa: E402
from ui import presets, widgets            # noqa: E402


class FakeAdapter:
    """假网卡：MAC 故意写成小写，用来验证写进预设时统一大写。"""

    name = "以太网"
    mac = "aa:bb:cc:dd:ee:ff"
    type_name = "有线"
    is_up = True
    display_ip = "10.0.0.5"


DEVICE = {
    "name": "测试设备",
    "device_ip": "192.0.2.11",
    "group": "机房A",
    "subnet_mask": "255.255.255.0",
    "ip_mode": "auto",
}


def make_preset(**kwargs):
    """按 schema 造一条预设（默认能被 _normalize_presets 接受）。"""
    preset = dict(cm.PRESET_DEFAULTS)
    preset.update({"name": "已有预设", "device_name": "测试设备",
                   "device_ip": "192.0.2.11", "mode": "overwrite"})
    preset.update(kwargs)
    return preset


class ScriptedChoices:
    """
    给 widgets.pick_option 用的脚本化返回值。

    队列元素：整数 = 选项下标；"add" = fixed_tail 第 0 项（即 len(options)）；
    "back" = -1（返回上级）。脚本用尽还继续调用则直接失败，避免测试悄悄跑偏。
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, options, title="", **kwargs):
        self.calls.append((list(options), title, kwargs))
        if not self.script:
            raise AssertionError(f"pick_option 调用次数超出脚本: {title!r}")
        item = self.script.pop(0)
        if item == "add":
            return len(options)
        if item == "back":
            return -1
        return int(item)


class ScriptedInput:
    """给 input() 用的脚本化输入；用完后抛 EOFError（页面按"取消"处理）。"""

    def __init__(self, values):
        self.values = list(values)
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if self.values:
            return self.values.pop(0)
        raise EOFError


class KeyFrames:
    """
    把"每轮扫描时按下的按键集合"喂给 presets._get_async_key_state。

    _scan_pressed() 每轮严格按 presets._CAPTURE_VK_ORDER 顺序各查询一次，
    因此用 调用次数 // 每轮次数 就能定位到当前轮次——这样 mock 的就是真正的
    GetAsyncKeyState 入口，而不是上层的扫描函数。
    """

    def __init__(self, frames):
        self.frames = [set(f) for f in frames]
        self.per_scan = len(presets._CAPTURE_VK_ORDER)
        self.calls = 0

    def __call__(self, vk):
        idx = min(self.calls // self.per_scan, len(self.frames) - 1)
        self.calls += 1
        return 0x8000 if vk in self.frames[idx] else 0


class PresetHarness:
    """把页面的外部依赖全部替换成可断言的假实现（上下文管理器）。"""

    def __init__(self, config, choices=(), inputs=(), confirms=(), hotkeys=(),
                 devices=None, adapters=None):
        self.config = config
        self.choices = ScriptedChoices(choices)
        self.inputs = ScriptedInput(inputs)
        self.confirms = list(confirms)
        self.hotkeys = list(hotkeys)
        self.devices = [DEVICE] if devices is None else list(devices)
        self.adapters = [FakeAdapter()] if adapters is None else list(adapters)
        self.confirm_prompts = []
        self.capture_calls = []
        self.saved_configs = []
        self._saved = []

    def __enter__(self):
        self._saved = [
            (presets, "show_header", presets.show_header),
            (presets, "get_devices", presets.get_devices),
            (presets, "get_network_adapters", presets.get_network_adapters),
            (presets, "capture_hotkey", presets.capture_hotkey),
            (widgets, "pick_option", widgets.pick_option),
            (widgets, "confirm", widgets.confirm),
            (builtins, "input", builtins.input),
            (cm, "save_config", cm.save_config),
        ]

        presets.show_header = lambda config=None: None          # 不查 WMI、不清屏
        presets.get_devices = lambda config: list(self.devices)
        presets.get_network_adapters = lambda filter_virtual=True: list(self.adapters)

        def fake_capture(prompt=presets.CAPTURE_PROMPT, timeout_s=None):
            self.capture_calls.append(prompt)
            return self.hotkeys.pop(0) if self.hotkeys else ""

        presets.capture_hotkey = fake_capture

        def fake_confirm(prompt, default=True):
            self.confirm_prompts.append(prompt)
            if not self.confirms:
                raise AssertionError(f"confirm 调用次数超出脚本: {prompt!r}")
            return self.confirms.pop(0)

        widgets.pick_option = self.choices
        widgets.confirm = fake_confirm
        builtins.input = self.inputs
        cm.save_config = lambda config, config_path=None: self.saved_configs.append(config)
        return self

    def __exit__(self, *exc):
        for module, name, value in self._saved:
            setattr(module, name, value)
        return False


class TestCaptureHotkey(unittest.TestCase):
    """capture_hotkey：GetAsyncKeyState 轮询解析（全部打桩，不碰真实键盘）。"""

    def setUp(self):
        self._saved = [
            (presets, "_capture_supported", presets._capture_supported),
            (presets, "_get_async_key_state", presets._get_async_key_state),
            (presets, "_sleep", presets._sleep),
        ]
        presets._capture_supported = lambda: True
        presets._sleep = lambda seconds: None

    def tearDown(self):
        for module, name, value in self._saved:
            setattr(module, name, value)

    def _capture(self, frames, timeout_s=1.0):
        presets._get_async_key_state = KeyFrames(frames)
        buf = io.StringIO()
        with redirect_stdout(buf):
            combo = presets.capture_hotkey(timeout_s=timeout_s)
        return combo, buf.getvalue()

    def test_ctrl_alt_1(self):
        combo, out = self._capture([{presets.VK_CONTROL, presets.VK_MENU, 0x31}, set()])
        self.assertEqual(combo, "Ctrl+Alt+1")
        self.assertIn("已捕获", out)

    def test_ctrl_shift_f5(self):
        combo, _ = self._capture([{presets.VK_CONTROL, presets.VK_SHIFT, 0x74}, set()])
        self.assertEqual(combo, "Ctrl+Shift+F5")

    def test_win_and_alt_order_is_canonical(self):
        combo, _ = self._capture([{presets.VK_LWIN, presets.VK_MENU, 0x49}, set()])
        self.assertEqual(combo, "Alt+Win+I")

    def test_escape_cancels(self):
        combo, out = self._capture([{presets.VK_ESCAPE}])
        self.assertEqual(combo, "")
        self.assertIn("已取消", out)

    def test_plain_key_warns_then_accepts_combo(self):
        combo, out = self._capture([{0x31}, {presets.VK_CONTROL, 0x31}, set()])
        self.assertEqual(combo, "Ctrl+1", "只按主键应提示后继续等待，不吞掉这次按键")
        self.assertIn("修饰键", out)

    def test_timeout_returns_empty(self):
        combo, out = self._capture([set()], timeout_s=0.01)
        self.assertEqual(combo, "")
        self.assertIn("超时", out)

    def test_non_windows_returns_empty_without_polling(self):
        presets._capture_supported = lambda: False

        def boom(vk):
            raise AssertionError("非 Windows 环境不应轮询 GetAsyncKeyState")

        presets._get_async_key_state = boom
        buf = io.StringIO()
        with redirect_stdout(buf):
            combo = presets.capture_hotkey(timeout_s=1.0)
        self.assertEqual(combo, "")
        self.assertIn("不支持", buf.getvalue())

    def test_captured_combo_is_accepted_by_core(self):
        """捕获结果必须能通过 core.win32host.parse_hotkey（注册热键的前提）。"""
        for frames, expected in (
            ([{presets.VK_CONTROL, presets.VK_MENU, 0x31}, set()], "Ctrl+Alt+1"),
            ([{presets.VK_CONTROL, presets.VK_SHIFT, 0x74}, set()], "Ctrl+Shift+F5"),
        ):
            combo, _ = self._capture(frames)
            self.assertEqual(combo, expected)
            self.assertIsNotNone(win32host.parse_hotkey(combo))


class TestHotkeyConflict(unittest.TestCase):
    def test_conflict_detects_same_combo_ignoring_spelling(self):
        config = {"presets": [make_preset(hotkey="Ctrl+Alt+1")]}
        self.assertEqual(presets._hotkey_conflict(config, "Control+Alt+1"), 0)
        self.assertIsNone(presets._hotkey_conflict(config, "Ctrl+Alt+2"))

    def test_exclude_index_skips_self(self):
        config = {"presets": [make_preset(hotkey="Ctrl+Alt+1")]}
        self.assertIsNone(presets._hotkey_conflict(config, "Ctrl+Alt+1", exclude_index=0))

    def test_empty_or_invalid_combo_never_conflicts(self):
        config = {"presets": [make_preset(hotkey="Ctrl+Alt+1")]}
        self.assertIsNone(presets._hotkey_conflict(config, ""))
        self.assertIsNone(presets._hotkey_conflict(config, "1"))


class TestShowPresets(unittest.TestCase):
    def _run(self, config, choices, inputs=(), confirms=(), hotkeys=(),
             devices=None, adapters=None):
        buf = io.StringIO()
        with PresetHarness(config, choices, inputs, confirms, hotkeys,
                           devices, adapters) as h:
            with redirect_stdout(buf):
                result = presets.show_presets(config)
        return h, result, buf.getvalue()

    # ---------- 新增 ----------

    def test_add_writes_complete_preset(self):
        config = {"devices": [DEVICE], "presets": [], "settings": {}}
        h, result, out = self._run(config, ["add", 0, 0, 0, "back"],
                                   inputs=["机房A-主用"], confirms=[False])
        self.assertIs(result, config, "契约：返回同一个（可能被修改的）config")
        self.assertEqual(len(result["presets"]), 1)
        preset = result["presets"][0]
        self.assertEqual(set(preset), set(cm.PRESET_DEFAULTS), "字段必须与 schema 一致")
        self.assertEqual(preset["name"], "机房A-主用")
        self.assertEqual(preset["adapter_mac"], "AA:BB:CC:DD:EE:FF", "MAC 必须大写")
        self.assertEqual(preset["device_name"], "测试设备")
        self.assertEqual(preset["device_ip"], "192.0.2.11")
        self.assertIn(preset["mode"], cm.PRESET_MODES, "mode 必须在白名单内")
        self.assertEqual(preset["mode"], "overwrite")
        self.assertEqual(preset["hotkey"], "")
        self.assertFalse(preset["enabled"], "新增默认停用，启用时再做干跑校验")
        self.assertIn("预设已添加", out)

    def test_add_with_append_mode_and_hotkey(self):
        config = {"devices": [DEVICE], "presets": [], "settings": {}}
        h, result, out = self._run(config, ["add", 0, 0, 1, "back"],
                                   inputs=[""], confirms=[True], hotkeys=["Ctrl+Alt+1"])
        preset = result["presets"][0]
        self.assertEqual(preset["name"], "测试设备", "空输入用默认名")
        self.assertEqual(preset["mode"], "append")
        self.assertEqual(preset["hotkey"], "Ctrl+Alt+1")
        self.assertEqual(len(h.capture_calls), 1)

    def test_add_without_devices_guides_to_device_manager(self):
        config = {"devices": [], "presets": [], "settings": {}}
        h, result, out = self._run(config, ["add", "back"], devices=[])
        self.assertEqual(result["presets"], [])
        self.assertIn("管理设备", out)

    def test_max_presets_limit_blocks_add(self):
        many = [make_preset(name=f"p{i}", device_ip=f"10.0.0.{i}")
                for i in range(cm.MAX_PRESETS)]
        config = {"devices": [DEVICE], "presets": many, "settings": {}}
        h, result, out = self._run(config, ["add", "back"])
        self.assertEqual(len(result["presets"]), cm.MAX_PRESETS)
        self.assertIn("上限", out)

    def test_empty_state_offers_add_entry(self):
        config = {"devices": [DEVICE], "presets": [], "settings": {}}
        h, result, out = self._run(config, ["back"])
        options, _title, kwargs = h.choices.calls[0]
        self.assertEqual(kwargs.get("fixed_tail"), ["[+] 新增预设"])
        self.assertIn(0, kwargs.get("non_selectable") or set(), "空态占位文案不可选")
        self.assertTrue(any("新增预设" in opt for opt in options))
        self.assertEqual(kwargs.get("hotkeys"), {"a": len(options)})

    # ---------- 热键去重 ----------

    def test_duplicate_hotkey_rejected_then_retry(self):
        config = {"settings": {}, "presets": [make_preset(hotkey="Ctrl+Alt+1")]}
        buf = io.StringIO()
        with PresetHarness(config, hotkeys=["Ctrl+Alt+1", "Ctrl+Shift+F5"]) as h:
            with redirect_stdout(buf):
                combo = presets._choose_hotkey(config)
        self.assertEqual(combo, "Ctrl+Shift+F5", "冲突后必须重新捕获")
        self.assertEqual(len(h.capture_calls), 2)
        self.assertIn("不能绑两次", buf.getvalue())

    def test_add_does_not_write_duplicate_hotkey(self):
        config = {"devices": [DEVICE], "settings": {},
                  "presets": [make_preset(hotkey="Ctrl+Alt+1")]}
        h, result, out = self._run(config, ["add", 0, 0, 0, "back"],
                                   inputs=["新预设"], confirms=[True],
                                   hotkeys=["Ctrl+Alt+1", ""])
        self.assertEqual(len(result["presets"]), 2)
        self.assertEqual(result["presets"][1]["hotkey"], "", "冲突的组合不能落库")
        self.assertEqual(result["presets"][0]["hotkey"], "Ctrl+Alt+1")
        self.assertIn("不能绑两次", out)

    def test_rebind_hotkey_updates_preset(self):
        config = {"devices": [DEVICE], "settings": {},
                  "presets": [make_preset(hotkey="Ctrl+Alt+1")]}
        h, result, out = self._run(config, [0, 3, "back"], hotkeys=["Ctrl+Shift+F5"])
        self.assertEqual(result["presets"][0]["hotkey"], "Ctrl+Shift+F5")

    def test_clear_hotkey(self):
        config = {"devices": [DEVICE], "settings": {},
                  "presets": [make_preset(hotkey="Ctrl+Alt+1")]}
        h, result, out = self._run(config, [0, 4, "back"])
        self.assertEqual(result["presets"][0]["hotkey"], "")
        self.assertIn("已清除热键", out)

    # ---------- 编辑 ----------

    def test_rename_preset(self):
        config = {"devices": [DEVICE], "settings": {},
                  "presets": [make_preset(name="旧名字")]}
        h, result, out = self._run(config, [0, 0, "back"], inputs=["新名字"])
        self.assertEqual(result["presets"][0]["name"], "新名字")

    def test_toggle_enabled(self):
        config = {"devices": [DEVICE], "settings": {},
                  "presets": [make_preset(enabled=False)]}
        h, result, out = self._run(config, [0, 1, "back"])
        self.assertTrue(result["presets"][0]["enabled"])
        self.assertIn("已启用", out)

    def test_switch_mode_to_append(self):
        config = {"devices": [DEVICE], "settings": {},
                  "presets": [make_preset(mode="overwrite")]}
        h, result, out = self._run(config, [0, 2, 1, "back"])
        self.assertEqual(result["presets"][0]["mode"], "append")
        self.assertIn(result["presets"][0]["mode"], cm.PRESET_MODES)

    # ---------- 删除 ----------

    def test_delete_empties_list(self):
        config = {"devices": [DEVICE], "settings": {},
                  "presets": [make_preset(name="待删除")]}
        h, result, out = self._run(config, [0, 5, "back"], confirms=[True])
        self.assertEqual(result["presets"], [])
        self.assertIn("已删除", out)
        self.assertTrue(any("确认删除" in p for p in h.confirm_prompts))

    def test_delete_cancelled_keeps_preset(self):
        config = {"devices": [DEVICE], "settings": {},
                  "presets": [make_preset(name="保留")]}
        h, result, out = self._run(config, [0, 5, "back"], confirms=[False])
        self.assertEqual(len(result["presets"]), 1)
        self.assertIn("已取消", out)

    # ---------- 健壮性与契约 ----------

    def test_empty_config_does_not_crash(self):
        h, result, out = self._run({}, ["back"])
        self.assertEqual(result["presets"], [])

    def test_broken_presets_value_is_repaired(self):
        for broken in (None, "x", 5, {"a": 1}):
            with self.subTest(broken=broken):
                config = {"presets": broken, "settings": {}}
                h, result, out = self._run(config, ["back"])
                self.assertEqual(result["presets"], [])

    def test_page_does_not_save_config(self):
        """契约：本页只改 config，落盘由动作注册表的 needs_save=True 负责。"""
        config = {"devices": [DEVICE], "presets": [], "settings": {}}
        h, result, out = self._run(config, ["add", 0, 0, 0, "back"],
                                   inputs=["x"], confirms=[False])
        self.assertEqual(h.saved_configs, [])

    def test_list_line_shows_all_fields(self):
        line = presets._preset_line(make_preset(
            name="机房A", device_name="SW1", device_ip="192.0.2.11",
            adapter_mac="AA:BB:CC:DD:EE:FF", mode="append",
            hotkey="Ctrl+Alt+1", enabled=True))
        for fragment in ("机房A", "SW1", "192.0.2.11", "AA:BB:CC:DD:EE:FF",
                         "追加", "Ctrl+Alt+1", "启用"):
            self.assertIn(fragment, line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
