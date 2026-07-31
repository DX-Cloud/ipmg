"""
配置文件管理模块
提供YAML配置文件的读写、容错、默认配置生成能力
"""

import os
import sys
import copy
import shutil
from datetime import datetime
from typing import Any

import yaml


# 配置文件路径：统一存放在 %USERPROFILE%\ipmg\
CONFIG_DIR = os.path.join(os.path.expanduser("~"), "ipmg")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.yaml")

# 设备配置默认字段及默认值
DEVICE_DEFAULTS = {
    "name": "",
    "group": "",              # 设备分组/站点
    "device_ip": "",
    "ip_mode": "auto",       # auto | manual
    "adapter_ip": "",
    "subnet_mask": "255.255.255.0",
    "gateway": "",
    "management_url": "https://{device_ip}",
    "favorite": False,
}

# 必填字段
DEVICE_REQUIRED = ["name", "device_ip", "subnet_mask"]


# 历史记录最大数量
MAX_HISTORY_RECORDS = 10

# 配置文件 schema 版本（用于未来的平滑迁移）
CONFIG_VERSION = 1

# 用户设置默认值（集中管理交互偏好，避免散落硬编码）
SETTINGS_DEFAULTS = {
    "auto_open_page": False,             # 配置成功后自动打开设备管理页
    "filter_virtual_adapters": True,     # 网卡列表默认过滤虚拟网卡
    "confirm_danger_default_no": True,   # 危险操作（删除/覆盖）默认拒绝
    "last_action": "configure",          # 主菜单记忆上次执行的动作
    "hooks": {
        "after_configure": "",           # 配置成功后执行的命令（钩子）
        "after_restore": "",             # 恢复成功后执行的命令（钩子）
    },
}


def _get_default_config() -> dict:
    """获取默认配置结构"""
    return {
        "version": CONFIG_VERSION,
        "devices": [],
        "network_adapters": {
            "last_selected_mac": "",
        },
        "backups": {},
        "ip_history": {},
        "groups": [],  # 预定义分组列表
        "settings": copy.deepcopy(SETTINGS_DEFAULTS),
    }


def _normalize_device(device: dict) -> dict:
    """规范化设备配置，补充缺失字段"""
    normalized = copy.deepcopy(DEVICE_DEFAULTS)
    if not isinstance(device, dict):
        # 非字典设备项直接返回默认空设备，避免崩溃
        return normalized
    for key, default_val in normalized.items():
        if key in device:
            normalized[key] = device[key]
        else:
            normalized[key] = default_val
    return normalized


def load_config(config_path: str = None) -> dict:
    """
    加载配置文件。
    文件不存在或格式错误时自动创建默认配置。
    """
    path = config_path or CONFIG_FILE

    if not os.path.exists(path):
        config = _get_default_config()
        save_config(config, path)
        return config

    try:
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        if not isinstance(config, dict):
            config = _get_default_config()

        # 确保顶层结构完整
        if "devices" not in config or not isinstance(config["devices"], list):
            config["devices"] = []
        if "network_adapters" not in config:
            config["network_adapters"] = {"last_selected_mac": ""}
        if "backups" not in config:
            config["backups"] = {}
        if "ip_history" not in config:
            config["ip_history"] = {}
        if "version" not in config or not isinstance(config.get("version"), int):
            config["version"] = 0

        # 版本化迁移框架
        config = _migrate_config(config)

        # 规范化设置（合并缺失项）
        if "settings" not in config or not isinstance(config.get("settings"), dict):
            config["settings"] = {}
        config["settings"] = _normalize_settings(config["settings"])

        # 规范化每个设备配置（跳过非字典项，避免手工编辑导致的启动崩溃）
        config["devices"] = [
            _normalize_device(d) for d in config["devices"] if isinstance(d, dict)
        ]

        return config

    except yaml.YAMLError:
        # YAML解析失败，备份损坏文件并返回默认配置
        backup_path = f"{path}.corrupted.{datetime.now().strftime('%Y%m%d%H%M%S')}"
        try:
            shutil.copy2(path, backup_path)
        except Exception:
            pass
        config = _get_default_config()
        save_config(config, path)
        return config


def _migrate_config(config: dict) -> dict:
    """
    按 version 字段逐版本迁移配置。
    无 version 的旧配置视为 v0，走全部历史迁移。
    """
    version = config.get("version", 0)

    # 旧版备份格式迁移（所有版本都需要）
    config = _migrate_backup_format(config)

    if version < 1:
        # v0 -> v1：引入 settings 段（结构兼容，无破坏性变更）
        if "settings" not in config or not isinstance(config.get("settings"), dict):
            config["settings"] = {}
        config["version"] = 1

    return config


def _normalize_settings(settings: dict) -> dict:
    """合并默认设置，补充缺失项并保留用户已配置项。"""
    merged = copy.deepcopy(SETTINGS_DEFAULTS)
    if not isinstance(settings, dict):
        return merged
    for key, value in settings.items():
        if key in merged:
            if isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key].update({k: v for k, v in value.items() if k in merged[key]})
            else:
                merged[key] = value
    return merged


