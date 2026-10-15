# -*- coding: utf-8 -*-
"""api.py — ClaudeResumer 的 Python API：視窗上每一顆按鈕、CLI 每一個子指令，都對到這裡的一個函式。

本人（2026-10-08）：「畫面上每一個按鈕某種程度上都是一個功能，應該抽成可以被呼叫的介面，讓 agent 在需要的時刻
決定用哪一個去做搶佔或 continue」「先不論功能是否正確能執行，先把 API 開出來，現在要用了；要先確認函數在哪裡。」
按鈕、CLI、這裡三者的對照表在 docs/api.md。

    from claude_timer import api

    r = api.sessions(cwd=r"C:\\Users\\me\\Documents\\MyGame")
    if r.ok:
        for s in r.data["sessions"]:
            print(s["session_id"], s["label"], s["branch"], s["busy"])

    r = api.resume(session_id, prompt="continue", at="06:00", take_over=True)

**每個函式都回 Result，不丟例外**（參數錯也是 code 4）：
    code  CLI 的結束碼——0 成功／1 失敗／2 找不到／3 等待中（對話被佔用、還沒到時間）／4 參數不對
    data  跟 `python -m claude_timer.cli <子指令> --json` 印出來的同一個物件
    text  給人看的那一行（CLI 不加 --json 時印的）
它們跟 CLI 走同一條程式（cli.run），不會有「API 說可以、CLI 說不行」。

**誰能叫、叫多大的令，這裡不管**：會關掉別人正在用的對話（release、take_over）、會花額度（resume、new、fire）的，
呼叫端（ClawBot、PipelineAgent、你自己的腳本）自己決定要不要問人、要不要驗證碼。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import cli

EXIT_OK, EXIT_FAILED, EXIT_NOT_FOUND, EXIT_WAIT, EXIT_BAD = (cli.EXIT_OK, cli.EXIT_FAILED, cli.EXIT_NOT_FOUND,
                                                           cli.EXIT_WAIT, cli.EXIT_BAD)


@dataclass
class Result:
    code: int
    data: dict = field(default_factory=dict)
    text: str = ""

    @property
    def ok(self) -> bool:
        return self.code == EXIT_OK

    @property
    def waiting(self) -> bool:
        """對話正被別的 Claude Code 開著（或排程還沒到）：不是失敗，晚一點會自己補送。"""
        return self.code == EXIT_WAIT

    @property
    def error(self) -> str:
        return str(self.data.get("error") or "")


def _call(*argv: str) -> Result:
    code, data, text = cli.run([str(a) for a in argv])
    return Result(code, data if isinstance(data, dict) else {"value": data}, text)


def _flags(*, at: Optional[str], permission_mode: str, allowed_tools: str, disallowed_tools: str,
           max_budget_usd: float, dry_run: bool) -> List[str]:
    out: List[str] = []
    if at:
        out += ["--at", at]
    out += ["--permission-mode", permission_mode]
    if allowed_tools:
        out += ["--allowed-tools", allowed_tools]
    if disallowed_tools:
        out += ["--disallowed-tools", disallowed_tools]
    if max_budget_usd:
        out += ["--max-budget-usd", str(max_budget_usd)]
    if dry_run:
        out.append("--dry-run")
    return out


# ── 看（不花錢、不改任何東西）──────────────────────────────────────────

def sessions(cwd: Optional[str] = None, limit: int = 200, include_hidden: bool = False) -> Result:
    """① 對話清單（視窗的清單＋「重新整理」「載入更多」「藏起暫存與程式用的」）。data["sessions"]。"""
    argv = ["sessions", "--limit", str(limit)]
    if cwd:
        argv += ["--cwd", cwd]
    if include_hidden:
        argv.append("--all")
    return _call(*argv)


def show(session_id: str, turns: int = 0) -> Result:
    """① 「查看內容」：摘要；turns＞0 多給最近幾輪「你說什麼 → 它最後回什麼」（data["last_turns"]）。"""
    return _call("show", session_id, "--turns", str(turns)) if turns else _call("show", session_id)


def check(session_id: str) -> Result:
    """① 「檢查可否送出」：執行檔、對話檔、資料夾、有沒有被佔用、佔用的閒置多久。被佔用時 code 3。"""
    return _call("check", session_id)


def jobs(limit: int = 50) -> Result:
    """CLI 排的工作清單（data["jobs"]）。視窗版的佇列只活在視窗裡，不在這裡。"""
    return _call("jobs", "--limit", str(limit))


def job(job_id: str) -> Result:
    """一筆工作的結果：做完了沒、它最後說了什麼、花多少、改了哪些檔案（data["changes"]）。"""
    return _call("job", job_id)


# ── 動手 ──────────────────────────────────────────────────────────────

def release(session_id: str, *, even_busy: bool = False) -> Result:
    """① 「釋放佔用（不送出）」：關掉佔用這個對話的 Claude Code 行程，什麼都不送。

    **破壞性**：那個對話在 VSCode 裡沒存、正在跑的東西會一起沒掉。只在人說「這個用完了、清掉它」時叫。
    """
    return _call("release", session_id, *(["--even-busy"] if even_busy else []))


def resume(session_id: str, prompt: str = "continue", at: Optional[str] = None, *, take_over: bool = False,
           fork: bool = False, permission_mode: str = "inherit", allowed_tools: str = "",
           disallowed_tools: str = "", max_budget_usd: float = 0.0, require_clean: bool = True,
           dry_run: bool = False) -> Result:
    """② ③ 接續一個對話：at 不給＝現在送（「立即送出」），給了＝排時間（「加入排程佇列」）。

    at：+30（分鐘後）、09:00（今天，過了就明天）、2026-10-04 09:00。
    被佔用時（② 的「被佔用時」）：預設等它關掉再補送；take_over=True＝到點還被佔用就關掉持有者再送
    （「時間到就強制接管」；**破壞性**）；fork=True＝不搶，用 --fork-session 續在新的對話 id 底下（**未實測**
    能不能繞過鎖）。「立刻接管並送出」＝先 release() 再 resume()。
    """
    argv = ["resume", session_id, "--prompt", prompt]
    argv += _flags(at=at, permission_mode=permission_mode, allowed_tools=allowed_tools,
                   disallowed_tools=disallowed_tools, max_budget_usd=max_budget_usd, dry_run=dry_run)
    if take_over:
        argv.append("--take-over")
    if fork:
        argv.append("--fork")
    if not require_clean:
        argv.append("--no-require-clean")
    return _call(*argv)


def new(cwd: str, prompt: str, at: Optional[str] = None, *, name: str = "", sandbox: str = "worktree",
        permission_mode: str = "acceptEdits", allowed_tools: str = "", disallowed_tools: str = "",
        max_budget_usd: float = 0.0, dry_run: bool = False) -> Result:
    """在專案開一個**全新的對話**做新任務（視窗版沒有這顆按鈕）。預設在獨立的 git worktree＋branch 跑。"""
    argv = ["new", "--cwd", cwd, "--prompt", prompt, "--sandbox", sandbox]
    if name:
        argv += ["--name", name]
    argv += _flags(at=at, permission_mode=permission_mode, allowed_tools=allowed_tools,
                   disallowed_tools=disallowed_tools, max_budget_usd=max_budget_usd, dry_run=dry_run)
    return _call(*argv)


def cancel(job_id: Optional[str] = None, *, all_jobs: bool = False) -> Result:
    """③ 「移除選取」的 CLI 版：還沒跑的不跑、正在跑的連子行程一起收掉。"""
    return _call("cancel", "--all") if all_jobs else _call("cancel", job_id or "")


def stash(job_id: str) -> Result:
    """把一筆接續做的改動收起來（git stash -u，名字 resume-<id>，找得回來）。"""
    return _call("stash", job_id)


def fire(job_id: str) -> Result:
    """現在就送出一筆已經排好的工作（工作排程器到點叫的就是這個）。已經送過的不會再送。"""
    return _call("fire", job_id)
