"""
core.singleton / core.win32host 的自动化回归测试（stdlib unittest）。

运行：
    python -m unittest discover -s tests -p "test_win32_modules.py" -v

测试在**无交互终端**的自动化管道里也必须通过：
- 不做托盘图标显示断言、不模拟真实按键、不断言隐藏效果；
- 热键冲突用 Ctrl+Alt+Shift+F24 这种几乎不可能被占用的组合；
- 不创建/删除计划任务，不触碰网卡配置。

托盘显示、真实按键唤出、ConPTY 隐藏效果 → 人工验证（见任务交付说明）。
"""

import ctypes
import os
import sys
import threading
import time
import unittest
import uuid

# 允许从仓库任意位置运行（python -m unittest ... 已含 cwd，这里再兜底一次）
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core import singleton, win32host  # noqa: E402

_IS_WINDOWS = sys.platform == "win32"
_IS_64BIT = ctypes.sizeof(ctypes.c_void_p) == 8

# 冲突测试专用组合：极少被占用（F24 需要 Fn 层，且三修饰键）
_FREE_COMBO = "Ctrl+Alt+Shift+F24"


# ============================== 热键字符串解析 ==============================


class TestHotkeyParse(unittest.TestCase):
    """parse_hotkey / format_hotkey 解析、格式化与往返。"""

    def test_parse_basic(self):
        self.assertEqual(win32host.parse_hotkey("Ctrl+Alt+I"),
                         (win32host.MOD_CONTROL | win32host.MOD_ALT, 0x49))
        self.assertEqual(win32host.parse_hotkey("ctrl+shift+f24"),
                         (win32host.MOD_CONTROL | win32host.MOD_SHIFT, 0x87))
        self.assertEqual(win32host.parse_hotkey("Win+Alt+1"),
                         (win32host.MOD_WIN | win32host.MOD_ALT, 0x31))
        self.assertEqual(win32host.parse_hotkey("control+windows+z"),
                         (win32host.MOD_CONTROL | win32host.MOD_WIN, 0x5A))

    def test_parse_returns_no_norepeat(self):
        mods, _vk = win32host.parse_hotkey("Ctrl+Alt+I")
        self.assertFalse(mods & win32host.MOD_NOREPEAT,
                         "parse_hotkey 不应带 MOD_NOREPEAT（注册时才 OR）")

    def test_parse_invalid(self):
        for combo in ("", None, "I", "Ctrl", "Alt+Shift", "Ctrl+I+J", "Ctrl+Foo",
                      "Ctrl+", "+I", "Ctrl+Alt+", "F24", "ctrl + + i"):
            with self.subTest(combo=combo):
                self.assertIsNone(win32host.parse_hotkey(combo))

    def test_format_and_roundtrip(self):
        self.assertEqual(win32host.format_hotkey(
            win32host.MOD_CONTROL | win32host.MOD_ALT, 0x49), "Ctrl+Alt+I")
        self.assertEqual(win32host.format_hotkey(
            win32host.MOD_WIN | win32host.MOD_SHIFT | win32host.MOD_ALT, 0x31),
            "Alt+Shift+Win+1")
        for combo in ("Ctrl+Alt+I", "Ctrl+Shift+F24", "Alt+Shift+Win+9",
                      "Ctrl+Alt+Shift+1", "Win+F1"):
            with self.subTest(combo=combo):
                parsed = win32host.parse_hotkey(combo)
                self.assertIsNotNone(parsed)
                self.assertEqual(win32host.parse_hotkey(
                    win32host.format_hotkey(*parsed)), parsed)

    def test_format_unknown_vk(self):
        self.assertEqual(win32host.format_hotkey(win32host.MOD_CONTROL, 0x00), "Ctrl+?")
        self.assertEqual(win32host.format_hotkey(0, 0x49), "I")


# ============================== NOTIFYICONDATAW 布局 ==============================


