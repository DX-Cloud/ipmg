# -*- coding: utf-8 -*-
"""
分组菜单的页内编号与分页回归测试。

需求（实测反馈修正后）：
- **序号是页内快捷键**：每一页都显示 1..N，按对应数字直接选中该页对应条目；
- 分组标题不占序号；
- 分页不把分组标题留在上一页页尾；某页从分组中间开始时页首补分组标题。

运行：python -m unittest discover -s tests -p "test_group_menu.py" -t . -v
"""
import builtins
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui import widgets  # noqa: E402


def build_options(spec):
    """spec: [(组名, 设备数), ...] → (options, non_selectable, section_rows)"""
    options, non_selectable, section_rows = [], set(), set()
    for group, count in spec:
        options.append(f"── [{group}] ──")
        non_selectable.add(len(options) - 1)
        section_rows.add(len(options) - 1)
        for k in range(1, count + 1):
            options.append(f"[ ] {group}-设备{k}")
    return options, non_selectable, section_rows


def selectable(options, ns):
    return [i for i in range(len(options)) if i not in ns]


def layout(options, ns, sections, page_size, fixed_tail=()):
    """返回 (pages, [(numbers, by_number), ...])"""
    pages = widgets._paginate(len(options), ns, sections, page_size)
    per_page = [widgets._page_numbers(s, e, ns, list(fixed_tail)) for s, e in pages]
    return pages, per_page


class TestPageNumbers(unittest.TestCase):
    def test_each_page_starts_at_one(self):
        """每页都从 1 开始（序号 = 当页快捷键）。"""
        options, ns, sections = build_options([("A", 7), ("B", 7), ("C", 8)])
        pages, per_page = layout(options, ns, sections, 9)
        self.assertEqual(len(pages), 3, f"实际分页: {pages}")
        for page_no, (numbers, _) in enumerate(per_page):
            self.assertEqual(sorted(numbers.values()), list(range(1, len(numbers) + 1)),
                             f"第 {page_no + 1} 页应从 1 开始连续编号")
            self.assertEqual(min(numbers.values()), 1)

    def test_section_headers_have_no_number(self):
        options, ns, sections = build_options([("A", 3), ("B", 3)])
        _, per_page = layout(options, ns, sections, 9)
        numbers = per_page[0][0]
        self.assertNotIn(0, numbers, "分组标题不应编号")
        self.assertEqual([numbers[i] for i in selectable(options, ns)], [1, 2, 3, 4, 5, 6])

    def test_number_maps_to_that_pages_rows(self):
        """同一序号在不同页指向不同条目（页内快捷键的含义）。"""
        options, ns, sections = build_options([("A", 7), ("B", 7)])
        pages, per_page = layout(options, ns, sections, 9)
        _, page1_by = per_page[0]
        _, page2_by = per_page[1]
        kind1, idx1 = page1_by[3]
        kind2, idx2 = page2_by[3]
        self.assertEqual((kind1, kind2), ("opt", "opt"))
        self.assertNotEqual(idx1, idx2, "第 3 号在每页指向不同设备")
        self.assertEqual(idx1, selectable(options, ns)[2], "第 1 页第 3 号 = 第 3 台设备")
        self.assertEqual(idx2, selectable(options, ns)[9 + 2], "第 2 页第 3 号 = 第 12 台设备")

    def test_fixed_tail_follows_page_numbers(self):
        """固定尾部排在**当页**设备之后（每页 9 个设备位，标题不占位）。"""
        options, ns, sections = build_options([("A", 7), ("B", 8)])
        _, per_page = layout(options, ns, sections, 9, fixed_tail=["[+] 添加新设备"])
        (numbers1, by1), (numbers2, by2) = per_page
        self.assertEqual(max(by1), 10, "第 1 页 9 台设备后，固定尾部是 10 号")
        self.assertEqual(max(by2), 7, "第 2 页 6 台设备后，固定尾部是 7 号")
        self.assertEqual(by1[max(by1)], ("fixed", 0))
        self.assertEqual(by2[max(by2)], ("fixed", 0))

    def test_page_holds_full_device_quota(self):
        """每页放满 9 个**设备**，分组标题不占这 9 个名额。"""
        options, ns, sections = build_options([("A", 7), ("B", 7), ("C", 8)])
        pages = widgets._paginate(len(options), ns, sections, 9)
        self.assertEqual(len(pages), 3, f"22 台设备 / 每页 9 台 = 3 页，实际 {pages}")
        counts = [sum(1 for i in range(s, e) if i not in ns) for s, e in pages]
        self.assertEqual(counts, [9, 9, 4], f"每页设备数应为 9/9/4，实际 {counts}")
        headers = [sum(1 for i in range(s, e) if i in ns) for s, e in pages]
        self.assertEqual(sum(headers), len(sections), "所有分组标题都要显示且各一次")


