# -*- coding: utf-8 -*-
"""sessions.py — 本機 Claude Code 對話的清單，以及「這個對話現在有沒有人開著」。

兩件事都純讀本機檔案，不花任何 token：

    scan_sessions()   掃 ~/.claude/projects/*/*.jsonl，給出可以挑選的對話清單
    session_holder()  讀 ~/.claude/sessions/<pid>.json，看某個對話是不是正被佔用

清單為什麼不是直接秀第一句話：Claude Code 自己會把對話標題（ai-title / custom-title）
跟最後一句使用者輸入（last-prompt）寫進同一個 .jsonl，用這些欄位比「檔案裡第一行
使用者訊息」好認太多——被 session limit 卡住的那個對話，通常是靠「最後我跟它說了
什麼」認出來的。

為什麼還要分「來源」（v1.3.0）：同一台電腦上，ClawBot 的 AI 秘書、resumer 自己的 new、
各種探針都用 `claude -p` 開對話，資料夾叫 agent_workspace、scratchpad、一串 hex；加上
worktree 分身跟主專案同名。清單上全部混在一起就是「一堆同名專案不知道哪個是哪個」。
所以每一列多記：git 分支（gitBranch）、從哪開的（entrypoint）、資料夾是哪一種
（classify_origin），同名的專案帶上一層資料夾，暫存與程式用的預設藏起來。
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..core.procs import process_image, same_process
from ..paths import projects_dir, sessions_dir

# 清單預設只帶最近這麼多個對話：768 個對話檔全部開來讀會讓視窗開啟卡住好幾秒，
# 而這支工具要找的一定是「剛剛被卡住的那個」。要更多再按「載入更多」。
DEFAULT_LIMIT = 200
# 每個檔案讀頭尾各一小段就夠了（cwd 在最前面，標題與最後一句在最後面）。
HEAD_LINES = 60
TAIL_BYTES = 96 * 1024


ORIGIN_PROJECT, ORIGIN_WORKTREE, ORIGIN_APP, ORIGIN_TEMP, ORIGIN_UNKNOWN = "project", "worktree", "app", "temp", "unknown"
ORIGIN_LABELS = {ORIGIN_PROJECT: "", ORIGIN_WORKTREE: "分身", ORIGIN_APP: "程式用", ORIGIN_TEMP: "暫存",
                 ORIGIN_UNKNOWN: "？"}
# 對話是從哪裡開的（.jsonl 裡每一則的 entrypoint）：VSCode 擴充、終端機、還是程式用 -p 開的（ClawBot、
# resumer 自己、各種探針）。同一個資料夾會同時有這三種，不標出來就會「一堆同名專案不知道哪個是哪個」。
ENTRY_LABELS = {"claude-vscode": "VSCode", "cli": "終端", "sdk-cli": "程式(-p)", "sdk-ts": "程式", "sdk-py": "程式"}
# 清單預設藏起來的來源：暫存資料夾與程式自己的工作區裡的對話，幾乎不會是你要接的那一個。
HIDDEN_BY_DEFAULT = (ORIGIN_TEMP, ORIGIN_APP)


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
    branch: str = ""                 # 對話當時的 git 分支（.jsonl 的 gitBranch）
    entrypoint: str = ""             # claude-vscode / cli / sdk-cli …
    origin: str = ORIGIN_UNKNOWN     # classify_origin() 算出來的
    label: str = ""                  # 清單上顯示的專案名；同名時帶上一層資料夾（scan_sessions 算）
    holder: dict | None = field(default=None, compare=False)

    @property
    def project(self) -> str:
        return Path(self.cwd).name if self.cwd else "(無專案路徑)"

    @property
    def display(self) -> str:
        """清單上的專案欄：有算出 label 就用它，否則退回資料夾名。"""
        return self.label or self.project

    @property
    def source_label(self) -> str:
        """「來源」欄：分身·VSCode、程式用·程式(-p)、VSCode……一眼看出是不是你自己開的那個。"""
        where = ORIGIN_LABELS.get(self.origin, "")
        how = ENTRY_LABELS.get(self.entrypoint, self.entrypoint or "")
        return "·".join(b for b in (where, how) if b)

    @property
    def hidden_by_default(self) -> bool:
        return self.origin in HIDDEN_BY_DEFAULT

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
    assign_labels(out)
    return out, len(paths)


def classify_origin(cwd: str) -> str:
    """這個對話的資料夾是哪一種：你的專案、專案的分身（worktree）、程式自己的工作區、暫存。

    純看路徑，不讀 git 設定（分身的 .git 是一個檔案而不是資料夾，看得出來；資料夾已經不在了就只看名字）。
    """
    if not cwd:
        return ORIGIN_UNKNOWN
    norm = os.path.normcase(os.path.abspath(cwd))
    for var in ("TEMP", "TMP"):
        root = os.environ.get(var)
        if root and _under(norm, root):
            return ORIGIN_TEMP
    # 排程器跑起來時 TEMP 可能指到別處：使用者自己的暫存資料夾照路徑認，非 Windows 看 /tmp
    if _under(norm, str(Path.home() / "AppData" / "Local" / "Temp")) or (norm + "/").startswith("/tmp/"):
        return ORIGIN_TEMP
    local = os.environ.get("LOCALAPPDATA")
    if local and _under(norm, local):
        return ORIGIN_APP
    parts = {p.lower() for p in Path(cwd).parts}
    if parts & {"worktrees", "_worktrees", ".worktrees"}:
        return ORIGIN_WORKTREE
    try:
        if (Path(cwd) / ".git").is_file():
            return ORIGIN_WORKTREE
    except OSError:
        pass
    return ORIGIN_PROJECT


def _under(norm_path: str, root: str) -> bool:
    root = os.path.normcase(os.path.abspath(root)).rstrip("\\/")
    return norm_path == root or norm_path.startswith(root + os.sep) or norm_path.startswith(root + "/")


def assign_labels(sessions: list[SessionInfo]) -> None:
    """同名的專案資料夾（例如兩個不同地方都叫 WebApp）在清單上帶上一層資料夾，才分得出來。

    暫存與程式用的資料夾名字本來就沒意義（scratchpad、agent_workspace、一串 hex），不特別處理，
    靠「來源」欄分辨。
    """
    by_name: dict[str, set[str]] = {}
    for s in sessions:
        if s.origin in (ORIGIN_PROJECT, ORIGIN_WORKTREE) and s.cwd:
            by_name.setdefault(s.project.lower(), set()).add(os.path.normcase(os.path.abspath(s.cwd)))
    for s in sessions:
        name = s.project
        if s.origin in (ORIGIN_PROJECT, ORIGIN_WORKTREE) and s.cwd and len(by_name.get(name.lower(), ())) > 1:
            parent = Path(s.cwd).parent.name
            name = f"{parent}/{name}" if parent else name
        s.label = name


def _peek(path: Path, mtime: float, size: int) -> SessionInfo | None:
    """讀頭尾各一小段，湊出清單要顯示的欄位。讀不到有意義的內容就跳過這個檔。"""
    cwd = ""
    first_prompt = ""
    branch = ""
    entrypoint = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= HEAD_LINES or (cwd and first_prompt and branch and entrypoint):
                    break
                rec = _loads(line)
                if rec is None:
                    continue
                if not cwd and rec.get("cwd"):
                    cwd = rec["cwd"]
                if not branch and rec.get("gitBranch"):
                    branch = str(rec["gitBranch"])
                if not entrypoint and rec.get("entrypoint"):
                    entrypoint = str(rec["entrypoint"])
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
        branch=branch,
        entrypoint=entrypoint,
        origin=classify_origin(cwd),
        label=Path(cwd).name if cwd else "",
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

def _holder_alive(pid: int, proc_start) -> bool:
    """這份 <pid>.json 的行程還在嗎？

    pid 會被作業系統重用：以前只看「pid 活著而且叫 claude」，VSCode 開開關關幾次之後，別的對話的
    claude 行程拿到同一個 pid，這份殘留檔就會讓一個早就關掉的對話一直顯示「被佔用」，怎麼關都
    釋放不了（2026-10-08 本人踩到）。Claude Code 2.1.29x 會把行程建立時間寫進 procStart，
    跟 GetProcessTimes 對得上才算同一個行程；沒有 procStart 的舊版才退回看執行檔名。

    非 Windows 或查不到時保守地當作「還活著」——寧可多擋一次，也不要白送一次必然失敗的接續。
    """
    if platform.system() != "Windows":
        return True
    try:
        created = int(proc_start) if proc_start else 0
    except (TypeError, ValueError):
        created = 0
    try:
        if created:
            return same_process(int(pid), created)
        image = process_image(int(pid))
        if image is None:
            return False  # 開不了＝行程不在了＝這份 session 檔是殘留
        return ("claude" in image) or (image == "")
    except Exception:
        return True


def active_holders() -> dict[str, dict]:
    """一次讀完 ~/.claude/sessions/，回傳 {sessionId: 持有者資訊}。

    每個正在跑的 Claude Code 行程（含 VSCode 擴充）都會寫一份 <pid>.json，裡面有它現在開著的
    sessionId、行程建立時間、狀態（idle／busy）與最後一次動作的時間。整份一次讀進來，清單上
    幾百列就不用各自再掃一次資料夾。純讀本機檔案，不花任何 token。
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
        if not sid or not pid or not _holder_alive(pid, rec.get("procStart")):
            continue
        holders[sid] = {
            "pid": pid,
            "entrypoint": rec.get("entrypoint", "?"),
            "cwd": rec.get("cwd", ""),
            "name": rec.get("name", ""),
            "status": rec.get("status", ""),
            "updated_at": rec.get("statusUpdatedAt") or rec.get("updatedAt") or 0,
        }
    return holders


