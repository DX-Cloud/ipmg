"""
设置界面
集中管理交互偏好：自动打开管理页、虚拟网卡过滤、动作钩子等。
设置项通过 config.yaml 的 settings 段持久化。
"""

from typing import Callable

from core import config_manager as cm
from ui import widgets
from ui.header import show_header


# (设置键, 显示名, 类型)  类型: bool -> 回车切换；str -> 输入新值
SETTING_ITEMS = [
    ("settings.auto_open_page", "配置成功后自动打开管理页面", bool),
    ("settings.filter_virtual_adapters", "网卡列表默认过滤虚拟网卡", bool),
    ("settings.hooks.after_configure", "配置成功后执行命令（钩子）", str),
    ("settings.hooks.after_restore", "恢复成功后执行命令（钩子）", str),
]


def _value_text(config: dict, key: str, value_type: type) -> str:
    value = cm.get_setting(config, key)
    if value_type is bool:
        return "开启" if value else "关闭"
    return str(value) if value else "（空）"


def show_settings(config: dict) -> dict:
    """设置管理主界面，返回更新后的配置。"""
    while True:
        show_header(config)
        options = [
            f"{label}: {_value_text(config, key, vtype)}"
            for key, label, vtype in SETTING_ITEMS
        ]

        idx = widgets.pick_option(
            options,
            "设置 - 请选择要修改的项（0 返回）",
            default_index=0,
            fixed_tail=["[?] 帮助说明"],
        )
        if idx < 0:
            return config
        if idx >= len(options):
            _show_help()
            continue

        key, label, vtype = SETTING_ITEMS[idx]
        if vtype is bool:
            current = bool(cm.get_setting(config, key))
            if widgets.confirm(f"{label}（当前: {'开启' if current else '关闭'}）切换为 {'关闭' if current else '开启'}?", default=False):
                cm.set_setting(config, key, not current)
                print("[OK] 已更新")
        else:
            current = str(cm.get_setting(config, key) or "")
            new_value = input(f"{label} [当前: {current or '空'}]（回车保持不变，输入 clear 清空）: ").strip()
            if new_value.lower() == "clear":
                cm.set_setting(config, key, "")
                print("[OK] 已清空")
            elif new_value:
                cm.set_setting(config, key, new_value)
                print("[OK] 已更新")

        cm.save_config(config)


def _show_help():
    print()
    print("设置说明：")
    print("  auto_open_page       : 配置 IP 成功后直接打开设备管理页面，不再询问")
    print("  filter_virtual_adapters : 网卡列表默认隐藏 VMware/Hyper-V/WSL/蓝牙等虚拟网卡")
    print("  钩子命令             : 配置/恢复成功后执行的命令，例如")
    print("                         powershell -Command \"Add-Type -AssemblyName System.Windows.Forms; ...\"")
    print("                         （留空表示不执行）")
    print()
    input("按回车键返回...")
