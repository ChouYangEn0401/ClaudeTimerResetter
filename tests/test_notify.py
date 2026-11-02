# -*- coding: utf-8 -*-
"""v1.5.2：做完傳的那一則先經 ClawBot 的 /v1/notify，不行才經 Notifier_TB 的 socket。

    .venv\\Scripts\\python -m unittest discover -s tests -v

全部用假的：假的 opener（不開網路埠、不連 ClawBot）、假的 _post（不連 socket）、不開 Notifier、journal 寫進暫存資料夾。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_timer.resumer import notify  # noqa: E402


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """answer(path, body)：dict＝200、int＝HTTP 錯誤、None＝連不上。"""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def open(self, req, timeout=None):
        path = "/" + req.full_url.split("/", 3)[3]
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        self.calls.append((path, body, req.get_header("Authorization")))
        got = self.answer(path, body)
        if got is None:
            raise urllib.error.URLError("refused")
        if isinstance(got, int):
            raise urllib.error.HTTPError(req.full_url, got, "err", {}, io.BytesIO(b"{}"))
        return FakeResp(json.dumps(got).encode("utf-8"))


def fake_link(answer):
    link = notify.ClawBotLink("http://127.0.0.1:8765", "tok")
    link._opener = FakeOpener(answer)
    return link


class NotifyViaClawBotTest(unittest.TestCase):

    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in ("CLAUDE_RESUMER_NOTIFY", "LOCALAPPDATA", "CLAWBOT_HOME")}
        os.environ["CLAUDE_RESUMER_NOTIFY"] = "1"
        os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ctr_appdata_")   # journal 不寫進你的資料夾
        self.originals = (notify.find_clawbot, notify.find, notify._post, notify._wake)
        self.posted = []
        notify.find = lambda: ("127.0.0.1", 9999, "s", Path("."))
        notify._post = lambda host, port, secret, text: self.posted.append(text) or True
        notify._wake = lambda folder: self.fail("不該去開 Notifier")

    def tearDown(self):
        notify.find_clawbot, notify.find, notify._post, notify._wake = self.originals
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_done_goes_to_clawbot_as_task_done(self):
        link = fake_link(lambda p, b: {"ok": True, "bot": "main"})
        notify.find_clawbot = lambda: link
        self.assertTrue(notify.send("✅ 做完了"))
        path, body, auth = link._opener.calls[0]
        self.assertEqual(path, "/v1/notify")
        self.assertEqual(auth, "Bearer tok")
        self.assertEqual(body, {"text": "✅ 做完了", "stream": "task", "kind": "done", "source": "ClaudeResumer"})
        self.assertEqual(self.posted, [])                        # ClawBot 收了就不走 socket

    def test_failed_is_task_problem(self):
        link = fake_link(lambda p, b: {"ok": True, "bot": "main"})
        notify.find_clawbot = lambda: link
        notify.send("❌", failed=True)
        self.assertEqual(link._opener.calls[0][1]["kind"], "problem")

    def test_clawbot_down_falls_back_to_socket(self):
        notify.find_clawbot = lambda: fake_link(lambda p, b: None)
        self.assertTrue(notify.send("x"))
        self.assertEqual(self.posted, ["x"])

    def test_clawbot_refuses_falls_back_to_socket(self):
        notify.find_clawbot = lambda: fake_link(lambda p, b: 403)
        self.assertTrue(notify.send("x"))
        self.assertEqual(self.posted, ["x"])

    def test_no_clawbot_falls_back_to_socket(self):
        notify.find_clawbot = lambda: None
        self.assertTrue(notify.send("x"))
        self.assertEqual(self.posted, ["x"])

    def test_switched_off_sends_nothing(self):
        os.environ["CLAUDE_RESUMER_NOTIFY"] = "0"
        notify.find_clawbot = lambda: self.fail("關掉了不該找 ClawBot")
        self.assertFalse(notify.send("x"))
        self.assertEqual(self.posted, [])

    def test_find_clawbot_picks_the_owner_token(self):
        notify.find_clawbot = self.originals[0]
        home = Path(tempfile.mkdtemp(prefix="ctr_clawbot_"))
        (home / "data").mkdir()
        (home / "data" / "gateway.json").write_text(json.dumps(
            {"url": "http://127.0.0.1:8765", "tokens": {"other": {"name": "x"}, "own": {"name": "owner"}}}),
            encoding="utf-8")
        os.environ["CLAWBOT_HOME"] = str(home)
        tried = []
        original_is_owner = notify.ClawBotLink.is_owner
        try:
            notify.ClawBotLink.is_owner = lambda self: tried.append(self.token) or self.token == "own"
            link = notify.find_clawbot()
            self.assertIsNotNone(link)
            self.assertEqual(link.token, "own")
            self.assertEqual(tried[:1], ["own"])              # 名字叫 owner 的先試
            tried.clear()
            notify.ClawBotLink.is_owner = lambda self: tried.append(self.token) and None
            self.assertIsNone(notify.find_clawbot())         # Host 沒開：不再試別的 token
            self.assertEqual(len(tried), 1)
        finally:
            notify.ClawBotLink.is_owner = original_is_owner

    def test_long_text_is_cut(self):
        link = fake_link(lambda p, b: {"ok": True})
        link.notify("字" * 5000)
        self.assertEqual(len(link._opener.calls[0][1]["text"]), 4000)


if __name__ == "__main__":
    unittest.main()
