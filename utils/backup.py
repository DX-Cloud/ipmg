"""
网卡IP备份与恢复模块
在修改网卡IP前备份原始配置到配置文件中，支持一键恢复
"""

from typing import Optional, Dict, Any

from core.config_manager import (
    get_adapter_backup, save_adapter_backup, clear_adapter_backup, add_ip_history
)
from core.ip_configurator import get_current_ip_config, set_static_ip, set_dhcp


def backup_adapter_config(adapter_name: str, adapter_mac: str, config: dict) -> bool:
    """
    备份指定网卡的当前IP配置。
    将IP、掩码、网关、是否DHCP等信息保存到配置文件。
    同时将静态IP配置添加到历史记录（用于历史恢复功能）。
    成功返回 True，失败返回 False。
    """
    try:
        current_config = get_current_ip_config(adapter_name)
        if current_config is None:
            return False

        backup = {
            "ip": current_config.get("ip", ""),
            "mask": current_config.get("mask", ""),
            "gateway": current_config.get("gateway", ""),
            "is_dhcp": current_config.get("is_dhcp", True),
            "adapter_name": adapter_name,
        }

        save_adapter_backup(config, adapter_mac, backup)

        # 添加到历史记录（仅静态IP）
        if not current_config.get("is_dhcp", True):
            add_ip_history(
                config,
                adapter_mac,
                current_config.get("ip", ""),
                current_config.get("mask", "255.255.255.0"),
                current_config.get("gateway", ""),
                is_dhcp=False
            )

        return True

    except Exception:
        return False


def restore_adapter_config(adapter_name: str, adapter_mac: str, config: dict) -> tuple:
    """
    恢复网卡到上次备份的IP配置。
    返回 (success: bool, error_msg: str)，成功时 error_msg 为空字符串。
    """
    try:
        backup = get_adapter_backup(config, adapter_mac)
        if backup is None:
            return (False, "未找到备份记录")

        if backup.get("is_dhcp", True):
            # 原来是DHCP，恢复DHCP
            success = set_dhcp(adapter_name)
            if not success:
                return (False, f"恢复DHCP失败 (网卡: {adapter_name})")
        else:
            # 原来是静态IP，恢复静态IP
            try:
                success = set_static_ip(
                    adapter_name,
                    backup.get("ip", ""),
                    backup.get("mask", "255.255.255.0"),
                    backup.get("gateway", "") or None,
                )
            except RuntimeError as e:
                return (False, str(e))
            if not success:
                return (False, f"恢复静态IP失败 (网卡: {adapter_name})")

        return (True, "")

    except RuntimeError as e:
        return (False, str(e))
    except Exception as e:
        return (False, f"恢复失败: {e}")


def has_backup(adapter_mac: str, config: dict) -> bool:
    """检查指定MAC的网卡是否存在备份"""
    return get_adapter_backup(config, adapter_mac) is not None
