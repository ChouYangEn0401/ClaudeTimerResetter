# -*- coding: utf-8 -*-
"""resumer.py — 定時接續指定 Claude Code 對話的獨立小工具。

跟 installer.py（背景反覆探針，見 README「設計理念」一節）是完全獨立的兩個程式，
而且時機點的含義完全不同，別搞混：

    installer.py（resetter）  「主動排重置」：token 卡住時反覆做一件便宜的事
                              （ping.refresh() 探針），把 5 小時視窗的重置時鐘催起來。
                              它會去查用量、算時間、決定打不打——那是它的本分。
    resumer.py                「已知重置點、準時送」：你（人）已經知道 token 幾點恢復，
                              就設在那個時間點，時間一到直接把那個對話接著送出去。
                              你設的時間就是最終決定，這支工具不去二次確認用量。

用法：被 Claude Code 的 session limit 卡住、畫面顯示大概幾點會重置時，打開這支工具，
從清單挑出被卡住的那個對話、寫（或用預設 "continue"）要接著送出的內容、設定重置時間，
選好「自動送出」還是「時間到手動確認再送出」，加入佇列。

時間一到就直接送——不查用量、不自作主張壓著等（那是 resetter 的職責，不是這裡的）。

**最重要的一條鐵律：送出的那一刻，那個對話不能開在 VSCode（或任何地方）裡。**
Claude Code 的 session 同一時間只能有一個持有者，重複開會回
「Session ID ... is already in use」然後秒退。本工具靠 ~/.claude/sessions/<pid>.json
（每個正在跑的 Claude Code 行程都會寫一份，含 sessionId）在**不花任何 token**的前提下
偵測某個對話是不是正被佔用；被佔用就擋下來、不白送，並提醒你把 VSCode 那個對話關掉。
關掉之後（自動模式）下一秒就會自己補送。

送出的是一個獨立於本程式的 claude 行程（DETACHED_PROCESS），所以確認送出之後把這支
工具關掉，被接續的那個對話還是會在背景繼續跑完。之後想看它做了什麼，回 VSCode 重新
開啟該對話即可——CLI 跟 VSCode 共用同一份 ~/.claude/projects 記錄，`--resume` 是往
同一個對話檔接著寫（實測不會分岔成新檔），所以看得到接續之後那一段。
"""
from __future__ import annotations

import ctypes
import json
import os
import platform
import subprocess
import sys
import time
import tkinter as tk
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

APP_VERSION = "1.0.0"
APP_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ClaudeTimerResetter"
LOG_PATH = APP_DATA_DIR / "resumer.log"
SESSIONS_DIR = Path.home() / ".claude" / "sessions"

PERMISSION_MODES = ["(不覆寫)", "acceptEdits", "bypassPermissions", "dontAsk", "plan"]

# 每個權限模式的中文說明（GUI 即時顯示）。resumer 是 --print 非互動執行，
# 沒有人能回應中途的權限提示，所以這個下拉在這裡其實比想像中重要。
PERMISSION_HELP = {
    "(不覆寫)": (
        "不加任何 --permission-mode 參數，沿用那個對話 / 專案原本的設定。最保守。\n"
        "但注意：resumer 是非互動（--print）執行，若那個對話中途需要工具權限確認，"
        "沒有人能按「同意」，它可能就卡住或中止——而且不會跳到 VSCode 讓你按。"
        "會改到檔案的接續，建議改選 acceptEdits。"
    ),
    "acceptEdits": (
        "自動同意「檔案編輯」類的權限，不會停下來問。\n"
        "最適合 resumer 的日常用法：讓接續的對話能一路把改檔的工作做完。"
    ),
    "bypassPermissions": (
        "略過所有權限檢查，什麼都不問直接做（包含執行指令）。\n"
        "最不中斷、但也最不設防。只有你完全清楚那個對話接下來要做什麼、而且信任它時才用。"
    ),
    "dontAsk": (
        "不主動詢問權限（行為接近沿用既有設定但不彈提示）。\n"
        "不確定的話用 acceptEdits 比較好懂。"
    ),
    "plan": (
        "計畫模式：只讓它規劃、不實際動手改東西。\n"
        "適合你只想讓它「接著想 / 接著規劃」，還不要它真的執行的情況。"
    ),
}

# 送出後盯著子行程這麼久；這段時間內就死掉的視為失敗（錯誤才有機會被看到）。
CONFIRM_SECONDS = 60

STATUS_WAITING = "等待中"
STATUS_MANUAL = "時間到，等待手動送出"
STATUS_LOCKED = "被佔用，請關閉 VSCode 後會自動補送"
STATUS_SENDING = "送出中，確認中"
STATUS_SENT = "已送出"
STATUS_FAILED = "失敗"

