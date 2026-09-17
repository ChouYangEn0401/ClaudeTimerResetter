# -*- coding: utf-8 -*-
"""resumer.py — ClaudeResumer.exe 的進入點（時間到，把指定對話接著送出去）。

    .venv\\Scripts\\python resumer.py     開排程器 GUI（沒有其他參數）

實作在 claude_timer.resumer；這個檔案刻意只做轉接，讓 PyInstaller 有一個明確、
穩定的 script 入口（見 packaging/ClaudeResumer.spec）。
"""
from __future__ import annotations

import sys

from claude_timer.resumer.app import main

if __name__ == "__main__":
    sys.exit(main())
