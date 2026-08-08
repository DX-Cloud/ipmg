"""
配置读写（复用 core.config_manager）
打开时严格校验，损坏/结构非法返回错误而非默认配置；保存走原子写入+备份。
"""

import os

import yaml

from core import config_manager as cm


def load(path=None):
    """
    加载配置。返回 (config, error)。
    文件不存在、YAML 损坏或结构非法时返回错误信息（不返回默认配置）。
    """
    path = path or cm.CONFIG_FILE
    if not os.path.exists(path):
        return None, f"文件不存在: {path}"

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return None, f"配置文件解析失败: {e}"
    except OSError as e:
        return None, f"无法读取文件: {e}"

    if not isinstance(raw, dict):
        return None, "配置文件顶层必须是映射（字典）"
    devices = raw.get("devices", [])
    if not isinstance(devices, list) or any(not isinstance(d, dict) for d in devices):
        return None, "配置格式无效：devices 必须是字典列表"

    try:
        return cm.load_config(path), ""
    except Exception as e:
        return None, f"加载失败: {e}"


def save(path: str, config: dict) -> str:
    """原子保存配置。返回错误信息，空串表示成功。"""
    try:
        cm.save_config(config, path)
        return ""
    except Exception as e:
        return f"保存失败: {e}"
