# -*- coding: utf-8 -*-
"""app.py — ClaudeResumer 的主視窗：挑對話 → 設定時間 → 排進佇列 → 時間到送出。

這支跟 resetter（claude_timer.resetter）是兩件不同的事，別混用：

    resetter   「主動排重置」：token 卡住時反覆做一件便宜的事（探針），把 5 小時
               視窗的重置時鐘催起來。它會查用量、算時間、決定打不打。
    resumer    「已知重置點、準時送」：你已經知道 token 幾點恢復，就設在那個時間點，
               時間一到直接把那個對話接著送出去。你設的時間就是最終決定，這支工具
               不去二次確認用量。

最重要的一條鐵律：送出的那一刻，那個對話不能開在 VSCode（或任何地方）裡。Claude Code
的 session 同一時間只能有一個持有者，重複開會回「Session ID ... is already in use」
然後秒退。本工具靠 ~/.claude/sessions/<pid>.json 在不花任何 token 的前提下偵測這件事，
被佔用就擋下來、不白送；自動模式下你一關掉它就會自己補送。

送出的是一個獨立行程（DETACHED_PROCESS），所以確認送出之後把這支工具關掉，被接續的
對話還是會在背景跑完；之後回 VSCode 開同一個對話就看得到那一段。
"""
from __future__ import annotations

import sys
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk

from .. import __version__
from ..paths import resumer_log
from ..ui import theme
from ..ui.scroll import ScrollableFrame
from ..ui.theme import COLORS
from . import journal
from .sessions import (
    DEFAULT_LIMIT, SessionInfo, active_holders, describe_holder, force_take_over,
    scan_sessions, session_holder,
)
from .tasks import (
    CONFIRM_SECONDS, PERMISSION_HELP, PERMISSION_MODES, STATUS_FAILED, STATUS_LOCKED,
    STATUS_MANUAL, STATUS_SENDING, STATUS_WAITING, Task, preflight,
)
from .viewer import ConversationViewer

