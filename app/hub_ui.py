"""启动窗口：欧洲五国账号，以及图片和 A+ 的上传记录。"""

from datetime import datetime
from pathlib import Path
import calendar
import csv
import sqlite3
import threading
import time
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

import customtkinter as ctk
from PIL import Image, ImageTk

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")
ctk.set_widget_scaling(1.0)
ctk.set_window_scaling(1.0)

from app.browser import COUNTRY_PORTS, SELLER_CENTRAL_DOMAINS, _choose_country_target, country_code, launch_chrome, port_for
from app.cdp import PageSession, list_targets
from app.file_pick import explain_read_failure
from app.pipeline import run
from app.progress_feed import set_listener

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

# 纯黑侧栏、浅灰工作台、白卡片。中文用微软雅黑，编号用 Consolas。
FONT = "Microsoft YaHei UI"
MONO = "Consolas"
SIDE = "#000000"
SIDE_HOVER = "#1D1D1F"
SIDE_TEXT = "#A3A3A3"
SIDE_RED = "#E10600"
SIDE_W = 220
NAV_ROW_H = 48
SIDE_ACTIVE = "#1D1D1F"
LOG_H = 250
FILTER_H = 128
NAVY = SIDE
NAVY_2 = SIDE_HOVER
NAV_TEXT = SIDE_TEXT
NAV_MUTED = SIDE_TEXT
PAGE = "#F8F9FA"
SHEET = "#FFFFFF"
HEAD = "#FFFFFF"
ROW_HOVER = "#F8F9FA"
INK = "#111111"
MUTED = "#6B7280"
LINE = "#E5E7EB"
EDGE = "#E5E7EB"
DIVIDER = "#F3F4F6"
BTN = "#111111"
BTN_HOVER = "#242424"
PILL_TEXT = "#6B7280"
PILL_HOVER = "#F8F9FA"
ACCENT = "#111111"
ACCENT_HOVER = "#242424"
PAGE_ON = "#F8F9FA"
DISABLED = "#C0C4CC"
ON_PRIMARY = "#FFFFFF"
FIELD = "#F3F4F6"
TAB_TRACK = "#F3F4F6"
TAB_TEXT = "#4B5563"
TAB_ON = "#1F2937"
TAB_ON_TEXT = "#FFFFFF"
BTN_FILL = "#F3F4F6"
BTN_FILL_HOVER = "#E5E7EB"
CAL_BG = "#F9FAFB"
GHOST_LINE = "#E5E7EB"
GHOST_LINE_HOT = "#D1D5DB"
GHOST_TEXT = "#6B7280"
GHOST_TEXT_HOT = "#111827"
GHOST_HOVER = "#F9FAFB"
FRAME = PAGE
BG = SHEET
CANVAS = PAGE
APPLE_LINE = LINE
APPLE_SECONDARY = MUTED
APPLE_BLUE = ACCENT
APPLE_TRACK = FIELD
CHIP = FIELD
TEXT = INK
COLUMN_WEIGHT = {
    "time": 1.7,
    "task_id": 1.5,
    "country": 0.7,
    "market": 0.6,
    "sku": 1.7,
    "asin": 1.2,
    "kind": 1.15,
    "file": 1.0,
    "page_slot": 0.8,
    "status": 0.7,
    "reason": 1.6,
    "error_code": 1.3,
    "error_detail": 2.2,
}
FILTER_CATEGORIES = (("site", "站点"), ("time", "时间"), ("task", "task_id"))

RECORD_MERGE = ("time", "task_id", "country", "market", "sku", "asin")
RECORD_MERGE_HEADS = ("时间", "task_id", "国家", "站点", "sku", "asin")
RECORD_DETAIL = ("kind", "file", "page_slot", "status", "reason", "error_code", "error_detail")
RECORD_DETAIL_HEADS = ("类型", "内容", "页面位置", "结果", "原因", "错误代码", "错误详情")
PAGE_SIZES = (10, 20, 50)


def _load_logo(max_width=168):
    """读透明或近黑底的 Aosom 字标，缩到侧栏里放得下。"""
    from app.config_loader import bundle_dir

    path = bundle_dir() / "assets" / "aosom_logo.png"
    image = Image.open(path).convert("RGBA")
    cleaned = []
    for red, green, blue, alpha in image.get_flattened_data():
        if red < 28 and green < 28 and blue < 28:
            cleaned.append((red, green, blue, 0))
        else:
            cleaned.append((red, green, blue, alpha))
    image.putdata(cleaned)
    if image.width > max_width:
        height = max(1, round(image.height * max_width / image.width))
        image = image.resize((max_width, height), Image.Resampling.LANCZOS)
    return ImageTk.PhotoImage(image)


def _install_ttk_styles(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        return
    style.configure(
        "Side.Horizontal.TProgressbar",
        troughcolor="#333333",
        background=ACCENT,
        bordercolor="#333333",
        lightcolor=ACCENT,
        darkcolor=ACCENT,
    )
    style.configure(
        "Sheet.Vertical.TScrollbar",
        background="#D2D2D7",
        troughcolor="#F5F5F7",
        bordercolor="#F5F5F7",
        arrowcolor="#7A7A7A",
        lightcolor="#D2D2D7",
        darkcolor="#D2D2D7",
        borderwidth=0,
        relief="flat",
    )
    style.map(
        "Sheet.Vertical.TScrollbar",
        background=[("active", "#C7C7CC"), ("pressed", "#A1A1A6")],
    )


def _silence_focus(widget):
    try:
        widget.tk.call(str(widget), "configure", "-takefocus", 0)
    except tk.TclError:
        pass


def _visible_pages(current, total):
    total = max(int(total or 1), 1)
    current = min(max(int(current or 1), 1), total)
    if total <= 5:
        return list(range(1, total + 1))
    start = max(1, current - 2)
    end = min(total, start + 4)
    start = max(1, end - 4)
    return list(range(start, end + 1))

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


def _paint_round(canvas, x1, y1, x2, y2, radius, color, tags=""):
    """圆角块。直边停在半径处，四段弧最后盖上，角和边在同一条线上。"""
    x1, y1, x2, y2 = int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))
    if x2 <= x1 or y2 <= y1:
        return
    radius = max(0, min(int(radius), (x2 - x1) // 2, (y2 - y1) // 2))
    opts = {"fill": color, "outline": "", "width": 0}
    if tags:
        opts["tags"] = tags
    if radius <= 0:
        canvas.create_rectangle(x1, y1, x2, y2, **opts)
        return
    canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, **opts)
    canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, **opts)
    canvas.create_arc(x1, y1, x1 + 2 * radius, y1 + 2 * radius, start=90, extent=90, style="pieslice", **opts)
    canvas.create_arc(x2 - 2 * radius, y1, x2, y1 + 2 * radius, start=0, extent=90, style="pieslice", **opts)
    canvas.create_arc(x1, y2 - 2 * radius, x1 + 2 * radius, y2, start=180, extent=90, style="pieslice", **opts)
    canvas.create_arc(x2 - 2 * radius, y2 - 2 * radius, x2, y2, start=270, extent=90, style="pieslice", **opts)


def _wrap_log(text, face, width):
    value = "" if text is None else str(text)
    lines = []
    buf = ""
    for char in value:
        if char == "\n":
            lines.append(buf)
            buf = ""
            continue
        if buf and face.measure(buf + char) > width:
            lines.append(buf)
            buf = char
        else:
            buf += char
    lines.append(buf)
    return lines or [""]


class SoftButton(tk.Canvas):
    """浅灰底、黑字、没有描边。圆角只填一块颜色，不再叠 1 像素边。"""

    def __init__(self, parent, text, command, kind="primary"):
        try:
            host = parent.cget("bg")
        except tk.TclError:
            host = SHEET
        self._font_size = 13 if kind == "quiet" else 14
        self._radius = 6 if kind == "quiet" else 8
        if kind == "quiet":
            height = 32
            extra = 24
        else:
            height = 40
            extra = 40
        font = tkfont.Font(family=FONT, size=self._font_size, weight="bold")
        super().__init__(
            parent,
            width=font.measure(text) + extra,
            height=height,
            bg=host,
            highlightthickness=0,
            bd=0,
            takefocus=0,
            cursor="hand2",
        )
        self._text = text
        self._command = command
        self._kind = kind
        self._hot = False
        self._draw()
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._click)

    def _draw(self):
        self.delete("all")
        width = int(self.cget("width"))
        height = int(self.cget("height"))
        fill = BTN_FILL_HOVER if self._hot else BTN_FILL
        _paint_round(self, 1, 1, max(width - 1, 2), max(height - 1, 2), self._radius, fill)
        self.create_text(width / 2, height / 2, text=self._text, fill=INK, font=(FONT, self._font_size, "bold"))

    def _enter(self, _event):
        self._hot = True
        self._draw()

    def _leave(self, _event):
        self._hot = False
        self._draw()

    def _click(self, _event):
        if self._command is not None:
            self._command()


class LineEntry(tk.Frame):
    """无边框、浅灰底、32 像素高，嵌在白底上。"""

    def __init__(self, parent, width_chars=16):
        super().__init__(parent, bg=FIELD, height=32, highlightthickness=0, bd=0)
        self.pack_propagate(False)
        self.configure(width=max(140, width_chars * 9))
        self.entry = tk.Entry(
            self,
            relief="flat",
            bd=0,
            highlightthickness=0,
            bg=FIELD,
            fg=INK,
            insertbackground=INK,
            font=(FONT, 12),
        )
        self.entry.pack(fill="both", expand=True, padx=8)

    def get(self):
        return self.entry.get()

    def delete(self, start, end):
        self.entry.delete(start, end)

    def insert(self, index, text):
        self.entry.insert(index, text)

    def bind(self, sequence, func, add=None):
        return self.entry.bind(sequence, func, add)


class PageSquare(tk.Canvas):
    """28 像素页码方格。当前页浅蓝底蓝字，其余白底灰边。"""

    def __init__(self, parent, text, command=None):
        super().__init__(parent, width=28, height=28, bg=SHEET, highlightthickness=0, bd=0, cursor="hand2")
        self._text = text
        self._command = command
        self._enabled = True
        self.set_mode("idle")
        self.bind("<Button-1>", self._click)

    def set_mode(self, mode):
        self._enabled = mode != "disabled"
        self.configure(cursor="hand2" if self._enabled else "arrow")
        if mode == "current":
            fill, edge, fg = PAGE_ON, ACCENT, ACCENT
        elif mode == "disabled":
            fill, edge, fg = SHEET, "#EBEEF5", DISABLED
        else:
            fill, edge, fg = SHEET, EDGE, MUTED
        self.delete("all")
        self.create_rectangle(1, 1, 27, 27, fill=fill, outline=edge)
        self.create_text(14, 14, text=self._text, fill=fg, font=(FONT, 11))

    def _click(self, _event):
        if self._enabled and self._command is not None:
            self._command()


class SizeBox(tk.Frame):
    """白底细边的条数框。箭头是一条折线，下拉是白底黑字。"""

    def __init__(self, parent, command):
        super().__init__(parent, bg=SHEET, height=40, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE, cursor="hand2")
        self.pack_propagate(False)
        self.configure(width=128)
        self._command = command
        self._value = 10
        self._pop = None
        self.label = tk.Label(self, text="10 条 / 页", bg=SHEET, fg=INK, font=(FONT, 13), cursor="hand2")
        self.label.pack(side="left", padx=(12, 0))
        arrow = tk.Canvas(self, width=16, height=16, bg=SHEET, highlightthickness=0, bd=0, cursor="hand2")
        arrow.pack(side="right", padx=(0, 10))
        arrow.create_line(4, 6, 8, 10, 12, 6, fill=MUTED, width=1, capstyle="round", joinstyle="round")
        for widget in (self, self.label, arrow):
            widget.bind("<Button-1>", self._open)

    def set_value(self, size):
        self._value = int(size)
        self.label.configure(text=self.get())

    def get(self):
        return f"{self._value} 条 / 页"

    def set(self, label):
        text = str(label).split()[0]
        if text.isdigit():
            self.set_value(int(text))

    def _open(self, _event):
        self._close()
        pop = tk.Toplevel(self)
        pop.overrideredirect(True)
        pop.configure(bg=LINE)
        box = tk.Frame(pop, bg=SHEET)
        box.pack(padx=1, pady=1, fill="both")
        for size in PAGE_SIZES:
            row = tk.Label(box, text=f"{size} 条 / 页", bg=SHEET, fg=INK, anchor="w", font=(FONT, 13), padx=12, pady=8, cursor="hand2")
            row.pack(fill="x")
            row.bind("<Enter>", lambda event, item=row: item.configure(bg=PAGE, fg=INK))
            row.bind("<Leave>", lambda event, item=row: item.configure(bg=SHEET, fg=INK))
            row.bind("<Button-1>", lambda event, value=size: self._choose(value))
        self.update_idletasks()
        pop.geometry(f"{self.winfo_width()}x{len(PAGE_SIZES) * 36}+{self.winfo_rootx()}+{self.winfo_rooty() + self.winfo_height() + 4}")
        self._pop = pop
        pop.bind("<FocusOut>", lambda event: self._close())
        pop.focus_set()

    def _choose(self, size):
        self._close()
        self.set_value(size)
        if self._command is not None:
            self._command(self.get())

    def _close(self):
        pop = self._pop
        self._pop = None
        if pop is not None:
            try:
                pop.destroy()
            except tk.TclError:
                pass


def _primary_button(parent, text, command):
    return SoftButton(parent, text, command, kind="primary")


