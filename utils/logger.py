"""
操作日志记录模块
使用 Python logging 模块，日志写入 logs/ 目录，按日期滚动
"""

import os
import sys
import logging
from datetime import datetime
from typing import Optional

# 日志目录：统一存放在 %USERPROFILE%\ipmg\logs\
LOG_DIR = os.path.join(os.path.expanduser("~"), "ipmg", "logs")

# 全局日志器
_logger: Optional[logging.Logger] = None


def _ensure_log_dir():
    """确保日志目录存在"""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)


def _get_logger() -> logging.Logger:
    """获取或初始化日志器"""
    global _logger

    if _logger is not None:
        return _logger

    try:
        _ensure_log_dir()

        _logger = logging.getLogger("IPManager")
        _logger.setLevel(logging.DEBUG)

        # 文件处理器 - 按日期命名
        log_file = os.path.join(LOG_DIR, f"ipmanager_{datetime.now().strftime('%Y%m%d')}.log")
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)

        # 日志格式
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)-8s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(formatter)

        _logger.addHandler(file_handler)

    except Exception:
        # 日志初始化失败静默处理，不影响主流程
        _logger = logging.getLogger("IPManager")
        _logger.addHandler(logging.NullHandler())

    return _logger


def log_operation(action: str, adapter: str = "", device: str = "", result: str = ""):
    """
    记录操作日志。
    :param action: 操作类型（如 "配置IP", "恢复IP", "添加设备" 等）
    :param adapter: 涉及的网卡名称
    :param device: 涉及的设备名称
    :param result: 操作结果（如 "成功", "失败: xxx"）
    """
    try:
        logger = _get_logger()
        parts = [f"[{action}]"]
        if adapter:
            parts.append(f"网卡={adapter}")
        if device:
            parts.append(f"设备={device}")
        if result:
            parts.append(f"结果={result}")
        logger.info(" | ".join(parts))
    except Exception:
        pass  # 日志写入失败静默处理


def log_error(action: str, error_msg: str):
    """记录错误日志"""
    try:
        logger = _get_logger()
        logger.error(f"[{action}] 错误: {error_msg}")
    except Exception:
        pass


def log_info(message: str):
    """记录信息日志"""
    try:
        logger = _get_logger()
        logger.info(message)
    except Exception:
        pass
