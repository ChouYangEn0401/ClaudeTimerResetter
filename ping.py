# -*- coding: utf-8 -*-
"""ping.py — 讓「重置時鐘」開始跑，順便回報現在 Claude 能不能用。

這支不是無腦打一次 API。它先花 0 元查一次用量，再決定要不要花那 $0.00025：

    5 小時視窗還沒開（resets_at 是 null）  → 打，打下去重置時鐘才會開始跑
    再 15 分鐘內就要重置                    → 原地等它重置，時間到再確認一次才打
    已經開了而且還早                        → 不打，這次省下來

關鍵是最後一條：**視窗已經在跑的時候再打就是白花錢**，因為重置時鐘早就啟動了，
再打一次不會讓它提早結束。

真的要打的時候，每一項都選最省的：

  模型      claude-haiku-4-5-20251001（目前最便宜的 Claude 模型）
  工具      全關（tools=""）→ 純文字進出，agent 模式貴 15~45 倍
  thinking  不開（MAX_THINKING_TOKENS=0）
  權限      manual（工具全關，這裡只是明確表態不自動放行）
  session   不保存（persist=False）→ 不重送歷史
  提示      系統提示跟提問都壓到最短，逼模型只回一個字 "k"
  預算      單次上限 $0.02，超過直接中止

有打的話一次大約 $0.00025、4~7 秒。

用法：
    test.bat                （或）  .venv\\Scripts\\python ping.py
    test.bat --json         機器可讀輸出
    test.bat --force        不看用量，一定打（等於舊版的無條件行為）
    test.bat --no-wait      該等的時候不原地等，直接結束（排程用）
    test.bat --max-wait N   最多原地等 N 秒（預設 920＝15 分鐘再多一點點）

結束碼（沿用 claude_subscription 的契約，方便排程/腳本判斷）：
    0 沒問題（含「不用打」跟「還在等」）/ 1 呼叫失敗（額度、逾時）
    2 找不到 claude / 3 未登入 / 4 參數或環境錯
"""
from __future__ import annotations

import json
import os
import sys
import time

import usage as usage_mod

# thinking 關掉：這支只是要一個 "k"，不需要模型多想
os.environ.setdefault("MAX_THINKING_TOKENS", "0")

MODEL = "claude-haiku-4-5-20251001"   # 最便宜的一檔；換 "haiku" 則跟隨官方最新 haiku
BUDGET_USD = 0.02

# 費用幾乎全在 output token（haiku 的 output 單價是 input 的 5 倍），所以重點是讓
# 模型「別多話」。原本的 "hi" 換來一整句寒暄（23 個 output token）；改成下面
# 這組之後只回一個 "k"（4 個 token），單次從 $0.000378 降到 $0.000254，省 33%。
# 系統提示也順便從中文改英文——同樣意思的英文 token 數比中文少。
SYSTEM = "Reply with exactly: k"
PROMPT = "k"

# 重置時間落在這個範圍內，就值得原地等它，而不是白打一次現在的視窗。
NEAR_RESET_SECONDS = 15 * 60
# 等到「剛好重置」那一秒容易比伺服器早一步，多留一點緩衝再確認。
WAIT_GRACE_SECONDS = 20
DEFAULT_MAX_WAIT = NEAR_RESET_SECONDS + WAIT_GRACE_SECONDS

EXIT_OK, EXIT_FAIL, EXIT_NO_BIN, EXIT_AUTH, EXIT_ARGS = 0, 1, 2, 3, 4


def run_probe(budget_usd: float = BUDGET_USD) -> dict:
    """無條件打一次探針，回傳結構化結果，不印任何東西。

    這是「真的花錢」的那一層，不做任何要不要打的判斷；判斷在 refresh()。
    """
    try:
        from claude_subscription import (
            ClaudeAuthError, ClaudeError, ClaudeNotFoundError, ask, find_claude_binary,
        )
    except ImportError as e:
        return {
            "ok": False,
            "exit_code": EXIT_ARGS,
            "reason": "套件沒裝好：請 pip install "
                      "https://github.com/ChouYangEn0401/ClaudeLogin/releases/download/"
                      "v0.2.0/claude_subscription-0.2.0-py3-none-any.whl",
            "detail": str(e),
        }

    t0 = time.time()
    try:
        binary = find_claude_binary()
        r = ask(
            PROMPT,
            model=MODEL,
            system=SYSTEM,                # 費用幾乎全在 output，連 system 也壓到最短
            tools="",                     # 純文字進出＝最便宜
            permission_mode="manual",
            persist=False,                # 不留 session、不重送歷史
            max_budget_usd=budget_usd,
            timeout=90,
        )
    except ClaudeNotFoundError as e:
        return {"ok": False, "exit_code": EXIT_NO_BIN,
                "reason": "找不到 claude 執行檔（請安裝官方 Claude Code）", "detail": str(e)}
    except ClaudeAuthError as e:
        return {"ok": False, "exit_code": EXIT_AUTH,
                "reason": "未登入或認證失敗（請執行 claude 登入）", "detail": str(e)}
    except ClaudeError as e:
        return {"ok": False, "exit_code": EXIT_FAIL,
                "reason": "呼叫失敗（額度用完 / 逾時 / 模型錯誤）", "detail": str(e)}

    elapsed = time.time() - t0
    return {
        "ok": True,
        "exit_code": EXIT_OK,
        "model": MODEL,
        "reply": (r.text or "").strip(),
        "cost_usd": r.cost_usd,
        "seconds": round(elapsed, 2),
        "binary": binary,
    }


