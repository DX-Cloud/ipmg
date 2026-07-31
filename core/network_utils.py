"""
网络工具模块
提供Ping连通性检测、IP冲突检测、自适应网卡IP计算、管理URL解析等能力
"""

import socket
import struct
import sys
import time
from typing import Optional

from netaddr import IPNetwork, IPAddress


def ping_host(ip: str, timeout: int = 2) -> bool:
    """
    Ping指定IP检测连通性。
    使用 ICMP socket 实现快速检测（无需 subprocess 调系统 ping 命令）。
    返回 True 表示可达，False 表示不可达。
    """
    try:
        # 尝试使用 ICMP socket（需要管理员权限）
        if sys.platform == "win32":
            # Windows 下使用原生 socket ICMP
            sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except PermissionError:
        # 无 ICMP 权限，降级为 TCP connect 探测（端口7或80）
        return _tcp_ping(ip, timeout)
    except OSError:
        # raw socket 不可用，降级为 TCP 探测
        return _tcp_ping(ip, timeout)

    try:
        sock.settimeout(timeout)
        sock.connect((ip, 0))

        # 构造 ICMP Echo Request
        icmp_id = 1
        checksum = 0
        header = struct.pack("BBHHH", 8, 0, checksum, icmp_id, 1)
        data = struct.pack("d", 0)
        checksum = _calculate_checksum(header + data)
        header = struct.pack("BBHHH", 8, 0, socket.htons(checksum), icmp_id, 1)

        sock.send(header + data)

        # 接收并校验 Echo Reply（必须匹配本请求的 type=0 和 ICMP ID）
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            sock.settimeout(remaining)
            packet = sock.recv(1024)
            if _is_echo_reply(packet, icmp_id):
                return True

    except socket.timeout:
        return False
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _is_echo_reply(packet: bytes, icmp_id: int) -> bool:
    """校验收到的原始包是否为对应该请求的 ICMP Echo Reply（type=0 且 ID 匹配）。"""
    try:
        if len(packet) < 28:
            return False
        # raw socket 收到的是带 IP 头的完整包，跳过 IP 头定位 ICMP 段
        ip_header_len = (packet[0] & 0x0F) * 4
        icmp = packet[ip_header_len:]
        if len(icmp) < 8:
            return False
        icmp_type = icmp[0]
        if icmp_type != 0:  # Echo Reply
            return False
        recv_id = struct.unpack("!H", icmp[4:6])[0]
        return recv_id == icmp_id
    except Exception:
        return False


def _tcp_ping(ip: str, timeout: int = 2) -> bool:
    """TCP连接探测，作为ICMP的降级方案"""
    for port in [7, 80, 443, 22]:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            if sock.connect_ex((ip, port)) == 0:
                return True
        except Exception:
            pass
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
    return False


def _calculate_checksum(data: bytes) -> int:
    """计算ICMP校验和"""
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) + data[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def check_ip_conflict(ip: str, interface_name: str = None) -> Optional[bool]:
    """
    检测目标IP是否冲突。
    返回 True 表示有冲突，False 表示无冲突，None 表示无法确定。
    通过尝试绑定目标IP来检测冲突。
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1)
        # 尝试绑定到目标IP
        sock.bind((ip, 0))
        sock.close()
        return False  # 绑定成功，无冲突
    except OSError:
        return True  # 绑定失败，可能有冲突
    except Exception:
        return None  # 无法确定


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
