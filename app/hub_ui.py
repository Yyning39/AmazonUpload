"""启动窗口：欧洲五国账号，以及图片和 A+ 的上传记录。"""

from datetime import datetime
from pathlib import Path
import csv
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.browser import COUNTRY_PORTS, SELLER_CENTRAL_DOMAINS, _choose_country_target, country_code, launch_chrome, port_for
from app.cdp import PageSession, list_targets
from app.file_pick import explain_read_failure
from app.pipeline import run

# 欧洲五国。英国、德国、法国、意大利、西班牙。
EU_COUNTRIES = (
    ("UK", "英国"),
    ("DE", "德国"),
    ("FR", "法国"),
    ("IT", "意大利"),
    ("ES", "西班牙"),
)
EU_NAMES = {code: name for code, name in EU_COUNTRIES}
NAME_TO_CODE = {name: code for code, name in EU_COUNTRIES}

DARK = "#151515"
RED = "#d71920"
BG = "#ffffff"
TEXT = "#151515"
MUTED = "#666666"

PICTURE_MERGE = ("time", "country", "market", "sku", "asin")
PICTURE_MERGE_HEADS = ("时间", "国家", "站点", "sku", "asin")
PICTURE_DETAIL = ("file", "page_slot", "status", "reason")
PICTURE_DETAIL_HEADS = ("图片", "页面位置", "结果", "原因")
APLUS_DETAIL = ("time", "sku", "asin", "country", "market", "status", "reason")
APLUS_DETAIL_HEADS = ("时间", "sku", "asin", "国家", "站点", "结果", "原因")
LINE = "#d9d9d9"

# 未登录欢迎页看注册地址和登录地址，不看链接文字。已登录后台才有账户切换条。
_LOGIN_STATE_JS = """
(() => {
  const links = Array.from(document.querySelectorAll('a'));
  const hrefOf = (el) => el.getAttribute('href') || '';
  const form = !!document.querySelector('#ap_email, #ap_signin_form');
  const href = location.href || '';
  const registerLink = links.some(el => hrefOf(el).indexOf('/gp/on-board/workflow/Registration/login.html') >= 0);
  const loginLink = links.some(el => hrefOf(el).indexOf('sc-login') >= 0);
  return {
    welcome: form || href.indexOf('/ap/signin') >= 0 || registerLink || loginLink,
    loggedIn: !!document.querySelector('.dropdown-account-switcher-header')
  };
})()
"""


def _mark_button(button, chosen):
    """选中用深底白字，未选中用白底灰边。"""
    if chosen:
        button.configure(
            bg="#1c1c1c",
            fg="#ffffff",
            activebackground="#333333",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 11, "bold"),
        )
    else:
        button.configure(
            bg="#ffffff",
            fg="#222222",
            activebackground="#f3f3f3",
            activeforeground="#222222",
            relief="solid",
            bd=1,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 11),
        )