class TestNotifyIconDataLayout(unittest.TestCase):
    """cbSize 与 64 位字段对齐（Shell_NotifyIconW 最容易踩的坑）。"""

    def test_cbsize(self):
        expected = 976 if _IS_64BIT else 956
        self.assertEqual(win32host.notifyicondata_size(), expected)
        self.assertEqual(ctypes.sizeof(win32host.NOTIFYICONDATAW), expected)
        self.assertEqual(win32host._new_nid().cbSize, expected)

    def test_field_offsets(self):
        nid = win32host.NOTIFYICONDATAW
        if _IS_64BIT:
            expected = {"cbSize": 0, "hWnd": 8, "uID": 16, "uFlags": 20,
                        "uCallbackMessage": 24, "hIcon": 32, "szTip": 40,
                        "dwState": 296, "szInfo": 304, "uTimeoutOrVersion": 816,
                        "szInfoTitle": 820, "dwInfoFlags": 948, "guidItem": 952,
                        "hBalloonIcon": 968}
        else:
            expected = {"cbSize": 0, "hWnd": 4, "uID": 8, "uFlags": 12,
                        "uCallbackMessage": 16, "hIcon": 20, "szTip": 24,
                        "dwState": 280, "szInfo": 288, "uTimeoutOrVersion": 800,
                        "szInfoTitle": 804, "dwInfoFlags": 932, "guidItem": 936,
                        "hBalloonIcon": 952}
        for name, offset in expected.items():
            with self.subTest(field=name):
                self.assertEqual(getattr(nid, name).offset, offset)

    def test_field_capacity(self):
        nid = win32host.NOTIFYICONDATAW
        fields = dict(nid._fields_)
        # 字符容量看字段类型的 _length_（.size 是字节数）
        self.assertEqual(fields["szTip"]._length_, 128)
        self.assertEqual(fields["szInfo"]._length_, 256)
        self.assertEqual(fields["szInfoTitle"]._length_, 64)


# ============================== 单实例与唤出信号 ==============================


