# -*- coding: utf-8 -*-
"""job 清單（claude_timer/resumer/jobs.py）在好幾個 CLI 行程同時寫的時候不能掉資料。

    .venv\\Scripts\\python -m unittest discover -s tests -v

ClawBot 可能同時派兩筆、工作排程器可能同一分鐘觸發好幾筆，每一筆都是獨立的行程。以前只有行程內的鎖：
兩個行程各讀一份、各寫回去，後寫的蓋掉先寫的；讀到一半遇到別人換檔還會當成空的、整份清掉。
這裡真的開四個行程同時各寫 25 筆，一筆都不能少。全部在暫存的 LOCALAPPDATA，不碰你的。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

WRITER = r'''
import sys
sys.path.insert(0, sys.argv[1])
from claude_timer.resumer import jobs as store
tag = sys.argv[2]
for i in range(25):
    store.update(store.Job(id=f"{tag}-{i:02d}", kind="new", cwd=".", prompt=f"{tag} {i}"))
'''


class JobStoreTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ctr_store_"))
        self.env = {**os.environ, "LOCALAPPDATA": str(self.tmp)}

    def test_parallel_writers_do_not_lose_jobs(self):
        script = self.tmp / "writer.py"
        script.write_text(WRITER, encoding="utf-8")
        procs = [subprocess.Popen([sys.executable, str(script), str(ROOT), f"w{n}"], env=self.env)
                 for n in range(4)]
        for p in procs:
            self.assertEqual(p.wait(timeout=120), 0)
        data = json.loads((self.tmp / "ClaudeTimerResetter" / "resume_jobs.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data), 100, f"少了 {100 - len(data)} 筆")
        leftovers = list((self.tmp / "ClaudeTimerResetter").glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_corrupt_file_is_kept_aside_not_silently_reused(self):
        backup = dict(os.environ)
        os.environ["LOCALAPPDATA"] = str(self.tmp)
        try:
            sys.path.insert(0, str(ROOT))
            from claude_timer.resumer import jobs as store
            folder = self.tmp / "ClaudeTimerResetter"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "resume_jobs.json").write_text("{ 壞掉", encoding="utf-8")
            self.assertEqual(store.load(), {})
            self.assertTrue(list(folder.glob("resume_jobs.corrupt-*.json")))
        finally:
            os.environ.clear()
            os.environ.update(backup)


if __name__ == "__main__":
    unittest.main()
