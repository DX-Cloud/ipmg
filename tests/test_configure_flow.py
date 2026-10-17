# -*- coding: utf-8 -*-
"""
配置 IP 流程（Y/n/a 三选）的集成测试 —— 需求 1 的端到端判据。

用 fake 打桩掉网卡枚举/设备选择/WMI，只验证 ui/app.py 的流程编排：
选模式 → 备份 → 应用 → 回读校验，以及"取消"和"追加不可行"两条安全分支。

运行：python -m unittest discover -s tests -p "test_configure_flow.py" -t . -v
"""
import builtins
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import operations  # noqa: E402
from ui import app, widgets  # noqa: E402


class FakeAdapter:
    name = "以太网"
    mac = "AA:BB:CC:DD:EE:FF"
    type_name = "有线"
    is_up = True
    display_ip = "10.0.0.5"


DEVICE = {
    "name": "测试设备",
    "device_ip": "192.0.2.11",
    "ip_mode": "auto",
    "subnet_mask": "255.255.255.0",
    "gateway": "192.0.2.1",
    "management_url": "",
}


def current_config(ips, masks, gateways, dhcp=False):
    return {
        "ip": ips[0] if ips else "",
        "mask": masks[0] if masks else "",
        "gateway": gateways[0] if gateways else "",
        "is_dhcp": dhcp,
        "ips": list(ips),
        "masks": list(masks),
        "gateways": list(gateways),
    }


class ConfigureFlowHarness:
    """把配置流程的外部依赖全部替换成可断言的假实现。"""

    def __init__(self, choice, current, already_present=False):
        self.choice = choice
        self.current = current
        self.calls = {"apply": [], "backup": 0, "logs": [], "hooks": 0}
        self.already_present = already_present
        self._saved = []

    def __enter__(self):
        self._saved = [
            (app, "show_header", app.show_header),
            (app, "get_network_adapters", app.get_network_adapters),
            (app, "get_last_adapter_mac", app.get_last_adapter_mac),
            (app, "select_adapter", app.select_adapter),
            (app, "get_devices", app.get_devices),
            (app, "_pick_device", app._pick_device),
            (app, "get_current_ip_config", app.get_current_ip_config),
            (app, "log_operation", app.log_operation),
            (widgets, "pick_option", widgets.pick_option),
            (widgets, "confirm_three", widgets.confirm_three),
            (operations, "get_current_ip_config", operations.get_current_ip_config),
            (operations, "backup_current", operations.backup_current),
            (operations, "apply_static", operations.apply_static),
            (operations, "save_state", operations.save_state),
            (operations, "run_hooks", operations.run_hooks),
            (builtins, "input", builtins.input),
            (app.cm, "get_setting", app.cm.get_setting),
        ]

        app.show_header = lambda config: None
        app.get_network_adapters = lambda filter_virtual=True: [FakeAdapter()]
        app.get_last_adapter_mac = lambda config: ""
        app.select_adapter = lambda adapters, mac: 0
        app.get_devices = lambda config: [DEVICE]
        app._pick_device = lambda config, adapter: dict(DEVICE)
        app.get_current_ip_config = lambda name: dict(self.current)
        app.log_operation = lambda *a, **k: self.calls["logs"].append(a)
        app.cm.get_setting = lambda config, key, default=None: (
            "overwrite" if key.endswith("default_mode") else default)

        widgets.pick_option = lambda *a, **k: 0
        widgets.confirm_three = lambda prompt, default=widgets.CHOICE_OVERWRITE: self.choice

        operations.get_current_ip_config = lambda name: dict(self.current)

        def fake_inspect(adapter_name, plan):
            from core.ip_configurator import merge_append_ips
            ips = list(self.current.get("ips") or [])
            masks = list(self.current.get("masks") or [])
            target = plan["adapter_ip"]
            target_mask = plan.get("mask") or "255.255.255.0"
            if self.current.get("is_dhcp") and (
                    not ips or all(a.startswith("169.254.") for a in ips)):
                return {"ok": False, "reason": "DHCP 且无可用地址，请改用覆盖",
                        "existing_ips": ips, "existing_masks": masks,
                        "existing_gateways": list(self.current.get("gateways") or []),
                        "merged_ips": [], "merged_masks": [], "already_present": False,
                        "is_dhcp": True, "count": len(ips), "warnings": []}
            present = self.already_present or target in ips
            merged_ips, merged_masks = merge_append_ips(ips, masks, target, target_mask)
            return {"ok": True, "reason": "", "existing_ips": ips, "existing_masks": masks,
                    "existing_gateways": list(self.current.get("gateways") or []),
                    "merged_ips": merged_ips, "merged_masks": merged_masks,
                    "already_present": present, "is_dhcp": bool(self.current.get("is_dhcp")),
                    "count": len(merged_ips), "warnings": []}

        operations.inspect_append = fake_inspect
        operations.backup_current = lambda config, adapter: (
            self.calls.__setitem__("backup", self.calls["backup"] + 1) or True)

        def fake_apply(adapter_name, plan, mode="overwrite"):
            self.calls["apply"].append(mode)
            return True

        operations.apply_static = fake_apply
        operations.save_state = lambda config, mac: None
        operations.run_hooks = lambda config, name, **kw: self.calls.__setitem__(
            "hooks", self.calls["hooks"] + 1)
        builtins.input = lambda *a, **k: ""
        return self

    def __exit__(self, *exc):
        for module, name, value in self._saved:
            setattr(module, name, value)
        return False