class TestPagination(unittest.TestCase):
    def test_no_page_ends_with_section_header(self):
        options, ns, sections = build_options([("A", 4), ("B", 3), ("C", 8)])
        pages = widgets._paginate(len(options), ns, sections, 9)
        for start, end in pages:
            self.assertNotIn(end - 1, sections, f"页 {(start, end)} 以标题结尾")
            self.assertGreater(end, start)

    def test_section_header_on_same_page_as_items(self):
        options, ns, sections = build_options([("A", 4), ("B", 3), ("C", 8)])
        pages = widgets._paginate(len(options), ns, sections, 9)
        header_c = [i for i, o in enumerate(options) if o == "── [C] ──"][0]
        page = [p for p in pages if p[0] <= header_c < p[1]][0]
        self.assertTrue(page[0] <= header_c + 1 < page[1],
                        "标题必须与它的第一条目同页")

    def test_cont_label_when_page_starts_mid_group(self):
        options, ns, sections = build_options([("A", 12)])
        pages = widgets._paginate(len(options), ns, sections, 9)
        self.assertEqual(widgets._page_cont_label(options, pages[1][0], ns, sections),
                         "── [A] ──")

    def test_no_cont_label_at_page_start_or_header(self):
        options, ns, sections = build_options([("A", 3), ("B", 3)])
        self.assertIsNone(widgets._page_cont_label(options, 0, ns, sections))
        header_b = [i for i, o in enumerate(options) if o == "── [B] ──"][0]
        self.assertIsNone(widgets._page_cont_label(options, header_b, ns, sections))

    def test_auto_detect_headers(self):
        options, ns, _ = build_options([("A", 3)])
        self.assertEqual(widgets._section_rows_of(options, ns, None), {0})


class PlainPickHarness:
    """驱动 _plain_pick：脚本化输入 + 记录每页渲染内容与提示语。"""

    def __init__(self, answers):
        self.answers = list(answers)
        self.frames = []
        self.prompts = []
        self.messages = []

    def __enter__(self):
        self._saved = [
            (widgets, "_print_lines", widgets._print_lines),
            (widgets.console, "print", widgets.console.print),
            (builtins, "input", builtins.input),
            (widgets, "_keyboard_available", widgets._keyboard_available),
        ]
        widgets._print_lines = lambda lines: self.frames.append(list(lines))
        widgets.console.print = lambda *a, **k: self.messages.append(
            " ".join(str(x) for x in a))

        def fake_input(prompt=""):
            self.prompts.append(prompt)
            return self.answers.pop(0)

        builtins.input = fake_input
        widgets._keyboard_available = lambda: False
        return self

    def __exit__(self, *exc):
        for obj, name, value in self._saved:
            setattr(obj, name, value)
        return False

    def frame_text(self, index=-1):
        return "\n".join(self.frames[index])


class TestPlainPickPageLocal(unittest.TestCase):
    def _run(self, answers, spec, fixed_tail=None, page_size=9):
        options, ns, sections = build_options(spec)
        h = PlainPickHarness(answers)
        with h:
            idx = widgets._plain_pick(options, "选择设备", 1, True, page_size,
                                      list(fixed_tail or []), ns, {}, False,
                                      None, None, None, None, sections)
        return idx, h, options, ns

    def test_first_page_numbering_and_selection(self):
        idx, h, options, ns = self._run(["3"], [("A", 7), ("B", 7)])
        expected = selectable(options, ns)[2]
        self.assertEqual(idx, expected, "第 1 页按 3 应选第 3 台设备")
        text = h.frame_text()
        self.assertIn("── [A] ──", text)
        self.assertNotIn("1. ──", text, "标题不带号")
        self.assertIn("1. ", text)

    def test_same_number_on_second_page_selects_that_pages_item(self):
        """翻页后按同一个数字，选中的是**第 2 页**的对应项。"""
        idx, h, options, ns = self._run(["n", "3"], [("A", 7), ("B", 7)])
        expected = selectable(options, ns)[9 + 2]
        self.assertEqual(idx, expected, "第 2 页按 3 应选第 2 页第 3 台设备")
        second = h.frame_text()
        self.assertIn("── [B] ──", second, "第 2 页顶部应显示分组标题")
        self.assertIn("1. ", second, "第 2 页从 1 开始编号")

    def test_prompt_range_is_page_local(self):
        _, h, _, _ = self._run(["n", "0"], [("A", 7), ("B", 7)])
        self.assertIn("1-9", h.prompts[0], "第 1 页满 9 台设备，提示应为 1-9")
        self.assertIn("1-5", h.prompts[1], "第 2 页只剩 5 台，提示应为 1-5")

    def test_fixed_tail_number_follows_page(self):
        options, ns, sections = build_options([("A", 7), ("B", 8)])
        with PlainPickHarness(["10"]):
            idx = widgets._plain_pick(options, "设备管理", 1, True, 9,
                                      ["[+] 添加新设备"], ns, {}, False,
                                      None, None, None, None, sections)
        self.assertEqual(idx, len(options), "第 1 页满 9 台设备 → 固定尾部是 10 号")
        with PlainPickHarness(["n", "7"]):
            idx2 = widgets._plain_pick(options, "设备管理", 1, True, 9,
                                       ["[+] 添加新设备"], ns, {}, False,
                                       None, None, None, None, sections)
        self.assertEqual(idx2, len(options), "第 2 页 6 台设备 → 固定尾部是 7 号")

    def test_back_still_works(self):
        idx, _, _, _ = self._run(["0"], [("A", 3)])
        self.assertEqual(idx, -1)

    def test_filter_recomputes_page_numbers(self):
        full, ns, sections = build_options([("A", 2), ("B", 2)])
        filtered, fns, fsec = build_options([("B", 2)])

        def filter_fn(keyword):
            full[:] = filtered
            ns.clear()
            ns.update(fns)
            sections.clear()
            sections.update(fsec)
            return filtered

        with PlainPickHarness(["B组", "1"]) as h:
            idx = widgets._plain_pick(full, "选择设备", 1, True, 9, [],
                                      ns, {}, True, filter_fn, None, None, None,
                                      sections)
        self.assertEqual(full[idx], "[ ] B-设备1", "过滤后 1 号是新列表第一台")
        self.assertIn("1. ", h.frame_text())


