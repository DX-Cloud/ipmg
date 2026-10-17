"""
需求 1「配置 IP 支持追加」内核侧测试（stdlib unittest，零新依赖）。

安全约定（硬保证）：
- 全程用记账型 FakeWMIConfig 替换 core.ip_configurator._get_adapter_config_by_name；
- 测试期间把 wmi.WMI 换成会抛错的桩，任何真实 WMI 连接尝试都会立刻失败；
- 不调用真实 EnableStatic / EnableDHCP / SetGateways，不改真实网卡，不读写用户配置文件
  （core.config_manager.CONFIG_FILE 已重定向到临时目录）。

运行：python -m unittest discover -s tests -p "test_append.py" -v
"""
import inspect
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import wmi  # noqa: E402  仅用于禁用真实 WMI 连接

from core import config_manager as cm  # noqa: E402
from core import ip_configurator as ipc  # noqa: E402
from core import operations as ops  # noqa: E402

ADAPTER = "以太网-fake"

# inspect_append 的契约字段（UI 依赖，缺一不可）
REPORT_KEYS = {
    "ok", "reason", "existing_ips", "existing_masks", "existing_gateways",
    "merged_ips", "merged_masks",
    "already_present", "is_dhcp", "count", "warnings",
}


class FakeWMIConfig:
    """记账型 fake WMI 网卡配置对象：只记录调用参数并模拟状态变更。"""

    def __init__(self, ips=(), masks=(), gateways=(), dhcp=False):
        self.IPAddress = list(ips)
        self.IPSubnet = list(masks)
        self.DefaultIPGateway = list(gateways)
        self.DHCPEnabled = dhcp
        self.calls = []

    # --- WMI 写方法（fake） ---
    def EnableStatic(self, IPAddress=None, SubnetMask=None, **kw):
        self.calls.append(("EnableStatic", list(IPAddress or []), list(SubnetMask or []), dict(kw)))
        self.IPAddress = list(IPAddress or [])
        self.IPSubnet = list(SubnetMask or [])
        self.DHCPEnabled = False
        return (0,)

    def SetGateways(self, DefaultIPGateway=None, GatewayCostMetric=None, **kw):
        extra = dict(kw)
        if GatewayCostMetric is not None:
            extra["GatewayCostMetric"] = list(GatewayCostMetric)
        self.calls.append(("SetGateways", list(DefaultIPGateway or []), extra))
        self.DefaultIPGateway = list(DefaultIPGateway or [])
        return (0,)

    def EnableDHCP(self, **kw):
        self.calls.append(("EnableDHCP",))
        self.DHCPEnabled = True
        self.IPAddress, self.IPSubnet, self.DefaultIPGateway = [], [], []
        return (0,)

    # --- 断言辅助 ---
    def named(self, name):
        return [c for c in self.calls if c[0] == name]

    def last(self, name):
        found = self.named(name)
        return found[-1] if found else None


def make_ips(count):
    """生成 count 个互不相同的 IPv4（10.0.0.1 …）。"""
    return [f"10.0.0.{i + 1}" for i in range(count)]


