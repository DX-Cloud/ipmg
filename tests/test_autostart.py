# -*- coding: utf-8 -*-
"""
core.autostart 的回归测试。

**全程 mock `subprocess.run`**：本文件不会真实创建/查询/删除任何计划任务
（基类 `AutostartTestCase.setUp` 会拦截所有 subprocess.run 调用，任何漏网的
真实调用都会落到 mock 上）。

运行：
    python -B -m unittest discover -s tests -p "test_autostart.py" -t .
"""

import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import autostart  # noqa: E402


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["schtasks"], returncode, stdout, stderr)


class AutostartTestCase(unittest.TestCase):
    """基线环境：伪装 Windows + schtasks 可用 + 拦截所有 subprocess.run。"""

    def setUp(self):
        run_patcher = mock.patch.object(autostart.subprocess, "run")
        self.mock_run = run_patcher.start()
        self.addCleanup(run_patcher.stop)
        self.mock_run.return_value = _completed(0)

        platform_patcher = mock.patch.object(autostart.sys, "platform", "win32")
        platform_patcher.start()
        self.addCleanup(platform_patcher.stop)

        which_patcher = mock.patch.object(
            autostart.shutil, "which",
            return_value=r"C:\Windows\System32\schtasks.exe")
        which_patcher.start()
        self.addCleanup(which_patcher.stop)

    # ---- 断言辅助 ----
    def _argv(self):
        """最近一次 subprocess.run 的 argv 列表（副本）。"""
        return list(self.mock_run.call_args.args[0])

    def _kwargs(self):
        return dict(self.mock_run.call_args.kwargs)

    def _value_after(self, flag, argv=None):
        argv = argv if argv is not None else self._argv()
        self.assertIn(flag, argv, f"缺少参数 {flag}: {argv}")
        index = argv.index(flag)
        self.assertLess(index + 1, len(argv), f"{flag} 缺少取值")
        return argv[index + 1]


# ============================== launch_command ==============================


class TestLaunchCommand(AutostartTestCase):
    def test_frozen_uses_quoted_exe(self):
        with mock.patch.object(autostart.sys, "frozen", True, create=True), \
                mock.patch.object(autostart.sys, "executable",
                                  r"C:\Program Files\IPManager\ipmg.exe"):
            self.assertEqual(
                autostart.launch_command(),
                '"C:\\Program Files\\IPManager\\ipmg.exe" --resident --autostart-min')

    def test_frozen_preserves_chinese_and_space_path(self):
        exe = r"C:\用户 目录\IP Manager\ipmg.exe"
        with mock.patch.object(autostart.sys, "frozen", True, create=True), \
                mock.patch.object(autostart.sys, "executable", exe):
            command = autostart.launch_command()
        self.assertEqual(command, f'"{exe}" --resident --autostart-min')
        self.assertEqual(command.count('"'), 2, "exe 路径只应有一对引号，不得二次转义")

    def test_frozen_falls_back_to_argv0(self):
        with mock.patch.object(autostart.sys, "frozen", True, create=True), \
                mock.patch.object(autostart.sys, "executable", ""), \
                mock.patch.object(autostart.sys, "argv", [r"C:\x\ipmg.exe"]):
            self.assertEqual(autostart.launch_command(),
                             '"C:\\x\\ipmg.exe" --resident --autostart-min')

    def test_source_uses_python_and_abs_main(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(autostart.__file__)))
        main_py = os.path.join(repo_root, "main.py")
        with mock.patch.object(autostart.sys, "frozen", False, create=True):
            command = autostart.launch_command()
        self.assertEqual(command,
                         f'"{sys.executable}" "{main_py}" --resident --autostart-min')
        self.assertTrue(main_py.endswith("main.py"))
        self.assertTrue(os.path.isabs(main_py))


# ============================== is_supported ==============================


class TestSupported(AutostartTestCase):
    def test_supported_on_windows_with_schtasks(self):
        self.assertTrue(autostart.is_supported())

    def test_not_supported_off_windows(self):
        with mock.patch.object(autostart.sys, "platform", "linux"):
            self.assertFalse(autostart.is_supported())

    def test_not_supported_without_schtasks(self):
        with mock.patch.object(autostart.shutil, "which", return_value=None):
            self.assertFalse(autostart.is_supported())

    def test_unsupported_apis_do_not_call_subprocess(self):
        with mock.patch.object(autostart.shutil, "which", return_value=None):
            self.assertEqual(autostart.enable(),
                             (False, autostart.UNSUPPORTED_MESSAGE))
            self.assertEqual(autostart.disable(),
                             (False, autostart.UNSUPPORTED_MESSAGE))
            self.assertFalse(autostart.task_exists())
            state = autostart.status()
            self.assertFalse(state["supported"])
            self.assertEqual(state["error"], autostart.UNSUPPORTED_MESSAGE)
        self.mock_run.assert_not_called()


# ============================== enable ==============================


