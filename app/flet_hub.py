"""用 Flet 画账号管理和上传记录。筛选、日志和上传仍走原来的函数。"""

from datetime import datetime
from pathlib import Path
import asyncio
import calendar
import csv
import io

import flet as ft

from app.browser import COUNTRY_PORTS, launch_chrome
from app.config_loader import bundle_dir
from app.file_pick import explain_read_failure
from app.hub_ui import (
    EU_COUNTRIES,
    EU_NAMES,
    RECORD_DETAIL,
    RECORD_DETAIL_HEADS,
    RECORD_MERGE,
    RECORD_MERGE_HEADS,
    _as_code,
    _attach_task_ids,
    _clip_text,
    _group_visible,
    _load_last_upload,
    _match_sites,
    _paginate,
    _parse_clock,
    _pill_colors,
    _save_last_upload,
    _short_clock,
    _sort_groups_newest,
    _time_filter_error,
    append_aplus_log,
    group_upload_rows,
    load_upload_records,
    read_login_state,
)
from app.pipeline import run
from app.progress_feed import set_listener

PAGE_BG = "#F8F9FA"
SHEET = "#FFFFFF"
INK = "#111111"
MUTED = "#6B7280"
LINE = "#E5E7EB"
SIDE = "#000000"
SIDE_HOVER = "#1D1D1F"
SIDE_TEXT = "#A3A3A3"
SIDE_RED = "#E10600"
TAB_TRACK = "#F3F4F6"
TAB_TEXT = "#4B5563"
TAB_ON = "#1F2937"
TAB_ON_TEXT = "#FFFFFF"
BTN_FILL = "#F3F4F6"
BTN_FILL_HOVER = "#E5E7EB"
CAL_BG = "#F9FAFB"
FIELD_H = 40
FIELD_R = 6
DAY = 32
MONTH_W = DAY * 7
WEEKDAY = "#9CA3AF"
FONT = "Microsoft YaHei UI"
SLIDE = ft.Animation(180, ft.AnimationCurve.EASE_OUT)
COLUMN_FLEX = {
    "time": 16,
    "task_id": 14,
    "country": 8,
    "market": 8,
    "sku": 16,
    "asin": 12,
    "kind": 10,
    "file": 12,
    "page_slot": 10,
    "status": 8,
    "reason": 16,
    "error_code": 12,
    "error_detail": 22,
}
HEADS = RECORD_MERGE_HEADS + RECORD_DETAIL_HEADS
KEYS = RECORD_MERGE + RECORD_DETAIL


def _hot(data):
    return data is True or str(data).lower() == "true"


def _page_size_dropdown(value, on_select):
    """看起来就是一行字：10 条/页 ⌄。菜单略透，浮在表格上。"""
    label = ft.Text(f"{value} 条/页", size=13, color=INK, font_family=FONT)
    quiet = {
        ft.ControlState.DEFAULT: SHEET,
        ft.ControlState.HOVERED: SHEET,
        ft.ControlState.FOCUSED: SHEET,
        ft.ControlState.PRESSED: SHEET,
    }

    def choose(size):
        def handler(e):
            label.value = f"{size} 条/页"
            on_select(size)

        return handler

    return ft.PopupMenuButton(
        content=ft.Row(
            spacing=4,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                label,
                ft.Icon(ft.CupertinoIcons.CHEVRON_DOWN, size=11, color=MUTED),
            ],
        ),
        padding=0,
        elevation=6,
        shadow_color="#33000000",
        bgcolor=ft.Colors.with_opacity(0.9, SHEET),
        menu_position=ft.PopupMenuPosition.OVER,
        shape=ft.RoundedRectangleBorder(radius=10),
        menu_padding=ft.Padding.symmetric(vertical=4),
        style=ft.ButtonStyle(
            bgcolor=quiet,
            overlay_color=ft.Colors.TRANSPARENT,
            elevation=0,
            padding=ft.Padding.all(0),
            shadow_color=ft.Colors.TRANSPARENT,
            side=ft.BorderSide(0, SHEET),
            visual_density=ft.VisualDensity.COMPACT,
        ),
        items=[
            ft.PopupMenuItem(
                content=ft.Text(f"{size} 条/页", size=13, color=INK, font_family=FONT),
                height=36,
                on_click=choose(size),
            )
            for size in (10, 20, 50)
        ],
    )


def _check_box(value, on_change):
    """白底，选中是红色勾。"""
    return ft.Checkbox(
        value=value,
        on_change=on_change,
        check_color=SIDE_RED,
        active_color=SHEET,
        fill_color={
            ft.ControlState.SELECTED: SHEET,
            ft.ControlState.DEFAULT: SHEET,
            ft.ControlState.HOVERED: SHEET,
        },
        border_side={
            ft.ControlState.SELECTED: ft.BorderSide(1.5, SIDE_RED),
            ft.ControlState.DEFAULT: ft.BorderSide(1, "#D1D5DB"),
            ft.ControlState.HOVERED: ft.BorderSide(1, "#D1D5DB"),
        },
        shape=ft.RoundedRectangleBorder(radius=4),
    )


def _soft_button(text, on_click, width=None, height=40):
    button = ft.Button(
        content=text,
        width=width,
        height=height,
        elevation=0,
        bgcolor=BTN_FILL,
        color=INK,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=8),
            elevation=0,
            padding=ft.Padding(left=14, right=14, top=0, bottom=0),
            bgcolor=BTN_FILL,
            color=INK,
        ),
        on_click=on_click,
        on_hover=lambda e: _paint_button(e.control, _hot(e.data)),
    )
    return button


def _paint_button(button, hot):
    color = BTN_FILL_HOVER if hot else BTN_FILL
    button.bgcolor = color
    if button.style is not None:
        button.style.bgcolor = color
    if button.page is not None:
        button.update()


_GROW = ft.Animation(520, ft.AnimationCurve.EASE_IN_OUT_CUBIC)
_FLOW = ft.Animation(1100, ft.AnimationCurve.EASE_IN_OUT)
_PILL = 36
_PILL_OPEN = 86


