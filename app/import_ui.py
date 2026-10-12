"""操作员选择或拖入一个 Excel 的窗口。"""

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES, TkinterDnD = None, None

from app.excel_io import read_workbook
from app.file_pick import choose_dropped_file, explain_read_failure


class ImportWindow:
    """选一个 Excel。数据读入后关闭窗口，上传由入口继续。"""

    def __init__(self, database_path, log_path):
        self.database_path = Path(database_path)
        self.log_path = Path(log_path)
        self.selected_path = None
        self.root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
        self.root.title("导入 Excel")
        self.root.geometry("640x360")
        self.root.minsize(520, 300)
        self._build()
        self._enable_drop()

    def _build(self):
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="网页已打开。选择或拖入一个 .xlsx 文件，随后读取并上传").pack(anchor="w")
        ttk.Label(
            frame,
            text="表头需要：sku、asin、country、market、picture、A+introduction、src",
        ).pack(anchor="w", pady=(4, 12))
        self.drop_label = tk.Label(
            frame,
            text="把一个 .xlsx 拖到这里",
            relief="groove",
            borderwidth=2,
            height=6,
            bg="#f7f7f7",
        )
        self.drop_label.pack(fill="both", expand=True)
        ttk.Button(frame, text="选择文件", command=self.choose_file).pack(anchor="w", pady=12)
        self.status = ttk.Label(frame, text=f"队列数据库：{self.database_path}", wraplength=580, justify="left")
        self.status.pack(anchor="w")
        if TkinterDnD is None:
            self.drop_label.configure(text="当前环境不能拖入文件，请使用「选择文件」。")

    def _enable_drop(self):
        if DND_FILES is None or not hasattr(self.root, "drop_target_register"):
            return
        for widget in (self.root, self.drop_label):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self.on_drop)

    def choose_file(self):
        """弹出文件框。取消时不提示。"""
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="选择 Excel",
            filetypes=[("Excel 工作簿", "*.xlsx")],
        )
        if not selected:
            return
        self.import_selected(Path(selected))

    def on_drop(self, event):
        """拖入文件。多个文件或非 xlsx 时提示原因并拒绝。"""
        try:
            raw_paths = self.root.tk.splitlist(event.data or "")
        except tk.TclError as exc:
            messagebox.showerror("无法导入", f"无法识别拖入的内容：{exc}", parent=self.root)
            return
        path, reason = choose_dropped_file(raw_paths)
        if reason:
            messagebox.showwarning("无法导入", reason, parent=self.root)
            return
        self.import_selected(path)

    def import_selected(self, path):
        self.status.configure(text=f"正在读取…\n{path}")
        self.root.update_idletasks()
        try:
            rows = read_workbook(path)
        except Exception as exc:
            messagebox.showerror("无法导入", explain_read_failure(exc, path), parent=self.root)
            self.status.configure(text=f"未完成。\n{path}")
            return
        if not rows:
            messagebox.showwarning("无法导入", "表格里没有数据行。", parent=self.root)
            self.status.configure(text=f"表格里没有数据行。\n{path}")
            return
        self.selected_path = Path(path)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def _report_text(report):
    """把每一行的国家和上传结果写成控制台文字。"""
    lines = []
    for item in report["results"]:
        line = f"{item['sku']}  {item['country']} / {item['market']}  {item['status']}"
        if item.get("reason"):
            line += "：" + item["reason"]
        for slot in item.get("slots") or []:
            slot_line = f"{slot['slot']} → {slot.get('page_slot') or '无'}  {slot['status']}"
            if slot.get("reason"):
                slot_line += "：" + slot["reason"]
            line += "\n" + slot_line
        lines.append(line)
    return "\n".join(lines)
