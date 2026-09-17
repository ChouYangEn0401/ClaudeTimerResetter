# -*- coding: utf-8 -*-
"""claude_timer — Claude Code 額度視窗的兩個小工具（共用同一份核心模組）。

    claude_timer.core      不帶畫面的核心：查用量、打探針、排程規則、Windows 工作排程
    claude_timer.ui        兩個 GUI 共用的外觀與元件（配色、字級、可捲動版面）
    claude_timer.resetter  ClaudeTimerResetter.exe：提前把 5 小時視窗的重置時鐘催起來
    claude_timer.resumer   ClaudeResumer.exe：時間一到，把指定對話接著送出去

進入點在專案根目錄的 resetter.py / resumer.py，這裡只放實作。
版本號是單一來源：兩個 GUI 的標題、打包產物都讀這裡。
"""
from __future__ import annotations

__version__ = "1.1.0"
APP_NAME = "ClaudeTimerResetter"
