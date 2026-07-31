"""
统一交互组件
提供选择菜单（分页/过滤/键盘导航/复制）、确认对话框、剪贴板工具。
TUI 各页面共用，避免 _pick_option 在多处重复维护。
"""

import sys
from typing import Callable, Dict, List, Optional, Sequence, Set

from rich.console import Console

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
            non_selectable: Set[int], cursor: Optional[int] = None,
            default_index: Optional[int] = None):
    """渲染一页菜单。cursor 仅在键盘导航模式提供。"""
    note = note_fn() if note_fn else ""
    title_line = f"{title}{('  [' + note + ']') if note else ''}"
    console.print(f"\n[bold cyan]{title_line}[/bold cyan]")
    console.print("-" * 55)

    fixed_base = min(start + page_size, len(options))
    for i in range(start, end):
        opt = options[i]
        num = i + 1
        if i in non_selectable:
            console.print(f"     {opt}")
            continue
        if cursor is not None:
            marker = " > " if i == cursor else "   "
        elif default_index is not None:
            marker = " > " if i == default_index else "   "
        else:
            marker = ""
        console.print(f"{marker}{num}. {opt}")

    if total_pages > 1:
        bottom = []
        if current_page > 0:
            bottom.append("↑ 上一页(n)")
        if current_page < total_pages - 1:
            bottom.append("↓ 下一页(p)")
        console.print(f"  [dim]{' | '.join(bottom)} (第{current_page + 1}/{total_pages}页)[/dim]")

    for i, ft in enumerate(fixed_tail):
        console.print(f"  {fixed_base + i + 1}. {ft}")

    if allow_back:
        console.print("   0. <-- 返回")
    if hotkeys:
        keys = " ".join(f"{k}={hotkeys[k] + 1}" for k in sorted(hotkeys))
        console.print(f"  [dim]快捷键: {keys}[/dim]")
    if cursor is not None:
        console.print("  [dim]↑/↓ 或 j/k 移动, 回车确认, c 复制, f 过滤[/dim]")
    console.print("-" * 55)


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
        _render(title, options, start, end, page_size, current_page, total_pages,
                fixed_tail, allow_back, hotkeys, note_fn, non_selectable,
                default_index=default_index)

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
    """键盘导航仅用于 Windows 交互式终端。"""
    if sys.platform != "win32":
        return False
    try:
        return bool(sys.stdin.isatty())
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

    def first_selectable(from_idx: int) -> int:
        idx = max(from_idx, 0)
        while idx < len(options) and idx in non_selectable:
            idx += 1
        return idx

    total_pages = max(1, (len(options) + page_size - 1) // page_size)
    current_page = 0
    cursor = first_selectable(default_index if 0 <= default_index < len(options) else 0)
    number_buf = ""

    while True:
        start = current_page * page_size
        end = min(start + page_size, len(options))
        _render(title, options, start, end, page_size, current_page, total_pages,
                fixed_tail, allow_back, hotkeys, note_fn, non_selectable,
                cursor=cursor)

        if number_buf:
            console.print(f"[dim]已输入: {number_buf}（回车跳转）[/dim]")

        key = _read_key(msvcrt)

        if key == "up":
            if cursor > 0:
                candidate = cursor - 1
                while candidate >= 0 and candidate in non_selectable:
                    candidate -= 1
                if candidate >= 0:
                    cursor = candidate
                    current_page = cursor // page_size
        elif key == "down":
            if cursor < len(options) - 1:
                candidate = cursor + 1
                while candidate < len(options) and candidate in non_selectable:
                    candidate += 1
                if candidate < len(options):
                    cursor = candidate
                    current_page = cursor // page_size
        elif key in ("j", "J"):
            # j 向下（vim 风格）
            if cursor < len(options) - 1:
                candidate = cursor + 1
                while candidate < len(options) and candidate in non_selectable:
                    candidate += 1
                if candidate < len(options):
                    cursor = candidate
                    current_page = cursor // page_size
        elif key in ("k", "K"):
            if cursor > 0:
                candidate = cursor - 1
                while candidate >= 0 and candidate in non_selectable:
                    candidate -= 1
                if candidate >= 0:
                    cursor = candidate
                    current_page = cursor // page_size
        elif key == "enter":
            if number_buf and number_buf.isdigit():
                idx = int(number_buf) - 1
                number_buf = ""
                if idx in non_selectable:
                    console.print("[red]该项不可选择[/red]")
                    continue
                fixed_base = min(start + page_size, len(options))
                if idx >= fixed_base and idx < fixed_base + len(fixed_tail):
                    return len(options) + (idx - fixed_base)
                if 0 <= idx < len(options):
                    return idx
                console.print(f"[red]无效序号 {number_buf}[/red]")
                continue
            if cursor not in non_selectable:
                fixed_base = min(current_page * page_size + page_size, len(options))
                if cursor >= fixed_base and cursor < fixed_base + len(fixed_tail):
                    return len(options) + (cursor - fixed_base)
                return cursor
        elif key == "escape" or (key == "0" and allow_back):
            return -1
        elif key in ("n", "N"):
            if current_page < total_pages - 1:
                current_page += 1
        elif key in ("p", "P"):
            if current_page > 0:
                current_page -= 1
        elif key == "backspace":
            number_buf = number_buf[:-1]
        elif key in ("c", "C") and copy_text:
            text = copy_text(cursor)
            if copy_to_clipboard(text):
                console.print(f"[green][OK] 已复制: {text}[/green]")
            else:
                console.print(f"[yellow][!] 复制失败[/yellow]")
        elif key in ("f", "F") and filterable and filter_fn:
            console.print("[cyan]输入过滤关键字（回车确认，Esc 取消）:[/cyan]")
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
                console.print(f"\r  [dim]关键字: {keyword}[/dim]", end="")
            console.print()
            if keyword:
                filtered = filter_fn(keyword)
                if filtered is None or not filtered:
                    console.print(f"[yellow][!] 无匹配结果（关键字: {keyword}）[/yellow]")
                else:
                    options[:] = filtered
                    if filter_meta:
                        filter_meta(keyword)
                    total_pages = max(1, (len(options) + page_size - 1) // page_size)
                    current_page = 0
                    cursor = first_selectable(0)
                    number_buf = ""
                    console.print(f"[green]匹配 {len(options)} 条（关键字: {keyword}）[/green]")
        elif key in hotkeys:
            return hotkeys[key]
        elif key.isdigit():
            number_buf += key
            if len(number_buf) > 4:
                number_buf = number_buf[-4:]
