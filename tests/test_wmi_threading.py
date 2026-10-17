# -*- coding: utf-8 -*-
"""
WMI 层的线程安全与自愈回归测试。

背景（真实故障）：`_get_wmi()` 原本把连接缓存在模块全局变量里，而 WMI 底层是 COM，
接口指针只能在创建它的线程使用。常驻模式下热键应用跑在工作线程，TUI 配置跑在主线程，
于是后一个线程拿到别的线程创建的连接 → 抛 COM 错误 → 表现为
"[X] 交换机-A01-核心: 备份网卡IP失败，已中止"。

运行：python -m unittest discover -s tests -p "test_wmi_threading.py" -t . -v
"""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import ip_configurator as ipc  # noqa: E402
from utils import backup as ub           # noqa: E402


class FakeWMIModule:
    """假的 wmi 模块：每次调用 WMI() 返回一个新对象并计数。"""

    def __init__(self):
        self.created = []

    def WMI(self):
        conn = object()
        self.created.append((threading.current_thread().name, conn))
        return conn


class FakeAdapterConfig:
    def __init__(self, ips=("10.0.0.5",), masks=("255.255.255.0",),
                 gateways=("10.0.0.1",), dhcp=False):
        self.IPAddress = list(ips)
        self.IPSubnet = list(masks)
        self.DefaultIPGateway = list(gateways)
        self.DHCPEnabled = dhcp


class WmiTestCase(unittest.TestCase):
    def setUp(self):
        self._saved = []
        self.fake_wmi = FakeWMIModule()
        self._patch(ipc, "wmi", self.fake_wmi)
        self._reset_thread_state()

    def tearDown(self):
        for obj, name, value in reversed(self._saved):
            setattr(obj, name, value)
        self._reset_thread_state()

    def _patch(self, obj, name, value):
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    @staticmethod
    def _reset_thread_state():
        """清掉当前线程的 WMI 连接与 COM 标记（COM 标记是线程级粘性状态，用例间必须隔离）。"""
        ipc.reset_wmi_connection()
        try:
            del ipc._wmi_local.com_ready
        except AttributeError:
            pass


class TestThreadLocalConnection(WmiTestCase):
    def test_same_thread_reuses_connection(self):
        first = ipc._get_wmi()
        second = ipc._get_wmi()
        self.assertIs(first, second, "同一线程应复用连接")
        self.assertEqual(len(self.fake_wmi.created), 1)

    def test_different_threads_get_different_connections(self):
        """核心回归：不同线程绝不能共用同一个 WMI/COM 连接。"""
        main_conn = ipc._get_wmi()
        holder = {}

        def worker():
            holder["conn"] = ipc._get_wmi()

        t = threading.Thread(target=worker, name="ipmg-preset")
        t.start()
        t.join(5)

        self.assertIn("conn", holder)
        self.assertIsNot(main_conn, holder["conn"],
                         "工作线程必须拿到自己的连接，否则会抛 COM 跨线程错误")
        self.assertEqual(len(self.fake_wmi.created), 2)
        self.assertEqual(self.fake_wmi.created[1][0], "ipmg-preset")

    def test_reset_only_affects_current_thread(self):
        first = ipc._get_wmi()
        ipc.reset_wmi_connection()
        second = ipc._get_wmi()
        self.assertIsNot(first, second, "reset 后应重建连接")