class TestEnable(AutostartTestCase):
    def test_builds_expected_schtasks_arguments(self):
        ok, message = autostart.enable(15)
        self.assertTrue(ok, message)
        self.assertIn(autostart.TASK_NAME, message)

        argv = self._argv()
        self.assertEqual(argv[0], "schtasks")
        self.assertIn("/Create", argv)
        self.assertIn("/F", argv)
        self.assertIn("/IT", argv)
        self.assertEqual(self._value_after("/TN", argv), "ipmg-resident")
        self.assertEqual(self._value_after("/SC", argv), "ONLOGON")
        self.assertEqual(self._value_after("/RL", argv), "HIGHEST")
        self.assertEqual(self._value_after("/DELAY", argv), "0000:15")
        self.mock_run.assert_called_once()

    def test_tr_value_is_full_quoted_command_line(self):
        autostart.enable(15)
        argv = self._argv()
        tr = self._value_after("/TR", argv)
        self.assertEqual(tr, autostart.launch_command())
        self.assertIn("--resident", tr)
        self.assertIn("--autostart-min", tr)
        # /TR 必须是单个 argv 元素，不能被拆成多段
        self.assertEqual(argv.count(tr), 1)
        self.assertNotIn("--resident", argv, "/TR 的值不应被拆散到独立参数")

    def test_subprocess_call_is_safe_and_headless(self):
        autostart.enable(15)
        kwargs = self._kwargs()
        self.assertIs(kwargs.get("shell"), False)
        self.assertIs(kwargs.get("capture_output"), True)
        self.assertEqual(kwargs.get("timeout"), autostart.SCHTASKS_TIMEOUT)
        self.assertEqual(kwargs.get("creationflags") & autostart.CREATE_NO_WINDOW,
                         autostart.CREATE_NO_WINDOW)

    def test_delay_seconds_formatting(self):
        for seconds, expected in ((0, "0000:00"), (15, "0000:15"),
                                  (90, "0001:30"), (3661, "0061:01"),
                                  (-5, "0000:00")):
            with self.subTest(delay_seconds=seconds):
                self.mock_run.reset_mock()
                self.mock_run.return_value = _completed(0)
                autostart.enable(seconds)
                self.assertEqual(self._value_after("/DELAY"), expected)

    def test_failure_returns_reason_without_raising(self):
        self.mock_run.return_value = _completed(1, stderr="错误: 拒绝访问。")
        ok, message = autostart.enable(15)
        self.assertFalse(ok)
        self.assertIn("拒绝访问", message)

    def test_failure_without_output_mentions_exit_code(self):
        self.mock_run.return_value = _completed(5)
        ok, message = autostart.enable(15)
        self.assertFalse(ok)
        self.assertIn("5", message)

    def test_timeout_returns_reason(self):
        self.mock_run.side_effect = subprocess.TimeoutExpired("schtasks", 15)
        ok, message = autostart.enable(15)
        self.assertFalse(ok)
        self.assertIn("超时", message)

    def test_unexpected_exception_returns_reason(self):
        self.mock_run.side_effect = OSError("boom")
        ok, message = autostart.enable(15)
        self.assertFalse(ok)
        self.assertIn("boom", message)


# ============================== task_exists ==============================


class TestTaskExists(AutostartTestCase):
    def test_query_arguments(self):
        self.mock_run.return_value = _completed(1)
        autostart.task_exists()
        self.assertEqual(self._argv(),
                         ["schtasks", "/Query", "/TN", "ipmg-resident"])

    def test_exit_code_zero_means_exists(self):
        self.mock_run.return_value = _completed(0)
        self.assertTrue(autostart.task_exists())

    def test_exit_code_nonzero_means_missing(self):
        self.mock_run.return_value = _completed(1)
        self.assertFalse(autostart.task_exists())

    def test_output_text_is_never_parsed(self):
        # 退出码 0 但输出是"找不到"→ 仍视为存在；反过来同理
        self.mock_run.return_value = _completed(0, stdout="错误: 找不到计划任务")
        self.assertTrue(autostart.task_exists())
        self.mock_run.return_value = _completed(1, stdout="成功: 任务已创建")
        self.assertFalse(autostart.task_exists())

    def test_exception_returns_false(self):
        self.mock_run.side_effect = OSError("boom")
        self.assertFalse(autostart.task_exists())


# ============================== disable ==============================


class TestDisable(AutostartTestCase):
    def test_delete_arguments_and_success(self):
        self.mock_run.return_value = _completed(0)
        ok, message = autostart.disable()
        self.assertTrue(ok)
        self.assertIn(autostart.TASK_NAME, message)
        self.assertEqual(self._argv(),
                         ["schtasks", "/Delete", "/F", "/TN", "ipmg-resident"])
        self.mock_run.assert_called_once()

    def test_idempotent_when_task_missing(self):
        self.mock_run.side_effect = [_completed(1, stderr="错误: 找不到"),
                                     _completed(1)]
        ok, message = autostart.disable()
        self.assertTrue(ok, "任务本就不存在时应视为关闭成功")
        self.assertIn("不存在", message)
        self.assertEqual(self.mock_run.call_count, 2)

    def test_failure_when_task_still_exists(self):
        self.mock_run.side_effect = [_completed(1, stderr="错误: 拒绝访问。"),
                                     _completed(0)]
        ok, message = autostart.disable()
        self.assertFalse(ok)
        self.assertIn("拒绝访问", message)

    def test_timeout_returns_reason(self):
        self.mock_run.side_effect = subprocess.TimeoutExpired("schtasks", 15)
        ok, message = autostart.disable()
        self.assertFalse(ok)
        self.assertIn("超时", message)


# ============================== status ==============================


class TestStatus(AutostartTestCase):
    def test_contract_keys(self):
        state = autostart.status()
        self.assertEqual(set(state.keys()),
                         {"supported", "exists", "command", "error"})

    def test_reports_existing_task_and_command(self):
        self.mock_run.return_value = _completed(0)
        state = autostart.status()
        self.assertTrue(state["supported"])
        self.assertTrue(state["exists"])
        self.assertEqual(state["command"], autostart.launch_command())
        self.assertEqual(state["error"], "")

    def test_missing_task_is_not_an_error(self):
        self.mock_run.return_value = _completed(1)
        state = autostart.status()
        self.assertFalse(state["exists"])
        self.assertEqual(state["error"], "")

    def test_query_exception_is_reported(self):
        self.mock_run.side_effect = OSError("boom")
        state = autostart.status()
        self.assertTrue(state["supported"])
        self.assertFalse(state["exists"])
        self.assertIn("boom", state["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
