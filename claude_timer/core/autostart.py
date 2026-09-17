# -*- coding: utf-8 -*-
"""autostart.py — 開機自動啟動工具列常駐模式。

不用 pywin32、不用系統管理員權限：在使用者自己的 Startup 資料夾丟一個 .bat，
Windows 登入時會自動執行。移除就是把這個檔案刪掉，乾淨、可逆。
"""
from __future__ import annotations

import os
from pathlib import Path

_SHORTCUT_NAME = "ClaudeTimerResetter.bat"


def _startup_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _shortcut_path() -> Path:
    return _startup_dir() / _SHORTCUT_NAME


def is_enabled() -> bool:
    return _shortcut_path().exists()


def enable(exe_path: str) -> None:
    content = f'@echo off\r\nstart "" "{exe_path}" --tray\r\n'
    _shortcut_path().write_text(content, encoding="mbcs")


def disable() -> None:
    _shortcut_path().unlink(missing_ok=True)
