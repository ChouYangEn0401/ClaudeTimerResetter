# -*- coding: utf-8 -*-
"""journal.py — resumer 的檔案紀錄。

GUI 是 --windowed 打包的，沒有主控台，所以「發生了什麼」只有兩個去處：畫面上的
狀況欄，跟這個檔案。排程排在半夜的時候，隔天早上能查的就只剩這個檔案。
"""
from __future__ import annotations

from datetime import datetime

from ..paths import ensure_install_dir, resumer_log


def stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write(line: str) -> None:
    ensure_install_dir()
    try:
        with open(resumer_log(), "a", encoding="utf-8") as f:
            f.write(f"{stamp()} {line}\n")
    except OSError:
        pass  # 寫不進紀錄檔不該讓工具本身停擺
