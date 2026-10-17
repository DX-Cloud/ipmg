# -*- coding: utf-8 -*-
"""
配置 schema 的回归测试（常驻/热键/预设 新键 + 未知键保留策略）。

运行：python -m unittest discover -s tests -p "test_config_schema.py" -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from core import config_manager as cm  # noqa: E402


class TestSettingsDefaults(unittest.TestCase):
    def test_new_groups_present(self):
        s = cm.SETTINGS_DEFAULTS
        self.assertIn("resident", s)
        self.assertIn("hotkeys", s)
        self.assertIn("notify", s)
        self.assertIn("configure", s)
        self.assertIs(s["resident"]["enabled"], False)
        self.assertIs(s["resident"]["close_to_tray"], True)
        self.assertIs(s["resident"]["autostart"], False)
        self.assertEqual(s["hotkeys"]["summon"], "Ctrl+Alt+I")
        self.assertIs(s["notify"]["balloon"], True)
        self.assertIs(s["notify"]["apply_requires_confirm"], False)
        self.assertEqual(s["configure"]["default_mode"], "overwrite")

    def test_new_keys_registered_in_schema(self):
        paths = {row[0] for row in cm.SETTING_SCHEMA}
        for p in ("resident.enabled", "resident.close_to_tray", "resident.autostart",
                  "hotkeys.summon", "notify.balloon", "notify.apply_requires_confirm",
                  "configure.default_mode"):
            self.assertIn(p, paths, f"{p} 必须登记进 SETTING_SCHEMA，否则设置页看不到")

    def test_default_config_has_presets(self):
        cfg = cm._get_default_config()
        self.assertEqual(cfg["presets"], [])
        self.assertIn("presets", cfg)


class TestNormalizeSettings(unittest.TestCase):
    def test_missing_keys_filled(self):
        merged = cm._normalize_settings({})
        self.assertEqual(merged["configure"]["default_mode"], "overwrite")
        self.assertIs(merged["resident"]["enabled"], False)

    def test_known_values_kept(self):
        merged = cm._normalize_settings({"configure": {"default_mode": "append"},
                                         "resident": {"enabled": True}})
        self.assertEqual(merged["configure"]["default_mode"], "append")
        self.assertIs(merged["resident"]["enabled"], True)
        # 未提供的同组键仍取默认
        self.assertIs(merged["resident"]["close_to_tray"], True)

    def test_unknown_top_level_key_preserved(self):
        """未知设置键必须保留：旧版本跑一次不应抹掉新版写入的设置。"""
        merged = cm._normalize_settings({"future_flag": True, "theme": "dark"})
        self.assertIs(merged["future_flag"], True)
        self.assertEqual(merged["theme"], "dark")

    def test_unknown_nested_key_preserved(self):
        merged = cm._normalize_settings({"update_check": {"enabled": False,
                                                          "mirror": "https://x"}})
        self.assertIs(merged["update_check"]["enabled"], False)
        self.assertEqual(merged["update_check"]["mirror"], "https://x")

    def test_non_dict_input(self):
        merged = cm._normalize_settings(None)
        self.assertEqual(merged["configure"]["default_mode"], "overwrite")


class TestNormalizePresets(unittest.TestCase):
    def test_valid_preset_gets_defaults(self):
        out = cm._normalize_presets([{"name": "  机房A  ", "device_ip": " 10.0.0.1 ",
                                      "adapter_mac": "aa:bb:cc:dd:ee:ff",
                                      "mode": "append", "enabled": 1}])
        self.assertEqual(len(out), 1)
        p = out[0]
        self.assertEqual(p["name"], "机房A")
        self.assertEqual(p["device_ip"], "10.0.0.1")
        self.assertEqual(p["adapter_mac"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(p["mode"], "append")
        self.assertIs(p["enabled"], True)
        self.assertEqual(p["hotkey"], "")          # 默认不绑定热键
        self.assertIn("device_name", p)

    def test_bad_mode_falls_back(self):
        out = cm._normalize_presets([{"device_name": "sw1", "mode": "merge"}])
        self.assertEqual(out[0]["mode"], "overwrite")

    def test_preset_without_device_ref_dropped(self):
        out = cm._normalize_presets([{"name": "空预设"}, {"device_ip": "10.0.0.9"}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["device_ip"], "10.0.0.9")

    def test_garbage_input(self):
        self.assertEqual(cm._normalize_presets(None), [])
        self.assertEqual(cm._normalize_presets("x"), [])
        self.assertEqual(cm._normalize_presets([1, "a", None]), [])

    def test_cap(self):
        many = [{"device_ip": f"10.0.0.{i}"} for i in range(cm.MAX_PRESETS + 5)]
        self.assertEqual(len(cm._normalize_presets(many)), cm.MAX_PRESETS)


class TestConfigRoundTrip(unittest.TestCase):
    """真实读写：未知设置键经过 load → save 之后必须仍在文件里。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cfg_schema_")
        self.path = os.path.join(self.tmp, "config.yaml")

    def test_unknown_settings_survive_roundtrip(self):
        cfg = cm._get_default_config()
        cfg["settings"]["future_flag"] = True
        cfg["settings"]["update_check"]["mirror"] = "https://mirror.example"
        cm.save_config(cfg, self.path)

        loaded = cm.load_config(self.path)
        self.assertIs(loaded["settings"]["future_flag"], True)
        self.assertEqual(loaded["settings"]["update_check"]["mirror"],
                         "https://mirror.example")

        cm.save_config(loaded, self.path)
        with open(self.path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        self.assertIs(raw["settings"]["future_flag"], True)
        self.assertEqual(raw["settings"]["update_check"]["mirror"],
                         "https://mirror.example")
        # 新增键也落盘了
        self.assertEqual(raw["presets"], [])
        self.assertEqual(raw["settings"]["configure"]["default_mode"], "overwrite")

    def test_presets_roundtrip(self):
        cfg = cm._get_default_config()
        cfg["presets"] = [{"name": "p1", "device_ip": "10.0.0.5", "mode": "append"}]
        cm.save_config(cfg, self.path)
        loaded = cm.load_config(self.path)
        self.assertEqual(len(loaded["presets"]), 1)
        self.assertEqual(loaded["presets"][0]["mode"], "append")


class TestOperationGate(unittest.TestCase):
    def test_acquire_release_and_busy(self):
        from core.opgate import OperationGate, OperationBusy
        g = OperationGate()
        self.assertFalse(g.busy)
        self.assertTrue(g.acquire("配置IP"))
        self.assertTrue(g.busy)
        self.assertIn("配置IP", g.busy_reason)
        g.release()
        self.assertFalse(g.busy)
        self.assertEqual(g.busy_reason, "")

    def test_second_acquire_without_wait_fails(self):
        import threading
        from core.opgate import OperationGate
        g = OperationGate()
        g.acquire("配置IP")
        result = {}

        def worker():
            result["ok"] = g.acquire("热键应用", timeout=0)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)
        self.assertIs(result.get("ok"), False, "跨线程必须互斥，且 timeout=0 不等待")
        g.release()

    def test_hold_context_manager_and_busy_exception(self):
        import threading
        from core.opgate import OperationGate, OperationBusy
        g = OperationGate()
        with g.hold("配置IP"):
            self.assertTrue(g.busy)
        self.assertFalse(g.busy)

        g.acquire("长操作")
        err = {}

        def worker():
            try:
                with g.hold("热键应用", timeout=0):
                    pass
            except OperationBusy as e:
                err["msg"] = str(e)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)
        self.assertIn("长操作", err.get("msg", ""))
        g.release()

    def test_reentrant_same_thread(self):
        from core.opgate import OperationGate
        g = OperationGate()
        self.assertTrue(g.acquire("外层"))
        self.assertTrue(g.acquire("内层"), "同一线程应可重入")
        g.release()
        self.assertTrue(g.busy, "重入两层只释放一层，仍应为持有状态")
        g.release()
        self.assertFalse(g.busy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
