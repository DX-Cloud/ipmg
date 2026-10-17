# -*- coding: utf-8 -*-
"""
三选确认控件 confirm_three 的行为测试（需求 1：Y/n/a）。

运行：python -m unittest discover -s tests -p "test_confirm_three.py" -t . -v
"""
import builtins
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui import widgets  # noqa: E402


class _FeedInput:
    """把一串输入喂给 input()，用完后继续抛 EOFError。"""

    def __init__(self, values):
        self.values = list(values)
        self.original = builtins.input

    def __enter__(self):
        def fake_input(prompt=""):
            if self.values:
                return self.values.pop(0)
            raise EOFError
        builtins.input = fake_input
        return self

    def __exit__(self, *exc):
        builtins.input = self.original
        return False


class TestConfirmThree(unittest.TestCase):
    def _run(self, values, default=widgets.CHOICE_OVERWRITE):
        buf = io.StringIO()
        with _FeedInput(values), redirect_stdout(buf):
            result = widgets.confirm_three("确认配置?", default=default)
        return result, buf.getvalue()

    def test_y_means_overwrite(self):
        self.assertEqual(self._run(["y"])[0], widgets.CHOICE_OVERWRITE)

    def test_yes_means_overwrite(self):
        self.assertEqual(self._run(["YES"])[0], widgets.CHOICE_OVERWRITE)

    def test_empty_uses_default_overwrite(self):
        self.assertEqual(self._run([""])[0], widgets.CHOICE_OVERWRITE)

    def test_empty_uses_explicit_default(self):
        self.assertEqual(self._run([""], default=widgets.CHOICE_CANCEL)[0],
                         widgets.CHOICE_CANCEL)

    def test_a_means_append(self):
        self.assertEqual(self._run(["a"])[0], widgets.CHOICE_APPEND)

    def test_append_word_and_case_and_spaces(self):
        self.assertEqual(self._run(["  Append  "])[0], widgets.CHOICE_APPEND)

    def test_n_means_cancel(self):
        self.assertEqual(self._run(["n"])[0], widgets.CHOICE_CANCEL)

    def test_no_means_cancel(self):
        self.assertEqual(self._run(["No"])[0], widgets.CHOICE_CANCEL)

    def test_invalid_then_valid_loops(self):
        result, out = self._run(["x", "1", "a"])
        self.assertEqual(result, widgets.CHOICE_APPEND)
        self.assertIn("请输入", out)

    def test_eof_means_cancel(self):
        self.assertEqual(self._run([])[0], widgets.CHOICE_CANCEL)

    def test_keyboard_interrupt_means_cancel(self):
        original = builtins.input

        def boom(prompt=""):
            raise KeyboardInterrupt

        builtins.input = boom
        try:
            with redirect_stdout(io.StringIO()):
                result = widgets.confirm_three("确认?")
        finally:
            builtins.input = original
        self.assertEqual(result, widgets.CHOICE_CANCEL)

    def test_confirmed_cancel_is_not_overwrite(self):
        """回归护栏：取消绝不能被当成覆盖。"""
        for value in ("n", "no"):
            self.assertNotEqual(self._run([value])[0], widgets.CHOICE_OVERWRITE)

    def test_confirm_still_returns_bool(self):
        """回归护栏：既有 confirm() 的契约不能被改动。"""
        with _FeedInput(["y"]), redirect_stdout(io.StringIO()):
            self.assertIs(widgets.confirm("继续?"), True)
        with _FeedInput([""]), redirect_stdout(io.StringIO()):
            self.assertIs(widgets.confirm("继续?", default=False), False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
