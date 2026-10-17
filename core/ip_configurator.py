"""
IP配置执行模块
使用 wmi Python 包通过 WMI 的 Win32_NetworkAdapterConfiguration
实现网卡IP的设置和修改，避免 netsh 命令行调用开销
"""

import wmi
from typing import Optional, Dict, Any

# WMI 连接缓存
_wmi_conn = None

# WMI 网卡配置常见返回码及解决建议
WMI_ERROR_HINTS = {
    1: "操作不受当前网卡支持，请更换网卡或检查驱动",
    84: "网卡当前未连接（断开状态），请接入网线后重试",
    85: "IP 地址或子网掩码格式无效，请检查输入",
    87: "无法将 IP 地址设置到该网卡，可能是地址无效或未被系统接受",
    91: "DHCP 设置失败，请检查网卡状态",
    97: "IP 地址已被其他网卡或主机占用，请更换地址",
    99: "网关地址无效或不在同一网段",
}


def _wmi_error_text(action: str, code: int) -> str:
    """生成带解决建议的错误提示。"""
    hint = WMI_ERROR_HINTS.get(code, "请检查网卡连接状态与 IP 配置后重试")
    return f"{action} 返回错误码: {code}\n建议: {hint}"


def _get_wmi():
    """获取WMI连接（缓存复用）"""
    global _wmi_conn
    if _wmi_conn is None:
        _wmi_conn = wmi.WMI()
    return _wmi_conn


def _get_adapter_config_by_name(adapter_name: str) -> Optional[Any]:
    """
    通过网卡友好名称（NetConnectionID）查找对应的配置对象。
    关联方式：Win32_NetworkAdapter.DeviceID = Win32_NetworkAdapterConfiguration.Index
    """
    c = _get_wmi()

    # 通过 Win32_NetworkAdapter 查找 NetConnectionID 匹配的网卡
    for adapter in c.Win32_NetworkAdapter(NetConnectionID=adapter_name):
        device_id = adapter.DeviceID
        # 通过 Index 关联到 NetworkAdapterConfiguration
        for config in c.Win32_NetworkAdapterConfiguration(Index=device_id):
            return config

    return None


def _is_ipv4(addr: Any) -> bool:
    """判断是否为 IPv4 地址字符串（沿用既有的 '有点无冒号' 粗判规则）。"""
    return isinstance(addr, str) and "." in addr and ":" not in addr