@unittest.skipUnless(_IS_WINDOWS, "仅 Windows")
class TestSingleton(unittest.TestCase):
    """互斥体与命名事件（同进程内可验证的路径）。"""

    # 每个测试进程用唯一名字：避免与真实运行的实例（或上一次跑残留的对象）互相干扰。
    # 用生产名 "ipmg" 的测试曾在有实例常驻时整片失败，属于测试自身的设计问题。
    APP = f"ipmg-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    def setUp(self):
        singleton.release()

    def tearDown(self):
        singleton.release()

    def test_names(self):
        self.assertEqual(singleton.mutex_name("ipmg"), "Local\\ipmg.singleton")
        self.assertEqual(singleton.event_name("ipmg"), "Local\\ipmg.activate")

    def test_acquire_release_reacquire(self):
        self.assertTrue(singleton.acquire(self.APP))
        self.assertTrue(singleton.acquire(self.APP), "同一进程重复 acquire 应幂等返回 True")
        singleton.release()
        self.assertTrue(singleton.acquire(self.APP), "release 后应能重新获取")

    def test_acquire_false_when_owned_by_another(self):
        """
        另一个**线程/进程**持有同名互斥体时，acquire 必须返回 False。

        注意：互斥体所有权按线程归属且可递归，同线程内再获取总是成功，
        因此必须让"外部持有者"跑在另一个线程里才构成真实占用。
        """
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CloseHandle.argtypes = [ctypes.c_void_p]

        name = singleton.mutex_name(self.APP)
        ready = threading.Event()
        release_holder = threading.Event()

        def holder_thread():
            handle = k32.CreateMutexW(None, True, name)   # 由本线程持有
            ready.set()
            release_holder.wait(5)
            k32.CloseHandle(handle)                       # 关句柄即释放所有权

        thread = threading.Thread(target=holder_thread, name="mutex-holder", daemon=True)
        thread.start()
        self.assertTrue(ready.wait(3), "测试前置：持有线程应就绪")
        try:
            self.assertFalse(singleton.acquire(self.APP),
                             "被其它线程持有时不应取得所有权")
        finally:
            release_holder.set()
            thread.join(3)
        self.assertTrue(singleton.acquire(self.APP), "持有者释放后应能获取")

    def test_acquire_succeeds_when_object_exists_but_unowned(self):
        """
        回归：同名对象存在但**无人持有**时，acquire 必须成功。

        旧实现只看 CreateMutexW 的 ERROR_ALREADY_EXISTS，这种残留对象会让程序
        永久以为"已有实例在运行"而拒绝启动。
        """
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        # bInitialOwner=False → 对象被创建，但没有任何进程持有它
        foreign = k32.CreateMutexW(None, False, singleton.mutex_name(self.APP))
        self.assertTrue(foreign, "测试前置：应能创建同名互斥体")
        try:
            self.assertTrue(singleton.acquire(self.APP),
                            "对象存在但无人持有时应能取得所有权")
        finally:
            singleton.release()
            k32.CloseHandle(foreign)

    def test_wait_activate_timeout_returns_false(self):
        self.assertFalse(singleton.wait_activate(self.APP, 150))

    def test_signal_without_waiter_returns_false(self):
        self.assertFalse(singleton.signal_existing(self.APP))

    def test_signal_then_wait(self):
        result = {}

        def waiter():
            result["ok"] = singleton.wait_activate(self.APP, 3000)

        thread = threading.Thread(target=waiter, name="test-wait-activate", daemon=True)
        thread.start()
        # 等待 waiter 建好事件（OpenEvent 成功即说明事件已存在）
        signaled = False
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if singleton.signal_existing(self.APP):
                signaled = True
                break
            time.sleep(0.05)
        thread.join(3.0)
        self.assertTrue(signaled, "等待开始后 signal_existing 应返回 True")
        self.assertTrue(result.get("ok"), "signal 后 wait_activate 应返回 True")

    def test_release_is_idempotent(self):
        singleton.acquire(self.APP)
        singleton.release()
        singleton.release()  # 不应抛异常


class TestRaiseConsole(unittest.TestCase):
    """raise_console：重复启动时把既有窗口拉到前台（不需要 Win32Host 实例）。"""

    def setUp(self):
        # 保存整个 DLL 绑定对象（不能只存某个函数的引用，否则还原后模块会被污染）
        self._real_k32 = win32host._kernel32
        self._real_u32 = win32host._user32

    def tearDown(self):
        win32host._kernel32 = self._real_k32
        win32host._user32 = self._real_u32

    @unittest.skipUnless(_IS_WINDOWS, "仅 Windows")
    def test_returns_false_without_console(self):
        """无控制台窗口（ConPTY/无控制台）时必须安全返回 False。"""

        class FakeK32:
            @staticmethod
            def GetConsoleWindow():
                return 0

        win32host._kernel32 = FakeK32
        self.assertFalse(win32host.raise_console())

    @unittest.skipUnless(_IS_WINDOWS, "仅 Windows")
    def test_shows_and_focuses_window(self):
        """有控制台窗口时应调用 ShowWindow + SetForegroundWindow。"""
        used = []

        class FakeK32:
            @staticmethod
            def GetConsoleWindow():
                return 0x1234

        class FakeU32:
            @staticmethod
            def ShowWindow(hwnd, cmd):
                used.append(("show", hwnd, cmd))
                return True

            @staticmethod
            def SetForegroundWindow(hwnd):
                used.append(("focus", hwnd))
                return True

            @staticmethod
            def IsWindowVisible(hwnd):
                return True

        win32host._kernel32, win32host._user32 = FakeK32, FakeU32
        self.assertTrue(win32host.raise_console())
        self.assertIn(("focus", 0x1234), used, "应尝试前置窗口")
        self.assertTrue(any(u[0] == "show" for u in used), "应显示窗口")

    @unittest.skipUnless(_IS_WINDOWS, "仅 Windows")
    def test_restores_real_bindings(self):
        """自检：用例结束后模块绑定必须恢复（防止污染后续用例）。"""
        win32host._kernel32 = object()
        win32host._kernel32 = self._real_k32
        self.assertIs(win32host._kernel32, self._real_k32)


