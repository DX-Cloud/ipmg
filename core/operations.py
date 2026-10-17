"""
业务操作层
配置/恢复的核心步骤与 TUI 展示解耦，供界面、CLI、自动化测试复用。
所有操作返回 (ok, message, config) 或直接抛出 RuntimeError（网络层失败）。
"""

import subprocess
from typing import Any, Dict, Optional, Tuple

from core import config_manager as cm
from core.ip_configurator import (
    DEFAULT_SUBNET_MASK,
    MAX_IPV4_PER_ADAPTER,
    WARN_IPV4_COUNT,
    get_current_ip_config,
    merge_append_ips,
    set_dhcp,
    set_static_ip,
)
from core.network_utils import resolve_adapter_ip, resolve_management_url
from utils.backup import backup_adapter_config, static_restore_args
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


def _append_report(reason: str = "", **overrides) -> dict:
    """构造追加模式预检报告（固定包含全部契约字段，UI 可无脑取值）。"""
    report = {
        "ok": False,
        "reason": reason,
        "existing_ips": [],
        "existing_masks": [],
        "existing_gateways": [],
        "merged_ips": [],
        "merged_masks": [],
        "already_present": False,
        "is_dhcp": False,
        "count": 0,
        "warnings": [],
    }
    report.update(overrides)
    return report


def _existing_ip_pairs(current: dict) -> Tuple[list, list]:
    """
    从 get_current_ip_config 的结果中取出 (ips, masks)。
    get_current_ip_config 已保证是 IPv4 且按索引对齐，这里只做类型兜底与空掩码补齐。
    """
    ips = [a for a in (current.get("ips") or []) if isinstance(a, str) and a]
    raw_masks = list(current.get("masks") or [])
    masks = []
    for i in range(len(ips)):
        mask = raw_masks[i] if i < len(raw_masks) and isinstance(raw_masks[i], str) else ""
        masks.append(mask or DEFAULT_SUBNET_MASK)
    return ips, masks


def _append_reject_reason(current: dict) -> Optional[str]:
    """
    追加模式的公共前置判断（不含目标 IP 校验）。
    返回 None 表示可以追加，否则返回拒绝原因：
    当前为 DHCP 且没有可用的静态基础地址（地址为空或仅有 APIPA 169.254.x）时无法确定追加的基础地址。
    """
    if not bool(current.get("is_dhcp", False)):
        return None
    ips = [a for a in (current.get("ips") or []) if isinstance(a, str) and a]
    if ips and not all(a.startswith("169.254.") for a in ips):
        return None
    kind = "仅持有 APIPA 自动专用地址(169.254.x)" if ips else "没有 IPv4 地址"
    return (
        f"网卡当前为 DHCP 自动获取且{kind}，无法确定追加的基础地址；"
        f"请改用覆盖模式（overwrite），或先为该网卡配置静态 IP"
    )