def decide(quota: dict, near_seconds: int = NEAR_RESET_SECONDS) -> dict:
    """看用量決定這次該做什麼：refresh（打）/ wait（等）/ skip（不打）。

    查不到用量時一律回 refresh：寧可多花 $0.00025，也不要因為讀不到狀態就漏掉
    一次該做的刷新——這支工具存在的意義就是那次刷新。
    """
    if not quota["ok"]:
        return {"action": "refresh", "wait_seconds": 0,
                "reason": f"查不到用量（{quota['reason']}），保守起見照打"}

    five = quota["windows"].get("five_hour")
    if five is None:
        return {"action": "refresh", "wait_seconds": 0,
                "reason": "端點沒給 five_hour 欄位，保守起見照打"}

    left = five.get("seconds_left")
    if not five.get("resets_at") or left is None:
        return {"action": "refresh", "wait_seconds": 0,
                "reason": "5 小時視窗還沒開始計時，打了才會啟動重置時鐘"}
    if left <= 0:
        return {"action": "refresh", "wait_seconds": 0, "reason": "5 小時視窗剛好到期"}
    if left <= near_seconds:
        return {"action": "wait", "wait_seconds": left + WAIT_GRACE_SECONDS,
                "reason": f"再 {usage_mod.fmt_left(left)} 就重置，等它重置完再打"}
    return {"action": "skip", "wait_seconds": 0,
            "reason": f"5 小時視窗還在跑（剩 {usage_mod.fmt_left(left)}），重置時鐘早就啟動了"}


def refresh(
    *,
    force: bool = False,
    max_wait_seconds: int = DEFAULT_MAX_WAIT,
    budget_usd: float = BUDGET_USD,
    notify=None,
) -> dict:
    """查用量 →（必要時原地等）→ 決定要不要打。排程／GUI／CLI 共用的進入點。

    max_wait_seconds=0 表示「不准原地等」。Task Scheduler 的 --tick 就是這樣叫的，
    因為那個工作 5 分鐘就會被系統砍掉（見 scheduler.py 的 ExecutionTimeLimit）。
    這種情況會回 action="waiting"，呼叫端下一輪再問一次，效果一樣是「時間到再
    確認一次」，只是由 1 分鐘一次的 tick 代替 sleep。

    回傳一定有 action / reason / quota；只有 action=="refreshed" 才有 probe。
    """
    quota = usage_mod.read_usage()
    plan = ({"action": "refresh", "wait_seconds": 0, "reason": "--force：不看用量一定打"}
            if force else decide(quota))

    waited = 0
    if plan["action"] == "wait":
        if max_wait_seconds <= 0 or plan["wait_seconds"] > max_wait_seconds:
            return {"ok": True, "exit_code": EXIT_OK, "action": "waiting",
                    "reason": plan["reason"], "quota": quota, "probe": None,
                    "waited_seconds": 0}
        if notify:
            notify(plan["reason"])
        time.sleep(plan["wait_seconds"])
        waited = plan["wait_seconds"]
        quota = usage_mod.read_usage()   # 等完再確認一次，別對著舊資料下決定
        plan = decide(quota)
        if plan["action"] != "refresh":
            # 等過頭了還是叫我們等／跳過，代表本機跟伺服器的時間有落差。
            # 都已經等了就別空手而回，直接打一次把新視窗開起來。
            plan = {"action": "refresh", "wait_seconds": 0,
                    "reason": "等到重置時間了，直接打一次把新視窗開起來"}

    if plan["action"] == "skip":
        return {"ok": True, "exit_code": EXIT_OK, "action": "skipped",
                "reason": plan["reason"], "quota": quota, "probe": None,
                "waited_seconds": waited}

    probe = run_probe(budget_usd=budget_usd)
    # 打完用量一定變了，重讀一次才是使用者真正想看的數字。
    quota_after = usage_mod.read_usage() if probe["ok"] else quota
    return {"ok": probe["ok"], "exit_code": probe["exit_code"], "action": "refreshed",
            "reason": plan["reason"], "quota": quota_after, "probe": probe,
            "waited_seconds": waited}


