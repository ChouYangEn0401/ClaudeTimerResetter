# -*- coding: utf-8 -*-
"""notify.py — 接管、做完的時候傳一則到 Telegram（先經 ClawBot 的 Host，不行才經 Notifier_TB；v1.5.2）。

**v1.5.2（2026-11-02）先經 ClawBot**：擁有者「全部都改，按照現在的分流架構，決定應該要把訊息塞給誰」。
打 ClawBot 本機入口的 ``POST /v1/notify``（只送給擁有者；ClawBot 照它的分流表挑 bot）：做完是 task.done
（主機 bot、不響），失敗是 task.problem（主機 bot、會響）。網址與擁有者的 token 每次讀 ClawBot 的
``data/gateway.json``（不抄進這裡；名字叫 owner 的先試，``GET /v1/me`` 確認是擁有者）。找的順序：環境變數
CLAWBOT_HOME → 這個專案旁邊的 ../MyFirstTelegramClawBot → ~/Documents/MyFirstTelegramClawBot。
127.0.0.1 不走 proxy、逾時 5 秒。ClawBot 找不到、連不上、說不行，才走下面這條舊的路；journal 記一行走了哪一條。

以下是 v1.5 的舊路（現在是退回用的）：

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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from . import journal

DISABLE_ENV = "CLAUDE_RESUMER_NOTIFY"
HOME_ENV = "NOTIFIER_TB_HOME"
TIMEOUT = 5.0
WAKE_WAIT = 30.0
IDLE_SECONDS = 180
CLAWBOT_HOME_ENV = "CLAWBOT_HOME"
CLAWBOT_TEXT_LIMIT = 4000
SOURCE = "ClaudeResumer"


def _root() -> Path:
    # 專案資料夾（打包成 exe 時是 exe 所在的資料夾）
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]


def _disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip() in ("0", "off", "false", "no")


class ClawBotLink:
    """ClawBot 的本機 HTTP 入口（契約在 MyFirstTelegramClawBot 的 docs/contracts.md §2.1）。"""

    def __init__(self, url: str, token: str, timeout: float = TIMEOUT):
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 127.0.0.1 不走 proxy

    def _call(self, path: str, body: Optional[dict] = None) -> Optional[dict]:
        """ClawBot 的回覆；連不上回 None；它收到了但說不行回 {"error": …}。"""
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, method="GET" if body is None else "POST",
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json"})
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                got = json.loads(resp.read().decode("utf-8") or "{}")
                return got if isinstance(got, dict) else {}
        except urllib.error.HTTPError as e:
            return {"error": f"http_{e.code}"}
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def is_owner(self) -> Optional[bool]:
        """None＝連不上（不用再試別的 token）。"""
        got = self._call("/v1/me")
        return None if got is None else bool(got.get("owner"))

    def notify(self, text: str, *, stream: str = "task", kind: str = "") -> Optional[dict]:
        if len(text) > CLAWBOT_TEXT_LIMIT:
            text = text[:CLAWBOT_TEXT_LIMIT - 1] + "…"
        body = {"text": text, "stream": stream, "source": SOURCE}
        if kind:
            body["kind"] = kind
        return self._call("/v1/notify", body)


def _clawbot_candidates() -> list[Path]:
    found = []
    if os.environ.get(CLAWBOT_HOME_ENV):
        found.append(Path(os.environ[CLAWBOT_HOME_ENV]).expanduser())
    found += [_root().parent / "MyFirstTelegramClawBot", Path.home() / "Documents" / "MyFirstTelegramClawBot"]
    return found


def find_clawbot() -> Optional[ClawBotLink]:
    """ClawBot 的本機入口（擁有者的 token）；被關掉、找不到、Host 沒開回 None。"""
    if _disabled():
        return None
    for folder in _clawbot_candidates():
        try:
            store = json.loads((folder / "data" / "gateway.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(store, dict):
            continue
        tokens = store.get("tokens") if isinstance(store.get("tokens"), dict) else {}
        url = str(store.get("url") or "http://127.0.0.1:8765")
        for token in sorted(tokens, key=lambda t: 0 if (tokens[t] or {}).get("name") == "owner" else 1):
            link = ClawBotLink(url, str(token))
            mine = link.is_owner()
            if mine is None:
                return None
            if mine:
                return link
        return None
    return None


def _candidates() -> list[Path]:
    found = []
    if os.environ.get(HOME_ENV):
        found.append(Path(os.environ[HOME_ENV]).expanduser())
    found += [_root().parent / "Notifier_TB", Path.home() / "Documents" / "Notifier_TB"]
    return found


def find() -> Optional[tuple[str, int, str, Path]]:
    """(host, port, secret, 資料夾)；找不到或被關掉回 None。"""
    if _disabled():
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


def _via_clawbot(text: str, kind: str) -> bool:
    link = find_clawbot()
    if link is None:
        return False
    got = link.notify(text, stream="task", kind=kind)
    if got and got.get("ok"):
        journal.write(f"NOTIFY 經 ClawBot 送出（task.{kind} → {got.get('bot', '?')} bot"
                      + ("，先收著" if got.get("held") else "") + "）")
        return True
    journal.write("NOTIFY ClawBot 沒送成（" + ("連不上" if got is None else str(got.get("error") or got))
                  + "），改走 Notifier_TB")
    return False


def send(text: str, *, wait: float = WAKE_WAIT, failed: bool = False) -> bool:
    """傳一則：先經 ClawBot（failed＝task.problem、不然 task.done），不行才經 Notifier_TB。
    永遠不丟例外；回傳有沒有送出去。"""
    try:
        if _disabled():
            return False
        if _via_clawbot(text, "problem" if failed else "done"):
            return True
        target = find()
        if target is None:
            journal.write("NOTIFY 沒有接上 Notifier_TB，這則沒傳：" + text.splitlines()[0][:80])
            return False
        host, port, secret, folder = target
        if _post(host, port, secret, text):
            journal.write("NOTIFY 經 Notifier_TB 的 socket 送出")
            return True
        if wait <= 0 or not _wake(folder):
            journal.write("NOTIFY Notifier_TB 沒開著、也開不起來，這則沒傳：" + text.splitlines()[0][:80])
            return False
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            time.sleep(1.0)
            if _post(host, port, secret, text):
                journal.write("NOTIFY 經 Notifier_TB 的 socket 送出（幫它開了）")
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
