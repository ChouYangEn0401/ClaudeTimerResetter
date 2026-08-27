# -*- coding: utf-8 -*-
"""scheduler.py — Windows 工作排程器 (Task Scheduler) 包裝。

只註冊一件事：每 1 分鐘跑一次 `<exe> --tick`。要不要真的打探針、打哪一條規則，
交給 rules.compute_due()（installer.py 的 --tick 進入點）決定，這裡完全不用
知道規則長什麼樣子——避免把「直到成功為止」這種語意硬翻成 Task Scheduler 的
trigger schema。

沿用專案已經踩過的教訓（README 提過 cmd.exe 用 cp950 讀 .bat 會爛掉）：判斷
工作是否存在只看 `schtasks` 的 return code，不解析任何在地化文字輸出——在地化
Windows（例如繁體中文）的 /FO LIST /V 欄位名稱是翻譯過的，字串比對不安全。
"""
from __future__ import annotations

import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "ClaudeTimerResetter"

_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT1M</Interval>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Settings>
    <StartWhenAvailable>true</StartWhenAvailable>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
  </Settings>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Actions>
    <Exec>
      <Command>{exe}</Command>
      <Arguments>--tick</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def task_exists() -> bool:
    r = _run(["schtasks", "/Query", "/TN", TASK_NAME])
    return r.returncode == 0


def install_task(exe_path: str) -> None:
    """建立（或覆蓋）「每 1 分鐘跑一次 --tick」的工作，不需要系統管理員權限。"""
    start = (datetime.now() + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:00")
    xml = _XML_TEMPLATE.format(start=start, exe=escape(exe_path))

    fd, tmp_path = tempfile.mkstemp(suffix=".xml")
    try:
        with open(fd, "w", encoding="utf-16") as f:
            f.write(xml)
        r = _run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", tmp_path, "/F"])
        if r.returncode != 0:
            raise RuntimeError(f"schtasks /Create 失敗: {r.stderr.strip() or r.stdout.strip()}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def uninstall_task() -> None:
    if task_exists():
        _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
