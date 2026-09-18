# -*- coding: utf-8 -*-
"""tasks.py — 一筆「幾點把這個對話接著送出去」的任務，以及送出前的零成本檢查。

送出＝開一個獨立於本程式的行程：

    claude --resume <session-id> --print "<你要接著說的話>"

兩個關鍵條件，錯一個就等於白送：

    cwd    一定要在那個對話自己的專案資料夾底下跑。Claude Code 的對話是按專案目錄
           存的（~/.claude/projects/<專案>/<id>.jsonl），在別的目錄 --resume 同一個
           id 會直接「找不到這個對話」。
    沒被佔用 同一個 session 同一時間只能有一個持有者。對話還開在 VSCode 裡的話，
           --resume 會回 "Session ID ... is already in use" 然後秒退。

所以送出前一定會先跑 preflight()（靜態檢查）跟 session_holder()（動態檢查），
兩個都不花 token。
"""
from __future__ import annotations

import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

from ..paths import ensure_install_dir, resume_task_log
from . import journal
from .sessions import SessionInfo, describe_holder, session_holder

PERMISSION_MODES = ["(不覆寫)", "acceptEdits", "bypassPermissions", "dontAsk", "plan"]

# 每個權限模式的說明（GUI 即時顯示）。resumer 是 --print 非互動執行，沒有人能回應
# 中途的權限提示，所以這個下拉在這裡比想像中重要。
PERMISSION_HELP = {
    "(不覆寫)": (
        "沿用那個對話 / 專案原本的設定，不加任何參數，最保守。\n"
        "注意：接續是非互動執行，若中途需要權限確認，沒有人能按「同意」，"
        "它可能就停在那裡。會改到檔案的接續建議改選 acceptEdits。"
    ),
    "acceptEdits": (
        "自動同意「檔案編輯」類的權限，不會停下來問。\n"
        "最適合日常用法：讓接續的對話能一路把改檔的工作做完。"
    ),
    "bypassPermissions": (
        "略過所有權限檢查，什麼都不問直接做（包含執行指令）。\n"
        "最不中斷、也最不設防。只有你完全清楚它接下來要做什麼時才用。"
    ),
    "dontAsk": (
        "不主動詢問權限（行為接近沿用既有設定但不彈提示）。\n"
        "不確定的話用 acceptEdits 比較好懂。"
    ),
    "plan": (
        "計畫模式：只讓它規劃、不實際動手改東西。\n"
        "適合你只想讓它接著想、還不要它真的執行的情況。"
    ),
}

# 送出後盯著子行程這麼久；這段時間內就死掉的視為失敗（錯誤才有機會被看到）。
CONFIRM_SECONDS = 60

STATUS_WAITING = "等待中"
STATUS_MANUAL = "時間到，等你確認"
STATUS_LOCKED = "被佔用，關掉後自動補送"
STATUS_SENDING = "送出中，確認中"
STATUS_SENT = "已送出"
STATUS_FAILED = "失敗"


def claude_binary() -> str:
    from claude_subscription import find_claude_binary
    return find_claude_binary()


def preflight(session: SessionInfo) -> list[str]:
    """靜態本機檢查：擋住送出的理由清單（空的＝這些項目沒問題）。

    這裡只查「不會變」的東西（執行檔、對話檔、專案資料夾）。「有沒有被佔用」會隨你
    開關 VSCode 而變，另外用 session_holder() 動態查，不放在這裡。
    """
    problems: list[str] = []
    try:
        claude_binary()
    except ImportError as e:
        problems.append(f"找不到 claude_subscription 套件（先跑 scripts\\setup.bat）：{e}")
    except Exception as e:  # ClaudeNotFoundError 等；這裡不該讓 GUI 整個掛掉
        problems.append(f"找不到 claude 執行檔：{e}")

    if not session.jsonl or not Path(session.jsonl).exists():
        problems.append(f"對話紀錄檔不見了：{session.jsonl or '(沒有記錄路徑)'}")
    if not session.cwd:
        problems.append("這個對話沒有記錄專案路徑（cwd），--resume 找不到它")
    elif not Path(session.cwd).is_dir():
        problems.append(f"專案資料夾不存在：{session.cwd}")
    return problems


