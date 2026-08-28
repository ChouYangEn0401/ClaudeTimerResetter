# -*- coding: utf-8 -*-
"""installer.py — ClaudeTimerResetter 的安裝器：GUI + 兩個背景進入點。

三種啟動方式（同一支 exe，靠參數分流）：
    (無參數)   開 Tkinter 主控台：編輯排程規則、選交付方式、安裝/解除安裝
    --tick     Task Scheduler 呼叫的靜默進入點：判斷有沒有規則到期，到期才打探針
    --tray     工具列常駐模式的進入點，委派給 tray.py

打包方式見 build_installer.bat（PyInstaller --onefile --windowed）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import autostart
import ping
import rules as rules_mod
import scheduler

APP_NAME = "ClaudeTimerResetter"


# ---------- 共用路徑/工具函式 ----------

def install_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / APP_NAME


def installed_exe_path() -> Path:
    return install_dir() / f"{APP_NAME}.exe"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def rules_path() -> Path:
    return install_dir() / "rules.json"


def state_path() -> Path:
    return install_dir() / "state.json"


def log_path() -> Path:
    return install_dir() / "run.log"


def append_log(line: str) -> None:
    d = install_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = log_path()
    lines: list[str] = []
    if p.exists():
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    lines.append(line)
    p.write_text("\n".join(lines[-500:]) + "\n", encoding="utf-8")


def tail_log(n: int = 10) -> str:
    p = log_path()
    if not p.exists():
        return "(尚無紀錄)"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:]) or "(尚無紀錄)"


# ---------- --tick 進入點（Task Scheduler 呼叫）----------

def run_tick_once() -> int:
    d = install_dir()
    if not d.exists():
        return 0  # 尚未安裝就不該有工作在跑，安全起見直接跳過
    rule_list = rules_mod.load_rules(rules_path())
    state = rules_mod.load_state(state_path())
    now = datetime.now()
    snap = rules_mod.snapshot(state)
    due_ids, state = rules_mod.compute_due(rule_list, state, now)
    rules_mod.save_state(state_path(), state)

    exit_code = 0
    for rid in due_ids:
        # max_wait_seconds=0：這條路是 Task Scheduler，工作跑超過 5 分鐘會被砍，
        # 不能在這裡 sleep 等重置。ping 回 waiting 時把規則狀態還原，讓下一分鐘
        # 的 tick 再判一次——用 tick 節奏代替 sleep，語意一樣。
        result = ping.refresh(max_wait_seconds=0)
        if result["action"] == "waiting":
            state = rules_mod.rollback(state, snap, rid)
            rules_mod.save_state(state_path(), state)
            append_log(f"{now.isoformat(timespec='seconds')} [WAIT] rule={rid} "
                       f"{ping.summarize(result)}")
            continue
        state = rules_mod.record_result(state, rid, result["ok"])
        rules_mod.save_state(state_path(), state)
        tag = "OK" if result["ok"] else "FAIL"
        detail = ping.summarize(result) if result["ok"] else result["probe"]["reason"]
        append_log(f"{now.isoformat(timespec='seconds')} [{tag}] rule={rid} {str(detail)[:120]}")
        if not result["ok"]:
            exit_code = result["exit_code"]
    return exit_code


# ---------- --tray 進入點 ----------

def run_tray() -> None:
    import tray
    tray.run(install_dir(), str(installed_exe_path()))


# ---------- 安裝／解除安裝的共用動作 ----------

def _kill_running_tray() -> None:
    pid_file = install_dir() / "tray.pid"
    if not pid_file.exists():
        return
    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (ValueError, OSError):
        pass
    pid_file.unlink(missing_ok=True)


def _self_delete_and_exit(exe: Path, folder: Path) -> None:
    """解除安裝時，如果目前執行的就是安裝目錄裡那份複製 exe，Windows 不給刪除
    執行中的檔案——用一個延遲執行的 helper 行程，2 秒後（本行程已結束）再刪。
    """
    cmd = f'cmd /c "timeout /t 2 /nobreak >nul & del /f /q "{exe}" & rmdir "{folder}""'
    subprocess.Popen(
        cmd,
        shell=True,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    os._exit(0)


def apply_install(rule_list: list[dict], mode: str, auto_start: bool) -> None:
    d = install_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = installed_exe_path()

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
        scheduler.install_task(str(dest))
    else:
        scheduler.uninstall_task()
        _kill_running_tray()
        if auto_start:
            autostart.enable(str(dest))
        else:
            autostart.disable()
        subprocess.Popen([str(dest), "--tray"], creationflags=subprocess.DETACHED_PROCESS)


def apply_uninstall() -> None:
    scheduler.uninstall_task()
    autostart.disable()
    _kill_running_tray()

    d = install_dir()
    for name in ("rules.json", "state.json", "run.log"):
        p = d / name
        if p.exists():
            p.unlink()

    dest = installed_exe_path()
    running_as_dest = is_frozen() and Path(sys.executable) == dest
    if running_as_dest:
        _self_delete_and_exit(dest, d)  # 不會返回
    if dest.exists():
        dest.unlink()
    try:
        d.rmdir()
    except OSError:
        pass


# ---------- GUI ----------

class RuleDialog(tk.Toplevel):
    """新增一條排程規則：下拉選型別，欄位依型別動態切換。"""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("新增規則")
        self.resizable(False, False)
        self.result: dict | None = None

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="規則類型:").grid(row=0, column=0, sticky="w")
        self.type_var = tk.StringVar(value="interval")
        type_box = ttk.Combobox(
            top, textvariable=self.type_var, state="readonly", width=16,
            values=["interval", "daily_times", "burst"],
        )
        type_box.grid(row=0, column=1, sticky="ew")
        type_box.bind("<<ComboboxSelected>>", lambda e: self._rebuild_fields())

        self.fields_frame = ttk.Frame(self, padding=10)
        self.fields_frame.pack(fill="both", expand=True)

        btns = ttk.Frame(self, padding=10)
        btns.pack(fill="x")
        ttk.Button(btns, text="確定", command=self._on_ok).pack(side="right")
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=6)

        self._rebuild_fields()
        self.transient(parent)
        self.grab_set()

    def _rebuild_fields(self) -> None:
        for w in self.fields_frame.winfo_children():
            w.destroy()
        t = self.type_var.get()
        if t == "interval":
            ttk.Label(self.fields_frame, text="每隔幾分鐘執行一次:").grid(row=0, column=0, sticky="w")
            self.minutes_var = tk.StringVar(value="60")
            ttk.Entry(self.fields_frame, textvariable=self.minutes_var, width=10).grid(row=0, column=1)
        elif t == "daily_times":
            ttk.Label(self.fields_frame, text="每天執行時間\n(HH:MM，逗號分隔):").grid(row=0, column=0, sticky="w")
            self.times_var = tk.StringVar(value="05:00, 10:00, 15:00, 20:00")
            ttk.Entry(self.fields_frame, textvariable=self.times_var, width=32).grid(row=0, column=1)
        elif t == "burst":
            ttk.Label(self.fields_frame, text="起始時間 (HH:MM):").grid(row=0, column=0, sticky="w")
            self.start_var = tk.StringVar(value="06:00")
            ttk.Entry(self.fields_frame, textvariable=self.start_var, width=10).grid(row=0, column=1, sticky="w")

            ttk.Label(self.fields_frame, text="每隔幾分鐘重試:").grid(row=1, column=0, sticky="w")
            self.interval_var = tk.StringVar(value="15")
            ttk.Entry(self.fields_frame, textvariable=self.interval_var, width=10).grid(row=1, column=1, sticky="w")

            ttk.Label(self.fields_frame, text="停止條件:").grid(row=2, column=0, sticky="w")
            self.stop_mode_var = tk.StringVar(value="count")
            ttk.Combobox(
                self.fields_frame, textvariable=self.stop_mode_var, state="readonly", width=14,
                values=["count", "until_success"],
            ).grid(row=2, column=1, sticky="w")

            ttk.Label(self.fields_frame, text="次數上限:").grid(row=3, column=0, sticky="w")
            self.limit_var = tk.StringVar(value="5")
            ttk.Entry(self.fields_frame, textvariable=self.limit_var, width=10).grid(row=3, column=1, sticky="w")
            ttk.Label(
                self.fields_frame, text="(選「直到成功為止」時，這是防呆用的安全上限，\n避免探針一直失敗時整天無限重打 API)",
                foreground="#666", justify="left",
            ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _on_ok(self) -> None:
        t = self.type_var.get()
        try:
            if t == "interval":
                minutes = int(self.minutes_var.get())
                if minutes < 1:
                    raise ValueError
                self.result = {"id": rules_mod.new_id(), "type": "interval", "minutes": minutes}
            elif t == "daily_times":
                times = [x.strip() for x in self.times_var.get().split(",") if x.strip()]
                if not times:
                    raise ValueError
                for tm in times:
                    datetime.strptime(tm, "%H:%M")
                self.result = {"id": rules_mod.new_id(), "type": "daily_times", "times": times}
            elif t == "burst":
                datetime.strptime(self.start_var.get(), "%H:%M")
                interval = int(self.interval_var.get())
                limit = int(self.limit_var.get())
                if interval < 1 or limit < 1:
                    raise ValueError
                mode = self.stop_mode_var.get()
                stop = (
                    {"mode": "count", "count": limit}
                    if mode == "count"
                    else {"mode": "until_success", "max_attempts": limit}
                )
                self.result = {
                    "id": rules_mod.new_id(),
                    "type": "burst",
                    "start_time": self.start_var.get(),
                    "interval_minutes": interval,
                    "stop": stop,
                }
        except ValueError:
            messagebox.showerror(
                "輸入錯誤", "請確認欄位格式正確（時間用 HH:MM，數字需為正整數）", parent=self
            )
            return
        self.destroy()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ClaudeTimerResetter 安裝器")
        self.geometry("560x600")
        self.rule_list: list[dict] = rules_mod.load_rules(rules_path())

        ttk.Label(self, text=f"安裝路徑: {install_dir()}", foreground="#666").pack(
            anchor="w", padx=10, pady=(10, 0)
        )

        list_frame = ttk.LabelFrame(self, text="排程規則", padding=8)
        list_frame.pack(fill="both", expand=True, padx=10, pady=8)
        self.listbox = tk.Listbox(list_frame, height=8)
        self.listbox.pack(fill="both", expand=True, side="left")
        scrollbar = ttk.Scrollbar(list_frame, command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scrollbar.set)

        rule_btns = ttk.Frame(self)
        rule_btns.pack(fill="x", padx=10)
        ttk.Button(rule_btns, text="新增規則", command=self._add_rule).pack(side="left")
        ttk.Button(rule_btns, text="刪除選取規則", command=self._remove_rule).pack(side="left", padx=6)

        mode_frame = ttk.LabelFrame(self, text="執行方式", padding=8)
        mode_frame.pack(fill="x", padx=10, pady=8)
        self.mode_var = tk.StringVar(value=self._detect_mode())
        ttk.Radiobutton(
            mode_frame, text="工作排程器（背景執行，不需保持視窗開啟）",
            variable=self.mode_var, value="scheduler",
        ).pack(anchor="w")
        ttk.Radiobutton(
            mode_frame, text="工具列常駐（小圖示常駐在工具列運作）",
            variable=self.mode_var, value="tray",
        ).pack(anchor="w")
        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(
            mode_frame, text="開機時自動啟動工具列常駐", variable=self.autostart_var,
        ).pack(anchor="w", padx=20)

        action_btns = ttk.Frame(self)
        action_btns.pack(fill="x", padx=10, pady=4)
        ttk.Button(action_btns, text="立即測試", command=self._test_now).pack(side="left")
        ttk.Button(action_btns, text="安裝／更新設定", command=self._install).pack(side="left", padx=6)
        ttk.Button(action_btns, text="解除安裝", command=self._uninstall).pack(side="left")

        self.status_var = tk.StringVar(value="尚未測試")
        ttk.Label(self, textvariable=self.status_var, wraplength=520, justify="left").pack(
            anchor="w", padx=10, pady=(4, 0)
        )

        log_frame = ttk.LabelFrame(self, text="最近執行紀錄", padding=8)
        log_frame.pack(fill="both", expand=True, padx=10, pady=8)
        self.log_text = tk.Text(log_frame, height=8, state="disabled")
        self.log_text.pack(fill="both", expand=True)

        self._refresh_rule_list()
        self._refresh_log()

    def _detect_mode(self) -> str:
        return "scheduler" if scheduler.task_exists() else "tray"

    def _refresh_rule_list(self) -> None:
        self.listbox.delete(0, tk.END)
        for r in self.rule_list:
            self.listbox.insert(tk.END, rules_mod.describe(r))

    def _refresh_log(self) -> None:
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, tail_log())
        self.log_text.config(state="disabled")

    def _add_rule(self) -> None:
        dlg = RuleDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            self.rule_list.append(dlg.result)
            self._refresh_rule_list()

    def _remove_rule(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        del self.rule_list[sel[0]]
        self._refresh_rule_list()

    def _test_now(self) -> None:
        self.status_var.set("測試中，請稍候（約需 5 秒）...")
        self.update()
        # 跟排程走同一條路：已經刷新過就不打，按幾次都不會多花錢。
        result = ping.refresh(max_wait_seconds=0)
        if not result["ok"]:
            self.status_var.set(f"[X] {result['probe']['reason']}")
            return
        if result["action"] == "refreshed":
            p = result["probe"]
            self.status_var.set(
                f"[OK] 已刷新｜模型 {p['model']}｜花費 ${p['cost_usd']:.5f}｜耗時 {p['seconds']}s"
            )
        else:
            self.status_var.set(f"[{result['action'].upper()}] {result['reason']}")

    def _install(self) -> None:
        if not self.rule_list:
            messagebox.showwarning("尚未設定規則", "請先新增至少一條排程規則", parent=self)
            return
        if not is_frozen():
            messagebox.showerror(
                "開發模式", "請先執行 build_installer.bat 打包成 exe 後再安裝", parent=self
            )
            return
        apply_install(self.rule_list, self.mode_var.get(), self.autostart_var.get())
        messagebox.showinfo("完成", "安裝／更新完成", parent=self)
        self._refresh_log()

    def _uninstall(self) -> None:
        if not messagebox.askyesno(
            "確認解除安裝", "將移除排程、工具列常駐與所有安裝檔案，確定要繼續嗎？", parent=self
        ):
            return
        apply_uninstall()  # 若目前執行的就是安裝目錄裡那份 exe，這裡不會返回
        messagebox.showinfo("完成", "已解除安裝", parent=self)
        self.destroy()


def launch_gui() -> None:
    App().mainloop()


def main() -> int:
    if "--tick" in sys.argv:
        return run_tick_once()
    if "--tray" in sys.argv:
        run_tray()
        return 0
    launch_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
