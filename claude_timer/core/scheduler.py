# -*- coding: utf-8 -*-
"""scheduler.py — Windows 工作排程器 (Task Scheduler) 包裝。

註冊的工作只做一件事：到點就跑一次 `<exe> --tick`。要不要真的打探針、打哪一條
規則，交給 rules.compute_due()（resetter.service 的 --tick 進入點）決定，這裡不需要懂
規則的細節。

觸發方式：優先照 rules.json 裡 daily_times 的時間點，替每個時間排一個「每天固定
時刻」的 CalendarTrigger（不是每分鐘輪詢——那個會讓人覺得像常駐/背景一直在跑）。
每個時間點外加一個 30 分鐘內每 10 分鐘的小重試窗，純粹用來 cover「觸發那一刻剛好
離 5 小時視窗重置很近、--tick 先退回」的邊界；平常那幾次補跑都是「查一下發現已經
做過了」的次秒級動作，不打 API、不花錢。

若規則裡沒有任何 daily_times（例如只有 interval / burst 這種需要細密輪詢的），才
退回舊的「每 1 分鐘」TimeTrigger，確保那些規則仍能被準時判定。

沿用專案踩過的教訓（docs/build.md 提過 cmd.exe 用 cp950 讀 .bat 會爛掉）：判斷工作是否
存在只看 `schtasks` 的 return code，不解析任何在地化文字輸出。
"""
from __future__ import annotations

import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "ClaudeTimerResetter"

_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>ClaudeTimerResetter - 到點跑一次 --tick，由 rules.json 決定要不要打探針</Description>
  </RegistrationInfo>
  <Triggers>
{triggers}  </Triggers>
  <Settings>
    <StartWhenAvailable>true</StartWhenAvailable>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
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

# 每天固定時刻。附一個「30 分鐘內每 10 分鐘」的小重試窗，處理「剛好接近重置、
# 這次先退回」的邊界；平常補跑幾乎零成本（發現已做過就直接結束）。
_CALENDAR_TRIGGER = """    <CalendarTrigger>
      <StartBoundary>{date}T{time}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
      <Repetition><Interval>PT10M</Interval><Duration>PT30M</Duration><StopAtDurationEnd>true</StopAtDurationEnd></Repetition>
    </CalendarTrigger>
"""

# 沒有 daily_times 時的退路：每 1 分鐘判一次，讓 interval / burst 規則仍準時。
_MINUTE_TRIGGER = """    <TimeTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT1M</Interval>
      </Repetition>
    </TimeTrigger>
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


def _daily_times(rule_list: list[dict] | None) -> list[str]:
    """把所有 daily_times 規則的時間點收成一組去重、排序的 HH:MM。"""
    times: set[str] = set()
    for r in rule_list or []:
        if r.get("type") == "daily_times":
            for t in r.get("times", []):
                times.add(t)
    return sorted(times)


def _build_triggers(rule_list: list[dict] | None) -> str:
    times = _daily_times(rule_list)
    if times:
        today = datetime.now().strftime("%Y-%m-%d")
        return "".join(_CALENDAR_TRIGGER.format(date=today, time=t) for t in times)
    # 沒有固定時刻的規則——退回每分鐘輪詢，interval / burst 才能被準時判定。
    start = (datetime.now() + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:00")
    return _MINUTE_TRIGGER.format(start=start)


def install_task(exe_path: str, rule_list: list[dict] | None = None) -> None:
    """建立（或覆蓋）排程工作，不需要系統管理員權限。

    有 daily_times 規則時排「每天固定時刻」觸發；否則退回每 1 分鐘。傳入 rule_list
    才能照規則排時間——舊呼叫端不傳的話仍可用（退回每分鐘），保持相容。
    """
    xml = _TASK_XML.format(triggers=_build_triggers(rule_list), exe=escape(exe_path))

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