def session_holder(session_id: str) -> dict | None:
    """這個 session 現在是不是正被某個活著的 Claude Code 行程佔用？

    回傳持有者資訊（pid / entrypoint / cwd / status），沒有被佔用則回 None。
    """
    return active_holders().get(session_id)


def holder_state(holder: dict) -> str:
    """「閒置中（最後動作 25 分鐘前）」或「正在跑」：要不要釋放它，看這一句最準。"""
    status = str(holder.get("status") or "")
    updated = holder.get("updated_at") or 0
    since = ""
    try:
        if updated:
            since = humanize_since(float(updated) / 1000.0)
    except (TypeError, ValueError):
        since = ""
    if status == "idle":
        return f"閒置中（最後動作 {since}）" if since else "閒置中"
    if status == "busy":
        return f"正在跑（{since}）" if since else "正在跑"
    return ""


def describe_holder(holder: dict) -> str:
    where = "VSCode" if str(holder.get("entrypoint", "")).endswith("vscode") else holder.get("entrypoint", "?")
    name = f"「{holder['name']}」" if holder.get("name") else ""
    state = holder_state(holder)
    return f"目前正開在 {where}（pid {holder['pid']}）{name}" + (f"，{state}" if state else "")


def _kill_tree(pid: int) -> list[tuple[int, str]]:
    """關掉 pid 與它所有的子孫行程（taskkill /T /F），回傳關掉之前列到的子孫 [(pid, 名稱)]。丟 OSError。"""
    from claude_timer.core.procs import descendants
    kids = descendants(int(pid))
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    return kids