def _search_button(on_click):
    """浅灰胶囊。收起只包住放大镜，展开刚好包住「查询」，里面有一条高光流过。"""
    label = ft.Text(
        "查询",
        size=14,
        color="#374151",
        font_family=FONT,
        no_wrap=True,
        opacity=0,
        animate_opacity=_GROW,
    )
    slot = ft.Container(
        width=0,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        animate=_GROW,
        alignment=ft.Alignment.CENTER_LEFT,
        padding=ft.Padding(left=6, top=0, right=0, bottom=0),
        content=label,
    )
    sheen = ft.Container(
        width=34,
        height=_PILL,
        left=-40,
        top=0,
        visible=False,
        border_radius=18,
        bgcolor=ft.Colors.with_opacity(0.95, "#FFFFFF"),
        animate_position=_FLOW,
    )
    layer = ft.Stack(
        width=_PILL,
        height=_PILL,
        animate_size=_GROW,
        controls=[
            sheen,
            ft.Container(
                height=_PILL,
                alignment=ft.Alignment.CENTER,
                padding=ft.Padding(left=10, top=0, right=10, bottom=0),
                content=ft.Row(
                    spacing=0,
                    alignment=ft.MainAxisAlignment.CENTER,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Icon(ft.Icons.SEARCH, size=16, color="#374151"),
                        slot,
                    ],
                ),
            ),
        ],
    )
    face = ft.Container(
        width=_PILL,
        height=_PILL,
        bgcolor=BTN_FILL,
        border_radius=18,
        alignment=ft.Alignment.CENTER,
        animate=_GROW,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        on_click=on_click,
        content=layer,
    )

    state = {"hot": False, "running": False}

    def ready(control):
        try:
            return control.page is not None
        except RuntimeError:
            return False

    async def sweep():
        if state["running"]:
            return
        state["running"] = True
        try:
            while state["hot"] and ready(sheen):
                sheen.visible = True
                sheen.left = -40
                sheen.update()
                await asyncio.sleep(0.05)
                if not state["hot"]:
                    break
                sheen.left = _PILL_OPEN + 8
                sheen.update()
                await asyncio.sleep(1.15)
        finally:
            state["running"] = False
            sheen.visible = False
            sheen.left = -40
            if ready(sheen):
                sheen.update()

    def hover(e):
        hot = _hot(e.data)
        state["hot"] = hot
        width = _PILL_OPEN if hot else _PILL
        face.width = width
        layer.width = width
        slot.width = 46 if hot else 0
        label.opacity = 1 if hot else 0
        if not hot:
            sheen.visible = False
            sheen.left = -40
        if ready(face):
            face.update()
            if hot and not state["running"]:
                face.page.run_task(sweep)

    face.on_hover = hover
    return face


def _refresh_button(on_click):
    """无字的浅色方块，只放一个深灰循环箭头。"""
    button = ft.Container(
        width=FIELD_H,
        height=FIELD_H,
        bgcolor=SHEET,
        border_radius=FIELD_R,
        alignment=ft.Alignment.CENTER,
        tooltip=ft.Tooltip(
            message="刷新",
            bgcolor="#111827",
            text_style=ft.TextStyle(size=12, color=SHEET),
            padding=ft.Padding(left=10, top=8, right=10, bottom=8),
        ),
        on_click=on_click,
        content=ft.Icon(ft.Icons.REFRESH, size=18, color="#4B5563"),
    )

    def hover(e):
        button.bgcolor = BTN_FILL if _hot(e.data) else SHEET
        try:
            mounted = button.page is not None
        except RuntimeError:
            mounted = False
        if mounted:
            button.update()

    button.on_hover = hover
    return button


def _field_border():
    side = ft.BorderSide(1, LINE)
    return {
        ft.ControlState.DEFAULT: ft.OutlineInputBorder(border_radius=FIELD_R, side=side),
        ft.ControlState.FOCUSED: ft.OutlineInputBorder(border_radius=FIELD_R, side=side),
    }


class SiteTabs(ft.Stack):
    """浅灰槽里一块深色滑块。点国家时滑块移过去，字跟着变白。"""

    def __init__(self, on_select):
        self._on_select = on_select
        self.codes = ["all"] + [code for code, _name in EU_COUNTRIES]
        self.names = ["全部"] + [name for _code, name in EU_COUNTRIES]
        self.widths = [max(56, 16 * len(name) + 28) for name in self.names]
        self._labels = []
        tabs = []
        for index, name in enumerate(self.names):
            label = ft.Text(name, size=14, color=TAB_ON_TEXT if index == 0 else TAB_TEXT, text_align=ft.TextAlign.CENTER)
            box = ft.Container(
                width=self.widths[index],
                height=32,
                alignment=ft.Alignment.CENTER,
                content=label,
                on_click=lambda e, code=self.codes[index]: self._on_select(code),
            )
            self._labels.append(label)
            tabs.append(box)
        track_w = 8 + sum(self.widths)
        self.thumb = ft.Container(
            bgcolor=TAB_ON,
            border_radius=16,
            top=4,
            left=4,
            width=self.widths[0],
            height=32,
            animate_position=SLIDE,
            animate_size=SLIDE,
        )
        super().__init__(
            width=track_w,
            height=40,
            controls=[
                ft.Container(width=track_w, height=40, bgcolor=TAB_TRACK, border_radius=20),
                self.thumb,
                ft.Row(controls=tabs, spacing=0, left=4, top=4),
            ],
        )

    def move(self, code):
        index = self.codes.index(code if code in self.codes else "all")
        self.thumb.left = 4 + sum(self.widths[:index])
        self.thumb.width = self.widths[index]
        for item, label in zip(self.codes, self._labels):
            label.color = TAB_ON_TEXT if item == self.codes[index] else TAB_TEXT


def _date_key(moment):
    if moment is None:
        return None
    return (moment.year, moment.month, moment.day)


def _add_month(year, month, step):
    month += step
    while month < 1:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return year, month


