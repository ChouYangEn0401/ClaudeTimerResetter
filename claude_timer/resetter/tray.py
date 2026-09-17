# -*- coding: utf-8 -*-
"""tray.py — 工具列常駐模式。

背景執行緒每 ~25 秒呼叫一次 service.run_due_rules()，跟工作排程器那條路（--tick）
走完全同一份規則判定，兩種交付方式的排程語意保證一致。

圖示用 pystray + Pillow 現畫，不需要額外準備 .ico 檔。
"""
from __future__ import annotations

import os
import subprocess
import threading
from datetime import datetime

import pystray
from PIL import Image, ImageDraw

from ..core import ping
from ..paths import ensure_install_dir, install_dir, installed_exe
from . import service

TICK_SECONDS = 25


def _icon_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(214, 122, 62, 255))
    d.text((24, 18), "C", fill=(255, 255, 255, 255))
    return img


def _worker(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            service.run_due_rules(tag=" tray")
        except Exception as e:  # 背景執行緒絕不能因為單次例外整個掛掉
            service.append_log(f"{datetime.now().isoformat(timespec='seconds')} [ERROR] tray tick: {e}")
        stop_event.wait(TICK_SECONDS)


def run() -> None:
    ensure_install_dir()
    pid_path = install_dir() / "tray.pid"
    pid_path.write_text(str(os.getpid()), encoding="ascii")

    stop_event = threading.Event()
    threading.Thread(target=_worker, args=(stop_event,), daemon=True).start()

    def open_console(_icon, _item) -> None:
        subprocess.Popen([str(installed_exe())])

    def test_now(_icon, _item) -> None:
        result = ping.refresh(max_wait_seconds=0)
        level = "OK" if result["ok"] else "FAIL"
        detail = ping.summarize(result) if result["ok"] else result["probe"]["reason"]
        service.append_log(f"{datetime.now().isoformat(timespec='seconds')} "
                           f"[{level}] manual test {str(detail)[:120]}")

    def quit_app(icon, _item) -> None:
        stop_event.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("開啟主控台", open_console),
        pystray.MenuItem("立即測試", test_now),
        pystray.MenuItem("結束程式", quit_app),
    )
    icon = pystray.Icon("ClaudeTimerResetter", _icon_image(), "ClaudeTimerResetter", menu)
    try:
        icon.run()
    finally:
        pid_path.unlink(missing_ok=True)