# ---------- 配色（clam 主題微調，走乾淨淺色 + 藍色重點）----------
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
    "list_sel": "#dbeafe",
    "log_bg": "#0f172a",
    "log_fg": "#e2e8f0",
}


def _claude_binary() -> str:
    from claude_subscription import find_claude_binary
    return find_claude_binary()


def _append_log(line: str) -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- 對話「正在被誰佔用」的零成本偵測 ----------

def _pid_alive_claude(pid: int) -> bool:
    """這個 pid 還活著、而且看起來真的是 claude（避免 pid 被別的程式重用時誤判）。

    非 Windows 或查不到映像名稱時，保守地當作「還活著」——寧可多擋一次（提醒使用者
    確認），也不要漏掉「其實正被佔用」而白送一次必然失敗的接續。
    """
    if platform.system() != "Windows":
        return True
    try:
        k = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False  # 開不了＝行程不在了＝這份 session 檔是殘留
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_uint(1024)
            ok = k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
            name = buf.value.lower() if ok else ""
            return ("claude" in name) or (name == "")
        finally:
            k.CloseHandle(h)
    except Exception:
        return True


def session_holder(session_id: str) -> dict | None:
    """這個 session 現在是不是正被某個活著的 Claude Code 行程佔用？

    回傳持有者資訊（含 pid / entrypoint / cwd），沒有被佔用則回 None。純讀本機檔案，
    不花任何 token。資料來源是 ~/.claude/sessions/<pid>.json——每個正在跑的 Claude Code
    行程（含 VSCode 擴充）都會寫一份，裡面有它現在開著的 sessionId。
    """
    if not SESSIONS_DIR.exists():
        return None
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if d.get("sessionId") != session_id:
            continue
        pid = d.get("pid")
        if pid and _pid_alive_claude(pid):
            return {
                "pid": pid,
                "entrypoint": d.get("entrypoint", "?"),
                "cwd": d.get("cwd", ""),
                "name": d.get("name", ""),
            }
    return None


def describe_holder(holder: dict) -> str:
    where = "VSCode" if str(holder.get("entrypoint", "")).endswith("vscode") else holder.get("entrypoint", "?")
    name = f"「{holder['name']}」" if holder.get("name") else ""
    return f"目前正開在 {where}（pid {holder['pid']}）{name}"


def force_take_over(session_id: str, timeout: float = 10.0) -> tuple[bool, str]:
    """主動搶占：強制關掉正持有這個 session 的行程，等鎖真的釋放。

    Claude Code 的鎖是「誰的行程還活著誰持有」，沒有禮貌接管的 API——要拿走只能讓
    現在的持有者結束。所以這裡直接 taskkill 那個 pid（破壞性：那個對話在 VSCode 裡
    沒存 / 正在跑的東西會一起沒掉），然後輪詢 session_holder() 直到回 None 才算接管
    成功。killing 後 VSCode 擴充有可能馬上重連把 session 搶回去，所以會等到逾時；
    逾時仍未釋放就回失敗，讓呼叫端不要硬送一次必然失敗的接續。
    """
    holder = session_holder(session_id)
    if holder is None:
        return True, "目前沒有被佔用，可直接送出"
    pid = holder.get("pid")
    if not pid:
        return False, "持有者資訊沒有 pid，無法接管"
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except OSError as e:
        return False, f"taskkill 失敗：{e}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session_holder(session_id) is None:
            return True, f"已接管（關閉 pid {pid}）"
        time.sleep(0.3)
    return False, f"關了 pid {pid}，但鎖還沒釋放（可能 VSCode 又重連），請稍後再試"


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


# ---------- 讀出對話內容（給「查看內容」小視窗用，不花錢）----------

def _extract_conv_text(content) -> str | None:
    """把一則訊息壓成一段人看得懂的文字：優先取真正的文字，純工具往返就給個標記。"""
    if isinstance(content, str):
        return content.strip() or None
    if not isinstance(content, list):
        return None
    texts: list[str] = []
    tool_names: list[str] = []
    has_tool_result = False
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text" and block.get("text"):
            texts.append(block["text"].strip())
        elif btype == "tool_use" and block.get("name"):
            tool_names.append(block["name"])
        elif btype == "tool_result":
            has_tool_result = True
    if texts:
        return "\n".join(t for t in texts if t)
    if tool_names:
        return "⚙ 使用工具：" + "、".join(dict.fromkeys(tool_names))
    if has_tool_result:
        return None  # 純工具結果，不是人看的內容，略過
    return None


