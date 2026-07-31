"""
业务操作层
配置/恢复的核心步骤与 TUI 展示解耦，供界面、CLI、自动化测试复用。
所有操作返回 (ok, message, config) 或直接抛出 RuntimeError（网络层失败）。
"""

import subprocess
from typing import Any, Dict, Optional, Tuple

from core import config_manager as cm
from core.ip_configurator import set_static_ip, set_dhcp
from core.network_utils import resolve_adapter_ip, resolve_management_url, ping_host
from utils.backup import backup_adapter_config
from utils.logger import log_operation


def build_configure_plan(device: dict) -> Dict[str, str]:
    """
    解析设备配置，生成网卡配置计划。
    返回 {adapter_ip, mask, gateway, mgmt_url}。
    """
    adapter_ip = resolve_adapter_ip(device)
    mask = device.get("subnet_mask", "255.255.255.0")
    gateway = device.get("gateway", "") or None
    mgmt_url = resolve_management_url(device)
    return {
        "adapter_ip": adapter_ip,
        "mask": mask,
        "gateway": gateway,
        "mgmt_url": mgmt_url,
    }


def backup_current(config: dict, adapter) -> bool:
    """备份网卡当前 IP 配置。"""
    return backup_adapter_config(adapter.name, adapter.mac, config)


def apply_static(adapter_name: str, plan: Dict[str, str]) -> bool:
    """应用静态 IP 配置。失败时抛出 RuntimeError（含错误码建议）。"""
    return set_static_ip(
        adapter_name,
        plan["adapter_ip"],
        plan["mask"],
        plan.get("gateway") or None,
    )


def save_state(config: dict, adapter_mac: str) -> None:
    """保存 MAC 记忆并持久化配置。"""
    cm.set_last_adapter_mac(config, adapter_mac)
    cm.save_config(config)


def verify_device(device_ip: str, timeout: int = 3) -> bool:
    """Ping 设备验证连通性。"""
    return ping_host(device_ip, timeout=timeout)


def restore_to_dhcp(config: dict, adapter) -> Tuple[bool, str, dict]:
    """恢复网卡为 DHCP。失败抛出 RuntimeError（含用户提示）。"""
    set_dhcp(adapter.name)
    cm.save_config(config)
    return (True, "DHCP", config)


def _find_backup_pos(stack: list, target: dict) -> Optional[int]:
    """定位目标备份（优先对象同一性，其次按内容匹配）。"""
    for i, bk in enumerate(stack):
        if bk is target or (
            bk.get("ip") == target.get("ip")
            and bk.get("mask") == target.get("mask")
            and bk.get("gateway") == target.get("gateway")
            and bool(bk.get("is_dhcp")) == bool(target.get("is_dhcp"))
            and bk.get("timestamp") == target.get("timestamp")
        ):
            return i
    return None


def undo_to_backup(config: dict, adapter, target: dict) -> Tuple[bool, str, dict]:
    """
    撤销恢复到目标备份版本。
    先应用成功再弹出备份；失败时备份栈保持不变，可重试。
    返回 (ok, message, config)。
    """
    stack = cm.get_adapter_backup_stack(config, adapter.mac)
    target_pos = _find_backup_pos(stack, target)
    if target_pos is None:
        return (False, "备份栈中未找到该版本，可能已被其他操作清除", config)

    target = stack[target_pos]
    try:
        if target.get("is_dhcp", True):
            ok = set_dhcp(adapter.name)
            label = "DHCP"
        else:
            ok = set_static_ip(
                adapter.name,
                target.get("ip", ""),
                target.get("mask", "255.255.255.0"),
                target.get("gateway", "") or None,
            )
            label = target.get("ip", "")
        if not ok:
            return (False, f"恢复到 {label} 失败", config)
    except RuntimeError as e:
        return (False, str(e), config)

    # 应用成功：弹出目标及其之上的中间版本
    for _ in range(target_pos + 1):
        _, config = cm.pop_adapter_backup(config, adapter.mac)
    if target_pos > 0:
        label = f"{label}（跳过 {target_pos} 级中间版本）"
    cm.save_config(config)
    return (True, label, config)


def history_restore(config: dict, adapter, record: dict) -> Tuple[bool, str, dict]:
    """恢复历史记录中的静态 IP 配置。"""
    ip = record.get("ip", "")
    mask = record.get("mask", "255.255.255.0")
    gateway = record.get("gateway", "") or None
    ok = set_static_ip(adapter.name, ip, mask, gateway)
    if not ok:
        return (False, f"恢复静态IP失败: {ip}", config)
    cm.save_config(config)
    return (True, ip, config)


def run_hooks(config: dict, hook_name: str, **context) -> None:
    """
    执行动作钩子（用户配置的自定义命令）。
    读取 settings.hooks.<hook_name>，非空时后台执行，不阻塞界面。
    """
    command = cm.get_setting(config, f"hooks.{hook_name}", "")
    if not command or not isinstance(command, str):
        return
    try:
        subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        log_operation("钩子", result=f"{hook_name}: {command}")
    except Exception:
        pass
