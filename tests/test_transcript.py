# -*- coding: utf-8 -*-
"""對話檔的結構化（claude_timer/resumer/transcript.py）：分輪、中間過程、摘要，以及 CLI 的 show --turns。

    .venv\\Scripts\\python -m unittest discover -s tests -v

純讀一個暫存的 .jsonl，不碰 Claude、不碰你的 ~/.claude。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_timer.resumer import transcript as tx  # noqa: E402


def _user(text, ts, **extra):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}, **extra}


def _tool_result(ts):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}}


def _assistant(blocks, ts):
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": blocks}}


def _text(s):
    return {"type": "text", "text": s}


def _tool(name):
    return {"type": "tool_use", "name": name, "id": "t", "input": {}}


RECORDS = [
    {"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-10-08T01:00:00Z", "sessionId": "s"},
    _user("幫我修登入頁的錯字", "2026-10-08T01:00:01Z", cwd="C:/proj", gitBranch="feat/login"),
    _assistant([{"type": "thinking", "thinking": "先看檔案"}, _tool("Read")], "2026-10-08T01:00:02Z"),
    _tool_result("2026-10-08T01:00:03Z"),
    _assistant([_text("找到了，改一下"), _tool("Edit"), _tool("Edit")], "2026-10-08T01:00:04Z"),
    _tool_result("2026-10-08T01:00:05Z"),
    _assistant([_text("改好了：\n- 登入頁兩個錯字修掉\n- 測試通過")], "2026-10-08T01:00:06Z"),
    _user("<system-reminder>不是人打的</system-reminder>", "2026-10-08T01:00:07Z", isMeta=True),
    _user("那順便看一下註冊頁", "2026-10-08T01:30:00Z"),
    _assistant([_tool("Grep")], "2026-10-08T01:30:01Z"),
    _tool_result("2026-10-08T01:30:02Z"),
]


class TranscriptTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ctr_tx_"))
        self.path = self.tmp / "s.jsonl"
        self.path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in RECORDS), encoding="utf-8")

    def test_turns_split_on_human_prompts(self):
        t = tx.read(self.path)
        self.assertTrue(t.ok, t.reason)
        self.assertEqual(len(t.turns), 2)
        first, second = t.turns
        self.assertEqual(first.prompt.text, "幫我修登入頁的錯字")
        # 最後一則回覆才是 reply；前面那句「找到了，改一下」跟工具、思考都是中間過程
        self.assertEqual(first.reply.text, "改好了：\n- 登入頁兩個錯字修掉\n- 測試通過")
        kinds = [e.kind for e in first.middle]
        self.assertEqual(kinds, [tx.KIND_THINKING, tx.KIND_TOOLS, tx.KIND_ASSISTANT, tx.KIND_TOOLS])
        self.assertEqual(first.tool_counter(), {"Read": 1, "Edit": 2})
        self.assertIn("Edit ×2", first.middle_label())
        self.assertIn("中途回覆 1 則", first.middle_label())
        self.assertIn("思考 1 則", first.middle_label())
        # 第二輪：只有工具動作、還沒有回覆（＝卡住的地方）
        self.assertEqual(second.prompt.text, "那順便看一下註冊頁")
        self.assertIsNone(second.reply)
        self.assertEqual([e.kind for e in second.middle], [tx.KIND_TOOLS])
        self.assertEqual(second.title, "那順便看一下註冊頁")

    def test_digest_counts_turns_and_last_words(self):
        d = tx.read(self.path).digest
        self.assertEqual((d.turns, d.human, d.assistant, d.tool_calls), (2, 2, 2, 4))
        self.assertEqual(d.last_human, "那順便看一下註冊頁")
        self.assertIn("改好了", d.last_assistant)
        self.assertIn("2 輪", d.volume_label())

    def test_reply_before_tools_is_only_a_midway_reply(self):
        entries = [
            tx.Entry(0, tx.KIND_HUMAN, "a"),
            tx.Entry(1, tx.KIND_ASSISTANT, "先回一句"),
            tx.Entry(2, tx.KIND_TOOLS, "⚙ Bash"),
        ]
        turns = tx.build_turns(entries)
        self.assertEqual(len(turns), 1)
        self.assertIsNone(turns[0].reply)
        self.assertEqual([e.text for e in turns[0].middle], ["先回一句", "⚙ Bash"])

    def test_compact_summary_before_first_prompt_gets_its_own_turn(self):
        entries = [
            tx.Entry(0, tx.KIND_SYSTEM, "【壓縮摘要】\n之前做了很多"),
            tx.Entry(1, tx.KIND_HUMAN, "繼續"),
            tx.Entry(2, tx.KIND_ASSISTANT, "好"),
        ]
        turns = tx.build_turns(entries)
        self.assertEqual(len(turns), 2)
        self.assertIsNone(turns[0].prompt)
        self.assertIn("壓縮摘要", turns[0].title)
        self.assertEqual(turns[1].reply.text, "好")

    def test_cli_show_turns(self):
        home = self.tmp / "home"
        proj = home / ".claude" / "projects" / "C--proj"
        proj.mkdir(parents=True)
        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        (proj / f"{sid}.jsonl").write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        backup = dict(os.environ)
        os.environ.update({"USERPROFILE": str(home), "HOME": str(home), "LOCALAPPDATA": str(self.tmp / "appdata")})
        try:
            from claude_timer import cli
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(["show", sid, "--turns", "1", "--json"])
            self.assertEqual(code, 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["turns"], 2)
            self.assertEqual(len(data["last_turns"]), 1)
            last = data["last_turns"][0]
            self.assertEqual((last["n"], last["prompt"], last["reply"]), (2, "那順便看一下註冊頁", ""))
            self.assertIn("Grep", last["middle"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli.main(["show", sid, "--turns", "2"])
            self.assertIn("── 第 1 輪", out.getvalue())
            self.assertIn("這一輪還沒有回覆", out.getvalue())
        finally:
            os.environ.clear()
            os.environ.update(backup)


if __name__ == "__main__":
    unittest.main()
