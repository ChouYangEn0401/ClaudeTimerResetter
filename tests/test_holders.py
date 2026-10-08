# -*- coding: utf-8 -*-
"""「這個對話現在有沒有人開著」（sessions.active_holders）：殘留的 <pid>.json 不能再被當成持有者。

    .venv\\Scripts\\python -m unittest discover -s tests -v

用這個測試自己的 python 行程當「活著的 pid」，在暫存的 ~/.claude/sessions/ 放假的檔；不碰你的。
"""
from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_timer.core.procs import process_created  # noqa: E402
from claude_timer.resumer import sessions  # noqa: E402


@unittest.skipUnless(platform.system() == "Windows", "行程建立時間的比對只在 Windows 做")
class HoldersTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ctr_hold_"))
        self.env_backup = dict(os.environ)
        home = self.tmp / "home"
        os.environ.update({"USERPROFILE": str(home), "HOME": str(home)})
        self.dir = home / ".claude" / "sessions"
        self.dir.mkdir(parents=True)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env_backup)

    def _write(self, fname: str, **rec) -> None:
        (self.dir / fname).write_text(json.dumps(rec), encoding="utf-8")

    def test_proc_start_must_match(self):
        me = os.getpid()
        created = process_created(me)
        self.assertTrue(created)
        now_ms = int(time.time() * 1000) - 25 * 60 * 1000
        # 同一個行程：算持有者，而且帶狀態
        self._write("a.json", pid=me, sessionId="live", procStart=str(created), entrypoint="claude-vscode",
                    name="x", status="idle", statusUpdatedAt=now_ms)
        # pid 活著但建立時間對不上：是被重用的 pid，殘留檔，不算
        self._write("b.json", pid=me, sessionId="stale", procStart=str(created - 12345), entrypoint="claude-vscode")
        # 行程早就不在了
        self._write("c.json", pid=4000000, sessionId="dead", procStart="1", entrypoint="claude-vscode")
        # 沒有 procStart 的舊版：退回看執行檔名；python 不叫 claude，所以不算
        self._write("d.json", pid=me, sessionId="old", entrypoint="claude-vscode")
        holders = sessions.active_holders()
        self.assertEqual(set(holders), {"live"})
        h = holders["live"]
        self.assertEqual((h["pid"], h["status"]), (me, "idle"))
        self.assertIn("閒置中", sessions.holder_state(h))
        self.assertIn("25 分鐘前", sessions.holder_state(h))
        self.assertIn("VSCode", sessions.describe_holder(h))
        self.assertIn("閒置中", sessions.describe_holder(h))
        self.assertIsNone(sessions.session_holder("stale"))


if __name__ == "__main__":
    unittest.main()
