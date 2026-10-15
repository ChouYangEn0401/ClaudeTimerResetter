# -*- coding: utf-8 -*-
"""v1.4：接手時照原本的權限接著做、只接管閒著的互動對話、看結果 JSON 判斷做了什麼。

    .venv\\Scripts\\python -m unittest discover -s tests -v

2026-10-15 03:51 的事故：到點強制接管把一個正在做事的 VSCode 對話砍斷，接手的 `claude --resume --print` 沒帶
`--permission-mode`（GUI 預設「(不覆寫)」），拿到 headless 的 default：白名單以外的指令、工作目錄以外的檔案全被拒，
55 秒後 exit 0，被記成「已送出」。這裡每一條都對著那一晚。不碰真的 claude、不殺任何真的行程。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_timer.resumer import inherit, launch, sessions  # noqa: E402


def transcript(tmp: Path, *records: dict) -> Path:
    p = tmp / "s.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    return p


def user(mode: str, entry: str = "claude-vscode") -> dict:
    return {"type": "user", "entrypoint": entry, "permissionMode": mode, "message": {"role": "user", "content": "x"}}


def assistant(model: str, effort: str, entry: str = "claude-vscode") -> dict:
    return {"type": "assistant", "entrypoint": entry, "requestedModel": model, "effort": effort,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "y"}]}}


class InheritTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ctr_inherit_"))

    def test_the_last_interactive_mode_model_and_effort_win(self):
        p = transcript(self.tmp, user("acceptEdits"), assistant("claude-sonnet-5", "high"),
                       user("auto"), assistant("claude-opus-5-5", "medium"),
                       # 上一次被接管跑出來的那一段（sdk-cli）記的是 default：不能拿來沿用
                       user("default", entry="sdk-cli"), assistant("claude-haiku-5-5", "low", entry="sdk-cli"))
        got = inherit.resolve(inherit.INHERIT, p)
        self.assertEqual((got.permission_mode, got.model, got.effort), ("auto", "claude-opus-5-5", "medium"))
        self.assertIn("沿用", got.note)
        self.assertEqual(inherit.resolve("inherit", p).permission_mode, "auto")

    def test_bypass_is_never_inherited(self):
        got = inherit.from_transcript(transcript(self.tmp, user("bypassPermissions")))
        self.assertEqual(got.permission_mode, "auto")
        self.assertIn("不沿用", got.note)

    def test_nothing_recorded_falls_back_and_says_so(self):
        self.assertEqual(inherit.from_transcript(transcript(self.tmp, {"type": "user"})).permission_mode, "acceptEdits")
        missing = inherit.from_transcript(self.tmp / "nope.jsonl")
        self.assertEqual(missing.permission_mode, "acceptEdits")
        self.assertIn("讀不到", missing.note)

    def test_explicit_choices_pass_through_and_no_override_adds_nothing(self):
        self.assertEqual(inherit.resolve("acceptEdits", None).permission_mode, "acceptEdits")
        self.assertEqual(inherit.resolve("(不覆寫)", None).permission_mode, "")

    def test_argv_carries_mode_model_effort_and_never_a_placeholder(self):
        argv = launch.build_argv("hi", resume="s1", permission_mode="auto", model="claude-opus-5-5", effort="medium")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "auto")
        self.assertEqual(argv[argv.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(argv[argv.index("--effort") + 1], "medium")
        for placeholder in ("", "(不覆寫)", inherit.INHERIT):
            self.assertNotIn("--permission-mode", launch.build_argv("hi", resume="s1", permission_mode=placeholder))

    def test_the_takeover_note_tells_the_new_process_where_it_stands(self):
        note = inherit.takeover_preamble(at="10-15 03:51", permission_mode="auto")
        self.assertIn("10-15 03:51", note)
        self.assertIn("權限模式是 auto", note)
        self.assertIn("停下來", note)


class ReleaseTest(unittest.TestCase):
    """真的跑 release_session 的判斷；只把「殺行程」換成記錄。"""

    def setUp(self):
        self.holder: dict[str, dict] = {}
        self.killed: list[int] = []
        self._orig = (sessions.active_holders, sessions._kill_tree)
        sessions.active_holders = lambda: dict(self.holder)

        def fake_kill(pid):
            self.killed.append(pid)
            self.holder.clear()
            return [(11, "bash.exe"), (12, "bash.exe"), (13, "python.exe")]
        sessions._kill_tree = fake_kill

    def tearDown(self):
        sessions.active_holders, sessions._kill_tree = self._orig

    def test_a_program_holding_the_session_is_never_killed(self):
        # PipelineAgent 的節點、另一筆 Resumer 工作：claude -p（sdk-cli）
        self.holder["s"] = {"pid": 7, "entrypoint": "sdk-cli", "status": "idle"}
        for allow_busy in (False, True):
            ok, message = sessions.release_session("s", timeout=0.1, allow_busy=allow_busy)
            self.assertFalse(ok)
            self.assertIn("程式", message)
        self.assertEqual(self.killed, [])

    def test_a_busy_conversation_is_not_cut_in_the_middle_by_a_schedule(self):
        self.holder["s"] = {"pid": 8, "entrypoint": "claude-vscode", "status": "busy", "updated_at": 0}
        ok, message = sessions.release_session("s", timeout=0.1)
        self.assertFalse(ok)
        self.assertIn("正在做事", message)
        self.assertEqual(self.killed, [])
        ok, message = sessions.release_session("s", timeout=0.5, allow_busy=True)   # 人自己按的
        self.assertTrue(ok, message)
        self.assertEqual(self.killed, [8])

    def test_an_idle_conversation_is_released_with_its_children_and_says_which(self):
        self.holder["s"] = {"pid": 9, "entrypoint": "claude-vscode", "status": "idle", "updated_at": 0}
        ok, message = sessions.release_session("s", timeout=0.5)
        self.assertTrue(ok, message)
        self.assertEqual(self.killed, [9])
        self.assertIn("bash.exe×2", message)
        self.assertIn("python.exe", message)

    def test_nobody_holding_it_needs_nothing(self):
        self.assertEqual(sessions.release_session("s", timeout=0.1), (True, "目前沒有被佔用"))
        self.assertEqual(self.killed, [])


class OutcomeTest(unittest.TestCase):

    def setUp(self):
        self.log = Path(tempfile.mkdtemp(prefix="ctr_out_")) / "resume.log"

    def _result(self, **kw):
        base = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 7, "total_cost_usd": 0.42,
                "result": "我停下來了"}
        base.update(kw)
        self.log.write_text("some stderr line\n" + json.dumps(base, ensure_ascii=False) + "\n", encoding="utf-8")

    def test_exit_zero_with_blocked_tools_is_not_plain_success(self):
        self._result(permission_denials=[
            {"tool_name": "Bash", "tool_input": {"command": "git -C C:/x log --oneline -3"}},
            {"tool_name": "Read", "tool_input": {"file_path": "C:/Users/user/Documents/worktrees/x.py"}},
            {"tool_name": "Bash", "tool_input": {"command": "git -C C:/x log --oneline -3"}}])
        out = launch.read_outcome(self.log)
        self.assertEqual(out["denials"], ["Bash(git -C C:/x log --oneline -3)",
                                          "Read(C:/Users/user/Documents/worktrees/x.py)"])
        line = launch.outcome_line(out)
        self.assertIn("被權限擋了 2 種", line)
        self.assertIn("7 輪", line)

    def test_clean_and_error_results_read_as_such(self):
        self._result()
        self.assertEqual(launch.outcome_line(launch.read_outcome(self.log)), "完成（7 輪、$0.42）")
        self._result(is_error=True, subtype="error_max_turns")
        self.assertTrue(launch.outcome_line(launch.read_outcome(self.log)).startswith("claude 回報錯誤"))
        self.log.write_text("no json here\n", encoding="utf-8")
        self.assertIsNone(launch.read_outcome(self.log))


if __name__ == "__main__":
    unittest.main()
