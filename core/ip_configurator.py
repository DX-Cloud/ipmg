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


def set_static_ip(adapter_name: str, ip: str, mask: str, gateway: str = None) -> bool:
    """
    调用 WMI EnableStatic 设置静态IP和掩码。
    可选设置网关（SetGateways）。
    成功返回 True，失败返回 False。
    """
    try:
        config = _get_adapter_config_by_name(adapter_name)
        if config is None:
            return False

        # 设置静态IP
        ret_val = config.EnableStatic(IPAddress=[ip], SubnetMask=[mask])
        if ret_val[0] != 0:
            raise RuntimeError(_wmi_error_text("设置静态IP", ret_val[0]))

        # 设置网关
        if gateway:
            gw_ret = config.SetGateways(DefaultIPGateway=[gateway])
            # 网关设置失败不影响主要操作

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
    返回字典: {"ip": "x.x.x.x", "mask": "x.x.x.x", "gateway": "x.x.x.x", "is_dhcp": True/False}
    查询失败返回 None。
    """
    try:
        config = _get_adapter_config_by_name(adapter_name)
        if config is None:
            return None

        ip_list = config.IPAddress or ()
        mask_list = config.IPSubnet or ()
        gateway_list = config.DefaultIPGateway or ()
        is_dhcp = config.DHCPEnabled

        # 提取 IPv4 地址
        ip = ""
        mask = ""
        for i, addr in enumerate(ip_list):
            if "." in addr and ":" not in addr:  # IPv4
                ip = addr
                mask = mask_list[i] if i < len(mask_list) else ""
                break

        # 提取网关
        gateway = ""
        for gw in gateway_list:
            if "." in gw and ":" not in gw:
                gateway = gw
                break

        return {
            "ip": ip,
            "mask": mask,
            "gateway": gateway,
            "is_dhcp": bool(is_dhcp),
        }

    except Exception:
        return None


def reset_wmi_connection():
    """重置WMI连接缓存"""
    global _wmi_conn
    _wmi_conn = None