def read_conversation(jsonl_path: str, tail: int = 120, max_lines: int = 8000) -> dict:
    """讀出一個對話的訊息，給預覽視窗用。純讀本機檔案，不花任何 token。

    為了對付很長的對話：只保留「第一則」＋「最後 tail 則」，中間用省略號帶過，
    這樣既知道對話在談什麼、又看得到它卡在哪，記憶體也不會爆。
    """
    from collections import deque
    first = None
    recent: deque = deque(maxlen=tail)
    total = 0
    truncated_file = False
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    truncated_file = True
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                role = rec.get("type")
                if role not in ("user", "assistant"):
                    continue
                text = _extract_conv_text(rec.get("message", {}).get("content"))
                if not text:
                    continue
                ts = rec.get("timestamp") or ""
                item = {"role": role, "text": text, "ts": ts}
                if first is None:
                    first = item
                recent.append(item)
                total += 1
    except OSError as e:
        return {"ok": False, "reason": f"讀不到對話檔：{e}", "messages": [], "total": 0}

    messages = list(recent)
    omitted = 0
    if total > len(messages):
        omitted = total - len(messages)
        if first is not None:
            messages = [first, {"role": "sep", "text": f"（中間省略 {omitted} 則）", "ts": ""}] + messages
    return {"ok": True, "reason": "", "messages": messages, "total": total,
            "truncated_file": truncated_file}


# ---------- 送出前的檢查（都不花錢）----------

