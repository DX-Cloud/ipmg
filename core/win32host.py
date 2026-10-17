"""
Win32 事件线程宿主：全局热键 + 系统托盘 + 隐藏控制台（纯 ctypes，零第三方依赖）。

线程模型（对应常驻方案 §4.4 / §3.4）
------------------------------------
``RegisterHotKey`` / ``Shell_NotifyIconW`` 都要求"在拥有消息队列的线程"里调用，
因此本模块把窗口类、隐藏窗口、热键与托盘图标全部收敛到一条宿主线程：

    Win32Host.start()  →  宿主线程 RegisterClassW + CreateWindowExW（隐藏窗口）
                       →  GetMessageW 消息循环

其它线程只能通过 ``_invoke()`` 提交请求：请求入队 → ``PostThreadMessageW`` 唤醒
宿主线程 → 宿主线程出队执行 → 每请求 ``threading.Event`` 同步返回结果。
公开方法因此是**同步阻塞并返回结果**的，且永不抛异常给调用方。

任何 Win32 调用的异常都被吞掉并降级为返回值/错误串 + logging，主程序不会崩。
"""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import sys
import threading
from ctypes import wintypes
from typing import Any, Callable, Optional, Tuple

_log = logging.getLogger("IPManager.win32host")

_IS_WINDOWS = sys.platform == "win32"

# ---- 热键修饰键 ----
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# ---- 消息 ----
WM_USER = 0x0400
WM_APP = 0x8000
WM_QUIT = 0x0012
WM_DESTROY = 0x0002
WM_HOTKEY = 0x0312
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
# 托盘通知码（NIN_*，随 lParam 传入）
NIN_SELECT = WM_USER + 0          # 0x0400
NIN_KEYSELECT = WM_USER + 1       # 0x0401
NIN_BALLOONSHOW = WM_USER + 2     # 0x0402
NIN_BALLOONHIDE = WM_USER + 3     # 0x0403
NIN_BALLOONTIMEOUT = WM_USER + 4  # 0x0404
NIN_BALLOONUSERCLICK = WM_USER + 5  # 0x0405

# 线程消息：宿主线程处理跨线程请求
WM_HOST_WAKE = WM_APP + 0x200
# 托盘回调消息
WM_TRAYICON = WM_APP + 0x201

# ---- 窗口 ----
SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9
HWND_MESSAGE = -3

# ---- 托盘 ----
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIIF_INFO = 0x00000001
NIIF_WARNING = 0x00000002
IDI_APPLICATION = 32512
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
LR_SHARED = 0x00008000

# ---- 错误码 ----
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
ERROR_CLASS_ALREADY_EXISTS = 1410

_WINDOW_CLASS = "ipmg_win32host_wndclass"
_WINDOW_TITLE = "ipmg_win32host"
_TRAY_UID = 1
_MAX_HOTKEY_ID = 0xBFFF

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


def _get_last_error() -> int:
    """读取 ctypes 保存的 LastError（配合 use_last_error=True 的 DLL 绑定）。"""
    try:
        getter = getattr(ctypes, "get_last_error", None) or getattr(ctypes, "GetLastError")
        return int(getter())
    except Exception:
        return 0


if _IS_WINDOWS:  # pragma: no cover - 平台分支
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    _user32.RegisterClassW.argtypes = [ctypes.c_void_p]
    _user32.RegisterClassW.restype = wintypes.WORD
    _user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE]
    _user32.UnregisterClassW.restype = wintypes.BOOL
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HANDLE, wintypes.LPVOID,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
    _user32.DefWindowProcW.restype = LRESULT
    _user32.GetMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.TranslateMessage.argtypes = [ctypes.c_void_p]
    _user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                       wintypes.UINT, wintypes.UINT]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _user32.LoadImageW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.UINT,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _user32.LoadImageW.restype = wintypes.HANDLE
    _user32.DestroyIcon.argtypes = [wintypes.HANDLE]
    _user32.DestroyIcon.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _user32.AttachThreadInput.restype = wintypes.BOOL

    # PostThreadMessageW 由 user32 导出（kernel32 亦有转发，做兜底）
    _post_thread_message = getattr(_user32, "PostThreadMessageW",
                                   getattr(_kernel32, "PostThreadMessageW", None))
    if _post_thread_message is not None:
        _post_thread_message.argtypes = [wintypes.DWORD, wintypes.UINT,
                                         wintypes.WPARAM, wintypes.LPARAM]
        _post_thread_message.restype = wintypes.BOOL

    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HANDLE
    _kernel32.GetCurrentThreadId.argtypes = []
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    # GetConsoleWindow 由 kernel32 导出（wincon.h），不是 user32
    _kernel32.GetConsoleWindow.argtypes = []
    _kernel32.GetConsoleWindow.restype = wintypes.HWND

    _shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
    _shell32.Shell_NotifyIconW.restype = wintypes.BOOL