class Task:
    def __init__(self, session: SessionInfo, prompt: str, when: datetime, mode: str,
                 permission_mode: str) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.session = session
        self.prompt = prompt
        self.when = when
        self.mode = mode                      # "auto" | "manual"
        self.permission_mode = permission_mode  # "(不覆寫)" 表示不加這個參數
        self.status = STATUS_WAITING
        self.error: str | None = None
        self.proc: subprocess.Popen | None = None
        self.fired_at: float | None = None
        self.log_path = resume_task_log(self.id)

    # -- 給畫面用 --

    def row(self) -> tuple[str, ...]:
        """Treeview 的一列：時間 / 專案 / 要送出的內容 / 方式 / 狀態。"""
        mode = "自動" if self.mode == "auto" else "手動確認"
        return (
            self.when.strftime("%m-%d %H:%M:%S"),
            self.session.project,
            " ".join(self.prompt.split())[:40],
            mode,
            self.status if not self.error else f"{self.status}",
        )

    @property
    def done(self) -> bool:
        return self.status == STATUS_SENT

    @property
    def settled(self) -> bool:
        """已經有最終結果、不再需要 tick 幫它做事（送出成功或硬失敗）。"""
        return self.status in (STATUS_SENT, STATUS_FAILED)

    # -- 送出 --

    def fire(self) -> bool:
        """真的把 prompt 送出去。回傳有沒有成功「啟動」，錯誤寫進 self.error。

        被佔用時不標成硬失敗，而是標 STATUS_LOCKED——自動模式的 tick 會在你關掉
        VSCode 之後自己補送，不用重排。
        """
        problems = preflight(self.session)
        if problems:
            self._fail("；".join(problems))
            return False

        holder = session_holder(self.session.session_id)
        if holder:
            self.status = STATUS_LOCKED
            self.error = f"{describe_holder(holder)}，請關閉後它會自動補送"
            return False

        try:
            exe = claude_binary()
        except Exception as e:
            self._fail(f"取得 claude 執行檔失敗：{e}")
            return False

        args = [exe, "--resume", self.session.session_id, "--print", self.prompt]
        if self.permission_mode and self.permission_mode != PERMISSION_MODES[0]:
            args += ["--permission-mode", self.permission_mode]

        ensure_install_dir()
        try:
            log = open(self.log_path, "w", encoding="utf-8")
        except OSError as e:
            self._fail(f"開不了記錄檔 {self.log_path}：{e}")
            return False
        try:
            self.proc = subprocess.Popen(
                args,
                cwd=self.session.cwd,          # 一定要在該對話自己的專案資料夾裡跑
                # DETACHED_PROCESS 之下沒有主控台，stdin 若用繼承的會是無效 handle。
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
        except OSError as e:
            log.close()
            self._fail(f"啟動 claude 失敗：{e}")
            return False
        finally:
            # 子行程已經拿到自己的 handle，這邊留著只會擋住之後讀 log。
            if not log.closed:
                log.close()

        self.status = STATUS_SENDING
        self.error = None
        self.fired_at = time.monotonic()
        journal.write(f"FIRE task={self.id} session={self.session.session_id} "
                      f"cwd={self.session.cwd} prompt={self.prompt[:60]!r}")
        return True

    def poll(self) -> bool:
        """盯著已送出的行程。回傳狀態有沒有變。"""
        if self.status != STATUS_SENDING or self.proc is None:
            return False
        rc = self.proc.poll()
        elapsed = time.monotonic() - (self.fired_at or 0)
        if rc is None:
            if elapsed >= CONFIRM_SECONDS:
                # 撐過確認期＝沒有立刻炸掉，剩下的交給那個背景行程自己跑完。
                self.status = STATUS_SENT
                journal.write(f"SENT task={self.id}（背景執行中）")
                return True
            return False
        if rc == 0:
            self.status = STATUS_SENT
            journal.write(f"SENT task={self.id}（已完成，exit 0）")
            return True
        self._fail(f"claude 以 exit {rc} 結束：{self._log_tail()}")
        return True

    def _log_tail(self, limit: int = 300) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as e:
            return f"(讀不到 {self.log_path}：{e})"
        if not text:
            return "(沒有輸出；最常見的原因是對話正開在 VSCode 裡，session 被佔用)"
        return " ".join(text.split())[-limit:]

    def _fail(self, message: str) -> None:
        self.status = STATUS_FAILED
        self.error = message
        journal.write(f"FAIL task={self.id} {message}")
