# -*- coding: utf-8 -*-
"""sessions.py — 本機 Claude Code 對話的清單，以及「這個對話現在有沒有人開著」。

兩件事都純讀本機檔案，不花任何 token：

    scan_sessions()   掃 ~/.claude/projects/*/*.jsonl，給出可以挑選的對話清單
    session_holder()  讀 ~/.claude/sessions/<pid>.json，看某個對話是不是正被佔用

清單為什麼不是直接秀第一句話：Claude Code 自己會把對話標題（ai-title / custom-title）
跟最後一句使用者輸入（last-prompt）寫進同一個 .jsonl，用這些欄位比「檔案裡第一行
使用者訊息」好認太多——被 session limit 卡住的那個對話，通常是靠「最後我跟它說了
什麼」認出來的。
"""
from __future__ import annotations

import ctypes
import json
import platform
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..paths import projects_dir, sessions_dir

# 清單預設只帶最近這麼多個對話：768 個對話檔全部開來讀會讓視窗開啟卡住好幾秒，
# 而這支工具要找的一定是「剛剛被卡住的那個」。要更多再按「載入更多」。
DEFAULT_LIMIT = 200
# 每個檔案讀頭尾各一小段就夠了（cwd 在最前面，標題與最後一句在最後面）。
HEAD_LINES = 60
TAIL_BYTES = 96 * 1024


@dataclass
class SessionInfo:
    """清單上的一列。jsonl / cwd / session_id 是送出時真正會用到的三個欄位。"""

    session_id: str
    jsonl: str
    cwd: str
    title: str
    last_prompt: str
    mtime: float
    size: int
    holder: dict | None = field(default=None, compare=False)

    @property
    def project(self) -> str:
        return Path(self.cwd).name if self.cwd else "(無專案路徑)"

    @property
    def last_active(self) -> str:
        return datetime.fromtimestamp(self.mtime).strftime("%m-%d %H:%M")

    @property
    def relative_time(self) -> str:
        return humanize_since(self.mtime)


def humanize_since(mtime: float) -> str:
    """「3 小時前」這種相對時間——挑對話時比絕對時間好判斷。"""
    delta = max(time.time() - mtime, 0)
    if delta < 90:
        return "剛剛"
    if delta < 3600:
        return f"{int(delta // 60)} 分鐘前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小時前"
    if delta < 86400 * 30:
        return f"{int(delta // 86400)} 天前"
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")


def clean_one_line(text: str, limit: int = 120) -> str:
    """把一段訊息壓成清單上看得懂的一行：拿掉標記、換行、多餘空白。"""
    if not text:
        return ""
    for tag in ("<command-name>", "<command-message>", "<command-args>",
                "<local-command-stdout>", "<system-reminder>"):
        text = text.replace(tag, " ").replace(tag.replace("<", "</"), " ")
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


# ---------- 掃描對話清單 ----------

def scan_sessions(limit: int = DEFAULT_LIMIT) -> tuple[list[SessionInfo], int]:
    """回傳（最近 limit 個對話, 本機對話總數）。依最後活動時間由新到舊。"""
    root = projects_dir()
    if not root.exists():
        return [], 0
    paths = []
    for p in root.glob("*/*.jsonl"):
        try:
            st = p.stat()
        except OSError:
            continue
        paths.append((st.st_mtime, st.st_size, p))
    paths.sort(key=lambda t: t[0], reverse=True)

    out: list[SessionInfo] = []
    for mtime, size, path in paths[:limit]:
        info = _peek(path, mtime, size)
        if info is not None:
            out.append(info)
    return out, len(paths)


def _peek(path: Path, mtime: float, size: int) -> SessionInfo | None:
    """讀頭尾各一小段，湊出清單要顯示的欄位。讀不到有意義的內容就跳過這個檔。"""
    cwd = ""
    first_prompt = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= HEAD_LINES or (cwd and first_prompt):
                    break
                rec = _loads(line)
                if rec is None:
                    continue
                if not cwd and rec.get("cwd"):
                    cwd = rec["cwd"]
                if not first_prompt and rec.get("type") == "user" and not rec.get("isMeta"):
                    first_prompt = clean_one_line(_plain_user_text(rec) or "")
    except OSError:
        return None

    title = ""
    last_prompt = ""
    for rec in _tail_records(path, size):
        t = rec.get("type")
        if t == "custom-title" and rec.get("customTitle"):
            title = rec["customTitle"]          # 使用者自己取的名字優先
        elif t == "ai-title" and rec.get("aiTitle") and not title:
            title = rec["aiTitle"]
        elif t == "last-prompt" and rec.get("lastPrompt"):
            last_prompt = clean_one_line(rec["lastPrompt"])
        elif not cwd and rec.get("cwd"):
            cwd = rec["cwd"]

    title = clean_one_line(title, 70) or first_prompt or "(沒有文字內容)"
    if not first_prompt and not last_prompt and title == "(沒有文字內容)":
        return None  # 純 queue-operation / 空檔，不是一個真的對話
    return SessionInfo(
        session_id=path.stem,
        jsonl=str(path),
        cwd=cwd,
        title=title,
        last_prompt=last_prompt or first_prompt,
        mtime=mtime,
        size=size,
    )