else:  # pragma: no cover - 平台分支
    _user32 = None
    _kernel32 = None
    _shell32 = None
    _post_thread_message = None


# ============================== 热键字符串 ==============================

_KEY_TO_VK = {}
for _i in range(26):
    _KEY_TO_VK[chr(ord("A") + _i)] = 0x41 + _i
for _d in range(10):
    _KEY_TO_VK[str(_d)] = 0x30 + _d
for _f in range(1, 25):
    _KEY_TO_VK[f"F{_f}"] = 0x70 + _f - 1

_VK_TO_KEY = {vk: name for name, vk in _KEY_TO_VK.items()}

_MOD_NAMES = ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"),
              (MOD_SHIFT, "Shift"), (MOD_WIN, "Win"))


def parse_hotkey(combo: str) -> Optional[Tuple[int, int]]:
    """
    解析 ``"Ctrl+Alt+I"`` → ``(mods, vk)``；非法返回 None。

    规则：修饰键大小写不敏感（Ctrl/Control/Alt/Shift/Win/Windows），主键为
    A-Z / 0-9 / F1-F24；**至少一个修饰键**且**只能一个主键**；空串、重复主键、
    未知键名、尾随 ``+`` 一律判非法。返回值不含 MOD_NOREPEAT（注册时再加）。
    """
    if not combo or not isinstance(combo, str):
        return None

    mods = 0
    key: Optional[str] = None
    for raw in combo.split("+"):
        part = raw.strip()
        if not part:
            return None
        low = part.lower()
        if low in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif low == "alt":
            mods |= MOD_ALT
        elif low == "shift":
            mods |= MOD_SHIFT
        elif low in ("win", "windows"):
            mods |= MOD_WIN
        else:
            if key is not None:
                return None  # 多个主键
            key = part.upper()

    if mods == 0 or key is None:
        return None
    vk = _KEY_TO_VK.get(key)
    if vk is None:
        return None
    return (mods, vk)


def format_hotkey(mods: int, vk: int) -> str:
    """``(mods, vk)`` → 规范字符串（顺序 Ctrl→Alt→Shift→Win→主键）。"""
    parts = [name for bit, name in _MOD_NAMES if int(mods) & bit]
    name = _VK_TO_KEY.get(int(vk))
    if name is None:
        name = f"0x{int(vk):02X}" if int(vk) > 0 else "?"
    parts.append(name)
    return "+".join(parts)


# ============================== NOTIFYICONDATAW ==============================


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_byte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    """Shell_NotifyIconW 的 V3 结构（字段顺序与 64 位对齐必须与 Windows 一致）。"""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HANDLE),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HANDLE),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HANDLE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


def notifyicondata_size() -> int:
    """``NOTIFYICONDATAW`` 的正确 cbSize（x64 应为 976，x86 应为 956）。"""
    return ctypes.sizeof(NOTIFYICONDATAW)


def _new_nid() -> NOTIFYICONDATAW:
    """构造一个已填好 cbSize 的 NOTIFYICONDATAW。"""
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    return nid


# ------------------------------ 原生对话框 ------------------------------
# 托盘气泡依赖系统通知设置，可能被"专注助手/通知关闭"静默抑制；
# 需要"一定会被看到"的场合（二次确认、气泡不可用时的结果反馈）用原生对话框。

MB_OK = 0x00000000
MB_YESNO = 0x00000004
MB_ICONQUESTION = 0x00000020
MB_ICONINFORMATION = 0x00000040
MB_ICONWARNING = 0x00000030
MB_TOPMOST = 0x00040000
MB_SETFOREGROUND = 0x00010000
IDYES = 6


def confirm_dialog(title: str, text: str) -> bool:
    """
    弹出原生"是/否"确认框，返回用户是否点了"是"。

    可在任意线程调用（内部有独立的消息循环），因此适合在热键工作线程里
    做"应用前二次确认"。任何异常都返回 False（安全侧：不确认就不动作）。
    """
    try:
        flags = MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND
        result = _user32.MessageBoxW(None, str(text)[:2000], str(title)[:200], flags)
        return int(result) == IDYES
    except Exception as exc:  # pragma: no cover - 防御
        _log.warning("confirm_dialog 失败: %s", exc)
        return False


