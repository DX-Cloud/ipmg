"""设备管理页：表格展示 + 增删改表单（校验与主程序一致）。"""

import tkinter as tk
from tkinter import messagebox, ttk

from core import config_manager as cm
from core.network_utils import validate_ip, validate_subnet_mask


class DevicePage(ttk.Frame):
    COLUMNS = ("name", "group", "device_ip", "subnet_mask", "gateway",
               "ip_mode", "adapter_ip", "management_url", "favorite")
    HEADERS = {
        "name": "名称", "group": "分组", "device_ip": "设备IP",
        "subnet_mask": "子网掩码", "gateway": "网关", "ip_mode": "IP模式",
        "adapter_ip": "网卡IP(手动)", "management_url": "管理URL", "favorite": "收藏",
    }

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self._build()

    def _build(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=6, pady=4)
        ttk.Button(toolbar, text="添加", command=self.add_device).pack(side="left", padx=2)
        ttk.Button(toolbar, text="编辑", command=self.edit_device).pack(side="left", padx=2)
        ttk.Button(toolbar, text="删除", command=self.delete_device).pack(side="left", padx=2)
        ttk.Label(toolbar, text="双击行可编辑", foreground="gray").pack(side="left", padx=12)

        self.tree = ttk.Treeview(self, columns=list(self.COLUMNS), show="headings")
        for col in self.COLUMNS:
            self.tree.heading(col, text=self.HEADERS[col])
            width = 220 if col in ("management_url", "name") else 110
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=6, pady=4)
        self.tree.bind("<Double-1>", lambda _e: self.edit_device())

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for i, device in enumerate(self.app.config.get("devices", [])):
            values = [str(device.get(col, "")) for col in self.COLUMNS]
            if device.get("favorite"):
                values[-1] = "★"
            self.tree.insert("", "end", iid=str(i), values=values)

    def _selected_index(self):
        selection = self.tree.selection()
        if not selection:
            return None
        return int(selection[0])

    def add_device(self):
        dialog = DeviceDialog(self, None)
        self.wait_window(dialog)
        if dialog.result:
            cm.add_device(self.app.config, dialog.result)
            self.app.mark_dirty()
            self.refresh()

    def edit_device(self):
        index = self._selected_index()
        if index is None:
            messagebox.showinfo("提示", "请先选择要编辑的设备")
            return
        dialog = DeviceDialog(self, self.app.config["devices"][index])
        self.wait_window(dialog)
        if dialog.result:
            cm.update_device(self.app.config, index, dialog.result)
            self.app.mark_dirty()
            self.refresh()

    def delete_device(self):
        index = self._selected_index()
        if index is None:
            messagebox.showinfo("提示", "请先选择要删除的设备")
            return
        name = self.app.config["devices"][index].get("name", "")
        if messagebox.askyesno("确认删除", f"确认删除设备 '{name}'？"):
            cm.delete_device(self.app.config, index)
            self.app.mark_dirty()
            self.refresh()


class DeviceDialog(tk.Toplevel):
    """设备添加/编辑表单对话框。"""

    FIELDS = [
        ("name", "设备名称 *", ""),
        ("group", "设备分组/站点", ""),
        ("device_ip", "设备默认IP *", ""),
        ("subnet_mask", "子网掩码 *", "255.255.255.0"),
        ("gateway", "网关（可选）", ""),
        ("management_url", "管理页面URL", "https://{device_ip}"),
    ]

    def __init__(self, parent, device):
        super().__init__(parent)
        self.title("编辑设备" if device else "添加设备")
        self.resizable(False, False)
        self.result = None
        self.vars = {}

        for i, (key, label, default) in enumerate(self.FIELDS):
            ttk.Label(self, text=label).grid(row=i, column=0, sticky="e", padx=6, pady=4)
            current = str((device or {}).get(key, "") or "")
            var = tk.StringVar(value=current or default)
            self.vars[key] = var
            ttk.Entry(self, textvariable=var, width=46).grid(row=i, column=1, padx=6, pady=4)

        row = len(self.FIELDS)
        ttk.Label(self, text="网卡IP策略").grid(row=row, column=0, sticky="e", padx=6, pady=4)
        old_mode = (device or {}).get("ip_mode", "auto")
        self.ip_mode_var = tk.StringVar(value=old_mode if old_mode in ("auto", "manual") else "auto")
        ttk.Combobox(self, textvariable=self.ip_mode_var, values=("auto", "manual"),
                     state="readonly", width=43).grid(row=row, column=1, sticky="w", padx=6, pady=4)

        row += 1
        ttk.Label(self, text="网卡IP（手动模式）").grid(row=row, column=0, sticky="e", padx=6, pady=4)
        self.adapter_ip_var = tk.StringVar(value=str((device or {}).get("adapter_ip", "") or ""))
        ttk.Entry(self, textvariable=self.adapter_ip_var, width=46).grid(row=row, column=1, padx=6, pady=4)

        row += 1
        self.fav_var = tk.BooleanVar(value=bool((device or {}).get("favorite", False)))
        ttk.Checkbutton(self, text="设为收藏", variable=self.fav_var).grid(
            row=row, column=1, sticky="w", padx=6, pady=4)

        row += 1
        self.error_label = ttk.Label(self, text="", foreground="red")
        self.error_label.grid(row=row, column=0, columnspan=2, padx=6, pady=2)

        row += 1
        buttons = ttk.Frame(self)
        buttons.grid(row=row, column=0, columnspan=2, pady=8)
        ttk.Button(buttons, text="确定", command=self._ok).pack(side="left", padx=8)
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="left", padx=8)

        self.transient(parent)
        self.grab_set()

    def _ok(self):
        data = {key: var.get().strip() for key, var in self.vars.items()}
        data["ip_mode"] = self.ip_mode_var.get()
        data["adapter_ip"] = self.adapter_ip_var.get().strip()
        data["favorite"] = bool(self.fav_var.get())

        errors = []
        if not data["name"]:
            errors.append("设备名称不能为空")
        if not validate_ip(data["device_ip"]):
            errors.append("设备IP格式无效")
        if not validate_subnet_mask(data["subnet_mask"]):
            errors.append("子网掩码格式无效")
        if data["gateway"] and not validate_ip(data["gateway"]):
            errors.append("网关格式无效")
        if data["ip_mode"] == "manual" and not validate_ip(data["adapter_ip"]):
            errors.append("手动模式下网卡IP格式无效")
        errors.extend(cm.validate_device(data))
        if errors:
            self.error_label.config(text="\n".join(errors))
            return
        self.result = data
        self.destroy()