class TestLookupRetry(WmiTestCase):
    def test_retry_after_empty_result(self):
        """连接失效时查询可能静默返回空 → 丢弃连接再查一次。"""
        ipc._get_wmi()                     # 先建立一条本线程连接
        self.assertIsNotNone(getattr(ipc._wmi_local, "conn", None))

        calls = []
        config = FakeAdapterConfig()

        def fake_lookup(name):
            calls.append(name)
            return None if len(calls) == 1 else config

        self._patch(ipc, "_get_adapter_config_by_name", fake_lookup)
        result = ipc._get_adapter_config_retry("WLAN 2")
        self.assertIs(result, config)
        self.assertEqual(len(calls), 2, "应重试一次")
        self.assertIsNone(getattr(ipc._wmi_local, "conn", None),
                          "重试前必须丢弃旧连接，否则重试用的还是坏连接")

    def test_no_retry_when_found(self):
        calls = []
        config = FakeAdapterConfig()

        def fake_lookup(name):
            calls.append(name)
            return config

        self._patch(ipc, "_get_adapter_config_by_name", fake_lookup)
        self.assertIs(ipc._get_adapter_config_retry("WLAN 2"), config)
        self.assertEqual(len(calls), 1, "查到就不该重试")

    def test_retry_when_lookup_raises(self):
        """查询抛异常（连接失效）→ 换连接重试一次。"""
        calls = []
        config = FakeAdapterConfig()

        def fake_lookup(name):
            calls.append(name)
            if len(calls) == 1:
                raise RuntimeError("x_wmi: Unexpected COM Error")
            return config

        self._patch(ipc, "_get_adapter_config_by_name", fake_lookup)
        result = ipc._get_adapter_config_retry("WLAN 2")
        self.assertIs(result, config)
        self.assertEqual(len(calls), 2)

    def test_get_current_ip_config_uses_fake_config(self):
        self._patch(ipc, "_get_adapter_config_by_name",
                    lambda name: FakeAdapterConfig())
        cur = ipc.get_current_ip_config("WLAN 2")
        self.assertEqual(cur["ip"], "10.0.0.5")
        self.assertEqual(cur["ips"], ["10.0.0.5"])


class TestComInitialization(WmiTestCase):
    """WMI 底层是 COM：每个线程首次使用前必须 CoInitialize（本次真机故障的根因）。"""

    def setUp(self):
        super().setUp()
        import types
        self.com_calls = []
        self._fake_pythoncom = types.SimpleNamespace(
            CoInitialize=lambda: self.com_calls.append(threading.current_thread().name))
        self._saved_modules = sys.modules.get("pythoncom")
        sys.modules["pythoncom"] = self._fake_pythoncom

    def tearDown(self):
        if self._saved_modules is not None:
            sys.modules["pythoncom"] = self._saved_modules
        else:
            sys.modules.pop("pythoncom", None)
        super().tearDown()

    def test_com_initialized_once_per_thread(self):
        ipc._get_wmi()
        self.assertEqual(self.com_calls, ["MainThread"],
                         "首次取连接时必须初始化 COM")
        ipc._get_wmi()
        self.assertEqual(len(self.com_calls), 1, "同一线程不应重复初始化")

    def test_worker_thread_initializes_com_itself(self):
        ipc._get_wmi()                      # 主线程先初始化
        holder = {}

        def worker():
            holder["conn"] = ipc._get_wmi()

        t = threading.Thread(target=worker, name="ipmg-preset")
        t.start()
        t.join(5)
        self.assertIn("ipmg-preset", self.com_calls,
                      "工作线程必须自己初始化 COM，否则 WMI 会抛 x_wmi_uninitialised_thread")

    def test_reset_keeps_com_ready(self):
        ipc._get_wmi()
        ipc.reset_wmi_connection()
        ipc._get_wmi()
        self.assertEqual(len(self.com_calls), 1,
                         "重置连接不应导致重复 CoInitialize（连接与 COM 是两回事）")


class TestBackupDiagnostics(WmiTestCase):
    def test_backup_logs_when_read_fails(self):
        """读不到配置时必须留下日志（原来静默返回 False，线上无法定位）。"""
        logged = []
        self._patch(ub, "get_current_ip_config", lambda name: None)
        self._patch(ub, "log_error", lambda action, msg: logged.append(("error", msg)))
        self._patch(ub, "log_exception", lambda action, msg="": logged.append(("exc", msg)))

        self.assertFalse(ub.backup_adapter_config("WLAN 2", "AA:BB:CC:DD:EE:FF", {}))
        self.assertTrue(logged, "读失败必须写日志")
        self.assertIn("WLAN 2", logged[0][1])

    def test_backup_logs_exception(self):
        logged = []
        self._patch(ub, "get_current_ip_config",
                    lambda name: (_ for _ in ()).throw(RuntimeError("COM Error")))
        self._patch(ub, "log_error", lambda action, msg: logged.append(msg))
        self._patch(ub, "log_exception", lambda action, msg="": logged.append(msg))

        self.assertFalse(ub.backup_adapter_config("WLAN 2", "AA:BB:CC:DD:EE:FF", {}))
        self.assertTrue(any("COM Error" in str(m) for m in logged),
                        f"异常内容要进日志: {logged}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