def inspect_append(adapter_name: str, plan: dict) -> dict:
    """
    追加模式预检（只读：不写 WMI、不落配置）。
    返回 {"ok", "reason", "existing_ips", "existing_masks", "existing_gateways",
          "merged_ips", "merged_masks", "already_present", "is_dhcp", "count", "warnings"}；
    count 为写入后的地址数（被拒绝时为当前地址数）。
    existing_gateways 供 UI 在写入后校验"默认网关没有丢"。
    """
    target_ip = (plan.get("adapter_ip") or "").strip()
    target_mask = (plan.get("mask") or DEFAULT_SUBNET_MASK).strip() or DEFAULT_SUBNET_MASK

    current = get_current_ip_config(adapter_name)
    if current is None:
        return _append_report(
            f"读取网卡 '{adapter_name}' 的当前 IP 配置失败（网卡不存在或 WMI 查询失败）"
        )

    existing_ips, existing_masks = _existing_ip_pairs(current)
    is_dhcp = bool(current.get("is_dhcp", False))
    gateways = [g for g in (current.get("gateways") or []) if isinstance(g, str) and g]
    base = {
        "existing_ips": list(existing_ips),
        "existing_masks": list(existing_masks),
        "existing_gateways": list(gateways),
        "is_dhcp": is_dhcp,
        "count": len(existing_ips),
    }

    if not target_ip:
        return _append_report("未指定要追加的 IP 地址（plan.adapter_ip 为空）", **base)

    reject = _append_reject_reason(current)
    if reject:
        return _append_report(reject, **base)

    # 目标 IP 已存在：掩码一致 → 幂等；掩码不同 → 拒绝（会写出重复地址）
    if target_ip in existing_ips:
        idx = existing_ips.index(target_ip)
        if existing_masks[idx] == target_mask:
            return _append_report(
                f"IP {target_ip} 已存在且掩码一致，无需重复写入",
                ok=True,
                already_present=True,
                merged_ips=list(existing_ips),
                merged_masks=list(existing_masks),
                **base,
            )
        return _append_report(
            f"IP {target_ip} 已存在但掩码不同（现有 {existing_masks[idx]} / 目标 {target_mask}），"
            f"追加会产生重复地址，请改用覆盖模式（overwrite）",
            **base,
        )

    try:
        merged_ips, merged_masks = merge_append_ips(
            existing_ips, existing_masks, target_ip, target_mask
        )
    except ValueError as e:
        return _append_report(str(e), **base)

    if len(merged_ips) > MAX_IPV4_PER_ADAPTER:
        base["count"] = len(merged_ips)
        return _append_report(
            f"追加后该网卡将有 {len(merged_ips)} 个 IPv4 地址，"
            f"超过单网卡上限 {MAX_IPV4_PER_ADAPTER} 个",
            merged_ips=merged_ips,
            merged_masks=merged_masks,
            **base,
        )

    warnings = []
    if is_dhcp:
        warnings.append(
            "网卡当前为 DHCP 自动获取：追加 IP 会将其切换为静态配置，"
            "DHCP 下发的地址与网关将不再生效"
        )
    if len(merged_ips) >= WARN_IPV4_COUNT:
        warnings.append(
            f"该网卡将配置 {len(merged_ips)} 个 IPv4 地址（建议不超过 {WARN_IPV4_COUNT} 个），"
            f"地址过多可能影响网络稳定性"
        )
    if gateways:
        warnings.append(
            "追加会同时回写网卡原有默认网关"
            f"（当前 {'、'.join(gateways)}），确保加 IP 之后默认网关不丢"
        )

    base["count"] = len(merged_ips)
    return _append_report(
        ok=True,
        merged_ips=merged_ips,
        merged_masks=merged_masks,
        warnings=warnings,
        **base,
    )


