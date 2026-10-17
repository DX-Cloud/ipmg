"""
网卡IP备份与恢复模块

- ``backup_adapter_config``：改网卡前把原配置存进配置文件（供撤销/历史恢复）
- ``full_ip_fields`` / ``static_restore_args``：新旧备份格式之间的转换工具

注意：真正的"恢复/撤销"执行入口是 ``core.operations``（restore_to_dhcp /
undo_to_backup / history_restore），它们由调用方持有操作闸门。本模块不再提供
直接写网卡的恢复函数，避免绕过闸门。
"""

from typing import Optional, Dict, Any

from core.config_manager import save_adapter_backup, add_ip_history
from core.ip_configurator import get_current_ip_config
from utils.logger import log_error, log_exception


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
            # 读不到当前配置：把原因写进日志，否则上层只能看到"备份失败"这种无信息量的提示
            log_error(
                "备份网卡IP",
                f"网卡 {adapter_name!r} 当前 IP 配置读取失败（WMI 查询返回空）",
            )
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

    except Exception as e:
        # 以前这里静默返回 False，线上只看到"备份网卡IP失败"，无法定位；
        # 现在把异常与堆栈写进日志（返回值语义不变）。
        log_error("备份网卡IP", f"网卡 {adapter_name!r} 备份异常: {type(e).__name__}: {e}")
        log_exception("备份网卡IP", f"网卡 {adapter_name!r}")
        return False
