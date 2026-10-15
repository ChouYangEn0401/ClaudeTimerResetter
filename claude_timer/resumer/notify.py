# -*- coding: utf-8 -*-
"""notify.py — 接管、做完的時候傳一則到 Telegram（經這台電腦上的 Notifier_TB；v1.5，2026-10-15）。

擁有者 2026-10-15：接管之後背景那一段沒有人看著，做完要講一聲。**這裡不連 Telegram**：往
Notifier_TB 的 socket 丟一個封包就結束（跟 LINEBotForChurch、ISDStockProject 同一個格式）::

    {"sig": <socket_secret>, "payload": <文字>}   →  TCP 127.0.0.1:9999

- 設定讀 Notifier_TB 自己的 config.json（host、port、密鑰只存在那一份）。找的順序：環境變數
  NOTIFIER_TB_HOME → 這個專案旁邊的 ../Notifier_TB → ~/Documents/Notifier_TB。
- 不想要：環境變數 CLAUDE_RESUMER_NOTIFY=0。找不到 Notifier、送不出去都不是錯誤，記一行 journal 就算了。
- Notifier 沒開著（它是要用才開、閒置自己關的）就幫它開（main.py --until-idle 180），最多等 WAKE_WAIT 秒。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from . import journal

DISABLE_ENV = "CLAUDE_RESUMER_NOTIFY"
HOME_ENV = "NOTIFIER_TB_HOME"
TIMEOUT = 5.0
WAKE_WAIT = 30.0
IDLE_SECONDS = 180


def _candidates() -> list[Path]:
    found = []
    if os.environ.get(HOME_ENV):
        found.append(Path(os.environ[HOME_ENV]).expanduser())
    # 專案資料夾（打包成 exe 時是 exe 所在的資料夾）旁邊那一個
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    found += [root.parent / "Notifier_TB", Path.home() / "Documents" / "Notifier_TB"]
    return found


def find() -> Optional[tuple[str, int, str, Path]]:
    """(host, port, secret, 資料夾)；找不到或被關掉回 None。"""
    if os.environ.get(DISABLE_ENV, "").strip() in ("0", "off", "false", "no"):
        return None
    for folder in _candidates():
        config = folder / "config.json"
        if not config.is_file():
            continue
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        secret = str(data.get("socket_secret") or "").strip() if isinstance(data, dict) else ""
        if secret:
            return (str(data.get("socket_host") or "127.0.0.1"), int(data.get("socket_port") or 9999), secret, folder)
    return None


def _post(host: str, port: int, secret: str, text: str) -> bool:
    packet = json.dumps({"sig": secret, "payload": text}, ensure_ascii=False).encode("utf-8")
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT) as conn:
            conn.sendall(packet)
    except OSError:
        return False
    return True


def _wake(folder: Path) -> bool:
    python = folder / ".venv" / "Scripts" / "python.exe"
    if not (python.is_file() and (folder / "main.py").is_file()):
        return False
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        subprocess.Popen(["cmd.exe", "/c", "start", "Notifier_TB (on demand)", "/min", str(python), "main.py",
                          "--until-idle", str(IDLE_SECONDS)], cwd=folder, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return False
    return True


def send(text: str, *, wait: float = WAKE_WAIT) -> bool:
    """傳一則。永遠不丟例外；回傳有沒有送出去。"""
    try:
        target = find()
        if target is None:
            journal.write("NOTIFY 沒有接上 Notifier_TB，這則沒傳：" + text.splitlines()[0][:80])
            return False
        host, port, secret, folder = target
        if _post(host, port, secret, text):
            return True
        if wait <= 0 or not _wake(folder):
            journal.write("NOTIFY Notifier_TB 沒開著、也開不起來，這則沒傳：" + text.splitlines()[0][:80])
            return False
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            time.sleep(1.0)
            if _post(host, port, secret, text):
                return True
        journal.write(f"NOTIFY 開了 Notifier_TB 但 {int(wait)} 秒內沒起來，這則沒傳")
        return False
    except Exception as e:  # noqa: BLE001 - 通知失敗不能害接續失敗
        journal.write(f"NOTIFY 失敗：{e}")
        return False


def finished_text(project: str, outcome: str, *, took_over: bool, snapshot_ref: str = "",
                  failed: bool = False) -> str:
    head = "❌ ClaudeResumer：接續失敗" if failed else "✅ ClaudeResumer：接續做完了"
    lines = [f"{head}（{project}）", outcome]
    if took_over:
        lines.append("（這一次是接管：原本開著那個對話的視窗被關掉了）")
    if snapshot_ref:
        lines.append(f"接手前的快照：{snapshot_ref}（git diff {snapshot_ref} 看接手後改了什麼）")
    return "\n".join(line for line in lines if line)
