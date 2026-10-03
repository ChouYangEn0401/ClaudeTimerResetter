# -*- coding: utf-8 -*-
"""launch.py — 把一句話送進 Claude Code 的唯一實作（GUI 的 Task.fire 與 CLI 共用）。

    claude --resume <session-id> --print "<要說的話>"         接續舊對話
    claude --session-id <新的 uuid> --print "<任務>"          在專案開一個全新的對話（id 先決定好，之後才找得到它）

兩個關鍵條件，錯一個就等於白送（見 tasks.py 開頭）：cwd 一定要是那個對話自己的專案資料夾；送出那一刻
那個對話不能正被別的 Claude Code 開著。送出的是獨立行程（DETACHED_PROCESS）：送完這支程式就可以結束，
被接續的對話會在背景自己跑完，輸出寫進 log。

測試或特殊環境可以用環境變數 CLAUDE_TIMER_CLAUDE_BIN 指定要跑哪支 claude（.py 檔會用目前的 Python 執行）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

CONFIRM_SECONDS = 60
NO_OVERRIDE = "(不覆寫)"


def claude_prefix() -> List[str]:
    override = os.environ.get("CLAUDE_TIMER_CLAUDE_BIN")
    if override:
        return [sys.executable, override] if override.lower().endswith(".py") else [override]
    from claude_subscription import find_claude_binary
    return [find_claude_binary()]


def build_argv(prompt: str, *, resume: Optional[str] = None, session_id: Optional[str] = None,
               permission_mode: Optional[str] = None, allowed_tools: Optional[str] = None,
               disallowed_tools: Optional[str] = None, max_budget_usd: Optional[float] = None,
               output_json: bool = False) -> List[str]:
    argv = claude_prefix()
    if resume:
        argv += ["--resume", resume]
    elif session_id:
        argv += ["--session-id", session_id]
    argv += ["--print", prompt]
    if permission_mode and permission_mode != NO_OVERRIDE:
        argv += ["--permission-mode", permission_mode]
    if allowed_tools:
        argv += ["--allowedTools", allowed_tools]
    if disallowed_tools:
        argv += ["--disallowedTools", disallowed_tools]
    if max_budget_usd:
        argv += ["--max-budget-usd", str(max_budget_usd)]
    if output_json:
        # log 最後就是一個 JSON（回覆、花費、session id）：CLI 的 job <id> 靠它判斷做完了沒
        argv += ["--output-format", "json"]
    return argv


def start_detached(argv: List[str], cwd: str, log_path: Path) -> subprocess.Popen:
    """開一個不屬於本程式的行程，輸出寫進 log_path。起不來丟 OSError。"""
    log = open(log_path, "w", encoding="utf-8")
    try:
        kwargs = dict(cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True)
        if sys.platform == "win32":
            # DETACHED_PROCESS 之下沒有主控台，stdin 若用繼承的會是無效 handle，所以 DEVNULL
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        return subprocess.Popen(argv, **kwargs)
    finally:
        # 子行程已經拿到自己的 handle，這邊留著只會擋住之後讀 log
        log.close()