def get_setting(config: dict, key: str, default=None):
    """读取设置项，支持点号路径（如 hooks.after_configure）。"""
    if key.startswith("settings."):
        key = key[len("settings."):]
    settings = config.get("settings", {})
    current = settings
    for part in key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default if default is not None else SETTINGS_DEFAULTS.get(key)
    return current


def set_setting(config: dict, key: str, value) -> dict:
    """写入设置项，支持点号路径。"""
    if key.startswith("settings."):
        key = key[len("settings."):]
    settings = config.setdefault("settings", {})
    parts = key.split(".")
    current = settings
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value
    return config


def _migrate_backup_format(config: dict) -> dict:
    """将旧版单备份格式迁移为栈格式"""
    backups = config.get("backups", {})
    migrated = False
    for mac, backup in backups.items():
        if isinstance(backup, dict) and "ip" in backup:
            # 旧格式：单条备份 → 包装为列表
            backups[mac] = [{
                "ip": backup.get("ip", ""),
                "mask": backup.get("mask", ""),
                "gateway": backup.get("gateway", ""),
                "is_dhcp": backup.get("is_dhcp", True),
                "adapter_name": backup.get("adapter_name", ""),
                "timestamp": backup.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            }]
            migrated = True
        elif isinstance(backup, list):
            continue
        else:
            backups[mac] = []
            migrated = True
    if migrated:
        config["backups"] = backups
    return config


def save_config(config: dict, config_path: str = None) -> None:
    """
    将配置写回YAML文件。
    写入失败时抛出异常由调用方处理。
    """
    path = config_path or CONFIG_FILE

    # 确保目录存在
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def get_devices(config: dict, group: str = None) -> list:
    """
    获取设备列表。
    可按分组过滤，不指定 group 则返回所有设备。
    收藏设备优先排序，同收藏级别按名称排序。
    """
    devices = config.get("devices", [])
    if group is not None:
        # 空字符串表示"未分组"设备
        devices = [d for d in devices if d.get("group", "") == group]
    return sorted(devices, key=lambda d: (not d.get("favorite", False), d.get("name", "")))


UNDEFINED_GROUP = "__undefined__"


def get_device_groups(config: dict) -> list:
    """
    获取所有设备分组列表（去重排序）。
    返回示例: ["Site A", "Site B", ""] 空串表示未分组。
    """
    groups = set()
    for d in config.get("devices", []):
        g = d.get("group", "")
        groups.add(g)
    result = sorted(g for g in groups if g)
    if "" in groups:
        result.append("")
    return result


def get_devices_by_group(config: dict) -> dict:
    """
    按分组组织设备。
    返回 {group_name: [devices...]}，group_name="" 表示未分组。
    """
    result = {}
    for d in config.get("devices", []):
        g = d.get("group", "")
        if g not in result:
            result[g] = []
        result[g].append(d)
    # 组内排序
    for g in result:
        result[g] = sorted(result[g], key=lambda d: (not d.get("favorite", False), d.get("name", "")))
    return result


def add_device(config: dict, device: dict) -> dict:
    """添加设备到配置"""
    normalized = _normalize_device(device)
    config["devices"].append(normalized)
    return config


def update_device(config: dict, index: int, device: dict) -> dict:
    """更新指定索引的设备配置"""
    if 0 <= index < len(config["devices"]):
        normalized = _normalize_device(device)
        config["devices"][index] = normalized
    return config


def delete_device(config: dict, index: int) -> dict:
    """删除指定索引的设备"""
    if 0 <= index < len(config["devices"]):
        config["devices"].pop(index)
    return config


def get_last_adapter_mac(config: dict) -> str:
    """获取上次选择的网卡MAC"""
    return config.get("network_adapters", {}).get("last_selected_mac", "")


def set_last_adapter_mac(config: dict, mac: str) -> dict:
    """设置上次选择的网卡MAC"""
    if "network_adapters" not in config:
        config["network_adapters"] = {}
    config["network_adapters"]["last_selected_mac"] = mac
    return config


def get_adapter_backups(config: dict) -> dict:
    """获取所有网卡备份（返回 {mac: [stack]}）"""
    return config.get("backups", {})


def get_adapter_backup(config: dict, mac: str) -> dict:
    """
    获取指定MAC网卡的最新备份（栈顶）。
    返回单条备份记录，无备份返回 None。
    """
    stack = config.get("backups", {}).get(mac, [])
    if not stack:
        return None
    return stack[0]


def get_adapter_backup_stack(config: dict, mac: str) -> list:
    """获取指定MAC网卡的完整备份栈"""
    return list(config.get("backups", {}).get(mac, []))


