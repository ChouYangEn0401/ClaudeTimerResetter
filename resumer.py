# -*- coding: utf-8 -*-
"""resumer.py — 定時接續指定 Claude Code 對話的獨立小工具。

跟 installer.py（背景反覆探針，見 README「設計理念」一節）是完全獨立的兩個程式：
    installer.py  常駐/排程，反覆執行同一件便宜的事（ping.py 的探針）
    resumer.py    手動打開，一次性排定「時間到了幫我把某個特定對話接著送出去」，
                  送完（或佇列清空）就自己關掉，不常駐、不追蹤後續結果

用法：被 Claude Code 的 session limit 卡住、畫面顯示大概幾點會重置時，打開這支工具，
從清單挑出被卡住的那個對話、寫（或用預設 "continue"）要接著送出的內容、設定重置時間，
選好「自動送出」還是「時間到手動確認再送出」，加入佇列。時間到了就用一個 detached 的
claude.exe 行程去 `--resume <session_id> --print "<prompt>"`，不等它回應、不管後續
（GUI 關掉之後那個接續的對話行程還是會繼續在背景跑，不會被這支工具的生命週期綁住）。
佇列裡的任務全部送出後，視窗會自動關閉。

之後要接著看那個對話在做什麼，使用者自己回 VSCode 重新開啟即可——這支工具只負責
「準時把 continue 送出去」這一件事。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tkinter as tk
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_DATA_DIR = Path(os.environ["LOCALAPPDATA"]) / "ClaudeTimerResetter"
LOG_PATH = APP_DATA_DIR / "resumer.log"

PERMISSION_MODES = ["(不覆寫)", "acceptEdits", "bypassPermissions", "dontAsk", "plan"]


def _claude_binary() -> str:
    from claude_subscription import find_claude_binary
    return find_claude_binary()


def _append_log(line: str) -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------- 掃描本機所有 Claude Code 對話 ----------

def scan_sessions() -> list[dict]:
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        return []
    sessions = []
    for jsonl_path in root.glob("*/*.jsonl"):
        info = _peek_session(jsonl_path)
        if info is not None:
            sessions.append(info)
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


def _peek_session(jsonl_path: Path) -> dict | None:
    cwd = None
    preview = None
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 200:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if cwd is None and rec.get("cwd"):
                    cwd = rec["cwd"]
                if preview is None and rec.get("type") == "user":
                    text = _extract_text(rec.get("message", {}).get("content"))
                    if text:
                        preview = text
                if cwd and preview:
                    break
    except OSError:
        return None
    if preview is None:
        return None  # 沒有真人打字內容的（例如純 queue-operation）不算是一個對話
    try:
        mtime = jsonl_path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return {
        "session_id": jsonl_path.stem,
        "cwd": cwd or "(未知專案路徑)",
        "preview": preview[:80],
        "mtime": mtime,
        "last_active": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
    }


def _extract_text(content) -> str | None:
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                return block["text"].strip()
    return None


# ---------- 排程佇列 ----------

class Task:
    def __init__(self, session: dict, prompt: str, when: datetime, mode: str, permission_mode: str) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.session = session
        self.prompt = prompt
        self.when = when
        self.mode = mode  # "auto" | "manual"
        self.permission_mode = permission_mode  # "(不覆寫)" 表示不加這個參數
        self.status = "等待中"

    def label(self) -> str:
        return (
            f"[{self.when.strftime('%H:%M')}] {self.session['cwd']} | "
            f"{self.session['preview'][:30]} | {self.mode} | {self.status}"
        )

    def fire(self) -> None:
        exe = _claude_binary()
        args = [exe, "--resume", self.session["session_id"], "--print", self.prompt]
        if self.permission_mode and self.permission_mode != PERMISSION_MODES[0]:
            args += ["--permission-mode", self.permission_mode]
        with open(LOG_PATH, "a", encoding="utf-8") as log:
            subprocess.Popen(
                args,
                stdout=log,
                stderr=log,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
        self.status = "已送出"
        _append_log(
            f"{datetime.now().isoformat(timespec='seconds')} FIRE "
            f"session={self.session['session_id']} prompt={self.prompt[:60]!r}"
        )


# ---------- GUI ----------

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Claude 對話接續排程器")
        self.geometry("760x640")
        self._closing = False
        self.sessions = scan_sessions()
        self._visible_sessions: list[dict] = []
        self.tasks: list[Task] = []

        top = ttk.LabelFrame(self, text="選擇對話", padding=8)
        top.pack(fill="both", expand=True, padx=10, pady=8)

        filter_row = ttk.Frame(top)
        filter_row.pack(fill="x")
        ttk.Label(filter_row, text="篩選:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self._refresh_session_list())
        ttk.Entry(filter_row, textvariable=self.filter_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(filter_row, text="重新整理", command=self._reload_sessions).pack(side="left")

        self.session_list = tk.Listbox(top, height=10)
        self.session_list.pack(fill="both", expand=True, pady=(6, 0))

        form = ttk.LabelFrame(self, text="新增排程任務", padding=8)
        form.pack(fill="x", padx=10, pady=8)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Prompt:").grid(row=0, column=0, sticky="nw")
        self.prompt_text = tk.Text(form, height=4, width=60)
        self.prompt_text.insert("1.0", "continue")
        self.prompt_text.grid(row=0, column=1, columnspan=3, sticky="ew", padx=6)
        ttk.Button(form, text="附加檔案...", command=self._attach_file).grid(row=1, column=1, sticky="w", padx=6, pady=(2, 0))

        ttk.Label(form, text="時間 (HH:MM):").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.time_var = tk.StringVar(value=(datetime.now() + timedelta(minutes=5)).strftime("%H:%M"))
        ttk.Entry(form, textvariable=self.time_var, width=10).grid(row=2, column=1, sticky="w", padx=6, pady=(6, 0))

        ttk.Label(form, text="送出方式:").grid(row=2, column=2, sticky="w", pady=(6, 0))
        self.mode_var = tk.StringVar(value="auto")
        mode_box = ttk.Frame(form)
        mode_box.grid(row=2, column=3, sticky="w", pady=(6, 0))
        ttk.Radiobutton(mode_box, text="自動送出", variable=self.mode_var, value="auto").pack(side="left")
        ttk.Radiobutton(mode_box, text="時間到手動確認", variable=self.mode_var, value="manual").pack(side="left")

        ttk.Label(form, text="權限模式 (選填):").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.perm_var = tk.StringVar(value=PERMISSION_MODES[0])
        ttk.Combobox(
            form, textvariable=self.perm_var, state="readonly", values=PERMISSION_MODES, width=18
        ).grid(row=3, column=1, sticky="w", padx=6, pady=(6, 0))
        ttk.Button(form, text="加入排程佇列", command=self._add_task).grid(row=3, column=3, sticky="e", pady=(6, 0))

        queue_frame = ttk.LabelFrame(self, text="排程佇列（全部送出後自動關閉）", padding=8)
        queue_frame.pack(fill="both", expand=True, padx=10, pady=8)
        self.queue_list = tk.Listbox(queue_frame, height=8)
        self.queue_list.pack(fill="both", expand=True, side="left")
        qbtns = ttk.Frame(queue_frame)
        qbtns.pack(side="left", fill="y", padx=6)
        ttk.Button(qbtns, text="移除選取", command=self._remove_task).pack(fill="x")
        ttk.Button(qbtns, text="立即送出選取", command=self._send_now_selected).pack(fill="x", pady=(6, 0))

        self.status_var = tk.StringVar(value="就緒")
        ttk.Label(self, textvariable=self.status_var, foreground="#666").pack(anchor="w", padx=10, pady=(0, 8))

        self._refresh_session_list()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(1000, self._tick)

    # -- session list --

    def _refresh_session_list(self) -> None:
        q = self.filter_var.get().strip().lower()
        self.session_list.delete(0, tk.END)
        self._visible_sessions = [
            s for s in self.sessions if not q or q in s["cwd"].lower() or q in s["preview"].lower()
        ]
        for s in self._visible_sessions:
            self.session_list.insert(tk.END, f"{s['last_active']} | {s['cwd']} | {s['preview']}")

    def _reload_sessions(self) -> None:
        self.sessions = scan_sessions()
        self._refresh_session_list()

    def _attach_file(self) -> None:
        path = filedialog.askopenfilename(title="選擇要附加的檔案", parent=self)
        if path:
            self.prompt_text.insert(tk.END, f' @"{path}"')

    # -- queue --

    def _add_task(self) -> None:
        sel = self.session_list.curselection()
        if not sel:
            messagebox.showwarning("尚未選擇對話", "請先在上面選一個對話", parent=self)
            return
        session = self._visible_sessions[sel[0]]
        prompt = self.prompt_text.get("1.0", tk.END).strip()
        if not prompt:
            messagebox.showwarning("Prompt 是空的", "請輸入要接續送出的內容", parent=self)
            return
        try:
            hh, mm = self.time_var.get().split(":")
            when = datetime.now().replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            if when <= datetime.now():
                when += timedelta(days=1)
        except ValueError:
            messagebox.showerror("時間格式錯誤", "請用 HH:MM，例如 18:41", parent=self)
            return
        self.tasks.append(Task(session, prompt, when, self.mode_var.get(), self.perm_var.get()))
        self._refresh_queue()

    def _refresh_queue(self) -> None:
        self.queue_list.delete(0, tk.END)
        for t in self.tasks:
            self.queue_list.insert(tk.END, t.label())

    def _remove_task(self) -> None:
        sel = self.queue_list.curselection()
        if not sel:
            return
        del self.tasks[sel[0]]
        self._refresh_queue()

    def _send_now_selected(self) -> None:
        sel = self.queue_list.curselection()
        if not sel:
            return
        self.tasks[sel[0]].fire()
        self._refresh_queue()
        self._maybe_close()

    # -- timer loop --

    def _tick(self) -> None:
        if self._closing:
            return
        now = datetime.now()
        changed = False
        for t in self.tasks:
            if t.status != "等待中":
                continue
            if now >= t.when:
                changed = True
                if t.mode == "auto":
                    t.fire()
                else:
                    t.status = "時間到，等待手動送出"
        if changed:
            self._refresh_queue()
        remaining = sum(1 for t in self.tasks if t.status != "已送出")
        self.status_var.set(f"佇列中還有 {remaining} 筆待處理" if self.tasks else "佇列是空的，關閉視窗即可結束")
        self._maybe_close()
        if not self._closing:
            self.after(1000, self._tick)

    def _maybe_close(self) -> None:
        if self._closing or not self.tasks:
            return
        if all(t.status == "已送出" for t in self.tasks):
            self._closing = True
            self.status_var.set("全部已送出，2 秒後自動關閉...")
            self.after(2000, self.destroy)

    def _on_close(self) -> None:
        pending = [t for t in self.tasks if t.status != "已送出"]
        if pending and not messagebox.askyesno(
            "還有未送出的任務",
            f"還有 {len(pending)} 筆任務尚未送出，關閉後就不會執行了，確定要關閉嗎？",
            parent=self,
        ):
            return
        self.destroy()


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
