"""
网络设备IP一键配置工具 - 程序入口

默认自动管理员提权、配置加载、TUI主循环；支持常驻后台（托盘 + 全局热键）。

命令行参数：
  （无）                 前台交互（默认）
  --resident            常驻后台：启动托盘与全局热键，并把控制台隐藏起来
  --autostart-min       供开机自启使用：等同于 --resident，但不打印启动横幅
  --no-singleton        跳过单实例检查（自动化/测试用，正常使用不要加）
  --selfcheck           环境自检（单实例/热键/托盘/控制台/自启任务），不进入 TUI
"""

import ctypes
import os
import subprocess
import sys
import time

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
USAGE = """ipmg - 网络设备IP一键配置工具

用法:
  ipmg [选项]

选项:
  -r, --resident       常驻后台（托盘图标 + 全局热键），启动后隐藏控制台
      --autostart-min  供开机自启任务使用：等价于 --resident，但不打印启动横幅
      --no-singleton   跳过多实例检查（调试用）
      --selfcheck      打印环境自检结果后退出（不需要管理员）
  -h, --help           显示本帮助并退出
  -V, --version        显示版本号并退出

示例:
  ipmg                 启动 TUI（需要管理员权限，会自动请求 UAC 提权）
  ipmg --resident      常驻后台，Ctrl+Alt+I 唤出/收起
  ipmg --selfcheck     检查单实例/托盘/热键/控制台隐藏/自启任务是否可用
"""


