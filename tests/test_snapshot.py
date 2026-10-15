# -*- coding: utf-8 -*-
"""v1.5：接手前存快照（snapshot.py）、GUI 的任務做完傳 Telegram（notify.py）。

    .venv\\Scripts\\python -m unittest discover -s tests -v

擁有者 2026-10-15：「雖然我不喜歡為了這樣把 git 備份用髒，但是確實比資料都損失好一點」、做完要講一聲。
不碰真的 claude、不殺任何行程、不傳到真的 Telegram（Notifier 換成一個記下內容的假函式）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["CLAUDE_RESUMER_NOTIFY"] = "0"

from claude_timer.resumer import notify, snapshot, tasks  # noqa: E402
from claude_timer.resumer.jobs import BUSY_TAKEOVER  # noqa: E402
from claude_timer.resumer.sessions import SessionInfo  # noqa: E402

SID = "abcdef12-0000-0000-0000-000000000000"


def git(cwd, *args) -> str:
    out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=True).stdout
    return out.decode("utf-8").strip()


class SnapshotTest(unittest.TestCase):

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="ctr_snap_"))
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "init")

    def test_clean_and_non_repo_take_nothing(self):
        self.assertFalse(snapshot.take(str(self.repo), SID).taken)
        self.assertIn("乾淨", snapshot.take(str(self.repo), SID).note)
        plain = Path(tempfile.mkdtemp(prefix="ctr_plain_"))
        self.assertIn("不是 git", snapshot.take(str(plain), SID).note)
        self.assertEqual(snapshot.listing(str(self.repo)), [])

    def test_unchanged_retries_reuse_the_same_snapshot(self):
        (self.repo / "a.txt").write_text("two\n", encoding="utf-8")
        first = snapshot.take(str(self.repo), SID)
        time.sleep(1.1)                                   # 名字帶秒：真的會變成另一份的話這裡看得出來
        again = snapshot.take(str(self.repo), SID)
        self.assertEqual(again.ref, first.ref)
        self.assertIn("沿用", again.note)
        (self.repo / "a.txt").write_text("three\n", encoding="utf-8")
        time.sleep(1.1)
        changed = snapshot.take(str(self.repo), SID)
        self.assertNotEqual(changed.ref, first.ref)
        self.assertEqual(len(snapshot.listing(str(self.repo))), 2)

    def test_the_real_index_is_untouched(self):
        (self.repo / "a.txt").write_text("staged\n", encoding="utf-8")
        git(self.repo, "add", "a.txt")
        (self.repo / "a.txt").write_text("staged then edited\n", encoding="utf-8")
        before = git(self.repo, "status", "--porcelain")
        snap = snapshot.take(str(self.repo), SID)
        self.assertEqual(git(self.repo, "status", "--porcelain"), before)        # MM a.txt 照舊
        self.assertEqual(git(self.repo, "show", f"{snap.ref}:a.txt"), "staged then edited")
        self.assertEqual(git(self.repo, "stash", "list"), "")                    # 不是用 stash 做的


class TaskNoticeTest(unittest.TestCase):

    def test_a_finished_takeover_says_so_with_the_snapshot(self):
        sent = []
        original, appdata = notify.send, os.environ.get("LOCALAPPDATA")
        notify.send = lambda text, **kw: sent.append(text) or True
        os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ctr_appdata_")   # log、journal 不寫進你的資料夾
        try:
            repo = Path(tempfile.mkdtemp(prefix="ctr_task_"))
            session = SessionInfo(SID, str(repo / "s.jsonl"), str(repo), "t", "", 0.0, 0)
            from datetime import datetime
            task = tasks.Task(session, "continue", datetime.now(), "auto", "auto", BUSY_TAKEOVER)
            task.status, task.was_taken_over = tasks.STATUS_SENT, True
            task.snapshot_ref = "refs/resumer/snapshots/20261015-035100-abcdef12"
            task.proc = subprocess.Popen([sys.executable, "-c", "pass"])
            task.proc.wait()
            task.log_path.parent.mkdir(parents=True, exist_ok=True)
            task.log_path.write_text('{"type":"result","subtype":"success","is_error":false,"result":"ok",'
                                     '"num_turns":3,"total_cost_usd":0.1}\n', encoding="utf-8")
            self.assertTrue(task.check_outcome())
            deadline = time.time() + 5
            while not sent and time.time() < deadline:
                time.sleep(0.05)
        finally:
            notify.send = original
            if appdata is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = appdata
        self.assertEqual(len(sent), 1)
        self.assertIn("接續做完了", sent[0])
        self.assertIn("接管", sent[0])
        self.assertIn("20261015-035100-abcdef12", sent[0])

    def test_notify_can_be_switched_off(self):
        os.environ["CLAUDE_RESUMER_NOTIFY"] = "0"
        self.assertIsNone(notify.find())


if __name__ == "__main__":
    unittest.main()
