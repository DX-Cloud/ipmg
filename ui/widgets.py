"""
统一交互组件
提供选择菜单（分页/过滤/键盘导航/复制）、确认对话框、剪贴板工具。
TUI 各页面共用，避免 _pick_option 在多处重复维护。
"""

import math
import shutil
import sys
from typing import Callable, Dict, List, Optional, Sequence, Set

from rich.console import Console
from rich.text import Text

console = Console()


def copy_to_clipboard(text: str) -> bool:
    """复制文本到剪贴板（win32clipboard，pywin32 依赖）。"""
    if not text:
        return False
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(str(text))
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False


def confirm(prompt: str, default: bool = True) -> bool:
    """
    统一确认对话框。
    - default=True 时提示 (Y/n)，回车确认
    - default=False 时提示 (y/N)，回车拒绝
    - 仅接受 y/yes/n/no（大小写不敏感），空输入使用默认值
    """
    hint = "(Y/n)" if default else "(y/N)"
    while True:
        try:
            raw = input(f"{prompt} {hint}: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            return False
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        console.print("[red]请输入 y 或 n[/red]")


def _render(title: str, options: Sequence[str], start: int, end: int,
            page_size: int, current_page: int, total_pages: int,
            fixed_tail: Sequence[str], allow_back: bool,
            hotkeys: Optional[Dict[str, int]], note_fn: Optional[Callable[[], str]],
            non_selectable: Set[int], cursor: Optional[tuple] = None,
            default_index: Optional[int] = None) -> List[str]:
    """
    构建一页菜单的文本行（含 rich 标记）。
    cursor 仅在键盘导航模式提供，为 (type, payload)：
      ("opt", 选项索引) / ("fixed", 固定尾部索引) / ("back", None)
    """
    lines: List[str] = [""]
    note = note_fn() if note_fn else ""
    title_line = f"{title}{('  [' + note + ']') if note else ''}"
    lines.append(f"[bold cyan]{title_line}[/bold cyan]")
    lines.append("-" * 55)

    cursor_type, cursor_payload = (cursor or (None, None))
    fixed_base = min(start + page_size, len(options))
    for i in range(start, end):
        opt = options[i]
        num = i + 1
        if i in non_selectable:
            lines.append(f"   {opt}")
            continue
        if cursor_type == "opt" and cursor_payload == i:
            marker = " > "
        elif default_index is not None:
            marker = " > " if i == default_index else "   "
        elif cursor_type is not None:
            marker = "   "  # 键盘模式：非当前项保持占位对齐
        else:
            marker = ""
        lines.append(f"{marker}{num}. {opt}")

    if total_pages > 1:
        bottom = []
        if current_page > 0:
            bottom.append("↑ 上一页(n)")
        if current_page < total_pages - 1:
            bottom.append("↓ 下一页(p)")
        lines.append(f"  [dim]{' | '.join(bottom)} (第{current_page + 1}/{total_pages}页)[/dim]")

    for i, ft in enumerate(fixed_tail):
        marker = " > " if cursor_type == "fixed" and cursor_payload == i else "   "
        lines.append(f"{marker}{fixed_base + i + 1}. {ft}")

    if allow_back:
        marker = " > " if cursor_type == "back" else "   "
        lines.append(f"{marker}0. <-- 返回")
    if hotkeys:
        keys = " ".join(f"{k}={hotkeys[k] + 1}" for k in sorted(hotkeys))
        lines.append(f"  [dim]快捷键: {keys}[/dim]")
    if cursor is not None:
        lines.append("  [dim]↑/↓ 或 j/k 移动, 回车确认, c 复制, f 过滤[/dim]")
    lines.append("-" * 55)
    return lines


def _print_lines(lines: Sequence[str]) -> None:
    """一次性打印整帧（单次终端写入，避免逐行闪烁）。"""
    console.print("\n".join(lines))


def _line_cell_len(line: str) -> int:
    """计算一行文本在终端中的显示宽度（含中文字符）。"""
    try:
        return Text.from_markup(line).cell_len
    except Exception:
        return len(line)


def _terminal_width() -> int:
    try:
        return shutil.get_terminal_size().columns or 80
    except Exception:
        return 80


def _measure_rows(lines: Sequence[str]) -> int:
    """估算一组文本行占用的终端行数（含自动换行）。"""
    width = _terminal_width()
    rows = 0
    for line in lines:
        rows += max(1, math.ceil(_line_cell_len(line) / width))
    return rows


def _frame_text(lines: Sequence[str]) -> str:
    """将文本行渲染为终端输出文本（含 rich 样式码），不直接写入。"""
    console.begin_capture()
    try:
        console.print("\n".join(lines))
    finally:
        capture = console.end_capture()
    return str(capture)


def _clear_and_redraw(lines: Sequence[str], prev_lines: Sequence[str]) -> None:
    """
    键盘模式原地刷新。
    将"光标上移 + 清空旧区域 + 整个新帧"合并为一次终端写入，
    避免清屏与重绘之间存在可见空白导致闪烁。
    """
    if prev_lines:
        rows = _measure_rows(prev_lines)
        prefix = f"\r\x1b[{rows}A\x1b[J"
    else:
        prefix = ""
    text = _frame_text(lines)
    try:
        console.file.write(prefix + text)
        console.file.flush()
    except Exception:
        pass


def _page_nav(choice: str, current_page: int, total_pages: int) -> Optional[int]:
    """翻页：返回新页码或 None。"""
    if choice.lower() in ("n", "next", ">", "."):
        return min(current_page + 1, total_pages - 1) if current_page < total_pages - 1 else None
    if choice.lower() in ("p", "prev", "<", ","):
        return max(current_page - 1, 0) if current_page > 0 else None
    return None


def pick_option(options: List[str], title: str, default_index: int = 0,
                allow_back: bool = True, page_size: int = 9,
                fixed_tail: Optional[List[str]] = None,
                non_selectable: Optional[Set[int]] = None,
                hotkeys: Optional[Dict[str, int]] = None,
                filterable: bool = False,
                filter_fn: Optional[Callable[[str], List[str]]] = None,
                filter_meta: Optional[Callable[[str], None]] = None,
                note_fn: Optional[Callable[[], str]] = None,
                copy_text: Optional[Callable[[int], str]] = None) -> int:
    """
    统一数字选择菜单。
    - 返回 -1 表示返回；0..total-1 为选项索引；total+i 为固定尾部选项 i
    - non_selectable: 不可选中的选项索引集合（如分组标题）
    - hotkeys: {字母: 选项索引} 快捷键映射
    - filterable: 允许输入非数字关键字过滤（需提供 filter_fn 重建选项列表）
    - copy_text: 输入 c<编号> 时调用，返回要复制的文本
    - Windows 交互式终端下支持 ↑/↓、j/k 键盘导航
    """
    if not options:
        return -1

    non_selectable = non_selectable or set()
    fixed_tail = fixed_tail or []
    hotkeys = hotkeys or {}
    total = len(options)

    if _keyboard_available():
        return _keyboard_pick(
            options, title, default_index, allow_back, page_size, fixed_tail,
            non_selectable, hotkeys, filterable, filter_fn, filter_meta,
            note_fn, copy_text,
        )

    return _plain_pick(
        options, title, default_index, allow_back, page_size, fixed_tail,
        non_selectable, hotkeys, filterable, filter_fn, filter_meta,
        note_fn, copy_text,
    )


def _plain_pick(options: List[str], title: str, default_index: int,
                allow_back: bool, page_size: int, fixed_tail: List[str],
                non_selectable: Set[int], hotkeys: Dict[str, int],
                filterable: bool, filter_fn: Optional[Callable],
                filter_meta: Optional[Callable],
                note_fn: Optional[Callable], copy_text: Optional[Callable]) -> int:
    total_pages = max(1, (len(options) + page_size - 1) // page_size)
    current_page = 0

    while True:
        start = current_page * page_size
        end = min(start + page_size, len(options))
        fixed_base = min(start + page_size, len(options))
        _print_lines(_render(title, options, start, end, page_size, current_page,
                             total_pages, fixed_tail, allow_back, hotkeys, note_fn,
                             non_selectable, default_index=default_index))

        max_valid = max(len(options), fixed_base + len(fixed_tail))
        prompt = f"请输入序号 (1-{max_valid}"
        if allow_back:
            prompt += ", 0=返回"
        if filterable:
            prompt += ", 关键字=过滤"
        if hotkeys:
            prompt += f", {'/'.join(sorted(hotkeys))}=快捷键"
        prompt += "): "
        try:
            choice = input(prompt).strip()
        except (KeyboardInterrupt, EOFError):
            return -1

        if not choice:
            return default_index if 0 <= default_index < len(options) else 0
        if allow_back and choice == "0":
            return -1

        if choice.lower() in hotkeys:
            return hotkeys[choice.lower()]

        if choice.lower() in ("n", "next", ">", "."):
            if current_page < total_pages - 1:
                current_page += 1
            continue
        if choice.lower() in ("p", "prev", "<", ","):
            if current_page > 0:
                current_page -= 1
            continue

        # 复制：c<编号>
        if choice[:1].lower() == "c" and choice[1:].isdigit() and copy_text:
            target = int(choice[1:]) - 1
            text = copy_text(target)
            if copy_to_clipboard(text):
                console.print(f"[green][OK] 已复制: {text}[/green]")
            else:
                console.print("[yellow][!] 复制失败（文本: %s）[/yellow]" % (text or ""))
            continue

        # 过滤：非数字输入且未命中其他分支
        if filterable and not choice.isdigit() and filter_fn:
            filtered = filter_fn(choice)
            if filtered is None or not filtered:
                console.print(f"[yellow][!] 无匹配结果（关键字: {choice}）[/yellow]")
                continue
            options[:] = filtered
            if filter_meta:
                filter_meta(choice)
            total_pages = max(1, (len(options) + page_size - 1) // page_size)
            current_page = 0
            console.print(f"[green]匹配 {len(options)} 条（关键字: {choice}）[/green]")
            continue

        try:
            idx = int(choice) - 1
            if idx in non_selectable:
                console.print("[red]该项不可选择[/red]")
                continue
            if idx >= fixed_base and idx < fixed_base + len(fixed_tail):
                return len(options) + (idx - fixed_base)
            if 0 <= idx < len(options):
                return idx
            console.print(f"[red]无效输入，请输入 1-{max_valid}[/red]")
        except ValueError:
            if filterable:
                console.print("[yellow]请输入数字序号、关键字或 c<编号>[/yellow]")
            else:
                console.print("[red]请输入数字[/red]")


def _keyboard_available() -> bool:
    """
    键盘导航仅用于 Windows 交互式终端，且要求 VT 转义序列可用
    （原地刷新依赖 ANSI 光标移动/清屏，否则回退普通输入模式）。
    """
    if sys.platform != "win32":
        return False
    try:
        return bool(sys.stdin.isatty()) and _vt_enabled()
    except Exception:
        return False


def _vt_enabled() -> bool:
    """确认（并尝试启用）控制台 VT 处理。"""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if not (mode.value & 0x0004):  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        return bool(mode.value & 0x0004)
    except Exception:
        return False


def _read_key(msvcrt) -> str:
    """读取一个按键，方向键归一化为 up/down/left/right。"""
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        second = msvcrt.getwch()
        return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(second, second)
    if ch == "\r":
        return "enter"
    if ch == "\x1b":
        return "escape"
    if ch == "\x08":
        return "backspace"
    return ch


def _keyboard_pick(options: List[str], title: str, default_index: int,
                   allow_back: bool, page_size: int, fixed_tail: List[str],
                   non_selectable: Set[int], hotkeys: Dict[str, int],
                   filterable: bool, filter_fn: Optional[Callable],
                   filter_meta: Optional[Callable], note_fn: Optional[Callable],
                   copy_text: Optional[Callable]) -> int:
    import msvcrt

    total_pages = max(1, (len(options) + page_size - 1) // page_size)
    current_page = 0
    cursor_pos = 0
    number_buf = ""
    printed: List[str] = []  # 当前屏幕上的全部文本行（用于原地刷新）

    while True:
        start = current_page * page_size
        end = min(start + page_size, len(options))
        # 当前页可导航条目：本页选项 + 固定尾部 + 返回
        entries: List[tuple] = [("opt", i) for i in range(start, end)]
        entries += [("fixed", i) for i in range(len(fixed_tail))]
        if allow_back:
            entries.append(("back", None))

        # 光标落在当前页第一个可选项上
        if current_page == 0 and not printed:
            target = default_index if 0 <= default_index < len(options) else 0
            target = max(start, min(target, end - 1))
            cursor_pos = target - start
        if cursor_pos >= len(entries):
            cursor_pos = max(0, len(entries) - 1)
        while cursor_pos < len(entries) and entries[cursor_pos][0] == "opt" \
                and entries[cursor_pos][1] in non_selectable:
            cursor_pos += 1
        if cursor_pos >= len(entries):
            cursor_pos = 0

        frame = _render(title, options, start, end, page_size, current_page,
                        total_pages, fixed_tail, allow_back, hotkeys, note_fn,
                        non_selectable, cursor=entries[cursor_pos])
        if number_buf:
            frame.append(f"[dim]已输入: {number_buf}（回车跳转）[/dim]")
        _clear_and_redraw(frame, printed)
        printed = list(frame)

        key = _read_key(msvcrt)

        if key in ("up", "k", "K"):
            if cursor_pos > 0:
                new = cursor_pos - 1
                while new > 0 and entries[new][0] == "opt" \
                        and entries[new][1] in non_selectable:
                    new -= 1
                cursor_pos = new
        elif key in ("down", "j", "J"):
            if cursor_pos < len(entries) - 1:
                new = cursor_pos + 1
                while new < len(entries) - 1 and entries[new][0] == "opt" \
                        and entries[new][1] in non_selectable:
                    new += 1
                cursor_pos = new
        elif key == "enter":
            if number_buf and number_buf.isdigit():
                entered = number_buf
                idx = int(entered) - 1
                number_buf = ""
                if idx in non_selectable:
                    msg = "[red]该项不可选择[/red]"
                    _print_lines([msg])
                    printed = printed + [msg]
                    continue
                fixed_base = min(start + page_size, len(options))
                if idx >= fixed_base and idx < fixed_base + len(fixed_tail):
                    return len(options) + (idx - fixed_base)
                if 0 <= idx < len(options):
                    return idx
                msg = f"[red]无效序号 {entered}[/red]"
                _print_lines([msg])
                printed = printed + [msg]
                continue
            etype, payload = entries[cursor_pos]
            if etype == "back":
                return -1
            if etype == "fixed":
                return len(options) + payload
            return payload
        elif key == "escape" or (key == "0" and allow_back):
            return -1
        elif key in ("n", "N"):
            if current_page < total_pages - 1:
                current_page += 1
                cursor_pos = 0
        elif key in ("p", "P"):
            if current_page > 0:
                current_page -= 1
                cursor_pos = 0
        elif key == "backspace":
            number_buf = number_buf[:-1]
        elif key in ("c", "C") and copy_text:
            etype, payload = entries[cursor_pos]
            copy_idx = payload if etype == "opt" else 0
            text = copy_text(copy_idx)
            if copy_to_clipboard(text):
                msg = f"[green][OK] 已复制: {text}[/green]"
            else:
                msg = "[yellow][!] 复制失败[/yellow]"
            _print_lines([msg])
            printed = printed + [msg]
        elif key in ("f", "F") and filterable and filter_fn:
            prompt_line = "[cyan]输入过滤关键字（回车确认，Esc 取消）:[/cyan]"
            _print_lines([prompt_line])
            printed = printed + [prompt_line]
            keyword = ""
            while True:
                k = _read_key(msvcrt)
                if k == "enter":
                    break
                if k == "escape":
                    keyword = ""
                    break
                if k == "backspace":
                    keyword = keyword[:-1]
                elif len(k) == 1 and k.isprintable():
                    keyword += k
                console.print(f"\r\x1b[2K  [dim]关键字: {keyword}[/dim]", end="")
                console.file.flush()
            console.print()
            printed += [f"  [dim]关键字: {keyword}[/dim]"]
            if keyword:
                filtered = filter_fn(keyword)
                if filtered is None or not filtered:
                    msg = f"[yellow][!] 无匹配结果（关键字: {keyword}）[/yellow]"
                    _print_lines([msg])
                    printed = printed + [msg]
                else:
                    options[:] = filtered
                    if filter_meta:
                        filter_meta(keyword)
                    total_pages = max(1, (len(options) + page_size - 1) // page_size)
                    current_page = 0
                    cursor_pos = 0
                    number_buf = ""
                    msg = f"[green]匹配 {len(options)} 条（关键字: {keyword}）[/green]"
                    _print_lines([msg])
                    printed = printed + [msg]
        elif key in hotkeys:
            return hotkeys[key]
        elif key.isdigit():
            number_buf += key
            if len(number_buf) > 4:
                number_buf = number_buf[-4:]
