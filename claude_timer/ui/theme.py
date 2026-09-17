# -*- coding: utf-8 -*-
"""theme.py — 兩個 GUI 共用的外觀：配色、字級、ttk 樣式、高 DPI 與視窗尺寸。

集中在這裡的理由不只是好看：字級跟 DPI 縮放一旦兩支各寫一份，就會出現「這支在
4K 螢幕糊掉、那支正常」這種只在某台電腦上出現的問題。
"""
from __future__ import annotations

import ctypes
import platform
import tkinter as tk
from tkinter import font as tkfont, ttk

# 乾淨淺色 + 藍色重點。名稱依用途而不是顏色，換配色時不用改呼叫端。
COLORS = {
    "bg": "#f4f5f7",
    "card": "#ffffff",
    "border": "#d9dce1",
    "text": "#1f2430",
    "muted": "#6b7280",
    "accent": "#2563eb",
    "accent_fg": "#ffffff",
    "ok": "#15803d",
    "warn": "#b45309",
    "err": "#b91c1c",
    "sel": "#dbeafe",
    "zebra": "#f8f9fb",
    "log_bg": "#0f172a",
    "log_fg": "#e2e8f0",
}


class Fonts:
    """一組具名字型。用 attribute 取用（f.h1 / f.body），不要在畫面程式裡硬寫字級。"""

    def __init__(self, family: str) -> None:
        self.family = family
        self.h1 = tkfont.Font(family=family, size=17, weight="bold")
        self.h2 = tkfont.Font(family=family, size=13, weight="bold")
        self.body = tkfont.Font(family=family, size=11)
        self.small = tkfont.Font(family=family, size=10)
        self.mono = tkfont.Font(family="Consolas", size=10)
        self.big_entry = tkfont.Font(family=family, size=13, weight="bold")


def enable_dpi_awareness() -> None:
    """Windows 高 DPI：告訴系統我們自己處理縮放，字才不會被系統放大糊掉。"""
    if platform.system() != "Windows":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def apply(root: tk.Misc) -> Fonts:
    """套用主題，回傳字型組。每個 Tk 視窗建立後呼叫一次。"""
    enable_dpi_awareness()
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except Exception:
        pass

    base = tkfont.nametofont("TkDefaultFont")
    family = "Microsoft JhengHei UI" if platform.system() == "Windows" else base.actual("family")
    base.configure(family=family, size=11)
    root.option_add("*Font", base)
    fonts = Fonts(family)

    c = COLORS
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=c["bg"], foreground=c["text"])
    st.configure("TFrame", background=c["bg"])
    st.configure("Card.TFrame", background=c["card"])
    st.configure("TLabel", background=c["bg"], foreground=c["text"])
    st.configure("Card.TLabel", background=c["card"], foreground=c["text"])
    st.configure("H1.TLabel", background=c["bg"], foreground=c["text"], font=fonts.h1)
    st.configure("H2.TLabel", background=c["card"], foreground=c["text"], font=fonts.h2)
    st.configure("Muted.TLabel", background=c["card"], foreground=c["muted"], font=fonts.small)
    st.configure("MutedBg.TLabel", background=c["bg"], foreground=c["muted"], font=fonts.small)
    st.configure("Ok.TLabel", background=c["card"], foreground=c["ok"], font=fonts.small)
    st.configure("Warn.TLabel", background=c["card"], foreground=c["warn"], font=fonts.small)
    st.configure("Err.TLabel", background=c["card"], foreground=c["err"], font=fonts.small)
    st.configure("TCheckbutton", background=c["card"], foreground=c["text"])
    st.map("TCheckbutton", background=[("active", c["card"])])
    st.configure("TRadiobutton", background=c["card"], foreground=c["text"])
    st.map("TRadiobutton", background=[("active", c["card"])])
    st.configure("Card.TLabelframe", background=c["card"], bordercolor=c["border"],
                 relief="solid", borderwidth=1)
    st.configure("Card.TLabelframe.Label", background=c["card"], foreground=c["accent"],
                 font=fonts.h2)
    st.configure("TEntry", fieldbackground="#ffffff", bordercolor=c["border"])
    st.configure("TCombobox", fieldbackground="#ffffff", bordercolor=c["border"])
    st.configure("TButton", background="#eef0f3", foreground=c["text"], borderwidth=1,
                 bordercolor=c["border"], focuscolor=c["bg"], padding=(11, 6))
    st.map("TButton", background=[("active", "#e2e5ea"), ("pressed", "#d5d9df")])
    st.configure("Accent.TButton", background=c["accent"], foreground=c["accent_fg"],
                 borderwidth=0, padding=(16, 7), font=fonts.h2)
    st.map("Accent.TButton", background=[("active", "#1d4ed8"), ("pressed", "#1e40af")])
    st.configure("Danger.TButton", background="#fee2e2", foreground=c["err"], borderwidth=1,
                 bordercolor="#fecaca", padding=(11, 6))
    st.map("Danger.TButton", background=[("active", "#fecaca"), ("pressed", "#fca5a5")])
    st.configure("Vertical.TScrollbar", background="#dfe3e8", troughcolor=c["bg"],
                 bordercolor=c["bg"], arrowcolor=c["muted"])
    st.configure("Treeview", background=c["card"], fieldbackground=c["card"],
                 foreground=c["text"], bordercolor=c["border"], rowheight=24)
    st.configure("Treeview.Heading", background="#eef0f3", foreground=c["muted"],
                 font=fonts.small, relief="flat")
    st.map("Treeview", background=[("selected", c["sel"])], foreground=[("selected", c["text"])])
    return fonts


def fit_on_screen(win, want_w: int, want_h: int, min_w: int, min_h: int) -> None:
    """視窗開起來一定要放得進螢幕：想要的尺寸跟可用空間取小的那個，然後置中。

    這是「畫面下半被切掉」最常見的成因——寫死 1140x970 遇到 1366x768 的筆電就爆版。
    配合 ScrollableFrame，就算縮到最小尺寸也仍然看得到全部內容（用捲軸）。
    """
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w = max(min(want_w, sw - 60), min(min_w, sw - 20))
    h = max(min(want_h, sh - 120), min(min_h, sh - 40))
    x = max((sw - w) // 2, 0)
    y = max((sh - h) // 3, 0)
    win.geometry(f"{w}x{h}+{x}+{y}")
    win.minsize(min(min_w, sw - 20), min(min_h, sh - 40))