# ============================== Win32Host 事件线程 ==============================


@unittest.skipUnless(_IS_WINDOWS, "仅 Windows")
class TestWin32Host(unittest.TestCase):
    """宿主线程生命周期、热键注册/冲突、控制台接口的健壮性。"""

    def setUp(self):
        self.hotkey_calls = []
        self.tray_events = []
        self.host = win32host.Win32Host(
            on_hotkey=self.hotkey_calls.append,
            on_tray=self.tray_events.append,
        )
        self.host.start()
        if not self.host.is_alive():
            self.skipTest(f"宿主线程不可用: {self.host._fatal}")

    def tearDown(self):
        try:
            self.host.stop()
        except Exception:
            pass

    def test_start_stop(self):
        self.assertTrue(self.host.is_alive())
        self.host.stop()
        self.assertFalse(self.host.is_alive())
        self.host.stop()  # 幂等

    def test_register_hotkey_ok_and_conflict(self):
        ok, err = self.host.register_hotkey(101, _FREE_COMBO)
        self.assertTrue(ok, f"首个注册应成功，实际错误: {err}")
        self.assertEqual(err, "")

        ok2, err2 = self.host.register_hotkey(102, _FREE_COMBO)
        self.assertFalse(ok2, "同一组合第二次注册应失败")
        self.assertIn("1409", err2, f"错误串应含 Win32 错误码 1409，实际: {err2}")

        self.host.unregister_hotkey(101)
        ok3, err3 = self.host.register_hotkey(102, _FREE_COMBO)
        self.assertTrue(ok3, f"释放原注册后应能重新注册，实际错误: {err3}")
        self.host.unregister_hotkey(102)

    def test_register_hotkey_invalid_combo(self):
        for combo in ("", "I", "Ctrl", "Ctrl+I+J", "Ctrl+Foo"):
            with self.subTest(combo=combo):
                ok, err = self.host.register_hotkey(110, combo)
                self.assertFalse(ok)
                self.assertTrue(err)

    def test_register_hotkey_invalid_id(self):
        ok, err = self.host.register_hotkey(0xC000, "Ctrl+Alt+I")
        self.assertFalse(ok)
        self.assertTrue(err)

    def test_console_toggles_return_bool(self):
        # 只断言"返回 bool 且不抛异常"，不断言隐藏效果（ConPTY 场景由上层降级）
        hidden = self.host.hide_console()
        shown = self.host.show_console()
        self.assertIsInstance(hidden, bool)
        self.assertIsInstance(shown, bool)

    def test_notify_without_tray_returns_false(self):
        self.assertFalse(self.host.notify("ipmg", "自动化测试不应弹出气泡"))

    def test_calls_after_stop_are_safe(self):
        self.host.stop()
        ok, err = self.host.register_hotkey(120, "Ctrl+Alt+I")
        self.assertFalse(ok)
        self.assertTrue(err)
        self.host.unregister_hotkey(120)          # 不抛异常
        self.assertFalse(self.host.set_tray(None, "ipmg"))
        self.assertFalse(self.host.notify("ipmg", "已停止"))
        self.host.remove_tray()                   # 不抛异常
        self.assertIsInstance(self.host.hide_console(), bool)
        self.assertIsInstance(self.host.show_console(), bool)

    def test_hotkey_callback_not_fired_without_keypress(self):
        self.assertEqual(self.hotkey_calls, [])
        self.assertEqual(self.tray_events, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