class CheckTable(tk.Frame):
    """带勾选的表格。merge_keys 那几列按组合成一格，detail_keys 每条记录单独一行。"""

    def __init__(self, parent):
        super().__init__(parent, bg="#ffffff")
        self.canvas = tk.Canvas(self, bg="#ffffff", highlightthickness=0, bd=0)
        self.scroll = ttk.Scrollbar(self, command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg="#ffffff")
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda event: self.sync_view())
        self.canvas.bind("<Configure>", self._fit_width)
        self.canvas.bind("<Map>", lambda event: self.sync_view())
        self.canvas.bind("<Enter>", lambda event: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda event: self.canvas.unbind_all("<MouseWheel>"))
        self.groups = []
        self.vars = []
        self.all_var = tk.BooleanVar(value=False)
        self.on_change = None

    def destroy(self):
        try:
            self.canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        super().destroy()

    def _fit_width(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def show(self, groups, merge_keys, detail_keys, merge_heads, detail_heads):
        for child in self.inner.winfo_children():
            child.destroy()
        self.groups = []
        self.vars = []
        self.all_var = tk.BooleanVar(value=False)
        heads = list(merge_heads) + list(detail_heads)
        span_all = max(1, len(heads) + 1)
        tk.Checkbutton(self.inner, variable=self.all_var, command=self._toggle_all, bg="#f5f5f5", activebackground="#f5f5f5").grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        for column, text in enumerate(heads, 1):
            tk.Label(self.inner, text=text, bg="#f5f5f5", fg=TEXT, font=("Microsoft YaHei UI", 10, "bold"), anchor="w", padx=8, pady=8).grid(row=0, column=column, sticky="nsew", padx=1, pady=1)
        grid_row = 1
        for group_index, group in enumerate(groups):
            if group_index:
                gap = tk.Frame(self.inner, height=8, bg="#d5d5d5")
                gap.grid_propagate(False)
                gap.grid(row=grid_row, column=0, columnspan=span_all, sticky="ew")
                self.inner.grid_rowconfigure(grid_row, minsize=8)
                grid_row += 1
            bg = "#ffffff" if group_index % 2 == 0 else "#f3f3f3"
            span = max(1, len(group))
            var = tk.BooleanVar(value=False)
            self.vars.append(var)
            self.groups.append(group)
            tk.Checkbutton(self.inner, variable=var, command=self._sync_all, bg=bg, activebackground=bg).grid(row=grid_row, column=0, rowspan=span, sticky="nsew")
            for offset, key in enumerate(merge_keys):
                _cell(self.inner, group[0].get(key, ""), bg=bg).grid(row=grid_row, column=1 + offset, rowspan=span, sticky="nsew", padx=1, pady=1)
            base = 1 + len(merge_keys)
            for index, row in enumerate(group):
                for offset, key in enumerate(detail_keys):
                    _cell(self.inner, row.get(key, ""), status=(key == "status"), bg=bg).grid(row=grid_row + index, column=base + offset, sticky="nsew", padx=1, pady=1)
            grid_row += span
        if not groups:
            tk.Label(self.inner, text="没有记录", bg="#ffffff", fg=MUTED, font=("Microsoft YaHei UI", 10), anchor="w", padx=8, pady=12).grid(row=1, column=0, columnspan=span_all, sticky="w")
        self.inner.grid_columnconfigure(0, minsize=36)
        for column in range(1, len(heads) + 1):
            self.inner.grid_columnconfigure(column, weight=1, minsize=72)
        self.sync_view()

    def sync_view(self):
        """行已经放进表格后，把画布高度拉开。否则条数有了，行仍停在看不见的高度。"""
        self.inner.update_idletasks()
        height = self.inner.winfo_reqheight()
        width = self.canvas.winfo_width()
        if width > 1:
            self.canvas.itemconfigure(self.window_id, width=width, height=max(height, 1))
        elif height > 0:
            self.canvas.itemconfigure(self.window_id, height=height)
        bbox = self.canvas.bbox("all")
        if bbox:
            self.canvas.configure(scrollregion=bbox)

    def _toggle_all(self):
        value = bool(self.all_var.get())
        for var in self.vars:
            var.set(value)
        self._notify()

    def _sync_all(self):
        self.all_var.set(bool(self.vars) and all(var.get() for var in self.vars))
        self._notify()

    def _notify(self):
        if self.on_change is not None:
            self.on_change()

    def selected_count(self):
        return sum(1 for var in self.vars if var.get())

    def selected_rows(self):
        chosen = []
        for group, var in zip(self.groups, self.vars):
            if var.get():
                chosen.extend(group)
        return chosen


def _cell(parent, text, status=False, bg="#ffffff"):
    value = "" if text is None else str(text)
    color = TEXT
    if status and value in {"成功", "完成", "已提交"}:
        color = "#12854e"
    elif status and value in {"失败", "需核对"}:
        color = "#be3737"
    return tk.Label(parent, text=value, bg=bg, fg=color, font=("Microsoft YaHei UI", 10), anchor="w", justify="left", padx=8, pady=6, wraplength=280)


class HubWindow:
    """左侧账号管理，右侧上传记录。窗口保持打开，表格随上传刷新。"""

    def __init__(self, database_path, picture_log_path, aplus_log_path):
        self.database_path = Path(database_path)
        self.picture_log_path = Path(picture_log_path)
        self.aplus_log_path = Path(aplus_log_path)
        self.page = "accounts"
        self.picture_site = "全部"
        self.aplus_site = "全部"
        self.record_tab = "picture"
        self.record_span = "all"
        self.filter_from = ""
        self.filter_to = ""
        self.filter_until_now = True
        self.record_query = ""
        self.picture_site_count = 0
        self.picture_shown_count = 0
        self.aplus_site_count = 0
        self.aplus_shown_count = 0
        self.last_upload_at = _load_last_upload(self._last_upload_path())
        self._login_token = {}
        self.uploading = False
        self._progress_spinning = False
        self._progress_done = 0
        self._progress_total = 0
        self.launching = False
        self._login_gen = 0
        self.login_labels = {}
        self.root = tk.Tk()
        self.root.title("Aosom 图片与 A+")
        self.root.geometry("1180x720")
        self.root.minsize(980, 560)
        self.root.configure(bg=BG)
        self._build()
        self.show_accounts(check=False)
        self.start_all_countries()

    def run(self):
        self.root.mainloop()

    def _build(self):
        side = tk.Frame(self.root, bg=DARK, width=230)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="Aosom", font=("Microsoft YaHei UI", 16, "bold"), fg=RED, bg=DARK).pack(anchor="w", padx=16, pady=(22, 2))
        tk.Label(side, text="图片与 A+", font=("Microsoft YaHei UI", 12, "bold"), fg="#ffffff", bg=DARK).pack(anchor="w", padx=16)
        tk.Label(side, text="欧洲五国", fg="#b8b8b8", bg=DARK, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=16, pady=(2, 18))
        self.nav_buttons = {}
        for key, text in (("accounts", "账号管理"), ("records", "上传记录")):
            button = tk.Button(
                side,
                text=text,
                command=lambda name=key: self._switch(name),
                relief="flat",
                bd=0,
                font=("Microsoft YaHei UI", 10),
                anchor="w",
                padx=14,
            )
            button.pack(fill="x", padx=12, pady=4, ipady=6)
            self.nav_buttons[key] = button
        tk.Button(
            side,
            text="选择 Excel",
            command=self.choose_excel,
            relief="flat",
            bd=0,
            font=("Microsoft YaHei UI", 10),
            bg="#ffffff",
            fg=TEXT,
            anchor="w",
            padx=14,
        ).pack(fill="x", padx=12, pady=(18, 4), ipady=6)
        progress = tk.Frame(side, bg=DARK)
        progress.pack(fill="both", expand=True, padx=12, pady=(10, 16))
        tk.Label(progress, text="进度", bg=DARK, fg="#ffffff", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        self.progress_label = tk.Label(progress, text="尚未开始", bg=DARK, fg="#d0d0d0", font=("Microsoft YaHei UI", 9), justify="left", anchor="w", wraplength=200)
        self.progress_label.pack(anchor="w", fill="x", pady=(6, 0))
        self.progressbar = ttk.Progressbar(progress, mode="determinate", maximum=1, value=0)
        self.progressbar.pack(fill="x", pady=(8, 8))
        self.progress_text = tk.Text(progress, height=12, wrap="word", bg="#2a2a2a", fg="#f2f2f2", bd=0, font=("Microsoft YaHei UI", 9), relief="flat")
        self.progress_text.pack(fill="both", expand=True)
        self.progress_text.configure(state="disabled")
        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)
        self.title_label = tk.Label(self.main, bg=BG, fg=TEXT, font=("Microsoft YaHei UI", 16, "bold"))
        self.title_label.pack(anchor="w", padx=22, pady=(18, 0))
        self.subtitle = tk.Label(self.main, bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9), justify="left")
        self.subtitle.pack(anchor="w", padx=22, pady=(4, 10))
        self.body = tk.Frame(self.main, bg=BG)
        self.body.pack(fill="both", expand=True, padx=22, pady=(0, 8))
        self.footer = tk.Label(self.main, bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9), justify="left", anchor="w")
        self.footer.pack(fill="x", padx=22, pady=(0, 12))

    def _switch(self, page):
        if page == "accounts":
            self.show_accounts()
        else:
            self.show_records()

    def _mark_nav(self):
        for key, button in self.nav_buttons.items():
            active = key == self.page
            button.configure(bg=RED if active else "#2a2a2a", fg="#ffffff", activebackground=RED, activeforeground="#ffffff")

    def _clear_body(self):
        for child in self.body.winfo_children():
            child.destroy()

    def show_accounts(self, check=True):
        self.page = "accounts"
        self._mark_nav()
        self._clear_body()
        self.login_labels = {}
        self.title_label.configure(text="账号管理")
        self.subtitle.configure(text="欧洲五国各自使用独立 Chrome。已登录看账户切换条，未登录看欢迎页上的注册地址。")
        actions = tk.Frame(self.body, bg=BG)
        actions.pack(fill="x", pady=(0, 8))
        tk.Button(actions, text="五个国家全部启动", command=self.start_all_countries, font=("Microsoft YaHei UI", 10)).pack(side="left")
        tk.Button(actions, text="重新检查登录", command=self._check_login_async, font=("Microsoft YaHei UI", 10)).pack(side="left", padx=8)
        for code, name in EU_COUNTRIES:
            row = tk.Frame(self.body, bg="#f7f7f7")
            row.pack(fill="x", pady=4)
            tk.Label(row, text=f"{name}  {code}", width=16, anchor="w", bg="#f7f7f7", fg=TEXT, font=("Microsoft YaHei UI", 11, "bold")).pack(side="left", padx=12, pady=10)
            tk.Label(row, text=f"端口 {COUNTRY_PORTS[code]}", width=12, anchor="w", bg="#f7f7f7", fg=MUTED).pack(side="left")
            status = tk.Label(row, text="正在启动" if self.launching or not check else "检查中", width=10, anchor="w", bg="#f7f7f7", fg=TEXT, font=("Microsoft YaHei UI", 11))
            status.pack(side="left")
            self.login_labels[code] = status
            tk.Button(row, text="打开 Chrome", command=lambda c=code: self.open_country(c)).pack(side="right", padx=12, pady=8)
        self.footer.configure(text="未登录：还在卖家欢迎页。已登录：已经进入卖家后台。")
        if check and not self.launching:
            self._check_login_async()

    def start_all_countries(self):
        if self.launching:
            self.footer.configure(text="五个国家正在启动。")
            return
        self.launching = True
        self._login_gen += 1
        for label in self.login_labels.values():
            if label.winfo_exists():
                label.configure(text="正在启动")
        self.footer.configure(text="正在启动英国、德国、法国、意大利、西班牙。")

        def work():
            errors = []
            for code, name in EU_COUNTRIES:
                try:
                    launch_chrome(code)
                except Exception as exc:
                    errors.append(f"{name}：{exc}")
            self.root.after(0, lambda: self._countries_started(errors))

        threading.Thread(target=work, daemon=True).start()

    def _countries_started(self, errors):
        self.launching = False
        if errors:
            self.footer.configure(text="有国家没有启动。" + " ".join(errors))
        else:
            self.footer.configure(text="五个国家的 Chrome 已启动。正在查看是否登录。")
        if self.page == "accounts":
            self._check_login_async()

    def _check_login_async(self):
        if self.launching:
            return
        self._login_gen += 1
        generation = self._login_gen
        for code, _name in EU_COUNTRIES:
            self._login_token[code] = self._login_token.get(code, 0) + 1
        for label in self.login_labels.values():
            if label.winfo_exists():
                label.configure(text="检查中")

        def work():
            states = {}
            for code, _name in EU_COUNTRIES:
                states[code] = read_login_state(code)
            self.root.after(0, lambda: self._apply_login(generation, states))

        threading.Thread(target=work, daemon=True).start()

    def _apply_login(self, generation, states):
        if generation != self._login_gen or self.page != "accounts":
            return
        for code, text in states.items():
            label = self.login_labels.get(code)
            if label is not None and label.winfo_exists():
                label.configure(text=text)

    def open_country(self, code):
        label = self.login_labels.get(code)
        if label is not None and label.winfo_exists():
            label.configure(text="正在打开")
        name = EU_NAMES[code]

        def work():
            try:
                info = launch_chrome(code)
                error = None
            except Exception as exc:
                info = None
                error = str(exc)
            self.root.after(0, lambda: self._country_opened(code, name, info, error))

        threading.Thread(target=work, daemon=True).start()

    def _country_opened(self, code, name, info, error):
        if error:
            messagebox.showerror("无法打开", error, parent=self.root)
            self._apply_one(code, self._login_token.get(code), "未确认")
            return
        if info.get("started"):
            self.footer.configure(text=f"已打开{name}独立 Chrome。其他国家没有重新打开。")
        else:
            self.footer.configure(text=f"{name}的 Chrome 已在端口 {info.get('port')} 上，未再启动第二个窗口。")
        self._check_one_async(code)

    def _check_one_async(self, code):
        self._login_token[code] = self._login_token.get(code, 0) + 1
        token = self._login_token[code]
        label = self.login_labels.get(code)
        if label is not None and label.winfo_exists():
            label.configure(text="检查中")

        def work():
            state = read_login_state(code)
            self.root.after(0, lambda: self._apply_one(code, token, state))

        threading.Thread(target=work, daemon=True).start()

    def _apply_one(self, code, token, state):
        if self._login_token.get(code) != token or self.page != "accounts":
            return
        label = self.login_labels.get(code)
        if label is not None and label.winfo_exists():
            label.configure(text=state)

    def show_records(self):
        self.page = "records"
        self._mark_nav()
        self._clear_body()
        self.title_label.configure(text="上传记录")
        self.subtitle.configure(text="勾选要下载的行。同一次上传的同一个 sku 合成一格，图片和结果仍各占一行。")
        tab_bar = tk.Frame(self.body, bg="#ffffff")
        tab_bar.pack(fill="x")
        self.tab_buttons = {}
        self.tab_lines = {}
        for key, text in (("picture", "图片上传"), ("aplus", "A+描述")):
            wrap = tk.Frame(tab_bar, bg="#ffffff")
            wrap.pack(side="left", padx=(0, 28))
            button = tk.Button(
                wrap,
                text=text,
                command=lambda name=key: self._select_tab(name),
                relief="flat",
                bd=0,
                bg="#ffffff",
                activebackground="#ffffff",
                cursor="hand2",
                font=("Microsoft YaHei UI", 15),
            )
            button.pack(anchor="w")
            line = tk.Frame(wrap, height=3, bg="#ffffff")
            line.pack(fill="x", pady=(6, 0))
            self.tab_buttons[key] = button
            self.tab_lines[key] = line
        tk.Frame(self.body, height=1, bg=LINE).pack(fill="x")
        country_bar = tk.Frame(self.body, bg="#ffffff")
        country_bar.pack(fill="x", pady=(12, 0))
        tk.Label(country_bar, text="站点", bg="#ffffff", fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(side="left", padx=(0, 8))
        self.country_buttons = {}
        for code, name in (("全部", "全部"), *EU_COUNTRIES):
            button = tk.Button(country_bar, text=name, command=lambda value=code: self._set_site(value), padx=12, pady=4, cursor="hand2")
            button.pack(side="left", padx=3)
            self.country_buttons[code] = button
        self._build_time_filter(self.body)
        tools = tk.Frame(self.body, bg="#ffffff")
        tools.pack(fill="x", pady=(0, 6))
        tk.Button(tools, text="刷新", command=self._fill_tables, font=("Microsoft YaHei UI", 10), bg="#ffffff").pack(side="right")
        tk.Button(tools, text="下载勾选", command=self._download_current, font=("Microsoft YaHei UI", 10), bg="#ffffff").pack(side="right", padx=(0, 6))
        self.count_label = tk.Label(self.body, text="", bg="#ffffff", fg=TEXT, font=("Microsoft YaHei UI", 10), anchor="w")
        self.count_label.pack(fill="x", pady=(0, 6))
        self.picture_frame = tk.Frame(self.body, bg="#ffffff")
        self.aplus_frame = tk.Frame(self.body, bg="#ffffff")
        self.picture_table = CheckTable(self.picture_frame)
        self.picture_table.pack(fill="both", expand=True)
        self.picture_table.on_change = self._update_count
        self.aplus_table = CheckTable(self.aplus_frame)
        self.aplus_table.pack(fill="both", expand=True)
        self.aplus_table.on_change = self._update_count
        self._select_tab(self.record_tab)
        self._fill_tables()

    def _select_tab(self, name):
        self.record_tab = name
        for key, button in self.tab_buttons.items():
            active = key == name
            button.configure(
                fg="#111111" if active else "#8a8a8a",
                font=("Microsoft YaHei UI", 15, "bold" if active else "normal"),
            )
            self.tab_lines[key].configure(bg="#111111" if active else "#ffffff")
        self._paint_countries()
        if not hasattr(self, "picture_frame"):
            return
        self.picture_frame.pack_forget()
        self.aplus_frame.pack_forget()
        frame = self.picture_frame if name == "picture" else self.aplus_frame
        frame.pack(fill="both", expand=True)
        table = self.picture_table if name == "picture" else self.aplus_table
        table.sync_view()
        self._update_count()

    def _paint_countries(self):
        site = self.picture_site if self.record_tab == "picture" else self.aplus_site
        for code, button in self.country_buttons.items():
            _mark_button(button, code == site)

    def _build_time_filter(self, parent):
        bar = tk.Frame(parent, bg="#f4f4f4", highlightthickness=1, highlightbackground=LINE)
        bar.pack(fill="x", pady=(10, 8))
        self.filter_bar = bar
        fields = tk.Frame(bar, bg="#f4f4f4")
        fields.pack(side="left", fill="x", expand=True, padx=8, pady=8)
        time_row = tk.Frame(fields, bg="#f4f4f4")
        time_row.pack(fill="x", pady=(0, 6))
        sku_row = tk.Frame(fields, bg="#f4f4f4")
        sku_row.pack(fill="x")
        tk.Label(time_row, text="时间", width=6, anchor="w", bg="#f4f4f4", fg=TEXT, font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        self.filter_mode = tk.StringVar(value=self.record_span)
        self.filter_buttons = {}
        for value, label in (("all", "全部"), ("current", "本次上传"), ("range", "时间段")):
            button = tk.Button(
                time_row,
                text=label,
                command=lambda mode=value: self._choose_span(mode),
                padx=12,
                pady=4,
                cursor="hand2",
            )
            button.pack(side="left", padx=3)
            self.filter_buttons[value] = button
        tk.Label(time_row, text="从", bg="#f4f4f4", fg=TEXT, font=("Microsoft YaHei UI", 11)).pack(side="left", padx=(12, 4))
        self.from_entry = tk.Entry(time_row, width=12, font=("Microsoft YaHei UI", 11), relief="solid", bd=1)
        self.from_entry.insert(0, self.filter_from)
        self.from_entry.pack(side="left", ipady=2)
        tk.Button(time_row, text="选择日期", command=lambda: self._pick_date("from"), font=("Microsoft YaHei UI", 10), bg="#ffffff", padx=8, pady=3).pack(side="left", padx=(4, 10))
        tk.Label(time_row, text="到", bg="#f4f4f4", fg=TEXT, font=("Microsoft YaHei UI", 11)).pack(side="left", padx=(4, 4))
        self.to_choice = tk.StringVar(value="now" if self.filter_until_now else "date")
        self.until_button = tk.Button(time_row, text="至今", command=self._choose_until_now, padx=12, pady=4, cursor="hand2")
        self.until_button.pack(side="left", padx=3)
        self.to_entry = tk.Entry(time_row, width=12, font=("Microsoft YaHei UI", 11), relief="solid", bd=1)
        self.to_entry.insert(0, self.filter_to)
        self.to_entry.pack(side="left", ipady=2)
        tk.Button(time_row, text="选择日期", command=lambda: self._pick_date("to"), font=("Microsoft YaHei UI", 10), bg="#ffffff", padx=8, pady=3).pack(side="left", padx=(4, 0))
        tk.Label(sku_row, text="sku", width=6, anchor="w", bg="#f4f4f4", fg=TEXT, font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        self.query_entry = tk.Entry(sku_row, width=28, font=("Microsoft YaHei UI", 11), relief="solid", bd=1)
        self.query_entry.insert(0, self.record_query)
        self.query_entry.pack(side="left", ipady=2)
        tk.Label(sku_row, text="可填 sku 或 asin", bg="#f4f4f4", fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=8)
        apply_button = tk.Button(bar, text="筛选", command=self._apply_search, padx=22, pady=16, cursor="hand2")
        apply_button.pack(side="right", padx=12, pady=8)
        _mark_button(apply_button, True)
        self._apply_filter_widgets()

    def _choose_span(self, mode):
        self.filter_mode.set(mode)
        self._apply_filter_widgets()

    def _choose_until_now(self):
        self.filter_mode.set("range")
        self.to_choice.set("now")
        self._apply_filter_widgets()

    def _apply_search(self):
        self.record_span = self.filter_mode.get()
        self.filter_from = self.from_entry.get().strip()
        self.filter_to = self.to_entry.get().strip()
        self.filter_until_now = self.to_choice.get() == "now"
        self.record_query = self.query_entry.get().strip()
        self._fill_tables()

    def _update_count(self):
        if not hasattr(self, "count_label") or not self.count_label.winfo_exists():
            return
        if self.record_tab == "picture":
            site_count, shown_count, table = self.picture_site_count, self.picture_shown_count, self.picture_table
        else:
            site_count, shown_count, table = self.aplus_site_count, self.aplus_shown_count, self.aplus_table
        selected = table.selected_count() if table.winfo_exists() else 0
        self.count_label.configure(text=f"当前站点 {site_count} 条 · 显示 {shown_count} 条 · 已选 {selected} 条")

    def _apply_filter_widgets(self):
        ranged = self.filter_mode.get() == "range"
        self.from_entry.configure(state="normal" if ranged else "disabled")
        self.to_entry.configure(state="normal" if ranged and self.to_choice.get() == "date" else "disabled")
        for value, button in self.filter_buttons.items():
            _mark_button(button, value == self.filter_mode.get())
        _mark_button(self.until_button, ranged and self.to_choice.get() == "now")

    def _pick_date(self, which):
        current = self.filter_from if which == "from" else self.filter_to
        moment = _parse_clock(current, end_of_day=False) or datetime.now()
        dialog = tk.Toplevel(self.root)
        dialog.title("选择开始日期" if which == "from" else "选择结束日期")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.configure(bg="#ffffff")
        box = tk.Frame(dialog, bg="#ffffff", padx=16, pady=14)
        box.pack()
        year = tk.Spinbox(box, from_=2020, to=2036, width=6, font=("Microsoft YaHei UI", 12))
        month = tk.Spinbox(box, from_=1, to=12, width=4, font=("Microsoft YaHei UI", 12))
        day = tk.Spinbox(box, from_=1, to=31, width=4, font=("Microsoft YaHei UI", 12))
        for widget, value in ((year, moment.year), (month, moment.month), (day, moment.day)):
            widget.delete(0, "end")
            widget.insert(0, str(value))
        year.pack(side="left")
        tk.Label(box, text="年", bg="#ffffff").pack(side="left", padx=(2, 6))
        month.pack(side="left")
        tk.Label(box, text="月", bg="#ffffff").pack(side="left", padx=(2, 6))
        day.pack(side="left")
        tk.Label(box, text="日", bg="#ffffff").pack(side="left", padx=(2, 0))

        def confirm():
            try:
                text = f"{int(year.get()):04d}-{int(month.get()):02d}-{int(day.get()):02d}"
                datetime.strptime(text, "%Y-%m-%d")
            except ValueError:
                return
            self.filter_mode.set("range")
            if which == "from":
                self._set_entry(self.from_entry, text)
            else:
                self.to_choice.set("date")
                self._set_entry(self.to_entry, text)
            dialog.destroy()
            self._apply_filter_widgets()

        tk.Button(dialog, text="确定", command=confirm, font=("Microsoft YaHei UI", 10)).pack(pady=(0, 12))
        dialog.update_idletasks()
        width = dialog.winfo_reqwidth()
        height = dialog.winfo_reqheight()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.transient(self.root)
        dialog.lift()
        dialog.focus_force()
        dialog.grab_set()

    def _set_entry(self, entry, text):
        entry.configure(state="normal")
        entry.delete(0, "end")
        entry.insert(0, text)

    def _set_site(self, code):
        if self.record_tab == "picture":
            self.picture_site = code
        else:
            self.aplus_site = code
        self._paint_countries()
        self._fill_tables()

    def _download_current(self):
        if self.record_tab == "picture":
            self._download_checked(
                "图片上传",
                PICTURE_MERGE_HEADS + PICTURE_DETAIL_HEADS,
                PICTURE_MERGE + PICTURE_DETAIL,
                self.picture_table,
            )
        else:
            self._download_checked("A+描述", APLUS_DETAIL_HEADS, APLUS_DETAIL, self.aplus_table)

    def _last_upload_path(self):
        return self.database_path.parent / "last_upload_at.txt"

    def _fill_tables(self):
        if not hasattr(self, "picture_table") or not self.picture_table.winfo_exists():
            return
        ok, message = _time_filter_error(self.record_span, self.filter_from, self.filter_to, self.filter_until_now)
        if not ok:
            self.footer.configure(text=message)
            return
        picture_groups = group_picture_rows([
            row for row in load_picture_records(self.picture_log_path) if _match_site(row, self.picture_site)
        ])
        picture_shown = _sort_groups_newest([
            group for group in picture_groups if _group_visible(group, self.record_span, self.filter_from, self.filter_to, self.filter_until_now, self.last_upload_at, self.record_query)
        ])
        aplus_site = [row for row in load_aplus_records(self.aplus_log_path) if _match_site(row, self.aplus_site)]
        aplus_shown = _sort_newest([
            row for row in aplus_site if _row_visible(row, self.record_span, self.filter_from, self.filter_to, self.filter_until_now, self.last_upload_at, self.record_query)
        ])
        self.picture_site_count = len(picture_groups)
        self.picture_shown_count = len(picture_shown)
        self.aplus_site_count = len(aplus_site)
        self.aplus_shown_count = len(aplus_shown)
        self.picture_table.show(picture_shown, PICTURE_MERGE, PICTURE_DETAIL, PICTURE_MERGE_HEADS, PICTURE_DETAIL_HEADS)
        self.aplus_table.show([[row] for row in aplus_shown], (), APLUS_DETAIL, (), APLUS_DETAIL_HEADS)
        self._update_count()
        note = "还没有本次上传。" if self.record_span == "current" and self.last_upload_at is None else "下载只包含勾选的那一次上传。"
        self.footer.configure(text=note)

    def _download_checked(self, title, headings, keys, table):
        if table is None or not table.winfo_exists():
            return
        rows = table.selected_rows()
        if not rows:
            messagebox.showinfo("没有勾选", "请先勾选要下载的行。", parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="下载勾选",
            defaultextension=".csv",
            initialfile=f"{title}.csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(headings)
            for row in rows:
                writer.writerow([row.get(key, "") for key in keys])
        self.footer.configure(text=f"已下载 {len(rows)} 行：" + path)

    def _begin_progress(self, text):
        if self._progress_spinning:
            self.progressbar.stop()
            self._progress_spinning = False
        self.progressbar.configure(mode="determinate", maximum=1, value=0)
        self.progress_text.configure(state="normal")
        self.progress_text.delete("1.0", "end")
        self.progress_text.configure(state="disabled")
        self._set_progress(text, 0, 0)

    def _set_progress(self, text, done, total):
        done = int(done or 0)
        total = int(total or 0)
        self._progress_done = done
        self._progress_total = total
        self.progress_label.configure(text=text)
        if total <= 0:
            if not self._progress_spinning:
                self.progressbar.configure(mode="indeterminate")
                self.progressbar.start(12)
                self._progress_spinning = True
        else:
            if self._progress_spinning:
                self.progressbar.stop()
                self._progress_spinning = False
            self.progressbar.configure(mode="determinate", maximum=total, value=min(done, total))
        self.progress_text.configure(state="normal")
        self.progress_text.insert("end", text + "\n")
        self.progress_text.see("end")
        self.progress_text.configure(state="disabled")

    def choose_excel(self):
        if self.uploading:
            messagebox.showinfo("正在上传", "请等当前 Excel 跑完。", parent=self.root)
            return
        path = filedialog.askopenfilename(parent=self.root, title="选择 Excel", filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        self.uploading = True
        self.last_upload_at = datetime.now().replace(microsecond=0)
        _save_last_upload(self._last_upload_path(), self.last_upload_at)
        self.footer.configure(text="正在读取并上传：" + path)
        self._begin_progress("正在读取 Excel")

        def report_progress(text, done, total):
            self.root.after(0, lambda text=text, done=done, total=total: self._set_progress(text, done, total))

        def work():
            try:
                report = run(path, self.database_path, self.picture_log_path, on_progress=report_progress)
                error = None
            except Exception as exc:
                report = None
                error = explain_read_failure(exc, path)
            self.root.after(0, lambda: self._upload_finished(report, error))

        threading.Thread(target=work, daemon=True).start()

    def _upload_finished(self, report, error):
        self.uploading = False
        if error:
            self.footer.configure(text=error)
            self._set_progress("未完成：" + error.splitlines()[0], self._progress_done, self._progress_total or 1)
            messagebox.showerror("上传未完成", error, parent=self.root)
            return
        append_aplus_log(self.aplus_log_path, report)
        count = len((report or {}).get("results") or [])
        self.footer.configure(text=f"本次处理 {count} 行。上传记录已刷新。")
        self._set_progress(f"本次处理 {count} 行", self._progress_total or 1, self._progress_total or 1)
        if self.page == "records":
            self._fill_tables()
        else:
            self.show_records()


def load_picture_records(path):
    """从 picture_log.md 读出每一张图片的结果。一块记录对应一张图。"""
    path = Path(path)
    if not path.is_file():
        return []
    rows = []
    for chunk in path.read_text(encoding="utf-8").split("【记录时间】：")[1:]:
        lines = chunk.splitlines()
        row = {
            "time": lines[0].strip() if lines else "",
            "country": "",
            "market": "",
            "keyword": "",
            "sku": "",
            "asin": "",
            "file": "",
            "page_slot": "",
            "status": "",
            "reason": "",
        }
        for line in lines[1:]:
            if line.startswith("sku："):
                row["sku"] = line.split("：", 1)[1].strip()
            elif line.startswith("asin："):
                row["asin"] = line.split("：", 1)[1].strip()
            elif line.startswith("关键词："):
                row["keyword"] = line.split("：", 1)[1].strip()
            elif line.startswith("country："):
                row["country"] = line.split("：", 1)[1].strip()
            elif line.startswith("market："):
                row["market"] = line.split("：", 1)[1].strip()
            elif line.startswith("图片："):
                file_name, page_slot = _parse_picture(line.split("：", 1)[1].strip())
                row["file"] = file_name
                row["page_slot"] = page_slot
            elif line.startswith("结果："):
                status, reason = _parse_result(line.split("：", 1)[1].strip())
                row["status"] = status
                row["reason"] = reason
        if not row["sku"]:
            row["sku"] = row["keyword"]
        rows.append(row)
    return rows


def _newest_key(row):
    return _parse_clock(row.get("time"), end_of_day=False) or datetime.min


def _sort_newest(rows):
    """日期新的排在前面。无法识别的时间放在最后。"""
    return sorted(rows, key=_newest_key, reverse=True)


def _sort_groups_newest(groups):
    """一次上传用其中最晚的时间排序。组内图片仍按原来的先后。"""
    return sorted(groups, key=lambda group: _newest_key(group[-1] if group else {}), reverse=True)


def group_picture_rows(rows):
    """同一站点、同一个 sku，且相邻两条不超过 3 分钟，算同一次上传。"""
    groups = []
    for row in rows:
        if groups and _same_picture_upload(groups[-1][-1], row):
            groups[-1].append(row)
        else:
            groups.append([row])
    return groups


def _same_picture_upload(previous, row):
    if (previous.get("sku") or "") != (row.get("sku") or ""):
        return False
    if (previous.get("asin") or "") != (row.get("asin") or ""):
        return False
    if _site_key(previous) != _site_key(row):
        return False
    start = _parse_clock(previous.get("time"), end_of_day=False)
    end = _parse_clock(row.get("time"), end_of_day=False)
    if start is None or end is None:
        return False
    return 0 <= (end - start).total_seconds() <= 3 * 60


def _site_key(row):
    return _as_code(row.get("market")) or _as_code(row.get("country")) or str(row.get("market") or row.get("country") or "")


def load_aplus_records(path):
    """从 aplus_log.md 读出每一行描述的提交结果。"""
    path = Path(path)
    if not path.is_file():
        return []
    rows = []
    for chunk in path.read_text(encoding="utf-8").split("【记录时间】：")[1:]:
        lines = chunk.splitlines()
        row = {"time": lines[0].strip() if lines else "", "sku": "", "asin": "", "country": "", "market": "", "status": "", "reason": ""}
        for line in lines[1:]:
            if "：" not in line:
                continue
            key, value = line.split("：", 1)
            field = {"sku": "sku", "asin": "asin", "country": "country", "market": "market", "结果": "status", "原因": "reason"}.get(key.strip())
            if field:
                row[field] = value.strip()
        rows.append(row)
    return rows


def append_aplus_log(path, report):
    """把本次结果里的描述提交写成可再读的记录。不写「不打开」。"""
    queued = (report or {}).get("queued") or []
    results = (report or {}).get("results") or []
    blocks = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for row, item in zip(queued, results):
        if not row.get("A+introduction"):
            continue
        part = _description_part(item.get("reason"))
        if part == "不打开":
            continue
        if part == "已提交":
            status, reason = "成功", ""
        elif part:
            status, reason = "失败", part
        else:
            status, reason = "失败", str(item.get("reason") or item.get("status") or "")
        blocks.append("\n".join([
            f"【记录时间】：{now}",
            f"sku：{row.get('sku', '')}",
            f"asin：{row.get('asin', '')}",
            f"country：{row.get('country', '')}",
            f"market：{row.get('market', '')}",
            f"结果：{status}",
            f"原因：{reason}",
            "",
        ]))
    if not blocks:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(blocks) + "\n")


def _description_part(reason):
    for part in str(reason or "").split("；"):
        text = part.strip()
        if text.startswith("描述："):
            return text.split("：", 1)[1].strip()
    return ""


def _parse_picture(text):
    file_name, _, rest = text.partition("→")
    page_slot = ""
    marker = "页面位置"
    if marker in rest:
        page_slot = rest.split(marker, 1)[1].strip()
    return file_name.strip(), page_slot


def _parse_result(text):
    status, _, reason = text.partition(" ")
    return status.strip(), reason.strip()


def _load_last_upload(path):
    path = Path(path)
    if not path.is_file():
        return None
    return _parse_clock(path.read_text(encoding="utf-8").strip(), end_of_day=False)


def _save_last_upload(path, moment):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(moment.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")


def _time_filter_error(span, start_text, end_text, until_now):
    if span != "range":
        return True, ""
    if start_text and _parse_clock(start_text, end_of_day=False) is None:
        return False, "开始时间无法识别，请用 2026-10-09 或 2026-10-09 08:30"
    if not until_now and end_text and _parse_clock(end_text, end_of_day=True) is None:
        return False, "结束时间无法识别，请用 2026-10-09 或 2026-10-09 18:00"
    return True, ""


def _match_query(row, query):
    text = str(query or "").strip().lower()
    if not text:
        return True
    blob = " ".join(str(row.get(key) or "") for key in ("sku", "asin", "keyword")).lower()
    return text in blob


def _row_visible(row, span, start_text, end_text, until_now, last_upload, query):
    return _match_time(row, span, start_text, end_text, until_now, last_upload) and _match_query(row, query)


def _group_visible(group, span, start_text, end_text, until_now, last_upload, query):
    head = group[0] if group else {}
    return _row_visible(head, span, start_text, end_text, until_now, last_upload, query)


def _match_time(row, span, start_text, end_text, until_now, last_upload):
    if span == "all":
        return True
    moment = _parse_clock(row.get("time"), end_of_day=False)
    if moment is None:
        return False
    if span == "current":
        return last_upload is not None and moment >= last_upload
    start = _parse_clock(start_text, end_of_day=False) if start_text else None
    if start is not None and moment < start:
        return False
    if not until_now and end_text:
        end = _parse_clock(end_text, end_of_day=True)
        if end is not None and moment > end:
            return False
    return True


def _parse_clock(text, end_of_day):
    raw = str(text or "").strip()
    if not raw:
        return None
    patterns = (
        ("%Y-%m-%d %H:%M:%S", False),
        ("%Y-%m-%d %H:%M", False),
        ("%Y/%m/%d %H:%M:%S", False),
        ("%Y/%m/%d %H:%M", False),
        ("%Y-%m-%d", True),
        ("%Y/%m/%d", True),
    )
    for pattern, date_only in patterns:
        try:
            moment = datetime.strptime(raw, pattern)
        except ValueError:
            continue
        if date_only and end_of_day:
            return moment.replace(hour=23, minute=59, second=59)
        return moment
    return None


def _match_site(row, selected):
    if selected == "全部":
        return True
    market = _as_code(row.get("market"))
    country = _as_code(row.get("country"))
    if market:
        return market == selected
    return country == selected


def _as_code(value):
    text = str(value or "").strip()
    if not text:
        return ""
    upper = text.upper()
    if upper in EU_NAMES or upper in COUNTRY_PORTS:
        return upper
    if text in NAME_TO_CODE:
        return NAME_TO_CODE[text]
    try:
        return country_code(text)
    except ValueError:
        return ""


def read_login_state(code):
    """看该国当前卖家页。欢迎页上的注册地址表示未登录，账户切换条表示已登录。"""
    if not _port_open(code):
        return "尚未打开"
    domain = SELLER_CENTRAL_DOMAINS[country_code(code)]
    try:
        target = _choose_country_target(list_targets(port_for(code)), domain)
    except Exception:
        return "未确认"
    session = PageSession(target, timeout=5)
    try:
        session.confirm_control()
        deadline = time.monotonic() + 4
        latest = None
        while True:
            latest = session.evaluate(_LOGIN_STATE_JS)
            if isinstance(latest, dict) and (latest.get("welcome") or latest.get("loggedIn")):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.4)
    except Exception:
        return "未确认"
    finally:
        session.close()
    if isinstance(latest, dict) and latest.get("loggedIn"):
        return "已登录"
    if isinstance(latest, dict) and latest.get("welcome"):
        return "未登录"
    return "未确认"


def _port_open(code):
    from app.browser import _debug_port_open

    try:
        return _debug_port_open(port_for(code))
    except Exception:
        return False
