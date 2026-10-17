"""
单实例互斥体与唤出信号（纯 ctypes，零第三方依赖）。

设计（对应常驻方案 §3.4）：
- 互斥体 ``Local\\ipmg.singleton``：判定"是否已有实例在运行"。
  ``Local\\`` 按登录会话隔离，符合"单桌面单实例"的场景。
- 事件 ``Local\\ipmg.activate``：第二实例用它通知常驻实例"唤出"。
  走 ``OpenEventW(EVENT_MODIFY_STATE) + SetEvent``：跨权限边界时 SetEvent
  不受 UIPI 限制（PostMessage 会被 UIPI 拦截），比窗口消息可靠。
- 非 Windows 或任何 API 失败：返回 False 并记 warning，**绝不抛异常**。
- 句柄由 OS 在进程退出时回收；``release`` 只做 CloseHandle。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Optional

_log = logging.getLogger("IPManager.singleton")

_IS_WINDOWS = sys.platform == "win32"

# ---- Win32 常量 ----
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102


def _get_last_error() -> int:
    """读取 ctypes 保存的 LastError（需配合 use_last_error=True 的 DLL 绑定）。"""
    try:
        getter = getattr(ctypes, "get_last_error", None) or getattr(ctypes, "GetLastError")
        return int(getter())
    except Exception:
        return 0


if _IS_WINDOWS:  # pragma: no cover - 平台分支
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.CreateMutexW.restype = wintypes.HANDLE
    _k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL,
                                  wintypes.LPCWSTR]
    _k32.CreateEventW.restype = wintypes.HANDLE
    _k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.OpenEventW.restype = wintypes.HANDLE
    _k32.SetEvent.argtypes = [wintypes.HANDLE]
    _k32.SetEvent.restype = wintypes.BOOL
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    _k32.ReleaseMutex.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
else:  # pragma: no cover - 平台分支
    _k32 = None

# ---- 进程内状态 ----
_mutex_handle: Optional[int] = None
_mutex_name: Optional[str] = None
_event_handle: Optional[int] = None
_event_name: Optional[str] = None


def mutex_name(app_name: str = "ipmg") -> str:
    """互斥体名（``Local\\<app_name>.singleton``）。"""
    return f"Local\\{app_name}.singleton"


def event_name(app_name: str = "ipmg") -> str:
    """唤出事件名（``Local\\<app_name>.activate``）。"""
    return f"Local\\{app_name}.activate"


def _close(handle) -> None:
    if handle:
        try:
            _k32.CloseHandle(handle)
        except Exception:
            pass


def acquire(app_name: str = "ipmg") -> bool:
    """
    创建并持有命名互斥体。

    返回 True = 本进程成为唯一实例；False = 已有实例在运行（或平台/API 不可用）。
    同一进程重复调用是幂等的。

    判定用"创建 + 立即尝试取得所有权"（``WaitForSingleObject(0)``），而不是只看
    ``CreateMutexW`` 的 ``ERROR_ALREADY_EXISTS``：后者只要系统里存在同名对象就
    认为"已有实例在运行"，哪怕那个对象根本没人持有（别的进程创建过、释放过却
    还留着句柄），会导致程序**永久拒绝启动**且难以自查。
    """
    global _mutex_handle, _mutex_name

    if _mutex_handle:
        return True
    if not _IS_WINDOWS or _k32 is None:
        _log.warning("acquire: 非 Windows 平台，单实例检查不可用")
        return False

    name = mutex_name(app_name)
    try:
        handle = _k32.CreateMutexW(None, False, name)   # 先不持有，稍后显式获取
        if not handle:
            _log.warning("acquire: CreateMutexW 失败 (err=%s)", _get_last_error())
            return False
        rc = _k32.WaitForSingleObject(handle, 0)
        if rc not in (WAIT_OBJECT_0, WAIT_ABANDONED):
            # WAIT_TIMEOUT = 另一个进程正持有 → 确实已有实例在运行
            _close(handle)
            return False
        if rc == WAIT_ABANDONED:
            _log.warning("acquire: 上一个实例异常退出（互斥体被放弃），本进程接管")
        _mutex_handle, _mutex_name = handle, name
        return True
    except Exception as exc:  # pragma: no cover - 防御
        _log.warning("acquire: 异常 %s", exc)
        return False


def signal_existing(app_name: str = "ipmg") -> bool:
    """
    第二实例调用：通知既有实例"唤出"。

    使用 OpenEventW(EVENT_MODIFY_STATE) + SetEvent。事件不存在时返回 False。
    """
    if not _IS_WINDOWS or _k32 is None:
        _log.warning("signal_existing: 非 Windows 平台，无法发送唤出信号")
        return False

    name = event_name(app_name)
    try:
        handle = _k32.OpenEventW(EVENT_MODIFY_STATE, False, name)
        if not handle:
            _log.warning("signal_existing: OpenEventW 失败 (err=%s, name=%s)",
                         _get_last_error(), name)
            return False
        try:
            return bool(_k32.SetEvent(handle))
        finally:
            _close(handle)
    except Exception as exc:  # pragma: no cover - 防御
        _log.warning("signal_existing: 异常 %s", exc)
        return False


def wait_activate(app_name: str, timeout_ms: int) -> bool:
    """
    常驻实例等待唤出信号。

    事件为自动重置（auto-reset）：一次 SetEvent 只唤醒一次等待；未被消费的信号
    会保持 signaled，不会丢失。返回 True = 收到信号，False = 超时/不可用。
    """
    global _event_handle, _event_name

    if not _IS_WINDOWS or _k32 is None:
        _log.warning("wait_activate: 非 Windows 平台，无法等待唤出信号")
        return False

    name = event_name(app_name)
    try:
        if _event_handle is None or _event_name != name:
            handle = _k32.CreateEventW(None, False, False, name)
            if not handle:
                _log.warning("wait_activate: CreateEventW 失败 (err=%s, name=%s)",
                             _get_last_error(), name)
                return False
            _close(_event_handle)
            _event_handle, _event_name = handle, name
        rc = _k32.WaitForSingleObject(_event_handle, max(0, int(timeout_ms)))
        return rc == WAIT_OBJECT_0
    except Exception as exc:  # pragma: no cover - 防御
        _log.warning("wait_activate: 异常 %s", exc)
        return False


def release(app_name: str = "ipmg") -> None:
    """释放并关闭本进程持有的互斥体/事件句柄（OS 亦会在进程退出时回收）。"""
    global _mutex_handle, _mutex_name, _event_handle, _event_name

    if _mutex_handle:
        try:
            _k32.ReleaseMutex(_mutex_handle)   # 先归还所有权，再关句柄
        except Exception:  # pragma: no cover - 防御
            pass
    _close(_mutex_handle)
    _close(_event_handle)
    _mutex_handle = None
    _mutex_name = None
    _event_handle = None
    _event_name = None
