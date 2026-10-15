# -*- coding: utf-8 -*-
"""launch.py — 把一句話送進 Claude Code 的唯一實作（GUI 的 Task.fire 與 CLI 共用）。

    claude --resume <session-id> --print "<要說的話>"         接續舊對話
    claude --resume <session-id> --fork-session --print …     接續但開成新的對話 id（原本那個可以留著開；未實測）
    claude --session-id <新的 uuid> --print "<任務>"          在專案開一個全新的對話（id 先決定好，之後才找得到它）

兩個關鍵條件，錯一個就等於白送（見 tasks.py 開頭）：cwd 一定要是那個對話自己的專案資料夾；送出那一刻
那個對話不能正被別的 Claude Code 開著。送出的是獨立行程（DETACHED_PROCESS）：送完這支程式就可以結束，
被接續的對話會在背景自己跑完，輸出寫進 log。

測試或特殊環境可以用環境變數 CLAUDE_TIMER_CLAUDE_BIN 指定要跑哪支 claude（.py 檔會用目前的 Python 執行）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

CONFIRM_SECONDS = 60
NO_OVERRIDE = "(不覆寫)"


def claude_prefix() -> List[str]:
    override = os.environ.get("CLAUDE_TIMER_CLAUDE_BIN")
    if override:
        return [sys.executable, override] if override.lower().endswith(".py") else [override]
    from claude_subscription import find_claude_binary
    return [find_claude_binary()]


def build_argv(prompt: str, *, resume: Optional[str] = None, session_id: Optional[str] = None,
               permission_mode: Optional[str] = None, allowed_tools: Optional[str] = None,
               disallowed_tools: Optional[str] = None, max_budget_usd: Optional[float] = None,
               output_json: bool = False, fork_session: bool = False, model: Optional[str] = None,
               effort: Optional[str] = None) -> List[str]:
    """permission_mode 是**已經決定好**的值（inherit.resolve 之後）：「(…)」開頭或空字串＝不加。"""
    argv = claude_prefix()
    if resume:
        argv += ["--resume", resume]
        if fork_session:
            # 續在一個新的對話 id 底下（claude 2.1.293 的 --fork-session）：原本那個對話可以留著開在 VSCode。
            # 代價：接續的那一段不會出現在原本的對話框裡；能不能繞過「session 被佔用」的鎖還沒實測。
            argv.append("--fork-session")
    elif session_id:
        argv += ["--session-id", session_id]
    argv += ["--print", prompt]
    if permission_mode and not permission_mode.startswith("("):
        argv += ["--permission-mode", permission_mode]
    if model:
        argv += ["--model", model]       # 沿用原本：接手的那一段跟你在 VSCode 用的是同一個模型
    if effort:
        argv += ["--effort", effort]
    if allowed_tools:
        argv += ["--allowedTools", allowed_tools]
    if disallowed_tools:
        argv += ["--disallowedTools", disallowed_tools]
    if max_budget_usd:
        argv += ["--max-budget-usd", str(max_budget_usd)]
    if output_json:
        # log 最後就是一個 JSON（回覆、花費、session id）：CLI 的 job <id> 靠它判斷做完了沒
        argv += ["--output-format", "json"]
    return argv


def read_outcome(log_path: Path) -> Optional[dict]:
    """`--output-format json` 的結果（log 最後那個 JSON 物件），整理成 {is_error, subtype, turns, cost, denials}；
    還沒有結果（還在跑、或死掉沒寫）就 None。

    為什麼要看它：exit 0 不等於做了事。2026-10-15 那一晚接手的對話被權限擋光、55 秒就結束、exit 0，被記成成功。
    `permission_denials` 是 Claude Code 自己列的「這一段哪些工具呼叫被擋了」。"""
    import json
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("type") == "result":
            denials = []
            for d in data.get("permission_denials") or []:
                if isinstance(d, dict):
                    args = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
                    what = next((str(args[k]) for k in ("command", "file_path", "path") if args.get(k)), "")
                    label = f"{d.get('tool_name', '?')}({what[:120]})" if what else str(d.get("tool_name", "?"))
                    if label not in denials:
                        denials.append(label)
            return {"is_error": bool(data.get("is_error")), "subtype": data.get("subtype"),
                    "turns": data.get("num_turns"), "cost": data.get("total_cost_usd"), "denials": denials,
                    "result": str(data.get("result") or "")}
    return None


def outcome_line(outcome: dict) -> str:
    """「完成：12 輪、$0.84」／「完成，但被權限擋了 3 次：Bash(git fetch)…」／「claude 回報錯誤：…」"""
    cost = f"、${outcome['cost']:.2f}" if isinstance(outcome.get("cost"), (int, float)) else ""
    head = f"{outcome.get('turns') or '?'} 輪{cost}"
    if outcome.get("is_error"):
        return f"claude 回報錯誤（{outcome.get('subtype')}，{head}）：{outcome.get('result', '')[:160]}"
    if outcome.get("denials"):
        shown = "、".join(outcome["denials"][:3]) + ("…" if len(outcome["denials"]) > 3 else "")
        return f"完成（{head}），但被權限擋了 {len(outcome['denials'])} 種：{shown}"
    return f"完成（{head}）"


def start_detached(argv: List[str], cwd: str, log_path: Path) -> subprocess.Popen:
    """開一個不屬於本程式的行程，輸出寫進 log_path。起不來丟 OSError。"""
    log = open(log_path, "w", encoding="utf-8")
    try:
        kwargs = dict(cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True)
        if sys.platform == "win32":
            # DETACHED_PROCESS 之下沒有主控台，stdin 若用繼承的會是無效 handle，所以 DEVNULL
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        return subprocess.Popen(argv, **kwargs)
    finally:
        # 子行程已經拿到自己的 handle，這邊留著只會擋住之後讀 log
        log.close()
