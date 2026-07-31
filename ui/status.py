"""
轻量状态模块
在 TUI 各页面之间传递"上次操作结果"，用于标题栏展示，
避免成功路径强制等待用户按键导致流程冗长。
"""

_last_result: str = ""


def set_last_result(text: str) -> None:
    global _last_result
    _last_result = text


def get_last_result() -> str:
    return _last_result
