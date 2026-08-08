"""备份栈与历史记录页（只读展示 + 清空）。"""

import tkinter as tk
from tkinter import messagebox, ttk

from core import config_manager as cm


class BackupPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self._build()

    def _build(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=6, pady=4)
        ttk.Button(toolbar, text="清空所选网卡备份/历史", command=self.clear_selected).pack(side="left", padx=2)
        ttk.Button(toolbar, text="清空全部", command=self.clear_all).pack(side="left", padx=2)

        self.tree = ttk.Treeview(self, columns=("type", "info", "time"), show="tree headings")
        self.tree.heading("type", text="类型")
        self.tree.heading("info", text="内容")
        self.tree.heading("time", text="时间")
        self.tree.column("type", width=70, anchor="center")
        self.tree.column("info", width=380)
        self.tree.column("time", width=170)
        self.tree.pack(fill="both", expand=True, padx=6, pady=4)

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        config = self.app.config or {}
        backups = config.get("backups", {}) or {}
        history = config.get("ip_history", {}) or {}
        if not backups and not history:
            self.tree.insert("", "end", text="（暂无备份与历史记录）")
            return
        for mac, stack in backups.items():
            parent = self.tree.insert("", "end", text=f"备份栈: {mac}", open=True)
            for entry in stack or []:
                mode = "DHCP" if entry.get("is_dhcp") else entry.get("ip", "")
                self.tree.insert(parent, "end", values=(
                    "撤销", f"{mode} / {entry.get('mask', '')}", entry.get("timestamp", "")))
        for mac, records in history.items():
            parent = self.tree.insert("", "end", text=f"历史记录: {mac}", open=True)
            for record in records or []:
                self.tree.insert(parent, "end", values=(
                    "历史", f"{record.get('ip', '')} / {record.get('mask', '')}",
                    record.get("timestamp", "")))

    def _selected_mac(self):
        selection = self.tree.selection()
        if not selection:
            return None
        text = self.tree.item(selection[0]).get("text", "")
        for prefix in ("备份栈: ", "历史记录: "):
            if text.startswith(prefix):
                return text[len(prefix):]
        return None

    def clear_selected(self):
        mac = self._selected_mac()
        if not mac:
            messagebox.showinfo("提示", "请先选择网卡节点（备份栈或历史记录）")
            return
        if messagebox.askyesno("确认", f"清空 {mac} 的备份与历史记录？"):
            cm.clear_adapter_backup(self.app.config, mac)
            cm.clear_ip_history(self.app.config, mac)
            self.app.mark_dirty()
            self.refresh()

    def clear_all(self):
        if messagebox.askyesno("确认", "清空所有网卡的备份与历史记录？"):
            cm.clear_adapter_backup(self.app.config)
            cm.clear_ip_history(self.app.config)
            self.app.mark_dirty()
            self.refresh()
