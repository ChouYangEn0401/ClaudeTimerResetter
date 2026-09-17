# -*- coding: utf-8 -*-
"""resetter.py — ClaudeTimerResetter.exe 的進入點（提前把額度視窗的重置時鐘催起來）。

    .venv\\Scripts\\python resetter.py            開主控台 GUI
    .venv\\Scripts\\python resetter.py --tick     工作排程器用的靜默進入點
    .venv\\Scripts\\python resetter.py --tray     工具列常駐模式

實作在 claude_timer.resetter；這個檔案刻意只做轉接，讓 PyInstaller 有一個明確、
穩定的 script 入口（見 packaging/ClaudeTimerResetter.spec）。
"""
from __future__ import annotations

import sys

from claude_timer.resetter.app import main

if __name__ == "__main__":
    sys.exit(main())
