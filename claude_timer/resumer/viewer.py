# -*- coding: utf-8 -*-
"""viewer.py — 對話內容預覽視窗（唯讀，純讀本機檔案，不花任何 token）。

排程之前一定要確認「是不是這個對話」。以前這裡把整個對話接成一長串字串倒出來，
在一個做了好幾小時、幾百則工具往返的對話上，那等於什麼都沒說。現在畫面分成三塊：

    摘要卡   期間、各類則數、最後一次你說了什麼 / 它回了什麼——通常看這裡就夠了
    時段大綱 左邊一列一段（照時間切，見 transcript.py），點一下跳到那一段
    內容     右邊照時段排開，太長的訊息先摺起來，想看再展開

工具動作與思考預設的顯示方式也不同：工具併成一行「⚙ Bash ×12、Edit ×3」，思考
預設不顯示（要看再勾）。目的只有一個——讓人一眼認出這是不是要接續的那個對話。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..ui import theme
from ..ui.theme import COLORS
from . import transcript as tx
from .sessions import SessionInfo, describe_holder, session_holder

# 單則超過這麼多行就先摺起來，附一個「展開全文」。
COLLAPSE_LINES = 12
# 訊息總數超過這個量時，只有最近幾段預設展開，其餘先收起來。
BIG_TRANSCRIPT = 60
SEGMENTS_OPEN_BY_DEFAULT = 3


class ConversationViewer(tk.Toplevel):
    def __init__(self, app, session: SessionInfo) -> None:
        super().__init__(app)
        self.app = app
        self.session = session
        self.fonts = app.fonts
        self.transcript = tx.read(session.jsonl)

        self._expanded_entries: set[int] = set()
        self._open_segments: set[int] = set()
        self._entry_marks: dict[int, str] = {}
        self._seg_marks: dict[int, str] = {}
        self._tag_seq = 0

        self.title(f"對話內容預覽｜{session.title}")
        self.configure(bg=COLORS["bg"])
        self.transient(app)
        theme.fit_on_screen(self, 1000, 820, 640, 460)

        self.show_tools = tk.BooleanVar(value=True)
        self.show_thinking = tk.BooleanVar(value=False)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render())

        self._build_header()
        self._build_summary()
        self._build_body()
        self._build_footer()

        self._init_open_segments()
        self._fill_outline()
        self._render()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(50, lambda: self.text.see(tk.END))   # 預設看最近的訊息

    # ---------- 版面 ----------

    def _build_header(self) -> None:
        head = ttk.Frame(self, style="Card.TFrame", padding=(14, 10))
        head.pack(fill="x")
        ttk.Label(head, text=self.session.title, style="H2.TLabel",
                  wraplength=900, justify="left").pack(anchor="w")
        meta = f"{self.session.cwd or '(無專案路徑)'}｜session {self.session.session_id}｜" \
               f"最後活動 {self.session.relative_time}"
        ttk.Label(head, text=meta, style="Muted.TLabel", wraplength=900,
                  justify="left").pack(anchor="w", pady=(2, 0))
        holder = session_holder(self.session.session_id)
        if holder:
            ttk.Label(head, text=f"● 這個對話{describe_holder(holder)}——送出前要先關掉它",
                      style="Warn.TLabel", wraplength=900, justify="left").pack(anchor="w", pady=(4, 0))

    def _build_summary(self) -> None:
        card = ttk.Labelframe(self, text="這個對話在做什麼", style="Card.TLabelframe", padding=10)
        card.pack(fill="x", padx=12, pady=(10, 0))
        card.columnconfigure(1, weight=1)
        if not self.transcript.ok:
            ttk.Label(card, text=self.transcript.reason, style="Err.TLabel",
                      wraplength=880, justify="left").grid(row=0, column=0, columnspan=2, sticky="w")
            return
        d = self.transcript.digest
        tools = "、".join(f"{n} ×{c}" for n, c in d.top_tools) or "沒有工具動作"
        rows = [
            ("期間", d.span_label()),
            ("訊息", f"{d.volume_label()}｜分成 {len(self.transcript.segments)} 個時段"),
            ("常用工具", tools),
            ("最後你說", d.last_human or "（沒有）"),
            ("最後它說", d.last_assistant or "（沒有）"),
        ]
        for i, (label, value) in enumerate(rows):
            ttk.Label(card, text=label, style="Muted.TLabel").grid(
                row=i, column=0, sticky="nw", padx=(0, 10), pady=1)
            ttk.Label(card, text=value, style="Card.TLabel", wraplength=780,
                      justify="left").grid(row=i, column=1, sticky="w", pady=1)
        if d.truncated:
            ttk.Label(card, text="（對話很長，只讀了最近的一段）", style="Warn.TLabel").grid(
                row=len(rows), column=1, sticky="w", pady=(4, 0))

    def _build_body(self) -> None:
        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=12, pady=10)

        left = ttk.Labelframe(pane, text="時段大綱（點一下跳過去）", style="Card.TLabelframe",
                              padding=(8, 6))
        self.outline = ttk.Treeview(left, columns=("when", "what"), show="headings",
                                    selectmode="browse", height=8)
        self.outline.heading("when", text="時間")
        self.outline.heading("what", text="這一段在談什麼")
        self.outline.column("when", width=110, anchor="w", stretch=False)
        self.outline.column("what", width=180, anchor="w")
        obar = ttk.Scrollbar(left, orient="vertical", command=self.outline.yview)
        self.outline.configure(yscrollcommand=obar.set)
        self.outline.pack(side="left", fill="both", expand=True)
        obar.pack(side="left", fill="y")
        self.outline.bind("<<TreeviewSelect>>", self._on_outline_select)
        pane.add(left, weight=1)

        right = ttk.Labelframe(pane, text="內容", style="Card.TLabelframe", padding=(8, 6))
        self.text = tk.Text(right, wrap="word", state="disabled", borderwidth=0,
                            highlightthickness=0, font=self.fonts.body, bg=COLORS["card"],
                            fg=COLORS["text"], padx=10, pady=6, spacing1=1, spacing3=3)
        tbar = ttk.Scrollbar(right, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=tbar.set)
        self.text.pack(side="left", fill="both", expand=True)
        tbar.pack(side="left", fill="y")
        pane.add(right, weight=3)

        c = COLORS
        self.text.tag_configure("who_user", foreground=c["accent"], font=self.fonts.h2, spacing1=10)
        self.text.tag_configure("who_asst", foreground=c["ok"], font=self.fonts.h2, spacing1=10)
        self.text.tag_configure("who_tool", foreground=c["muted"], font=self.fonts.small, spacing1=8)
        self.text.tag_configure("who_think", foreground="#7c3aed", font=self.fonts.small, spacing1=8)
        self.text.tag_configure("meta", foreground=c["muted"], font=self.fonts.small)
        self.text.tag_configure("body", lmargin1=14, lmargin2=14)
        self.text.tag_configure("tool_body", lmargin1=14, lmargin2=14, foreground=c["muted"],
                                font=self.fonts.small)
        self.text.tag_configure("think_body", lmargin1=14, lmargin2=14, foreground="#6b21a8",
                                font=self.fonts.small)
        self.text.tag_configure("seg", foreground=c["accent"], font=self.fonts.h2,
                                spacing1=16, spacing3=6)
        self.text.tag_configure("system", foreground=c["warn"], font=self.fonts.small,
                                spacing1=10, spacing3=4, justify="center")
        self.text.tag_configure("hit", background="#fef08a")

    def _build_footer(self) -> None:
        bar = ttk.Frame(self, style="Card.TFrame", padding=(12, 10))
        bar.pack(fill="x")
        ttk.Label(bar, text="搜尋：", style="Card.TLabel").pack(side="left")
        ttk.Entry(bar, textvariable=self.search_var, width=22).pack(side="left", padx=(4, 10))
        ttk.Checkbutton(bar, text="顯示工具動作", variable=self.show_tools,
                        command=self._render).pack(side="left")
        ttk.Checkbutton(bar, text="顯示思考過程", variable=self.show_thinking,
                        command=self._render).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="跳到最新", command=lambda: self.text.see(tk.END)).pack(side="left", padx=(12, 0))

        ttk.Button(bar, text="就選這個對話", style="Accent.TButton",
                   command=self._choose).pack(side="right")
        ttk.Button(bar, text="關閉", command=self.destroy).pack(side="right", padx=(0, 8))
        self.hint_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.hint_var, style="Muted.TLabel").pack(side="right", padx=(0, 12))

    # ---------- 大綱 ----------

    def _init_open_segments(self) -> None:
        segments = self.transcript.segments
        total = len(self.transcript.entries)
        if total <= BIG_TRANSCRIPT:
            self._open_segments = {s.index for s in segments}
        else:
            self._open_segments = {s.index for s in segments[-SEGMENTS_OPEN_BY_DEFAULT:]}

    def _fill_outline(self) -> None:
        for seg in self.transcript.segments:
            start = seg.start.strftime("%m-%d %H:%M") if seg.start else "—"
            self.outline.insert("", "end", iid=str(seg.index), values=(start, seg.title))

    def _on_outline_select(self, _event) -> None:
        sel = self.outline.selection()
        if not sel:
            return
        index = int(sel[0])
        self._open_segments.add(index)
        self._render()
        mark = self._seg_marks.get(index)
        if mark:
            self.text.see(mark)

    # ---------- 內容 ----------

    def _visible(self, entry: tx.Entry) -> bool:
        if entry.kind == tx.KIND_TOOLS and not self.show_tools.get():
            return False
        if entry.kind == tx.KIND_THINKING and not self.show_thinking.get():
            return False
        query = self.search_var.get().strip().lower()
        if query and query not in entry.text.lower():
            return False
        return True

    def _render(self) -> None:
        if not self.transcript.ok:
            self._set_text(self.transcript.reason)
            return
        query = self.search_var.get().strip()
        self.text.configure(state="normal")
        self.text.delete("1.0", tk.END)
        self._entry_marks.clear()
        self._seg_marks.clear()

        shown = 0
        for seg in self.transcript.segments:
            entries = [e for e in seg.entries if self._visible(e)]
            if not entries:
                continue
            self._mark(self._seg_marks, seg.index, "seg")
            opened = seg.index in self._open_segments or bool(query)
            self.text.insert(tk.END, seg.header() + "\n", "seg")
            if not opened:
                self._link(f"　▸ 展開這個時段（{len(entries)} 則）\n",
                           lambda i=seg.index: self._toggle_segment(i))
                continue
            for entry in entries:
                self._insert_entry(entry, query)
                shown += 1

        if shown == 0:
            self.text.insert(tk.END, "\n（沒有符合目前篩選條件的訊息）\n", "system")
        self.text.configure(state="disabled")
        total = len(self.transcript.entries)
        self.hint_var.set(f"顯示 {shown} / {total} 則" + ("（搜尋中）" if query else ""))

    def _insert_entry(self, entry: tx.Entry, query: str) -> None:
        self._mark(self._entry_marks, entry.index, "entry")
        if entry.kind == tx.KIND_SYSTEM:
            self.text.insert(tk.END, f"— {tx.first_line(entry.text, 60)} —\n", "system")
            if entry.text.startswith("【壓縮摘要】"):
                self._insert_body(entry, "body", query)
            return

        who, who_tag, body_tag = {
            tx.KIND_HUMAN: ("你", "who_user", "body"),
            tx.KIND_ASSISTANT: ("Claude", "who_asst", "body"),
            tx.KIND_TOOLS: ("工具動作", "who_tool", "tool_body"),
            tx.KIND_THINKING: ("思考", "who_think", "think_body"),
        }[entry.kind]
        self.text.insert(tk.END, who, who_tag)
        stamp = entry.time_label()
        if stamp:
            self.text.insert(tk.END, f"   {stamp}", "meta")
        self.text.insert(tk.END, "\n")
        self._insert_body(entry, body_tag, query)

    def _insert_body(self, entry: tx.Entry, body_tag: str, query: str) -> None:
        lines = entry.text.splitlines() or [""]
        collapsed = (len(lines) > COLLAPSE_LINES and entry.index not in self._expanded_entries
                     and not query)
        shown_lines = lines[:COLLAPSE_LINES] if collapsed else lines
        start = self.text.index(tk.END)
        self.text.insert(tk.END, "\n".join(shown_lines) + "\n", body_tag)
        if query:
            self._highlight(start, query)
        if collapsed:
            rest = len(lines) - COLLAPSE_LINES
            self._link(f"　▾ 展開全文（還有 {rest} 行）\n",
                       lambda i=entry.index: self._toggle_entry(i))

    def _highlight(self, start: str, query: str) -> None:
        needle = query.lower()
        index = start
        while True:
            hit = self.text.search(needle, index, tk.END, nocase=True)
            if not hit:
                return
            end = f"{hit}+{len(needle)}c"
            self.text.tag_add("hit", hit, end)
            index = end

    def _link(self, label: str, command) -> None:
        self._tag_seq += 1
        tag = f"link{self._tag_seq}"
        self.text.insert(tk.END, label, (tag, "meta"))
        self.text.tag_configure(tag, foreground=COLORS["accent"], underline=True)
        self.text.tag_bind(tag, "<Button-1>", lambda _e: command())
        self.text.tag_bind(tag, "<Enter>", lambda _e: self.text.configure(cursor="hand2"))
        self.text.tag_bind(tag, "<Leave>", lambda _e: self.text.configure(cursor=""))

    def _mark(self, store: dict, key: int, prefix: str) -> None:
        name = f"{prefix}{key}"
        self.text.mark_set(name, self.text.index(tk.END))
        self.text.mark_gravity(name, "left")
        store[key] = name

    def _toggle_entry(self, index: int) -> None:
        self._expanded_entries.add(index)
        self._render()
        mark = self._entry_marks.get(index)
        if mark:
            self.text.see(mark)

    def _toggle_segment(self, index: int) -> None:
        self._open_segments.add(index)
        self._render()
        mark = self._seg_marks.get(index)
        if mark:
            self.text.see(mark)

    def _set_text(self, message: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, message)
        self.text.configure(state="disabled")

    # ---------- 動作 ----------

    def _choose(self) -> None:
        self.app.select_session(self.session)
        self.destroy()
