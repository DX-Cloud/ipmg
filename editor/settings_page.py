"""设置页：按 SETTING_SCHEMA 生成表单，含网卡记忆。"""

import tkinter as tk
from tkinter import ttk

from core import config_manager as cm


class SettingsPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.rows = []
        self._build()

    def _build(self):
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True, padx=14, pady=10)

        ttk.Label(container, text="上次选择网卡 MAC").grid(row=0, column=0, sticky="e", pady=4)
        self.last_mac_var = tk.StringVar()
        ttk.Entry(container, textvariable=self.last_mac_var, width=48).grid(
            row=0, column=1, sticky="w", pady=4)

        for i, (key, label, vtype) in enumerate(cm.SETTING_SCHEMA, start=1):
            ttk.Label(container, text=label).grid(row=i, column=0, sticky="e", pady=4)
            if vtype == "bool":
                var = tk.BooleanVar()
                ttk.Checkbutton(container, variable=var).grid(row=i, column=1, sticky="w", pady=4)
            else:
                var = tk.StringVar()
                ttk.Entry(container, textvariable=var, width=48).grid(
                    row=i, column=1, sticky="w", pady=4)
            self.rows.append((key, vtype, var))

        ttk.Label(container, text="（勾选/填写后点击上方「保存」生效）",
                  foreground="gray").grid(row=len(cm.SETTING_SCHEMA) + 1,
                                          column=0, columnspan=2, pady=10)

    def refresh(self):
        config = self.app.config or {}
        self.last_mac_var.set(config.get("network_adapters", {}).get("last_selected_mac", ""))
        for key, vtype, var in self.rows:
            value = cm.get_setting(config, key)
            if vtype == "bool":
                var.set(bool(value))
            else:
                var.set(str(value or ""))

    def apply(self):
        config = self.app.config
        if config is None:
            return
        config.setdefault("network_adapters", {})["last_selected_mac"] = self.last_mac_var.get().strip()
        for key, vtype, var in self.rows:
            if vtype == "bool":
                cm.set_setting(config, key, bool(var.get()))
            else:
                cm.set_setting(config, key, var.get().strip())
