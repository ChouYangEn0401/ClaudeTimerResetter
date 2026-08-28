# -*- coding: utf-8 -*-
"""usage.py — 免費讀出「現在用掉多少額度、幾點恢復」。

跟 ping.py 是互補的兩件事：

  ping.py   花錢（$0.00025）打一次模型 → **啟動** 5 小時視窗的重置時鐘
  usage.py  不花錢、不吃 token → **讀出** 目前視窗用了幾 %、幾點重置

資料來源是 Claude Code 自己 `/usage` 用的那支端點 `GET /api/oauth/usage`，
用本機 `~/.claude/.credentials.json` 裡的訂閱 OAuth token 呼叫。純 HTTP GET，
不經過模型，所以完全不計費。

用法：
    usage.bat                （或）  .venv\\Scripts\\python usage.py
    .venv\\Scripts\\python usage.py --json     機器可讀輸出

結束碼（沿用 ping.py 的契約）：
    0 讀到了 / 1 讀取失敗（網路、伺服器）/ 3 未登入或 token 過期 / 4 環境不對
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
TIMEOUT = 15

EXIT_OK, EXIT_FAIL, EXIT_AUTH, EXIT_ENV = 0, 1, 3, 4

# 這支端點回的視窗很多（大多是還沒開放的功能，值為 null），只挑計時器用得到的兩個。
WINDOWS = (("five_hour", "5 小時視窗"), ("seven_day", "7 天視窗"))


def _credentials_path() -> Path:
    return Path(os.path.expanduser("~")) / ".claude" / ".credentials.json"


def _read_token() -> str:
    """取出訂閱 OAuth access token。環境變數優先，其次讀 Claude Code 的憑證檔。"""
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if tok:
        return tok
    path = _credentials_path()
    if not path.exists():
        raise FileNotFoundError(
            f"找不到 {path}；請先執行一次官方 claude 登入（或設 CLAUDE_CODE_OAUTH_TOKEN）"
        )
    blob = json.loads(path.read_text(encoding="utf-8"))
    oauth = blob.get("claudeAiOauth") or blob
    tok = oauth.get("accessToken") or oauth.get("access_token")
    if not tok:
        raise KeyError(f"{path} 裡沒有 accessToken 欄位")
    return tok


def _parse_window(raw: dict | None) -> dict | None:
    """把端點回的一個視窗轉成好用的形狀；null（功能未開放）回 None。"""
    if not isinstance(raw, dict):
        return None
    pct = raw.get("utilization")
    resets_at = raw.get("resets_at")
    out = {"percent": pct, "resets_at": resets_at, "resets_local": None, "seconds_left": None}
    if resets_at:
        # 端點給的是帶時區的 ISO 字串（...+00:00），轉成本機時間比較好讀。
        when = datetime.fromisoformat(resets_at)
        out["resets_local"] = when.astimezone().isoformat(timespec="seconds")
        out["seconds_left"] = max(0, int((when - datetime.now(timezone.utc)).total_seconds()))
    return out


def read_usage() -> dict:
    """讀一次用量，回傳結構化結果，不印任何東西。

    輸出格式刻意跟 ping.run_probe() 對齊（ok / exit_code / reason / detail），
    讓排程、GUI、tray 可以用同一套判斷邏輯處理兩支探針。
    """
    try:
        token = _read_token()
    except (FileNotFoundError, KeyError, json.JSONDecodeError, OSError) as e:
        return {"ok": False, "exit_code": EXIT_ENV, "reason": "讀不到本機憑證", "detail": str(e)}

    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "anthropic-beta": OAUTH_BETA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 403):
            return {"ok": False, "exit_code": EXIT_AUTH,
                    "reason": "token 過期或無效（開一次 Claude Code 就會自動換新）",
                    "detail": f"HTTP {e.code} {body}"}
        return {"ok": False, "exit_code": EXIT_FAIL,
                "reason": f"端點回錯誤 HTTP {e.code}", "detail": body}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return {"ok": False, "exit_code": EXIT_FAIL, "reason": "讀取失敗（網路或格式）",
                "detail": str(e)}

    windows = {key: _parse_window(payload.get(key)) for key, _ in WINDOWS}
    five = windows.get("five_hour") or {}
    return {
        "ok": True,
        "exit_code": EXIT_OK,
        "windows": windows,
        # resets_at 是 null＝這個視窗還沒被任何一次呼叫開啟。對計時器來說這是關鍵訊號：
        # 視窗沒開，打探針才有意義（開了再打就是白花錢）。
        "window_open": bool(five.get("resets_at")),
        "raw": payload,
    }


def pad(label: str, width: int = 10) -> str:
    """把標籤補到固定的顯示寬度（中文算兩格），讓冒號對齊。"""
    cols = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in label)
    return label + " " * max(0, width - cols)


def fmt_left(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    h, m = divmod(seconds // 60, 60)
    return f"{h}h{m:02d}m"


def main() -> int:
    as_json = "--json" in sys.argv
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows 主控台是 cp950
        except (AttributeError, ValueError):
            pass

    result = read_usage()
    if not result["ok"]:
        if as_json:
            print(json.dumps({"ok": False, "reason": result["reason"],
                              "detail": result["detail"][:400]}, ensure_ascii=False))
        else:
            print(f"[X] {result['reason']}")
            print(f"    {result['detail'].strip()[:400]}", file=sys.stderr)
        return result["exit_code"]

    if as_json:
        print(json.dumps({"ok": True, "window_open": result["window_open"],
                          "windows": result["windows"]}, ensure_ascii=False))
        return EXIT_OK

    print("[OK] 額度用量（這次查詢不花錢）")
    for key, label in WINDOWS:
        w = result["windows"].get(key)
        if w is None:
            continue
        pct = "—" if w["percent"] is None else f"{w['percent']:.0f}%"
        when = w["resets_local"] or "尚未開始計時"
        print(f"    {pad(label)} : 已用 {pct:>4}｜重置 {when}"
              f"｜剩 {fmt_left(w['seconds_left'])}")
    if not result["window_open"]:
        print(f"    {pad('提示')} : 5 小時視窗還沒開始計時，現在打探針才有意義")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
