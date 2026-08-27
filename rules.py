# -*- coding: utf-8 -*-
"""rules.py — 排程規則引擎（純標準函式庫，無外部相依）。

四種規則型別，使用者可以自由組合，不限制單一排程模式：

    interval      每隔 N 分鐘執行一次（全天，不分日期）
    daily_times   每天在固定幾個時間點執行
    burst         每天某時間點之後，每隔 N 分鐘重試，直到「次數用完」或「成功一次」為止

Task Scheduler 跟工具列常駐兩種背景執行方式（driver）都呼叫這裡的 compute_due()，
排程語意只維護一份，兩邊行為保證一致。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def _today_str(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def default_state(now: datetime | None = None) -> dict:
    return {"date": _today_str(now or datetime.now()), "rules": {}}


def load_rules(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_rules(path: Path, rule_list: list[dict]) -> None:
    path.write_text(json.dumps(rule_list, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state(path: Path) -> dict:
    if not path.exists():
        return default_state()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_state()


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _init_rule_state(rule: dict) -> dict:
    t = rule["type"]
    if t == "interval":
        return {"last_fired_at": None}
    if t == "daily_times":
        return {"fired_times": []}
    if t == "burst":
        return {"attempts": 0, "last_attempt_at": None, "succeeded": False}
    raise ValueError(f"unknown rule type: {t}")


def compute_due(rule_list: list[dict], state: dict, now: datetime) -> tuple[list[str], dict]:
    """回傳這一次 tick 到期的 rule id 清單，並回傳「已標記為觸發」後的新 state。

    呼叫端應該在真的打探針*之前*就先把回傳的 state 存檔——就算探針呼叫途中當機，
    下次 tick 也不會把同一次觸發重打一次。探針結果（成功與否）另外用
    record_result() 回填到 state，只影響 until_success 型的 burst 規則。
    """
    today = _today_str(now)
    rules_state: dict = state.setdefault("rules", {})

    if state.get("date") != today:
        for rule in rule_list:
            if rule["type"] in ("daily_times", "burst"):
                rules_state[rule["id"]] = _init_rule_state(rule)
        state["date"] = today

    due: list[str] = []
    for rule in rule_list:
        rs = rules_state.get(rule["id"])
        if rs is None:
            rs = _init_rule_state(rule)
            rules_state[rule["id"]] = rs

        if rule["type"] == "interval":
            last = rs.get("last_fired_at")
            last_dt = datetime.fromisoformat(last) if last else None
            if last_dt is None or now - last_dt >= timedelta(minutes=rule["minutes"]):
                rs["last_fired_at"] = now.isoformat()
                due.append(rule["id"])

        elif rule["type"] == "daily_times":
            now_hm = now.strftime("%H:%M")
            fired = set(rs.get("fired_times", []))
            # 一次 tick 只補一個時間點：避免補跑時（例如電腦剛醒來）一次打好幾次 API
            for t in sorted(rule["times"]):
                if t not in fired and now_hm >= t:
                    fired.add(t)
                    rs["fired_times"] = sorted(fired)
                    due.append(rule["id"])
                    break

        elif rule["type"] == "burst":
            if now.strftime("%H:%M") < rule["start_time"]:
                continue
            stop = rule["stop"]
            limit = stop["count"] if stop["mode"] == "count" else stop["max_attempts"]
            if stop["mode"] == "until_success" and rs.get("succeeded"):
                continue
            if rs.get("attempts", 0) >= limit:
                continue
            last = rs.get("last_attempt_at")
            last_dt = datetime.fromisoformat(last) if last else None
            if last_dt is None or now - last_dt >= timedelta(minutes=rule["interval_minutes"]):
                rs["attempts"] = rs.get("attempts", 0) + 1
                rs["last_attempt_at"] = now.isoformat()
                due.append(rule["id"])

    return due, state


def record_result(state: dict, rule_id: str, ok: bool) -> dict:
    """探針跑完後回填成功與否，只有 burst 規則的 succeeded 欄位會用到。"""
    rs = state.get("rules", {}).get(rule_id)
    if rs is not None and "succeeded" in rs:
        rs["succeeded"] = rs.get("succeeded") or ok
    return state


def describe(rule: dict) -> str:
    """組出人看得懂的一行說明，給 GUI 規則列表跟 log 用。"""
    t = rule["type"]
    if t == "interval":
        return f"每隔 {rule['minutes']} 分鐘執行一次"
    if t == "daily_times":
        return "每天 " + ", ".join(rule["times"]) + " 執行"
    if t == "burst":
        stop = rule["stop"]
        if stop["mode"] == "count":
            tail = f"重複 {stop['count']} 次"
        else:
            tail = f"直到成功為止（最多 {stop['max_attempts']} 次）"
        return f"每天 {rule['start_time']} 起，每 {rule['interval_minutes']} 分鐘一次，{tail}"
    return "未知規則"
