# -*- coding: utf-8 -*-
"""service.py — resetter 不帶畫面的那一半：跑規則、寫紀錄、安裝／解除安裝。

GUI（app.py）、工作排程器（`--tick`）、工具列常駐（tray.py）三條路都走這裡的
run_due_rules()，排程語意只有一份，三邊行為保證一致——以前 app 跟 tray 各抄了一份
幾乎一樣的迴圈，改一邊忘另一邊就會不一致，所以合併在這裡。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from ..core import autostart, ping, rules as rules_mod, scheduler
from ..paths import (
    ensure_install_dir, install_dir, installed_exe, rules_path, run_log, state_path,
)

LOG_KEEP_LINES = 500


# ---------- 紀錄 ----------

def append_log(line: str) -> None:
    ensure_install_dir()
    p = run_log()
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines() if p.exists() else []
    lines.append(line)
    p.write_text("\n".join(lines[-LOG_KEEP_LINES:]) + "\n", encoding="utf-8")


def tail_log(n: int = 12) -> str:
    p = run_log()
    if not p.exists():
        return "(尚無紀錄)"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:]) or "(尚無紀錄)"


# ---------- 跑規則（三條路共用）----------

def run_due_rules(*, tag: str = "") -> list[tuple[str, dict]]:
    """判斷有沒有規則到期，到期的就跑一次 ping。回傳 [(rule_id, ping 結果)]。

    max_wait_seconds=0：不在這裡 sleep 等視窗重置。Task Scheduler 的工作 5 分鐘就會
    被系統砍掉（見 scheduler.py 的 ExecutionTimeLimit），tray 也不該讓工作執行緒卡住。
    ping 回 waiting 時把那條規則的觸發還原，讓下一輪 tick 重新判一次——用 tick 節奏
    代替 sleep，語意一樣是「時間到再確認一次」。
    """
    rule_list = rules_mod.load_rules(rules_path())
    state = rules_mod.load_state(state_path())
    now = datetime.now()
    snap = rules_mod.snapshot(state)
    due_ids, state = rules_mod.compute_due(rule_list, state, now)
    rules_mod.save_state(state_path(), state)

    stamp = now.isoformat(timespec="seconds")
    results: list[tuple[str, dict]] = []
    for rid in due_ids:
        result = ping.refresh(max_wait_seconds=0)
        results.append((rid, result))
        if result["action"] == "waiting":
            state = rules_mod.rollback(state, snap, rid)
            rules_mod.save_state(state_path(), state)
            append_log(f"{stamp} [WAIT]{tag} rule={rid} {ping.summarize(result)}")
            continue
        state = rules_mod.record_result(state, rid, result["ok"])
        rules_mod.save_state(state_path(), state)
        level = "OK" if result["ok"] else "FAIL"
        detail = ping.summarize(result) if result["ok"] else result["probe"]["reason"]
        append_log(f"{stamp} [{level}]{tag} rule={rid} {str(detail)[:120]}")
    return results


def run_tick_once() -> int:
    """Task Scheduler 的 `--tick` 進入點。回傳結束碼（有失敗才非 0）。"""
    if not install_dir().exists():
        return 0  # 還沒安裝就不該有工作在跑，安全起見直接跳過
    exit_code = 0
    for _rid, result in run_due_rules():
        if result["action"] != "waiting" and not result["ok"]:
            exit_code = result["exit_code"]
    return exit_code


# ---------- 安裝狀態 ----------

def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def tray_pid_path() -> Path:
    return install_dir() / "tray.pid"


def tray_running() -> bool:
    p = tray_pid_path()
    if not p.exists():
        return False
    try:
        pid = int(p.read_text(encoding="ascii").strip())
    except (ValueError, OSError):
        return False
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                       capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return str(pid) in (r.stdout or "")


def current_mode() -> str | None:
    """目前實際安裝的是哪一種交付方式（沒安裝回 None）。"""
    if scheduler.task_exists():
        return "scheduler"
    if autostart.is_enabled() or tray_running():
        return "tray"
    return None


def status_lines() -> list[str]:
    """給 GUI 顯示的「現在到底裝了什麼」——每行一句人話。"""
    mode = current_mode()
    if mode == "scheduler":
        head = "● 已安裝：Windows 工作排程器（背景執行，不需要開著視窗）"
    elif mode == "tray":
        head = "● 已安裝：工具列常駐" + ("（執行中）" if tray_running() else "（目前沒有在跑）")
    else:
        head = "○ 尚未安裝：下面設好規則後按「安裝／更新設定」才會開始自動執行"
    lines = [head, f"安裝位置：{install_dir()}"]
    if autostart.is_enabled():
        lines.append("開機自動啟動：已開啟")
    rule_list = rules_mod.load_rules(rules_path())
    if rule_list:
        lines.append(f"已存檔規則：{len(rule_list)} 條")
    return lines


# ---------- 安裝／解除安裝 ----------

def _kill_running_tray() -> None:
    p = tray_pid_path()
    if not p.exists():
        return
    try:
        pid = int(p.read_text(encoding="ascii").strip())
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    except (ValueError, OSError):
        pass
    p.unlink(missing_ok=True)


def _self_delete_and_exit(exe: Path, folder: Path) -> None:
    """解除安裝時，如果現在跑的就是安裝目錄裡那份 exe，Windows 不給刪執行中的檔案。
    丟一個延遲 2 秒的 helper 行程，等本行程結束後再刪。
    """
    cmd = f'cmd /c "timeout /t 2 /nobreak >nul & del /f /q "{exe}" & rmdir "{folder}""'
    subprocess.Popen(cmd, shell=True,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    os._exit(0)


def apply_install(rule_list: list[dict], mode: str, auto_start: bool) -> None:
    """把規則存檔並套用交付方式。兩種模式互斥，切換時會清掉另一種的殘留。"""
    ensure_install_dir()
    dest = installed_exe()

    if is_frozen():
        src = Path(sys.executable)
        if src != dest:
            shutil.copy2(src, dest)

    rules_mod.save_rules(rules_path(), rule_list)
    if not state_path().exists():
        rules_mod.save_state(state_path(), rules_mod.default_state())

    if mode == "scheduler":
        autostart.disable()
        _kill_running_tray()
        scheduler.install_task(str(dest), rule_list)
    else:
        scheduler.uninstall_task()
        _kill_running_tray()
        if auto_start:
            autostart.enable(str(dest))
        else:
            autostart.disable()
        subprocess.Popen([str(dest), "--tray"], creationflags=subprocess.DETACHED_PROCESS)


def apply_uninstall() -> None:
    """排程、開機捷徑、常駐行程、設定與紀錄全部清掉，系統上不留東西。"""
    scheduler.uninstall_task()
    autostart.disable()
    _kill_running_tray()

    d = install_dir()
    for name in ("rules.json", "state.json", "run.log", "resumer.log"):
        (d / name).unlink(missing_ok=True)
    for leftover in d.glob("resume-*.log"):
        leftover.unlink(missing_ok=True)

    dest = installed_exe()
    if is_frozen() and Path(sys.executable) == dest:
        _self_delete_and_exit(dest, d)  # 不會返回
    dest.unlink(missing_ok=True)
    try:
        d.rmdir()
    except OSError:
        pass


__all__ = [
    "append_log", "tail_log", "run_due_rules", "run_tick_once",
    "is_frozen", "tray_running", "current_mode", "status_lines",
    "apply_install", "apply_uninstall",
]
