# -*- coding: utf-8 -*-
"""
开机自启（Windows 计划任务）。

为什么用计划任务而不是 HKCU\\...\\Run
------------------------------------
改网卡 IP 需要管理员权限。``HKCU\\Run`` 的自启项以普通权限启动，用户每次登录
都会被 UAC 弹窗拦截（或被取消后残留一个"能开不能改 IP"的半残进程）。计划任务
用 ``/RL HIGHEST`` 在登录时**静默以最高权限**拉起，只在"创建/删除"时需要一次
管理员权限。

命令模板::

    schtasks /Create /F /TN "ipmg-resident" /SC ONLOGON /RL HIGHEST /IT
             /DELAY 0000:15 /TR "<带引号的命令行>"

- ``/SC ONLOGON``：用户登录时触发；
- ``/RL HIGHEST``：最高权限运行（免 UAC）；
- ``/IT``：只在交互式会话运行 —— 托盘图标与全局热键必须有桌面会话；
- ``/DELAY mmmm:ss``：登录后延迟（默认 15 秒），错开登录高峰；
- ``/TR`` 的值是"任务以后要执行的命令行"，其中 exe 路径必须自带引号。

关于 ``/TR`` 的引号规则
-----------------------
``launch_command()`` 返回的字符串**本身**就带引号（如
``"C:\\Program Files\\ipmg\\ipmg.exe" --resident --autostart-min``）。调用
``schtasks`` 时把这个字符串作为**单个 argv 元素**交给 ``subprocess.run``（列表
参数 + ``shell=False``），由 ``CreateProcess`` 负责外层转义；**不要**再手工拼
反斜杠或额外引号，否则路径会被二次转义。

所有外部调用都：列表参数、``shell=False``、``capture_output=True``、超时保护、
``CREATE_NO_WINDOW``（不闪黑框）；异常一律降级为 ``(False, 原因)``，绝不向上抛。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

_log = logging.getLogger("IPManager.autostart")

#: 计划任务名（创建/查询/删除共用）
TASK_NAME = "ipmg-resident"

#: 常驻模式附加参数（顺序固定，便于测试与文档引用）
RESIDENT_ARGS = ("--resident", "--autostart-min")

#: schtasks 可执行文件名（靠 PATH 解析；is_supported() 用 shutil.which 探测）
SCHTASKS = "schtasks"

#: 单次 schtasks 调用超时（秒）
SCHTASKS_TIMEOUT = 15

#: 不弹出控制台窗口（仅 Windows 有效；POSIX 上必须为 0）
CREATE_NO_WINDOW = 0x08000000

#: 不支持时的统一说明
UNSUPPORTED_MESSAGE = "当前系统不支持计划任务"


# ============================== 内部工具 ==============================


def _is_windows() -> bool:
    return sys.platform == "win32"


def _format_delay(delay_seconds: int) -> str:
    """把秒数格式化为 schtasks 的 ``mmmm:ss``（例：15 → ``0000:15``）。"""
    try:
        seconds = int(delay_seconds)
    except (TypeError, ValueError):
        seconds = 0
    seconds = max(0, min(seconds, 9999 * 60 + 59))
    return f"{seconds // 60:04d}:{seconds % 60:02d}"


def _run_schtasks(args: List[str]) -> "subprocess.CompletedProcess":
    """执行一条 schtasks 命令。异常原样抛出，由调用方转成 (False, 原因)。"""
    creationflags = CREATE_NO_WINDOW if _is_windows() else 0
    return subprocess.run(
        [SCHTASKS] + list(args),
        shell=False,
        capture_output=True,
        text=True,
        creationflags=creationflags,
        timeout=SCHTASKS_TIMEOUT,
    )


def _output_text(proc: "subprocess.CompletedProcess") -> str:
    """拼接 stdout/stderr（兼容 bytes/str），压成单行并截断。"""
    chunks = []
    for value in (getattr(proc, "stderr", None), getattr(proc, "stdout", None)):
        if not value:
            continue
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        text = " ".join(str(value).split())
        if text:
            chunks.append(text)
    return " | ".join(chunks)[:300]


def _failure_message(proc: "subprocess.CompletedProcess") -> str:
    """把一次失败的 schtasks 调用转成给用户看的错误说明。"""
    code = getattr(proc, "returncode", None)
    detail = _output_text(proc)
    if detail:
        return f"{detail}（schtasks 退出码 {code}）"
    return f"schtasks 执行失败（退出码 {code}）"


# ============================== 对外接口 ==============================


def launch_command() -> str:
    """
    当前运行方式对应的自启命令行（含 exe/python 路径引号）。

    - 冻结（PyInstaller）: ``"<exe>" --resident --autostart-min``
    - 源码运行:           ``"<python.exe>" "<abs main.py>" --resident --autostart-min``
    """
    if getattr(sys, "frozen", False):
        exe = sys.executable or sys.argv[0]
        return f'"{exe}" ' + " ".join(RESIDENT_ARGS)

    python_exe = sys.executable or "python"
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main_py = os.path.join(repo_root, "main.py")
    return f'"{python_exe}" "{main_py}" ' + " ".join(RESIDENT_ARGS)


def is_supported() -> bool:
    """Windows 且本机存在 schtasks 时返回 True。"""
    try:
        if not _is_windows():
            return False
        return shutil.which(SCHTASKS) is not None
    except Exception:  # pragma: no cover - 防御
        return False


def _query_task_exists() -> Tuple[bool, Optional[str]]:
    """
    查询计划任务是否存在：只认 ``schtasks`` 的**退出码**（0=存在），
    绝不解析中英文输出。返回 ``(exists, error)``，error 仅在调用异常时非空。
    """
    try:
        proc = _run_schtasks(["/Query", "/TN", TASK_NAME])
    except subprocess.TimeoutExpired:
        return (False, f"查询计划任务超时（>{SCHTASKS_TIMEOUT}s）")
    except FileNotFoundError:
        return (False, "未找到 schtasks 命令")
    except Exception as exc:  # 权限/编码/系统错误等
        return (False, f"查询计划任务失败: {exc}")
    # 非 0 一律视为"不存在"：schtasks 对"任务不存在"与查询失败都可能返回 1，
    # 解析输出不可靠且受系统语言影响（方案要求只认退出码）。
    return (getattr(proc, "returncode", 1) == 0, None)


def task_exists() -> bool:
    """计划任务是否存在（退出码判定；异常/不支持时返回 False）。"""
    if not is_supported():
        return False
    exists, _error = _query_task_exists()
    return exists


def enable(delay_seconds: int = 15) -> Tuple[bool, str]:
    """
    创建/覆盖自启计划任务。返回 ``(ok, 说明)``。

    :param delay_seconds: 登录后延迟秒数（默认 15），转成 ``mmmm:ss``
    """
    if not is_supported():
        return (False, UNSUPPORTED_MESSAGE)

    delay = _format_delay(delay_seconds)
    args = [
        "/Create",
        "/F",                       # 已存在则覆盖，天然幂等
        "/TN", TASK_NAME,
        "/SC", "ONLOGON",
        "/RL", "HIGHEST",           # 最高权限运行 → 免 UAC
        "/IT",                      # 交互式会话（托盘/热键必需）
        "/DELAY", delay,
        "/TR", launch_command(),    # 单个 argv 元素，内部引号已就绪
    ]
    try:
        proc = _run_schtasks(args)
    except subprocess.TimeoutExpired:
        _log.warning("enable: schtasks 超时（>%ss）", SCHTASKS_TIMEOUT)
        return (False, f"创建计划任务超时（>{SCHTASKS_TIMEOUT}s）")
    except Exception as exc:
        _log.warning("enable: 调用 schtasks 异常: %s", exc)
        return (False, f"创建计划任务失败: {exc}")

    if getattr(proc, "returncode", 1) != 0:
        message = _failure_message(proc)
        _log.warning("enable: 创建失败: %s", message)
        return (False, message)
    return (True, f"已创建计划任务 {TASK_NAME}（登录后 {max(0, int(delay_seconds or 0))} 秒以最高权限启动）")


def disable() -> Tuple[bool, str]:
    """
    删除自启计划任务。返回 ``(ok, 说明)``；任务本来就不存在时也视为成功（幂等）。
    """
    if not is_supported():
        return (False, UNSUPPORTED_MESSAGE)

    try:
        proc = _run_schtasks(["/Delete", "/F", "/TN", TASK_NAME])
    except subprocess.TimeoutExpired:
        _log.warning("disable: schtasks 超时（>%ss）", SCHTASKS_TIMEOUT)
        return (False, f"删除计划任务超时（>{SCHTASKS_TIMEOUT}s）")
    except Exception as exc:
        _log.warning("disable: 调用 schtasks 异常: %s", exc)
        return (False, f"删除计划任务失败: {exc}")

    if getattr(proc, "returncode", 1) == 0:
        return (True, f"已删除计划任务 {TASK_NAME}，开机自启已关闭")

    # 删除失败：再查一次区分"本来就不存在"（幂等成功）与真实失败（如拒绝访问）
    exists, _error = _query_task_exists()
    if not exists:
        return (True, f"计划任务 {TASK_NAME} 不存在，开机自启已处于关闭状态")
    message = _failure_message(proc)
    _log.warning("disable: 删除失败: %s", message)
    return (False, message)


def status() -> Dict[str, object]:
    """
    自启状态汇总。

    返回 ``{"supported": bool, "exists": bool, "command": str, "error": str}``：
    command 是当前运行方式对应的自启命令行（尚未注册时也展示，便于设置页预览）。
    """
    result: Dict[str, object] = {
        "supported": False,
        "exists": False,
        "command": "",
        "error": "",
    }
    if not is_supported():
        result["error"] = UNSUPPORTED_MESSAGE
        return result

    result["supported"] = True
    result["command"] = launch_command()
    exists, error = _query_task_exists()
    result["exists"] = exists
    result["error"] = error or ""
    return result