def parse_args(argv=None) -> dict:
    """
    解析命令行参数（不引入 argparse：参数很少，且要保持零依赖启动速度）。

    返回字典：resident / autostart_min / no_singleton / selfcheck / help / version / error。
    未知的 ``-`` 开头选项写入 ``error``（由 main 决定如何提示），位置参数忽略。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    flags = {"resident": False, "autostart_min": False, "no_singleton": False,
             "selfcheck": False, "help": False, "version": False, "error": ""}
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
        elif arg in ("--help", "-h", "/?"):
            flags["help"] = True
        elif arg in ("--version", "-V"):
            flags["version"] = True
        elif arg.startswith("-"):
            flags["error"] = f"未知选项: {arg}"
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
            if not preset.get("enabled"):
                print(f"预设热键 {hotkey:<12}: 跳过（该预设未启用，热键不会注册）")
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


def _ask_exit_resident(config: dict) -> str:
    """
    常驻状态下点"退出"时询问意图。
    返回 "tray"（最小化到托盘继续常驻）/ "quit"（彻底退出）/ "cancel"（什么也不做）。
    默认项由设置 settings.resident.close_to_tray 决定。
    """
    from core.config_manager import get_setting
    from ui import widgets

    close_to_tray = bool(get_setting(config, "settings.resident.close_to_tray", True))
    options = [
        "最小化到托盘（继续常驻，随时可用热键唤出）",
        "完全退出（结束常驻，托盘图标消失）",
    ]
    idx = widgets.pick_option(
        options,
        "程序正在后台常驻，请选择退出方式",
        default_index=0 if close_to_tray else 1,
    )
    if idx < 0:
        return "cancel"
    return "tray" if idx == 0 else "quit"


# 控制台控制处理器必须保持引用，否则会被垃圾回收导致回调失效
_CTRL_HANDLER = None


def _install_console_close_handler(on_close) -> None:
    """
    注册控制台控制处理器：点窗口 X（CTRL_CLOSE_EVENT）、注销、关机时做清理。

    注意：控制台程序**无法阻止**系统在收到 CTRL_CLOSE_EVENT 后结束进程，
    这里只能保证离开时把托盘图标与热键干净地摘掉（避免残留"幽灵图标"）。
    常驻模式下控制台默认是隐藏的，正常不会点到 X；要退出请在菜单里选"完全退出"。
    """
    global _CTRL_HANDLER
    if sys.platform != "win32":
        return
    try:
        handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
        ctrl_close, ctrl_logoff, ctrl_shutdown = 2, 5, 6

        def _handler(event):
            if event in (ctrl_close, ctrl_logoff, ctrl_shutdown):
                try:
                    on_close()
                except Exception:
                    pass
                return True
            return False

        _CTRL_HANDLER = handler_type(_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_CTRL_HANDLER, True)
    except Exception:
        _CTRL_HANDLER = None


def main() -> int:
    """主入口"""
    flags = parse_args()

    if flags["error"]:
        print(flags["error"], file=sys.stderr)
        print("运行 ipmg --help 查看可用选项。", file=sys.stderr)
        return 2

    if flags["help"]:
        print(USAGE)
        return 0

    if flags["version"]:
        from core.version import APP_VERSION
        print(f"ipmg {APP_VERSION}")
        return 0

    if flags["selfcheck"]:
        return run_selfcheck()

    # 单实例检查必须放在提权之前：否则第二个实例会白弹一次 UAC 才退出
    from core import singleton
    if not flags["no_singleton"]:
        if not singleton.acquire():
            # 通知既有实例把窗口唤到前台（既有实例的唤出监听线程会收到）
            if singleton.signal_existing():
                print("[i] ipmg 已在运行，已请求既有实例显示窗口。")
            else:
                print("[i] ipmg 已在运行（未能通知既有实例显示窗口，可用唤出热键）。")
            return 0
        # 本进程成为唯一实例：开始监听"唤出"信号
        _start_summon_watcher()

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
    resident_controller = None
    if resident_mode:
        resident_controller = _start_resident(config, flags)
        if resident_controller is not None:
            # 点 X / 注销 / 关机时把托盘图标与热键摘干净，避免残留幽灵图标
            _install_console_close_handler(_stop_resident)

    # 后台版本检测（可配置，不阻塞主流程）
    try:
        from core.update_check import UpdateChecker, default_checker
        if get_setting(config, "settings.update_check.enabled", True):
            custom_url = str(get_setting(config, "settings.update_check.url", "") or "").strip()
            # 设置里填了镜像地址就只查它，留空则用内置源
            (UpdateChecker(urls=[custom_url]) if custom_url else default_checker).start()
    except Exception:
        pass

    # 主循环
    while True:
        try:
            action = show_main_menu(config)

            if action == "exit":
                # 常驻状态下"退出"默认是最小化到托盘，而不是结束进程
                if resident_controller is not None and resident_controller.running:
                    decision = _ask_exit_resident(config)
                    if decision == "cancel":
                        continue
                    if decision == "tray":
                        if resident_controller.hide_console():
                            print("[常驻] 已最小化到托盘，程序继续在后台运行（热键或托盘图标可唤出）。")
                        else:
                            print("[常驻] 当前终端无法隐藏窗口，程序仍在后台常驻；"
                                  "再次选择退出可完全结束。")
                        log_info("最小化到托盘")
                        continue
                    # decision == "quit"：继续往下走，彻底退出
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
    _stop_resident()
    singleton.release()
    return 0


def _stop_resident() -> None:
    """收掉常驻资源：移除托盘图标、注销热键、结束事件线程。"""
    from ui import resident as resident_module
    controller = resident_module.get_controller()
    if controller is not None:
        try:
            controller.stop()
        except Exception:
            pass
        resident_module.set_controller(None)


def _start_summon_watcher() -> None:
    """
    监听"唤出"事件的后台线程。

    第二个实例启动时无法抢占单实例互斥体，会调用 ``singleton.signal_existing()``
    置位命名事件；本线程收到后把本进程的窗口显示并前置，同时唤醒 TUI 重绘。
    常驻实例额外走控制器的 show_console（隐藏的控制台窗口同样能恢复）。
    """
    import threading

    def _loop():
        from core import singleton
        from core.win32host import raise_console
        from ui import console as uconsole

        while True:
            try:
                if not singleton.wait_activate("ipmg", 500):
                    continue
                from ui import resident as resident_module
                controller = resident_module.get_controller()
                if controller is not None:
                    controller.show_console()
                else:
                    raise_console()
                uconsole.post_event(uconsole.EVENT_SUMMON)
                uconsole.wake()
            except Exception:
                # 监听线程不允许把主进程拖垮：静默退避后继续
                time.sleep(0.5)

    threading.Thread(target=_loop, name="ipmg-summon", daemon=True).start()


def _start_resident(config: dict, flags: dict):
    """启动常驻控制器，并按需隐藏控制台。成功返回控制器，失败返回 None。"""
    from core.config_manager import get_setting
    from ui import resident as resident_module
    from utils.logger import log_error

    quiet = bool(flags.get("autostart_min"))   # 开机自启场景不打印启动横幅

    def _say(text: str) -> None:
        if not quiet:
            print(text)

    controller = resident_module.ResidentController(config)
    ok, message = controller.start()
    _say(f"[常驻] {message}")
    if not ok:
        return None
    resident_module.set_controller(controller)

    for combo, err in controller.hotkey_errors:
        _say(f"[常驻][!] 热键 {combo} 注册失败: {err}")
        log_error("注册热键", f"{combo}: {err}")
    for name, why in controller.skipped_hotkeys:
        _say(f"[常驻][i] {name} 绑定了热键但未生效：{why}（到「快捷预设」里启用后即可用）")

    summon = get_setting(config, "settings.hotkeys.summon", "") or "（未设置）"
    _say(f"[常驻] 唤出/收起热键: {summon}；托盘左键同样可唤出。")
    if bool(get_setting(config, "settings.notify.apply_requires_confirm", False)):
        _say("[常驻] 已开启「应用前需要二次确认」：按预设热键会先弹出确认框。")

    if controller.hide_console():
        _say("[常驻] 控制台已隐藏（按唤出热键或点托盘图标可再次显示）。")
    else:
        _say("[常驻] 当前终端不支持隐藏控制台（Windows Terminal/ConPTY），已保留窗口；"
             "关闭该窗口会直接结束进程。")
    return controller


if __name__ == "__main__":
    sys.exit(main())
