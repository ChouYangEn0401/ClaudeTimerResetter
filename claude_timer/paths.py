# -*- coding: utf-8 -*-
"""paths.py — 所有「東西放哪裡」的單一來源。

兩支工具共用同一個資料夾 %LOCALAPPDATA%\ClaudeTimerResetter\：

    ClaudeTimerResetter.exe   安裝時複製過去的本體（排程叫的是這一份）
    rules.json / state.json   排程規則與規則狀態
    run.log                   resetter 每次 tick 的結果
    resumer.log               resumer 的操作紀錄
    resume-<task>.log         每一筆接續任務的 claude 輸出

放在 LOCALAPPDATA 而不是程式旁邊，是因為 exe 可能被放在唯讀或會被刪掉的位置
（例如下載資料夾），設定跟紀錄不該跟著一起消失。
"""
from __future__ import annotations

import os
from pathlib import Path

from . import APP_NAME


def install_dir() -> Path:
    """安裝／資料目錄。沒有 LOCALAPPDATA（非 Windows）時退回家目錄，方便開發時試跑。"""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / APP_NAME


def ensure_install_dir() -> Path:
    d = install_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def installed_exe() -> Path:
    return install_dir() / f"{APP_NAME}.exe"


def rules_path() -> Path:
    return install_dir() / "rules.json"


def state_path() -> Path:
    return install_dir() / "state.json"


def run_log() -> Path:
    return install_dir() / "run.log"


def resumer_log() -> Path:
    return install_dir() / "resumer.log"


def resume_task_log(task_id: str) -> Path:
    return install_dir() / f"resume-{task_id}.log"


def claude_home() -> Path:
    return Path.home() / ".claude"


def projects_dir() -> Path:
    """Claude Code 的對話記錄：~/.claude/projects/<專案>/<session-id>.jsonl"""
    return claude_home() / "projects"


def sessions_dir() -> Path:
    """正在跑的 Claude Code 行程各自寫一份 ~/.claude/sessions/<pid>.json（含 sessionId）。"""
    return claude_home() / "sessions"
