# -*- coding: utf-8 -*-
"""viewer.py — 對話內容預覽視窗（唯讀，純讀本機檔案，不花任何 token）。

排程之前一定要確認「是不是這個對話」。人認對話靠的是**我當時說了什麼、它最後回了什麼**，
不是中間幾十次工具往返。所以畫面一輪一輪排：

    你      ：這一輪你打的那段話
    ▸ 中間過程：⚙ Bash ×12、Edit ×3｜中途回覆 2 則   ← 預設收起來，點一下才展開
    Claude  ：這一輪最後回的那一段（整段）

v1.1.0 的做法是照時間差切段，但一段裡還是一堆「⚙ 工具動作」洗版，要找內容得拉半天；
v1.3.0 改成以「你說一句」為界切輪（transcript.py 的 Turn），工具與思考全部收進「中間過程」。
左邊的大綱列每一輪（時間＋你說的第一句），點一下跳過去；搜尋只留下有那段字的輪。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..ui import theme
from ..ui.theme import COLORS
from . import transcript as tx
from .sessions import SessionInfo, describe_holder, session_holder

# 你打的那段超過這麼多行就先摺起來，附一個「展開全文」。
COLLAPSE_LINES = 12
# 它最後回的那一段要整段看，上限放寬；真的超長才摺。
REPLY_COLLAPSE_LINES = 60
# 中間過程裡的單則（中途回覆、思考）用短的上限。
MIDDLE_COLLAPSE_LINES = 8
# 輪數超過這個量時，只有最近幾輪預設展開，其餘先收成一行。
BIG_TRANSCRIPT = 30
TURNS_OPEN_BY_DEFAULT = 10


class ConversationViewer(tk.Toplevel):
    def __init__(self, app, session: SessionInfo) -> None:
        super().__init__(app)
        self.app = app
        self.session = session
        self.fonts = app.fonts
        self.transcript = tx.read(session.jsonl)

        self._expanded_entries: set[int] = set()   # 展開全文的那幾則
        self._open_turns: set[int] = set()         # 很長的對話裡，哪幾輪是展開的
        self._open_middles: set[int] = set()       # 哪幾輪的「中間過程」是展開的
        self._turn_marks: dict[int, str] = {}
        self._tag_seq = 0

        self.title(f"對話內容預覽｜{session.title}")
        self.configure(bg=COLORS["bg"])
        self.transient(app)
        theme.fit_on_screen(self, 1000, 820, 640, 460)

        self.show_thinking = tk.BooleanVar(value=False)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render())

        self._build_header()
        self._build_summary()
        self._build_body()
        self._build_footer()

        self._init_open_turns()
        self._fill_outline()
        self._render()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(50, lambda: self.text.see(tk.END))   # 預設看最近的一輪（通常就是卡住的地方）

    # ---------- 版面 ----------

    def _build_header(self) -> None:
        head = ttk.Frame(self, style="Card.TFrame", padding=(14, 10))
        head.pack(fill="x")
        ttk.Label(head, text=self.session.title, style="H2.TLabel",
                  wraplength=900, justify="left").pack(anchor="w")
        meta = f"{self.session.cwd or '(無專案路徑)'}｜session {self.session.session_id}｜" \
               f"最後活動 {self.session.relative_time}"
        if self.session.branch:
            meta += f"｜分支 {self.session.branch}"
        if self.session.source_label:
            meta += f"｜{self.session.source_label}"
        ttk.Label(head, text=meta, style="Muted.TLabel", wraplength=900,
                  justify="left").pack(anchor="w", pady=(2, 0))
        holder = session_holder(self.session.session_id)
        if holder:
            ttk.Label(head, text=f"● 這個對話{describe_holder(holder)}——送出前要先關掉它，或在主畫面選「釋放佔用」",
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
            ("訊息", d.volume_label()),
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

        left = ttk.Labelframe(pane, text="每一輪你說了什麼（點一下跳過去）", style="Card.TLabelframe",
                              padding=(8, 6))
        self.outline = ttk.Treeview(left, columns=("when", "what"), show="headings",
                                    selectmode="browse", height=8)
        self.outline.heading("when", text="時間")
        self.outline.heading("what", text="你說")
        self.outline.column("when", width=110, anchor="w", stretch=False)
        self.outline.column("what", width=180, anchor="w")
        obar = ttk.Scrollbar(left, orient="vertical", command=self.outline.yview)
        self.outline.configure(yscrollcommand=obar.set)
        self.outline.pack(side="left", fill="both", expand=True)
        obar.pack(side="left", fill="y")
        self.outline.bind("<<TreeviewSelect>>", self._on_outline_select)
        pane.add(left, weight=1)

        right = ttk.Labelframe(pane, text="內容（你說 → 它最後回；中間過程收起來）", style="Card.TLabelframe",
                               padding=(8, 6))
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
        self.text.tag_configure("who_mid", foreground=c["muted"], font=self.fonts.small, spacing1=8)
        self.text.tag_configure("who_think", foreground="#7c3aed", font=self.fonts.small, spacing1=8)
        self.text.tag_configure("meta", foreground=c["muted"], font=self.fonts.small)
        self.text.tag_configure("body", lmargin1=14, lmargin2=14)
        self.text.tag_configure("mid_body", lmargin1=28, lmargin2=28, foreground=c["muted"],
                                font=self.fonts.small)
        self.text.tag_configure("think_body", lmargin1=28, lmargin2=28, foreground="#6b21a8",
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
        ttk.Button(bar, text="全部展開中間過程", command=self._open_all_middles).pack(side="left")
        ttk.Checkbutton(bar, text="展開時連思考也顯示", variable=self.show_thinking,
                        command=self._render).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="跳到最新", command=lambda: self.text.see(tk.END)).pack(side="left", padx=(12, 0))

        ttk.Button(bar, text="就選這個對話", style="Accent.TButton",
                   command=self._choose).pack(side="right")
        ttk.Button(bar, text="關閉", command=self.destroy).pack(side="right", padx=(0, 8))
        self.hint_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.hint_var, style="Muted.TLabel").pack(side="right", padx=(0, 12))

    # ---------- 大綱 ----------

    def _init_open_turns(self) -> None:
        turns = self.transcript.turns
        if len(turns) <= BIG_TRANSCRIPT:
            self._open_turns = {t.index for t in turns}
        else:
            self._open_turns = {t.index for t in turns[-TURNS_OPEN_BY_DEFAULT:]}

    def _fill_outline(self) -> None:
        for turn in self.transcript.turns:
            start = turn.start.strftime("%m-%d %H:%M") if turn.start else "—"
            self.outline.insert("", "end", iid=str(turn.index), values=(start, turn.title))

    def _on_outline_select(self, _event) -> None:
        sel = self.outline.selection()
        if not sel:
            return
        index = int(sel[0])
        self._open_turns.add(index)
        self._render()
        mark = self._turn_marks.get(index)
        if mark:
            self.text.see(mark)

    # ---------- 內容 ----------

    def _matches(self, turn: tx.Turn, query: str) -> bool:
        if not query:
            return True
        return any(query in e.text.lower() for e in turn.entries)

    def _render(self) -> None:
        if not self.transcript.ok:
            self._set_text(self.transcript.reason)
            return
        query = self.search_var.get().strip().lower()
        self.text.configure(state="normal")
        self.text.delete("1.0", tk.END)
        self._turn_marks.clear()

        shown = 0
        for turn in self.transcript.turns:
            if not self._matches(turn, query):
                continue
            shown += 1
            self._mark(turn.index)
            self.text.insert(tk.END, turn.header() + "\n", "seg")
            if turn.index not in self._open_turns and not query:
                self._link(f"　▸ 展開這一輪（你：{turn.title}）\n",
                           lambda i=turn.index: self._open_turn(i))
                continue
            self._insert_turn(turn, query)

        if shown == 0:
            self.text.insert(tk.END, "\n（沒有符合目前篩選條件的訊息）\n", "system")
        self.text.configure(state="disabled")
        total = len(self.transcript.turns)
        self.hint_var.set(f"顯示 {shown} / {total} 輪" + ("（搜尋中）" if query else ""))

    def _insert_turn(self, turn: tx.Turn, query: str) -> None:
        if turn.prompt is not None:
            self._insert_who("你", "who_user", turn.prompt.time_label())
            self._insert_body(turn.prompt, "body", query, COLLAPSE_LINES)

        if turn.middle:
            opened = turn.index in self._open_middles or bool(query)
            if opened:
                self._link(f"　▾ 收起中間過程（{turn.middle_label()}）\n",
                           lambda i=turn.index: self._close_middle(i))
                for entry in turn.middle:
                    self._insert_middle_entry(entry, query)
            else:
                self._link(f"　▸ 中間過程：{turn.middle_label()}\n",
                           lambda i=turn.index: self._open_middle(i))

        if turn.reply is not None:
            self._insert_who("Claude", "who_asst", turn.reply.time_label(), extra="最後回覆")
            self._insert_body(turn.reply, "body", query, REPLY_COLLAPSE_LINES)
        elif turn.prompt is not None:
            self.text.insert(tk.END, "（這一輪還沒有回覆——可能就是卡在這裡）\n", "system")

    def _insert_middle_entry(self, entry: tx.Entry, query: str) -> None:
        if entry.kind == tx.KIND_THINKING and not self.show_thinking.get():
            return
        if entry.kind == tx.KIND_SYSTEM:
            self.text.insert(tk.END, f"— {tx.first_line(entry.text, 60)} —\n", "system")
            if entry.text.startswith("【壓縮摘要】"):
                self._insert_body(entry, "mid_body", query, MIDDLE_COLLAPSE_LINES)
            return
        if entry.kind == tx.KIND_TOOLS:
            self.text.insert(tk.END, f"　　{entry.text}", "mid_body")
            stamp = entry.time_label()
            if stamp:
                self.text.insert(tk.END, f"   {stamp}", "meta")
            self.text.insert(tk.END, "\n")
            return
        who, who_tag, body_tag = {
            tx.KIND_ASSISTANT: ("Claude（中途）", "who_mid", "mid_body"),
            tx.KIND_THINKING: ("思考", "who_think", "think_body"),
        }[entry.kind]
        self.text.insert(tk.END, "　　" + who, who_tag)
        stamp = entry.time_label()
        if stamp:
            self.text.insert(tk.END, f"   {stamp}", "meta")
        self.text.insert(tk.END, "\n")
        self._insert_body(entry, body_tag, query, MIDDLE_COLLAPSE_LINES)

    def _insert_who(self, who: str, tag: str, stamp: str, extra: str = "") -> None:
        self.text.insert(tk.END, who, tag)
        bits = [b for b in (stamp, extra) if b]
        if bits:
            self.text.insert(tk.END, "   " + "   ".join(bits), "meta")
        self.text.insert(tk.END, "\n")

    def _insert_body(self, entry: tx.Entry, body_tag: str, query: str, limit: int) -> None:
        lines = entry.text.splitlines() or [""]
        collapsed = (len(lines) > limit and entry.index not in self._expanded_entries and not query)
        shown_lines = lines[:limit] if collapsed else lines
        start = self.text.index(tk.END)
        self.text.insert(tk.END, "\n".join(shown_lines) + "\n", body_tag)
        if query:
            self._highlight(start, query)
        if collapsed:
            rest = len(lines) - limit
            self._link(f"　▾ 展開全文（還有 {rest} 行）\n",
                       lambda i=entry.index: self._expand_entry(i))

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

    def _mark(self, index: int) -> None:
        name = f"turn{index}"
        self.text.mark_set(name, self.text.index(tk.END))
        self.text.mark_gravity(name, "left")
        self._turn_marks[index] = name

    def _rerender_at(self, index: int) -> None:
        self._render()
        mark = self._turn_marks.get(index)
        if mark:
            self.text.see(mark)

    def _expand_entry(self, index: int) -> None:
        self._expanded_entries.add(index)
        turn = next((t for t in self.transcript.turns if any(e.index == index for e in t.entries)), None)
        self._rerender_at(turn.index if turn else index)

    def _open_turn(self, index: int) -> None:
        self._open_turns.add(index)
        self._rerender_at(index)

    def _open_middle(self, index: int) -> None:
        self._open_middles.add(index)
        self._rerender_at(index)

    def _close_middle(self, index: int) -> None:
        self._open_middles.discard(index)
        self._rerender_at(index)

    def _open_all_middles(self) -> None:
        self._open_turns = {t.index for t in self.transcript.turns}
        self._open_middles = {t.index for t in self.transcript.turns if t.middle}
        self._render()

    def _set_text(self, message: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", tk.END)
        self.text.insert(tk.END, message)
        self.text.configure(state="disabled")

    # ---------- 動作 ----------

    def _choose(self) -> None:
        self.app.select_session(self.session)
        self.destroy()