def preflight(session: dict) -> list[str]:
    """靜態本機檢查：擋住送出的理由清單（空的＝這些項目沒問題）。

    這裡只查「不會變」的東西（執行檔、對話檔、專案資料夾）。「有沒有被佔用」是會隨
    你開關 VSCode 而變的，另外用 session_holder() 動態查，不放在這裡。
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
        self.log_path = APP_DATA_DIR / f"resume-{self.id}.log"

    def label(self) -> str:
        tail = f" | {self.error}" if self.error else ""
        return (
            f"[{self.when.strftime('%H:%M:%S')}] {self.session['cwd'] or '(無專案路徑)'} | "
            f"{self.session['preview'][:30]} | {self.mode} | {self.status}{tail}"
        )

    @property
    def done(self) -> bool:
        return self.status == STATUS_SENT

    @property
    def settled(self) -> bool:
        """已經有最終結果、不再需要 tick 幫它做事（送出成功或硬失敗）。"""
        return self.status in (STATUS_SENT, STATUS_FAILED)

    # -- 送出 --

    def fire(self) -> bool:
        """真的把 prompt 送出去。回傳有沒有成功「啟動」，錯誤寫進 self.error。

        送出前先做兩道零成本檢查：靜態 preflight + 動態的「有沒有被佔用」。被佔用時
        不標成硬失敗，而是標 STATUS_LOCKED——自動模式的 tick 會在你關掉 VSCode 之後
        自己補送，不用你重排。
        """
        problems = preflight(self.session)
        if problems:
            self._fail("；".join(problems))
            return False

        holder = session_holder(self.session["session_id"])
        if holder:
            self.status = STATUS_LOCKED
            self.error = f"{describe_holder(holder)}，請關閉後它會自動補送"
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
                # 目錄下 --resume 這個 id 會直接「找不到這個對話」。
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
        self.error = None
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

class ConversationViewer(tk.Toplevel):
    """一個唯讀小視窗：把選定對話的往來內容攤開來看，避免排程時選錯對話。

    只讀本機的 .jsonl，不花任何 token。開啟時捲到最底（最近的訊息），因為那通常就是
    「被 session limit 卡住」的地方，最能幫你確認是不是要接續的那個對話。
    """

    def __init__(self, app: "App", session: dict) -> None:
        super().__init__(app)
        self.app = app
        self.session = session
        c = COLORS
        self.title("對話內容預覽")
        self.geometry("760x640")
        self.minsize(520, 400)
        self.configure(bg=c["card"])
        self.transient(app)

        head = ttk.Frame(self, style="Card.TFrame", padding=(14, 12))
        head.pack(fill="x")
        ttk.Label(head, text=session.get("cwd") or "(無專案路徑)", style="H2.TLabel",
                  wraplength=700, justify="left").pack(anchor="w")
        ttk.Label(head, text=f"session {session['session_id']}｜最後活動 {session.get('last_active', '')}",
                  style="Muted.TLabel", wraplength=700, justify="left").pack(anchor="w", pady=(2, 0))
        self.count_var = tk.StringVar(value="讀取中…")
        ttk.Label(head, textvariable=self.count_var, style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        body = ttk.Frame(self, style="Card.TFrame", padding=(14, 0))
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", state="disabled", borderwidth=1, relief="solid",
                            highlightthickness=0, font=app._font_small, bg="#ffffff", fg=c["text"],
                            padx=10, pady=8, spacing3=4)
        self.text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, command=self.text.yview)
        sb.pack(side="left", fill="y")
        self.text.config(yscrollcommand=sb.set)
        # 角色樣式
        self.text.tag_configure("role_user", foreground=c["accent"], font=app._font_h2, spacing1=8)
        self.text.tag_configure("role_asst", foreground=c["ok"], font=app._font_h2, spacing1=8)
        self.text.tag_configure("sep", foreground=c["muted"], justify="center", spacing1=6, spacing3=6)
        self.text.tag_configure("meta", foreground=c["muted"], font=app._font_small)

        btns = ttk.Frame(self, style="Card.TFrame", padding=(14, 12))
        btns.pack(fill="x")
        ttk.Button(btns, text="就選這個對話", style="Accent.TButton",
                   command=self._choose).pack(side="right")
        ttk.Button(btns, text="關閉", command=self.destroy).pack(side="right", padx=(0, 8))

        self._load()
        self.bind("<Escape>", lambda e: self.destroy())

    def _load(self) -> None:
        data = read_conversation(self.session["jsonl"])
        self.text.configure(state="normal")
        self.text.delete("1.0", tk.END)
        if not data["ok"]:
            self.text.insert(tk.END, data["reason"])
            self.count_var.set("")
            self.text.configure(state="disabled")
            return
        for m in data["messages"]:
            if m["role"] == "sep":
                self.text.insert(tk.END, f"\n{m['text']}\n\n", "sep")
                continue
            who = "你" if m["role"] == "user" else "Claude"
            tag = "role_user" if m["role"] == "user" else "role_asst"
            stamp = self._fmt_ts(m["ts"])
            self.text.insert(tk.END, who, tag)
            if stamp:
                self.text.insert(tk.END, f"   {stamp}", "meta")
            self.text.insert(tk.END, "\n")
            self.text.insert(tk.END, m["text"].strip() + "\n")
        self.text.configure(state="disabled")
        self.text.see(tk.END)  # 捲到最近的訊息
        note = "（檔案很長，只讀了前面一段）" if data.get("truncated_file") else ""
        self.count_var.set(f"共 {data['total']} 則訊息{note}")

    @staticmethod
    def _fmt_ts(ts: str) -> str:
        if not ts:
            return ""
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%m-%d %H:%M")
        except (ValueError, TypeError):
            return ""

    def _choose(self) -> None:
        self.app.select_session(self.session)
        self.destroy()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self._init_scaling_and_style()
        self.title(f"Claude 對話接續排程器 v{APP_VERSION}")
        self.geometry("960x820")
        self.minsize(820, 720)
        self.configure(bg=COLORS["bg"])
        self._closing = False
        self.sessions = scan_sessions()
        self._visible_sessions: list[dict] = []
        self.tasks: list[Task] = []

        self._build_ui()

        self._refresh_session_list()
        self._refresh_perm_help()
        self._sync_time_format()
        self._say(f"掃到 {len(self.sessions)} 個對話。記錄檔：{LOG_PATH}")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(250, self._tick)

    # -- 外觀 --

    def _init_scaling_and_style(self) -> None:
        # 高 DPI：讓 Tk 依系統縮放，字不會糊、版面不會擠成一團。
        if platform.system() == "Windows":
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                try:
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass
        try:
            dpi = self.winfo_fpixels("1i")
            self.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass

        base = tkfont.nametofont("TkDefaultFont")
        family = "Segoe UI" if platform.system() == "Windows" else base.actual("family")
        base.configure(family=family, size=10)
        self.option_add("*Font", base)
        self._font_h1 = tkfont.Font(family=family, size=15, weight="bold")
        self._font_h2 = tkfont.Font(family=family, size=11, weight="bold")
        self._font_small = tkfont.Font(family=family, size=9)
        self._font_mono = tkfont.Font(family="Consolas", size=9)

        st = ttk.Style(self)
        st.theme_use("clam")
        c = COLORS
        st.configure(".", background=c["bg"], foreground=c["text"])
        st.configure("Card.TFrame", background=c["card"])
        st.configure("TFrame", background=c["bg"])
        st.configure("TLabel", background=c["bg"], foreground=c["text"])
        st.configure("Card.TLabel", background=c["card"], foreground=c["text"])
        st.configure("Muted.TLabel", background=c["card"], foreground=c["muted"], font=self._font_small)
        st.configure("MutedBg.TLabel", background=c["bg"], foreground=c["muted"], font=self._font_small)
        st.configure("H2.TLabel", background=c["card"], foreground=c["text"], font=self._font_h2)
        st.configure("TCheckbutton", background=c["card"], foreground=c["text"])
        st.map("TCheckbutton", background=[("active", c["card"])])
        st.configure("TRadiobutton", background=c["card"], foreground=c["text"])
        st.map("TRadiobutton", background=[("active", c["card"])])
        st.configure("Card.TLabelframe", background=c["card"], bordercolor=c["border"], relief="solid", borderwidth=1)
        st.configure("Card.TLabelframe.Label", background=c["card"], foreground=c["accent"], font=self._font_h2)
        st.configure("TEntry", fieldbackground="#ffffff", bordercolor=c["border"])
        st.configure("TCombobox", fieldbackground="#ffffff", bordercolor=c["border"])
        # 一般按鈕
        st.configure("TButton", background="#eef0f3", foreground=c["text"], borderwidth=1,
                     bordercolor=c["border"], focuscolor=c["bg"], padding=(10, 5))
        st.map("TButton", background=[("active", "#e2e5ea"), ("pressed", "#d5d9df")])
        # 主要行動按鈕（藍）
        st.configure("Accent.TButton", background=c["accent"], foreground=c["accent_fg"],
                     borderwidth=0, padding=(14, 6), font=self._font_h2)
        st.map("Accent.TButton", background=[("active", "#1d4ed8"), ("pressed", "#1e40af")])
        st.configure("Vertical.TScrollbar", background="#e2e5ea", troughcolor=c["card"],
                     bordercolor=c["card"], arrowcolor=c["muted"])

    def _card(self, parent, title: str) -> ttk.Labelframe:
        f = ttk.Labelframe(parent, text=title, style="Card.TLabelframe", padding=12)
        return f

    def _build_ui(self) -> None:
        c = COLORS
        outer = ttk.Frame(self, padding=(14, 12))
        outer.pack(fill="both", expand=True)

        # 標題 + 那條最重要的提醒
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Claude 對話接續排程器", font=self._font_h1).pack(anchor="w")
        ttk.Label(
            header,
            text="時間一到就把選定的對話接著送出去。鐵律：送出當下該對話不能開在 VSCode 裡"
                 "（會被鎖住）。本工具會在不花 token 的情況下先幫你偵測、擋下並提醒。",
            style="MutedBg.TLabel", wraplength=920, justify="left",
        ).pack(anchor="w", pady=(2, 10))

        # --- 選擇對話 ---
        top = self._card(outer, "① 選擇要接續的對話")
        top.pack(fill="both", expand=True)
        filter_row = ttk.Frame(top, style="Card.TFrame")
        filter_row.pack(fill="x")
        ttk.Label(filter_row, text="篩選：", style="Card.TLabel").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self._refresh_session_list())
        ttk.Entry(filter_row, textvariable=self.filter_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(filter_row, text="重新整理", command=self._reload_sessions).pack(side="left")
        ttk.Button(filter_row, text="查看內容", command=self._view_conversation).pack(side="left", padx=(6, 0))
        ttk.Button(filter_row, text="檢查可否傳送（不花錢）", command=self._diagnose).pack(side="left", padx=(6, 0))

        list_wrap = ttk.Frame(top, style="Card.TFrame")
        list_wrap.pack(fill="both", expand=True, pady=(8, 0))
        self.session_list = tk.Listbox(
            list_wrap, height=9, activestyle="none", borderwidth=1, relief="solid",
            highlightthickness=0, font=self._font_small, bg="#ffffff", fg=c["text"],
            selectbackground=c["list_sel"], selectforeground=c["text"],
        )
        self.session_list.pack(side="left", fill="both", expand=True)
        self.session_list.bind("<Double-Button-1>", lambda e: self._view_conversation())
        sbar = ttk.Scrollbar(list_wrap, command=self.session_list.yview)
        sbar.pack(side="left", fill="y")
        self.session_list.config(yscrollcommand=sbar.set)
        ttk.Label(top, text="小提示：雙擊任一列（或按「查看內容」）可先預覽對話內容，不花錢，避免選錯。",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        # --- 新增任務 ---
        form = self._card(outer, "② 排程內容")
        form.pack(fill="x", pady=(12, 0))
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="要接著送出的內容：", style="Card.TLabel").grid(row=0, column=0, sticky="nw")
        self.prompt_text = tk.Text(form, height=4, width=60, borderwidth=1, relief="solid",
                                   highlightthickness=0, font=self._font_small, bg="#ffffff", fg=c["text"])
        self.prompt_text.insert("1.0", "continue")
        self.prompt_text.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(8, 0))
        ttk.Button(form, text="附加檔案…", command=self._attach_file).grid(
            row=1, column=1, sticky="w", padx=(8, 0), pady=(4, 0))

        # 時間 + 精準到秒 + 現在時間
        ttk.Label(form, text="送出時間：", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 0))
        time_row = ttk.Frame(form, style="Card.TFrame")
        time_row.grid(row=2, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(10, 0))
        self.time_var = tk.StringVar()
        self.time_entry = ttk.Entry(time_row, textvariable=self.time_var, width=12, font=self._font_h2)
        self.time_entry.pack(side="left")
        self.seconds_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(time_row, text="精準到秒 (HH:MM:SS)", variable=self.seconds_var,
                        command=self._sync_time_format).pack(side="left", padx=(10, 0))
        self.clock_var = tk.StringVar(value="")
        ttk.Label(time_row, textvariable=self.clock_var, style="Muted.TLabel").pack(side="left", padx=(16, 0))
        ttk.Button(time_row, text="＝現在", command=self._set_time_now).pack(side="left", padx=(10, 0))
        ttk.Button(time_row, text="+1 分", command=lambda: self._bump_time(60)).pack(side="left", padx=(4, 0))

        # 送出方式
        ttk.Label(form, text="送出方式：", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.mode_var = tk.StringVar(value="auto")
        mode_box = ttk.Frame(form, style="Card.TFrame")
        mode_box.grid(row=3, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(10, 0))
        ttk.Radiobutton(mode_box, text="自動送出", variable=self.mode_var, value="auto").pack(side="left")
        ttk.Radiobutton(mode_box, text="時間到手動確認", variable=self.mode_var, value="manual").pack(side="left", padx=(12, 0))
        ttk.Label(
            form,
            text="「自動送出」＝時間一到就直接送（一般都用這個，被 VSCode 佔用時會等你關掉後自動補送）；"
                 "「時間到手動確認」＝時間到只提醒你，等你按下面的「立即送出」才送。",
            style="Muted.TLabel", wraplength=900, justify="left",
        ).grid(row=4, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(2, 0))

        # 權限模式
        ttk.Label(form, text="權限模式（選填）：", style="Card.TLabel").grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.perm_var = tk.StringVar(value=PERMISSION_MODES[0])
        perm_combo = ttk.Combobox(form, textvariable=self.perm_var, state="readonly",
                                  values=PERMISSION_MODES, width=20)
        perm_combo.grid(row=5, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        perm_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_perm_help())
        self.perm_help_var = tk.StringVar()
        ttk.Label(form, textvariable=self.perm_help_var, style="Muted.TLabel",
                  wraplength=900, justify="left").grid(
            row=6, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(4, 0))

        ttk.Button(form, text="加入排程佇列", style="Accent.TButton", command=self._add_task).grid(
            row=7, column=1, columnspan=3, sticky="e", pady=(12, 0))

        # --- 佇列 ---
        queue_frame = self._card(outer, "③ 排程佇列")
        queue_frame.pack(fill="both", expand=True, pady=(12, 0))
        qtop = ttk.Frame(queue_frame, style="Card.TFrame")
        qtop.pack(fill="both", expand=True)
        self.queue_list = tk.Listbox(
            qtop, height=6, activestyle="none", borderwidth=1, relief="solid",
            highlightthickness=0, font=self._font_small, bg="#ffffff", fg=c["text"],
            selectbackground=c["list_sel"], selectforeground=c["text"],
        )
        self.queue_list.pack(side="left", fill="both", expand=True)
        qbtns = ttk.Frame(queue_frame, style="Card.TFrame")
        qbtns.pack(fill="x", pady=(8, 0))
        ttk.Button(qbtns, text="移除選取", command=self._remove_task).pack(side="left")
        ttk.Button(qbtns, text="立即送出選取", command=self._send_now_selected).pack(side="left", padx=(6, 0))
        ttk.Button(qbtns, text="強制接管並送出", command=self._force_take_over_selected).pack(side="left", padx=(6, 0))
        self.autoclose_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            qbtns, text="全部確認送出後自動關閉視窗（取消勾選＝送完也不關，讓你留著看結果）",
            variable=self.autoclose_var,
        ).pack(side="right")

        # --- 狀況 / 錯誤 ---
        log_frame = self._card(outer, "狀況 / 錯誤（--windowed 沒有主控台，訊息只會出現在這裡）")
        log_frame.pack(fill="both", expand=True, pady=(12, 0))
        logwrap = ttk.Frame(log_frame, style="Card.TFrame")
        logwrap.pack(fill="both", expand=True)
        self.log_text = tk.Text(logwrap, height=7, wrap="word", state="disabled", borderwidth=0,
                                highlightthickness=0, font=self._font_mono,
                                bg=COLORS["log_bg"], fg=COLORS["log_fg"], insertbackground=COLORS["log_fg"])
        self.log_text.pack(side="left", fill="both", expand=True)
        lbar = ttk.Scrollbar(logwrap, command=self.log_text.yview)
        lbar.pack(side="left", fill="y")
        self.log_text.config(yscrollcommand=lbar.set)

        self.status_var = tk.StringVar(value="就緒")
        ttk.Label(outer, textvariable=self.status_var, style="MutedBg.TLabel").pack(
            anchor="w", pady=(8, 0))

    # -- 時間欄位（HH:MM ↔ HH:MM:SS）--

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
        """切換「精準到秒」時，把現有的時間字串重新格式化，並補一個合理預設。"""
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

    # -- 畫面上的記錄 --

    def _say(self, line: str) -> None:
        stamped = f"{datetime.now().strftime('%H:%M:%S')}  {line}"
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, stamped + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")
        _append_log(f"{_stamp()} {line}")

    def _refresh_perm_help(self) -> None:
        self.perm_help_var.set(PERMISSION_HELP.get(self.perm_var.get(), ""))

    # -- session list --

    def _refresh_session_list(self) -> None:
        q = self.filter_var.get().strip().lower()
        self.session_list.delete(0, tk.END)
        self._visible_sessions = [
            s for s in self.sessions if not q or q in s["cwd"].lower() or q in s["preview"].lower()
        ]
        for s in self._visible_sessions:
            open_tag = "  ● 開啟中" if session_holder(s["session_id"]) else ""
            self.session_list.insert(tk.END, f"{s['last_active']} | {s['cwd']} | {s['preview']}{open_tag}")

    def _reload_sessions(self) -> None:
        self.sessions = scan_sessions()
        self._refresh_session_list()
        self._say(f"重新掃到 {len(self.sessions)} 個對話")

    def _selected_session(self) -> dict | None:
        sel = self.session_list.curselection()
        return self._visible_sessions[sel[0]] if sel else None

    def _diagnose(self) -> None:
        """不花錢的送出前檢查：靜態 preflight + 動態「有沒有被佔用」。"""
        session = self._selected_session()
        if session is None:
            messagebox.showwarning("尚未選擇對話", "請先在上面選一個對話", parent=self)
            return
        problems = preflight(session)
        holder = session_holder(session["session_id"])
        if holder:
            problems.append(f"這個對話{describe_holder(holder)}——現在送會被鎖住而失敗，"
                            f"請先把它關掉")
        if problems:
            for p in problems:
                self._say(f"[檢查] ✗ {p}")
        else:
            self._say(f"[檢查] ✓ 可以送：{session['session_id']} @ {session['cwd']}（目前沒有被佔用）")

    def _view_conversation(self) -> None:
        """打開唯讀預覽視窗看選定對話的內容，不花錢。"""
        session = self._selected_session()
        if session is None:
            messagebox.showwarning("尚未選擇對話", "請先在上面選一個對話", parent=self)
            return
        ConversationViewer(self, session)

    def select_session(self, session: dict) -> None:
        """從預覽視窗的「就選這個對話」回來：在清單裡把它選起來（必要時清掉篩選）。"""
        if session not in self._visible_sessions:
            self.filter_var.set("")
            self._refresh_session_list()
        try:
            idx = self._visible_sessions.index(session)
        except ValueError:
            return
        self.session_list.selection_clear(0, tk.END)
        self.session_list.selection_set(idx)
        self.session_list.see(idx)
        self.session_list.activate(idx)

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
            messagebox.showwarning("內容是空的", "請輸入要接續送出的內容", parent=self)
            return
        try:
            when = self._parse_time(self.time_var.get())
        except ValueError:
            messagebox.showerror("時間格式錯誤", "請用 HH:MM 或 HH:MM:SS，例如 18:41 或 18:41:30", parent=self)
            return

        task = Task(session, prompt, when, self.mode_var.get(), self.perm_var.get())
        self.tasks.append(task)
        self._refresh_queue()
        self._say(f"已排入 {when.strftime('%m-%d %H:%M:%S')}：{session['cwd']}（task={task.id}）")
        for p in preflight(session):
            self._say(f"[警告] 這筆現在就有問題，時間到大概會失敗：{p}")

    def _refresh_queue(self) -> None:
        sel = self.queue_list.curselection()
        self.queue_list.delete(0, tk.END)
        for t in self.tasks:
            self.queue_list.insert(tk.END, t.label())
        if sel and sel[0] < len(self.tasks):
            self.queue_list.selection_set(sel[0])

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
            messagebox.showinfo("尚未選擇", "請先在佇列裡選一筆", parent=self)
            return
        task = self.tasks[sel[0]]
        if task.settled:
            self._say(f"task={task.id} 已經是最終狀態（{task.status}），不重送")
            return
        self._fire(task, note="手動立即送出")
        self._refresh_queue()

    def _force_take_over_selected(self) -> None:
        """主動搶占選取任務的 session：關掉持有它的行程後再送出。破壞性，先跳確認。"""
        sel = self.queue_list.curselection()
        if not sel:
            messagebox.showinfo("尚未選擇", "請先在佇列裡選一筆", parent=self)
            return
        task = self.tasks[sel[0]]
        if task.settled:
            self._say(f"task={task.id} 已經是最終狀態（{task.status}），不重送")
            return
        holder = session_holder(task.session["session_id"])
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
        ok, msg = force_take_over(task.session["session_id"])
        self._say(f"[接管] task={task.id}：{msg}")
        if ok:
            self._fire(task, note="接管後送出")
        self._refresh_queue()

    def _fire(self, task: Task, *, note: str) -> None:
        self._say(f"{note}：task={task.id} → {task.session['cwd']}")
        if task.fire():
            self._say(f"已啟動 task={task.id}，盯 {CONFIRM_SECONDS} 秒確認沒有馬上失敗")
        elif task.status == STATUS_LOCKED:
            self._say(f"[擋下] task={task.id}：{task.error}")
        else:
            self._say(f"[失敗] task={task.id}：{task.error}")

    # -- timer loop --

    def _tick(self) -> None:
        if self._closing:
            return
        now = datetime.now()
        self.clock_var.set("現在 " + now.strftime("%H:%M:%S"))
        changed = False

        for t in self.tasks:
            if t.status == STATUS_SENDING:
                changed |= t.poll()
                if t.status == STATUS_FAILED:
                    self._say(f"[失敗] task={t.id}：{t.error}")
                elif t.status == STATUS_SENT:
                    self._say(f"[完成] task={t.id} 已確認送出")
                continue

            # 可以被（自動）觸發的狀態：還在等、或上次被佔用擋下。
            if t.status not in (STATUS_WAITING, STATUS_LOCKED):
                continue
            if now < t.when:
                continue

            if t.mode == "manual" and t.status == STATUS_WAITING:
                t.status = STATUS_MANUAL
                self._say(f"task={t.id} 時間到了，等你按「立即送出選取」")
                changed = True
                continue
            if t.mode == "manual":
                continue  # 手動模式被鎖住不自動補送，等使用者自己按

            was_locked = t.status == STATUS_LOCKED
            prev_err = t.error
            # 時間一到就送。你設的時間就是你判斷的重置點——這裡不查用量、不壓著等。
            fired = t.fire()
            changed = True
            if fired:
                self._say(f"時間到，自動送出：task={t.id} → {t.session['cwd']}")
                self._say(f"已啟動 task={t.id}，盯 {CONFIRM_SECONDS} 秒確認沒有馬上失敗")
            elif t.status == STATUS_LOCKED:
                # 只在第一次、或訊息有變時說一次，免得每秒洗版。
                if not was_locked or t.error != prev_err:
                    self._say(f"[擋下] task={t.id}：{t.error}")
            else:
                self._say(f"[失敗] task={t.id}：{t.error}")

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
        pending = sum(1 for t in self.tasks if not t.settled)
        locked = sum(1 for t in self.tasks if t.status == STATUS_LOCKED)
        failed = sum(1 for t in self.tasks if t.status == STATUS_FAILED)
        parts = [f"待處理 {pending}"]
        if locked:
            parts.append(f"被佔用 {locked}（關掉 VSCode 那個對話就會自動補送）")
        if failed:
            parts.append(f"失敗 {failed}（不會自動關閉，請看上面的訊息）")
        self.status_var.set("｜".join(parts))

    def _maybe_close(self) -> None:
        # 只有「每一筆都確認送出」且使用者允許自動關閉時才關。有任何一筆失敗就把視窗
        # 留著，否則使用者永遠看不到失敗原因——這正是舊版最大的問題。
        if self._closing or not self.tasks or not self.autoclose_var.get():
            return
        if all(t.done for t in self.tasks):
            self._closing = True
            self.status_var.set("全部已確認送出，3 秒後自動關閉...")
            self.after(3000, self.destroy)

    def _on_close(self) -> None:
        pending = [t for t in self.tasks if not t.settled]
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
