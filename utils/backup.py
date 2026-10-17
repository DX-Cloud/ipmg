"""
网卡IP备份与恢复模块
在修改网卡IP前备份原始配置到配置文件中，支持一键恢复
"""

from typing import Optional, Dict, Any

from core.config_manager import (
    get_adapter_backup, save_adapter_backup, clear_adapter_backup, add_ip_history,
    pop_adapter_backup, backup_stack_depth
)
from core.ip_configurator import get_current_ip_config, set_static_ip, set_dhcp


def full_ip_fields(current_config: Optional[dict]) -> Dict[str, list]:
    """
    从 get_current_ip_config 的结果中整理出"完整 IP 集合"字段。
    返回 {"ips": [...], "masks": [...], "gateways": [...]}，索引 0 为主 IP。
    缺少新字段时回退到旧的单值 ip/mask/gateway（只增字段，不改旧字段语义）。
    """
    current_config = current_config or {}
    ip = current_config.get("ip") or ""
    mask = current_config.get("mask") or ""
    gateway = current_config.get("gateway") or ""

    ips = [a for a in (current_config.get("ips") or []) if a]
    masks = list(current_config.get("masks") or [])
    gateways = [g for g in (current_config.get("gateways") or []) if g]

    if not ips and ip:
        ips, masks = [ip], [mask]
    if not gateways and gateway:
        gateways = [gateway]

    return {"ips": ips, "masks": masks, "gateways": gateways}


def static_restore_args(entry: Optional[dict]) -> Dict[str, Any]:
    """
    从备份条目/历史记录中提取 set_static_ip 的调用参数（向后兼容）。

    - 新格式条目（带 ips/masks/gateways）：还原全部主 IP + 次要 IP；
    - 旧格式条目（只有 ip/mask/gateway 单值）：ips/masks/gateways 返回 None，
      set_static_ip 回退到原有的单值行为，恢复结果与修复前完全一致。
    """
    entry = entry or {}
    ips = [a for a in (entry.get("ips") or []) if a]
    masks = list(entry.get("masks") or [])
    gateways = [g for g in (entry.get("gateways") or []) if g]
    return {
        "ip": entry.get("ip", "") or "",
        "mask": entry.get("mask", "255.255.255.0") or "255.255.255.0",
        "gateway": entry.get("gateway", "") or None,
        "ips": ips or None,
        "masks": masks or None,
        "gateways": gateways or None,
    }


def backup_adapter_config(adapter_name: str, adapter_mac: str, config: dict) -> bool:
    """
    备份指定网卡的当前IP配置。
    将IP、掩码、网关、是否DHCP等信息保存到配置文件。
    同时将静态IP配置添加到历史记录（用于历史恢复功能）。
    网卡上配置了多个 IPv4 时，全部地址/掩码/网关都会写入
    ips/masks/gateways 字段（索引 0 为主 IP），恢复时可一次性还原。
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
        # 新增字段：完整地址/掩码/网关列表（旧字段保持原语义 = 第 0 项）
        backup.update(full_ip_fields(current_config))

        save_adapter_backup(config, adapter_mac, backup)

        # 添加到历史记录（仅静态IP）
        if not current_config.get("is_dhcp", True):
            full = full_ip_fields(current_config)
            add_ip_history(
                config,
                adapter_mac,
                current_config.get("ip", ""),
                current_config.get("mask", "255.255.255.0"),
                current_config.get("gateway", ""),
                is_dhcp=False,
                ips=full["ips"],
                masks=full["masks"],
                gateways=full["gateways"],
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
            # 原来是静态IP，恢复静态IP（新格式条目会一次性还原全部主/次要 IP）
            try:
                success = set_static_ip(adapter_name, **static_restore_args(backup))
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


def undo_adapter_config(adapter_name: str, adapter_mac: str, config: dict) -> tuple:
    """
    撤销一次IP配置：弹出栈顶备份并恢复到该状态。
    返回 (success: bool, backup: dict or None, error_msg: str)。
    success=True 表示撤销成功，backup 为已应用的备份记录。
    """
    try:
        backup, config = pop_adapter_backup(config, adapter_mac)
        if backup is None:
            return (False, None, "无可撤销的备份记录")

        if backup.get("is_dhcp", True):
            success = set_dhcp(adapter_name)
            if not success:
                return (False, backup, f"撤销DHCP失败 (网卡: {adapter_name})")
        else:
            try:
                success = set_static_ip(adapter_name, **static_restore_args(backup))
            except RuntimeError as e:
                return (False, backup, str(e))
            if not success:
                return (False, backup, f"撤销静态IP失败 (网卡: {adapter_name})")

        return (True, backup, "")

    except RuntimeError as e:
        return (False, None, str(e))
    except Exception as e:
        return (False, None, f"撤销失败: {e}")


def undo_stack_depth(adapter_mac: str, config: dict) -> int:
    """获取可撤销的备份数量"""
    return backup_stack_depth(config, adapter_mac)
