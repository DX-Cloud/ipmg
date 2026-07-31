"""
网络设备IP一键配置工具 - 程序入口
默认自动管理员提权、配置加载、TUI主循环
"""

import sys
import os
import ctypes
import subprocess

# 设置控制台为 UTF-8 编码
if sys.platform == "win32":
    try:
        os.system("chcp 65001 >nul 2>&1")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def is_admin() -> bool:
    """检测是否以管理员权限运行"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_as_admin():
    """以管理员权限重新启动程序"""
    try:
        if getattr(sys, "frozen", False):
            # PyInstaller 打包后的 exe：可执行文件即程序本体，参数只传原始 argv
            exe = sys.executable
            params = subprocess.list2cmdline(sys.argv[1:])
        else:
            # 源码运行：先传 python.exe，再传带引号的脚本路径和参数
            exe = sys.executable
            script = os.path.abspath(sys.argv[0])
            params = f'"{script}"'
            if sys.argv[1:]:
                params += " " + subprocess.list2cmdline(sys.argv[1:])

        # ShellExecuteW 返回值 > 32 表示成功；<= 32（如用户取消 UAC）视为失败
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        if result <= 32:
            return False
        # 提权成功，父进程退出，由新的管理员进程接管
        sys.exit(0)
    except Exception:
        return False


def main():
    """主入口"""
    # 管理员权限检测 - 自动提权
    if not is_admin():
        if not run_as_admin():
            # 提权失败或用户取消，仍然继续运行（部分功能可能不可用）
            print("[!] 警告: 管理员提权失败，部分功能可能不可用")
            input("按回车键继续...")

    # 导入模块（延迟导入，加快启动感知速度）
    from core.config_manager import load_config, save_config
    from ui.app import show_main_menu, run_configure_flow, run_restore_flow
    from ui.app import run_export_import_flow
    from ui.device_manager import show_device_manager
    from utils.logger import log_info

    log_info("程序启动")

    # 加载配置
    config = load_config()

    # 主循环
    while True:
        try:
            action = show_main_menu(config)

            if action == "configure":
                config = run_configure_flow(config)
            elif action == "restore":
                config = run_restore_flow(config)
            elif action == "manage":
                config = show_device_manager(config)
                save_config(config)
            elif action == "export":
                config = run_export_import_flow(config, "export")
            elif action == "import":
                config = run_export_import_flow(config, "import")
            elif action == "exit":
                log_info("程序退出")
                break

        except KeyboardInterrupt:
            print("\n\n已退出")
            break
        except Exception as e:
            from utils.logger import log_error
            log_error("主循环", str(e))
            print(f"\n发生错误: {e}")
            input("按回车键继续...")


if __name__ == "__main__":
    main()
