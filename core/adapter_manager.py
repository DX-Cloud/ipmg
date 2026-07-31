"""
网卡管理模块
使用 ctypes 直接调用 Windows iphlpapi.dll 的 GetAdaptersAddresses API 获取网卡信息
避免 subprocess 开销，实现零延迟网卡枚举
"""

import ctypes
import ctypes.wintypes
import socket
import struct
from typing import List, Optional, Dict, Any

# Windows 常量
ERROR_BUFFER_OVERFLOW = 111
ERROR_SUCCESS = 0
GAA_FLAG_SKIP_ANYCAST = 0x0002
GAA_FLAG_SKIP_MULTICAST = 0x0004
GAA_FLAG_SKIP_DNS_SERVER = 0x0008
MAX_ADAPTER_ADDRESS_LENGTH = 8

# 地址族
AF_UNSPEC = 0
AF_INET = 2
AF_INET6 = 23

# 网卡类型
IF_TYPE_SOFTWARE_LOOPBACK = 24
IF_TYPE_ETHERNET_CSMACD = 6
IF_TYPE_IEEE80211 = 71

# DLL
iphlpapi = ctypes.windll.iphlpapi
kernel32 = ctypes.windll.kernel32


# 手动定义 SOCKADDR（ctypes.wintypes 中没有）
class SOCKADDR(ctypes.Structure):
    _fields_ = [
        ("sa_family", ctypes.c_ushort),
        ("sa_data", ctypes.c_ubyte * 14),
    ]


# ctypes 结构体定义
class SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [
        ("lpSockaddr", ctypes.POINTER(SOCKADDR)),
        ("iSockaddrLength", ctypes.c_int),
    ]


class IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
    pass


IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
    ("Length", ctypes.c_ulong),
    ("Flags", ctypes.wintypes.DWORD),
    ("Next", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
    ("Address", SOCKET_ADDRESS),
    ("PrefixOrigin", ctypes.c_int),
    ("SuffixOrigin", ctypes.c_int),
    ("DadState", ctypes.c_int),
    ("ValidLifetime", ctypes.c_ulong),
    ("PreferredLifetime", ctypes.c_ulong),
    ("LeaseLifetime", ctypes.c_ulong),
    ("OnLinkPrefixLength", ctypes.c_ubyte),
]


class IP_ADAPTER_ADDRESSES(ctypes.Structure):
    pass


IP_ADAPTER_ADDRESSES._fields_ = [
    ("Length", ctypes.c_ulong),
    ("IfIndex", ctypes.wintypes.DWORD),
    ("Next", ctypes.POINTER(IP_ADAPTER_ADDRESSES)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)),
    ("FirstAnycastAddress", ctypes.c_void_p),
    ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.c_void_p),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", ctypes.c_ubyte * MAX_ADAPTER_ADDRESS_LENGTH),
    ("PhysicalAddressLength", ctypes.wintypes.DWORD),
    ("Flags", ctypes.wintypes.DWORD),
    ("Mtu", ctypes.wintypes.DWORD),
    ("IfType", ctypes.wintypes.DWORD),
    ("OperStatus", ctypes.c_int),
    ("Ipv6IfIndex", ctypes.wintypes.DWORD),
    ("ZoneIndices", ctypes.wintypes.DWORD * 16),
    ("FirstPrefix", ctypes.c_void_p),
    ("TransmitLinkSpeed", ctypes.c_uint64),
    ("ReceiveLinkSpeed", ctypes.c_uint64),
    ("FirstWinsServerAddress", ctypes.c_void_p),
    ("FirstGatewayAddress", ctypes.c_void_p),
    ("Luid", ctypes.c_uint64),
    ("Dhcpv4Server", SOCKET_ADDRESS),
]


class NetworkAdapter:
    """网卡信息数据类"""

    def __init__(self, name: str, description: str, mac: str, ip_addresses: List[Dict[str, str]],
                 if_type: int, is_up: bool):
        self.name = name                    # 友好名称（如 "以太网"）
        self.description = description      # 网卡描述
        self.mac = mac                      # MAC地址（如 "AA:BB:CC:DD:EE:FF"）
        self.ip_addresses = ip_addresses    # [{"ip": "x.x.x.x", "mask": "x.x.x.x"}, ...]
        self.if_type = if_type             # 网卡类型
        self.is_up = is_up                 # 是否启用

    @property
    def is_loopback(self) -> bool:
        return self.if_type == IF_TYPE_SOFTWARE_LOOPBACK

    @property
    def type_name(self) -> str:
        if self.if_type == IF_TYPE_ETHERNET_CSMACD:
            return "以太网"
        elif self.if_type == IF_TYPE_IEEE80211:
            return "无线"
        elif self.if_type == IF_TYPE_SOFTWARE_LOOPBACK:
            return "回环"
        else:
            return f"类型{self.if_type}"

    @property
    def display_ip(self) -> str:
        """获取第一个IPv4地址的显示字符串"""
        for addr in self.ip_addresses:
            return addr.get("ip", "")
        return ""

    @property
    def display_mask(self) -> str:
        """获取第一个IPv4地址的掩码"""
        for addr in self.ip_addresses:
            return addr.get("mask", "")
        return ""

    def __repr__(self):
        return f"NetworkAdapter(name={self.name!r}, mac={self.mac!r}, ip={self.display_ip!r})"


