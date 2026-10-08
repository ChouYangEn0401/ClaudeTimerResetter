# -*- coding: utf-8 -*-
"""對話清單（claude_timer/resumer/sessions.py）：來源分類、同名專案的標籤、分支，以及 CLI sessions 的預設過濾。

    .venv\\Scripts\\python -m unittest discover -s tests -v

全部在暫存資料夾裡做一份假的 ~/.claude/projects，不碰你的。
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


def _session(proj_dir: Path, sid: str, cwd: str, *, branch: str = "", entrypoint: str = "claude-vscode",
             text: str = "做點事") -> None:
    rec = {"type": "user", "cwd": cwd, "timestamp": "2026-10-08T01:00:00Z", "entrypoint": entrypoint,
           "message": {"role": "user", "content": text}}
    if branch:
        rec["gitBranch"] = branch
    lines = [rec, {"type": "last-prompt", "lastPrompt": text}]
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / f"{sid}.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines),
                                           encoding="utf-8")


class SessionsTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ctr_sess_"))
        self.env_backup = dict(os.environ)
        home = self.tmp / "home"
        self.local = self.tmp / "appdata"
        self.temp = self.tmp / "temp"
        os.environ.update({"USERPROFILE": str(home), "HOME": str(home), "LOCALAPPDATA": str(self.local),
                           "TEMP": str(self.temp), "TMP": str(self.temp)})
        projects = home / ".claude" / "projects"
        # 兩個不同地方都叫 WebApp；一個是 worktrees 底下的分身；一個是程式的工作區；一個在暫存
        self.a = self.tmp / "work" / "WebApp"
        self.b = self.tmp / "play" / "WebApp"
        self.lane = self.tmp / "worktrees" / "WebApp_LaneA"
        self.bot = self.local / "ClawBot" / "agent_workspace"
        self.scratch = self.temp / "claude" / "x" / "scratchpad"
        for d in (self.a, self.b, self.lane, self.bot, self.scratch):
            d.mkdir(parents=True)
        _session(projects / "p-a", "aaaaaaaa-0000-0000-0000-000000000001", str(self.a), branch="main")
        _session(projects / "p-b", "bbbbbbbb-0000-0000-0000-000000000002", str(self.b), branch="feat/x")
        _session(projects / "p-lane", "cccccccc-0000-0000-0000-000000000003", str(self.lane), branch="lane-a",
                 entrypoint="cli")
        _session(projects / "p-bot", "dddddddd-0000-0000-0000-000000000004", str(self.bot), entrypoint="sdk-cli",
                 text="[2026-10-08 10:42] 自上一輪以來")
        _session(projects / "p-tmp", "eeeeeeee-0000-0000-0000-000000000005", str(self.scratch), entrypoint="sdk-cli",
                 text="Run this exact bash command once")
        from claude_timer.resumer import sessions
        self.sessions = sessions
        self._orig_holders = sessions.active_holders
        sessions.active_holders = lambda: {}

    def tearDown(self):
        self.sessions.active_holders = self._orig_holders
        os.environ.clear()
        os.environ.update(self.env_backup)

    def test_origin_label_and_branch(self):
        found, total = self.sessions.scan_sessions(50)
        self.assertEqual(total, 5)
        by = {s.session_id[:8]: s for s in found}
        a, b, lane, bot, tmp = by["aaaaaaaa"], by["bbbbbbbb"], by["cccccccc"], by["dddddddd"], by["eeeeeeee"]
        # 同名專案帶上一層資料夾，分身另外標
        self.assertEqual((a.origin, a.display, a.branch), ("project", "work/WebApp", "main"))
        self.assertEqual((b.origin, b.display, b.branch), ("project", "play/WebApp", "feat/x"))
        self.assertEqual((lane.origin, lane.display, lane.branch), ("worktree", "WebApp_LaneA", "lane-a"))
        self.assertEqual(lane.source_label, "分身·終端")
        self.assertEqual((bot.origin, bot.source_label), ("app", "程式用·程式(-p)"))
        self.assertEqual((tmp.origin, tmp.source_label), ("temp", "暫存·程式(-p)"))
        self.assertEqual(a.source_label, "VSCode")
        self.assertTrue(bot.hidden_by_default and tmp.hidden_by_default)
        self.assertFalse(a.hidden_by_default or lane.hidden_by_default)
        # project 欄位（ClawBot 在用）維持資料夾名不變
        self.assertEqual(a.project, "WebApp")

    def test_worktree_detected_by_git_file(self):
        wt = self.tmp / "elsewhere" / "Thing"
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: ../../repo/.git/worktrees/Thing\n", encoding="utf-8")
        self.assertEqual(self.sessions.classify_origin(str(wt)), "worktree")
        self.assertEqual(self.sessions.classify_origin(""), "unknown")
        self.assertEqual(self.sessions.classify_origin(str(self.a)), "project")

    def _cli(self, *args):
        from claude_timer import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(args) + ["--json"])
        return code, json.loads(out.getvalue())

    def test_cli_sessions_hides_noise_unless_asked(self):
        code, data = self._cli("sessions")
        self.assertEqual(code, 0)
        ids = {s["session_id"][:8] for s in data["sessions"]}
        self.assertEqual(ids, {"aaaaaaaa", "bbbbbbbb", "cccccccc"})
        self.assertEqual(data["total"], 5)
        row = next(s for s in data["sessions"] if s["session_id"].startswith("aaaaaaaa"))
        self.assertEqual((row["label"], row["branch"], row["origin"], row["source"]),
                         ("work/WebApp", "main", "project", "VSCode"))
        code, data = self._cli("sessions", "--all")
        self.assertEqual(len(data["sessions"]), 5)
        # 指定資料夾時不過濾（ClawBot 的 /proj_sessions 要的就是那個資料夾的全部）
        code, data = self._cli("sessions", "--cwd", str(self.bot))
        self.assertEqual([s["session_id"][:8] for s in data["sessions"]], ["dddddddd"])


if __name__ == "__main__":
    unittest.main()
