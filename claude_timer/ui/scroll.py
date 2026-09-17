# -*- coding: utf-8 -*-
"""scroll.py — 可捲動的版面容器。

為什麼需要這個：Tk 的 Frame 不會捲動，內容比視窗高就是直接被切掉，使用者沒有任何
辦法看到下半部（視窗縮小、螢幕比較矮、系統縮放 125% 都會踩到）。這個容器把內容放進
Canvas，內容一超過可視高度就自動出現捲軸，沒超過就自動把捲軸收起來。

用法：

    scroller = ScrollableFrame(root)
    scroller.pack(fill="both", expand=True)
    body = scroller.body          # 畫面元件都放進 body，其餘的它自己處理
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .theme import COLORS

# 滾輪一格捲幾「行」。Windows 一格 delta 是 120。
_WHEEL_UNITS = 3


class ScrollableFrame(ttk.Frame):
    def __init__(self, master, *, padding=(0, 0), background: str | None = None) -> None:
        super().__init__(master)
        bg = background or COLORS["bg"]
        self.canvas = tk.Canvas(self, background=bg, highlightthickness=0, borderwidth=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_yset)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.body = ttk.Frame(self.canvas, padding=padding)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        # 滑鼠滾輪：bind_all 是應用程式層級的，所以處理函式要自己確認事件是不是
        # 發生在這個容器裡面（見 _on_wheel），才不會去捲到別的視窗。
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self.bind_all("<Button-4>", self._on_wheel, add="+")   # X11 滾輪上
        self.bind_all("<Button-5>", self._on_wheel, add="+")   # X11 滾輪下

    # -- 尺寸同步 --

    def _on_body_configure(self, _event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        # 內容寬度永遠等於可視寬度：只上下捲，不左右捲。
        self.canvas.itemconfigure(self._window, width=event.width)

    def _on_yset(self, first: str, last: str) -> None:
        """捲軸沒用到就收起來——省空間，也讓使用者一眼知道沒有被藏起來的內容。"""
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.vbar.grid_remove()
        else:
            self.vbar.grid()
        self.vbar.set(first, last)

    # -- 滾輪 --

    def _on_wheel(self, event):
        widget = getattr(event, "widget", None)
        if not isinstance(widget, tk.Misc) or not self.winfo_exists():
            return None
        node = widget
        while node is not None:
            if node is self.canvas:
                break
            # 內層自己有捲軸的元件（清單、文字框）自己捲，不要連外層一起捲。
            if isinstance(node, (tk.Text, tk.Listbox, tk.Canvas, ttk.Treeview)):
                return None
            if node is node.winfo_toplevel():
                return None  # 這個事件不在本容器裡（例如另一個視窗）
            node = node.master
        if node is None:
            return None
        first, last = self.canvas.yview()
        if float(first) <= 0.0 and float(last) >= 1.0:
            return None  # 內容放得下，沒什麼好捲的
        delta = getattr(event, "delta", 0)
        step = (-1 if delta > 0 else 1) if delta else (-1 if getattr(event, "num", 5) == 4 else 1)
        self.canvas.yview_scroll(step * _WHEEL_UNITS, "units")
        return "break"

    # -- 對外 --

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0.0)

    def scroll_to_bottom(self) -> None:
        self.canvas.yview_moveto(1.0)
