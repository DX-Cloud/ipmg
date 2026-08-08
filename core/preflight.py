"""
配置前预检
在切换 IP 前检查本机冲突、ARP 冲突、网关网段、设备可达性，
错误项阻止配置，警告项提示后允许继续。
"""

import re
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Set

from netaddr import IPAddress, IPNetwork

from core.network_utils import ping_host


@dataclass
class PreflightIssue:
    level: str          # "error" 阻止配置 / "warning" 提示后继续
    message: str
    suggestion: str = ""


def check_local_conflict(adapters: Sequence, target_ip: str) -> Optional[PreflightIssue]:
    """本机其他网卡是否已配置目标 IP。"""
    for adapter in adapters:
        for addr in getattr(adapter, "ip_addresses", []):
            if addr.get("ip") == target_ip:
                return PreflightIssue(
                    "error",
                    f"目标IP {target_ip} 已配置在本机网卡 {adapter.name}",
                    "请更换目标IP，或先释放该网卡上的地址",
                )
    return None


def _local_macs(adapters: Sequence) -> Set[str]:
    return {a.mac.upper() for a in adapters if getattr(a, "mac", "")}


def check_arp_conflict(target_ip: str, local_macs: Set[str]) -> Optional[PreflightIssue]:
    """
    ARP 表冲突检测（警告级：ARP 缓存可能过期，不阻断配置）。
    兼容中文/英文系统的 arp -a 输出格式。
    """
    try:
        proc = subprocess.run(
            ["arp", "-a"], capture_output=True, text=True, timeout=3
        )
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except Exception:
        return None

    ip_pattern = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
    mac_pattern = re.compile(r"^([0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2}$")

    for line in text.splitlines():
        parts = line.split()
        if not parts or not ip_pattern.match(parts[0]):
            continue
        if parts[0] != target_ip:
            continue
        for token in parts[1:]:
            if mac_pattern.match(token):
                mac = token.replace("-", ":").upper()
                if mac not in local_macs:
                    return PreflightIssue(
                        "warning",
                        f"ARP表中目标IP {target_ip} 对应其他主机（{mac}），可能存在IP冲突",
                        "若确认地址被占用请更换；ARP缓存可能过期，可继续配置",
                    )
                break
    return None


def check_gateway(gateway: Optional[str], adapter_ip: str, mask: str) -> Optional[PreflightIssue]:
    """网关是否在掩码网段内，且不等于网卡自身 IP。"""
    if not gateway:
        return None
    if gateway == adapter_ip:
        return PreflightIssue(
            "error",
            f"网关 {gateway} 与网卡IP相同",
            "请填写正确的网关地址，或留空",
        )
    try:
        network = IPNetwork(f"{adapter_ip}/{mask}")
        if IPAddress(gateway) not in network:
            return PreflightIssue(
                "error",
                f"网关 {gateway} 不在网段 {network} 内",
                "请修改网关或子网掩码",
            )
    except Exception:
        return None
    return None


def check_device_reachable(device_ip: str, timeout: int = 2) -> Optional[PreflightIssue]:
    """配置前目标设备可达性（警告级）。"""
    if not device_ip:
        return None
    if not ping_host(device_ip, timeout=timeout):
        return PreflightIssue(
            "warning",
            f"设备 {device_ip} 当前不可达",
            "可能设备未开机、IP已变更或网络未通；配置后仍会再次验证",
        )
    return None


def run_preflight(
    adapters: Sequence,
    selected_adapter,
    device: dict,
    plan: dict,
) -> List[PreflightIssue]:
    """
    汇总预检结果。
    plan 需包含 adapter_ip / mask / gateway。
    """
    issues: List[PreflightIssue] = []
    target_ip = plan.get("adapter_ip", "")
    selected_mac = (getattr(selected_adapter, "mac", "") or "").upper()
    others = [a for a in adapters if (getattr(a, "mac", "") or "").upper() != selected_mac]

    issue = check_local_conflict(others, target_ip)
    if issue:
        issues.append(issue)

    issue = check_arp_conflict(target_ip, _local_macs(adapters))
    if issue:
        issues.append(issue)

    issue = check_gateway(plan.get("gateway"), target_ip, plan.get("mask", "255.255.255.0"))
    if issue:
        issues.append(issue)

    issue = check_device_reachable(device.get("device_ip", ""))
    if issue:
        issues.append(issue)

    return issues
