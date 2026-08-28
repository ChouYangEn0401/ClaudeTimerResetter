# -*- coding: utf-8 -*-
"""resumer.py — 定時接續指定 Claude Code 對話的獨立小工具。

跟 installer.py（背景反覆探針，見 README「設計理念」一節）是完全獨立的兩個程式：
    installer.py  常駐/排程，反覆執行同一件便宜的事（ping.refresh() 的探針）
    resumer.py    手動打開，一次性排定「時間到了幫我把某個特定對話接著送出去」，
                  真的送出去、而且確認沒有馬上失敗之後才關掉

用法：被 Claude Code 的 session limit 卡住、畫面顯示大概幾點會重置時，打開這支工具，
從清單挑出被卡住的那個對話、寫（或用預設 "continue"）要接著送出的內容、設定重置時間，
選好「自動送出」還是「時間到手動確認再送出」，加入佇列。

時間到之後不是傻傻地就送：先花 0 元查一次用量（usage.py），如果 5 小時視窗還沒重置，
就繼續等，最多從預定時間起再等 WAIT_LIMIT_SECONDS（15 分鐘），時間到就照送。
送出後還會盯著那個行程 CONFIRM_SECONDS 秒——馬上死掉的話（最常見的兩種：對話正開在
VSCode 裡「already in use」、或 session id 找不到）就標成失敗並把錯誤印在畫面上，
不會騙你說已送出。全部確認送出之後視窗才會自己關掉。

之後要接著看那個對話在做什麼，使用者自己回 VSCode 重新開啟即可——這支工具只負責
「準時把 continue 送出去」這一件事。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import tkinter as tk
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import usage as usage_mod

APP_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ClaudeTimerResetter"
LOG_PATH = APP_DATA_DIR / "resumer.log"

PERMISSION_MODES = ["(不覆寫)", "acceptEdits", "bypassPermissions", "dontAsk", "plan"]

# 預定時間到了但 5 小時視窗還沒重置時，最多再等這麼久，然後不管三七二十一照送。
WAIT_LIMIT_SECONDS = 15 * 60
# 重置時間剛過的那一秒容易比伺服器早一步，多留一點緩衝。
WAIT_GRACE_SECONDS = 20
# 送出後盯著子行程這麼久；這段時間內就死掉的視為失敗（錯誤才有機會被看到）。
CONFIRM_SECONDS = 60
# 用量查詢的快取秒數，避免 1 秒一次的 tick 狂打那支端點。
QUOTA_CACHE_SECONDS = 30

STATUS_WAITING = "等待中"
STATUS_HOLDING = "等重置"
STATUS_MANUAL = "時間到，等待手動送出"
STATUS_SENDING = "送出中，確認中"
STATUS_SENT = "已送出"
STATUS_FAILED = "失敗"


def _claude_binary() -> str:
    from claude_subscription import find_claude_binary
    return find_claude_binary()


def _append_log(line: str) -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
        "jsonl": str(jsonl_path),
        "cwd": cwd or "",
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


# ---------- 送出前的檢查 ----------

def preflight(session: dict) -> list[str]:
    """回傳擋住送出的理由清單（空的＝可以送）。純本機檢查，不花錢。

    這支工具以前是 fire-and-forget，任何一項出錯都只會安靜地什麼都沒發生
    （--windowed 打包沒有主控台，例外訊息無處可去），所以這裡先一次檢查完，
    把問題直接寫到畫面上。
    """
    problems: list[str] = []
    try:
        _claude_binary()
    except ImportError as e:
        problems.append(f"找不到 claude_subscription 套件（先跑 setup.bat）：{e}")
    except Exception as e:  # ClaudeNotFoundError 等；這裡不該讓 GUI 整個掛掉
        problems.append(f"找不到 claude 執行檔：{e}")

    jsonl = session.get("jsonl")
    if not jsonl or not Path(jsonl).exists():
        problems.append(f"對話紀錄檔不見了：{jsonl or '(沒有記錄路徑)'}")

    cwd = session.get("cwd")
    if not cwd:
        problems.append("這個對話沒有記錄專案路徑（cwd），--resume 找不到它")
    elif not Path(cwd).is_dir():
        problems.append(f"專案資料夾不存在：{cwd}")
    return problems


# ---------- 排程佇列 ----------

class Task:
    def __init__(self, session: dict, prompt: str, when: datetime, mode: str,
                 permission_mode: str) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.session = session
        self.prompt = prompt
        self.when = when
        self.mode = mode  # "auto" | "manual"
        self.permission_mode = permission_mode  # "(不覆寫)" 表示不加這個參數
        self.status = STATUS_WAITING
        self.error: str | None = None
        self.proc: subprocess.Popen | None = None
        self.fired_at: float | None = None
        self.hold_until = when + timedelta(seconds=WAIT_LIMIT_SECONDS)
        self.log_path = APP_DATA_DIR / f"resume-{self.id}.log"

    def label(self) -> str:
        tail = f" | {self.error}" if self.error else ""
        return (
            f"[{self.when.strftime('%H:%M')}] {self.session['cwd'] or '(無專案路徑)'} | "
            f"{self.session['preview'][:30]} | {self.mode} | {self.status}{tail}"
        )

    @property
    def done(self) -> bool:
        return self.status == STATUS_SENT

    # -- 送出 --

    def fire(self) -> bool:
        """真的把 prompt 送出去。回傳有沒有成功「啟動」，錯誤寫進 self.error。"""
        problems = preflight(self.session)
        if problems:
            self._fail("；".join(problems))
            return False
        try:
            exe = _claude_binary()
        except Exception as e:
            self._fail(f"取得 claude 執行檔失敗：{e}")
            return False

        args = [exe, "--resume", self.session["session_id"], "--print", self.prompt]
        if self.permission_mode and self.permission_mode != PERMISSION_MODES[0]:
            args += ["--permission-mode", self.permission_mode]

        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            log = open(self.log_path, "w", encoding="utf-8")
        except OSError as e:
            self._fail(f"開不了記錄檔 {self.log_path}：{e}")
            return False
        try:
            self.proc = subprocess.Popen(
                args,
                # 關鍵：一定要在該對話自己的專案資料夾裡跑。Claude Code 的 session
                # 是按專案目錄存的（~/.claude/projects/<專案>/<id>.jsonl），在別的
                # 目錄下 --resume 這個 id 會直接「找不到這個對話」。舊版沒給 cwd，
                # 所以看起來像是「排程有跑但什麼都沒發生」。
                cwd=self.session["cwd"],
                # DETACHED_PROCESS 之下沒有主控台，stdin 若用繼承的會是無效 handle。
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
        except OSError as e:
            log.close()
            self._fail(f"啟動 claude 失敗：{e}")
            return False
        finally:
            # 子行程已經拿到自己的 handle，這邊留著只會擋住之後讀 log。
            if not log.closed:
                log.close()

        self.status = STATUS_SENDING
        self.fired_at = time.monotonic()
        _append_log(f"{_stamp()} FIRE task={self.id} session={self.session['session_id']} "
                    f"cwd={self.session['cwd']} prompt={self.prompt[:60]!r}")
        return True

    def poll(self) -> bool:
        """盯著已送出的行程。回傳狀態有沒有變。"""
        if self.status != STATUS_SENDING or self.proc is None:
            return False
        rc = self.proc.poll()
        elapsed = time.monotonic() - (self.fired_at or 0)
        if rc is None:
            if elapsed >= CONFIRM_SECONDS:
                # 撐過確認期＝沒有立刻炸掉，剩下的交給那個背景行程自己跑完。
                self.status = STATUS_SENT
                _append_log(f"{_stamp()} SENT task={self.id}（背景執行中）")
                return True
            return False
        if rc == 0:
            self.status = STATUS_SENT
            _append_log(f"{_stamp()} SENT task={self.id}（已完成，exit 0）")
            return True
        self._fail(f"claude 以 exit {rc} 結束：{self._log_tail()}")
        return True

    def _log_tail(self, limit: int = 300) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as e:
            return f"(讀不到 {self.log_path}：{e})"
        if not text:
            return "(沒有輸出；最常見的原因是對話正開在 VSCode 裡，session 被佔用)"
        return " ".join(text.split())[-limit:]

    def _fail(self, message: str) -> None:
        self.status = STATUS_FAILED
        self.error = message
        _append_log(f"{_stamp()} FAIL task={self.id} {message}")


# ---------- GUI ----------

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Claude 對話接續排程器")
        self.geometry("880x760")
        self._closing = False
        self.sessions = scan_sessions()
        self._visible_sessions: list[dict] = []
        self.tasks: list[Task] = []
        self._quota: dict | None = None
        self._quota_at = 0.0

        top = ttk.LabelFrame(self, text="選擇對話", padding=8)
        top.pack(fill="both", expand=True, padx=10, pady=8)

        filter_row = ttk.Frame(top)
        filter_row.pack(fill="x")
        ttk.Label(filter_row, text="篩選:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self._refresh_session_list())
        ttk.Entry(filter_row, textvariable=self.filter_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(filter_row, text="重新整理", command=self._reload_sessions).pack(side="left")
        ttk.Button(filter_row, text="檢查選取的對話", command=self._diagnose).pack(side="left", padx=(6, 0))

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

        queue_frame = ttk.LabelFrame(self, text="排程佇列（全部確認送出後才自動關閉）", padding=8)
        queue_frame.pack(fill="both", expand=True, padx=10, pady=8)
        self.queue_list = tk.Listbox(queue_frame, height=8)
        self.queue_list.pack(fill="both", expand=True, side="left")
        qbtns = ttk.Frame(queue_frame)
        qbtns.pack(side="left", fill="y", padx=6)
        ttk.Button(qbtns, text="移除選取", command=self._remove_task).pack(fill="x")
        ttk.Button(qbtns, text="立即送出選取", command=self._send_now_selected).pack(fill="x", pady=(6, 0))

        log_frame = ttk.LabelFrame(self, text="狀況 / 錯誤（--windowed 沒有主控台，訊息只會出現在這裡）", padding=8)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=8, wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True, side="left")
        ttk.Scrollbar(log_frame, command=self.log_text.yview).pack(side="left", fill="y")

        self.status_var = tk.StringVar(value="就緒")
        ttk.Label(self, textvariable=self.status_var, foreground="#666").pack(anchor="w", padx=10, pady=(0, 8))

        self._refresh_session_list()
        self._say(f"掃到 {len(self.sessions)} 個對話。記錄檔：{LOG_PATH}")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(1000, self._tick)

    # -- 畫面上的記錄 --

    def _say(self, line: str) -> None:
        stamped = f"{datetime.now().strftime('%H:%M:%S')}  {line}"
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, stamped + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")
        _append_log(f"{_stamp()} {line}")

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
        self._say(f"重新掃到 {len(self.sessions)} 個對話")

    def _selected_session(self) -> dict | None:
        sel = self.session_list.curselection()
        return self._visible_sessions[sel[0]] if sel else None

    def _diagnose(self) -> None:
        """不花錢的送出前檢查，讓使用者在預定時間之前就知道會不會失敗。"""
        session = self._selected_session()
        if session is None:
            messagebox.showwarning("尚未選擇對話", "請先在上面選一個對話", parent=self)
            return
        problems = preflight(session)
        if problems:
            for p in problems:
                self._say(f"[檢查] 有問題：{p}")
        else:
            self._say(f"[檢查] 可以送：{session['session_id']} @ {session['cwd']}")
        self._say("[檢查] 提醒：這個對話如果正開在 VSCode 裡，--resume 會說 session 已被佔用，"
                  "送出前請先把那個視窗關掉")

    def _attach_file(self) -> None:
        path = filedialog.askopenfilename(title="選擇要附加的檔案", parent=self)
        if path:
            self.prompt_text.insert(tk.END, f' @"{path}"')

    # -- queue --

    def _add_task(self) -> None:
        session = self._selected_session()
        if session is None:
            messagebox.showwarning("尚未選擇對話", "請先在上面選一個對話", parent=self)
            return
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

        task = Task(session, prompt, when, self.mode_var.get(), self.perm_var.get())
        self.tasks.append(task)
        self._refresh_queue()
        self._say(f"已排入 {when.strftime('%m-%d %H:%M')}：{session['cwd']}（task={task.id}）")
        for p in preflight(session):
            self._say(f"[警告] 這筆現在就有問題，時間到大概會失敗：{p}")

    def _refresh_queue(self) -> None:
        self.queue_list.delete(0, tk.END)
        for t in self.tasks:
            self.queue_list.insert(tk.END, t.label())

    def _remove_task(self) -> None:
        sel = self.queue_list.curselection()
        if not sel:
            return
        removed = self.tasks.pop(sel[0])
        self._refresh_queue()
        self._say(f"已移除 task={removed.id}")

    def _send_now_selected(self) -> None:
        sel = self.queue_list.curselection()
        if not sel:
            return
        task = self.tasks[sel[0]]
        self._fire(task, note="手動立即送出")
        self._refresh_queue()

    def _fire(self, task: Task, *, note: str) -> None:
        self._say(f"{note}：task={task.id} → {task.session['cwd']}")
        if task.fire():
            self._say(f"已啟動 task={task.id}，盯 {CONFIRM_SECONDS} 秒確認沒有馬上失敗")
        else:
            self._say(f"[失敗] task={task.id}：{task.error}")

    # -- 用量閘門 --

    def _quota_now(self) -> dict:
        """查用量，30 秒內重複使用同一份結果。"""
        if self._quota is None or time.monotonic() - self._quota_at > QUOTA_CACHE_SECONDS:
            self._quota = usage_mod.read_usage()
            self._quota_at = time.monotonic()
        return self._quota

    def _reset_blocking(self, now: datetime) -> tuple[bool, str]:
        """5 小時視窗是不是還沒重置？回傳（要不要繼續等, 說明）。"""
        quota = self._quota_now()
        if not quota["ok"]:
            return False, f"查不到用量（{quota['reason']}），直接送"
        five = quota["windows"].get("five_hour")
        if five is None or not five.get("resets_at"):
            return False, "5 小時視窗沒在計時，可以送"
        left = five.get("seconds_left") or 0
        if left <= 0:
            return False, "5 小時視窗剛重置，可以送"
        return True, (f"5 小時視窗還有 {usage_mod.fmt_left(left)} 才重置"
                      f"（{five['resets_local']}）")

    # -- timer loop --

    def _tick(self) -> None:
        if self._closing:
            return
        now = datetime.now()
        changed = False

        for t in self.tasks:
            if t.status == STATUS_SENDING:
                changed |= t.poll()
                if t.status == STATUS_FAILED:
                    self._say(f"[失敗] task={t.id}：{t.error}")
                elif t.status == STATUS_SENT:
                    self._say(f"[完成] task={t.id} 已確認送出")
                continue

            if t.status not in (STATUS_WAITING, STATUS_HOLDING):
                continue
            if now < t.when:
                continue

            if t.mode == "manual" and t.status == STATUS_WAITING:
                t.status = STATUS_MANUAL
                self._say(f"task={t.id} 時間到了，等你按「立即送出選取」")
                changed = True
                continue

            blocking, why = self._reset_blocking(now)
            if blocking and now < t.hold_until:
                if t.status != STATUS_HOLDING:
                    t.status = STATUS_HOLDING
                    changed = True
                    self._say(f"task={t.id} 時間到但{why}，最多再等到 "
                              f"{t.hold_until.strftime('%H:%M')}")
                continue
            if blocking:
                self._say(f"task={t.id} 已經等滿 {WAIT_LIMIT_SECONDS // 60} 分鐘（{why}），照送")
            self._fire(t, note="時間到，自動送出")
            changed = True

        if changed:
            self._refresh_queue()
        self._update_status()
        self._maybe_close()
        if not self._closing:
            self.after(1000, self._tick)

    def _update_status(self) -> None:
        if not self.tasks:
            self.status_var.set("佇列是空的，關閉視窗即可結束")
            return
        pending = sum(1 for t in self.tasks if not t.done and t.status != STATUS_FAILED)
        failed = sum(1 for t in self.tasks if t.status == STATUS_FAILED)
        parts = [f"待處理 {pending}"]
        if failed:
            parts.append(f"失敗 {failed}（失敗時不會自動關閉，請看上面的訊息）")
        self.status_var.set("｜".join(parts))

    def _maybe_close(self) -> None:
        # 只有「每一筆都確認送出」才自動關閉。有任何一筆失敗就把視窗留著，
        # 否則使用者永遠看不到失敗原因——這正是舊版最大的問題。
        if self._closing or not self.tasks:
            return
        if all(t.done for t in self.tasks):
            self._closing = True
            self.status_var.set("全部已確認送出，3 秒後自動關閉...")
            self.after(3000, self.destroy)

    def _on_close(self) -> None:
        pending = [t for t in self.tasks if not t.done]
        if pending and not messagebox.askyesno(
            "還有未送出的任務",
            f"還有 {len(pending)} 筆任務尚未確認送出，關閉後就不會執行了，確定要關閉嗎？",
            parent=self,
        ):
            return
        self.destroy()


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
