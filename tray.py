# -*- coding: utf-8 -*-
"""tray.py — 工具列常駐模式（driver B）。

背景執行緒每 ~25 秒 tick 一次，呼叫跟 Task Scheduler 那條路（installer.py --tick）
完全一樣的 rules.compute_due()，確保兩種交付方式排程語意一致。工具列圖示用
pystray + Pillow 現畫，不需要額外準備 .ico 檔案。
"""
from __future__ import annotations

import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

import ping
import rules as rules_mod

TICK_SECONDS = 25


def _make_icon_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(214, 122, 62, 255))
    d.text((24, 18), "C", fill=(255, 255, 255, 255))
    return img


def _append_log(install_dir: Path, line: str) -> None:
    log_path = install_dir / "run.log"
    lines: list[str] = []
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    lines.append(line)
    log_path.write_text("\n".join(lines[-500:]) + "\n", encoding="utf-8")


def _run_due_rules(install_dir: Path, tag_prefix: str = "") -> None:
    rules_path = install_dir / "rules.json"
    state_path = install_dir / "state.json"
    rule_list = rules_mod.load_rules(rules_path)
    state = rules_mod.load_state(state_path)
    now = datetime.now()
    snap = rules_mod.snapshot(state)
    due_ids, state = rules_mod.compute_due(rule_list, state, now)
    rules_mod.save_state(state_path, state)
    for rid in due_ids:
        # 跟 installer.py --tick 同一套：不在工作執行緒裡 sleep 等重置，
        # 改成還原規則狀態、讓 25 秒後的下一輪 tick 再判一次。
        result = ping.refresh(max_wait_seconds=0)
        if result["action"] == "waiting":
            state = rules_mod.rollback(state, snap, rid)
            rules_mod.save_state(state_path, state)
            _append_log(
                install_dir,
                f"{now.isoformat(timespec='seconds')} [WAIT]{tag_prefix} rule={rid} "
                f"{ping.summarize(result)}",
            )
            continue
        state = rules_mod.record_result(state, rid, result["ok"])
        rules_mod.save_state(state_path, state)
        tag = "OK" if result["ok"] else "FAIL"
        detail = ping.summarize(result) if result["ok"] else result["probe"]["reason"]
        _append_log(
            install_dir,
            f"{now.isoformat(timespec='seconds')} [{tag}]{tag_prefix} rule={rid} {str(detail)[:120]}",
        )


def _worker(install_dir: Path, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            _run_due_rules(install_dir)
        except Exception as e:  # 背景執行緒絕不能因為單次例外整個掛掉
            _append_log(install_dir, f"{datetime.now().isoformat(timespec='seconds')} [ERROR] tray tick: {e}")
        stop_event.wait(TICK_SECONDS)


def run(install_dir: Path, exe_path: str) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    pid_path = install_dir / "tray.pid"
    pid_path.write_text(str(os.getpid()), encoding="ascii")

    stop_event = threading.Event()
    worker = threading.Thread(target=_worker, args=(install_dir, stop_event), daemon=True)
    worker.start()

    def _open_console(icon: pystray.Icon, item: pystray.MenuItem) -> None:
        subprocess.Popen([exe_path])

    def _test_now(icon: pystray.Icon, item: pystray.MenuItem) -> None:
        result = ping.refresh(max_wait_seconds=0)
        tag = "OK" if result["ok"] else "FAIL"
        detail = ping.summarize(result) if result["ok"] else result["probe"]["reason"]
        _append_log(
            install_dir,
            f"{datetime.now().isoformat(timespec='seconds')} [{tag}] manual test {str(detail)[:120]}",
        )

    def _quit(icon: pystray.Icon, item: pystray.MenuItem) -> None:
        stop_event.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("開啟主控台", _open_console),
        pystray.MenuItem("立即測試", _test_now),
        pystray.MenuItem("結束程式", _quit),
    )
    icon = pystray.Icon("ClaudeTimerResetter", _make_icon_image(), "ClaudeTimerResetter", menu)
    try:
        icon.run()
    finally:
        pid_path.unlink(missing_ok=True)