class KeyboardPickHarness:
    """驱动 _keyboard_pick：脚本化按键 + 记录每帧渲染内容。"""

    def __init__(self, keys):
        self.keys = list(keys)
        self.frames = []

    def __enter__(self):
        self._saved = [
            (widgets, "_next_key", widgets._next_key),
            (widgets, "_clear_and_redraw", widgets._clear_and_redraw),
            (widgets, "_remember_frame", widgets._remember_frame),
            (widgets._console, "drain_events", widgets._console.drain_events),
            (widgets._console, "reset_size_baseline",
             widgets._console.reset_size_baseline),
        ]
        widgets._next_key = lambda: self.keys.pop(0) if self.keys else "escape"
        widgets._clear_and_redraw = lambda frame, prev=None: self.frames.append(list(frame))
        widgets._remember_frame = lambda frame: None
        widgets._console.drain_events = lambda: False
        widgets._console.reset_size_baseline = lambda: None
        return self

    def __exit__(self, *exc):
        for obj, name, value in self._saved:
            setattr(obj, name, value)
        return False

    def frame_text(self, index=-1):
        return "\n".join(self.frames[index])


class TestKeyboardPickPageLocal(unittest.TestCase):
    def _run(self, keys, spec, page_size=9):
        options, ns, sections = build_options(spec)
        h = KeyboardPickHarness(keys)
        with h:
            idx = widgets._keyboard_pick(options, "选择设备", 1, True, page_size,
                                         [], ns, {}, False, None, None, None,
                                         None, False, sections)
        return idx, h, options, ns

    def test_digits_are_page_local(self):
        idx1, _, options, ns = self._run(["3", "enter"], [("A", 7), ("B", 7)])
        self.assertEqual(idx1, selectable(options, ns)[2], "第 1 页按 3 = 第 3 台")
        idx2, _, options2, ns2 = self._run(["right", "3", "enter"], [("A", 7), ("B", 7)])
        self.assertEqual(idx2, selectable(options2, ns2)[9 + 2], "第 2 页按 3 = 第 12 台")

    def test_second_page_frame_restarts_numbering(self):
        _, h, _, _ = self._run(["right", "escape"], [("A", 7), ("B", 7)])
        second = h.frame_text()
        self.assertIn("── [B] ──", second, "第 2 页顶部有分组标题")
        self.assertIn(" 1. ", second, "第 2 页编号从 1 开始")

    def test_cursor_enter_selects_item(self):
        options, ns, sections = build_options([("A", 3)])
        h = KeyboardPickHarness(["down", "enter"])
        with h:
            idx = widgets._keyboard_pick(options, "选择设备", 1, True, 9, [],
                                         ns, {}, False, None, None, None,
                                         None, False, sections)
        self.assertEqual(idx, selectable(options, ns)[1], "下移一格再回车 = 第 2 台")

    def test_page_down_keeps_header_visible(self):
        options, ns, sections = build_options([("大组", 12)])
        h = KeyboardPickHarness(["right", "escape"])
        with h:
            widgets._keyboard_pick(options, "选择设备", 1, True, 9, [],
                                   ns, {}, False, None, None, None, None,
                                   False, sections)
        self.assertIn("── [大组] ──", h.frame_text(), "续页顶部补分组标题")

    def test_up_at_page_top_wraps_to_last_entry(self):
        """↑ 到第一项后回绕到本页最后一项（"返回" 0 号），不切页。"""
        options, ns, sections = build_options([("A", 7), ("B", 7), ("C", 8)])
        h = KeyboardPickHarness(["up", "escape"])
        with h:
            widgets._keyboard_pick(options, "选择设备", 1, True, 9, [],
                                   ns, {}, False, None, None, None, None,
                                   False, sections)
        self.assertIn("> 0. <-- 返回", h.frame_text(), "光标回绕到 0 号（返回）")
        self.assertIn("(第1/", h.frame_text(), "不切页")

    def test_down_at_last_entry_wraps_to_first(self):
        """在 0 号继续 ↓ 回到第 1 项。"""
        options, ns, sections = build_options([("A", 7), ("B", 7), ("C", 8)])
        h = KeyboardPickHarness(["up", "down", "escape"])
        with h:
            widgets._keyboard_pick(options, "选择设备", 1, True, 9, [],
                                   ns, {}, False, None, None, None, None,
                                   False, sections)
        self.assertIn("> 1. ", h.frame_text(), "从 0 号往下回到第 1 项")
        self.assertIn("(第1/", h.frame_text(), "不切页")

    def test_vertical_keys_never_switch_page(self):
        """↑/↓ 无论如何都不切页（翻页只认 ←/→）。"""
        options, ns, sections = build_options([("A", 7), ("B", 7), ("C", 8)])
        h = KeyboardPickHarness(["right", "up", "up", "down", "up", "escape"])
        with h:
            widgets._keyboard_pick(options, "选择设备", 1, True, 9, [],
                                   ns, {}, False, None, None, None, None,
                                   False, sections)
        self.assertIn("(第2/", h.frame_text(), "页内回绕始终停留第 2 页")

    def test_only_left_right_switch_pages(self):
        options, ns, sections = build_options([("A", 7), ("B", 7), ("C", 8)])
        h = KeyboardPickHarness(["right", "right", "left", "escape"])
        with h:
            widgets._keyboard_pick(options, "选择设备", 1, True, 9, [],
                                   ns, {}, False, None, None, None, None,
                                   False, sections)
        self.assertIn("(第2/", h.frame_text(), "→ 下一页、← 上一页")