def run_flow(choice, current, already_present=False):
    buf = io.StringIO()
    with ConfigureFlowHarness(choice, current, already_present) as h:
        with redirect_stdout(buf):
            app.run_configure_flow({"settings": {}, "devices": [DEVICE]})
    return h, buf.getvalue()


class TestConfigureFlowChoices(unittest.TestCase):
    CURRENT = current_config(["10.0.0.5"], ["255.255.255.0"], ["10.0.0.1"])

    def test_default_overwrite_path(self):
        h, out = run_flow(widgets.CHOICE_OVERWRITE, self.CURRENT)
        self.assertEqual(h.calls["apply"], ["overwrite"])
        self.assertEqual(h.calls["backup"], 1, "覆盖模式仍然先备份")
        self.assertIn("IP配置成功", out)
        self.assertIn("配置IP", [log[0] for log in h.calls["logs"]])

    def test_append_path(self):
        h, out = run_flow(widgets.CHOICE_APPEND, self.CURRENT)
        self.assertEqual(h.calls["apply"], ["append"], "选 a 必须以 append 模式写入")
        self.assertEqual(h.calls["backup"], 1, "追加前也要备份（撤销的唯一手段）")
        self.assertIn("IP追加成功", out)
        self.assertIn("追加IP", [log[0] for log in h.calls["logs"]],
                      "追加要用独立的动作名，便于日志检索")
        self.assertIn("10.0.0.5", out, "应展示网卡写入前的地址")

    def test_cancel_does_nothing(self):
        h, out = run_flow(widgets.CHOICE_CANCEL, self.CURRENT)
        self.assertEqual(h.calls["apply"], [], "取消绝不能写网卡")
        self.assertEqual(h.calls["backup"], 0, "取消不应产生备份")
        self.assertIn("已取消", out)

    def test_append_rejected_falls_back_to_explicit_error(self):
        """追加不可行（DHCP + APIPA）时必须明确拒绝，不能悄悄降级成覆盖。"""
        apipa = current_config(["169.254.10.20"], ["255.255.0.0"], [], dhcp=True)
        h, out = run_flow(widgets.CHOICE_APPEND, apipa)
        self.assertEqual(h.calls["apply"], [], "不可追加时不得写入")
        self.assertIn("无法追加", out)

    def test_append_already_present_short_circuits(self):
        h, out = run_flow(widgets.CHOICE_APPEND, self.CURRENT, already_present=True)
        self.assertEqual(h.calls["apply"], [], "地址已存在时无需写 WMI")
        self.assertEqual(h.calls["backup"], 0, "没有实际改动就不必备份")
        self.assertIn("无需追加", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