class AppendKernelTest(unittest.TestCase):
    """脚手架：替换网卡查找、禁用真实 WMI、隔离配置文件路径。"""

    @classmethod
    def setUpClass(cls):
        cls._real_lookup = ipc._get_adapter_config_by_name
        cls._real_wmi_factory = wmi.WMI
        cls._real_config_file = cm.CONFIG_FILE
        cm.CONFIG_FILE = os.path.join(tempfile.mkdtemp(prefix="ipmg_test_append_"), "config.yaml")

        def _forbidden(*_args, **_kw):
            raise AssertionError("测试禁止建立真实 WMI 连接")

        wmi.WMI = _forbidden

    @classmethod
    def tearDownClass(cls):
        ipc._get_adapter_config_by_name = cls._real_lookup
        wmi.WMI = cls._real_wmi_factory
        cm.CONFIG_FILE = cls._real_config_file

    def setUp(self):
        self.fake = FakeWMIConfig()
        ipc._get_adapter_config_by_name = lambda name: self.fake if name == ADAPTER else None

    def use_fake(self, ips=(), masks=(), gateways=(), dhcp=False):
        self.fake = FakeWMIConfig(ips, masks, gateways, dhcp)
        return self.fake

    @staticmethod
    def plan(ip, mask="255.255.255.0", gateway=None):
        return {"adapter_ip": ip, "mask": mask, "gateway": gateway, "mgmt_url": None}

    # ================= 纯函数 merge_append_ips =================

    def test_merge_appends_new_ip_after_existing(self):
        ips, masks = ipc.merge_append_ips(["10.0.0.5"], ["255.255.255.0"],
                                          "192.168.1.5", "255.255.0.0")
        self.assertEqual(ips, ["10.0.0.5", "192.168.1.5"])
        self.assertEqual(masks, ["255.255.255.0", "255.255.0.0"])

    def test_merge_primary_stays_first(self):
        ips, masks = ipc.merge_append_ips(
            ["10.0.0.5", "172.16.0.5"], ["255.255.255.0", "255.255.0.0"],
            "192.168.1.5", "255.255.255.0")
        self.assertEqual(ips, ["10.0.0.5", "172.16.0.5", "192.168.1.5"])
        self.assertEqual(masks, ["255.255.255.0", "255.255.0.0", "255.255.255.0"])

    def test_merge_dedupes_by_ip_not_by_mask(self):
        """同 IP 异掩码：直接拼接会写出重复地址，必须按 IP 去重（首次出现优先）。"""
        ips, masks = ipc.merge_append_ips(["10.0.0.5"], ["255.255.255.0"],
                                          "10.0.0.5", "255.255.0.0")
        self.assertEqual(ips, ["10.0.0.5"])
        self.assertEqual(len(ips), len(set(ips)), "结果中不得出现重复 IP")
        self.assertEqual(masks, ["255.255.255.0"])

    def test_merge_dedupes_duplicates_already_in_existing(self):
        ips, masks = ipc.merge_append_ips(
            ["10.0.0.5", "10.0.0.5", "192.168.1.5"],
            ["255.255.255.0", "255.255.0.0", "255.255.255.0"],
            "172.16.0.5", "255.255.0.0")
        self.assertEqual(ips, ["10.0.0.5", "192.168.1.5", "172.16.0.5"])
        self.assertEqual(masks, ["255.255.255.0", "255.255.255.0", "255.255.0.0"])

    def test_merge_filters_ipv6_and_empty_entries(self):
        ips, masks = ipc.merge_append_ips(
            ["fe80::1", "10.0.0.5", "", None],
            ["64", "255.255.255.0", "255.255.0.0", "x"],
            "192.168.1.5", "255.255.255.0")
        self.assertEqual(ips, ["10.0.0.5", "192.168.1.5"])
        self.assertEqual(masks, ["255.255.255.0", "255.255.255.0"])

    def test_merge_uses_default_mask_when_new_mask_empty(self):
        ips, masks = ipc.merge_append_ips(["10.0.0.5"], ["255.255.255.0"], "192.168.1.5", "")
        self.assertEqual(masks, ["255.255.255.0", "255.255.255.0"])

    def test_merge_from_empty_existing(self):
        ips, masks = ipc.merge_append_ips([], [], "10.0.0.5", "255.255.255.0")
        self.assertEqual(ips, ["10.0.0.5"])
        self.assertEqual(masks, ["255.255.255.0"])

    def test_merge_accepts_scalar_strings(self):
        ips, masks = ipc.merge_append_ips("10.0.0.5", "255.255.255.0", "192.168.1.5", "255.255.0.0")
        self.assertEqual(ips, ["10.0.0.5", "192.168.1.5"])

    def test_merge_invalid_new_ip_raises_value_error(self):
        for bad in ("", "abc", "fe80::1", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ipc.merge_append_ips(["10.0.0.5"], ["255.255.255.0"], bad, "255.255.255.0")

    def test_merge_uses_project_loose_ipv4_predicate(self):
        """
        沿用项目既有的 `_is_ipv4`（有点无冒号）粗判，与 overwrite 路径一致：
        段值越界（如 10.0.0.256）不在这里拦截，仍由上层校验/WMI 错误码(85/87)兜底。
        """
        ips, _ = ipc.merge_append_ips(["10.0.0.5"], ["255.255.255.0"], "10.0.0.256", "255.255.255.0")
        self.assertEqual(ips, ["10.0.0.5", "10.0.0.256"])

    def test_merge_does_not_mutate_inputs(self):
        src_ips = ["10.0.0.5"]
        src_masks = ["255.255.255.0"]
        ipc.merge_append_ips(src_ips, src_masks, "192.168.1.5", "255.255.0.0")
        self.assertEqual(src_ips, ["10.0.0.5"])
        self.assertEqual(src_masks, ["255.255.255.0"])

    # ================= apply_static(mode="append") =================

    def test_append_unions_ips_and_writes_once(self):
        self.use_fake(["10.0.0.5"], ["255.255.255.0"], ["10.0.0.1"])
        ok = ops.apply_static(ADAPTER, self.plan("192.168.1.5", "255.255.0.0"), mode="append")

        self.assertTrue(ok)
        enable = self.fake.named("EnableStatic")
        self.assertEqual(len(enable), 1, "EnableStatic 必须恰好调用 1 次")
        self.assertEqual(enable[0][1], ["10.0.0.5", "192.168.1.5"])
        self.assertEqual(enable[0][2], ["255.255.255.0", "255.255.0.0"])
        gw = self.fake.named("SetGateways")
        self.assertEqual(len(gw), 1,
                         "追加模式必须回写原网关：写入后若不再显式设置，部分场景会丢默认网关")
        self.assertEqual(gw[0][1], ["10.0.0.1"], "回写的必须是写入前读到的原网关值")
        self.assertEqual(self.fake.IPAddress, ["10.0.0.5", "192.168.1.5"])

    def test_append_ignores_plan_gateway(self):
        """plan 里的网关（目标设备网段）不得覆盖网卡原有网关。"""
        self.use_fake(["10.0.0.5"], ["255.255.255.0"], ["10.0.0.1"])
        ops.apply_static(ADAPTER, self.plan("192.168.1.5", "255.255.0.0", gateway="10.9.9.1"),
                         mode="append")
        gw = self.fake.named("SetGateways")
        self.assertEqual(len(gw), 1)
        self.assertEqual(gw[0][1], ["10.0.0.1"],
                         "只能回写原网关，不能改成 plan 里的 10.9.9.1")
        self.assertEqual(self.fake.DefaultIPGateway, ["10.0.0.1"], "网关值不应被改动")

    def test_append_without_existing_gateway_skips_setgateways(self):
        """原网卡本来没有网关时，追加不得凭空设置一个网关。"""
        self.use_fake(["10.0.0.5"], ["255.255.255.0"], [])
        ops.apply_static(ADAPTER, self.plan("192.168.1.5", "255.255.0.0", gateway="10.9.9.1"),
                         mode="append")
        self.assertEqual(self.fake.named("SetGateways"), [])
        self.assertEqual(self.fake.DefaultIPGateway, [])

    def test_append_keeps_primary_first_with_multiple_existing(self):
        self.use_fake(["10.0.0.5", "172.16.0.5"], ["255.255.255.0", "255.255.0.0"])
        ops.apply_static(ADAPTER, self.plan("192.168.1.5", "255.255.0.0"), mode="append")
        written = self.fake.last("EnableStatic")[1]
        self.assertEqual(written[0], "10.0.0.5", "主 IP 必须仍在数组第 0 位")
        self.assertEqual(written, ["10.0.0.5", "172.16.0.5", "192.168.1.5"])

    def test_append_is_idempotent_when_already_present(self):
        self.use_fake(["10.0.0.5", "192.168.1.5"],
                      ["255.255.255.0", "255.255.0.0"], ["10.0.0.1"])
        ok = ops.apply_static(ADAPTER, self.plan("192.168.1.5", "255.255.0.0"), mode="append")
        self.assertTrue(ok)
        self.assertEqual(self.fake.calls, [], "幂等情形不得写 WMI")

    def test_append_same_ip_different_mask_raises(self):
        self.use_fake(["10.0.0.5"], ["255.255.0.0"])
        with self.assertRaises(ValueError) as ctx:
            ops.apply_static(ADAPTER, self.plan("10.0.0.5", "255.255.255.0"), mode="append")
        self.assertIn("掩码", str(ctx.exception))
        self.assertEqual(self.fake.calls, [], "冲突时不得写 WMI")

    def test_append_without_adapter_config_raises_runtime_error(self):
        ipc._get_adapter_config_by_name = lambda name: None
        with self.assertRaises(RuntimeError):
            ops.apply_static(ADAPTER, self.plan("192.168.1.5"), mode="append")

    def test_append_invalid_target_ip_raises_value_error(self):
        for bad in ("", "abc", "fe80::1"):
            with self.subTest(bad=bad):
                self.use_fake(["10.0.0.5"], ["255.255.255.0"])
                with self.assertRaises(ValueError):
                    ops.apply_static(ADAPTER, self.plan(bad), mode="append")
                self.assertEqual(self.fake.calls, [])

    def test_append_rejects_dhcp_without_usable_base(self):
        for ips in (["169.254.10.10"], []):
            with self.subTest(ips=ips):
                self.use_fake(ips, ["255.255.0.0"] if ips else [], dhcp=True)
                with self.assertRaises(RuntimeError):
                    ops.apply_static(ADAPTER, self.plan("192.168.1.5"), mode="append")
                self.assertEqual(self.fake.calls, [])

    def test_append_allows_dhcp_with_real_lease(self):
        self.use_fake(["192.168.1.100"], ["255.255.255.0"], dhcp=True)
        ok = ops.apply_static(ADAPTER, self.plan("10.0.0.5"), mode="append")
        self.assertTrue(ok)
        self.assertEqual(self.fake.last("EnableStatic")[1], ["192.168.1.100", "10.0.0.5"])

    def test_append_rejects_when_exceeding_max(self):
        self.use_fake(make_ips(ipc.MAX_IPV4_PER_ADAPTER),
                      ["255.255.255.0"] * ipc.MAX_IPV4_PER_ADAPTER)
        with self.assertRaises(ValueError) as ctx:
            ops.apply_static(ADAPTER, self.plan("192.168.99.5"), mode="append")
        self.assertIn(str(ipc.MAX_IPV4_PER_ADAPTER), str(ctx.exception))
        self.assertEqual(self.fake.calls, [])

    def test_append_allows_exactly_max(self):
        self.use_fake(make_ips(ipc.MAX_IPV4_PER_ADAPTER - 1),
                      ["255.255.255.0"] * (ipc.MAX_IPV4_PER_ADAPTER - 1))
        ok = ops.apply_static(ADAPTER, self.plan("192.168.99.5"), mode="append")
        self.assertTrue(ok)
        self.assertEqual(len(self.fake.last("EnableStatic")[1]), ipc.MAX_IPV4_PER_ADAPTER)

    def test_append_rereads_config_after_inspect(self):
        """预检之后网卡被外部改动：apply 必须重新读取（避免 TOCTOU）。"""
        self.use_fake(["10.0.0.5"], ["255.255.255.0"])
        report = ops.inspect_append(ADAPTER, self.plan("192.168.1.5", "255.255.0.0"))
        self.assertEqual(report["count"], 2)

        self.fake.IPAddress = ["10.0.0.5", "172.16.0.5"]
        self.fake.IPSubnet = ["255.255.255.0", "255.255.0.0"]
        self.fake.calls.clear()
        ops.apply_static(ADAPTER, self.plan("192.168.1.5", "255.255.0.0"), mode="append")
        self.assertEqual(self.fake.last("EnableStatic")[1],
                         ["10.0.0.5", "172.16.0.5", "192.168.1.5"])

    def test_invalid_mode_raises_value_error(self):
        self.use_fake(["10.0.0.5"], ["255.255.255.0"])
        for bad in ("append ", "APPEND", "merge", ""):
            with self.subTest(mode=bad):
                with self.assertRaises(ValueError):
                    ops.apply_static(ADAPTER, self.plan("192.168.1.5"), mode=bad)
        self.assertEqual(self.fake.calls, [])

    # ================= overwrite 模式零回归 =================

    def test_overwrite_single_ip_matches_legacy_call(self):
        self.use_fake(["10.0.0.5"], ["255.255.255.0"], ["10.0.0.1"])
        ok = ops.apply_static(ADAPTER, self.plan("192.168.100.50", "255.255.255.0", "192.168.100.1"))
        self.assertTrue(ok)
        self.assertEqual(self.fake.calls, [
            ("EnableStatic", ["192.168.100.50"], ["255.255.255.0"], {}),
            ("SetGateways", ["192.168.100.1"], {}),
        ])

    def test_overwrite_multi_ip_still_writes_single_address(self):
        """网卡本来有主+次要 IP：覆盖模式仍只写 1 个地址（与修复前完全一致）。"""
        self.use_fake(["10.0.0.5", "192.168.1.5"],
                      ["255.255.255.0", "255.255.0.0"], ["10.0.0.1"])
        ops.apply_static(ADAPTER, self.plan("192.168.100.50", "255.255.255.0", "192.168.100.1"),
                         mode="overwrite")
        enable = self.fake.named("EnableStatic")
        self.assertEqual(len(enable), 1)
        self.assertEqual(enable[0][1], ["192.168.100.50"])
        self.assertEqual(enable[0][2], ["255.255.255.0"])

    def test_overwrite_without_gateway_skips_setgateways(self):
        self.use_fake(["10.0.0.5"], ["255.255.255.0"])
        ops.apply_static(ADAPTER, self.plan("192.168.100.50"))
        self.assertEqual(self.fake.named("SetGateways"), [])
        self.assertEqual(self.fake.last("EnableStatic")[1], ["192.168.100.50"])

    def test_explicit_overwrite_equals_default(self):
        self.use_fake(["10.0.0.5"], ["255.255.255.0"], ["10.0.0.1"])
        ops.apply_static(ADAPTER, self.plan("192.168.100.50", "255.255.255.0", "192.168.100.1"),
                         mode="overwrite")
        explicit = list(self.fake.calls)

        self.use_fake(["10.0.0.5"], ["255.255.255.0"], ["10.0.0.1"])
        ops.apply_static(ADAPTER, self.plan("192.168.100.50", "255.255.255.0", "192.168.100.1"))
        self.assertEqual(explicit, self.fake.calls)

    def test_set_static_ip_positional_signature_unchanged(self):
        """UI 依赖 set_static_ip(name, ip, mask, gw) 的既有位置参数顺序。"""
        params = list(inspect.signature(ipc.set_static_ip).parameters)
        self.assertEqual(params[:4], ["adapter_name", "ip", "mask", "gateway"])

    def test_append_mode_signature_default_is_overwrite(self):
        params = inspect.signature(ops.apply_static).parameters
        self.assertEqual(list(params)[:3], ["adapter_name", "plan", "mode"])
        self.assertEqual(params["mode"].default, "overwrite")

    # ================= inspect_append =================

    def test_inspect_reports_union_without_writing(self):
        self.use_fake(["10.0.0.5"], ["255.255.255.0"], ["10.0.0.1"])
        report = ops.inspect_append(ADAPTER, self.plan("192.168.1.5", "255.255.0.0"))

        self.assertEqual(set(report.keys()), REPORT_KEYS)
        self.assertTrue(report["ok"])
        self.assertEqual(report["existing_ips"], ["10.0.0.5"])
        self.assertEqual(report["existing_masks"], ["255.255.255.0"])
        self.assertEqual(report["merged_ips"], ["10.0.0.5", "192.168.1.5"])
        self.assertEqual(report["merged_masks"], ["255.255.255.0", "255.255.0.0"])
        self.assertFalse(report["already_present"])
        self.assertFalse(report["is_dhcp"])
        self.assertEqual(report["count"], 2)
        self.assertEqual(self.fake.calls, [], "预检必须只读")

    def test_inspect_already_present_is_idempotent(self):
        self.use_fake(["10.0.0.5", "192.168.1.5"],
                      ["255.255.255.0", "255.255.0.0"])
        report = ops.inspect_append(ADAPTER, self.plan("192.168.1.5", "255.255.0.0"))
        self.assertTrue(report["ok"])
        self.assertTrue(report["already_present"])
        self.assertEqual(report["merged_ips"], ["10.0.0.5", "192.168.1.5"])
        self.assertEqual(report["count"], 2)

    def test_inspect_same_ip_different_mask_is_rejected(self):
        self.use_fake(["10.0.0.5"], ["255.255.0.0"])
        report = ops.inspect_append(ADAPTER, self.plan("10.0.0.5", "255.255.255.0"))
        self.assertFalse(report["ok"])
        self.assertIn("掩码", report["reason"])
        self.assertIn("覆盖", report["reason"])
        self.assertEqual(self.fake.calls, [])

    def test_inspect_dhcp_warning(self):
        self.use_fake(["192.168.1.100"], ["255.255.255.0"], dhcp=True)
        report = ops.inspect_append(ADAPTER, self.plan("10.0.0.5"))
        self.assertTrue(report["ok"])
        self.assertTrue(report["is_dhcp"])
        self.assertTrue(any("DHCP" in w for w in report["warnings"]), report["warnings"])

    def test_inspect_dhcp_apipa_is_rejected(self):
        for ips in (["169.254.10.10"], []):
            with self.subTest(ips=ips):
                self.use_fake(ips, ["255.255.0.0"] if ips else [], dhcp=True)
                report = ops.inspect_append(ADAPTER, self.plan("10.0.0.5"))
                self.assertFalse(report["ok"])
                self.assertIn("DHCP", report["reason"])
                self.assertEqual(report["warnings"], [])

    def test_inspect_without_adapter_config(self):
        ipc._get_adapter_config_by_name = lambda name: None
        report = ops.inspect_append(ADAPTER, self.plan("10.0.0.5"))
        self.assertEqual(set(report.keys()), REPORT_KEYS)
        self.assertFalse(report["ok"])
        self.assertTrue(report["reason"])
        self.assertEqual(report["existing_ips"], [])
        self.assertEqual(report["count"], 0)

    def test_inspect_invalid_target_ip(self):
        self.use_fake(["10.0.0.5"], ["255.255.255.0"])
        report = ops.inspect_append(ADAPTER, self.plan(""))
        self.assertFalse(report["ok"])
        self.assertTrue(report["reason"])

    def test_inspect_warns_at_threshold(self):
        below = ipc.WARN_IPV4_COUNT - 2      # +1 → WARN-1，无数量告警
        self.use_fake(make_ips(below), ["255.255.255.0"] * below)
        report = ops.inspect_append(ADAPTER, self.plan("192.168.99.5"))
        self.assertTrue(report["ok"])
        self.assertFalse(any("地址过多" in w for w in report["warnings"]), report["warnings"])
        self.assertEqual(report["count"], ipc.WARN_IPV4_COUNT - 1)

        at = ipc.WARN_IPV4_COUNT - 1         # +1 → WARN，出现数量告警
        self.use_fake(make_ips(at), ["255.255.255.0"] * at)
        report = ops.inspect_append(ADAPTER, self.plan("192.168.99.5"))
        self.assertTrue(report["ok"])
        self.assertEqual(report["count"], ipc.WARN_IPV4_COUNT)
        self.assertTrue(any("地址过多" in w for w in report["warnings"]), report["warnings"])

    def test_inspect_warns_when_gateway_present(self):
        self.use_fake(["10.0.0.5"], ["255.255.255.0"], ["10.0.0.1"])
        report = ops.inspect_append(ADAPTER, self.plan("192.168.1.5", "255.255.0.0"))
        self.assertTrue(any("网关" in w for w in report["warnings"]), report["warnings"])

    def test_inspect_rejects_when_exceeding_max(self):
        self.use_fake(make_ips(ipc.MAX_IPV4_PER_ADAPTER),
                      ["255.255.255.0"] * ipc.MAX_IPV4_PER_ADAPTER)
        report = ops.inspect_append(ADAPTER, self.plan("192.168.99.5"))
        self.assertFalse(report["ok"])
        self.assertIn(str(ipc.MAX_IPV4_PER_ADAPTER), report["reason"])
        self.assertEqual(report["count"], ipc.MAX_IPV4_PER_ADAPTER + 1)
        self.assertEqual(self.fake.calls, [])

    def test_constants(self):
        self.assertEqual(ipc.MAX_IPV4_PER_ADAPTER, 32)
        self.assertEqual(ipc.WARN_IPV4_COUNT, 8)

    def test_config_file_is_isolated(self):
        """测试期间任何 save_config 都只能写到临时目录。"""
        self.assertTrue(cm.CONFIG_FILE.startswith(tempfile.gettempdir()), cm.CONFIG_FILE)
        self.assertNotIn(os.path.join(os.path.expanduser("~"), "ipmg"), cm.CONFIG_FILE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