def summarize(result: dict) -> str:
    """壓成一行給 log 用（installer/tray 的 tick 都會寫這行）。"""
    action = result["action"]
    if action == "refreshed" and result["probe"] and result["probe"]["ok"]:
        return f"已刷新 ${result['probe']['cost_usd']:.5f}｜{result['reason']}"
    if action == "skipped":
        return f"沒打，省一次｜{result['reason']}"
    if action == "waiting":
        return f"等重置中｜{result['reason']}"
    return result["reason"]


# ---------- CLI ----------

def _parse_argv(argv: list[str]) -> dict | None:
    opts = {"json": "--json" in argv, "force": "--force" in argv,
            "max_wait": 0 if "--no-wait" in argv else DEFAULT_MAX_WAIT}
    if "--max-wait" in argv:
        i = argv.index("--max-wait")
        try:
            opts["max_wait"] = int(argv[i + 1])
        except (IndexError, ValueError):
            return None
    return opts


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows 主控台是 cp950
        except (AttributeError, ValueError):
            pass

    opts = _parse_argv(sys.argv[1:])
    if opts is None:
        print("[X] --max-wait 後面要接秒數，例如 --max-wait 300", file=sys.stderr)
        return EXIT_ARGS

    notify = None if opts["json"] else (lambda msg: print(f"[WAIT] {msg}...", flush=True))
    result = refresh(force=opts["force"], max_wait_seconds=opts["max_wait"], notify=notify)

    probe = result["probe"]
    if probe is not None and not probe["ok"]:
        _out(opts["json"], reason=probe["reason"], detail=probe["detail"])
        return probe["exit_code"]

    if opts["json"]:
        print(json.dumps(_json_payload(result), ensure_ascii=False))
    else:
        _print_human(result)
    return result["exit_code"]


def _json_payload(result: dict) -> dict:
    quota = result["quota"]
    payload = {
        "ok": result["ok"],
        "action": result["action"],          # refreshed / skipped / waiting
        "reason": result["reason"],
        "waited_seconds": result["waited_seconds"],
        "usage": ({"windows": quota["windows"], "window_open": quota["window_open"]}
                  if quota["ok"] else {"error": quota["reason"]}),
        "probe": None,
    }
    if result["probe"] is not None:
        p = result["probe"]
        payload["probe"] = {"model": p["model"], "reply": p["reply"],
                            "cost_usd": p["cost_usd"], "seconds": p["seconds"],
                            "binary": p["binary"]}
    return payload


def _print_human(result: dict) -> None:
    action = result["action"]
    if action == "refreshed":
        p = result["probe"]
        print("[OK] 已刷新，Claude 可以用")
        print(f"     {usage_mod.pad('原因')} : {result['reason']}")
        if result["waited_seconds"]:
            print(f"     {usage_mod.pad('原地等了')} : "
                  f"{usage_mod.fmt_left(result['waited_seconds'])}")
        print(f"     {usage_mod.pad('模型')} : {p['model']}")
        print(f"     {usage_mod.pad('回覆')} : {p['reply'][:120]}")
        print(f"     {usage_mod.pad('花費')} : ${p['cost_usd']:.5f}")
        print(f"     {usage_mod.pad('耗時')} : {p['seconds']:.1f}s")
        print(f"     {usage_mod.pad('執行檔')} : {p['binary']}")
        _print_windows(result["quota"], full=True)
    elif action == "skipped":
        print("[SKIP] 已經刷新過了，這次不打、不花錢")
        print(f"     {usage_mod.pad('原因')} : {result['reason']}")
        _print_windows(result["quota"], full=False)
    else:  # waiting
        print("[WAIT] 還沒到重置時間，這次不打")
        print(f"     {usage_mod.pad('原因')} : {result['reason']}")
        _print_windows(result["quota"], full=False)


def _print_windows(quota: dict, *, full: bool) -> None:
    """有刷新就把用量整組印出來；沒刷新的話使用者只需要知道下次幾點重置。"""
    if not quota["ok"]:
        print(f"     {usage_mod.pad('用量')} : (查不到：{quota['reason']})")
        return
    if not full:
        five = quota["windows"].get("five_hour")
        when = (five or {}).get("resets_local") or "尚未開始計時"
        left = usage_mod.fmt_left((five or {}).get("seconds_left"))
        print(f"     {usage_mod.pad('下次重置')} : {when}｜剩 {left}")
        return
    for key, label in usage_mod.WINDOWS:
        w = quota["windows"].get(key)
        if w is None:
            continue
        pct = "—" if w["percent"] is None else f"{w['percent']:.0f}%"
        when = w["resets_local"] or "尚未開始計時"
        print(f"     {usage_mod.pad(label)} : 已用 {pct:>4}｜重置 {when}"
              f"｜剩 {usage_mod.fmt_left(w['seconds_left'])}")


def _out(as_json: bool, *, reason: str, detail: str) -> None:
    if as_json:
        print(json.dumps({"ok": False, "action": "refreshed", "reason": reason,
                          "detail": detail[:400]}, ensure_ascii=False))
    else:
        print(f"[X] {reason}")
        print(f"    {detail.strip()[:400]}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
