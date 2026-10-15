# -*- coding: utf-8 -*-
"""ClaudeResumer 命令列版（claude_timer/cli.py）的離線測試。

    .venv\\Scripts\\python -m unittest discover -s tests -v

不碰真的 Claude、不碰工作排程器、不碰你的 %LOCALAPPDATA% 和 ~/.claude：
- 假的 claude（CLAUDE_TIMER_CLAUDE_BIN 指到一支 .py）：在 cwd 改一個已追蹤的檔案，最後印出跟 claude
  --output-format json 一樣的結果 JSON。
- LOCALAPPDATA、USERPROFILE／HOME 換成暫存資料夾，裡面放一個假的對話檔。
- CLAUDE_TIMER_NO_SCHTASKS=1：排時間只產生 XML，不註冊。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["CLAUDE_RESUMER_NOTIFY"] = "0"   # 測試絕對不能真的傳到擁有者的 Telegram；要測的那一個自己開

FAKE_CLAUDE = r'''
import json, os, sys
args = sys.argv[1:]
sid = args[args.index("--resume") + 1] if "--resume" in args else args[args.index("--session-id") + 1]
prompt = args[args.index("--print") + 1]
if os.environ.get("FAKE_CLAUDE_ARGS"):
    with open(os.environ["FAKE_CLAUDE_ARGS"], "a", encoding="utf-8") as f:
        f.write(json.dumps(args) + "\n")
if os.path.exists("README.md"):
    with open("README.md", "a", encoding="utf-8") as f:
        f.write("\nchanged by claude: " + prompt + "\n")
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok: " + prompt,
                  "total_cost_usd": 0.0012, "session_id": sid, "num_turns": 1}))
'''


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


class CliTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ctr_cli_"))
        self.env_backup = dict(os.environ)
        home = self.tmp / "home"
        os.environ.update({"LOCALAPPDATA": str(self.tmp / "appdata"), "USERPROFILE": str(home), "HOME": str(home),
                           "CLAUDE_TIMER_NO_SCHTASKS": "1", "CLAUDE_TIMER_CONFIRM_SECONDS": "15"})
        fake = self.tmp / "fake_claude.py"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        os.environ["CLAUDE_TIMER_CLAUDE_BIN"] = str(fake)
        self.args_log = self.tmp / "claude_args.jsonl"
        os.environ["FAKE_CLAUDE_ARGS"] = str(self.args_log)
        self.repo = self.tmp / "WebApp"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "README.md").write_text("hello\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "init")
        self.sid = "11111111-2222-3333-4444-555555555555"
        proj = home / ".claude" / "projects" / "C--tmp-WebApp"
        proj.mkdir(parents=True)
        lines = [
            {"type": "user", "cwd": str(self.repo), "timestamp": "2026-10-03T10:00:00Z",
             "message": {"role": "user", "content": "幫我做登入頁"}},
            {"type": "assistant", "timestamp": "2026-10-03T10:01:00Z",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "好，做到一半額度用完了"}]}},
            {"type": "last-prompt", "lastPrompt": "幫我做登入頁"},
        ]
        (proj / f"{self.sid}.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines),
                                                encoding="utf-8")
        from claude_timer.resumer import sessions
        self.sessions = sessions
        self.holder = {}
        self._orig_holders = sessions.active_holders
        sessions.active_holders = lambda: dict(self.holder)
        # 假的「釋放」：記下被叫了幾次、把持有者清掉（真的會 taskkill，測試裡不能跑）
        self.released = []
        self._orig_release = sessions.release_session

        def fake_release(session_id, timeout=10.0, *, allow_busy=False):
            self.released.append(session_id)
            if self.holder.pop(session_id, None) is None:
                return True, "目前沒有被佔用"
            return True, "已釋放（關閉 pid 1）"
        sessions.release_session = fake_release

    def tearDown(self):
        self.sessions.active_holders = self._orig_holders
        self.sessions.release_session = self._orig_release
        os.environ.clear()
        os.environ.update(self.env_backup)

    def claude_args(self):
        return [json.loads(x) for x in self.args_log.read_text(encoding="utf-8").splitlines() if x.strip()]

    def run_cli(self, *args):
        from claude_timer import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(args) + ["--json"])
        return code, json.loads(out.getvalue())

    # ── 清單、摘要、檢查（不花錢）──

    def test_sessions_show_check(self):
        code, data = self.run_cli("sessions", "--cwd", str(self.repo))
        self.assertEqual(code, 0)
        self.assertEqual([s["session_id"] for s in data["sessions"]], [self.sid])
        self.assertEqual(data["sessions"][0]["last_prompt"], "幫我做登入頁")
        code, data = self.run_cli("show", self.sid)
        self.assertEqual(code, 0)
        self.assertIn("額度用完", data["last_assistant"])
        code, data = self.run_cli("check", self.sid)
        self.assertTrue(data["ok"])
        self.holder[self.sid] = {"pid": 1, "entrypoint": "claude-vscode", "cwd": str(self.repo)}
        code, data = self.run_cli("check", self.sid)
        self.assertEqual((code, data["busy"]), (3, True))
        code, data = self.run_cli("show", "no-such-session")
        self.assertEqual(code, 2)

    # ── 接續：現在送、結果、改動、收起來 ──

    def test_resume_now_reads_result_and_stash(self):
        code, job = self.run_cli("resume", self.sid, "--prompt", "continue")
        self.assertEqual(code, 0, job)
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["result"], "ok: continue")
        self.assertAlmostEqual(job["cost_usd"], 0.0012)
        self.assertEqual(os.path.normcase(job["run_cwd"]), os.path.normcase(str(self.repo)))
        self.assertTrue(job["start_commit"])
        self.assertIn("README.md", job["changes"])
        code, out = self.run_cli("stash", job["id"])
        self.assertEqual(code, 0)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertIn(f"resume-{job['id']}", git(self.repo, "stash", "list"))

    def test_a_dirty_tree_is_snapshotted_then_resumed(self):
        """v1.5（擁有者 2026-10-15）：髒的不再擋，先存快照照樣接。不動工作目錄、不動暫存區、不多分支。"""
        (self.repo / "README.md").write_text("dirty\n", encoding="utf-8")
        (self.repo / "new.txt").write_text("new file, not added yet\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text(".env\n", encoding="utf-8")
        (self.repo / ".env").write_text("SECRET=must-not-be-snapshotted\n", encoding="utf-8")
        branches = git(self.repo, "branch", "--list")
        code, job = self.run_cli("resume", self.sid, "--prompt", "continue")
        self.assertEqual((code, job["status"]), (0, "done"), job)
        ref = job["snapshot"]
        self.assertTrue(ref.startswith("refs/resumer/snapshots/") and ref.endswith(self.sid[:8]), ref)
        self.assertEqual(git(self.repo, "show", f"{ref}:README.md"), "dirty")      # 接手前那一刻的內容
        self.assertEqual(git(self.repo, "show", f"{ref}:new.txt"), "new file, not added yet")
        self.assertNotIn(".env", git(self.repo, "ls-tree", "-r", "--name-only", ref))  # .gitignore 照樣有效
        self.assertEqual(git(self.repo, "rev-parse", f"{ref}^"), git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(git(self.repo, "branch", "--list"), branches)            # 沒有多一個分支
        self.assertIn("?? new.txt", git(self.repo, "status", "--porcelain"))      # 暫存區沒被動到
        self.assertIn("changed by claude", (self.repo / "README.md").read_text(encoding="utf-8"))
        # 接手的那一段知道那些改動是上一段留下的、要照原樣接著做（擁有者 2026-10-15：「下一個 session 接手時會遇到什麼」）
        args = self.claude_args()[-1]
        sent = args[args.index("--print") + 1]
        self.assertIn(ref, sent)
        self.assertIn("還沒 commit", sent)
        self.assertIn("不要 git stash、reset、checkout", sent)
        self.assertTrue(sent.endswith("continue"))
        code, job = self.run_cli("resume", self.sid, "--prompt", "again", "--no-require-clean")   # 舊旗標還收
        self.assertEqual(job["status"], "done")

    def test_a_clean_tree_needs_no_snapshot_and_old_ones_are_pruned(self):
        code, job = self.run_cli("resume", self.sid, "--prompt", "continue")
        self.assertEqual(job["snapshot"], "")
        from claude_timer.resumer import snapshot
        for i in range(snapshot.KEEP + 3):
            (self.repo / "README.md").write_text(f"dirty {i}\n", encoding="utf-8")
            git(self.repo, "update-ref", f"{snapshot.PREFIX}20260101-0000{i:02d}-old", "HEAD")
        snapshot.prune(str(self.repo))
        self.assertEqual(len(snapshot.listing(str(self.repo))), snapshot.KEEP)

    def test_takeover_snapshots_before_closing_the_holder_and_notifies(self):
        """接管：關掉持有者之前先存快照；做完經 Notifier_TB 傳一則（假的 Notifier：本機 socket）。"""
        import socket
        import threading
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(5)
        got = []

        def serve():
            server.settimeout(60)
            try:
                conn, _ = server.accept()
                with conn:
                    got.append(json.loads(conn.recv(65536).decode("utf-8")))
            except OSError:
                pass
        threading.Thread(target=serve, daemon=True).start()
        nt = self.tmp / "Notifier_TB"
        nt.mkdir()
        (nt / "config.json").write_text(json.dumps({"socket_host": "127.0.0.1", "socket_port": server.getsockname()[1],
                                                    "socket_secret": "s3"}), encoding="utf-8")
        os.environ.update({"NOTIFIER_TB_HOME": str(nt), "CLAUDE_RESUMER_NOTIFY": "1"})
        (self.repo / "README.md").write_text("half done\n", encoding="utf-8")
        seen_at_release = []

        def release_and_look(session_id, timeout=10.0, *, allow_busy=False):
            seen_at_release.append(git(self.repo, "for-each-ref", "refs/resumer/"))
            self.holder.pop(session_id, None)
            return True, "已釋放（關閉 pid 1）"
        self.sessions.release_session = release_and_look
        self.holder[self.sid] = {"pid": 1, "entrypoint": "claude-vscode", "cwd": str(self.repo)}
        code, job = self.run_cli("resume", self.sid, "--prompt", "continue", "--take-over")
        self.assertEqual(job["status"], "done", job)
        self.assertTrue(job["took_over"] and job["snapshot"])
        self.assertIn(job["snapshot"], seen_at_release[0])                     # 關掉之前就存好了
        deadline = time.time() + 60
        while not got and time.time() < deadline:
            time.sleep(0.2)
        server.close()
        self.assertTrue(got, "做完要經 Notifier_TB 傳一則")
        self.assertEqual(got[0]["sig"], "s3")
        text = got[0]["payload"]
        self.assertIn("接續做完了（WebApp）", text)
        self.assertIn("接管", text)
        self.assertIn(job["snapshot"], text)

    # ── 排時間：一次性工作、到點 fire、重複 fire 安全、內容釘死 ──

    def test_scheduled_fire_is_idempotent_and_pinned(self):
        code, job = self.run_cli("resume", self.sid, "--prompt", "continue", "--at", "+60", "--dry-run")
        self.assertEqual((code, job["status"]), (0, "scheduled"))
        self.assertIn(f"fire {job['id']}", job["task_xml"])
        self.assertIn("<Interval>PT2M</Interval>", job["task_xml"])
        self.assertIn("StartWhenAvailable", job["task_xml"])
        code, fired = self.run_cli("fire", job["id"])
        self.assertEqual((code, fired["status"]), (0, "done"))
        code, again = self.run_cli("fire", job["id"])
        self.assertEqual((code, again["status"], again["fired_at"]), (0, "done", fired["fired_at"]))
        code, other = self.run_cli("resume", self.sid, "--prompt", "原本的話", "--at", "+60", "--dry-run")
        from claude_timer.resumer import jobs as store
        tampered = store.get(other["id"])
        tampered.prompt = "rm -rf 一切"
        store.update(tampered)
        code, result = self.run_cli("fire", other["id"])
        self.assertEqual((code, result["status"]), (1, "failed"))
        self.assertIn("不一樣", result["error"])

    def test_busy_session_waits_then_sends(self):
        code, job = self.run_cli("resume", self.sid, "--prompt", "continue", "--at", "+1", "--dry-run")
        self.holder[self.sid] = {"pid": 1, "entrypoint": "claude-vscode", "cwd": str(self.repo)}
        code, waiting = self.run_cli("fire", job["id"])
        self.assertEqual((code, waiting["status"]), (3, "waiting"))
        self.holder.clear()
        code, sent = self.run_cli("fire", job["id"])
        self.assertEqual((code, sent["status"]), (0, "done"))

    # ── 被佔用時：到時接管、分支接續、只釋放 ──

    def test_take_over_releases_holder_at_fire_time(self):
        code, job = self.run_cli("resume", self.sid, "--prompt", "continue", "--at", "+1", "--take-over",
                                 "--dry-run")
        self.assertEqual((code, job["status"], job["busy_policy"]), (0, "scheduled", "takeover"))
        self.assertEqual(self.released, [], "排程時不該動手，到點才接管")
        self.holder[self.sid] = {"pid": 1, "entrypoint": "claude-vscode", "cwd": str(self.repo)}
        code, fired = self.run_cli("fire", job["id"])
        self.assertEqual((code, fired["status"]), (0, "done"), fired)
        self.assertEqual(self.released, [self.sid])
        self.assertNotIn("--fork-session", self.claude_args()[-1])
        # 預設（wait）的那一筆照舊只等不搶
        self.holder[self.sid] = {"pid": 1, "entrypoint": "claude-vscode", "cwd": str(self.repo)}
        code, waiting = self.run_cli("resume", self.sid, "--prompt", "continue")
        self.assertEqual((code, waiting["status"], waiting["busy_policy"]), (3, "waiting", "wait"))
        self.assertEqual(self.released, [self.sid])

    def test_fork_does_not_wait_and_passes_fork_session(self):
        self.holder[self.sid] = {"pid": 1, "entrypoint": "claude-vscode", "cwd": str(self.repo)}
        code, job = self.run_cli("resume", self.sid, "--prompt", "continue", "--fork")
        self.assertEqual((code, job["status"], job["busy_policy"]), (0, "done", "fork"), job)
        self.assertIn("--fork-session", self.claude_args()[-1])
        self.assertEqual(self.released, [])
        code, err = self.run_cli("resume", self.sid, "--prompt", "x", "--fork", "--take-over")
        self.assertEqual(code, 4)

    def test_resume_carries_the_conversations_own_mode_model_and_effort(self):
        """v1.4：不指定就是 inherit——照 VSCode 裡最後用的權限模式、模型、effort；接管那段（sdk-cli）的 default 不算。"""
        jsonl = next((self.tmp / "home" / ".claude" / "projects").glob(f"*/{self.sid}.jsonl"))
        extra = [
            {"type": "user", "entrypoint": "claude-vscode", "permissionMode": "auto", "cwd": str(self.repo),
             "message": {"role": "user", "content": "繼續"}},
            {"type": "assistant", "entrypoint": "claude-vscode", "requestedModel": "claude-opus-5-5", "effort": "medium",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "好"}]}},
            {"type": "user", "entrypoint": "sdk-cli", "permissionMode": "default", "cwd": str(self.repo),
             "message": {"role": "user", "content": "continue"}},
        ]
        with open(jsonl, "a", encoding="utf-8") as fh:
            fh.write("\n" + "\n".join(json.dumps(x, ensure_ascii=False) for x in extra))
        code, job = self.run_cli("resume", self.sid, "--prompt", "continue")
        self.assertEqual(code, 0, job)
        args = self.claude_args()[-1]
        self.assertEqual(args[args.index("--permission-mode") + 1], "auto")
        self.assertEqual(args[args.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(args[args.index("--effort") + 1], "medium")
        code, job = self.run_cli("resume", self.sid, "--prompt", "continue", "--permission-mode", "acceptEdits",
                                 "--no-require-clean")          # 上一次的假 claude 改了 README
        self.assertEqual(code, 0, job)
        args = self.claude_args()[-1]
        self.assertEqual(args[args.index("--permission-mode") + 1], "acceptEdits")   # 明講的照明講的

    def test_release_verb(self):
        code, data = self.run_cli("release", self.sid)
        self.assertEqual((code, data["was_busy"], data["released"]), (0, False, True))
        self.holder[self.sid] = {"pid": 1, "entrypoint": "claude-vscode", "cwd": str(self.repo), "name": "x"}
        code, data = self.run_cli("check", self.sid)
        self.assertEqual((code, data["busy"]), (3, True))
        self.assertIn("VSCode", data["holder"])
        code, data = self.run_cli("release", self.sid)
        self.assertEqual((code, data["was_busy"], data["released"]), (0, True, True))
        self.assertEqual(self.released, [self.sid])
        self.assertNotIn(self.sid, self.holder)
        code, data = self.run_cli("release", "no-such-session")
        self.assertEqual(code, 2)

    # ── 新對話：獨立 worktree、不碰原本的資料夾 ──

    def test_new_runs_in_its_own_worktree(self):
        code, job = self.run_cli("new", "--cwd", str(self.repo), "--prompt", "加一個關於頁")
        self.assertEqual((code, job["status"]), (0, "done"), job)
        self.assertEqual(job["branch"], f"resume/{job['id']}")
        self.assertIn("worktrees", job["run_cwd"])
        self.assertEqual((self.repo / "README.md").read_text(encoding="utf-8"), "hello\n")
        self.assertIn("加一個關於頁", (Path(job["run_cwd"]) / "README.md").read_text(encoding="utf-8"))
        self.assertIn(job["branch"], git(self.repo, "branch", "--list", "resume/*"))
        self.assertEqual(len(job["session_id"]), 36)
        plain = self.tmp / "plain"
        plain.mkdir()
        code, job = self.run_cli("new", "--cwd", str(plain), "--prompt", "x")
        self.assertEqual((code, job["status"]), (1, "failed"))
        self.assertIn("不是 git repo", job["error"])
        code, job = self.run_cli("new", "--cwd", str(plain), "--prompt", "x", "--sandbox", "none")
        self.assertEqual(job["status"], "done")

    # ── 清單、取消、時間 ──

    def test_jobs_cancel_and_times(self):
        self.run_cli("resume", self.sid, "--prompt", "a", "--at", "+30", "--dry-run")
        self.run_cli("resume", self.sid, "--prompt", "b", "--at", "+40", "--dry-run")
        code, listed = self.run_cli("jobs")
        self.assertEqual(len(listed["jobs"]), 2)
        code, cancelled = self.run_cli("cancel", "--all")
        self.assertEqual(len(cancelled["cancelled"]), 2)
        code, listed = self.run_cli("jobs")
        self.assertTrue(all(j["status"] == "cancelled" for j in listed["jobs"]))
        from claude_timer.cli import parse_at
        now = datetime(2026, 10, 3, 22, 0)
        self.assertEqual(parse_at("+30", now), datetime(2026, 10, 3, 22, 30))
        self.assertEqual(parse_at("09:00", now), datetime(2026, 10, 4, 9, 0))
        self.assertEqual(parse_at("23:15", now), datetime(2026, 10, 3, 23, 15))
        self.assertEqual(parse_at("2026-10-05 08:00", now), datetime(2026, 10, 5, 8, 0))
        code, err = self.run_cli("resume", self.sid, "--prompt", "x", "--at", "明天")
        self.assertEqual(code, 4)


if __name__ == "__main__":
    unittest.main()
