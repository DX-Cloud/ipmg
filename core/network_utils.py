"""
网络工具模块
提供自适应网卡IP计算、管理URL解析、IP与掩码校验等能力
"""

from typing import Optional

from netaddr import IPNetwork, IPAddress


def calculate_adapter_ip_auto(device_ip: str, subnet_mask: str) -> str:
    """
    自适应网卡IP计算。
    根据设备IP和子网掩码，自动计算网段内可用主机IP范围的最后一个可用IP作为网卡IP。

    算法：
    1. 用netaddr计算网段的可用主机IP范围（排除网络地址和广播地址）
    2. 取最后一个可用主机IP
    3. 如果该IP与设备IP相同，则取倒数第二个可用IP

    示例：
      device_ip=10.251.251.251/24 → 可用范围.1~.254 → 取254
      device_ip=192.168.1.1/24 → 可用范围.1~.254 → 取254
      device_ip=192.168.1.254/24 → 可用范围.1~.254 → 冲突规避取253
    """
    try:
        device_addr = IPAddress(device_ip)
        network = IPNetwork(f"{device_ip}/{subnet_mask}")

        # /31、/32 网段没有可用的主机地址（对点链路除外，本工具不适用）
        if network.prefixlen >= 31:
            raise ValueError(f"网段 {network} 无可用主机IP")

        # 网络地址 network[0]，广播地址 network[-1]，可用主机为 network[1:-1]。
        # 直接用索引取 O(1) 计算，避免对 /0 等大网段物化全部主机导致卡死/内存耗尽。
        target_ip = network[-2]  # 最后一个可用主机IP

        # 冲突规避：如果与设备IP相同，取倒数第二个
        if target_ip == device_addr:
            if network.size < 4:  # 小于 /30 时没有两个可用主机
                raise ValueError(f"网段 {network} 可用主机IP不足，无法自动计算网卡IP")
            target_ip = network[-3]

        return str(target_ip)

    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"计算网卡IP失败: {e}")


def resolve_adapter_ip(device_config: dict) -> str:
    """
    根据设备的ip_mode统一解析最终网卡IP。
    - auto模式：调用 calculate_adapter_ip_auto 自适应计算
    - manual模式：直接返回设备配置中的 adapter_ip 字段
    """
    ip_mode = device_config.get("ip_mode", "auto")
    device_ip = device_config.get("device_ip", "")
    subnet_mask = device_config.get("subnet_mask", "255.255.255.0")

    if ip_mode == "manual":
        adapter_ip = device_config.get("adapter_ip", "")
        if not adapter_ip:
            raise ValueError("手动指定模式下，网卡IP不能为空")
        return adapter_ip
    else:
        # auto 模式
        if not device_ip or not subnet_mask:
            raise ValueError("设备IP和子网掩码不能为空")
        return calculate_adapter_ip_auto(device_ip, subnet_mask)


def resolve_management_url(device_config: dict) -> Optional[str]:
    """
    解析管理页面URL。
    将 {device_ip} 变量替换为实际设备IP。
    返回最终URL字符串，模板为空时返回None。
    """
    url_template = device_config.get("management_url", "")
    if not url_template:
        return None

    device_ip = device_config.get("device_ip", "")
    return url_template.replace("{device_ip}", device_ip)


def validate_ip(ip_str: str) -> bool:
    """验证IPv4地址格式"""
    try:
        parts = ip_str.split(".")
        if len(parts) != 4:
            return False
        for p in parts:
            n = int(p)
            if n < 0 or n > 255:
                return False
        return True
    except (ValueError, AttributeError):
        return False


def validate_subnet_mask(mask_str: str) -> bool:
    """验证子网掩码格式"""
    try:
        if not validate_ip(mask_str):
            return False
        # 检查掩码合法性
        parts = [int(p) for p in mask_str.split(".")]
        binary = "".join(f"{p:08b}" for p in parts)
        # 全 0 不是合法子网掩码（会形成 /0 网段）
        if "1" not in binary:
            return False
        # 掩码应该是一串连续的1后跟连续的0
        if "01" in binary:
            return False
        return True
    except (ValueError, AttributeError):
        return False