RULE_LINE = ("鐵律：送出的那一刻，該對話不能開在 VSCode 裡（session 會被鎖住）。"
             "本工具會先幫你偵測並擋下來，不花 token，關掉之後自動補送。")


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.fonts = theme.apply(self)
        self.title(f"Claude 對話接續排程器 v{__version__}")
        self.configure(bg=COLORS["bg"])
        # 想要的尺寸放不進螢幕就自動縮小；縮小之後內容靠捲軸仍然看得到（見 ui/scroll.py）。
        theme.fit_on_screen(self, 1180, 940, 720, 520)

        self._closing = False
        self._tick_id: str | None = None
        self.sessions: list[SessionInfo] = []
        self.total_sessions = 0
        self.limit = DEFAULT_LIMIT
        self._visible: list[SessionInfo] = []
        self._selected: SessionInfo | None = None
        self.tasks: list[Task] = []

        self._build_ui()
        self._reload_sessions(announce=False)
        self._refresh_perm_help()
        self._sync_time_format()
        self._say(f"就緒。掃到 {self.total_sessions} 個對話，紀錄檔：{resumer_log()}")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tick_id = self.after(250, self._tick)

    # ---------- 版面 ----------

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(14, 12, 14, 6))
        header.pack(fill="x")
        ttk.Label(header, text="Claude 對話接續排程器", style="H1.TLabel").pack(anchor="w")
        ttk.Label(header, text="時間一到，就把你挑的那個對話接著送出去。不查用量、不壓著等——"
                              "你設的時間就是決定。",
                  style="MutedBg.TLabel", wraplength=1000, justify="left").pack(anchor="w", pady=(2, 0))
        ttk.Label(header, text=RULE_LINE, style="MutedBg.TLabel", wraplength=1000,
                  justify="left").pack(anchor="w", pady=(2, 0))

        self.scroller = ScrollableFrame(self, padding=(14, 8))
        self.scroller.pack(fill="both", expand=True)
        body = self.scroller.body

        self._build_session_card(body)
        self._build_form_card(body)
        self._build_queue_card(body)
        self._build_log_card(body)

        bar = ttk.Frame(self, padding=(14, 8))
        bar.pack(fill="x")
        self.status_var = tk.StringVar(value="就緒")
        ttk.Label(bar, textvariable=self.status_var, style="MutedBg.TLabel").pack(anchor="w")

    def _card(self, parent, title: str) -> ttk.Labelframe:
        card = ttk.Labelframe(parent, text=title, style="Card.TLabelframe", padding=12)
        card.pack(fill="x", pady=(0, 12))
        return card

    def _build_session_card(self, parent) -> None:
        card = self._card(parent, "① 選哪一個對話")
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="篩選：", style="Card.TLabel").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._refresh_session_list())
        ttk.Entry(row, textvariable=self.filter_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="重新整理", command=lambda: self._reload_sessions()).pack(side="left")
        ttk.Button(row, text="查看內容", command=self._view_conversation).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="檢查可否送出", command=self._diagnose).pack(side="left", padx=(6, 0))

        wrap = ttk.Frame(card, style="Card.TFrame")
        wrap.pack(fill="both", expand=True, pady=(8, 0))
        columns = ("when", "project", "title", "last", "state")
        self.session_tree = ttk.Treeview(wrap, columns=columns, show="headings", height=9,
                                         selectmode="browse")
        for key, label, width, stretch in (
            ("when", "最後活動", 90, False),
            ("project", "專案", 160, False),
            ("title", "對話標題", 240, True),
            ("last", "最後一句", 320, True),
            ("state", "狀態", 90, False),
        ):
            self.session_tree.heading(key, text=label)
            self.session_tree.column(key, width=width, stretch=stretch, anchor="w")
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.session_tree.yview)
        self.session_tree.configure(yscrollcommand=bar.set)
        self.session_tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="left", fill="y")
        self.session_tree.tag_configure("open", foreground=COLORS["warn"])
        self.session_tree.tag_configure("odd", background=COLORS["zebra"])
        self.session_tree.bind("<Double-Button-1>", lambda _e: self._view_conversation())
        self.session_tree.bind("<<TreeviewSelect>>", self._on_session_select)

        foot = ttk.Frame(card, style="Card.TFrame")
        foot.pack(fill="x", pady=(6, 0))
        self.session_hint_var = tk.StringVar(value="")
        ttk.Label(foot, textvariable=self.session_hint_var, style="Muted.TLabel").pack(side="left")
        self.more_button = ttk.Button(foot, text="載入更多", command=self._load_more)
        self.more_button.pack(side="right")
        ttk.Label(card, text="不確定是不是這個？雙擊該列先看內容（摘要、時段、往來訊息都在裡面，不花錢）。"
                             "標成「開啟中」的代表現在正開在 VSCode，送出前要先關掉。",
                  style="Muted.TLabel", wraplength=1000, justify="left").pack(anchor="w", pady=(6, 0))

    def _build_form_card(self, parent) -> None:
        card = self._card(parent, "② 時間到要送出什麼")
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text="接著送出的內容：", style="Card.TLabel").grid(row=0, column=0, sticky="nw")
        self.prompt_text = tk.Text(card, height=4, width=60, borderwidth=1, relief="solid",
                                   highlightthickness=0, font=self.fonts.body, bg="#ffffff",
                                   fg=COLORS["text"])
        self.prompt_text.insert("1.0", "continue")
        self.prompt_text.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(8, 0))
        prompt_row = ttk.Frame(card, style="Card.TFrame")
        prompt_row.grid(row=1, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(4, 0))
        ttk.Button(prompt_row, text="附加檔案…", command=self._attach_file).pack(side="left")
        ttk.Label(prompt_row, text="預設 continue（＝請它接著做下去）。附加檔案會用 @路徑 的寫法加進去。",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))

        ttk.Label(card, text="送出時間：", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=(12, 0))
        time_row = ttk.Frame(card, style="Card.TFrame")
        time_row.grid(row=2, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(12, 0))
        self.time_var = tk.StringVar()
        ttk.Entry(time_row, textvariable=self.time_var, width=12,
                  font=self.fonts.big_entry).pack(side="left")
        self.seconds_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(time_row, text="精準到秒", variable=self.seconds_var,
                        command=self._sync_time_format).pack(side="left", padx=(10, 0))
        self.clock_var = tk.StringVar(value="")
        ttk.Label(time_row, textvariable=self.clock_var, style="Muted.TLabel").pack(side="left", padx=(14, 0))
        ttk.Button(time_row, text="＝現在", command=self._set_time_now).pack(side="left", padx=(10, 0))
        ttk.Button(time_row, text="+1 分", command=lambda: self._bump_time(60)).pack(side="left", padx=(4, 0))
        ttk.Label(card, text="填 Claude 告訴你的重置時間即可（HH:MM 或 HH:MM:SS）。已經過了今天這個時間的話，"
                             "會自動算成明天。",
                  style="Muted.TLabel", wraplength=920, justify="left").grid(
            row=3, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(2, 0))

        ttk.Label(card, text="送出方式：", style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=(12, 0))
        self.mode_var = tk.StringVar(value="auto")
        mode_box = ttk.Frame(card, style="Card.TFrame")
        mode_box.grid(row=4, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(12, 0))
        ttk.Radiobutton(mode_box, text="自動送出（建議）", variable=self.mode_var,
                        value="auto").pack(side="left")
        ttk.Radiobutton(mode_box, text="時間到只提醒我，我自己按", variable=self.mode_var,
                        value="manual").pack(side="left", padx=(12, 0))
        ttk.Label(card, text="自動送出＝時間一到就直接送；被 VSCode 佔用時會擋下來，等你關掉後自動補送。"
                             "另一個選項只會在時間到時提醒你，要按下面的「立即送出」才送。",
                  style="Muted.TLabel", wraplength=920, justify="left").grid(
            row=5, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(2, 0))

        ttk.Label(card, text="權限模式：", style="Card.TLabel").grid(row=6, column=0, sticky="w", pady=(12, 0))
        self.perm_var = tk.StringVar(value=PERMISSION_MODES[0])
        combo = ttk.Combobox(card, textvariable=self.perm_var, state="readonly",
                             values=PERMISSION_MODES, width=20)
        combo.grid(row=6, column=1, sticky="w", padx=(8, 0), pady=(12, 0))
        combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_perm_help())
        self.perm_help_var = tk.StringVar()
        ttk.Label(card, textvariable=self.perm_help_var, style="Muted.TLabel",
                  wraplength=920, justify="left").grid(
            row=7, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(4, 0))

        self.selected_var = tk.StringVar(value="尚未選擇對話")
        ttk.Label(card, textvariable=self.selected_var, style="Muted.TLabel",
                  wraplength=700, justify="left").grid(row=8, column=0, columnspan=2,
                                                       sticky="w", pady=(14, 0))
        ttk.Button(card, text="加入排程佇列", style="Accent.TButton",
                   command=self._add_task).grid(row=8, column=3, sticky="e", pady=(14, 0))

    def _build_queue_card(self, parent) -> None:
        card = self._card(parent, "③ 排程佇列")
        wrap = ttk.Frame(card, style="Card.TFrame")
        wrap.pack(fill="both", expand=True)
        columns = ("when", "project", "prompt", "mode", "state")
        self.queue_tree = ttk.Treeview(wrap, columns=columns, show="headings", height=6,
                                       selectmode="browse")
        for key, label, width, stretch in (
            ("when", "送出時間", 130, False),
            ("project", "專案", 170, False),
            ("prompt", "送出內容", 260, True),
            ("mode", "方式", 90, False),
            ("state", "狀態", 220, False),
        ):
            self.queue_tree.heading(key, text=label)
            self.queue_tree.column(key, width=width, stretch=stretch, anchor="w")
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=bar.set)
        self.queue_tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="left", fill="y")
        self.queue_tree.tag_configure("failed", foreground=COLORS["err"])
        self.queue_tree.tag_configure("locked", foreground=COLORS["warn"])
        self.queue_tree.tag_configure("sent", foreground=COLORS["ok"])
        self.queue_tree.bind("<<TreeviewSelect>>", lambda _e: self._refresh_queue_detail())

        self.queue_detail_var = tk.StringVar(value="")
        ttk.Label(card, textvariable=self.queue_detail_var, style="Muted.TLabel",
                  wraplength=1000, justify="left").pack(anchor="w", pady=(6, 0))

        btns = ttk.Frame(card, style="Card.TFrame")
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="移除選取", command=self._remove_task).pack(side="left")
        ttk.Button(btns, text="立即送出", command=self._send_now_selected).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="強制接管並送出", style="Danger.TButton",
                   command=self._force_take_over_selected).pack(side="left", padx=(6, 0))
        self.autoclose_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(btns, text="全部送出後自動關閉視窗", variable=self.autoclose_var).pack(side="right")
        ttk.Label(card, text="「強制接管」會直接關掉正開著那個對話的行程（未存的東西會一起沒掉），趕時間才用；"
                             "平常讓它被擋下、你關掉 VSCode 後自動補送就好。",
                  style="Muted.TLabel", wraplength=1000, justify="left").pack(anchor="w", pady=(6, 0))

    def _build_log_card(self, parent) -> None:
        card = self._card(parent, "狀況 / 錯誤")
        wrap = ttk.Frame(card, style="Card.TFrame")
        wrap.pack(fill="both", expand=True)
        self.log_text = tk.Text(wrap, height=7, wrap="word", state="disabled", borderwidth=0,
                                highlightthickness=0, font=self.fonts.mono,
                                bg=COLORS["log_bg"], fg=COLORS["log_fg"],
                                insertbackground=COLORS["log_fg"])
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=bar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        bar.pack(side="left", fill="y")
        ttk.Label(card, text=f"同樣的訊息也會寫進 {resumer_log()}", style="Muted.TLabel",
                  wraplength=1000, justify="left").pack(anchor="w", pady=(6, 0))

    # ---------- 對話清單 ----------

    def _reload_sessions(self, *, announce: bool = True) -> None:
        self.sessions, self.total_sessions = scan_sessions(self.limit)
        self._refresh_session_list()
        if announce:
            self._say(f"重新掃到 {self.total_sessions} 個對話（顯示最近 {len(self.sessions)} 個）")

    def _load_more(self) -> None:
        self.limit += DEFAULT_LIMIT
        self._reload_sessions()

    def _refresh_session_list(self) -> None:
        query = self.filter_var.get().strip().lower()
        holders = active_holders()
        self.session_tree.delete(*self.session_tree.get_children())
        self._visible = [
            s for s in self.sessions
            if not query or query in s.cwd.lower() or query in s.title.lower()
            or query in s.last_prompt.lower()
        ]
        for i, s in enumerate(self._visible):
            s.holder = holders.get(s.session_id)
            tags = ["open"] if s.holder else (["odd"] if i % 2 else [])
            self.session_tree.insert(
                "", "end", iid=s.session_id, tags=tags,
                values=(s.relative_time, s.project, s.title, s.last_prompt,
                        "● 開啟中" if s.holder else ""),
            )
        shown, total = len(self._visible), self.total_sessions
        self.session_hint_var.set(
            f"顯示 {shown} 個對話（本機共 {total} 個，目前載入最近 {len(self.sessions)} 個）")
        if len(self.sessions) >= total:
            self.more_button.state(["disabled"])
        else:
            self.more_button.state(["!disabled"])
        if self._selected is not None and self._selected.session_id in self.session_tree.get_children():
            self.session_tree.selection_set(self._selected.session_id)

    def _on_session_select(self, _event=None) -> None:
        sel = self.session_tree.selection()
        if not sel:
            return
        self._selected = next((s for s in self._visible if s.session_id == sel[0]), None)
        if self._selected is not None:
            self.selected_var.set(f"已選：{self._selected.project}｜{self._selected.title}")

    def _selected_session(self) -> SessionInfo | None:
        if self._selected is None:
            messagebox.showwarning("尚未選擇對話", "請先在上面的清單挑一個對話", parent=self)
        return self._selected

    def select_session(self, session: SessionInfo) -> None:
        """從預覽視窗的「就選這個對話」回來：在清單裡把它選起來（必要時清掉篩選）。"""
        if session not in self._visible:
            self.filter_var.set("")
            self._refresh_session_list()
        if session.session_id in self.session_tree.get_children():
            self.session_tree.selection_set(session.session_id)
            self.session_tree.see(session.session_id)
        self._selected = session
        self.selected_var.set(f"已選：{session.project}｜{session.title}")

    def _view_conversation(self) -> None:
        session = self._selected_session()
        if session is not None:
            ConversationViewer(self, session)

    def _diagnose(self) -> None:
        """不花錢的送出前檢查：靜態 preflight + 動態「有沒有被佔用」。"""
        session = self._selected_session()
        if session is None:
            return
        problems = preflight(session)
        holder = session_holder(session.session_id)
        if holder:
            problems.append(f"這個對話{describe_holder(holder)}——現在送會被鎖住而失敗，請先把它關掉")
        if problems:
            for p in problems:
                self._say(f"[檢查] ✗ {p}")
        else:
            self._say(f"[檢查] ✓ 可以送：{session.project}（{session.session_id}，目前沒有被佔用）")

    def _attach_file(self) -> None:
        path = filedialog.askopenfilename(title="選擇要附加的檔案", parent=self)
        if path:
            self.prompt_text.insert(tk.END, f' @"{path}"')

    # ---------- 時間欄位 ----------

    def _time_fmt(self) -> str:
        return "%H:%M:%S" if self.seconds_var.get() else "%H:%M"

    def _set_time_now(self) -> None:
        self.time_var.set(datetime.now().strftime(self._time_fmt()))

    def _bump_time(self, seconds: int) -> None:
        try:
            base = self._parse_time(self.time_var.get())
        except ValueError:
            base = datetime.now()
        self.time_var.set((base + timedelta(seconds=seconds)).strftime(self._time_fmt()))

    def _sync_time_format(self) -> None:
        raw = self.time_var.get().strip()
        try:
            when = self._parse_time(raw) if raw else (datetime.now() + timedelta(minutes=5))
        except ValueError:
            when = datetime.now() + timedelta(minutes=5)
        self.time_var.set(when.strftime(self._time_fmt()))

    @staticmethod
    def _parse_time(raw: str) -> datetime:
        """接受 HH:MM 或 HH:MM:SS，回傳今天（或明天）的那個時間點。"""
        parts = raw.strip().split(":")
        if len(parts) == 2:
            hh, mm, ss = int(parts[0]), int(parts[1]), 0
        elif len(parts) == 3:
            hh, mm, ss = int(parts[0]), int(parts[1]), int(parts[2])
        else:
            raise ValueError(raw)
        if not (0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60):
            raise ValueError(raw)
        now = datetime.now()
        when = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
        return when

    # ---------- 佇列 ----------

    def _add_task(self) -> None:
        session = self._selected_session()
        if session is None:
            return
        prompt = self.prompt_text.get("1.0", tk.END).strip()
        if not prompt:
            messagebox.showwarning("內容是空的", "請輸入時間到要接著送出的內容", parent=self)
            return
        try:
            when = self._parse_time(self.time_var.get())
        except ValueError:
            messagebox.showerror("時間格式錯誤", "請用 HH:MM 或 HH:MM:SS，例如 18:41 或 18:41:30",
                                 parent=self)
            return

        task = Task(session, prompt, when, self.mode_var.get(), self.perm_var.get())
        self.tasks.append(task)
        self._refresh_queue()
        self._say(f"已排入 {when.strftime('%m-%d %H:%M:%S')}：{session.project}（task={task.id}）")
        for p in preflight(session):
            self._say(f"[警告] 這筆現在就有問題，時間到大概會失敗：{p}")

    def _refresh_queue(self) -> None:
        selected = self.queue_tree.selection()
        self.queue_tree.delete(*self.queue_tree.get_children())
        for task in self.tasks:
            tag = ""
            if task.status == STATUS_FAILED:
                tag = "failed"
            elif task.status == STATUS_LOCKED:
                tag = "locked"
            elif task.done:
                tag = "sent"
            self.queue_tree.insert("", "end", iid=task.id, values=task.row(),
                                   tags=([tag] if tag else []))
        for iid in selected:
            if iid in self.queue_tree.get_children():
                self.queue_tree.selection_set(iid)
        self._refresh_queue_detail()

    def _refresh_queue_detail(self) -> None:
        task = self._selected_task(quiet=True)
        if task is None:
            self.queue_detail_var.set("")
            return
        detail = f"{task.session.project}｜{task.session.title}"
        if task.error:
            detail += f"\n{task.status}：{task.error}"
        self.queue_detail_var.set(detail)

    def _selected_task(self, *, quiet: bool = False) -> Task | None:
        sel = self.queue_tree.selection()
        if not sel:
            if not quiet:
                messagebox.showinfo("尚未選擇", "請先在佇列裡選一筆", parent=self)
            return None
        return next((t for t in self.tasks if t.id == sel[0]), None)

    def _remove_task(self) -> None:
        task = self._selected_task()
        if task is None:
            return
        self.tasks.remove(task)
        self._refresh_queue()
        self._say(f"已移除 task={task.id}")

    def _send_now_selected(self) -> None:
        task = self._selected_task()
        if task is None:
            return
        if task.settled:
            self._say(f"task={task.id} 已經是最終狀態（{task.status}），不重送")
            return
        self._fire(task, note="手動立即送出")
        self._refresh_queue()

    def _force_take_over_selected(self) -> None:
        """主動搶占選取任務的 session：關掉持有它的行程後再送出。破壞性，先跳確認。"""
        task = self._selected_task()
        if task is None:
            return
        if task.settled:
            self._say(f"task={task.id} 已經是最終狀態（{task.status}），不重送")
            return
        holder = session_holder(task.session.session_id)
        if holder is None:
            self._fire(task, note="強制接管（其實沒被佔用，直接送）")
            self._refresh_queue()
            return
        if not messagebox.askyesno(
            "確認強制接管",
            f"這個對話{describe_holder(holder)}。\n\n"
            f"強制接管會直接關閉那個行程（pid {holder['pid']}），"
            f"它裡面沒存或正在跑的東西會一起遺失。\n\n確定要接管並送出嗎？",
            parent=self,
        ):
            return
        ok, message = force_take_over(task.session.session_id)
        self._say(f"[接管] task={task.id}：{message}")
        if ok:
            self._fire(task, note="接管後送出")
        self._refresh_queue()

    def _fire(self, task: Task, *, note: str) -> None:
        self._say(f"{note}：task={task.id} → {task.session.project}")
        if task.fire():
            self._say(f"已啟動 task={task.id}，盯 {CONFIRM_SECONDS} 秒確認沒有馬上失敗")
        elif task.status == STATUS_LOCKED:
            self._say(f"[擋下] task={task.id}：{task.error}")
        else:
            self._say(f"[失敗] task={task.id}：{task.error}")

    # ---------- 畫面上的紀錄 ----------

    def _say(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"{datetime.now().strftime('%H:%M:%S')}  {line}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")
        journal.write(line)

    def _refresh_perm_help(self) -> None:
        self.perm_help_var.set(PERMISSION_HELP.get(self.perm_var.get(), ""))

    # ---------- 計時迴圈 ----------

    def _tick(self) -> None:
        # 視窗已經關掉時 after 還是可能再觸發一次，這裡擋掉（否則 Tk 會丟
        # invalid command name ... 的背景錯誤）。
        if self._closing or not self.winfo_exists():
            return
        now = datetime.now()
        self.clock_var.set("現在 " + now.strftime("%H:%M:%S"))
        changed = False

        for t in self.tasks:
            if t.status == STATUS_SENDING:
                changed |= t.poll()
                if t.status == STATUS_FAILED:
                    self._say(f"[失敗] task={t.id}：{t.error}")
                elif t.done:
                    self._say(f"[完成] task={t.id} 已確認送出")
                continue

            # 可以被（自動）觸發的狀態：還在等、或上次被佔用擋下。
            if t.status not in (STATUS_WAITING, STATUS_LOCKED):
                continue
            if now < t.when:
                continue

            if t.mode == "manual" and t.status == STATUS_WAITING:
                t.status = STATUS_MANUAL
                self._say(f"task={t.id} 時間到了，等你按「立即送出」")
                changed = True
                continue
            if t.mode == "manual":
                continue  # 手動模式被鎖住不自動補送，等使用者自己按

            was_locked = t.status == STATUS_LOCKED
            previous_error = t.error
            # 時間一到就送。你設的時間就是你判斷的重置點——這裡不查用量、不壓著等。
            fired = t.fire()
            changed = True
            if fired:
                self._say(f"時間到，自動送出：task={t.id} → {t.session.project}")
                self._say(f"已啟動 task={t.id}，盯 {CONFIRM_SECONDS} 秒確認沒有馬上失敗")
            elif t.status == STATUS_LOCKED:
                # 只在第一次、或訊息有變時說一次，免得每秒洗版。
                if not was_locked or t.error != previous_error:
                    self._say(f"[擋下] task={t.id}：{t.error}")
            else:
                self._say(f"[失敗] task={t.id}：{t.error}")

        if changed:
            self._refresh_queue()
        self._update_status()
        self._maybe_close()
        if not self._closing:
            self._tick_id = self.after(1000, self._tick)

    def _update_status(self) -> None:
        if not self.tasks:
            self.status_var.set("佇列是空的。挑一個對話、設好時間，再按「加入排程佇列」。")
            return
        pending = sum(1 for t in self.tasks if not t.settled)
        locked = sum(1 for t in self.tasks if t.status == STATUS_LOCKED)
        failed = sum(1 for t in self.tasks if t.status == STATUS_FAILED)
        parts = [f"待處理 {pending}"]
        if locked:
            parts.append(f"被佔用 {locked}（關掉 VSCode 那個對話就會自動補送）")
        if failed:
            parts.append(f"失敗 {failed}（視窗不會自動關，錯誤在上面的狀況欄）")
        self.status_var.set("｜".join(parts))

    def _maybe_close(self) -> None:
        # 只有「每一筆都確認送出」且使用者允許自動關閉時才關。有任何一筆失敗就把視窗
        # 留著，否則使用者永遠看不到失敗原因。
        if self._closing or not self.tasks or not self.autoclose_var.get():
            return
        if all(t.done for t in self.tasks):
            self._closing = True
            self.status_var.set("全部已確認送出，3 秒後自動關閉…")
            self.after(3000, self.destroy)

    def _on_close(self) -> None:
        pending = [t for t in self.tasks if not t.settled]
        if pending and not messagebox.askyesno(
            "還有未送出的任務",
            f"還有 {len(pending)} 筆任務尚未確認送出，關閉後就不會執行了，確定要關閉嗎？",
            parent=self,
        ):
            return
        self._closing = True
        if self._tick_id is not None:
            self.after_cancel(self._tick_id)
        self.destroy()


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