def _outline_button(parent, text, command):
    return SoftButton(parent, text, command, kind="secondary")


def _small_button(parent, text, command):
    return SoftButton(parent, text, command, kind="quiet")


def _quiet_button(parent, text, command):
    button = ctk.CTkButton(
        parent,
        text=text,
        command=command,
        width=64,
        height=32,
        corner_radius=8,
        fg_color=BTN_FILL,
        hover_color=BTN_FILL_HOVER,
        text_color=INK,
        border_width=0,
        font=(FONT, 12),
    )
    _silence_focus(button)
    return button


def _fit_width(text, font, limit):
    """列不够宽时再截一截，避免文字画进旁边的格子。"""
    value = "" if text is None else str(text)
    face = tkfont.Font(family=font[0], size=font[1])
    if face.measure(value) <= limit:
        return value
    keep = value
    while keep and face.measure(keep + "...") > limit:
        keep = keep[:-1]
    return (keep + "...") if keep else "..."


def _clip_text(text, limit=10):
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _short_clock(text):
    moment = _parse_clock(text, end_of_day=False)
    if moment is None:
        return _clip_text(text)
    return moment.strftime("%m-%d %H:%M")


def _pill_colors(value):
    text = "" if value is None else str(value)
    if text in {"成功", "完成", "已提交", "已登录", "Confirm"}:
        return "#047857", "#ECFDF5"
    if text == "跳过":
        return "#B45309", "#FFFBEB"
    if text in {"失败", "需核对", "未登录", "Error"}:
        return "#B91C1C", "#FEF2F2"
    if text in {"检查中", "正在启动", "正在打开", "Process"}:
        return "#374151", "#F3F4F6"
    if text == "New":
        return "#1D4ED8", "#EFF6FF"
    return "#6B7280", "#F5F5F5"


class StatusPill(tk.Canvas):
    """圆角药丸。文字左右各留 8 像素，不贴着色块。"""

    def __init__(self, parent, value=""):
        try:
            host = parent.cget("bg")
        except tk.TclError:
            host = SHEET
        super().__init__(parent, height=22, width=48, bg=host, highlightthickness=0, bd=0, takefocus=0)
        self._host = host
        self._value = None
        self.set_value(value)

    def set_value(self, value):
        text = "" if value is None else str(value)
        self._value = text
        fg, bg = _pill_colors(text)
        face = tkfont.Font(family=FONT, size=11)
        width = max(face.measure(text or "—") + 16, 36)
        height = 22
        self.configure(width=width, height=height, bg=self._host)
        self.delete("all")
        _paint_round(self, 0, 0, width, height, 11, bg)
        self.create_text(width / 2, height / 2, text=text or "—", fill=fg, font=(FONT, 11))


def _paint_pill(label, value):
    text = "" if value is None else str(value)
    if getattr(label, "_value", None) == text:
        return
    if hasattr(label, "set_value"):
        label.set_value(text)
        return
    fg, bg = _pill_colors(text)
    label._value = text
    label.configure(text=text, fg=fg, bg=bg, padx=8, pady=2)


def _mark_button(button, chosen):
    button.configure(
        bg=ACCENT if chosen else SHEET,
        fg=ON_PRIMARY if chosen else INK,
        activebackground="#0369A1" if chosen else FIELD,
        activeforeground=ON_PRIMARY if chosen else INK,
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=ACCENT if chosen else LINE,
        highlightcolor=ACCENT,
        font=(FONT, 12, "bold" if chosen else "normal"),
    )


class CellTip:
    def __init__(self):
        self.win = None
        self.job = None
        self.owner = None

    def schedule(self, widget, text, x=None, y=None):
        self.hide()
        self.owner = widget
        self._at = (x, y)
        self.job = widget.after(350, lambda: self._show(widget, text))

    def _show(self, widget, text):
        self.job = None
        if not widget.winfo_exists():
            return
        win = tk.Toplevel(widget)
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(
            win,
            text=text,
            bg="#303133",
            fg="#FFFFFF",
            font=(FONT, 11),
            padx=8,
            pady=6,
            justify="left",
            wraplength=420,
        ).pack()
        at = getattr(self, "_at", (None, None))
        if at[0] is not None:
            win.geometry(f"+{int(at[0])}+{int(at[1])}")
        else:
            win.geometry(f"+{widget.winfo_rootx()}+{widget.winfo_rooty() + widget.winfo_height() + 6}")
        self.win = win

    def hide(self):
        if self.job is not None and self.owner is not None:
            try:
                self.owner.after_cancel(self.job)
            except tk.TclError:
                pass
        self.job = None
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None