def save_adapter_backup(config: dict, mac: str, backup: dict) -> dict:
    """
    保存网卡备份到栈顶。
    保留最近 MAX_HISTORY_RECORDS 条备份以支持多级撤销。
    """
    if "backups" not in config:
        config["backups"] = {}
    stack = config["backups"].get(mac, [])
    if "timestamp" not in backup:
        backup["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stack.insert(0, backup)
    if len(stack) > MAX_HISTORY_RECORDS:
        stack = stack[:MAX_HISTORY_RECORDS]
    config["backups"][mac] = stack
    return config


def pop_adapter_backup(config: dict, mac: str) -> tuple:
    """
    弹出栈顶备份并返回。
    返回 (backup_dict, updated_config)，无备份时 backup_dict 为 None。
    """
    stack = config.get("backups", {}).get(mac, [])
    if not stack:
        return (None, config)
    backup = stack.pop(0)
    config["backups"][mac] = stack
    return (backup, config)


def clear_adapter_backup(config: dict, mac: str = None) -> dict:
    """
    清除指定MAC的网卡备份栈。
    不指定 mac 则清除所有备份。
    """
    if mac:
        config.get("backups", {}).pop(mac, None)
    else:
        config["backups"] = {}
    return config


def backup_stack_depth(config: dict, mac: str) -> int:
    """获取指定MAC网卡的备份栈深度"""
    return len(config.get("backups", {}).get(mac, []))


def export_config(config: dict, export_path: str) -> None:
    """导出配置到指定路径"""
    save_config(config, export_path)


def import_config(import_path: str) -> dict:
    """
    从指定路径导入配置。
    先严格校验文件结构与字段类型，避免损坏/非配置文件覆盖现有配置。
    校验通过后按常规流程规范化（缺失字段使用默认值填充）。
    """
    try:
        with open(import_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"配置文件解析失败: {e}") from e
    except OSError as e:
        raise ValueError(f"无法读取文件: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("配置格式无效：顶层必须是映射（字典）")

    devices = data.get("devices", [])
    if not isinstance(devices, list):
        raise ValueError("配置格式无效：devices 必须是列表")
    for i, d in enumerate(devices):
        if not isinstance(d, dict):
            raise ValueError(f"配置格式无效：第 {i + 1} 个设备配置不是映射")

    return load_config(import_path)


def validate_device(device: dict) -> list:
    """
    验证设备配置字段完整性。
    返回错误信息列表，空列表表示验证通过。
    """
    errors = []
    for field in DEVICE_REQUIRED:
        if not device.get(field):
            errors.append(f"缺少必填字段: {field}")

    # 验证IP格式（简单检查）
    if device.get("device_ip"):
        parts = device["device_ip"].split(".")
        if len(parts) != 4:
            errors.append("设备IP格式无效")
        else:
            try:
                nums = [int(p) for p in parts]
                if any(n < 0 or n > 255 for n in nums):
                    errors.append("设备IP格式无效")
            except ValueError:
                errors.append("设备IP格式无效")

    if device.get("ip_mode") == "manual" and not device.get("adapter_ip"):
        errors.append("手动指定模式下，网卡IP不能为空")

    return errors


# ============== IP历史记录管理函数 ==============

def get_ip_history(config: dict, mac: str) -> list:
    """
    获取指定网卡的IP历史记录。
    返回历史记录列表，按时间倒序排列（最新的在前）。
    """
    history = config.get("ip_history", {}).get(mac, [])
    return history


def _make_history_key(ip: str, mask: str, gateway: str) -> str:
    """生成历史记录去重用的唯一键"""
    return f"{ip}|{mask}|{gateway}"


def add_ip_history(config: dict, mac: str, ip: str, mask: str, gateway: str, is_dhcp: bool = False) -> dict:
    """
    添加IP历史记录。
    - 自动去重：相同IP配置合并为一项，更新时间戳
    - 限制最多保存MAX_HISTORY_RECORDS条记录
    - DHCP配置不记录到历史（因为有专门的DHCP选项）
    """
    if is_dhcp:
        # DHCP配置不记录到历史
        return config

    if "ip_history" not in config:
        config["ip_history"] = {}

    history = config["ip_history"].get(mac, [])
    new_record = {
        "ip": ip,
        "mask": mask,
        "gateway": gateway or "",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 去重：检查是否已存在相同配置
    new_key = _make_history_key(ip, mask, gateway or "")
    existing_index = None
    for i, record in enumerate(history):
        existing_key = _make_history_key(
            record.get("ip", ""),
            record.get("mask", ""),
            record.get("gateway", "")
        )
        if existing_key == new_key:
            existing_index = i
            break

    if existing_index is not None:
        # 已存在，更新时间戳并移到最前面
        history.pop(existing_index)
        history.insert(0, new_record)
    else:
        # 不存在，添加到最前面
        history.insert(0, new_record)

    # 限制历史记录数量
    if len(history) > MAX_HISTORY_RECORDS:
        history = history[:MAX_HISTORY_RECORDS]

    config["ip_history"][mac] = history
    return config


def clear_ip_history(config: dict, mac: str = None) -> dict:
    """
    清除IP历史记录。
    - 如果指定MAC，清除该网卡的历史记录
    - 如果不指定MAC，清除所有历史记录
    """
    if mac:
        config.get("ip_history", {}).pop(mac, None)
    else:
        config["ip_history"] = {}
    return config
