# -*- coding: utf-8 -*-
"""ping.py — 用最便宜的方式確認「現在 Claude 能不能用」。

這支的唯一目的是**測試 whl 通不通**，不是做事，所以每一項都選最省的：

  模型      claude-haiku-4-5-20251001（目前最便宜的 Claude 模型）
  工具      全關（tools=""）→ 純文字進出，agent 模式貴 15~45 倍
  thinking  不開（MAX_THINKING_TOKENS=0）
  權限      manual（工具全關，這裡只是明確表態不自動放行）
  session   不保存（persist=False）→ 不重送歷史
  提示      "hi"
  預算      單次上限 $0.02，超過直接中止

一次大約 $0.001~0.003、3~6 秒。

用法：
    test.bat              （或）  .venv\\Scripts\\python ping.py
    .venv\\Scripts\\python ping.py --json     機器可讀輸出

結束碼（沿用 claude_subscription 的契約，方便排程/腳本判斷）：
    0 可用 / 1 呼叫失敗（額度、逾時）/ 2 找不到 claude / 3 未登入 / 4 參數錯
"""
from __future__ import annotations

import json
import os
import sys
import time

# thinking 關掉：這支只是要一個 "hi"，不需要模型多想
os.environ.setdefault("MAX_THINKING_TOKENS", "0")

MODEL = "claude-haiku-4-5-20251001"   # 最便宜的一檔；換 "haiku" 則跟隨官方最新 haiku
BUDGET_USD = 0.02
PROMPT = "hi"

EXIT_OK, EXIT_FAIL, EXIT_NO_BIN, EXIT_AUTH, EXIT_ARGS = 0, 1, 2, 3, 4


def main() -> int:
    as_json = "--json" in sys.argv
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows 主控台是 cp950
        except (AttributeError, ValueError):
            pass

    try:
        from claude_subscription import (
            ClaudeAuthError, ClaudeError, ClaudeNotFoundError, ask, find_claude_binary,
        )
    except ImportError as e:
        print(f"[X] 套件沒裝好：{e}\n    pip install "
              "https://github.com/ChouYangEn0401/ClaudeLogin/releases/download/"
              "v0.2.0/claude_subscription-0.2.0-py3-none-any.whl", file=sys.stderr)
        return EXIT_ARGS

    t0 = time.time()
    try:
        binary = find_claude_binary()
        r = ask(
            PROMPT,
            model=MODEL,
            tools="",                     # 純文字進出＝最便宜
            permission_mode="manual",
            persist=False,                # 不留 session、不重送歷史
            max_budget_usd=BUDGET_USD,
            timeout=90,
        )
    except ClaudeNotFoundError as e:
        _out(as_json, ok=False, reason="找不到 claude 執行檔（請安裝官方 Claude Code）", detail=str(e))
        return EXIT_NO_BIN
    except ClaudeAuthError as e:
        _out(as_json, ok=False, reason="未登入或認證失敗（請執行 claude 登入）", detail=str(e))
        return EXIT_AUTH
    except ClaudeError as e:
        _out(as_json, ok=False, reason="呼叫失敗（額度用完 / 逾時 / 模型錯誤）", detail=str(e))
        return EXIT_FAIL

    elapsed = time.time() - t0
    if as_json:
        print(json.dumps({
            "ok": True, "model": MODEL, "reply": (r.text or "").strip(),
            "cost_usd": r.cost_usd, "seconds": round(elapsed, 2), "binary": binary,
        }, ensure_ascii=False))
    else:
        print("[OK] Claude 可以用")
        print(f"     模型     : {MODEL}")
        print(f"     回覆     : {(r.text or '').strip()[:120]}")
        print(f"     花費     : ${r.cost_usd:.5f}")
        print(f"     耗時     : {elapsed:.1f}s")
        print(f"     執行檔   : {binary}")
    return EXIT_OK


def _out(as_json: bool, *, ok: bool, reason: str, detail: str) -> None:
    if as_json:
        print(json.dumps({"ok": ok, "reason": reason, "detail": detail[:400]}, ensure_ascii=False))
    else:
        print(f"[X] {reason}")
        print(f"    {detail.strip()[:400]}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
