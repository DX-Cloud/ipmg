"""
网络设备IP一键配置工具 - 程序入口

默认自动管理员提权、配置加载、TUI主循环；支持常驻后台（托盘 + 全局热键）。

命令行参数：
  （无）                 前台交互（默认）
  --resident            常驻后台：启动托盘与全局热键，并把控制台隐藏起来
  --autostart-min       供开机自启使用：等同于 --resident，附加"最小化启动"语义
  --no-singleton        跳过单实例检查（自动化/测试用，正常使用不要加）
  --selfcheck           环境自检（单实例/热键/托盘/控制台/自启任务），不进入 TUI
"""

import ctypes
import os
import subprocess
import sys

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


def _enable_vt_console():
    """
    启用 Windows 控制台的 VT 转义序列处理。
    必须在 rich Console 初始化之前调用，否则 rich 会判定为 legacy 模式，
    导致自定义 ANSI 控制序列（光标移动/清屏）失效。
    """
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        for handle_id in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


# ---------------------------------------------------------------- 命令行参数
def parse_args(argv=None) -> dict:
    """解析命令行参数（不引入 argparse：参数很少，且要保持零依赖启动速度）。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    flags = {"resident": False, "autostart_min": False,
             "no_singleton": False, "selfcheck": False}
    for arg in argv:
        if arg in ("--resident", "-r"):
            flags["resident"] = True
        elif arg == "--autostart-min":
            flags["resident"] = True
            flags["autostart_min"] = True
        elif arg == "--no-singleton":
            flags["no_singleton"] = True
        elif arg == "--selfcheck":
            flags["selfcheck"] = True
    return flags


# ---------------------------------------------------------------- 自检
def run_selfcheck() -> int:
    """打印环境自检结果，返回进程退出码（0=全部可用，1=有项目不可用）。"""
    from core import autostart
    from core import singleton
    from core.config_manager import CONFIG_FILE, get_setting, load_config

    print("=" * 66)
    print("ipmg 环境自检")
    print("=" * 66)

    frozen = bool(getattr(sys, "frozen", False))
    print(f"Python            : {sys.version.split()[0]} ({'打包运行' if frozen else '源码运行'})")
    print(f"管理员权限        : {'是' if is_admin() else '否（改网卡会失败）'}")

    config = load_config()
    print(f"配置文件          : {CONFIG_FILE}")
    print(f"设备数 / 预设数    : {len(config.get('devices') or [])} / {len(config.get('presets') or [])}")
    print(f"常驻设置          : enabled={get_setting(config, 'settings.resident.enabled', False)}"
          f" autostart={get_setting(config, 'settings.resident.autostart', False)}"
          f" 热键={get_setting(config, 'settings.hotkeys.summon', '')!r}")

    problems = []

    # 单实例：能拿到说明没有别的实例在跑
    got = singleton.acquire()
    if got:
        singleton.release()
        print("单实例互斥体      : 可用")
    else:
        problems.append("单实例互斥体被占用（已有实例在运行？）")
        print("单实例互斥体      : 被占用")

    # 托盘 / 热键 / 控制台：用一次真实的事件线程验证
    try:
        from core.win32host import Win32Host
        host = Win32Host(on_hotkey=lambda _id: None)
        host.start()
        tray = bool(host.set_tray(None, "ipmg 自检"))
        print(f"托盘图标          : {'可用' if tray else '不可用'}")
        if not tray:
            problems.append("托盘图标不可用")

        combo = str(get_setting(config, "settings.hotkeys.summon", "") or "").strip()
        if combo:
            ok, err = host.register_hotkey(100, combo)
            print(f"唤出热键 {combo:<12}: {'可用' if ok else '失败 - ' + err}")
            if not ok:
                problems.append(f"唤出热键不可用: {err}")
            host.unregister_hotkey(100)
        for index, preset in enumerate(config.get("presets") or []):
            hotkey = str(preset.get("hotkey") or "").strip()
            if not hotkey:
                continue
            ok, err = host.register_hotkey(200 + index, hotkey)
            print(f"预设热键 {hotkey:<12}: {'可用' if ok else '失败 - ' + err}")
            if not ok:
                problems.append(f"预设热键 {hotkey} 不可用: {err}")
            host.unregister_hotkey(200 + index)

        hidden = bool(host.hide_console())
        if hidden:
            host.show_console()
        print(f"隐藏控制台        : {'可用' if hidden else '不可用（Windows Terminal/ConPTY 下正常，将退化为最小化）'}")
        host.remove_tray()
        host.stop()
    except Exception as e:
        problems.append(f"Win32 事件线程不可用: {e}")
        print(f"Win32 事件线程    : 失败 - {e}")

    status = autostart.status()
    print(f"开机自启(计划任务) : supported={status.get('supported')} exists={status.get('exists')}")
    print(f"自启命令行        : {status.get('command')}")
    if status.get("error"):
        print(f"自启状态读取错误   : {status['error']}")

    print("-" * 66)
    if problems:
        print("自检发现问题：")
        for item in problems:
            print(f"  - {item}")
        print("=" * 66)
        return 1
    print("自检全部通过")
    print("=" * 66)
    return 0


def main() -> int:
    """主入口"""
    flags = parse_args()

    if flags["selfcheck"]:
        return run_selfcheck()

    # 单实例检查必须放在提权之前：否则第二个实例会白弹一次 UAC 才退出
    from core import singleton
    if not flags["no_singleton"]:
        if not singleton.acquire():
            singleton.signal_existing()      # 通知既有实例把窗口唤到前台
            print("[i] ipmg 已在运行，已请求既有窗口显示（热键可随时唤出）。")
            return 0

    # 先启用 VT（必须在 rich Console 初始化前）
    _enable_vt_console()

    # 管理员权限检测 - 自动提权
    if not is_admin():
        if not run_as_admin():
            # 提权失败或用户取消，仍然继续运行（部分功能可能不可用）
            print("[!] 警告: 管理员提权失败，部分功能可能不可用")
            input("按回车键继续...")

    # 导入模块（延迟导入，加快启动感知速度）
    from core.config_manager import get_setting, load_config, save_config, set_setting
    from ui.app import show_main_menu
    from ui.actions import find_action
    from utils.logger import log_info, log_error

    log_info("程序启动")

    # 加载配置
    config = load_config()

    # 常驻：命令行参数优先，其次看设置
    resident_mode = flags["resident"] or bool(get_setting(config, "settings.resident.enabled", False))
    if resident_mode:
        _start_resident(config, flags)

    # 后台版本检测（可配置，不阻塞主流程）
    try:
        from core.update_check import default_checker
        if get_setting(config, "settings.update_check.enabled", True):
            default_checker.start()
    except Exception:
        pass

    # 主循环
    while True:
        try:
            action = show_main_menu(config)

            if action == "exit":
                log_info("程序退出")
                break

            # 记忆上次动作（主菜单下次默认高亮）
            try:
                set_setting(config, "settings.last_action", action)
            except Exception:
                pass

            entry = find_action(action)
            if entry is None:
                continue
            config = entry.handler(config)
            if entry.needs_save:
                try:
                    save_config(config)
                except Exception as e:
                    log_error("保存配置", str(e))
                    print(f"\n发生错误: {e}")
                    input("按回车键继续...")

        except KeyboardInterrupt:
            print("\n\n已退出")
            break
        except Exception as e:
            log_error("主循环", str(e))
            print(f"\n发生错误: {e}")
            input("按回车键继续...")

    # 退出时收掉常驻资源（托盘/热键/事件线程）
    from ui import resident as resident_module
    controller = resident_module.get_controller()
    if controller is not None:
        controller.stop()
        resident_module.set_controller(None)
    singleton.release()
    return 0


def _start_resident(config: dict, flags: dict) -> None:
    """启动常驻控制器，并按需隐藏控制台。"""
    from core.config_manager import get_setting
    from ui import resident as resident_module

    controller = resident_module.ResidentController(config)
    ok, message = controller.start()
    print(f"[常驻] {message}")
    if not ok:
        return
    resident_module.set_controller(controller)

    for combo, err in controller.hotkey_errors:
        print(f"[常驻][!] 热键 {combo} 注册失败: {err}")

    summon = get_setting(config, "settings.hotkeys.summon", "") or "（未设置）"
    print(f"[常驻] 唤出/收起热键: {summon}；托盘左键同样可唤出。")

    if not bool(get_setting(config, "settings.resident.close_to_tray", True)):
        print("[常驻] 提示: 设置里的“关闭窗口时保留常驻”已关闭，退出程序将结束常驻。")

    if controller.hide_console():
        print("[常驻] 控制台已隐藏（按唤出热键或点托盘图标可再次显示）。")
    else:
        print("[常驻] 当前终端不支持隐藏控制台（Windows Terminal/ConPTY），已保留窗口。")


if __name__ == "__main__":
    sys.exit(main())
