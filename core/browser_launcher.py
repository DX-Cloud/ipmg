"""
浏览器调用模块
提供快捷打开设备Web管理页面的能力
使用 os.startfile 调用 ShellExecuteW，兼容管理员权限进程
"""

import os
import ctypes
from typing import Optional

from core.network_utils import resolve_management_url
from utils.logger import log_operation, log_error


def open_management_page(device_config: dict) -> bool:
    """
    解析设备管理URL模板并调用默认浏览器打开。
    成功返回 True，失败返回 False。
    """
    url = resolve_management_url(device_config)

    if url is None:
        return False

    return open_url(url, device_config.get("name", ""))


def open_url(url: str, device_name: str = "") -> bool:
    """
    打开指定URL。
    使用 os.startfile (ShellExecuteW) 以兼容管理员权限进程。
    成功返回 True，失败返回 False。
    """
    try:
        os.startfile(url)
        log_operation("打开管理页面", device=device_name, result=f"URL={url}")
        return True
    except Exception as e:
        # 备用方案：使用 ctypes 直接调用 ShellExecuteW
        try:
            ctypes.windll.shell32.ShellExecuteW(
                None, "open", url, None, None, 1
            )
            log_operation("打开管理页面(备用)", device=device_name, result=f"URL={url}")
            return True
        except Exception as e2:
            log_error("打开管理页面", f"URL={url}, 错误={e}; 备用也失败: {e2}")
            return False