class TestDeviceMenuIntegration(unittest.TestCase):
    """ui/app.py 的菜单构建与标记同步。"""

    def _config(self):
        return {
            "devices": [
                {"name": "核心交换机", "group": "机房A", "device_ip": "10.0.0.1",
                 "ip_mode": "auto", "subnet_mask": "255.255.255.0"},
                {"name": "接入交换机", "group": "机房A", "device_ip": "10.0.0.2",
                 "ip_mode": "auto", "subnet_mask": "255.255.255.0"},
                {"name": "防火墙", "group": "机房B", "device_ip": "10.0.1.1",
                 "ip_mode": "auto", "subnet_mask": "255.255.255.0"},
            ]
        }

    def test_menu_marks_section_rows(self):
        from ui import app
        options, index_map, ns, first, sections = app._build_device_menu(self._config())
        self.assertEqual(sections, {0, 3})
        self.assertEqual(ns, sections)
        self.assertEqual(first, 1, "默认光标应落在第一台设备")
        numbers, _ = widgets._page_numbers(0, len(options), ns, [])
        self.assertEqual([numbers[i] for i in sorted(index_map)], [1, 2, 3],
                         "三台设备编号 1/2/3（标题不占号）")

    def test_pick_device_syncs_marks_in_place_on_filter(self):
        """过滤后 widgets 持有的集合对象必须在原地更新为过滤后的标记。"""
        from ui import app

        captured = {}
        ids = []
        saved = app.widgets.pick_option

        def fake_pick(options, title, **kw):
            captured["before"] = set(kw["non_selectable"])
            ids.append(id(kw["non_selectable"]))
            filtered = kw["filter_fn"]("机房B")
            ids.append(id(kw["non_selectable"]))
            captured["after"] = set(kw["non_selectable"])
            captured["after_sections"] = set(kw["section_rows"])
            captured["filtered"] = list(filtered)
            return -1

        class FakeAdapter:
            name = "以太网"

        app.widgets.pick_option = fake_pick
        try:
            app._pick_device(self._config(), FakeAdapter())
        finally:
            app.widgets.pick_option = saved

        self.assertEqual(captured["before"], {0, 3})
        self.assertEqual(captured["after"], {0}, "过滤后只剩一个分组标题")
        self.assertEqual(captured["after_sections"], {0})
        self.assertEqual(ids[0], ids[1], "过滤前后必须是同一个集合对象（原地更新）")
        self.assertEqual(len(captured["filtered"]), 2, "机房B 一个标题 + 一台设备")


if __name__ == "__main__":
    unittest.main(verbosity=2)
