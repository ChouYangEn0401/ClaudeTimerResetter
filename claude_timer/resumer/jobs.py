# -*- coding: utf-8 -*-
"""jobs.py — 排好的接續／新任務，存在檔案裡，不靠任何視窗開著（CLI 用；2026-10-03）。

一筆 job 的一生：

    scheduled  建立了；有 --at 的話，Windows 工作排程器上有一個 ClaudeResumer-<id> 會到點跑 `fire <id>`
    waiting    到點了但對話正被別的 Claude Code 開著（被佔用）——排程的重複觸發每 2 分鐘補試，最多 1 小時
    sent       送出去了：claude 在背景跑（獨立行程，pid 記下來）
    done       claude 結束，log 裡有結果 JSON（回覆、花費、session id）
    failed     失敗（原因在 error）
    cancelled  取消了

內容釘死：建立時把「要做什麼」（種類、對話、資料夾、要說的話、權限模式、工具限制、預算、隔離方式）算成
content_hash 存起來；fire 時再算一次，不一樣就不跑——排程檔被改過的任務不會被當成原本那一筆執行。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from ..core.procs import process_created  # noqa: F401  （cli.py 以 store.process_created 取用）
from ..paths import ensure_install_dir, install_dir

BUSY_WAIT, BUSY_TAKEOVER, BUSY_FORK = "wait", "takeover", "fork"   # 到點時對話被佔用怎麼辦（見 tasks.py）

SCHEDULED, WAITING, SENT, DONE, FAILED, CANCELLED = "scheduled", "waiting", "sent", "done", "failed", "cancelled"
FINAL = (DONE, FAILED, CANCELLED)
_LOCK = threading.Lock()


def jobs_path() -> Path:
    return install_dir() / "resume_jobs.json"


def job_log(job_id: str) -> Path:
    return install_dir() / f"job-{job_id}.log"


@dataclass
class Job:
    id: str
    kind: str                         # "resume" 接續舊對話 / "new" 在專案開新對話
    cwd: str                          # 專案資料夾（resume：那個對話自己的資料夾）
    prompt: str
    session_id: str = ""              # resume：要接的對話；new：先決定好的新對話 id
    at: str = ""                      # ISO 時間；空字串＝建立時就送
    permission_mode: str = "acceptEdits"
    allowed_tools: str = ""
    disallowed_tools: str = ""
    max_budget_usd: float = 0.0
    require_clean: bool = True        # resume：工作目錄要乾淨（做完的改動才收得回來）
    busy_policy: str = BUSY_WAIT      # resume：到點時對話被佔用——wait 等它關掉／takeover 關掉它再送／fork 分支接續
    sandbox: str = "worktree"         # new：worktree＝在獨立的 git worktree＋branch 跑；none＝直接在 cwd
    name: str = ""
    after: str = ""                   # 預留：等另一筆 job 做完才跑（「源源不絕」那一步，下一輪做）
    content_hash: str = ""
    status: str = SCHEDULED
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    fired_at: str = ""
    finished_at: str = ""
    run_cwd: str = ""                 # 真正跑的資料夾（new＋worktree 時是 worktree）
    branch: str = ""
    start_commit: str = ""
    pid: int = 0
    pid_created: int = 0
    result: str = ""
    cost_usd: float = 0.0
    error: str = ""

    @property
    def log_path(self) -> Path:
        return job_log(self.id)

    def what(self) -> dict:
        """要做的事（不含狀態）：content_hash 從這裡算。"""
        return {"kind": self.kind, "cwd": os.path.normcase(os.path.abspath(self.cwd)), "prompt": self.prompt,
                "session_id": self.session_id, "permission_mode": self.permission_mode,
                "allowed_tools": self.allowed_tools, "disallowed_tools": self.disallowed_tools,
                "max_budget_usd": self.max_budget_usd, "require_clean": self.require_clean,
                "busy_policy": self.busy_policy, "sandbox": self.sandbox}

    def compute_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.what(), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["log"] = str(self.log_path)
        return d


def new_id() -> str:
    return secrets.token_hex(4)


@contextmanager
def _file_lock(timeout: float = 20.0) -> Iterator[None]:
    """跨行程的鎖：同一時間只有一個行程在「讀－改－寫」resume_jobs.json。

    為什麼要：ClawBot 可能同時派兩筆、工作排程器可能同一分鐘觸發好幾筆，每一筆都是一個獨立的 CLI 行程。
    以前只有行程內的鎖，兩個行程各自讀到同一份、各自寫回去，後寫的會蓋掉先寫的那一筆（job 憑空消失）；
    而且其中一個剛好在另一個 os.replace 的那一瞬間讀檔，會拿到 PermissionError、當成空的，寫回去就整份清空。
    （2026-10-08：ClawBot 的 tests/test_resumer_link.py 兩筆工作重疊時抓到的。）
    """
    ensure_install_dir()
    fh = open(install_dir() / "resume_jobs.lock", "a+b")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError("等 resume_jobs.json 的鎖等太久（別的 ClaudeResumer 行程卡住了？）")
                time.sleep(0.05)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


def load() -> Dict[str, Job]:
    """讀 job 清單。檔案不存在＝空的；別的行程正在換檔（Windows 的 PermissionError）就等一下再讀，
    **不能**當成空的——呼叫端接著寫回去，整份就沒了。內容壞掉的檔案改名留證據，再當成空的。"""
    path = jobs_path()
    for attempt in range(40):
        try:
            text = path.read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return {}
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)
    try:
        raw = json.loads(text)
    except ValueError:
        try:
            path.replace(path.with_name(f"resume_jobs.corrupt-{int(time.time())}.json"))
        except OSError:
            pass
        return {}
    known = {f for f in Job.__dataclass_fields__}
    return {k: Job(**{f: v for f, v in d.items() if f in known}) for k, d in raw.items()}


def save(jobs: Dict[str, Job]) -> None:
    """原子寫入：先寫暫存檔再取代，寫到一半當掉也不會留下半個 JSON。暫存檔帶 pid，兩個行程不會搶同一個。"""
    ensure_install_dir()
    path = jobs_path()
    tmp = path.with_name(f"resume_jobs.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({k: asdict(v) for k, v in jobs.items()}, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # 別的行程剛好在讀
            if attempt == 39:
                raise
            time.sleep(0.05)


def update(job: Job) -> None:
    with _LOCK, _file_lock():
        jobs = load()
        jobs[job.id] = job
        save(jobs)


def get(job_id: str) -> Optional[Job]:
    return load().get(job_id)


def all_jobs() -> List[Job]:
    return sorted(load().values(), key=lambda j: j.created, reverse=True)


# ── 送出去的行程還活著嗎（pid＋建立時間，pid 會被重用；core/procs.py）──────────────

def alive(job: Job) -> bool:
    created = process_created(job.pid)
    return created is not None and (not job.pid_created or created == job.pid_created)


def read_result(job: Job) -> Optional[dict]:
    """claude --output-format json 跑完時，log 就是一個 JSON 物件；還沒跑完或壞掉回 None。"""
    try:
        text = job.log_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text:
        return None
    for candidate in (text, text.splitlines()[-1]):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict) and ("result" in data or "is_error" in data):
                return data
        except ValueError:
            continue
    return None


def log_tail(job: Job, limit: int = 400) -> str:
    try:
        text = job.log_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return " ".join(text.split())[-limit:]