class CheckTable(tk.Frame):
    """勾选和文字都是画布图元，不再往格子里放原生控件。"""

    ROW_H = 40
    HEAD_H = 36
    RULE_H = 1
    CHECK_W = 36

    def __init__(self, parent):
        super().__init__(parent, bg=SHEET)
        self.canvas = tk.Canvas(self, bg=SHEET, highlightthickness=0, bd=0, takefocus=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.groups = []
        self.on_change = None
        self.on_country = None
        self._checked = []
        self._header_on = False
        self._bands = []
        self._rules_y = []
        self._widths = []
        self._xs = []
        self._content_height = 1
        self._merge_n = 0
        self._detail_n = 0
        self._fitted_width = 0
        self._fitted_height = 0
        self._resize_job = None
        self._pending_size = None
        self._in_relayout = False
        self._reset_view = False
        self._hover_band = None
        self._hover_id = None
        self._tip = CellTip()
        self._tip_cell = None

    def destroy(self):
        self._cancel_resize()
        self._tip.hide()
        super().destroy()

    def _cancel_resize(self):
        if self._resize_job is None:
            return
        try:
            self.after_cancel(self._resize_job)
        except tk.TclError:
            pass
        self._resize_job = None

    def _arm_resize(self):
        self._cancel_resize()
        try:
            self._resize_job = self.after(150, self._relayout)
        except tk.TclError:
            self._resize_job = None

    def _on_configure(self, event):
        if event.width <= 1:
            return
        if self._in_relayout:
            self._pending_size = (event.width, event.height)
            return
        if event.width == self._fitted_width and event.height == self._fitted_height:
            return
        self._pending_size = (event.width, event.height)
        self._arm_resize()

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def show(self, groups, merge_keys, detail_keys, merge_heads, detail_heads):
        self._cancel_resize()
        self._tip.hide()
        self._tip_cell = None
        self.groups = [group for group in groups if group]
        self._checked = [False] * len(self.groups)
        self._header_on = False
        self._merge_n = len(merge_keys)
        self._detail_n = len(detail_keys)
        self._col_keys = list(merge_keys) + list(detail_keys)
        heads = list(merge_heads) + list(detail_heads)
        self.canvas.delete("grid")
        self._hover_band = None
        self._hover_id = self.canvas.create_rectangle(0, 0, 0, 0, fill="", outline="", tags="grid")
        bands = []
        header_cells = []
        header_cells.append(self._make_check(0, self.HEAD_H, header=True, group_index=None))
        for index, text in enumerate(heads):
            header_cells.append(self._make_text(index + 1, 0, self.HEAD_H, text, text, key="", header=True))
        bands.append({"y": 0, "h": self.HEAD_H, "kind": "head", "cells": header_cells, "group_index": None})
        y = self.HEAD_H
        rules = [y]
        y += self.RULE_H
        for group_index, group in enumerate(self.groups):
            if group_index:
                rules.append(y)
                y += self.RULE_H
            span = len(group)
            block_h = span * self.ROW_H
            cells = [self._make_check(y, block_h, header=False, group_index=group_index)]
            for offset, key in enumerate(merge_keys):
                raw = group[0].get(key, "")
                cells.append(self._make_text(1 + offset, y, block_h, raw, self._shown_text(raw, key), key=key, mono=(key == "task_id")))
            for index, row in enumerate(group):
                row_y = y + index * self.ROW_H
                for offset, key in enumerate(detail_keys):
                    raw = row.get(key, "")
                    cells.append(
                        self._make_text(
                            1 + self._merge_n + offset,
                            row_y,
                            self.ROW_H,
                            raw,
                            self._shown_text(raw, key, status=(key == "status")),
                            key=key,
                            status=(key == "status"),
                        )
                    )
            bands.append({"y": y, "h": block_h, "kind": "body", "cells": cells, "group_index": group_index})
            y += block_h
        if not self.groups:
            empty = self._make_text(0, y, 72, "没有记录", "没有记录", key="", header=True)
            empty["full_width"] = True
            bands.append({"y": y, "h": 72, "kind": "empty", "cells": [empty], "group_index": None})
            y += 72
        self._rules_y = rules
        self._content_height = max(y, 1)
        self._bands = bands
        self._rule_ids = []
        for rule_y in rules:
            self._rule_ids.append(self.canvas.create_line(0, rule_y, 1, rule_y, fill=DIVIDER, tags="grid"))
        self._header_bg = self.canvas.create_rectangle(0, 0, 1, self.HEAD_H, fill=SHEET, outline="", tags="grid")
        self.canvas.tag_lower(self._header_bg)
        self.canvas.tag_lower(self._hover_id)
        self._reset_view = True
        self._widths = []
        self._relayout()

    def _shown_text(self, raw, key, status=False):
        value = "" if raw is None else str(raw)
        if key == "time":
            return _short_clock(value)
        if status:
            return value or "—"
        return _clip_text(value)

    def _make_check(self, y, h, header, group_index):
        rect = self.canvas.create_rectangle(0, 0, 0, 0, fill=SHEET, outline=EDGE, width=1, tags="grid")
        mark = self.canvas.create_line(0, 0, 0, 0, 0, 0, fill=ON_PRIMARY, width=2, state="hidden", capstyle="round", tags="grid")
        return {
            "type": "check",
            "col": 0,
            "y": y,
            "h": h,
            "header": header,
            "group_index": group_index,
            "rect": rect,
            "mark": mark,
            "key": "",
            "text": "",
            "full": "",
        }

    def _make_text(self, col, y, h, raw, shown, key="", header=False, mono=False, status=False):
        value = "" if raw is None else str(raw)
        if status:
            fg, bg = _pill_colors(value or "—")
            self._pill_i = getattr(self, "_pill_i", 0) + 1
            item = self.canvas.create_text(0, 0, text=shown, anchor="w", fill=fg, font=(FONT, 11), tags="grid")
            return {
                "type": "pill",
                "col": col,
                "y": y,
                "h": h,
                "key": key,
                "text": shown,
                "full": value,
                "fill": fg,
                "pill": bg,
                "shape": f"pill{self._pill_i}",
                "item": item,
                "header": False,
            }
        fill = MUTED if header else INK
        font = (MONO, 12) if mono else (FONT, 12 if header else 13)
        item = self.canvas.create_text(0, 0, text=shown, anchor="w", fill=fill, font=font, tags="grid")
        return {
            "type": "text",
            "col": col,
            "y": y,
            "h": h,
            "key": key,
            "text": shown,
            "full": value,
            "font": font,
            "drawn": shown,
            "item": item,
            "header": header,
            "full_width": False,
        }

    def _column_boxes(self, width):
        width = max(int(width) - 20, self.CHECK_W)
        count = 1 + self._merge_n + self._detail_n
        check = min(self.CHECK_W, width)
        rest = max(width - check, 0)
        weights = [COLUMN_WEIGHT.get(key, 1) for key in getattr(self, "_col_keys", ())]
        if len(weights) != max(count - 1, 0):
            weights = [1] * max(count - 1, 0)
        total = sum(weights) or 1
        widths = [check]
        used = 0
        for index, weight in enumerate(weights):
            if index == len(weights) - 1:
                widths.append(max(rest - used, 0))
            else:
                part = int(rest * weight / total)
                widths.append(part)
                used += part
        keys = [None] + list(getattr(self, "_col_keys", ()))
        floors = {"time": 112, "kind": 84, "status": 72, "country": 72, "market": 72}
        for index, key in enumerate(keys):
            if index >= len(widths):
                break
            need = floors.get(key, 0)
            if need and widths[index] < need and len(widths) > 1:
                deficit = need - widths[index]
                widths[index] = need
                donor = max((item for item in range(len(widths)) if item != index), key=lambda item: widths[item])
                widths[donor] = max(36, widths[donor] - deficit)
        xs = []
        cursor = 0
        for item in widths:
            xs.append(cursor)
            cursor += item
        return xs, widths

    def _relayout(self):
        self._resize_job = None
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width <= 1 or not self._bands:
            return
        self._in_relayout = True
        self._pending_size = None
        try:
            expected = 1 + self._merge_n + self._detail_n
            width_changed = width != self._fitted_width or len(self._widths) != expected
            self._fitted_width = width
            self._fitted_height = height
            if self._reset_view:
                self.canvas.yview_moveto(0)
                self._reset_view = False
            if width_changed or not self._widths:
                self._xs, self._widths = self._column_boxes(width)
            self._place_all(width)
        finally:
            self._in_relayout = False
        pending = self._pending_size
        if pending and pending[0] > 1 and pending != (self._fitted_width, self._fitted_height):
            self._arm_resize()

    def _place_all(self, width):
        for rule_id, rule_y in zip(self._rule_ids, self._rules_y):
            self.canvas.coords(rule_id, 0, rule_y, width, rule_y)
        for band in self._bands:
            for cell in band["cells"]:
                self._place_cell(cell, width)
        if self._hover_band is not None:
            band = self._hover_band
            self.canvas.coords(self._hover_id, 0, band["y"], width, band["y"] + band["h"])
        self.canvas.configure(scrollregion=(0, 0, width, self._content_height))
        self.canvas.coords(self._header_bg, 0, 0, width, max(self.HEAD_H - 1, 1))
        self.canvas.tag_lower(self._header_bg)
        self.canvas.tag_lower(self._hover_id)
        if self._rule_ids:
            self.canvas.tag_raise(self._rule_ids[0])

    def _place_cell(self, cell, width):
        if cell.get("full_width"):
            x = 12
            col_w = max(width - 32, 12)
        else:
            col = cell["col"]
            x = self._xs[col]
            col_w = self._widths[col]
        if cell["type"] == "check":
            left = x + max((col_w - 16) / 2, 4)
            top = cell["y"] + max((cell["h"] - 16) / 2, 4)
            self.canvas.coords(cell["rect"], left, top, left + 16, top + 16)
            self.canvas.coords(
                cell["mark"],
                left + 3, top + 8,
                left + 7, top + 12,
                left + 13, top + 4,
            )
            self._paint_check(cell)
            return
        if cell["type"] == "pill":
            text_w = tkfont.Font(family=FONT, size=11).measure(cell["text"]) + 16
            box_w = min(max(text_w, 36), max(col_w - 16, 36))
            left = x + 8
            top = cell["y"] + max((cell["h"] - 22) / 2, 4)
            self.canvas.delete(cell["shape"])
            _paint_round(self.canvas, left, top, left + box_w, top + 22, 11, cell["pill"], (cell["shape"], "grid"))
            self.canvas.coords(cell["item"], left + 8, cell["y"] + cell["h"] / 2)
            self.canvas.tag_raise(cell["item"])
            return
        limit = max((width if cell.get("full_width") else col_w) - 16, 12)
        drawn = _fit_width(cell["text"], cell["font"], limit)
        if drawn != cell.get("drawn"):
            self.canvas.itemconfigure(cell["item"], text=drawn)
            cell["drawn"] = drawn
        self.canvas.coords(cell["item"], x + 8, cell["y"] + cell["h"] / 2)

    def _paint_check(self, cell):
        on = self._header_on if cell["header"] else self._checked[cell["group_index"]]
        self.canvas.itemconfigure(cell["rect"], fill=ACCENT if on else SHEET, outline=ACCENT if on else EDGE)
        self.canvas.itemconfigure(cell["mark"], state="normal" if on else "hidden")

    def _paint_checks(self):
        for band in self._bands:
            for cell in band["cells"]:
                if cell["type"] == "check":
                    self._paint_check(cell)

    def _col_at(self, x):
        for index, (left, width) in enumerate(zip(self._xs, self._widths)):
            if left <= x < left + width:
                return index
        return None

    def _band_at(self, y):
        for band in self._bands:
            if band["y"] <= y < band["y"] + band["h"]:
                return band
        return None

    def _cell_at(self, band, col, y):
        for cell in band["cells"]:
            if cell["col"] == col and cell["y"] <= y < cell["y"] + cell["h"]:
                return cell
        return None

    def _on_press(self, event):
        if not self._widths:
            return
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        col = self._col_at(x)
        band = self._band_at(y)
        if band is None or col is None:
            return
        if band["kind"] == "head" and col == 0:
            self._toggle_all()
            return
        if band["kind"] != "body":
            return
        if col == 0:
            self._toggle_group(band["group_index"])
            return
        cell = self._cell_at(band, col, y)
        if cell is None or cell.get("key") not in {"country", "market"}:
            return
        code = _as_code(cell.get("full"))
        if code and self.on_country is not None:
            self.on_country(code)

    def _toggle_all(self):
        turn_on = not (self._checked and all(self._checked))
        self._checked = [turn_on] * len(self._checked)
        self._header_on = turn_on
        self._paint_checks()
        self._notify()

    def _toggle_group(self, index):
        if index is None or index >= len(self._checked):
            return
        self._checked[index] = not self._checked[index]
        self._header_on = bool(self._checked) and all(self._checked)
        self._paint_checks()
        self._notify()

    def _on_motion(self, event):
        if not self._widths:
            return
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        band = self._band_at(y)
        if band is not None and band["kind"] == "body":
            if self._hover_band is not band:
                self._hover_band = band
                width = max(sum(self._widths), 1)
                self.canvas.coords(self._hover_id, 0, band["y"], width, band["y"] + band["h"])
                self.canvas.itemconfigure(self._hover_id, fill=ROW_HOVER)
                self.canvas.tag_lower(self._hover_id)
                self.canvas.tag_lower(self._header_bg)
                if self._rule_ids:
                    self.canvas.tag_raise(self._rule_ids[0])
        elif self._hover_band is not None:
            self._clear_hover()
        col = self._col_at(x)
        cell = self._cell_at(band, col, y) if band is not None and col is not None else None
        if cell is not None and cell.get("key") in {"country", "market"}:
            self.canvas.configure(cursor="hand2")
        else:
            self.canvas.configure(cursor="")
        if cell is self._tip_cell:
            return
        self._tip_cell = cell
        full = "" if cell is None else str(cell.get("full") or "")
        shown = "" if cell is None else str(cell.get("drawn") or cell.get("text") or "")
        if full and full != shown:
            self._tip.schedule(self.canvas, full, event.x_root + 14, event.y_root + 18)
        else:
            self._tip.hide()

    def _on_leave(self, event):
        # 提示框弹出来时，指针还在表格里，这一下 Leave 不能把提示清掉。
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if 0 <= event.x < width and 0 <= event.y < height:
            return
        self._clear_hover()
        self._tip_cell = None
        self._tip.hide()
        try:
            self.canvas.configure(cursor="")
        except tk.TclError:
            pass

    def _clear_hover(self):
        self._hover_band = None
        if self._hover_id is not None:
            self.canvas.coords(self._hover_id, 0, 0, 0, 0)
            self.canvas.itemconfigure(self._hover_id, fill="")

    def _notify(self):
        if self.on_change is not None:
            self.on_change()

    def selected_count(self):
        return sum(1 for flag in self._checked if flag)

    def selected_rows(self):
        chosen = []
        for group, flag in zip(self.groups, self._checked):
            if flag:
                chosen.extend(group)
        return chosen


def _cell_color(text, status=False):
    value = "" if text is None else str(text)
    if status and value in {"成功", "完成", "已提交"}:
        return GOOD
    if status and value in {"跳过"}:
        return WARN
    if status and value in {"失败", "需核对"}:
        return BAD
    return TEXT


def _apply_cell(widget, text, status=False, key=""):
    value = "" if text is None else str(text)
    if key == "time":
        shown = _short_clock(value)
    elif status:
        shown = value
    else:
        shown = _clip_text(value)
    chip = getattr(widget, "_chip", None)
    target = chip if chip is not None else widget
    if chip is not None:
        _paint_pill(chip, value or "—")
        chip.configure(text=shown or "—")
    else:
        target.configure(text=shown, fg=_cell_color(value, status), bg=SHEET)
    target._full_text = value
    widget._full_text = value


class DatePop:
    """圆角月历。白底细灰边，年份按钮白底，选中年份一圈品牌红。底部是全宽的全部时间。"""

    CELL = 32
    CHROMA = "#ff00ff"

    def __init__(self, master, x, y, year, month, day, on_pick, range_mode=False):
        self.year = int(year)
        self.month = int(month)
        self.day = int(day) if day else None
        self.on_pick = on_pick
        self.range_mode = range_mode
        self.anchor = None
        self.end = None
        self.view = "day"
        self.decade = (self.year // 10) * 10
        self._hot = None
        self._hits = []
        self._closed = False
        self._finish_job = None
        self.ox, self.oy = 8, 8
        width = 16 + self.CELL * 7
        height = 16 + 36 + 22 + self.CELL * 6 + (56 if range_mode else 12)
        outer_w = width + self.ox * 2
        outer_h = height + self.oy * 2
        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.configure(bg=self.CHROMA)
        self.win.geometry(f"{outer_w}x{outer_h}+{int(x)}+{int(y)}")
        try:
            self.win.wm_attributes("-transparentcolor", self.CHROMA)
        except tk.TclError:
            self.win.configure(bg=LINE)
        self.canvas = tk.Canvas(
            self.win, width=outer_w, height=outer_h, bg=self.CHROMA, highlightthickness=0, bd=0, takefocus=0,
        )
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda event: self._leave())
        self.win.bind("<Escape>", lambda event: self.close())
        self._draw()
        self.win.lift()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._finish_job is not None:
            try:
                self.win.after_cancel(self._finish_job)
            except tk.TclError:
                pass
            self._finish_job = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def _shift(self, step):
        month = self.month + int(step)
        year = self.year
        if month < 1:
            month = 12
            year -= 1
        elif month > 12:
            month = 1
            year += 1
        self.year = year
        self.month = month
        self._clamp_day()
        self._draw()

    def _clamp_day(self):
        _weekday, count = calendar.monthrange(self.year, self.month)
        if self.day is not None and self.day > count:
            self.day = count

    def _cancel_finish(self):
        if self._finish_job is None:
            return
        try:
            self.win.after_cancel(self._finish_job)
        except tk.TclError:
            pass
        self._finish_job = None

    def _chip(self, canvas, cx, cy, text, kind, value):
        face = tkfont.Font(family=FONT, size=12, weight="bold")
        half = face.measure(text) / 2
        left = int(round(cx - half - 8))
        right = int(round(cx + half + 8))
        top = int(cy) - 14
        bottom = int(cy) + 14
        if self._hot == (kind, value):
            _paint_round(canvas, left, top, right, bottom, 4, PILL_HOVER)
        canvas.create_text(int(round(cx)), int(cy), text=text, fill=INK, font=(FONT, 12, "bold"))
        self._hits.append((kind, left, top, right, bottom, value))

    def _arrows(self, canvas, width):
        for sign, center in ((-1, 22), (1, width - 22)):
            if self._hot == ("arrow", sign):
                _paint_round(canvas, center - 12, 10, center + 12, 38, 4, PILL_HOVER)
            canvas.create_text(center, 24, text="‹" if sign < 0 else "›", fill=INK, font=(FONT, 13))
            self._hits.append(("arrow", center - 14, 8, center + 14, 40, sign))

    def _panel(self, canvas, width, height, items, cols, kind, selected, gap_x=8, margin=12, size=12):
        footer = 48 if self.range_mode else 8
        gap_y = 8
        rows = (len(items) + cols - 1) // cols
        cell_w = int((width - margin * 2 - gap_x * (cols - 1)) / cols)
        cell_h = 36
        block_h = rows * cell_h + (rows - 1) * gap_y
        room = height - footer - 52
        top0 = 52 + max(0, (room - block_h) // 2)
        for index, (label, value) in enumerate(items):
            row, column = divmod(index, cols)
            in_row = min(cols, len(items) - row * cols)
            row_w = in_row * cell_w + (in_row - 1) * gap_x
            left = int((width - row_w) / 2) + column * (cell_w + gap_x)
            top = top0 + row * (cell_h + gap_y)
            right = left + cell_w
            bottom = top + cell_h
            chosen = value == selected
            if chosen:
                _paint_round(canvas, left, top, right, bottom, 8, SIDE_RED)
                _paint_round(canvas, left + 2, top + 2, right - 2, bottom - 2, 6, SHEET)
            elif self._hot == (kind, value):
                _paint_round(canvas, left, top, right, bottom, 8, "#E5E7EB")
            else:
                _paint_round(canvas, left, top, right, bottom, 8, SHEET)
            canvas.create_text((left + right) / 2, (top + bottom) / 2, text=label, fill=INK, font=(FONT, size))
            self._hits.append((kind, left, top, right, bottom, value))

    def _paint_shell(self):
        canvas = self.canvas
        width = int(canvas.cget("width"))
        height = int(canvas.cget("height"))
        _paint_round(canvas, 4, 5, width - 2, height - 1, 12, "#E5E7EB", "shell")
        _paint_round(canvas, 3, 4, width - 3, height - 2, 12, "#F3F4F6", "shell")
        _paint_round(canvas, 2, 2, width - 4, height - 4, 12, LINE, "shell")
        _paint_round(canvas, 3, 3, width - 5, height - 5, 11, CAL_BG, "shell")

    def _draw_footer(self, canvas, width, height):
        line_y = height - 44
        canvas.create_line(12, line_y, width - 12, line_y, fill=LINE)
        if self._hot == ("clear", 0):
            canvas.create_rectangle(8, line_y + 1, width - 8, height - 14, fill="#E5E7EB", outline="")
        canvas.create_text(width / 2, line_y + 22, text="全部时间", fill=INK, font=(FONT, 12))
        self._hits.append(("clear", 0, line_y, width, height, 0))

    def _draw(self):
        canvas = self.canvas
        canvas.delete("all")
        self._hits = []
        self._paint_shell()
        width = int(canvas.cget("width")) - self.ox * 2
        height = int(canvas.cget("height")) - self.oy * 2
        if self.view == "year":
            self._draw_years(canvas, width, height)
        elif self.view == "month":
            self._draw_months(canvas, width, height)
        else:
            self._draw_days(canvas, width)
        if self.range_mode:
            self._draw_footer(canvas, width, height)
        ox, oy = self.ox, self.oy
        self._hits = [(kind, left + ox, top + oy, right + ox, bottom + oy, value) for kind, left, top, right, bottom, value in self._hits]
        canvas.addtag_all("body")
        canvas.dtag("shell", "body")
        canvas.move("body", ox, oy)

    def _draw_days(self, canvas, width):
        self._arrows(canvas, width)
        face = tkfont.Font(family=FONT, size=12, weight="bold")
        year_text = f"{self.year}年"
        month_text = f"{self.month}月"
        gap = 4
        total = face.measure(year_text) + 16 + gap + face.measure(month_text) + 16
        origin = (width - total) / 2
        year_cx = origin + 8 + face.measure(year_text) / 2
        month_cx = origin + face.measure(year_text) + 16 + gap + 8 + face.measure(month_text) / 2
        self._chip(canvas, year_cx, 24, year_text, "year-label", self.year)
        self._chip(canvas, month_cx, 24, month_text, "month-label", self.month)
        names = "一二三四五六日"
        for index, name in enumerate(names):
            canvas.create_text(8 + self.CELL * index + self.CELL / 2, 58, text=name, fill=MUTED, font=(FONT, 10))
        weekday, count = calendar.monthrange(self.year, self.month)
        for day in range(1, count + 1):
            slot = weekday + day - 1
            column = slot % 7
            row = slot // 7
            left = 8 + column * self.CELL
            top = 74 + row * self.CELL
            right = left + self.CELL - 4
            bottom = top + self.CELL - 4
            key = (self.year, self.month, day)
            chosen = day == self.day
            between = False
            if self.range_mode and self.anchor is not None:
                end = self.end
                if end is None:
                    chosen = key == self.anchor
                else:
                    low, high = sorted((self.anchor, end))
                    chosen = key in {low, high}
                    between = low < key < high
            if between:
                canvas.create_rectangle(left, top, right, bottom, fill="#F3F4F6", outline="#F3F4F6")
            elif chosen:
                _paint_round(canvas, left, top, right, bottom, 6, SIDE_RED)
                _paint_round(canvas, left + 2, top + 2, right - 2, bottom - 2, 4, SHEET)
            canvas.create_text(
                (left + right) / 2,
                (top + bottom) / 2,
                text=str(day),
                fill=INK,
                font=(FONT, 11),
            )
            self._hits.append(("day", left, top, right, bottom, day))

    def _draw_years(self, canvas, width, height):
        self._arrows(canvas, width)
        start = self.decade
        canvas.create_text(width / 2, 24, text=f"{start} - {start + 9}", fill=INK, font=(FONT, 12, "bold"))
        items = [(str(start + offset), start + offset) for offset in range(10)]
        self._panel(canvas, width, height, items, 5, "year", self.year, gap_x=6, margin=8, size=12)

    def _draw_months(self, canvas, width, height):
        self._chip(canvas, width / 2, 24, f"{self.year}年", "year-label", self.year)
        items = [(f"{month}月", month) for month in range(1, 13)]
        self._panel(canvas, width, height, items, 4, "month-cell", self.month)

    def _motion(self, event):
        if self._closed:
            return
        hot = None
        for kind, left, top, right, bottom, value in self._hits:
            if kind in {"year-label", "month-label", "year", "month-cell", "arrow", "clear"} and left <= event.x <= right and top <= event.y <= bottom:
                hot = (kind, value)
                break
        if hot != self._hot:
            self._hot = hot
            self._draw()

    def _leave(self):
        if self._closed or self._hot is None:
            return
        self._hot = None
        self._draw()

    def _click(self, event):
        if self._closed:
            return
        for kind, left, top, right, bottom, value in self._hits:
            if left <= event.x <= right and top <= event.y <= bottom:
                if kind == "arrow":
                    self._cancel_finish()
                    if self.view == "year":
                        self.decade += 10 * int(value)
                        self._hot = None
                        self._draw()
                    else:
                        self._shift(value)
                elif kind == "year-label":
                    self._cancel_finish()
                    self.decade = (self.year // 10) * 10
                    self.view = "year"
                    self._hot = None
                    self._draw()
                elif kind == "month-label":
                    self._cancel_finish()
                    self.view = "month"
                    self._hot = None
                    self._draw()
                elif kind == "year":
                    self.year = int(value)
                    self.view = "month"
                    self._hot = None
                    self._draw()
                elif kind == "month-cell":
                    self.month = int(value)
                    self._clamp_day()
                    self.view = "day"
                    self._hot = None
                    self._draw()
                elif kind == "clear":
                    callback = self.on_pick
                    self.close()
                    callback(None, None)
                elif self.range_mode:
                    picked = (self.year, self.month, value)
                    if self.anchor is None or self.end is not None:
                        self.anchor = picked
                        self.end = None
                        self._draw()
                        return
                    self.end = picked
                    if self.end < self.anchor:
                        self.anchor, self.end = self.end, self.anchor
                    self._draw()
                    self._cancel_finish()
                    self._finish_job = self.win.after(140, self._finish)
                else:
                    self.day = value
                    self._draw()
                    self._cancel_finish()
                    self._finish_job = self.win.after(140, self._finish)
                return

    def _finish(self):
        self._finish_job = None
        if self._closed:
            return
        callback = self.on_pick
        if self.range_mode:
            if self.anchor is None:
                return
            start = self._iso(self.anchor)
            end = self._iso(self.end or self.anchor)
            self.close()
            callback(start, end)
            return
        if self.day is None:
            return
        text = f"{self.year:04d}-{self.month:02d}-{self.day:02d}"
        self.close()
        callback(text)

    def _iso(self, parts):
        year, month, day = parts
        return f"{year:04d}-{month:02d}-{day:02d}"


class HubWindow:
    """左侧账号管理，右侧上传记录。窗口保持打开，表格随上传刷新。"""

    def __init__(self, database_path, picture_log_path, aplus_log_path, progress_note=""):
        self.database_path = Path(database_path)
        self.picture_log_path = Path(picture_log_path)
        self.aplus_log_path = Path(aplus_log_path)
        self.page = "accounts"
        self.record_sites = set()
        self.filter_category = "site"
        self._filter_pop = None
        self.record_span = "all"
        self.filter_from = ""
        self.filter_to = ""
        self.filter_until_now = True
        self.record_query = ""
        self.record_page = 1
        self.record_page_size = 10
        self.record_pages = 1
        self.accounts_page = None
        self.records_page = None
        self._shown_signature = None
        self.record_site_count = 0
        self.record_shown_count = 0
        self.last_upload_at = _load_last_upload(self._last_upload_path())
        self._login_token = {}
        self.uploading = False
        self._progress_spinning = False
        self._progress_done = 0
        self._progress_total = 0
        self.launching = False
        self._login_gen = 0
        self.login_labels = {}
        self._nav_hot = None
        self._nav_order = ()
        self._nav_top = 0
        self._nav_bottom = 0
        self._filter_hot = None
        self._filter_buttons = {}
        self._thumb_geom = None
        self._thumb_code = None
        self._thumb_job = None
        self._log_lines = []
        self._log_offset = 0
        self._log_follow = True
        self._log_top = 0
        self._side_height = 0
        self._spin_job = None
        self._spin_pos = 0
        self._shield_ctk = False
        self._fill_after_resize = False
        self._resize_cover_job = None
        self._root_size = None
        self._date_pop = None
        self._date_guard = False
        self.root = tk.Tk()
        self.root.bind_all("<Button-1>", self._close_filter_if_outside, add="+")
        self.root.title("Aosom 图片与 A+")
        self.root.geometry("1180x720")
        self.root.minsize(980, 560)
        self.root.configure(bg=FRAME, highlightthickness=0)
        self._build()
        self.show_accounts(check=True)
        set_listener(self._on_progress)
        if progress_note:
            self._set_progress(progress_note, 0, 1)
        self.root.after(60000, self._watch_login)

    def run(self):
        self.root.mainloop()

    def _build(self):
        side = tk.Frame(self.root, bg=SIDE, width=SIDE_W)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        _install_ttk_styles(self.root)
        self.log_frame = tk.Frame(side, bg=SIDE, height=LOG_H)
        self.log_frame.pack(side="bottom", fill="x", padx=16, pady=(0, 20))
        self.log_frame.pack_propagate(False)
        tk.Label(self.log_frame, text="进度", bg=SIDE, fg=ON_PRIMARY, font=(FONT, 12, "bold")).pack(anchor="w")
        self.progress_label = tk.Label(self.log_frame, text="尚未开始", bg=SIDE, fg=SIDE_TEXT, font=(FONT, 10), anchor="w")
        self.progress_label.pack(anchor="w", fill="x", pady=(4, 6))
        self._bar_canvas = tk.Canvas(self.log_frame, height=6, bg=SIDE, highlightthickness=0, bd=0)
        self._bar_canvas.pack(fill="x", pady=(0, 8))
        self._bar_width = 0
        self._bar_track = self._bar_canvas.create_rectangle(0, 0, 0, 6, fill="#333333", outline="")
        self._bar_fill = self._bar_canvas.create_rectangle(0, 0, 0, 6, fill=ON_PRIMARY, outline="")
        self._bar_canvas.bind("<Configure>", self._on_bar_configure)
        self.progress_text = tk.Text(
            self.log_frame,
            height=6,
            wrap="word",
            bg=SIDE_HOVER,
            fg=SIDE_TEXT,
            bd=0,
            font=(FONT, 10),
            relief="flat",
            highlightthickness=0,
            insertbackground=SIDE_TEXT,
            spacing1=2,
            spacing3=4,
        )
        self.progress_text.pack(fill="both", expand=True)
        self.progress_text.bind("<Key>", self._block_progress_key)
        self.progress_text.bind("<MouseWheel>", self._progress_wheel)
        self.side_canvas = tk.Canvas(side, bg=SIDE, width=SIDE_W, highlightthickness=0, bd=0, takefocus=0, cursor="hand2")
        self._logo_image = _load_logo()
        self.side_canvas.create_image(16, 24, image=self._logo_image, anchor="nw")
        self._nav_top = 24 + self._logo_image.height() + 20
        self._draw_nav()
        self.side_canvas.configure(height=self._nav_bottom + 8)
        self.side_canvas.pack(side="top", fill="x")
        self.side_canvas.bind("<Button-1>", self._nav_click)
        self.side_canvas.bind("<Motion>", self._nav_motion)
        self.side_canvas.bind("<Leave>", self._nav_leave)
        self.root.update_idletasks()
        self._root_size = (self.root.winfo_width(), self.root.winfo_height())
        self.root.bind("<Configure>", self._on_root_configure)
        self.main = tk.Frame(self.root, bg=PAGE)
        self.main.pack(side="left", fill="both", expand=True)
        self.title_row = tk.Frame(self.main, bg=PAGE)
        self.title_row.pack(fill="x", padx=20, pady=(18, 0))
        self.title_label = tk.Label(self.title_row, bg=PAGE, fg=INK, font=(FONT, 22, "bold"))
        self.title_label.pack(side="left")
        self.title_actions = tk.Frame(self.title_row, bg=PAGE)
        self.account_actions = tk.Frame(self.title_actions, bg=PAGE)
        self.record_actions = tk.Frame(self.title_actions, bg=PAGE)
        self.subtitle = tk.Label(self.main, bg=PAGE, fg=MUTED, font=(FONT, 14), justify="left")
        self.subtitle.pack(anchor="w", padx=20, pady=(6, 12))
        self.body = tk.Frame(self.main, bg=PAGE)
        self.body.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        self.footer = tk.Label(self.main, bg=PAGE, fg=MUTED, font=(FONT, 12), justify="left", anchor="w")
        self.footer.pack(fill="x", padx=20, pady=(0, 16))

    def _switch(self, page):
        if page == self.page:
            return
        self._close_filter()
        if page == "accounts":
            self.show_accounts(check=False)
        else:
            self.show_records()

    def _show_page(self, page):
        for frame in (self.accounts_page, self.records_page):
            if frame is None or frame is page:
                continue
            frame.pack_forget()
        if not page.winfo_manager():
            page.pack(fill="both", expand=True)

    def _release_record_wheel(self):
        table = getattr(self, "record_table", None)
        if table is None:
            return
        try:
            if table.winfo_exists():
                table.canvas.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass

    def _draw_nav(self):
        items = (("accounts", "账号管理"), ("records", "上传记录"), ("excel", "选择 Excel"))
        self._nav_order = tuple(key for key, _text in items)
        canvas = self.side_canvas
        self.nav_canvas = canvas
        self._nav_bg = {}
        self._nav_bar = {}
        self._nav_text = {}
        for index, (key, text) in enumerate(items):
            top = self._nav_top + index * NAV_ROW_H
            self._nav_bg[key] = canvas.create_rectangle(0, top, SIDE_W, top + NAV_ROW_H, fill=SIDE, outline="", tags="nav")
            self._nav_bar[key] = canvas.create_rectangle(0, top, 3, top + NAV_ROW_H, fill=SIDE, outline="", tags="nav")
            self._nav_text[key] = canvas.create_text(18, top + NAV_ROW_H / 2, text=text, anchor="w", fill=SIDE_TEXT, font=(FONT, 13), tags="nav")
        self._nav_bottom = self._nav_top + NAV_ROW_H * len(items)

    def _nav_key_at(self, y):
        if y < self._nav_top or y >= self._nav_bottom:
            return None
        index = int((y - self._nav_top) // NAV_ROW_H)
        if index < 0 or index >= len(self._nav_order):
            return None
        return self._nav_order[index]

    def _nav_click(self, event):
        key = self._nav_key_at(event.y)
        if key == "excel":
            self.choose_excel()
        elif key in {"accounts", "records"}:
            self._switch(key)

    def _nav_motion(self, event):
        key = self._nav_key_at(event.y)
        if key == self._nav_hot:
            return
        previous = self._nav_hot
        self._nav_hot = key
        if previous is not None:
            self._paint_nav_hover(previous, False)
        if key is not None:
            self._paint_nav_hover(key, True)

    def _nav_leave(self, _event):
        if self._nav_hot is None:
            return
        previous = self._nav_hot
        self._nav_hot = None
        self._paint_nav_hover(previous, False)

    def _paint_nav_hover(self, key, hover):
        active = key == self.page
        if active:
            bg = SIDE_ACTIVE
        elif hover:
            bg = SIDE_HOVER
        else:
            bg = SIDE
        self.nav_canvas.itemconfig(self._nav_bg[key], fill=bg)
        self.nav_canvas.itemconfig(self._nav_bar[key], fill=SIDE_RED if active else bg)

    def _mark_nav(self):
        for key in self._nav_order:
            active = key == self.page
            self.nav_canvas.itemconfig(
                self._nav_text[key],
                fill=ON_PRIMARY if active else SIDE_TEXT,
                font=(FONT, 13, "bold" if active else "normal"),
            )
            self._paint_nav_hover(key, key == self._nav_hot)

    def _on_root_configure(self, event):
        if event.widget is not self.root or event.width <= 1:
            return
        size = (event.width, event.height)
        if size == self._root_size:
            return
        self._root_size = size
        self._arm_resize_cover()

    def _arm_resize_cover(self):
        """缩放时表格晚 150 毫秒再填。侧栏和筛选是画布图元，留在画面上，不再盖住重画。"""
        self._shield_ctk = True
        if self._resize_cover_job is not None:
            try:
                self.root.after_cancel(self._resize_cover_job)
            except tk.TclError:
                pass
        self._resize_cover_job = self.root.after(150, self._finish_resize_cover)

    def _finish_resize_cover(self):
        self._resize_cover_job = None
        self._shield_ctk = False
        if self._fill_after_resize:
            self._fill_after_resize = False
            self._fill_tables()

    def _on_bar_configure(self, event):
        if event.widget is not self._bar_canvas or event.width <= 1:
            return
        if event.width == self._bar_width:
            return
        self._bar_width = event.width
        self._bar_canvas.coords(self._bar_track, 0, 0, event.width, 6)
        if not self._progress_spinning:
            self._paint_bar_value()

    def _paint_bar_value(self):
        x1, y1, x2, y2 = self._bar_canvas.coords(self._bar_track)
        span = max(x2 - x1, 0)
        total = self._progress_total
        if total <= 0:
            ratio = 0
        else:
            ratio = min(max(self._progress_done, 0), total) / total
        self._bar_canvas.coords(self._bar_fill, x1, y1, x1 + span * ratio, y2)
        self._bar_canvas.itemconfig(self._bar_fill, fill=ON_PRIMARY)

    def _stop_spin(self):
        self._progress_spinning = False
        job = self._spin_job
        self._spin_job = None
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass

    def _start_spin(self):
        if self._progress_spinning:
            return
        self._progress_spinning = True
        self._spin_pos = 0
        self._tick_spin()

    def _tick_spin(self):
        self._spin_job = None
        if not self._progress_spinning:
            return
        x1, y1, x2, y2 = self._bar_canvas.coords(self._bar_track)
        width = max(x2 - x1, 1)
        seg = 42
        travel = width + seg
        self._spin_pos = (self._spin_pos + 6) % travel
        left = max(x1, x1 + self._spin_pos - seg)
        right = min(x2, left + seg)
        if right <= left:
            right = min(x2, left + 1)
        self._bar_canvas.coords(self._bar_fill, left, y1, right, y2)
        self._bar_canvas.itemconfig(self._bar_fill, fill=ON_PRIMARY)
        self._spin_job = self.root.after(50, self._tick_spin)

    def _style_main(self, canvas):
        self.main.configure(bg=PAGE)
        for widget in (self.title_row, self.title_label, self.title_actions, self.account_actions, self.record_actions, self.subtitle, self.body, self.footer):
            widget.configure(bg=PAGE)
        self.title_label.configure(font=(FONT, 20, "bold"), fg=INK)
        self.subtitle.configure(fg=MUTED, font=(FONT, 12))

    def _show_header_actions(self, frame):
        for item in (self.account_actions, self.record_actions):
            if item.winfo_manager():
                item.pack_forget()
        frame.pack(side="right", pady=(0, 4))
        if not self.title_actions.winfo_manager():
            self.title_actions.pack(side="right", padx=(0, 24), pady=(0, 8))

    def show_accounts(self, check=False):
        self.page = "accounts"
        self._mark_nav()
        self._style_main(False)
        self._close_filter()
        self._release_record_wheel()
        self._ensure_account_actions()
        self._show_header_actions(self.account_actions)
        self._ensure_accounts_page()
        self._show_page(self.accounts_page)
        self.title_label.configure(text="账号管理")
        self.subtitle.configure(text="欧洲五国各自使用独立 Chrome。")
        if not self.footer.winfo_manager():
            self.footer.pack(fill="x", padx=20, pady=(0, 16))
        self.footer.configure(text="未登录：还在卖家欢迎页。已登录：已经进入卖家后台。")
        if check and not self.launching:
            self._check_login_async(quiet=True)

    def _ensure_accounts_page(self):
        if self.accounts_page is not None and self.accounts_page.winfo_exists():
            return
        page = tk.Frame(self.body, bg=PAGE)
        self.accounts_page = page
        self.login_labels = {}
        card = tk.Frame(page, bg=SHEET, highlightthickness=1, highlightbackground=LINE, highlightcolor=LINE, takefocus=0)
        card.pack(fill="both", expand=True)
        head = tk.Frame(card, bg=HEAD)
        head.pack(fill="x")
        tk.Label(head, text="国家", width=16, anchor="w", bg=HEAD, fg=MUTED, font=(FONT, 12)).pack(side="left", padx=16, pady=10)
        tk.Label(head, text="端口", width=12, anchor="w", bg=HEAD, fg=MUTED, font=(FONT, 12)).pack(side="left")
        tk.Label(head, text="状态", anchor="w", bg=HEAD, fg=MUTED, font=(FONT, 12)).pack(side="left", padx=8)
        for code, name in EU_COUNTRIES:
            tk.Frame(card, height=1, bg=LINE).pack(fill="x")
            row = tk.Frame(card, bg=SHEET)
            row.pack(fill="x")
            tk.Label(row, text=f"{name}  {code}", width=16, anchor="w", bg=SHEET, fg=INK, font=(FONT, 13)).pack(side="left", padx=16, pady=14)
            tk.Label(row, text=str(COUNTRY_PORTS[code]), width=12, anchor="w", bg=SHEET, fg=MUTED, font=(MONO, 12)).pack(side="left")
            status = StatusPill(row, "正在启动" if self.launching else "未确认")
            status.pack(side="left", padx=8, pady=12)
            self.login_labels[code] = status
            _small_button(row, "打开", lambda c=code: self.open_country(c)).pack(side="right", padx=(12, 24), pady=(8, 12))
        tk.Frame(card, bg=SHEET, height=16).pack(fill="x")

    def _watch_login(self):
        """每分钟看一次五国登录。端口没开就是尚未打开，不拉起 Chrome。"""
        if not self.launching:
            self._check_login_async(quiet=True)
        self.root.after(60000, self._watch_login)

    def start_all_countries(self):
        if self.launching:
            self.footer.configure(text="五个国家正在启动。")
            return
        self.launching = True
        self._login_gen += 1
        for label in self.login_labels.values():
            if label.winfo_exists():
                _paint_pill(label, "正在启动")
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
        self._check_login_async(quiet=True)

    def _check_login_async(self, quiet=False):
        if self.launching:
            return
        self._login_gen += 1
        generation = self._login_gen
        for code, _name in EU_COUNTRIES:
            self._login_token[code] = self._login_token.get(code, 0) + 1
        if not quiet:
            for label in self.login_labels.values():
                if label.winfo_exists():
                    _paint_pill(label, "检查中")

        def work():
            states = {}
            for code, _name in EU_COUNTRIES:
                states[code] = read_login_state(code)
            self.root.after(0, lambda: self._apply_login(generation, states))

        threading.Thread(target=work, daemon=True).start()

    def _apply_login(self, generation, states):
        if generation != self._login_gen:
            return
        for code, text in states.items():
            label = self.login_labels.get(code)
            if label is not None and label.winfo_exists():
                _paint_pill(label, text)

    def open_country(self, code):
        label = self.login_labels.get(code)
        if label is not None and label.winfo_exists():
            _paint_pill(label, "正在打开")
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

        def work():
            state = read_login_state(code)
            self.root.after(0, lambda: self._apply_one(code, token, state))

        threading.Thread(target=work, daemon=True).start()

    def _apply_one(self, code, token, state):
        if self._login_token.get(code) != token:
            return
        label = self.login_labels.get(code)
        if label is not None and label.winfo_exists():
            _paint_pill(label, state)

    def show_records(self):
        self.page = "records"
        self._mark_nav()
        self._style_main(True)
        self._close_filter()
        self._ensure_records_page()
        if self.title_actions.winfo_manager():
            self.title_actions.pack_forget()
        self._show_page(self.records_page)
        self.title_label.configure(text="上传记录")
        self.subtitle.configure(text="")
        if self.footer.winfo_manager():
            self.footer.pack_forget()
        self._paint_chips()
        self._fill_tables()

    def _ensure_account_actions(self):
        if getattr(self, "_account_actions_ready", False):
            return
        primary = _primary_button(self.account_actions, "一起拉起", self.start_all_countries)
        secondary = _outline_button(self.account_actions, "重新检查登录", self._check_login_async)
        primary.pack(side="left", pady=(0, 4))
        secondary.pack(side="left", padx=(8, 4), pady=(0, 4))
        self.account_actions.update_idletasks()
        width = primary.winfo_reqwidth() + secondary.winfo_reqwidth() + 20
        height = max(primary.winfo_reqheight(), secondary.winfo_reqheight()) + 8
        self.account_actions.configure(width=width, height=height)
        self.account_actions.pack_propagate(False)
        self._account_actions_ready = True

    def _ensure_title_actions(self):
        if getattr(self, "_title_actions_ready", False):
            return
        tk.Label(self.record_actions, text="task_id", bg=PAGE, fg=MUTED, font=(FONT, 12)).pack(side="left", padx=(0, 8))
        self.task_entry = tk.Entry(
            self.record_actions,
            font=(FONT, 12),
            relief="flat",
            bd=0,
            width=18,
            bg=FIELD,
            fg=INK,
            insertbackground=INK,
            highlightthickness=0,
        )
        self.task_entry.pack(side="left", padx=(0, 8), ipady=4)
        self.task_entry.bind("<Return>", lambda event: self._apply_task_query(self.task_entry.get()))
        _outline_button(self.record_actions, "刷新", self._fill_tables).pack(side="left", padx=(0, 8))
        _primary_button(self.record_actions, "下载勾选", self._download_current).pack(side="left")
        self._title_actions_ready = True

    def _build_filter(self, card):
        """国家、日期、查询画在一张画布上。缩放只拉宽画布，不重建这些图元。"""
        canvas = tk.Canvas(card, bg=SHEET, height=FILTER_H, highlightthickness=0, bd=0, takefocus=0, cursor="hand2")
        canvas.pack(fill="x")
        self.filter_canvas = canvas
        self._filter_buttons = {}
        self._filter_hot = None
        face = tkfont.Font(family=FONT, size=14)
        names = (("all", "全部"),) + EU_COUNTRIES
        track_h, inset = 40, 4
        tab_h = track_h - inset * 2
        widths = [max(face.measure(name) + 28, 56) for _code, name in names]
        track_w = inset * 2 + sum(widths)
        track_x, track_y = 16, 16
        _paint_round(canvas, track_x, track_y, track_x + track_w, track_y + track_h, track_h // 2, TAB_TRACK, "site-track")
        x = track_x + inset
        for (code, name), width in zip(names, widths):
            self._add_filter_button(("site", code), x, track_y + inset, width, tab_h, name, 14, radius=tab_h // 2)
            x += width
        small = tkfont.Font(family=FONT, size=14)
        row_y, row_h, radius = 68, 40, 8
        canvas.create_text(16, row_y + row_h / 2, text="task_id", anchor="w", fill=MUTED, font=(FONT, 14))
        entry_x = 16 + small.measure("task_id") + 12
        field_w = 220
        _paint_round(canvas, entry_x, row_y, entry_x + field_w, row_y + row_h, radius, BTN_FILL, "task-field")
        shell = tk.Frame(canvas, bg=BTN_FILL, height=row_h - radius * 2, width=field_w - radius * 2, highlightthickness=0, bd=0)
        shell.pack_propagate(False)
        self.task_entry = tk.Entry(
            shell,
            relief="flat",
            bd=0,
            bg=BTN_FILL,
            fg=INK,
            insertbackground=INK,
            highlightthickness=0,
            font=(FONT, 14),
        )
        self.task_entry.pack(fill="both", expand=True)
        self.task_entry.bind("<Return>", lambda event: self._apply_record_filter())
        canvas.create_window(
            entry_x + radius,
            row_y + radius,
            window=shell,
            anchor="nw",
            width=field_w - radius * 2,
            height=row_h - radius * 2,
        )
        x = entry_x + field_w + 12
        self._add_date_shell(x, row_y)
        x += self._filter_buttons[("date", "range")]["box"][2] - self._filter_buttons[("date", "range")]["box"][0] + 12
        self._add_filter_button(("action", "query"), x, row_y, 88, row_h, "查询", 14, radius=8)
        x += 88 + 8
        self._add_filter_button(("action", "refresh"), x, row_y, 88, row_h, "刷新", 14, radius=8)
        canvas.bind("<Button-1>", self._filter_click)
        canvas.bind("<Motion>", self._filter_motion)
        canvas.bind("<Leave>", self._filter_leave)
        canvas.bind("<MouseWheel>", lambda event: "break")
        for key in self._filter_buttons:
            self._paint_filter_button(key)
        self._sync_site_thumb()

    def _add_date_shell(self, x, y):
        """一个圆角外壳，高度与 task_id 相同。未选时只写全部时间。"""
        canvas = self.filter_canvas
        face = tkfont.Font(family=FONT, size=12)
        width = max(188, face.measure("12-31 至 12-31") + 56)
        height = 40
        x, y = int(x), int(y)
        edge = "edge-date-range"
        fill = "fill-date-range"
        _paint_round(canvas, x, y, x + width, y + height, 8, BTN_FILL, edge)
        _paint_round(canvas, x, y, x + width, y + height, 8, BTN_FILL, fill)
        icon = x + 14
        top = y + (height - 14) // 2
        canvas.create_rectangle(icon, top + 2, icon + 14, top + 14, outline=MUTED, width=1)
        canvas.create_line(icon, top + 6, icon + 14, top + 6, fill=MUTED)
        canvas.create_line(icon + 4, top, icon + 4, top + 4, fill=MUTED)
        canvas.create_line(icon + 10, top, icon + 10, top + 4, fill=MUTED)
        text_id = canvas.create_text(x + 32, y + height / 2, text="全部时间", anchor="w", fill=INK, font=(FONT, 14))
        canvas.create_text(x + width - 14, y + height / 2, text="▼", fill=MUTED, font=(FONT, 9))
        self._filter_buttons[("date", "range")] = {
            "box": (x, y, x + width, y + height),
            "edge": edge,
            "fill": fill,
            "text": text_id,
        }

    def _add_filter_button(self, key, x, y, width, height, text, size, radius=4):
        x, y, width, height = int(x), int(y), int(width), int(height)
        if key[0] == "site":
            text_id = self.filter_canvas.create_text(
                x + width / 2,
                y + height / 2,
                text=text,
                fill=TAB_TEXT,
                font=(FONT, size),
            )
            self._filter_buttons[key] = {
                "box": (x, y, x + width, y + height),
                "edge": None,
                "fill": None,
                "text": text_id,
                "radius": height // 2,
            }
            return
        edge = f"edge-{key[0]}-{key[1]}"
        fill = f"fill-{key[0]}-{key[1]}"
        _paint_round(self.filter_canvas, x, y, x + width, y + height, radius, EDGE, edge)
        inner = max(radius - 1, 0)
        _paint_round(self.filter_canvas, x + 1, y + 1, x + width - 1, y + height - 1, inner, SHEET, fill)
        weight = "bold" if key[0] == "action" else "normal"
        text_id = self.filter_canvas.create_text(
            x + width / 2,
            y + height / 2,
            text=text,
            fill=INK,
            font=(FONT, size, weight),
        )
        self._filter_buttons[key] = {
            "box": (x, y, x + width, y + height),
            "edge": edge,
            "fill": fill,
            "text": text_id,
            "radius": radius,
        }

    def _filter_colors(self, key):
        hot = self._filter_hot == key
        kind, name = key
        if kind == "site":
            chosen = next(iter(self.record_sites)) if len(self.record_sites) == 1 else ""
            on = (name == "all" and not self.record_sites) or name == chosen
            if on:
                return TAB_ON, TAB_ON, TAB_ON_TEXT
            return TAB_TRACK, TAB_TRACK, TAB_TEXT
        fill = BTN_FILL_HOVER if hot else BTN_FILL
        return fill, fill, INK

    def _selected_site_code(self):
        if len(self.record_sites) == 1:
            return next(iter(self.record_sites))
        return "all"

    def _place_thumb(self, x, y, w, h):
        canvas = self.filter_canvas
        canvas.delete("site-thumb")
        x, y, w, h = int(round(x)), int(round(y)), int(round(w)), int(round(h))
        radius = max(h // 2, 1)
        _paint_round(canvas, x, y, x + w, y + h, radius, TAB_ON, "site-thumb")
        self._thumb_geom = (x, y, w, h)
        canvas.tag_lower("site-track")
        canvas.tag_raise("site-thumb")
        right = x + w
        for key, meta in self._filter_buttons.items():
            if key[0] != "site":
                continue
            x1, _y1, x2, _y2 = meta["box"]
            center = (x1 + x2) / 2
            canvas.itemconfig(meta["text"], fill=TAB_ON_TEXT if x <= center <= right else TAB_TEXT)
            canvas.tag_raise(meta["text"])

    def _sync_site_thumb(self):
        canvas = getattr(self, "filter_canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return
        code = self._selected_site_code()
        meta = self._filter_buttons.get(("site", code))
        if meta is None:
            return
        x1, y1, x2, y2 = meta["box"]
        target = (x1, y1, x2 - x1, y2 - y1)
        if self._thumb_geom is None or self._thumb_code is None:
            self._place_thumb(*target)
            self._thumb_code = code
            return
        if code == self._thumb_code:
            return
        start = self._thumb_geom
        self._thumb_code = code
        self._run_thumb(start, target)

    def _run_thumb(self, start, target):
        if self._thumb_job is not None:
            try:
                self.filter_canvas.after_cancel(self._thumb_job)
            except tk.TclError:
                pass
            self._thumb_job = None
        steps = 8

        def frame(index):
            self._thumb_job = None
            if not self.filter_canvas.winfo_exists():
                return
            amount = index / steps
            ease = 1 - (1 - amount) ** 3
            geom = tuple(start[axis] + (target[axis] - start[axis]) * ease for axis in range(4))
            self._place_thumb(*geom)
            if index < steps:
                self._thumb_job = self.filter_canvas.after(16, lambda index=index: frame(index + 1))

        frame(1)

    def _paint_filter_button(self, key):
        meta = self._filter_buttons.get(key)
        if meta is None or not self.filter_canvas.winfo_exists():
            return
        fill, edge, fg = self._filter_colors(key)
        canvas = self.filter_canvas
        if key == ("date", "range"):
            canvas.itemconfig(meta["edge"], fill=fill, outline="")
            canvas.itemconfig(meta["fill"], fill=fill, outline="")
            canvas.itemconfig(meta["text"], fill=fg)
            return
        if key[0] == "site":
            canvas.tag_raise("site-thumb")
            canvas.tag_raise(meta["text"])
            return
        x1, y1, x2, y2 = meta["box"]
        radius = meta.get("radius", 4)
        canvas.delete(meta["edge"])
        canvas.delete(meta["fill"])
        _paint_round(canvas, x1, y1, x2, y2, radius, fill, meta["fill"])
        canvas.itemconfig(meta["text"], fill=fg)
        canvas.tag_raise(meta["text"])

    def _filter_key_at(self, x, y):
        for key, meta in self._filter_buttons.items():
            x1, y1, x2, y2 = meta["box"]
            if x1 <= x <= x2 and y1 <= y <= y2:
                return key
        return None

    def _filter_click(self, event):
        key = self._filter_key_at(event.x, event.y)
        if key is None:
            return
        kind, name = key
        if kind == "site":
            self._choose_site_tab(name)
        elif kind == "date" and name == "range":
            self._pick_range()
        elif kind == "date":
            self._pick_date(name)
        elif name == "query":
            self._apply_record_filter()
        elif name == "refresh":
            self._fill_tables()

    def _filter_motion(self, event):
        key = self._filter_key_at(event.x, event.y)
        if key == self._filter_hot:
            return
        previous = self._filter_hot
        self._filter_hot = key
        if previous is not None:
            self._paint_filter_button(previous)
        if key is not None:
            self._paint_filter_button(key)

    def _filter_leave(self, _event):
        if self._filter_hot is None:
            return
        previous = self._filter_hot
        self._filter_hot = None
        self._paint_filter_button(previous)

    def _ensure_records_page(self):
        if self.records_page is not None and self.records_page.winfo_exists():
            return
        page = tk.Frame(self.body, bg=PAGE)
        self.records_page = page
        card = tk.Frame(page, bg=SHEET, highlightthickness=1, highlightbackground="#E5E7EB", highlightcolor="#E5E7EB", takefocus=0)
        card.pack(fill="both", expand=True)
        self._build_filter(card)
        footer_bar = tk.Frame(card, bg=SHEET)
        footer_bar.pack(side="bottom", fill="x", padx=16, pady=10)
        footer_bar.grid_columnconfigure(1, weight=1)
        download = SoftButton(footer_bar, "下载勾选", self._download_current, kind="primary")
        download.grid(row=0, column=0, sticky="w", padx=(0, 4), pady=(0, 2))
        self.pager = tk.Frame(footer_bar, bg=SHEET)
        self.pager.grid(row=0, column=1)
        self.prev_button = self._pager_button(self.pager, "<", lambda: self._turn_page(-1))
        self.prev_button.pack(side="left", padx=2)
        self.page_numbers = tk.Frame(self.pager, bg=SHEET)
        self.page_numbers.pack(side="left", padx=2)
        self.next_button = self._pager_button(self.pager, ">", lambda: self._turn_page(1))
        self.next_button.pack(side="left", padx=2)
        self.page_size_box = SizeBox(footer_bar, self._on_page_size_label)
        self.page_size_box.set(f"{self.record_page_size} 条 / 页")
        self.page_size_box.grid(row=0, column=2, sticky="e")
        self.record_frame = tk.Frame(card, bg=SHEET)
        self.record_table = CheckTable(self.record_frame)
        self.record_table.pack(fill="both", expand=True)
        self.record_table.on_change = self._update_count
        self.record_table.on_country = self._choose_site_tab
        self.record_frame.pack(fill="both", expand=True)

    def _date_field(self, parent, which, caption):
        button = ctk.CTkButton(
            parent,
            text=f"{caption}  未选",
            width=156,
            height=32,
            corner_radius=8,
            border_width=0,
            fg_color=BTN_FILL,
            hover_color=BTN_FILL_HOVER,
            text_color=INK,
            font=(FONT, 12),
            command=lambda name=which: self._pick_date(name),
        )
        button._date_caption = caption
        return button

    def _pager_button(self, parent, text, command):
        button = ctk.CTkButton(
            parent,
            text=text,
            width=32,
            height=32,
            corner_radius=8,
            border_width=0,
            border_color=SHEET,
            fg_color=SHEET,
            hover_color=PAGE,
            text_color=MUTED,
            font=(FONT, 12),
            command=command,
        )
        _silence_focus(button)
        return button

    def _paint_page_button(self, button, mode):
        if not button.winfo_exists():
            return
        if mode == "current":
            button.configure(fg_color=BTN_FILL, hover_color=BTN_FILL_HOVER, text_color=INK, border_width=0, state="normal")
        elif mode == "disabled":
            button.configure(fg_color=SHEET, hover_color=SHEET, text_color=DISABLED, border_width=0, state="disabled")
        else:
            button.configure(fg_color=SHEET, hover_color=BTN_FILL, text_color=MUTED, border_width=0, state="normal")

    def _page_chip(self, parent, number):
        chip = self._pager_button(parent, str(number), lambda page=number: self._go_page(page))
        self._paint_page_button(chip, "current" if number == self.record_page else "idle")
        return chip

    def _paint_pager(self):
        if not hasattr(self, "page_numbers") or not self.page_numbers.winfo_exists():
            return
        for child in self.page_numbers.winfo_children():
            child.destroy()
        for number in _visible_pages(self.record_page, self.record_pages):
            self._page_chip(self.page_numbers, number).pack(side="left", padx=3)
        self._paint_page_button(self.prev_button, "idle" if self.record_page > 1 else "disabled")
        self._paint_page_button(self.next_button, "idle" if self.record_page < self.record_pages else "disabled")
        label = f"{self.record_page_size} 条 / 页"
        if hasattr(self, "page_size_box") and self.page_size_box.get() != label:
            self.page_size_box.set(label)

    def _on_page_size_label(self, label):
        text = str(label).split()[0]
        if text.isdigit() and int(text) != self.record_page_size:
            self._set_page_size(int(text))

    def _go_page(self, number):
        self.record_page = max(1, int(number))
        self._fill_tables()

    def _turn_page(self, step):
        target = self.record_page + int(step)
        if target < 1 or target > self.record_pages:
            return
        self.record_page = target
        self._fill_tables()

    def _set_page_size(self, size):
        self.record_page_size = int(size)
        self.record_page = 1
        self._fill_tables()

    def _toggle_filter(self):
        pop = self._filter_pop
        if pop is not None and pop.winfo_exists():
            self._close_filter()
            return
        self._open_filter()

    def _open_filter(self):
        self._close_filter()
        pop = tk.Toplevel(self.root)
        pop.overrideredirect(True)
        pop.configure(bg=SHEET, highlightthickness=1, highlightbackground=LINE)
        self._filter_pop = pop
        self.filter_button.update_idletasks()
        x = self.filter_button.winfo_rootx()
        y = self.filter_button.winfo_rooty() + self.filter_button.winfo_height() + 8
        pop.geometry(f"+{x}+{y}")
        body = tk.Frame(pop, bg=SHEET)
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg=SHEET, width=132)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Frame(body, width=1, bg=LINE).pack(side="left", fill="y")
        self._filter_options = tk.Frame(body, bg=SHEET, width=340)
        self._filter_options.pack(side="left", fill="both", expand=True, padx=4, pady=6)
        self._filter_cat_buttons = {}
        for key, label in FILTER_CATEGORIES:
            button = ctk.CTkButton(
                left,
                text=label,
                command=lambda name=key: self._choose_filter_category(name),
                anchor="w",
                width=116,
                height=36,
                corner_radius=8,
                border_width=0,
                fg_color=BTN_FILL,
                hover_color=BTN_FILL_HOVER,
                text_color=INK,
                font=(FONT, 12),
            )
            button.pack(fill="x")
            self._filter_cat_buttons[key] = button
        self._paint_filter_categories()
        self._show_filter_options()
        pop.bind("<Escape>", lambda event: self._close_filter())
        pop.lift()

    def _choose_filter_category(self, name):
        self.filter_category = name
        self._paint_filter_categories()
        self._show_filter_options()

    def _paint_filter_categories(self):
        buttons = getattr(self, "_filter_cat_buttons", None) or {}
        for key, button in buttons.items():
            if not button.winfo_exists():
                continue
            chosen = key == self.filter_category
            button.configure(
                fg_color=BTN_FILL_HOVER if chosen else BTN_FILL,
                hover_color=BTN_FILL_HOVER,
                text_color=INK,
                border_width=0,
                font=(FONT, 12, "bold" if chosen else "normal"),
            )

    def _show_filter_options(self):
        box = getattr(self, "_filter_options", None)
        if box is None or not box.winfo_exists():
            return
        for child in box.winfo_children():
            child.destroy()
        if self.filter_category == "site":
            self._site_vars = {}
            for code, name in EU_COUNTRIES:
                var = tk.BooleanVar(value=code in self.record_sites)
                self._site_vars[code] = var
                tk.Checkbutton(
                    box,
                    text=name,
                    variable=var,
                    command=lambda value=code: self._toggle_site(value),
                    bg=SHEET,
                    fg=INK,
                    activebackground=FIELD,
                    activeforeground=INK,
                    selectcolor=SHEET,
                    anchor="w",
                    font=(FONT, 12),
                    cursor="hand2",
                    padx=12,
                    pady=4,
                ).pack(fill="x", anchor="w")
            return
        if self.filter_category == "time":
            for value, label in (("all", "全部"), ("current", "本次上传"), ("range", "时间段")):
                var = tk.BooleanVar(value=self.record_span == value)
                tk.Checkbutton(
                    box,
                    text=label,
                    variable=var,
                    command=lambda mode=value: self._choose_span(mode),
                    bg=SHEET,
                    fg=INK,
                    activebackground=FIELD,
                    activeforeground=INK,
                    selectcolor=SHEET,
                    anchor="w",
                    font=(FONT, 12),
                    cursor="hand2",
                    padx=12,
                    pady=4,
                ).pack(fill="x", anchor="w")
            if self.record_span == "range":
                self._build_range_fields(box)
            return
        tk.Label(box, text="输入后回车", bg=SHEET, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=14, pady=(12, 4))
        entry = tk.Entry(
            box,
            font=(FONT, 12),
            relief="flat",
            bd=0,
            bg=FIELD,
            fg=INK,
            insertbackground=INK,
            highlightthickness=0,
        )
        entry.insert(0, self.record_query)
        entry.pack(fill="x", padx=14, pady=(0, 12), ipady=4)
        entry.bind("<Return>", lambda event: self._apply_task_query(entry.get()))
        entry.focus_set()

    def _build_range_fields(self, box):
        row = tk.Frame(box, bg=SHEET)
        row.pack(fill="x", padx=12, pady=(4, 8))
        tk.Label(row, text="从", bg=SHEET, fg=TEXT).pack(side="left")
        tk.Label(row, text=self.filter_from or "未选", bg=SHEET, fg=INK, font=(FONT, 10, "bold")).pack(side="left", padx=4)
        ctk.CTkButton(row, text="选择", width=64, height=28, corner_radius=8, border_width=0, fg_color=BTN_FILL, hover_color=BTN_FILL_HOVER, text_color=INK, command=lambda: self._pick_date("from")).pack(side="left", padx=(4, 10))
        tk.Label(row, text="到", bg=SHEET, fg=TEXT).pack(side="left")
        end = "至今" if self.filter_until_now else (self.filter_to or "未选")
        tk.Label(row, text=end, bg=SHEET, fg=INK, font=(FONT, 10, "bold")).pack(side="left", padx=4)
        ctk.CTkButton(row, text="至今", width=64, height=28, corner_radius=8, border_width=0, fg_color=BTN_FILL, hover_color=BTN_FILL_HOVER, text_color=INK, command=self._choose_until_now).pack(side="left")
        ctk.CTkButton(row, text="选择", width=64, height=28, corner_radius=8, border_width=0, fg_color=BTN_FILL, hover_color=BTN_FILL_HOVER, text_color=INK, command=lambda: self._pick_date("to")).pack(side="left", padx=4)

    def _choose_span(self, mode):
        self.record_span = mode
        self.record_page = 1
        self._paint_chips()
        self._fill_tables()
        self._show_filter_options()

    def _choose_until_now(self):
        self.record_span = "range"
        self.filter_until_now = True
        self.record_page = 1
        self._paint_chips()
        self._fill_tables()
        self._show_filter_options()

    def _choose_site_tab(self, code):
        self.record_sites.clear()
        if code != "all":
            self.record_sites.add(code)
        self.record_page = 1
        self._paint_chips()
        self._fill_tables()

    def _toggle_site(self, code):
        if code in self.record_sites:
            self.record_sites.discard(code)
        else:
            self.record_sites.add(code)
        self.record_page = 1
        self._paint_chips()
        self._fill_tables()

    def _apply_task_query(self, text):
        self.record_query = str(text or "").strip()
        self.record_page = 1
        self._paint_chips()
        self._fill_tables()

    def _apply_record_filter(self):
        entry = getattr(self, "task_entry", None)
        self.record_query = entry.get().strip() if entry is not None and entry.winfo_exists() else self.record_query
        if self.filter_from or self.filter_to:
            self.record_span = "range"
            if not self.filter_to:
                self.filter_until_now = True
        else:
            self.record_span = "all"
        self.record_page = 1
        self._paint_dates()
        self._paint_chips()
        self._fill_tables()

    def _paint_dates(self):
        canvas = getattr(self, "filter_canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return
        shell = self._filter_buttons.get(("date", "range"))
        if shell is not None:
            canvas.itemconfig(shell["text"], text=self._date_caption())
        start = self._filter_buttons.get(("date", "from"))
        if start is not None:
            canvas.itemconfig(start["text"], text=f"从  {self.filter_from or '未选'}")
        end = self._filter_buttons.get(("date", "to"))
        if end is not None:
            canvas.itemconfig(end["text"], text=f"到  {self.filter_to or '未选'}")

    def _date_caption(self):
        if not self.filter_from and not self.filter_to:
            return "全部时间"

        def short(text):
            moment = _parse_clock(text, end_of_day=False)
            if moment is None:
                return text
            return moment.strftime("%m-%d")

        start = short(self.filter_from) if self.filter_from else "…"
        end = "至今" if self.filter_until_now and not self.filter_to else short(self.filter_to or self.filter_from)
        return f"{start} 至 {end}"

    def _clear_filter(self, kind, value=""):
        if kind == "site":
            self.record_sites.discard(value)
            var = getattr(self, "_site_vars", {}).get(value)
            if var is not None:
                var.set(False)
        elif kind == "time":
            self.record_span = "all"
            self._show_filter_options()
        elif kind == "task":
            self.record_query = ""
            self._show_filter_options()
        self.record_page = 1
        self._paint_chips()
        self._fill_tables()

    def _paint_record_chrome(self):
        if getattr(self, "filter_canvas", None) is not None and self.filter_canvas.winfo_exists():
            for key in self._filter_buttons:
                self._paint_filter_button(key)
            self._sync_site_thumb()
        self._paint_dates()
        for mode, button in (getattr(self, "span_buttons", None) or {}).items():
            if not button.winfo_exists():
                continue
            on = self.record_span == mode
            button.configure(
                fg=ACCENT if on else MUTED,
                font=(FONT, 12, "bold" if on else "normal"),
            )
        entry = getattr(self, "task_entry", None)
        inner = getattr(entry, "_entry", None) or getattr(entry, "entry", entry)
        focused = None
        if inner is not None and inner.winfo_exists():
            try:
                focused = inner.focus_get()
            except tk.TclError:
                focused = None
        if entry is not None and entry.winfo_exists() and focused is not inner and entry.get() != self.record_query:
            entry.delete(0, "end")
            entry.insert(0, self.record_query)
        box = getattr(self, "range_box", None)
        if box is None or not box.winfo_exists():
            return
        if self.record_span == "range":
            self._paint_range_box()
            if not box.winfo_manager():
                box.pack(fill="x", padx=16, pady=(0, 8), before=self.record_frame)
        elif box.winfo_manager():
            box.pack_forget()

    def _paint_range_box(self):
        box = self.range_box
        for child in box.winfo_children():
            child.destroy()
        tk.Label(box, text="从", bg=SHEET, fg=MUTED, font=(FONT, 12)).pack(side="left")
        tk.Label(box, text=self.filter_from or "未选", bg=SHEET, fg=INK, font=(FONT, 12)).pack(side="left", padx=(4, 8))
        _quiet_button(box, "选择", lambda: self._pick_date("from")).pack(side="left")
        tk.Label(box, text="到", bg=SHEET, fg=MUTED, font=(FONT, 12)).pack(side="left", padx=(16, 0))
        end = "至今" if self.filter_until_now else (self.filter_to or "未选")
        tk.Label(box, text=end, bg=SHEET, fg=INK, font=(FONT, 12)).pack(side="left", padx=(4, 8))
        _quiet_button(box, "至今", self._choose_until_now).pack(side="left")
        _quiet_button(box, "选择", lambda: self._pick_date("to")).pack(side="left", padx=(8, 0))

    def _paint_chips(self):
        self._paint_record_chrome()
        if not hasattr(self, "chips") or not self.chips.winfo_exists():
            return
        for child in self.chips.winfo_children():
            child.destroy()
        chips = []
        for code, name in EU_COUNTRIES:
            if code in self.record_sites:
                chips.append((name, "site", code))
        if self.record_span == "current":
            chips.append(("本次上传", "time", ""))
        elif self.record_span == "range":
            end = "至今" if self.filter_until_now else (self.filter_to or "未选")
            start = self.filter_from or "未选"
            chips.append((f"{start} 至 {end}", "time", ""))
        if self.record_query:
            chips.append((self.record_query, "task", ""))
        for label, kind, value in chips:
            chip = tk.Frame(self.chips, bg=CHIP)
            chip.pack(side="left", padx=3)
            tk.Label(chip, text=label, bg=CHIP, fg=INK, font=(FONT, 10)).pack(side="left", padx=(10, 2), pady=4)
            ctk.CTkButton(
                chip,
                text="×",
                command=lambda item_kind=kind, item_value=value: self._clear_filter(item_kind, item_value),
                width=22,
                height=22,
                corner_radius=11,
                fg_color=CHIP,
                hover_color=PAGE,
                text_color=INK,
                border_width=0,
                font=(FONT, 12),
            ).pack(side="left", padx=(0, 4))

    def _close_filter_if_outside(self, event):
        self._close_date_if_outside(event)
        pop = self._filter_pop
        if pop is None or not pop.winfo_exists():
            return
        try:
            if event.widget.winfo_toplevel() not in (self.root, pop):
                return
        except Exception:
            return
        if _pointer_inside(pop, event.x_root, event.y_root):
            return
        if hasattr(self, "filter_button") and self.filter_button.winfo_exists() and _pointer_inside(self.filter_button, event.x_root, event.y_root):
            return
        self._close_filter()

    def _close_filter(self):
        self._close_date_pop()
        pop = self._filter_pop
        self._filter_pop = None
        if pop is not None:
            try:
                if pop.winfo_exists():
                    pop.destroy()
            except Exception:
                pass

    def _update_count(self):
        if not hasattr(self, "count_label") or not self.count_label.winfo_exists():
            return
        table = self.record_table if hasattr(self, "record_table") else None
        selected = table.selected_count() if table is not None and table.winfo_exists() else 0
        self.count_label.configure(
            text=f"筛选 {self.record_shown_count} 组 · 第 {self.record_page}/{self.record_pages} 页 · 已选 {selected} 组"
        )
        count = getattr(self, "toolbar_count", None)
        if count is not None and count.winfo_exists():
            count.configure(text=f"{self.record_shown_count} 组")
        filtered = getattr(self, "stat_filtered", None)
        if filtered is not None and filtered.winfo_exists():
            filtered.configure(text=str(self.record_shown_count))
        current = getattr(self, "stat_page", None)
        if current is not None and current.winfo_exists():
            current.configure(text=f"{self.record_page}/{self.record_pages}")
        picked = getattr(self, "stat_selected", None)
        if picked is not None and picked.winfo_exists():
            picked.configure(text=str(selected))

    def _close_date_if_outside(self, event):
        pop = getattr(self, "_date_pop", None)
        if pop is None or pop._closed or not pop.win.winfo_exists():
            return
        if self._date_guard:
            self._date_guard = False
            return
        if _pointer_inside(pop.win, event.x_root, event.y_root):
            return
        self._close_date_pop()

    def _close_date_pop(self):
        pop = getattr(self, "_date_pop", None)
        self._date_pop = None
        if pop is not None:
            pop.close()

    def _commit_range(self, start, end):
        if not start:
            self.filter_from = ""
            self.filter_to = ""
            self.filter_until_now = True
        else:
            self.filter_from = start
            self.filter_to = end or start
            self.filter_until_now = False
        self._paint_dates()

    def _pick_range(self):
        self._close_date_pop()
        moment = _parse_clock(self.filter_from, end_of_day=False) or datetime.now()
        meta = self._filter_buttons.get(("date", "range"))
        canvas = self.filter_canvas
        x1, _y1, _x2, y2 = meta["box"]
        self._date_guard = True
        self._date_pop = DatePop(
            self.root,
            int(canvas.winfo_rootx() + x1),
            int(canvas.winfo_rooty() + y2 + 6),
            moment.year,
            moment.month,
            None,
            self._commit_range,
            range_mode=True,
        )

    def _commit_date(self, which, text):
        if which == "from":
            self.filter_from = text
        else:
            self.filter_until_now = False
            self.filter_to = text
        self._paint_dates()

    def _pick_date(self, which):
        self._close_date_pop()
        current = self.filter_from if which == "from" else self.filter_to
        moment = _parse_clock(current, end_of_day=False) or datetime.now()
        meta = self._filter_buttons.get(("date", which))
        canvas = getattr(self, "filter_canvas", None)
        if meta is not None and canvas is not None and canvas.winfo_exists():
            x1, _y1, _x2, y2 = meta["box"]
            origin_x = int(canvas.winfo_rootx() + x1)
            origin_y = int(canvas.winfo_rooty() + y2 + 6)
        else:
            origin_x = int(self.root.winfo_rootx() + 40)
            origin_y = int(self.root.winfo_rooty() + 80)
        self._date_guard = True
        self._date_pop = DatePop(
            self.root,
            origin_x,
            origin_y,
            moment.year,
            moment.month,
            moment.day if current else None,
            lambda text, name=which: self._commit_date(name, text),
        )

    def _download_current(self):
        self._download_checked(
            "上传记录",
            RECORD_MERGE_HEADS + RECORD_DETAIL_HEADS,
            RECORD_MERGE + RECORD_DETAIL,
            self.record_table,
        )

    def _last_upload_path(self):
        return self.database_path.parent / "last_upload_at.txt"

    def _fill_tables(self):
        if self._shield_ctk:
            self._fill_after_resize = True
            return
        if not hasattr(self, "record_table") or not self.record_table.winfo_exists():
            return
        ok, message = _time_filter_error(self.record_span, self.filter_from, self.filter_to, self.filter_until_now)
        if not ok:
            self.footer.configure(text=message)
            return
        rows = [
            row for row in load_upload_records(self.picture_log_path, self.aplus_log_path)
            if _match_sites(row, self.record_sites)
        ]
        _attach_task_ids(rows, self.database_path)
        site_groups = group_upload_rows(rows)
        shown = _sort_groups_newest([
            group for group in site_groups
            if _group_visible(group, self.record_span, self.filter_from, self.filter_to, self.filter_until_now, self.last_upload_at, self.record_query)
        ])
        page_groups, page, pages = _paginate(shown, self.record_page, self.record_page_size)
        self.record_page = page
        self.record_pages = pages
        self.record_site_count = len(site_groups)
        self.record_shown_count = len(shown)
        if len(self.record_sites) == 1:
            site_name = EU_NAMES.get(next(iter(self.record_sites)), "")
            self.subtitle.configure(text=f"当前只看{site_name}。点「全部」恢复，点表格里的国家或站点也会筛选。")
        elif self.page == "records":
            self.subtitle.configure(text="")
        signature = (
            tuple(sorted(self.record_sites)),
            page,
            self.record_page_size,
            tuple(
                tuple(tuple(row.get(key, "") for key in RECORD_MERGE + RECORD_DETAIL) for row in group)
                for group in page_groups
            ),
        )
        if signature != self._shown_signature:
            self.record_table.show(page_groups, RECORD_MERGE, RECORD_DETAIL, RECORD_MERGE_HEADS, RECORD_DETAIL_HEADS)
            self._shown_signature = signature
        self._paint_pager()
        self._update_count()
        note = "还没有本次上传。" if self.record_span == "current" and self.last_upload_at is None else ""
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
        self._stop_spin()
        self._progress_done = 0
        self._progress_total = 0
        if getattr(self, "progress_text", None) is not None:
            self.progress_text.delete("1.0", "end")
        if getattr(self, "_bar_canvas", None) is not None:
            self._paint_bar_value()
        self._set_progress(text, 0, 0)

    def _on_progress(self, text, done=0, total=0, refresh_records=False):
        """后台线程调用。回到窗口线程再改进度，并在上传记录页刷新日志。"""
        self.root.after(
            0,
            lambda text=text, done=done, total=total, refresh_records=refresh_records: self._apply_feed(
                text, done, total, refresh_records
            ),
        )

    def _apply_feed(self, text, done, total, refresh_records=False):
        self._set_progress(text, done, total)
        if self.page == "records":
            self._fill_tables()

    def _set_progress(self, text, done, total):
        done = int(done or 0)
        total = int(total or 0)
        self._progress_done = done
        self._progress_total = total
        if getattr(self, "progress_text", None) is None:
            return
        self.progress_label.configure(text=_fit_width(" ".join(str(text or "").split()), (FONT, 10), SIDE_W - 52))
        if total <= 0:
            self._start_spin()
        else:
            self._stop_spin()
            self._paint_bar_value()
        follow = self._progress_at_end()
        self.progress_text.insert("end", str(text or "") + "\n")
        if follow:
            self.progress_text.see("end")

    def _progress_at_end(self):
        try:
            _top, bottom = self.progress_text.yview()
        except tk.TclError:
            return True
        return bottom >= 0.98

    def _progress_wheel(self, event):
        self.progress_text.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _block_progress_key(self, event):
        if event.state & 0x4 and event.keysym.lower() in {"c", "a"}:
            return None
        return "break"

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
        self._on_progress("开始读取 Excel：" + path, 0, 0)

        def work():
            try:
                report = run(
                    path,
                    self.database_path,
                    self.picture_log_path,
                    on_progress=self._on_progress,
                    aplus_log_path=self.aplus_log_path,
                )
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
            "task_id": "",
            "country": "",
            "market": "",
            "keyword": "",
            "sku": "",
            "asin": "",
            "kind": "图片",
            "file": "",
            "page_slot": "",
            "status": "",
            "reason": "",
        }
        for line in lines[1:]:
            if line.startswith("task_id：") or line.startswith("TASK_ID："):
                row["task_id"] = line.split("：", 1)[1].strip()
            elif line.startswith("sku："):
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
            elif line.startswith("错误代码："):
                row["error_code"] = line.split("：", 1)[1].strip()
            elif line.startswith("错误详情："):
                row["error_detail"] = line.split("：", 1)[1].strip()
        if not row["sku"]:
            row["sku"] = row["keyword"]
        _fill_error_fields(row, "site_images")
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
    return group_upload_rows(rows)


def group_upload_rows(rows):
    """图片和 A+ 按时间排好后，同一次上传合成一组。"""
    ordered = sorted(rows, key=_chrono_key)
    groups = []
    for row in ordered:
        if groups and _same_upload(groups[-1][-1], row):
            groups[-1].append(row)
        else:
            groups.append([row])
    return groups


def _chrono_key(row):
    moment = _parse_clock(row.get("time"), end_of_day=False)
    return (moment is None, moment or datetime.min)


def _same_upload(previous, row):
    if (previous.get("sku") or "") != (row.get("sku") or ""):
        return False
    previous_asin = str(previous.get("asin") or "").strip()
    row_asin = str(row.get("asin") or "").strip()
    if previous_asin and row_asin and previous_asin != row_asin:
        return False
    if _site_key(previous) != _site_key(row):
        return False
    previous_id = str(previous.get("task_id") or "").strip()
    row_id = str(row.get("task_id") or "").strip()
    if previous_id and row_id and previous_id != row_id:
        return False
    start = _parse_clock(previous.get("time"), end_of_day=False)
    end = _parse_clock(row.get("time"), end_of_day=False)
    if start is None or end is None:
        return False
    return 0 <= (end - start).total_seconds() <= 3 * 60


def _paginate(groups, page, size):
    """按组切页。页码超出时收到最后一页。没有记录时仍是第 1 页。"""
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 10
    if size not in PAGE_SIZES:
        size = 10
    total = len(groups)
    pages = max(1, (total + size - 1) // size) if total else 1
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = min(max(1, page), pages)
    start = (page - 1) * size
    return groups[start:start + size], page, pages


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
        row = {
            "time": lines[0].strip() if lines else "",
            "task_id": "",
            "sku": "",
            "asin": "",
            "country": "",
            "market": "",
            "kind": "A+",
            "file": "描述",
            "page_slot": "",
            "status": "",
            "reason": "",
        }
        for line in lines[1:]:
            if "：" not in line:
                continue
            key, value = line.split("：", 1)
            field = {
                "task_id": "task_id",
                "TASK_ID": "task_id",
                "sku": "sku",
                "asin": "asin",
                "country": "country",
                "market": "market",
                "结果": "status",
                "原因": "reason",
                "错误代码": "error_code",
                "错误详情": "error_detail",
            }.get(key.strip())
            if field:
                row[field] = value.strip()
        _fill_error_fields(row, "aplus")
        rows.append(row)
    return rows


def load_upload_records(picture_path, aplus_path):
    """图片日志和 A+ 日志合成同一份记录。"""
    return load_picture_records(picture_path) + load_aplus_records(aplus_path)


def _attach_task_ids(rows, database_path):
    """旧日志没有 task_id 时，本机任务能唯一对上才补上。"""
    index = _task_index(database_path)
    for row in rows:
        if str(row.get("task_id") or "").strip():
            continue
        sku = str(row.get("sku") or "")
        asin = str(row.get("asin") or "")
        site = _site_key(row)
        if asin:
            matches = index.get((sku, asin, site))
        else:
            matches = set()
            for (item_sku, _item_asin, item_site), ids in index.items():
                if item_sku == sku and item_site == site:
                    matches.update(ids)
        if matches and len(matches) == 1:
            row["task_id"] = next(iter(matches))


def _task_index(database_path):
    path = Path(database_path)
    if not path.is_file():
        return {}
    try:
        connection = sqlite3.connect(path)
        try:
            found = connection.execute(
                "SELECT task_id, sku, asin, main_country, cross_country FROM task"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return {}
    index = {}
    for task_id, sku, asin, main_country, cross_country in found:
        text = str(task_id or "").strip()
        if not text:
            continue
        site = _as_code(cross_country) or _as_code(main_country)
        key = (str(sku or ""), str(asin or ""), site)
        index.setdefault(key, set()).add(text)
    return index


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
        elif part.startswith("跳过"):
            status, reason = "跳过", _skip_detail(part)
        elif part:
            status, reason = "失败", part
        else:
            status, reason = "失败", str(item.get("reason") or item.get("status") or "")
        blocks.append("\n".join([
            f"【记录时间】：{now}",
            f"task_id：{row.get('task_id', '')}",
            f"sku：{row.get('sku', '')}",
            f"asin：{row.get('asin', '')}",
            f"country：{row.get('country', '')}",
            f"market：{row.get('market', '')}",
            f"结果：{status}",
            f"原因：{reason}",
            f"错误代码：{_error_code_for(status, reason, 'aplus')}",
            f"错误详情：{reason}",
            "",
        ]))
    if not blocks:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(blocks) + "\n")


def _error_code_for(status, detail, action):
    text = str(detail or "").strip()
    if status != "失败" or not text:
        return ""
    from app.task_run import _failure_code

    return _failure_code(Exception(text), action)[0]


def _fill_error_fields(row, action):
    """旧日志没有这两列时，用原因补上错误详情，失败行再归出错误代码。"""
    detail = str(row.get("error_detail") or "").strip()
    if not detail:
        detail = str(row.get("reason") or "").strip()
        row["error_detail"] = detail
    code = str(row.get("error_code") or "").strip()
    if not code:
        code = _error_code_for(row.get("status"), detail, action)
    row["error_code"] = code


def _description_part(reason):
    for part in str(reason or "").split("；"):
        text = part.strip()
        if text.startswith("描述："):
            return text.split("：", 1)[1].strip()
    return ""


def _skip_detail(part):
    """「跳过（原因）」只把括号里的说明写入记录。"""
    detail = str(part or "")[2:].strip()
    if detail.startswith("（") and detail.endswith("）"):
        detail = detail[1:-1].strip()
    return detail


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
    return text in str(row.get("task_id") or "").lower()


def _row_visible(row, span, start_text, end_text, until_now, last_upload, query):
    return _match_time(row, span, start_text, end_text, until_now, last_upload) and _match_query(row, query)


def _group_visible(group, span, start_text, end_text, until_now, last_upload, query):
    head = group[0] if group else {}
    if not _match_time(head, span, start_text, end_text, until_now, last_upload):
        return False
    return any(_match_query(row, query) for row in group)


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
    return _match_sites(row, {selected})


def _match_sites(row, sites):
    """国家和站点只要有一个对上就留下。以前只认 market，国家点了表不会变。"""
    if not sites:
        return True
    codes = {code for code in (_as_code(row.get("market")), _as_code(row.get("country"))) if code}
    if codes & set(sites):
        return True
    raw = {str(row.get("market") or "").strip(), str(row.get("country") or "").strip()}
    names = {EU_NAMES.get(code, code) for code in sites}
    return bool(raw & (set(sites) | names))


def _pointer_inside(widget, x, y):
    try:
        left = widget.winfo_rootx()
        top = widget.winfo_rooty()
    except tk.TclError:
        return False
    return left <= x < left + widget.winfo_width() and top <= y < top + widget.winfo_height()


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