def _as_list(value) -> list:
    """把标量/None/序列统一成列表（兼容手工编辑 YAML 时写成单个字符串的情况）。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    try:
        return list(value)
    except TypeError:
        return []


def _align_ipv4(ips, masks, default_mask: str = "") -> tuple:
    """
    把 IP/掩码两个序列整理成一一对齐的数组（供 WMI EnableStatic 使用）。

    - WMI 的 IPAddress/IPSubnet 是**数组**，数组第 0 项为网卡主 IP，其余为次要 IP；
      掩码与地址按索引一一对应，因此这里必须保持索引对齐。
    - 过滤 IPv6 / 空项 / 非法项，并按 (ip, mask) 去重（保留首次出现顺序）。
    - 掩码缺失或为空时用 default_mask 补齐（调用方可指定系统默认 255.255.255.0）。
    - 保持输入顺序，确保主 IP 始终排在第 0 位。
    """
    src_ips = _as_list(ips)
    src_masks = _as_list(masks)

    result_ips, result_masks = [], []
    seen = set()
    for i, addr in enumerate(src_ips):
        if not _is_ipv4(addr):
            continue
        mask = src_masks[i] if i < len(src_masks) and isinstance(src_masks[i], str) else ""
        if not mask:
            mask = default_mask
        if (addr, mask) in seen:
            continue
        seen.add((addr, mask))
        result_ips.append(addr)
        result_masks.append(mask)
    return result_ips, result_masks


def set_static_ip(adapter_name: str, ip: str, mask: str, gateway: str = None,
                  ips: list = None, masks: list = None, gateways: list = None) -> bool:
    """
    调用 WMI EnableStatic 设置静态IP和掩码（支持一次写入多个地址）。
    可选设置网关（SetGateways）。
    成功返回 True，失败抛出 RuntimeError（含用户友好提示）。

    参数（向后兼容）：
    - ip/mask/gateway：单值主 IP 语义，保持旧调用方式不变；
    - ips/masks：可选，该网卡**全部** IPv4 地址+掩码（数组第 0 项为主 IP，其余成为次要 IP）。
      传入时以其为准，用于把备份里的主 IP + 次要 IP 一次性还原；不传则退回 ip/mask 单值行为。
    - gateways：可选，完整网关列表（优先于 gateway 单值）。
    """
    try:
        config = _get_adapter_config_by_name(adapter_name)
        if config is None:
            return False

        # 组装地址数组：新格式 ips/masks 优先，旧格式退回单值 ip/mask
        if ips:
            ip_array, mask_array = _align_ipv4(ips, masks, default_mask="255.255.255.0")
        else:
            ip_array, mask_array = _align_ipv4([ip], [mask or "255.255.255.0"],
                                               default_mask="255.255.255.0")
        if not ip_array:
            raise RuntimeError("没有有效的 IPv4 地址，无法设置（IP 记录为空或格式无效）")

        # 设置静态IP（数组方式：第 0 项为主 IP，其余为次要 IP）
        ret_val = config.EnableStatic(IPAddress=ip_array, SubnetMask=mask_array)
        if ret_val[0] != 0:
            raise RuntimeError(_wmi_error_text("设置静态IP", ret_val[0]))

        # 设置网关（完整列表；网关设置失败不影响主要操作）
        gw_array = _as_list(gateways) if gateways else _as_list(gateway)
        gw_array = [gw for gw in gw_array if _is_ipv4(gw)]
        if gw_array:
            # 去重保序，避免 WMI 因重复网关报错
            deduped = []
            for gw in gw_array:
                if gw not in deduped:
                    deduped.append(gw)
            if len(deduped) == 1:
                # 单网关：保持与修复前完全一致的调用形式（该形式已在现网验证可用）
                config.SetGateways(DefaultIPGateway=deduped)
            else:
                # 多网关：WMI 要求 GatewayCostMetric 与 DefaultIPGateway 等长，代价默认取 1
                config.SetGateways(DefaultIPGateway=deduped,
                                   GatewayCostMetric=[1] * len(deduped))

        return True

    except Exception as e:
        raise RuntimeError(f"设置静态IP失败: {e}")


def set_dhcp(adapter_name: str) -> bool:
    """
    恢复网卡为 DHCP 自动获取。
    直接调用 WMI EnableDHCP。
    成功返回 True，失败抛出 RuntimeError（含用户友好提示）。
    """
    config = _get_adapter_config_by_name(adapter_name)
    if config is None:
        raise RuntimeError(f"未找到网卡 '{adapter_name}' 的WMI配置对象")

    ret_val = config.EnableDHCP()
    code = ret_val[0] if isinstance(ret_val, (tuple, list)) else ret_val

    if code == 0 or code == 1:
        return True

    if code == 84:
        raise RuntimeError(
            "网卡当前未连接（断开状态），无法切换为 DHCP 自动获取。\n"
            "请将网卡接入网线后重试。"
        )

    raise RuntimeError(_wmi_error_text("恢复DHCP", code))


def get_current_ip_config(adapter_name: str) -> Optional[Dict[str, Any]]:
    """
    通过WMI查询网卡当前IP配置。
    返回字典:
      {
        "ip": "x.x.x.x", "mask": "x.x.x.x", "gateway": "x.x.x.x", "is_dhcp": True/False,
        # 以下为新增字段（旧的单值字段语义不变，仍等于数组第 0 项 = 主 IP）：
        "ips": ["主IP", "次要IP", ...],
        "masks": ["主IP掩码", "次要IP掩码", ...],
        "gateways": ["网关1", "网关2", ...],
      }
    WMI 的 IPAddress/IPSubnet/DefaultIPGateway 都是数组，这里完整保留全部 IPv4 项，
    索引 0 为主 IP，其余为次要 IP；查询失败返回 None。
    """
    try:
        config = _get_adapter_config_by_name(adapter_name)
        if config is None:
            return None

        ip_list = config.IPAddress or ()
        mask_list = config.IPSubnet or ()
        gateway_list = config.DefaultIPGateway or ()
        is_dhcp = config.DHCPEnabled

        # 全部 IPv4 地址+掩码（按索引对齐，索引 0 为主 IP）
        ips, masks = _align_ipv4(ip_list, mask_list, default_mask="")

        # 全部 IPv4 网关（去重保序）
        gateways = []
        for gw in gateway_list:
            if _is_ipv4(gw) and gw not in gateways:
                gateways.append(gw)

        return {
            # 既有单值字段：语义不变，= 数组第 0 项
            "ip": ips[0] if ips else "",
            "mask": masks[0] if masks else "",
            "gateway": gateways[0] if gateways else "",
            "is_dhcp": bool(is_dhcp),
            # 新增：完整列表
            "ips": ips,
            "masks": masks,
            "gateways": gateways,
        }

    except Exception:
        return None


def reset_wmi_connection():
    """重置WMI连接缓存"""
    global _wmi_conn
    _wmi_conn = None
