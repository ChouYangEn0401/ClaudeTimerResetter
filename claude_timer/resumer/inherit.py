# -*- coding: utf-8 -*-
"""inherit.py — 接續時「照原本的來」：權限模式、模型、effort 從那個對話自己的紀錄讀（v1.4）。

為什麼要有（2026-10-15 的事故）：GUI 預設「(不覆寫)」＝不加 `--permission-mode`。但 VSCode 裡的 auto 模式不是存在
設定檔的東西，是那個互動行程的狀態——`claude --resume --print` 起來的新行程拿到的是 headless 的預設（default）：
要權限的工具沒有人能按同意，白名單以外的指令、工作目錄以外的檔案一律被拒。那一晚接手的兩個對話都這樣停住，
而 exit 0 又被記成「已送出」。

對話紀錄（~/.claude/projects/<目錄>/<id>.jsonl）每一個 user 訊息都帶著 `permissionMode`，每一則 assistant
回覆帶著 `requestedModel` 與 `effort`，而且每一行都有 `entrypoint`。所以：

- 只看**互動的那幾筆**（claude-vscode、cli）：接管跑出來的那一段是 `sdk-cli`，它記的 `default` 不能拿來沿用，
  不然接一次就永遠退回 default。
- `bypassPermissions` **永遠不沿用**：沒人看著的背景執行不該是什麼都不問；改用 `auto`（分類器擋危險的），
  並在 log 寫一句。真的要 bypass，排程時明講。
- 讀不到（舊對話、紀錄裡沒有）就退回 acceptEdits，並寫明是退回的。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

INHERIT = "(沿用原本)"          # GUI 的選項；CLI／API 用 "inherit"
INHERIT_TOKENS = {INHERIT, "inherit"}
INTERACTIVE = {"claude-vscode", "cli"}
NEVER_INHERIT = {"bypassPermissions": "auto"}
FALLBACK_MODE = "acceptEdits"
KNOWN_MODES = {"default", "acceptEdits", "auto", "dontAsk", "plan", "bypassPermissions"}
MAX_SCAN_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Inherited:
    permission_mode: str
    model: str = ""
    effort: str = ""
    note: str = ""              # 給 log 與畫面看的一句話：從哪裡來、有沒有改


def from_transcript(jsonl_path: str | Path | None) -> Inherited:
    """讀那個對話最後一次**互動時**的權限模式、模型、effort。"""
    mode = model = effort = ""
    path = Path(jsonl_path) if jsonl_path else None
    try:
        if path is None or path.stat().st_size > MAX_SCAN_BYTES:
            raise OSError("no transcript")
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"entrypoint"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict) or rec.get("entrypoint") not in INTERACTIVE:
                    continue
                if rec.get("type") == "user" and rec.get("permissionMode") in KNOWN_MODES:
                    mode = rec["permissionMode"]
                if rec.get("type") == "assistant":
                    model = str(rec.get("requestedModel") or model)
                    effort = str(rec.get("effort") or effort)
    except OSError:
        return Inherited(FALLBACK_MODE, note=f"讀不到對話紀錄，權限退回 {FALLBACK_MODE}")
    if not mode:
        return Inherited(FALLBACK_MODE, model, effort, note=f"紀錄裡沒有互動時的權限模式，退回 {FALLBACK_MODE}")
    if mode in NEVER_INHERIT:
        safer = NEVER_INHERIT[mode]
        return Inherited(safer, model, effort,
                         note=f"原本是 {mode}；背景執行沒人看著，不沿用，改用 {safer}")
    return Inherited(mode, model, effort, note=f"沿用原本的權限模式 {mode}" + (f"、模型 {model}" if model else ""))


def resolve(choice: str | None, jsonl_path: str | Path | None) -> Inherited:
    """排程時選的權限 → 真的要帶的參數。選了「沿用原本」才讀紀錄；其他照選的（「(不覆寫)」＝空字串）。"""
    if choice in INHERIT_TOKENS:
        return from_transcript(jsonl_path)
    if not choice or choice.startswith("("):
        return Inherited("", note="不覆寫：headless 的預設模式，要權限的工具沒有人能按同意")
    return Inherited(choice, note=f"指定權限模式 {choice}")


def takeover_preamble(*, at: str, permission_mode: str) -> str:
    """強制接管之後送出的那句話前面加的說明：接手的那個要知道自己的處境，被擋住時才知道該停下來寫狀態。"""
    mode = permission_mode or "default（headless 的預設）"
    return (f"（ClaudeTimerResetter 在 {at} 接管了這個對話：原本開著它的互動行程已經被關掉，連同它開的子程序。"
            f"你現在是在背景非互動執行，權限模式是 {mode}，沒有人能回答問題或按核准。"
            "如果被權限擋住、或需要人決定，就停下來，把現在的狀態寫清楚：哪些做完而且 commit 了、"
            "哪些還在進行、有沒有留下沒停的程式、下一步是什麼。）\n\n")
