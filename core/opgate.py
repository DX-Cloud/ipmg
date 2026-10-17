"""
操作闸门（Operation Gate）
=========================
串行化所有"对网卡有副作用"的操作，避免多个执行体同时改同一块网卡：

- 前台 TUI 线程：配置 IP / 恢复 IP 整段持锁
- 常驻态的后台热键线程：快捷键应用配置时以 timeout=0 抢锁，抢不到就提示
  "当前有操作进行中"，绝不排队等待（否则用户连按热键会堆积操作）

设计取舍：
- 用 RLock 而不是 Lock：同一线程内可能嵌套调用（例如配置流程内部再取一次），
  可重入更安全；跨线程依然是互斥的。
- busy/busy_reason 自己维护标志位而不是探测锁的内部状态，避免依赖私有 API。
"""

import contextlib
import threading
from typing import Iterator, Optional


class OperationBusy(RuntimeError):
    """抢不到操作闸门时抛出。"""


class OperationGate:
    """进程内操作闸门（可重入，跨线程互斥）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._depth = 0          # 递归深度：同一线程可重入，只有归零才算真正释放
        self._reason: Optional[str] = None
        self._owner: Optional[str] = None

    # ---------------- 基本接口 ----------------
    def acquire(self, reason: str = "", timeout: float = 0.0) -> bool:
        """
        尝试取得闸门。

        timeout <= 0 表示"只试一次，绝不等待"；否则最多等待 timeout 秒。
        成功返回 True，失败返回 False（不抛异常，方便热键路径直接判断）。
        """
        if timeout and timeout > 0:
            ok = self._lock.acquire(timeout=timeout)
        else:
            ok = self._lock.acquire(blocking=False)
        if ok:
            self._depth += 1
            self._reason = reason or None
            self._owner = threading.current_thread().name
        return ok

    def release(self) -> None:
        """释放一层闸门（未持有时安全忽略）。"""
        if self._depth <= 0:
            return
        self._depth -= 1
        if self._depth == 0:
            self._reason = None
            self._owner = None
        try:
            self._lock.release()
        except RuntimeError:
            # 非持有线程误调用：忽略，避免把清理路径变成崩溃点
            pass

    @contextlib.contextmanager
    def hold(self, reason: str = "", timeout: float = 0.0) -> Iterator[None]:
        """
        上下文管理器：with gate.hold("配置IP"):
        抢不到时抛 OperationBusy（调用方自行决定是提示还是降级）。
        """
        if not self.acquire(reason, timeout=timeout):
            raise OperationBusy(self.busy_reason or "当前有操作进行中")
        try:
            yield
        finally:
            self.release()

    # ---------------- 状态查询 ----------------
    @property
    def busy(self) -> bool:
        """当前是否有操作在跑（含本线程持有）。"""
        return self._depth > 0

    @property
    def busy_reason(self) -> str:
        """正在进行的操作名称，空闲时为空串。"""
        if self._depth <= 0:
            return ""
        if self._reason:
            return f"当前有操作进行中（{self._reason}）"
        return "当前有操作进行中"

    @property
    def owner(self) -> str:
        """持有者线程名，空闲时为空串。"""
        return self._owner or ""


# 进程内单例：TUI、热键线程、未来的 CLU/自动化入口共用同一个闸门
gate = OperationGate()