# 只接管「人開著的」對話。程式開的（claude -p：PipelineAgent 的節點、另一筆 Resumer 工作）有自己的主人在追蹤它，
# 從這裡殺掉它，那邊就會看到一段莫名其妙中斷的 session（v1.4）。
INTERACTIVE_ENTRYPOINTS = ("claude-vscode", "cli")


def release_session(session_id: str, timeout: float = 10.0, *, allow_busy: bool = False) -> tuple[bool, str]:
    """把佔用這個對話的行程（連同它的子程序）關掉、等鎖真的釋放。**不送任何東西**。

    Claude Code 的鎖是「誰的行程還活著誰持有」，沒有禮貌接管的 API——要拿走只能讓現在的持有者
    結束（破壞性：那個對話在 VSCode 裡沒存／正在跑的東西會一起沒掉）。所以先擋兩種不該殺的（v1.4）：

    - **程式開的**（entrypoint 不是 VSCode／終端機）：一律不碰，回 False——呼叫端照「被佔用」處理，它結束後補送。
    - **正在跑的**（status busy）：排程到點的接管不殺，回 False 等下一次重試（等它停下來，例如額度用完）；
      只有人自己按「立刻接管」「釋放佔用」時（`allow_busy=True`）才殺。2026-10-15 03:51 就是到點把一個正在
      做事的對話從中間砍斷。

    殺的時候用 taskkill /T：它開的 bash、背景測試、自己起的伺服器一起收，不留沒人管的孤兒；收掉的名字寫進回傳訊息。
    然後輪詢 session_holder() 直到回 None 才算釋放成功。kill 之後 VSCode 擴充有可能馬上重連把 session 搶回去，
    所以會等到逾時；逾時仍未釋放就回失敗，讓呼叫端不要硬送一次必然失敗的接續。
    """
    holder = session_holder(session_id)
    if holder is None:
        return True, "目前沒有被佔用"
    pid = holder.get("pid")
    if not pid:
        return False, "持有者資訊沒有 pid，無法釋放"
    entry = str(holder.get("entrypoint") or "")
    if entry not in INTERACTIVE_ENTRYPOINTS:
        return False, (f"這個對話正被程式開著（{entry or '?'}，pid {pid}：claude -p，例如 PipelineAgent 的節點或另一筆 "
                       "Resumer 工作），不接管；它結束後會自動補送")
    if holder.get("status") == "busy" and not allow_busy:
        return False, f"{describe_holder(holder)}：正在做事，不從中間砍斷；等它停下來（例如額度用完）再接管，會再試"
    try:
        kids = _kill_tree(int(pid))
    except OSError as e:
        return False, f"taskkill 失敗：{e}"
    with_kids = ""
    if kids:
        names: dict[str, int] = {}
        for _, name in kids:
            names[name] = names.get(name, 0) + 1
        with_kids = "，連帶關掉 " + "、".join(f"{n}×{c}" if c > 1 else n for n, c in sorted(names.items()))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session_holder(session_id) is None:
            return True, f"已釋放（關閉 pid {pid}{with_kids}）"
        time.sleep(0.3)
    return False, f"關了 pid {pid}{with_kids}，但鎖還沒釋放（可能 VSCode 又重連），請稍後再試"
