"""
控制台底层能力
==============
TUI 原本阻塞在 ``msvcrt.getwch()`` / ``input()`` 上，一旦阻塞就无法响应任何
外部事件（常驻态的全局热键、窗口唤出、后台任务结果）。本模块提供：

1. **可打断的按键等待** ``wait_key(timeout_ms)``
   - ``timeout_ms=None``：阻塞等待，但可被 :func:`wake` 打断（返回 None）
   - ``timeout_ms>=0``：最多等待指定毫秒，超时返回 None
   - 终端尺寸变化时立即返回 None，让上层重绘（避免 resize 后画面错位）

2. **跨线程唤醒** :func:`wake`
   后台线程（如热键宿主线程）需要主线程立刻重绘/处理时调用，不直接操作界面。

3. **跨线程事件队列** :func:`post_event` / :func:`drain_events`
   rich Console 是模块级单例，后台线程直接 print 会撕裂自绘画面，
   因此后台只投递事件，由主线程统一渲染。

按键名与 ``ui.widgets._read_key`` 完全一致：``up/down/left/right/enter/escape/backspace``
或单个字符。
"""

import queue
import sys
import threading
import time
from typing import List, Optional

# 事件名（跨线程投递用）
EVENT_REDRAW = "redraw"        # 请求重绘当前帧
EVENT_SUMMON = "summon"        # 请求把窗口带到前台
EVENT_RESULT = "result"        # 后台操作有结果待汇报
EVENT_RESIZE = "resize"        # 终端尺寸变化，需要重绘

# 轮询间隔：20ms 足够跟手，又不会把 CPU 拉满
POLL_INTERVAL_S = 0.02

_events: "queue.Queue[str]" = queue.Queue()
_wake_flag = threading.Event()
_last_size: Optional[tuple] = None


# ---------------------------------------------------------------- 事件队列
def post_event(name: str) -> None:
    """后台线程投递一个事件（线程安全）。"""
    _events.put(name)


def drain_events() -> List[str]:
    """取出当前所有待处理事件（主线程调用）。"""
    out: List[str] = []
    while True:
        try:
            out.append(_events.get_nowait())
        except queue.Empty:
            break
    return out


def has_events() -> bool:
    return not _events.empty()


# ---------------------------------------------------------------- 唤醒
def wake() -> None:
    """
    打断正在等待的 :func:`wait_key`（线程安全）。
    用于"热键唤出/后台结果就绪"这类需要主线程立刻反应的场景。
    """
    _wake_flag.set()


# ---------------------------------------------------------------- 底层能力
def vt_enabled() -> bool:
    """确认（并尝试启用）控制台 VT 处理，ANSI 原地刷新依赖它。"""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if not (mode.value & 0x0004):  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        return bool(mode.value & 0x0004)
    except Exception:
        return False


def keyboard_available() -> bool:
    """键盘导航仅用于 Windows 交互式终端，且要求 VT 可用。"""
    if sys.platform != "win32":
        return False
    try:
        return bool(sys.stdin.isatty()) and vt_enabled()
    except Exception:
        return False


def read_key(msvcrt_module) -> str:
    """阻塞读一个按键，方向键归一化为 up/down/left/right（与原实现一致）。"""
    ch = msvcrt_module.getwch()
    if ch in ("\x00", "\xe0"):
        second = msvcrt_module.getwch()
        return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(second, second)
    if ch == "\r":
        return "enter"
    if ch == "\x1b":
        return "escape"
    if ch == "\x08":
        return "backspace"
    return ch


def terminal_size() -> Optional[tuple]:
    """当前终端 (行, 列)；取不到返回 None。"""
    try:
        size = __import__("os").get_terminal_size()
        return (size.lines, size.columns)
    except Exception:
        return None


def _size_changed() -> bool:
    """终端尺寸是否自上次检查后变化（变化即需要重绘）。"""
    global _last_size
    current = terminal_size()
    if current is None:
        return False
    changed = _last_size is not None and current != _last_size
    _last_size = current
    return changed


def reset_size_baseline() -> None:
    """重绘完成后调用，重新记录尺寸基线。"""
    global _last_size
    _last_size = terminal_size()


# ---------------------------------------------------------------- 可打断等待
def wait_key(timeout_ms: Optional[int] = None, force: bool = False) -> Optional[str]:
    """
    等待一个按键。

    - ``timeout_ms is None``：一直等，直到有按键或 :func:`wake` 被调用（返回 None）
    - ``timeout_ms >= 0``：最多等这么久；超时、被唤醒、或终端尺寸变化都返回 None
    - ``force=True``：跳过"是否交互式终端"的判定，直接按键盘模式轮询。
      调用方（如 ui/widgets）已经通过自己的 :func:`keyboard_available` 判定进入
      键盘模式，这里再判定一次会导致测试打桩失效，故由调用方决定。

    返回值要么是按键名（见模块说明），要么是 None（表示"没有按键，请检查事件与重绘"）。
    尺寸变化时会先投递 EVENT_RESIZE 事件再返回 None，上层据此决定是否重绘——
    这样空闲时（没有按键、没有事件）上层可以完全不重绘，避免常驻态下每 200ms 白画一帧。
    非 Windows / 非交互式终端且 ``force=False`` 时退化为阻塞 ``input()``（保持既有行为）。
    """
    if not force and not keyboard_available():
        line = input()
        if not line:
            return "enter"
        return line[0]

    import msvcrt

    if _size_changed():
        post_event(EVENT_RESIZE)
        return None

    start = time.monotonic()
    while True:
        if _wake_flag.is_set():
            _wake_flag.clear()
            return None
        try:
            if msvcrt.kbhit():
                return read_key(msvcrt)
        except Exception:
            # 控制台被关闭/重定向等：交给上层按非交互处理
            return None
        if timeout_ms is not None and (time.monotonic() - start) * 1000.0 >= timeout_ms:
            return None
        if _size_changed():
            post_event(EVENT_RESIZE)
            return None
        time.sleep(POLL_INTERVAL_S)
