# -*- coding: utf-8 -*-
"""Python API（claude_timer/api.py）：每個函式跟 CLI 走同一條路，結果一樣、不丟例外。

    .venv\\Scripts\\python -m unittest discover -s tests -v

沿用 test_cli 的假環境：假的 claude、暫存的 LOCALAPPDATA 與 ~/.claude、不碰工作排程器、「釋放」不真的殺行程。
"""
from __future__ import annotations

import unittest

import test_cli  # 只借它的 setUp／tearDown；不要 import 類別本身，不然它的測試會在這裡再跑一次


class ApiTest(unittest.TestCase):
    setUp = test_cli.CliTest.setUp
    tearDown = test_cli.CliTest.tearDown

    def test_every_call_mirrors_the_cli(self):
        from claude_timer import api

        r = api.sessions(cwd=str(self.repo))
        self.assertTrue(r.ok)
        self.assertEqual([s["session_id"] for s in r.data["sessions"]], [self.sid])
        r = api.show(self.sid, turns=1)
        self.assertTrue(r.ok)
        self.assertEqual(r.data["last_turns"][0]["prompt"], "幫我做登入頁")
        self.assertTrue(api.check(self.sid).ok)

        self.holder[self.sid] = {"pid": 1, "entrypoint": "claude-vscode", "cwd": str(self.repo)}
        r = api.check(self.sid)
        self.assertTrue(r.waiting and r.data["busy"])
        r = api.release(self.sid)
        self.assertTrue(r.ok and r.data["released"])
        self.assertEqual(self.released, [self.sid])

        r = api.resume(self.sid, at="+60", take_over=True, dry_run=True)
        self.assertTrue(r.ok, r.data)
        self.assertEqual((r.data["status"], r.data["busy_policy"]), ("scheduled", "takeover"))
        job_id = r.data["id"]
        self.holder[self.sid] = {"pid": 1, "entrypoint": "claude-vscode", "cwd": str(self.repo)}
        r = api.fire(job_id)
        self.assertEqual((r.code, r.data["status"]), (0, "done"))
        self.assertEqual(api.job(job_id).data["result"], "ok: continue")
        self.assertTrue(api.stash(job_id).ok)

        r = api.new(str(self.repo), "加一個關於頁")
        self.assertEqual((r.code, r.data["status"]), (0, "done"))
        self.assertTrue(r.data["branch"].startswith("resume/"))
        self.assertGreaterEqual(len(api.jobs().data["jobs"]), 2)

        r = api.resume(self.sid, at="明天")
        self.assertEqual(r.code, api.EXIT_BAD)
        self.assertIn("看不懂時間", r.error)
        r = api.resume(self.sid, take_over=True, fork=True)
        self.assertEqual(r.code, api.EXIT_BAD)
        self.assertEqual(api.show("no-such-session").code, api.EXIT_NOT_FOUND)
        self.assertEqual(api.cancel().code, api.EXIT_BAD)       # 沒給 id 也沒給 all
        self.assertTrue(api.cancel(all_jobs=True).ok)


if __name__ == "__main__":
    unittest.main()