def info_dialog(title: str, text: str, warning: bool = False) -> None:
    """弹出原生"确定"提示框（用于气泡不可用时的兜底反馈）。"""
    try:
        flags = MB_OK | MB_TOPMOST | MB_SETFOREGROUND
        flags |= MB_ICONWARNING if warning else MB_ICONINFORMATION
        _user32.MessageBoxW(None, str(text)[:2000], str(title)[:200], flags)
    except Exception as exc:  # pragma: no cover - 防御
        _log.warning("info_dialog 失败: %s", exc)


# ============================== 事件线程宿主 ==============================


class _HostUnavailable(RuntimeError):
    """宿主线程不可用（未启动/已停止/响应超时）。"""


class _Request:
    __slots__ = ("func", "args", "event", "ok", "result")

    def __init__(self, func: Callable, args: tuple):
        self.func = func
        self.args = args
        self.event = threading.Event()
        self.ok = False
        self.result: Any = None


class Win32Host(threading.Thread):
    """
    一条 Win32 事件线程：隐藏消息窗口 + 全局热键 + 托盘图标。

    :param on_hotkey: 热键回调，参数为注册时使用的 hotkey_id（在宿主线程执行）
    :param on_tray:   托盘回调，参数为事件名：
                      ``"left_click"`` / ``"double_click"`` / ``"right_click"`` /
                      ``"balloon_click"`` / ``"tray_0xNNNN"``（未知消息）
    """

    def __init__(self, on_hotkey: Callable[[int], None],
                 on_tray: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(name="ipmg-win32host", daemon=True)
        self._on_hotkey = on_hotkey
        self._on_tray = on_tray
        self._requests: "queue.Queue[_Request]" = queue.Queue()
        self._ready = threading.Event()
        self._thread_id = 0
        self._hwnd = None
        self._fatal = ""
        self._stopped = False
        self._wndproc = None          # 必须持有引用，否则回调被 GC
        self._hotkeys = {}            # hotkey_id -> combo
        self._nid: Optional[NOTIFYICONDATAW] = None
        self._tray_active = False
        self._icon_handle = None
        self._icon_from_file = False

    # ---------- 生命周期 ----------

    def start(self) -> None:  # noqa: D401 - 覆盖 Thread.start
        """启动宿主线程并等待窗口/消息队列就绪（最多 5 秒）。"""
        try:
            super().start()
        except Exception as exc:  # pragma: no cover - 防御
            self._fatal = f"事件线程启动失败: {exc}"
            _log.warning("%s", self._fatal)
            return
        if not self._ready.wait(5.0) and not self._fatal:
            self._fatal = "事件线程启动超时"

    def stop(self) -> None:
        """停止宿主线程（PostThreadMessageW(WM_QUIT)）并回收资源。"""
        self._stopped = True
        if not _IS_WINDOWS:
            return
        try:
            if threading.current_thread() is self:
                _user32.PostQuitMessage(0)
                return
            if self._thread_id and self.is_alive() and _post_thread_message is not None:
                _post_thread_message(self._thread_id, WM_QUIT, 0, 0)
        except Exception as exc:  # pragma: no cover - 防御
            _log.warning("stop: 投递 WM_QUIT 异常 %s", exc)
        if self.is_alive():
            self.join(timeout=3.0)

    def run(self) -> None:  # noqa: D102
        if not _IS_WINDOWS:
            self._fatal = "非 Windows 平台，Win32Host 不可用"
            _log.warning("%s", self._fatal)
            self._ready.set()
            return
        try:
            self._init_win32()
        except Exception as exc:
            self._fatal = f"事件线程初始化失败: {exc}"
            _log.warning("%s", self._fatal, exc_info=True)
            self._ready.set()
            return

        self._thread_id = int(_kernel32.GetCurrentThreadId())
        self._ready.set()
        try:
            self._message_loop()
        except Exception as exc:  # pragma: no cover - 防御
            _log.warning("消息循环异常: %s", exc, exc_info=True)
        finally:
            self._cleanup()

    # ---------- 宿主线程内部：初始化 / 消息循环 / 清理 ----------

    def _init_win32(self) -> None:
        hinst = _kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self._wnd_proc)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._wndproc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinst
        wc.hIcon = None
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = _WINDOW_CLASS
        if not _user32.RegisterClassW(ctypes.byref(wc)):
            err = _get_last_error()
            if err != ERROR_CLASS_ALREADY_EXISTS:
                raise OSError(f"RegisterClassW 失败 (err={err})")
        # 隐藏的顶层窗口：Shell_NotifyIconW 的宿主，不显示、不进任务栏
        self._hwnd = _user32.CreateWindowExW(
            0, _WINDOW_CLASS, _WINDOW_TITLE, 0, 0, 0, 0, 0, None, None, hinst, None)
        if not self._hwnd:
            raise OSError(f"CreateWindowExW 失败 (err={_get_last_error()})")

    def _message_loop(self) -> None:
        msg = wintypes.MSG()
        while True:
            rc = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if rc == 0 or rc == -1:  # WM_QUIT 或错误
                break
            # 线程消息（PostThreadMessageW）的 hWnd 为 NULL
            if not msg.hWnd and msg.message == WM_HOST_WAKE:
                self._drain_requests()
                continue
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

    def _drain_requests(self) -> None:
        while True:
            try:
                req = self._requests.get_nowait()
            except queue.Empty:
                return
            try:
                req.result = req.func(*req.args)
                req.ok = True
            except Exception as exc:  # pragma: no cover - 防御
                req.result = exc
                req.ok = False
            finally:
                req.event.set()

    def _cleanup(self) -> None:
        try:
            self._op_remove_tray()
        except Exception:
            pass
        for hotkey_id in list(self._hotkeys.keys()):
            try:
                self._op_unregister_hotkey(hotkey_id)
            except Exception:
                pass
        try:
            if self._hwnd:
                _user32.DestroyWindow(self._hwnd)
        except Exception:
            pass
        try:
            _user32.UnregisterClassW(_WINDOW_CLASS, _kernel32.GetModuleHandleW(None))
        except Exception:
            pass
        self._hwnd = None
        self._thread_id = 0

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_HOTKEY:
                callback = self._on_hotkey
                if callback is not None:
                    try:
                        callback(int(wparam))
                    except Exception as exc:  # pragma: no cover - 用户回调
                        _log.warning("on_hotkey 回调异常: %s", exc)
                return 0
            if msg == WM_TRAYICON:
                self._dispatch_tray(int(lparam))
                return 0
            if msg == WM_DESTROY:
                _user32.PostQuitMessage(0)
                return 0
        except Exception as exc:  # pragma: no cover - 防御
            _log.warning("窗口过程异常: %s", exc)
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _dispatch_tray(self, code: int) -> None:
        names = {
            WM_LBUTTONUP: "left_click",
            WM_LBUTTONDBLCLK: "double_click",
            WM_RBUTTONUP: "right_click",
            NIN_SELECT: "select",
            NIN_KEYSELECT: "key_select",
            NIN_BALLOONSHOW: "balloon_show",
            NIN_BALLOONHIDE: "balloon_hide",
            NIN_BALLOONTIMEOUT: "balloon_timeout",
            NIN_BALLOONUSERCLICK: "balloon_click",
        }
        event = names.get(code, f"tray_0x{code:04X}")
        callback = self._on_tray
        if callback is not None:
            try:
                callback(event)
            except Exception as exc:  # pragma: no cover - 用户回调
                _log.warning("on_tray 回调异常: %s", exc)

    # ---------- 跨线程编排 ----------

    def _invoke(self, func: Callable, *args, timeout: float = 5.0) -> Any:
        """把 ``func`` 编排到宿主线程执行并同步取回结果。"""
        if threading.current_thread() is self:
            return func(*args)
        if self._fatal:
            raise _HostUnavailable(self._fatal)
        if self._stopped or not self._ready.is_set() or not self._thread_id:
            raise _HostUnavailable("事件线程未运行")
        if _post_thread_message is None:
            raise _HostUnavailable("PostThreadMessageW 不可用")

        req = _Request(func, args)
        self._requests.put(req)
        if not _post_thread_message(self._thread_id, WM_HOST_WAKE, 0, 0):
            raise _HostUnavailable(f"唤醒事件线程失败 (err={_get_last_error()})")
        if not req.event.wait(timeout):
            raise _HostUnavailable("事件线程响应超时")
        if not req.ok:
            raise req.result if isinstance(req.result, BaseException) else _HostUnavailable(
                str(req.result))
        return req.result

    # ---------- 宿主线程内执行的操作 ----------

    def _op_register_hotkey(self, hotkey_id: int, mods: int, vk: int,
                            combo: str) -> Tuple[bool, str]:
        if hotkey_id in self._hotkeys:
            self._op_unregister_hotkey(hotkey_id)
        if not _user32.RegisterHotKey(self._hwnd, hotkey_id, mods | MOD_NOREPEAT, vk):
            err = _get_last_error()
            detail = f"热键注册失败（Win32 错误 {err}"
            if err == ERROR_HOTKEY_ALREADY_REGISTERED:
                detail += " = 该组合已被其他程序占用"
            detail += f"）: {combo}"
            return (False, detail)
        self._hotkeys[hotkey_id] = combo
        return (True, "")

    def _op_unregister_hotkey(self, hotkey_id: int) -> None:
        self._hotkeys.pop(hotkey_id, None)
        try:
            _user32.UnregisterHotKey(self._hwnd, hotkey_id)
        except Exception:
            pass

    def _load_icon(self, icon_path: Optional[str]):
        """加载 .ico；失败或未指定时退回系统默认图标 IDI_APPLICATION。"""
        handle = None
        from_file = False
        if icon_path:
            try:
                if os.path.exists(icon_path):
                    handle = _user32.LoadImageW(None, icon_path, IMAGE_ICON, 0, 0,
                                                LR_LOADFROMFILE | LR_DEFAULTSIZE)
                    from_file = bool(handle)
            except Exception as exc:
                _log.warning("LoadImageW 失败: %s", exc)
        if not handle:
            # MAKEINTRESOURCE(IDI_APPLICATION)：LPARAM 低位为资源 ID
            handle = _user32.LoadImageW(None, ctypes.c_wchar_p(IDI_APPLICATION),
                                        IMAGE_ICON, 0, 0, LR_SHARED | LR_DEFAULTSIZE)
            from_file = False
        self._icon_from_file = from_file
        return handle

    def _op_set_tray(self, icon_path: Optional[str], tooltip: str) -> bool:
        # 先记录旧图标状态，再加载新图标（_load_icon 会覆盖 self._icon_from_file）
        old_icon, old_from_file = self._icon_handle, self._icon_from_file
        nid = _new_nid()
        nid.hWnd = self._hwnd
        nid.uID = _TRAY_UID
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.szTip = (tooltip or "ipmg")[:127]
        icon = self._load_icon(icon_path)
        if not icon:
            _log.warning("set_tray: 图标句柄为空")
            return False
        nid.hIcon = icon

        action = NIM_MODIFY if self._tray_active else NIM_ADD
        if not _shell32.Shell_NotifyIconW(action, ctypes.byref(nid)):
            err = _get_last_error()
            if self._tray_active and not _shell32.Shell_NotifyIconW(NIM_ADD,
                                                                   ctypes.byref(nid)):
                _log.warning("set_tray: Shell_NotifyIconW 失败 (err=%s)", err)
                if self._icon_from_file and icon:
                    _user32.DestroyIcon(icon)
                return False
        self._nid = nid
        self._tray_active = True
        self._icon_handle = icon
        if old_icon and old_icon != icon and old_from_file:
            try:
                _user32.DestroyIcon(old_icon)
            except Exception:
                pass
        return True

    def _op_notify(self, title: str, text: str) -> bool:
        if not self._tray_active or self._nid is None:
            return False
        nid = self._nid
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_INFO
        nid.szInfo = (text or "")[:255]
        nid.szInfoTitle = (title or "ipmg")[:63]
        nid.dwInfoFlags = NIIF_INFO
        nid.uTimeoutOrVersion = 10000  # 毫秒（未启用 V4 版本时为 uTimeout）
        try:
            return bool(_shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))
        except Exception as exc:  # pragma: no cover - 防御
            _log.warning("notify 失败: %s", exc)
            return False

    def _op_remove_tray(self) -> None:
        if not self._tray_active or self._nid is None:
            return
        try:
            _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
        except Exception as exc:  # pragma: no cover - 防御
            _log.warning("remove_tray 失败: %s", exc)
        finally:
            if self._icon_handle and self._icon_from_file:
                try:
                    _user32.DestroyIcon(self._icon_handle)
                except Exception:
                    pass
            self._tray_active = False
            self._nid = None
            self._icon_handle = None
            self._icon_from_file = False

    # ---------- 公开 API ----------

    def register_hotkey(self, hotkey_id: int, combo: str) -> Tuple[bool, str]:
        """注册全局热键。返回 ``(ok, 错误说明)``；失败说明含 Win32 错误码。"""
        try:
            hotkey_id = int(hotkey_id)
        except (TypeError, ValueError):
            return (False, f"热键 id 非法: {hotkey_id!r}")
        if not 0 <= hotkey_id <= _MAX_HOTKEY_ID:
            return (False, f"热键 id 超出范围(0-{_MAX_HOTKEY_ID}): {hotkey_id}")

        parsed = parse_hotkey(combo)
        if parsed is None:
            return (False, f"热键格式无效: {combo!r}（需至少一个修饰键 + A-Z/0-9/F1-F24）")
        mods, vk = parsed
        try:
            return self._invoke(self._op_register_hotkey, hotkey_id, mods, vk, combo)
        except _HostUnavailable as exc:
            return (False, str(exc))
        except Exception as exc:  # pragma: no cover - 防御
            _log.warning("register_hotkey 异常: %s", exc)
            return (False, f"热键注册异常: {exc}")

    def unregister_hotkey(self, hotkey_id: int) -> None:
        """注销全局热键（幂等，失败静默）。"""
        try:
            self._invoke(self._op_unregister_hotkey, int(hotkey_id))
        except Exception:
            pass

    def set_tray(self, icon_path: Optional[str], tooltip: str) -> bool:
        """创建/更新托盘图标。``icon_path=None`` 时用系统默认图标。"""
        try:
            return bool(self._invoke(self._op_set_tray, icon_path, tooltip))
        except _HostUnavailable as exc:
            _log.warning("set_tray: %s", exc)
            return False
        except Exception as exc:  # pragma: no cover - 防御
            _log.warning("set_tray 异常: %s", exc)
            return False

    def remove_tray(self) -> None:
        """删除托盘图标（幂等，失败静默）。"""
        try:
            self._invoke(self._op_remove_tray)
        except Exception:
            pass

    def notify(self, title: str, text: str) -> bool:
        """弹出气泡通知（NIF_INFO）。无托盘图标时返回 False。"""
        try:
            return bool(self._invoke(self._op_notify, title, text))
        except _HostUnavailable as exc:
            _log.warning("notify: %s", exc)
            return False
        except Exception as exc:  # pragma: no cover - 防御
            _log.warning("notify 异常: %s", exc)
            return False

    def hide_console(self) -> bool:
        """
        隐藏控制台窗口。ConPTY（Windows Terminal）或无控制台时
        ``GetConsoleWindow()`` 为 0 → 返回 False，由上层降级；绝不抛异常。
        """
        if not _IS_WINDOWS:
            return False
        try:
            hwnd = _kernel32.GetConsoleWindow()
            if not hwnd:
                _log.debug("hide_console: 无控制台窗口（ConPTY/无控制台），返回 False")
                return False
            _user32.ShowWindow(hwnd, SW_HIDE)
            return not bool(_user32.IsWindowVisible(hwnd))
        except Exception as exc:
            _log.warning("hide_console 失败: %s", exc)
            return False

    def show_console(self) -> bool:
        """显示并尝试前置控制台窗口；返回是否可见（失败返回 False）。"""
        if not _IS_WINDOWS:
            return False
        try:
            hwnd = _kernel32.GetConsoleWindow()
            if not hwnd:
                _log.debug("show_console: 无控制台窗口（ConPTY/无控制台），返回 False")
                return False
            _user32.ShowWindow(hwnd, SW_SHOW)
            _user32.ShowWindow(hwnd, SW_RESTORE)
            visible = bool(_user32.IsWindowVisible(hwnd))
            self._force_foreground(hwnd)
            return visible
        except Exception as exc:
            _log.warning("show_console 失败: %s", exc)
            return False

    @staticmethod
    def _force_foreground(hwnd) -> bool:
        """SetForegroundWindow，失败时用 AttachThreadInput 兜底（尽力而为）。"""
        try:
            if _user32.SetForegroundWindow(hwnd):
                return True
            fg = _user32.GetForegroundWindow()
            cur_tid = int(_kernel32.GetCurrentThreadId())
            fg_tid = int(_user32.GetWindowThreadProcessId(fg, None)) if fg else 0
            if fg_tid and fg_tid != cur_tid:
                _user32.AttachThreadInput(fg_tid, cur_tid, True)
                try:
                    return bool(_user32.SetForegroundWindow(hwnd))
                finally:
                    _user32.AttachThreadInput(fg_tid, cur_tid, False)
        except Exception as exc:  # pragma: no cover - 尽力而为
            _log.debug("_force_foreground 失败: %s", exc)
        return False