def _mac_to_str(physical_address, length) -> str:
    """将MAC地址字节数组转为字符串"""
    if length == 0:
        return ""
    # 防止部分隧道适配器上报超长地址导致越界读取结构体相邻字段
    length = min(int(length), MAX_ADAPTER_ADDRESS_LENGTH)
    return ":".join(f"{physical_address[i]:02X}" for i in range(length))


def _sockaddr_to_ip(sockaddr_ptr, sockaddr_len) -> Optional[str]:
    """将SOCKADDR转为IP字符串"""
    if not sockaddr_ptr or sockaddr_len == 0:
        return None

    try:
        sockaddr = sockaddr_ptr.contents
        family = sockaddr.sa_family

        if family == AF_INET:
            # IPv4: sockaddr_in = sa_family(2) + sin_port(2) + sin_addr(4) + padding(8)
            # sin_addr 从 sa_data 的第2个字节开始（跳过 port 的2字节）
            raw = bytes(sockaddr.sa_data)
            if len(raw) >= 6:
                ip_bytes = raw[2:6]
                return socket.inet_ntoa(ip_bytes)
    except Exception:
        pass
    return None


def _prefix_length_to_mask(prefix_len: int) -> str:
    """将前缀长度转为子网掩码字符串"""
    if prefix_len > 32:
        prefix_len = 32
    mask = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF
    return socket.inet_ntoa(struct.pack("!I", mask))


def get_network_adapters() -> List[NetworkAdapter]:
    """
    获取所有活动网卡列表。
    使用 GetAdaptersAddresses API，避免 subprocess 开销。
    返回排除了回环网卡的活动网卡列表。
    """
    adapters = []
    buf_size = ctypes.wintypes.ULONG(15000)
    buf = None
    attempts = 0
    max_attempts = 3

    while attempts < max_attempts:
        buf = ctypes.create_string_buffer(buf_size.value)
        ptr_adapter_addresses = ctypes.cast(buf, ctypes.POINTER(IP_ADAPTER_ADDRESSES))

        # GAA_FLAG: 跳过不需要的信息，加快查询速度
        flags = (GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_SKIP_DNS_SERVER)

        result = iphlpapi.GetAdaptersAddresses(
            AF_UNSPEC,           # 获取所有地址族
            flags,
            None,                # 无预留
            ptr_adapter_addresses,
            ctypes.byref(buf_size),
        )

        if result == ERROR_SUCCESS:
            break
        elif result == ERROR_BUFFER_OVERFLOW:
            buf_size.value *= 2
            attempts += 1
        else:
            return adapters  # 调用失败，返回空列表

    if attempts >= max_attempts:
        return adapters

    # 遍历链表
    current = ptr_adapter_addresses
    while current:
        adapter = None
        try:
            adapter = current.contents

            # 跳过回环网卡
            if adapter.IfType == IF_TYPE_SOFTWARE_LOOPBACK:
                current = adapter.Next
                continue

            # 记录启用状态（不过滤未启用的网卡）
            is_up = adapter.OperStatus == 1  # IfOperStatusUp

            # 跳过没有 FriendlyName 的虚拟网卡（如某些隧道适配器）
            friendly_name = adapter.FriendlyName or ""
            if not friendly_name:
                current = adapter.Next
                continue

            # 提取网卡信息（friendly_name 已在上面获取）
            description = adapter.Description or ""
            mac = _mac_to_str(adapter.PhysicalAddress, adapter.PhysicalAddressLength)
            if_type = adapter.IfType

            # 提取IPv4地址列表
            ip_addresses = []
            uni_addr = adapter.FirstUnicastAddress
            while uni_addr:
                try:
                    uni = uni_addr.contents
                    ip_str = _sockaddr_to_ip(uni.Address.lpSockaddr, uni.Address.iSockaddrLength)
                    if ip_str and ":" not in ip_str:  # 只要IPv4
                        # 获取子网掩码
                        prefix_len = uni.OnLinkPrefixLength
                        mask = _prefix_length_to_mask(prefix_len)
                        ip_addresses.append({"ip": ip_str, "mask": mask})
                except Exception:
                    pass
                uni_addr = uni.Next

            net_adapter = NetworkAdapter(
                name=friendly_name,
                description=description,
                mac=mac,
                ip_addresses=ip_addresses,
                if_type=if_type,
                is_up=is_up,
            )
            adapters.append(net_adapter)

        except Exception:
            pass

        # adapter 读取失败时无法安全前进，直接终止遍历
        if adapter is None:
            break
        if adapter.Next:
            current = adapter.Next
        else:
            break

    return adapters


def select_adapter(adapters: List[NetworkAdapter], last_mac: str = "") -> int:
    """
    根据MAC记忆返回推荐选中的网卡索引。
    如果找到上次选择的网卡，返回其索引；否则返回0。
    """
    if not adapters:
        return -1

    if last_mac:
        for i, adapter in enumerate(adapters):
            if adapter.mac.upper() == last_mac.upper():
                return i

    return 0


def get_adapter_by_mac(adapters: List[NetworkAdapter], mac: str) -> Optional[NetworkAdapter]:
    """根据MAC查找特定网卡"""
    if not mac:
        return None
    for adapter in adapters:
        if adapter.mac.upper() == mac.upper():
            return adapter
    return None
