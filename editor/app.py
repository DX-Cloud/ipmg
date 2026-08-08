"""ipmg 配置可视化编辑器主窗口（tkinter）。"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from core import config_manager as cm
from core.version import APP_VERSION_DISPLAY
from editor import backup_page, device_page, settings_page, storage


class EditorApp:
    def __init__(self, root):
        self.root = root
        self.config = None
        self.path = cm.CONFIG_FILE
        self.dirty = False
        root.title(f"ipmg 配置编辑器 {APP_VERSION_DISPLAY}")
        root.geometry("980x640")
        self._build()
        self._load_default()

    def _build(self):
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill="x", padx=6, pady=6)
        ttk.Button(toolbar, text="打开", command=self.open_file).pack(side="left", padx=2)
        ttk.Button(toolbar, text="保存", command=self.save_file).pack(side="left", padx=2)
        ttk.Button(toolbar, text="另存为", command=self.save_as).pack(side="left", padx=2)
        ttk.Label(toolbar, text=f"默认配置: {cm.CONFIG_FILE}", foreground="gray").pack(
            side="left", padx=12)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=6)
        self.device_page = device_page.DevicePage(self.notebook, self)
        self.settings_page = settings_page.SettingsPage(self.notebook, self)
        self.backup_page = backup_page.BackupPage(self.notebook, self)
        self.notebook.add(self.device_page, text="设备")
        self.notebook.add(self.settings_page, text="设置")
        self.notebook.add(self.backup_page, text="备份与历史")

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w",
                  relief="sunken").pack(fill="x", side="bottom")

    def _load_default(self):
        if os.path.exists(cm.CONFIG_FILE):
            self._load(cm.CONFIG_FILE)
        else:
            self.status_var.set(f"默认配置不存在: {cm.CONFIG_FILE}，请使用「打开」选择文件")

    def _load(self, path):
        config, error = storage.load(path)
        if error:
            messagebox.showerror("加载失败", error)
            self.status_var.set(error)
            return
        self.config = config
        self.path = path
        self.dirty = False
        self.device_page.refresh()
        self.settings_page.refresh()
        self.backup_page.refresh()
        self.status_var.set(f"已加载: {path}")

    def open_file(self):
        path = filedialog.askopenfilename(
            title="选择配置文件",
            initialdir=os.path.dirname(self.path) or os.path.expanduser("~"),
            filetypes=[("YAML 文件", "*.yaml *.yml"), ("所有文件", "*.*")],
        )
        if not path:
            return
        if self.dirty and not messagebox.askyesno("未保存", "当前修改尚未保存，继续打开将丢失修改？"):
            return
        self._load(path)

    def _apply_pages(self):
        if self.config is not None:
            self.settings_page.apply()

    def save_file(self):
        if self.config is None:
            messagebox.showinfo("提示", "尚未加载配置")
            return
        self._apply_pages()
        error = storage.save(self.path, self.config)
        if error:
            messagebox.showerror("保存失败", error)
            return
        self.dirty = False
        self.status_var.set(f"已保存: {self.path}（若主程序运行中，重启后生效）")

    def save_as(self):
        if self.config is None:
            messagebox.showinfo("提示", "尚未加载配置")
            return
        path = filedialog.asksaveasfilename(
            title="另存为", defaultextension=".yaml",
            filetypes=[("YAML 文件", "*.yaml"), ("所有文件", "*.*")],
        )
        if path:
            self.path = path
            self.save_file()

    def mark_dirty(self):
        self.dirty = True
        self.status_var.set(f"已修改: {self.path}（未保存）")


def main():
    root = tk.Tk()
    EditorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