class DatePanel:
    """左右两个月各自选年、选月。改一侧不会带动另一侧。"""

    def __init__(self, year, month, on_done, on_close=None, start=None, end=None):
        self.left_year = int(year)
        self.left_month = int(month)
        if end is not None and (end.year, end.month) != (self.left_year, self.left_month):
            self.right_year = end.year
            self.right_month = end.month
        else:
            self.right_year, self.right_month = _add_month(self.left_year, self.left_month, 1)
        self.left_page = (self.left_year // 12) * 12
        self.right_page = (self.right_year // 12) * 12
        self.open_side = None
        self.open_part = None
        self.anchor = _date_key(start)
        self.end = _date_key(end)
        if self.anchor and self.end and self.end < self.anchor:
            self.anchor, self.end = self.end, self.anchor
        self.on_done = on_done
        self.on_close = on_close or (lambda: None)
        self.body = ft.Column(spacing=0, tight=True)
        self.control = ft.Container(
            width=20 + MONTH_W + 72 + MONTH_W + 20,
            bgcolor=SHEET,
            border=ft.Border.all(1, LINE),
            border_radius=12,
            shadow=ft.BoxShadow(blur_radius=18, spread_radius=0, color="#1A000000", offset=ft.Offset(0, 6)),
            padding=ft.Padding(left=20, top=20, right=20, bottom=20),
            content=self.body,
        )
        self.redraw()

    def redraw(self):
        clear = ft.Container(
            height=36,
            alignment=ft.Alignment.CENTER,
            border_radius=FIELD_R,
            content=ft.Text("全部时间", size=13, color=INK, font_family=FONT),
            on_hover=self._hover_clear,
            on_click=lambda e: self.on_done(None, None),
        )
        self.body.controls = [
            self._two_months(),
            ft.Container(height=20),
            clear,
        ]
        try:
            mounted = self.control.page is not None
        except RuntimeError:
            mounted = False
        if mounted:
            self.control.update()

    def _stamp(self, key):
        if key is None:
            return "选择"
        return f"{key[1]:02d}-{key[2]:02d}"

    def _range_column(self):
        pick_start = self.anchor is None
        pick_end = self.anchor is not None and self.end is None
        close = ft.Container(
            width=28,
            height=28,
            alignment=ft.Alignment.CENTER,
            border_radius=8,
            tooltip="关闭",
            content=ft.Text("×", size=18, color=MUTED),
            on_click=lambda e: self.on_close(),
            on_hover=lambda e: self._hover_chip(e),
        )
        return ft.Container(
            width=72,
            content=ft.Column(
                spacing=8,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    close,
                    self._head("开始", self.anchor, pick_start),
                    self._head("结束", self.end, pick_end),
                ],
            ),
        )

    def _head(self, title, key, picking):
        return ft.Column(
            spacing=2,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text(title, size=12, color=MUTED, font_family=FONT, text_align=ft.TextAlign.CENTER),
                ft.Text(self._stamp(key), size=14, color=INK if key else MUTED, font_family=FONT, text_align=ft.TextAlign.CENTER),
                ft.Container(width=28, height=2, bgcolor=SIDE_RED if picking else ft.Colors.TRANSPARENT),
            ],
        )

    def _hover_clear(self, e):
        e.control.bgcolor = "#E5E7EB" if _hot(e.data) else None
        e.control.update()

    def _header(self, title, on_title, on_prev, on_next):
        return ft.Row(
            width=MONTH_W,
            spacing=0,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                self._arrow("‹", on_prev),
                ft.Container(content=ft.Text(title, size=13, color=INK, font_family=FONT), on_click=on_title),
                self._arrow("›", on_next),
            ],
        )

    def _arrow(self, mark, handler):
        return ft.Container(
            width=22,
            height=28,
            alignment=ft.Alignment.CENTER,
            border_radius=6,
            content=ft.Text(mark, size=16, color="#4B5563"),
            on_click=handler,
            on_hover=lambda e: self._hover_chip(e),
        )

    def _year_arrow(self, forward, handler):
        mark = "›" if forward else "‹"
        first = 0 if forward else 6
        second = 6 if forward else 0
        return ft.Container(
            width=22,
            height=28,
            alignment=ft.Alignment.CENTER,
            border_radius=6,
            content=ft.Stack(
                width=16,
                height=16,
                controls=[
                    ft.Text(mark, size=15, color="#4B5563", left=first, top=-2),
                    ft.Text(mark, size=15, color="#4B5563", left=second, top=-2),
                ],
            ),
            on_click=handler,
            on_hover=lambda e: self._hover_chip(e),
        )

    def _hover_chip(self, e):
        e.control.bgcolor = "#E5E7EB" if _hot(e.data) else None
        e.control.update()

    def _cell(self, label, chosen, handler, width=54, height=30):
        border = ft.Border.all(2, SIDE_RED) if chosen else None
        box = ft.Container(
            width=width,
            height=height,
            alignment=ft.Alignment.CENTER,
            bgcolor=SHEET,
            border=border,
            border_radius=6,
            content=ft.Text(label, size=12, color=INK, font_family=FONT),
            on_click=handler,
            on_hover=lambda e, picked=chosen: self._hover_cell(e, picked),
        )
        return box

    def _hover_day(self, e, chosen, between):
        if chosen:
            return
        if _hot(e.data) or between:
            e.control.bgcolor = "#F3F4F6"
        else:
            e.control.bgcolor = ft.Colors.TRANSPARENT
        e.control.update()

    def _hover_cell(self, e, chosen):
        if chosen:
            return
        e.control.bgcolor = "#E5E7EB" if _hot(e.data) else SHEET
        e.control.update()

    def _ym(self, side):
        if side == "left":
            return self.left_year, self.left_month
        return self.right_year, self.right_month

    def _set_ym(self, side, year, month):
        if side == "left":
            self.left_year, self.left_month = year, month
        else:
            self.right_year, self.right_month = year, month

    def _toggle_part(self, side, part):
        if self.open_side == side and self.open_part == part:
            self.open_side = None
            self.open_part = None
        else:
            self.open_side = side
            self.open_part = part
            if part == "year":
                year, _month = self._ym(side)
                page = (year // 12) * 12
                if side == "left":
                    self.left_page = page
                else:
                    self.right_page = page
        self.redraw()

    def _shift_side(self, side, step):
        year, month = _add_month(*self._ym(side), step)
        self._set_ym(side, year, month)
        self.redraw()

    def _shift_year(self, side, step):
        year, month = self._ym(side)
        year += step
        self._set_ym(side, year, month)
        page = (year // 12) * 12
        if side == "left":
            self.left_page = page
        else:
            self.right_page = page
        self.redraw()

    def _choose_year(self, side, year):
        _current, month = self._ym(side)
        self._set_ym(side, year, month)
        self.open_side = None
        self.open_part = None
        self.redraw()

    def _choose_month(self, side, month):
        year, _current = self._ym(side)
        self._set_ym(side, year, month)
        self.open_side = None
        self.open_part = None
        self.redraw()

    def _shift_page(self, side, step):
        if side == "left":
            self.left_page += 12 * step
        else:
            self.right_page += 12 * step
        self.redraw()

    def _part_button(self, side, part, label):
        opened = self.open_side == side and self.open_part == part
        return ft.Container(
            height=28,
            padding=ft.Padding(left=2, top=0, right=2, bottom=0),
            alignment=ft.Alignment.CENTER,
            border=ft.Border(bottom=ft.BorderSide(2, SIDE_RED if opened else ft.Colors.TRANSPARENT)),
            content=ft.Text(label, size=14, color=INK, font_family=FONT, no_wrap=True),
            on_click=lambda e, picked_side=side, picked_part=part: self._toggle_part(picked_side, picked_part),
        )

    def _year_grid(self, side):
        page = self.left_page if side == "left" else self.right_page
        current, _month = self._ym(side)
        cells = [
            self._cell(str(year), year == current, lambda e, picked_side=side, value=year: self._choose_year(picked_side, value), width=54, height=30)
            for year in range(page, page + 12)
        ]
        return ft.Column(
            width=MONTH_W,
            spacing=2,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                self._header(f"{page} - {page + 11}", lambda e: None, lambda e, picked_side=side: self._shift_page(picked_side, -1), lambda e, picked_side=side: self._shift_page(picked_side, 1)),
                self._wrap(cells, 4, gap=2),
            ],
        )

    def _month_grid(self, side):
        _year, current = self._ym(side)
        cells = [
            self._cell(f"{month}月", month == current, lambda e, picked_side=side, value=month: self._choose_month(picked_side, value), width=54, height=30)
            for month in range(1, 13)
        ]
        return self._wrap(cells, 4, gap=2)

    def _two_months(self):
        return ft.Row(
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.START,
            controls=[
                self._side("left"),
                self._range_column(),
                self._side("right"),
            ],
        )

    def _side(self, side):
        year, month = self._ym(side)
        if self.open_side == side and self.open_part == "year":
            body = self._year_grid(side)
        elif self.open_side == side and self.open_part == "month":
            body = self._month_grid(side)
        else:
            body = self._days(year, month)
        return ft.Column(
            width=MONTH_W,
            spacing=4,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    width=MONTH_W,
                    spacing=0,
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        self._year_arrow(False, lambda e, picked_side=side: self._shift_year(picked_side, -1)),
                        self._arrow("‹", lambda e, picked_side=side: self._shift_side(picked_side, -1)),
                        ft.Row(
                            spacing=2,
                            tight=True,
                            controls=[
                                self._part_button(side, "year", f"{year}年"),
                                self._part_button(side, "month", f"{month}月"),
                            ],
                        ),
                        self._arrow("›", lambda e, picked_side=side: self._shift_side(picked_side, 1)),
                        self._year_arrow(True, lambda e, picked_side=side: self._shift_year(picked_side, 1)),
                    ],
                ),
                body,
            ],
        )

    def _days(self, year, month):
        names = [
            ft.Container(
                width=DAY,
                height=24,
                alignment=ft.Alignment.CENTER,
                content=ft.Text(name, size=12, color=WEEKDAY, font_family=FONT, text_align=ft.TextAlign.CENTER),
            )
            for name in "一二三四五六日"
        ]
        weekday, count = calendar.monthrange(year, month)
        cells = [ft.Container(width=DAY, height=DAY) for _ in range(weekday)]
        for day in range(1, count + 1):
            cells.append(self._day_cell(year, month, day))
        return ft.Column(
            width=MONTH_W,
            spacing=4,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(width=MONTH_W, spacing=0, alignment=ft.MainAxisAlignment.CENTER, controls=names),
                self._wrap(cells, 7, gap=0),
            ],
        )

    def _day_cell(self, year, month, day):
        key = (year, month, day)
        chosen = False
        between = False
        if self.anchor is not None and self.end is None:
            chosen = key == self.anchor
        elif self.anchor is not None and self.end is not None:
            low, high = sorted((self.anchor, self.end))
            chosen = key in {low, high}
            between = low < key < high
        if chosen:
            bg, border = SHEET, ft.Border.all(2, SIDE_RED)
        elif between:
            bg, border = "#F3F4F6", None
        else:
            bg, border = ft.Colors.TRANSPARENT, None
        return ft.Container(
            width=DAY,
            height=DAY,
            alignment=ft.Alignment.CENTER,
            bgcolor=bg,
            border=border,
            border_radius=6,
            content=ft.Text(str(day), size=13, color=INK, font_family=FONT, text_align=ft.TextAlign.CENTER),
            on_click=lambda e, y=year, m=month, value=day: self._pick_day(y, m, value),
            on_hover=lambda e, picked=chosen, inside=between: self._hover_day(e, picked, inside),
        )

    def _pick_day(self, year, month, day):
        picked = (year, month, day)
        if self.anchor is None or self.end is not None:
            self.anchor = picked
            self.end = None
            self.redraw()
            return
        self.end = picked
        if self.end < self.anchor:
            self.anchor, self.end = self.end, self.anchor
        start, end = self.anchor, self.end
        self.on_done(f"{start[0]:04d}-{start[1]:02d}-{start[2]:02d}", f"{end[0]:04d}-{end[1]:02d}-{end[2]:02d}")

    def _wrap(self, cells, columns, gap=8):
        rows = []
        for start in range(0, len(cells), columns):
            rows.append(ft.Row(spacing=gap, controls=cells[start : start + columns]))
        return ft.Column(spacing=gap, tight=True, controls=rows)