def _tail_records(path: Path, size: int) -> list[dict]:
    """讀檔案最後 TAIL_BYTES 並解析出完整的 JSON 行（第一行多半被切斷，丟掉）。"""
    try:
        with open(path, "rb") as f:
            if size > TAIL_BYTES:
                f.seek(-TAIL_BYTES, 2)
                f.readline()
            raw = f.read()
    except OSError:
        return []
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        rec = _loads(line)
        if rec is not None:
            out.append(rec)
    return out


def _loads(line: str) -> dict | None:
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    return rec if isinstance(rec, dict) else None


def _plain_user_text(rec: dict) -> str | None:
    """使用者記錄裡真正是「人打的字」的那一段（工具回應那種不算）。"""
    content = rec.get("message", {}).get("content")
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                return block["text"].strip()
    return None


# ---------- 「這個對話現在有沒有人開著」 ----------

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


def active_holders() -> dict[str, dict]:
    """一次讀完 ~/.claude/sessions/，回傳 {sessionId: 持有者資訊}。

    每個正在跑的 Claude Code 行程（含 VSCode 擴充）都會寫一份 <pid>.json，裡面有它
    現在開著的 sessionId。整份一次讀進來，清單上幾百列就不用各自再掃一次資料夾。
    純讀本機檔案，不花任何 token。
    """
    d = sessions_dir()
    if not d.exists():
        return {}
    holders: dict[str, dict] = {}
    for f in d.glob("*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sid = rec.get("sessionId")
        pid = rec.get("pid")
        if not sid or not pid or not _pid_alive_claude(pid):
            continue
        holders[sid] = {
            "pid": pid,
            "entrypoint": rec.get("entrypoint", "?"),
            "cwd": rec.get("cwd", ""),
            "name": rec.get("name", ""),
        }
    return holders


def session_holder(session_id: str) -> dict | None:
    """這個 session 現在是不是正被某個活著的 Claude Code 行程佔用？

    回傳持有者資訊（pid / entrypoint / cwd），沒有被佔用則回 None。
    """
    return active_holders().get(session_id)


def describe_holder(holder: dict) -> str:
    where = "VSCode" if str(holder.get("entrypoint", "")).endswith("vscode") else holder.get("entrypoint", "?")
    name = f"「{holder['name']}」" if holder.get("name") else ""
    return f"目前正開在 {where}（pid {holder['pid']}）{name}"


def force_take_over(session_id: str, timeout: float = 10.0) -> tuple[bool, str]:
    """主動搶占：強制關掉正持有這個 session 的行程，等鎖真的釋放。

    Claude Code 的鎖是「誰的行程還活著誰持有」，沒有禮貌接管的 API——要拿走只能讓
    現在的持有者結束。所以這裡直接 taskkill 那個 pid（破壞性：那個對話在 VSCode 裡
    沒存 / 正在跑的東西會一起沒掉），然後輪詢 session_holder() 直到回 None 才算接管
    成功。kill 之後 VSCode 擴充有可能馬上重連把 session 搶回去，所以會等到逾時；
    逾時仍未釋放就回失敗，讓呼叫端不要硬送一次必然失敗的接續。
    """
    holder = session_holder(session_id)
    if holder is None:
        return True, "目前沒有被佔用，可直接送出"
    pid = holder.get("pid")
    if not pid:
        return False, "持有者資訊沒有 pid，無法接管"
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, text=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError as e:
        return False, f"taskkill 失敗：{e}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session_holder(session_id) is None:
            return True, f"已接管（關閉 pid {pid}）"
        time.sleep(0.3)
    return False, f"關了 pid {pid}，但鎖還沒釋放（可能 VSCode 又重連），請稍後再試"