def apply_static(adapter_name: str, plan: Dict[str, str], mode: str = "overwrite") -> bool:
    """
    应用静态 IP 配置。失败时抛出 RuntimeError（含错误码建议）。

    mode:
    - "overwrite"（默认）：与既有行为完全一致，只写入 plan 中的单个地址（覆盖网卡现有地址）；
    - "append"：重新读取网卡当前地址后追加 plan 的目标地址（按 IP 去重，主 IP 仍在第 0 位），
      一次性写入完整数组；**网关保持原样**——回写写入前读到的原网关值（不使用 plan 里的网关），
      原网关为空时完全不碰网关；
      目标地址已存在且掩码一致时幂等返回 True（不写 WMI）。
    其它取值抛 ValueError；追加模式下地址非法 / 同 IP 异掩码 / 地址数超上限同样抛 ValueError。
    """
    if mode == "overwrite":
        return set_static_ip(
            adapter_name,
            plan["adapter_ip"],
            plan["mask"],
            plan.get("gateway") or None,
        )
    if mode != "append":
        raise ValueError(f"不支持的 IP 写入模式: {mode!r}（仅支持 'overwrite' / 'append'）")

    # 追加模式：重新读取当前配置（不复用 inspect_append 的结果，避免 TOCTOU）
    current = get_current_ip_config(adapter_name)
    if current is None:
        raise RuntimeError(
            f"读取网卡 '{adapter_name}' 的当前 IP 配置失败（网卡不存在或 WMI 查询失败），无法追加 IP"
        )

    reject = _append_reject_reason(current)
    if reject:
        raise RuntimeError(reject)

    target_ip = (plan.get("adapter_ip") or "").strip()
    target_mask = (plan.get("mask") or DEFAULT_SUBNET_MASK).strip() or DEFAULT_SUBNET_MASK
    existing_ips, existing_masks = _existing_ip_pairs(current)

    if target_ip in existing_ips:
        idx = existing_ips.index(target_ip)
        if existing_masks[idx] == target_mask:
            return True  # 幂等：已存在且掩码一致，不写 WMI
        raise ValueError(
            f"IP {target_ip} 已存在但掩码不同（现有 {existing_masks[idx]} / 目标 {target_mask}），"
            f"追加会产生重复地址，请改用覆盖模式"
        )

    merged_ips, merged_masks = merge_append_ips(
        existing_ips, existing_masks, target_ip, target_mask
    )
    if len(merged_ips) > MAX_IPV4_PER_ADAPTER:
        raise ValueError(
            f"追加后该网卡将有 {len(merged_ips)} 个 IPv4 地址，"
            f"超过单网卡上限 {MAX_IPV4_PER_ADAPTER} 个"
        )

    # 写入地址数组，并回写"写入前读到的原网关值"。
    # 原因：EnableStatic 只负责地址，默认网关是独立属性；写入后若不显式设置，
    # 部分驱动/DHCP 转静态场景会丢掉默认网关（直接断网，比不加 IP 更糟）。
    # 这里回写的是原值，语义仍是"追加 IP，网关不变"；plan 里的网关属于目标设备
    # 网段，追加模式刻意不使用，避免把默认网关改到设备网段去。
    existing_gateways = [gw for gw in (current.get("gateways") or []) if gw]
    return set_static_ip(
        adapter_name,
        merged_ips[0],
        merged_masks[0],
        ips=merged_ips,
        masks=merged_masks,
        gateways=existing_gateways or None,
    )


def save_state(config: dict, adapter_mac: str) -> None:
    """保存 MAC 记忆并持久化配置。"""
    cm.set_last_adapter_mac(config, adapter_mac)
    cm.save_config(config)


def restore_to_dhcp(config: dict, adapter) -> Tuple[bool, str, dict]:
    """恢复网卡为 DHCP。失败抛出 RuntimeError（含用户提示）。"""
    set_dhcp(adapter.name)
    cm.save_config(config)
    return (True, "DHCP", config)


def _same_ip_set(a, b) -> bool:
    """
    比较两份备份的全量 IP 字段（ips/masks/gateways）。
    任一侧没有这些字段（旧格式条目）时视为一致，退回原有的单值比较，避免误判"找不到备份"。
    """
    if not a or not b:
        return True
    return a == b


def _find_backup_pos(stack: list, target: dict) -> Optional[int]:
    """定位目标备份（优先对象同一性，其次按内容匹配）。"""
    for i, bk in enumerate(stack):
        if bk is target or (
            bk.get("ip") == target.get("ip")
            and bk.get("mask") == target.get("mask")
            and bk.get("gateway") == target.get("gateway")
            and _same_ip_set(bk.get("ips"), target.get("ips"))
            and _same_ip_set(bk.get("masks"), target.get("masks"))
            and _same_ip_set(bk.get("gateways"), target.get("gateways"))
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
            # 新格式备份带 ips/masks/gateways → 一次性还原主 IP + 次要 IP；旧条目自动退回单值
            ok = set_static_ip(adapter.name, **static_restore_args(target))
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
    """恢复历史记录中的静态 IP 配置（多地址记录会一次性还原全部 IP）。"""
    args = static_restore_args(record)
    ip = args["ip"]
    ok = set_static_ip(adapter.name, **args)
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