class HubWindow:
    """Flet 窗口。构造时只记下路径，run 才打开界面。"""

    def __init__(self, database_path, picture_log_path, aplus_log_path, progress_note=""):
        self.database_path = Path(database_path)
        self.picture_log_path = Path(picture_log_path)
        self.aplus_log_path = Path(aplus_log_path)
        self.progress_note = progress_note or ""
        self.page = None
        self.page_name = "accounts"
        self.record_sites = set()
        self.record_span = "all"
        self.filter_from = ""
        self.filter_to = ""
        self.filter_until_now = True
        self.record_query = ""
        self.record_page = 1
        self.record_page_size = 10
        self.record_pages = 1
        self.record_shown_count = 0
        self.last_upload_at = _load_last_upload(self.database_path.parent / "last_upload_at.txt")
        self.launching = False
        self.uploading = False
        self._login_gen = 0
        self._login_cache = {}
        self._page_groups = []
        self._checked = []
        self._pending = []
        self._status = {}
        set_listener(self._on_progress)

    def run(self):
        ft.run(self._main, assets_dir=str(bundle_dir() / "assets"))

    def _main(self, page: ft.Page):
        self.page = page
        page.title = "Aosom 图片与 A+"
        page.theme = ft.Theme(font_family=FONT)
        page.bgcolor = PAGE_BG
        page.padding = 0
        page.window.width = 1180
        page.window.height = 720
        page.window.min_width = 980
        page.window.min_height = 560
        self._build(page)
        self._show_accounts()
        if self.progress_note:
            self._append_log(self.progress_note, 0, 1)
        for item in self._pending:
            self._append_log(*item)
        self._pending.clear()
        page.update()
        page.run_task(self._watch_login)

    def _build(self, page):
        self.nav = {}
        logo = bundle_dir() / "assets" / "aosom_logo.png"
        logo_box = ft.Image(src=str(logo), width=168, height=36, fit=ft.BoxFit.CONTAIN) if logo.is_file() else ft.Text("Aosom", color=SHEET, size=18)
        nav_rows = []
        self._nav_order = ("accounts", "records", "excel")
        for key, label in (("accounts", "账号管理"), ("records", "上传记录"), ("excel", "选择 Excel")):
            caption = ft.Text(label, size=14, color=SIDE_TEXT, font_family=FONT)
            row = ft.Container(
                height=48,
                bgcolor=SIDE,
                alignment=ft.Alignment.CENTER_LEFT,
                on_click=lambda e, name=key: self._nav(name),
                on_hover=lambda e, name=key: self._nav_hover(name, _hot(e.data)),
                content=ft.Container(padding=ft.Padding(left=20, top=0, right=12, bottom=0), content=caption),
            )
            self.nav[key] = {"row": row, "text": caption}
            nav_rows.append(row)
        self.nav_mark = ft.Container(
            width=3,
            height=48,
            left=0,
            top=0,
            bgcolor=SIDE_RED,
            animate_position=SLIDE,
            content=ft.Container(width=3, height=48, bgcolor=SIDE_RED),
        )
        nav_column = ft.Stack(
            height=48 * len(nav_rows),
            controls=[ft.Column(spacing=0, tight=True, controls=nav_rows), self.nav_mark],
        )
        self.progress_label = ft.Text("尚未开始", size=11, color=SIDE_TEXT)
        self.progress_bar = ft.ProgressBar(value=0, color=SHEET, bgcolor="#333333", bar_height=6)
        self.progress_log = ft.ListView(height=130, spacing=2, auto_scroll=True)
        side = ft.Container(
            width=220,
            bgcolor=SIDE,
            padding=ft.Padding(left=0, top=20, right=0, bottom=16),
            content=ft.Column(
                expand=True,
                controls=[
                    ft.Container(padding=ft.Padding(left=16, top=0, right=16, bottom=0), content=logo_box),
                    ft.Container(height=16),
                    nav_column,
                    ft.Container(expand=True),
                    ft.Container(
                        padding=ft.Padding(left=16, top=0, right=16, bottom=0),
                        content=ft.Column(
                            spacing=6,
                            controls=[
                                ft.Text("进度", size=13, color=SHEET, font_family=FONT),
                                self.progress_label,
                                self.progress_bar,
                                self.progress_log,
                            ],
                        ),
                    ),
                ],
            ),
        )
        self.title = ft.Text("账号管理", size=20, color=INK, font_family=FONT)
        self.subtitle = ft.Text("", size=14, color=MUTED, font_family=FONT)
        self.footer = ft.Text("", size=12, color=MUTED)
        self.header_actions = ft.Row(spacing=8)
        self.body = ft.Column(expand=True, spacing=0)
        main = ft.Container(
            expand=True,
            bgcolor=PAGE_BG,
            padding=ft.Padding(left=20, top=18, right=20, bottom=12),
            content=ft.Column(
                expand=True,
                spacing=8,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[self.title, self.header_actions],
                    ),
                    self.subtitle,
                    self.body,
                    self.footer,
                ],
            ),
        )
        page.add(ft.Row(expand=True, spacing=0, controls=[side, main]))
        self._paint_nav()

    def _nav(self, name):
        if name == "excel":
            self.page.run_task(self._choose_excel)
            return
        if name == "accounts":
            self._show_accounts()
        else:
            self._show_records()
        self.page.update()

    def _nav_hover(self, name, hot):
        if name == self.page_name:
            return
        row = self.nav[name]["row"]
        row.bgcolor = SIDE_HOVER if hot else SIDE
        row.update()

    def _paint_nav(self):
        for key, parts in self.nav.items():
            on = key == self.page_name
            parts["row"].bgcolor = SIDE_HOVER if on else SIDE
            parts["text"].color = SHEET if on else SIDE_TEXT
            parts["text"].weight = ft.FontWeight.NORMAL
        if self.page_name in self._nav_order:
            self.nav_mark.top = self._nav_order.index(self.page_name) * 48

    def _set_footer(self, text):
        self.footer.value = text or ""
        self.footer.visible = bool(self.footer.value)

    def _show_accounts(self):
        self.page_name = "accounts"
        self._paint_nav()
        self.title.value = "账号管理"
        self.subtitle.value = "欧洲五国各自使用独立 Chrome。"
        self.subtitle.visible = True
        self.footer.value = "未登录：还在卖家欢迎页。已登录：已经进入卖家后台。"
        self.footer.visible = True
        self.header_actions.controls = [
            _soft_button("一起拉起", lambda e: self.page.run_task(self._start_all)),
            _soft_button("重新检查登录", lambda e: self.page.run_task(self._check_login, False)),
        ]
        self._status = {}
        rows = []
        for code, name in EU_COUNTRIES:
            chip, text = self._status_chip(self._login_cache.get(code, "未确认"))
            self._status[code] = (chip, text)
            rows.append(
                ft.Container(
                    border=ft.Border.only(top=ft.BorderSide(1, LINE)),
                    padding=ft.Padding(left=16, top=8, right=16, bottom=8),
                    content=ft.Row(
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Text(f"{name}  {code}", width=140, size=14, color=INK),
                            ft.Text(str(COUNTRY_PORTS[code]), width=80, size=13, color=MUTED),
                            chip,
                            ft.Container(expand=True),
                            _soft_button("打开", lambda e, item=code: self.page.run_task(self._open_country, item), height=32),
                        ],
                    ),
                )
            )
        self.body.controls = [
            ft.Container(
                expand=True,
                bgcolor=SHEET,
                border=ft.Border.all(1, LINE),
                border_radius=12,
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                content=ft.Column(
                    spacing=0,
                    controls=[
                        ft.Container(
                            padding=ft.Padding(left=16, top=12, right=16, bottom=12),
                            content=ft.Row(
                                controls=[
                                    ft.Text("国家", width=140, size=12, color=MUTED),
                                    ft.Text("端口", width=80, size=12, color=MUTED),
                                    ft.Text("状态", size=12, color=MUTED),
                                ]
                            ),
                        ),
                        *rows,
                    ],
                ),
            )
        ]
        if self.page is not None:
            self.page.run_task(self._check_login, True)

    def _status_chip(self, value):
        fg, bg = _pill_colors(value)
        text = ft.Text(value, size=12, color=fg)
        chip = ft.Container(
            bgcolor=bg,
            border_radius=11,
            padding=ft.Padding(left=8, right=8, top=3, bottom=3),
            content=text,
        )
        return chip, text

    def _set_status(self, code, value):
        pair = self._status.get(code)
        if pair is None:
            return
        chip, text = pair
        fg, bg = _pill_colors(value)
        text.value = value
        text.color = fg
        chip.bgcolor = bg

    async def _watch_login(self):
        while True:
            await asyncio.sleep(60)
            if not self.launching:
                await self._check_login(True)

    async def _check_login(self, quiet=True):
        if self.launching:
            return
        self._login_gen += 1
        generation = self._login_gen
        if not quiet and self.page_name == "accounts":
            for code, _name in EU_COUNTRIES:
                self._set_status(code, "检查中")
            self.page.update()

        def work():
            return {code: read_login_state(code) for code, _name in EU_COUNTRIES}

        states = await asyncio.to_thread(work)
        if generation != self._login_gen:
            return
        self._login_cache.update(states)
        if self.page_name != "accounts":
            return
        for code, value in states.items():
            self._set_status(code, value)
        self.page.update()

    async def _start_all(self):
        if self.launching:
            self._set_footer("五个国家正在启动。")
            self.page.update()
            return
        self.launching = True
        self._login_gen += 1
        for code, _name in EU_COUNTRIES:
            self._set_status(code, "正在启动")
        self._set_footer("正在启动英国、德国、法国、意大利、西班牙。")
        self.page.update()

        def work():
            errors = []
            for code, name in EU_COUNTRIES:
                try:
                    launch_chrome(code)
                except Exception as exc:
                    errors.append(f"{name}：{exc}")
            return errors

        errors = await asyncio.to_thread(work)
        self.launching = False
        self._set_footer("有国家没有启动。" + " ".join(errors) if errors else "五个国家的 Chrome 已启动。正在查看是否登录。")
        self.page.update()
        await self._check_login(True)

    async def _open_country(self, code):
        self._set_status(code, "正在打开")
        self.page.update()
        name = EU_NAMES[code]

        def work():
            try:
                return launch_chrome(code), None
            except Exception as exc:
                return None, str(exc)

        info, error = await asyncio.to_thread(work)
        if error:
            self._set_status(code, "未确认")
            self._alert("无法打开", error)
            return
        if info.get("started"):
            self._set_footer(f"已打开{name}独立 Chrome。其他国家没有重新打开。")
        else:
            self._set_footer(f"{name}的 Chrome 已在端口 {info.get('port')} 上，未再启动第二个窗口。")
        self.page.update()
        state = await asyncio.to_thread(read_login_state, code)
        self._set_status(code, state)
        self.page.update()

    def _show_records(self):
        self.page_name = "records"
        self._paint_nav()
        self.title.value = "上传记录"
        self.subtitle.value = ""
        self.subtitle.visible = False
        self.footer.value = ""
        self.footer.visible = False
        self.header_actions.controls = []
        self.task_field = ft.TextField(
            value=self.record_query,
            width=220,
            height=FIELD_H,
            text_size=14,
            color=INK,
            bgcolor=SHEET,
            filled=True,
            fill_color=SHEET,
            border=_field_border(),
            content_padding=ft.Padding(left=12, top=8, right=12, bottom=8),
            cursor_color=INK,
            text_style=ft.TextStyle(size=14, color=INK, font_family=FONT),
            on_submit=lambda e: self._apply_query(),
        )
        self.date_label = ft.Text(self._date_caption(), size=14, color=INK, font_family=FONT, no_wrap=True)
        self._query_button = _search_button(lambda e: self._apply_query())
        self.tabs = SiteTabs(self._choose_site)
        self.tabs.move(self._selected_code())
        self.table = ft.Column(
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self._row_checks = []
        self._header_check = None
        self.page_row = ft.Row(spacing=4, alignment=ft.MainAxisAlignment.CENTER)
        self.size_box = _page_size_dropdown(str(self.record_page_size), self._on_page_size)
        filters = ft.Column(
            spacing=12,
            controls=[
                self.tabs,
                ft.Row(
                    spacing=8,
                    alignment=ft.MainAxisAlignment.START,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Text("task_id", size=14, color=MUTED),
                        self.task_field,
                        ft.Container(
                            width=256,
                            height=FIELD_H,
                            bgcolor=SHEET,
                            border=ft.Border.all(1, LINE),
                            border_radius=FIELD_R,
                            padding=ft.Padding(left=12, right=12, top=0, bottom=0),
                            alignment=ft.Alignment.CENTER_LEFT,
                            on_click=lambda e: self._open_dates(),
                            content=self.date_label,
                        ),
                        self._query_button,
                        _refresh_button(lambda e: self._fill()),
                    ],
                ),
            ],
        )
        footer = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                _soft_button("下载勾选", lambda e: self.page.run_task(self._download)),
                self.page_row,
                self.size_box,
            ],
        )
        self.body.controls = [
            ft.Container(
                expand=True,
                bgcolor=SHEET,
                border=ft.Border.all(1, LINE),
                border_radius=12,
                padding=ft.Padding(left=16, top=16, right=16, bottom=12),
                content=ft.Column(
                    expand=True,
                    spacing=12,
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    controls=[filters, self.table, footer],
                ),
            )
        ]
        self._fill()

    def _selected_code(self):
        if len(self.record_sites) == 1:
            return next(iter(self.record_sites))
        return "all"

    def _choose_site(self, code):
        self.record_sites.clear()
        if code != "all":
            self.record_sites.add(code)
        self.record_page = 1
        self.tabs.move(code)
        self._fill()
        self.page.update()

    def _date_caption(self):
        if not self.filter_from and not self.filter_to:
            return "📅 选择日期范围"

        def short(text):
            moment = _parse_clock(text, end_of_day=False)
            if moment is None:
                return text or "…"
            return moment.strftime("%Y-%m-%d")

        return f"📅 {short(self.filter_from)} ~ {short(self.filter_to or self.filter_from)}"

    def _open_dates(self):
        start = _parse_clock(self.filter_from, end_of_day=False)
        end = _parse_clock(self.filter_to, end_of_day=False)
        moment = start or datetime.now()
        panel = DatePanel(
            moment.year,
            moment.month,
            lambda picked_start, picked_end: self._commit_range(picked_start, picked_end),
            on_close=self._close_dates,
            start=start,
            end=end,
        )
        dialog = ft.AlertDialog(
            modal=True,
            bgcolor=ft.Colors.TRANSPARENT,
            content_padding=ft.Padding.all(0),
            content=panel.control,
        )
        self._date_dialog = dialog
        self.page.show_dialog(dialog)

    def _close_dates(self):
        self.page.pop_dialog()

    def _commit_range(self, start, end):
        if not start:
            self.filter_from = ""
            self.filter_to = ""
            self.filter_until_now = True
        else:
            self.filter_from = start
            self.filter_to = end or start
            self.filter_until_now = False
        self.date_label.value = self._date_caption()
        self.page.pop_dialog()
        self.page.update()

    def _apply_query(self):
        self.record_query = (self.task_field.value or "").strip()
        if self.filter_from or self.filter_to:
            self.record_span = "range"
            if not self.filter_to:
                self.filter_until_now = True
        else:
            self.record_span = "all"
        self.record_page = 1
        self._fill()
        self.page.update()

    def _fill(self):
        if self.page_name != "records":
            return
        ok, message = _time_filter_error(self.record_span, self.filter_from, self.filter_to, self.filter_until_now)
        if not ok:
            self._set_footer(message)
            return
        rows = [row for row in load_upload_records(self.picture_log_path, self.aplus_log_path) if _match_sites(row, self.record_sites)]
        _attach_task_ids(rows, self.database_path)
        shown = _sort_groups_newest(
            [
                group
                for group in group_upload_rows(rows)
                if _group_visible(group, self.record_span, self.filter_from, self.filter_to, self.filter_until_now, self.last_upload_at, self.record_query)
            ]
        )
        page_groups, page, pages = _paginate(shown, self.record_page, self.record_page_size)
        self.record_page = page
        self.record_pages = pages
        self.record_shown_count = len(shown)
        self._page_groups = page_groups
        self._checked = [False] * len(page_groups)
        if len(self.record_sites) == 1:
            site_name = EU_NAMES.get(next(iter(self.record_sites)), "")
            self.subtitle.value = f"当前只看{site_name}。点「全部」恢复，点表格里的国家或站点也会筛选。"
            self.subtitle.visible = True
        else:
            self.subtitle.value = ""
            self.subtitle.visible = False
        self._render_table()
        self._render_pager()

    def _render_table(self):
        all_on = bool(self._checked) and all(self._checked)
        self._header_check = _check_box(all_on, self._toggle_all)
        self._row_checks = []
        header = self._line(
            [
                ft.Container(width=42, content=self._header_check),
                *[self._head(text, key) for text, key in zip(HEADS, KEYS)],
            ]
        )
        body = []
        if not self._page_groups:
            body.append(ft.Container(height=72, alignment=ft.Alignment.CENTER_LEFT, content=ft.Text("没有记录", color=MUTED)))
        for index, group in enumerate(self._page_groups):
            body.append(self._group_row(index, group))
        self.table.controls = [header, ft.Divider(height=1, thickness=1, color="#F3F4F6"), *body]

    def _head(self, text, key):
        label = ft.Text(text, size=12, color=MUTED, max_lines=1, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
        if key == "status":
            return ft.Container(width=72, content=label)
        return ft.Container(expand=COLUMN_FLEX[key], content=label)

    def _line(self, controls):
        return ft.Row(spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER, controls=controls)

    def _cell(self, key, content, tooltip=None, on_click=None):
        box = dict(alignment=ft.Alignment.CENTER_LEFT, tooltip=tooltip or None, on_click=on_click, content=content)
        if key == "status":
            box["width"] = 72
        else:
            box["expand"] = COLUMN_FLEX[key]
        return ft.Container(**box)

    def _group_row(self, index, group):
        head = group[0]
        details = ft.Column(spacing=2, tight=True, controls=[self._detail_line(row) for row in group])
        merges = []
        for key in RECORD_MERGE:
            raw = "" if head.get(key) is None else str(head.get(key))
            shown = _short_clock(raw) if key == "time" else raw
            text = ft.Text(shown or "—", size=13, color=INK, max_lines=1, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
            click = (lambda e, value=raw: self._choose_from_cell(value)) if key in {"country", "market"} else None
            merges.append(self._cell(key, text, tooltip=raw, on_click=click))
        box = _check_box(self._checked[index], lambda e, item=index: self._toggle_one(item, e.control.value))
        self._row_checks.append(box)
        return ft.Container(
            border=ft.Border.only(bottom=ft.BorderSide(1, "#F3F4F6")),
            padding=ft.Padding(top=6, bottom=6, left=0, right=0),
            content=self._line([ft.Container(width=42, content=box), *merges, ft.Container(expand=sum(COLUMN_FLEX[key] for key in RECORD_DETAIL), content=details)]),
        )

    def _detail_line(self, row):
        cells = []
        for key in RECORD_DETAIL:
            raw = "" if row.get(key) is None else str(row.get(key))
            if key == "status":
                fg, bg = _pill_colors(raw or "—")
                pill = ft.Container(
                    bgcolor=bg,
                    border_radius=11,
                    padding=ft.Padding(left=8, right=8, top=2, bottom=2),
                    content=ft.Text(raw or "—", size=12, color=fg, no_wrap=True),
                )
                cells.append(self._cell(key, pill, tooltip=raw))
            else:
                text = ft.Text(raw or "—", size=13, color=INK, max_lines=1, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
                tip = None
                if raw:
                    tip = ft.Tooltip(
                        message=raw,
                        bgcolor="#111827",
                        text_style=ft.TextStyle(size=12, color="#FFFFFF"),
                        padding=ft.Padding(left=10, top=8, right=10, bottom=8),
                    )
                cells.append(self._cell(key, text, tooltip=tip))
        return self._line(cells)

    def _choose_from_cell(self, raw):
        code = _as_code(raw)
        if code in EU_NAMES:
            self._choose_site(code)

    def _toggle_one(self, index, value):
        if 0 <= index < len(self._checked):
            self._checked[index] = bool(value)
        if self._header_check is not None:
            self._header_check.value = bool(self._checked) and all(self._checked)
            self._header_check.update()

    def _toggle_all(self, e):
        flag = bool(e.control.value)
        self._checked = [flag] * len(self._row_checks)
        for box in self._row_checks:
            box.value = flag
        self.page.update()

    def _render_pager(self):
        chips = []

        def go(number):
            def handler(e):
                if number < 1 or number > self.record_pages or number == self.record_page:
                    return
                self.record_page = number
                self._fill()
                self.page.update()

            return handler

        chips.append(self._page_button("<", go(self.record_page - 1), enabled=self.record_page > 1))
        for number in self._visible_pages():
            chips.append(self._page_button(str(number), go(number), current=number == self.record_page))
        chips.append(self._page_button(">", go(self.record_page + 1), enabled=self.record_page < self.record_pages))
        self.page_row.controls = chips

    def _visible_pages(self):
        total = max(self.record_pages, 1)
        start = max(1, self.record_page - 2)
        end = min(total, start + 4)
        start = max(1, end - 4)
        return list(range(start, end + 1))

    def _page_button(self, text, handler, current=False, enabled=True):
        return ft.Button(
            content=text,
            width=32,
            height=32,
            elevation=0,
            disabled=not enabled and not current,
            bgcolor=BTN_FILL if current else SHEET,
            color=INK if current else MUTED,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8), elevation=0, padding=ft.Padding.all(0)),
            on_click=handler,
        )

    def _on_page_size(self, size):
        size = int(size)
        if size != self.record_page_size:
            self.record_page_size = size
            self.record_page = 1
            self._fill()
            self.page.update()

    def _selected_rows(self):
        chosen = []
        for group, flag in zip(self._page_groups, self._checked):
            if flag:
                chosen.extend(group)
        return chosen

    async def _download(self):
        rows = self._selected_rows()
        if not rows:
            self._alert("没有勾选", "请先勾选要下载的行。")
            return
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(list(RECORD_MERGE_HEADS) + list(RECORD_DETAIL_HEADS))
        for row in rows:
            writer.writerow([row.get(key, "") for key in KEYS])
        path = await ft.FilePicker().save_file(
            dialog_title="下载勾选",
            file_name="上传记录.csv",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["csv"],
            src_bytes=buffer.getvalue().encode("utf-8-sig"),
        )
        if path:
            self._set_footer(f"已下载 {len(rows)} 行：" + path)
            self.page.update()

    async def _choose_excel(self):
        if self.uploading:
            self._alert("正在上传", "请等当前 Excel 跑完。")
            return
        files = await ft.FilePicker().pick_files(
            dialog_title="选择 Excel",
            allow_multiple=False,
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["xlsx"],
        )
        if not files or not files[0].path:
            return
        path = files[0].path
        self.uploading = True
        self.last_upload_at = datetime.now().replace(microsecond=0)
        _save_last_upload(self.database_path.parent / "last_upload_at.txt", self.last_upload_at)
        self._set_footer("正在读取并上传：" + path)
        self._append_log("开始读取 Excel：" + path, 0, 0)
        self.page.update()

        def work():
            try:
                report = run(
                    path,
                    self.database_path,
                    self.picture_log_path,
                    on_progress=self._on_progress,
                    aplus_log_path=self.aplus_log_path,
                )
                return report, None
            except Exception as exc:
                return None, explain_read_failure(exc, path)

        report, error = await asyncio.to_thread(work)
        await self._upload_finished(report, error)

    async def _upload_finished(self, report, error):
        self.uploading = False
        if error:
            self._set_footer(error)
            self._append_log("未完成：" + error.splitlines()[0], 1, 1)
            self._alert("上传未完成", error)
            return
        append_aplus_log(self.aplus_log_path, report)
        count = len((report or {}).get("results") or [])
        self._set_footer(f"本次处理 {count} 行。上传记录已刷新。")
        self._append_log(f"本次处理 {count} 行", 1, 1)
        self._show_records()
        self.page.update()

    def _on_progress(self, text, done=0, total=0, refresh_records=False):
        if self.page is None:
            self._pending.append((text, done, total, refresh_records))
            return
        self.page.run_task(self._apply_feed, text, done, total, refresh_records)

    async def _apply_feed(self, text, done=0, total=0, refresh_records=False):
        self._append_log(text, done, total)
        if refresh_records and self.page_name == "records":
            self._fill()
        self.page.update()

    def _append_log(self, text, done=0, total=0):
        done = int(done or 0)
        total = int(total or 0)
        label = " ".join(str(text or "").split())
        self.progress_label.value = label[:42]
        if total > 0:
            self.progress_bar.value = max(0, min(1, done / total))
        else:
            self.progress_bar.value = None
        self.progress_log.controls.append(ft.Text(str(text or ""), size=11, color=SIDE_TEXT))
        if len(self.progress_log.controls) > 200:
            del self.progress_log.controls[:-200]

    def _alert(self, title, message):
        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=title,
                content=ft.Text(message),
                actions=[ft.TextButton(content="知道了", on_click=lambda e: self.page.pop_dialog())],
            )
        )
