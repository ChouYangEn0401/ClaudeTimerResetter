# -*- coding: utf-8 -*-
"""snapshot.py — 接手一個對話之前，把專案資料夾現在的樣子存一份到 git（v1.5，2026-10-15）。

為什麼：以前 resume 要求工作目錄乾淨（require_clean），髒的就不接；而且「到點接管」是先關掉持有者
才檢查，髒的話等於把人家關掉、又什麼都沒做。擁有者 2026-10-15：「雖然我不喜歡為了這樣把 git 備份用髒，
但是確實比資料都損失好一點」——所以改成**先存快照、照樣接著做**。

怎麼存（**不動你的工作目錄、不動你的暫存區、不多一個分支、不推出去**）：

    1. 用一份暫時的 index（GIT_INDEX_FILE）把現在的檔案全部 add 進去——.gitignore 照樣有效，
       所以 .env、data/ 這種本來就不進 git 的東西不會被存進來
    2. write-tree → commit-tree（父節點是 HEAD）→ 放在 refs/resumer/snapshots/<時間>-<對話前 8 碼>

refs/resumer/* 不是分支：`git branch`、VSCode 的分支清單、`git log` 都看不到，`git push` 也不會帶出去；
只有 `git log --all` 看得到。留最新的 KEEP 份，舊的自己刪（commit 之後 git gc 會收掉）。
拿回來：`git restore --source=<ref> -- <檔案>`，或 `git diff <ref>` 看接手之後改了什麼。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

PREFIX = "refs/resumer/snapshots/"
KEEP = 20
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Snapshot:
    ref: str = ""          # 空的＝沒存（乾淨、不是 git、或失敗）
    commit: str = ""
    files: int = 0         # 跟 HEAD 比，有幾個檔案不一樣
    note: str = ""         # 一句話：存了／不用存／為什麼沒存

    @property
    def taken(self) -> bool:
        return bool(self.ref)


def _git(cwd: str, *args: str, env: Optional[dict] = None) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60, env=env, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return r.returncode, (r.stdout if r.returncode == 0 else (r.stderr or r.stdout)).strip()


def take(cwd: str, session_id: str = "", *, reason: str = "接手前") -> Snapshot:
    """有還沒 commit 的改動就存一份；乾淨的、不是 git 的什麼都不做。永遠不丟例外。"""
    if not cwd or not Path(cwd).is_dir():
        return Snapshot(note="資料夾不存在，沒存快照")
    code, top = _git(cwd, "rev-parse", "--show-toplevel")
    if code != 0:
        return Snapshot(note="不是 git repo，沒存快照")
    code, status = _git(cwd, "status", "--porcelain", "--untracked-files=all")
    if code != 0:
        return Snapshot(note=f"看不到 git 狀態，沒存快照：{status[:120]}")
    changed = [line for line in status.splitlines() if line.strip()]
    if not changed:
        return Snapshot(note="工作目錄是乾淨的，不用存快照")
    code, head = _git(cwd, "rev-parse", "--verify", "HEAD")
    if code != 0:
        return Snapshot(note="這個 repo 還沒有任何 commit，沒存快照")
    fd, index = tempfile.mkstemp(prefix="resumer-index-")
    os.close(fd)
    os.unlink(index)                       # git 要自己建這個檔；先佔好名字就好
    env = dict(os.environ, GIT_INDEX_FILE=index)
    try:
        for step in (("read-tree", head), ("add", "-A", "--", ".")):
            code, text = _git(top, *step, env=env)
            if code != 0:
                return Snapshot(note=f"存快照失敗（git {step[0]}）：{text[:160]}")
        code, tree = _git(top, "write-tree", env=env)
        if code != 0:
            return Snapshot(note=f"存快照失敗（git write-tree）：{tree[:160]}")
    finally:
        Path(index).unlink(missing_ok=True)
    # 跟這個對話上一份快照一模一樣（例如接管要等持有者閒下來、每一輪都來試）：用原本那一份，
    # 不然一直重試會把 KEEP 份額吃光、把真正有用的舊快照擠掉
    suffix = "-" + (session_id or "manual")[:8]
    for ref in listing(top):
        if ref.endswith(suffix):
            if _git(top, "rev-parse", f"{ref}^{{tree}}")[1] == tree and _git(top, "rev-parse", f"{ref}^")[1] == head:
                return Snapshot(ref=ref, commit=_git(top, "rev-parse", ref)[1], files=len(changed),
                                note=f"跟上一份快照一樣，沿用 {ref}")
            break
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    message = f"resumer 快照：{reason}（{stamp}{'，對話 ' + session_id if session_id else ''}）"
    commit_env = dict(os.environ)
    commit_env.setdefault("GIT_AUTHOR_NAME", "ClaudeResumer")
    commit_env.setdefault("GIT_AUTHOR_EMAIL", "resumer@localhost")
    commit_env.setdefault("GIT_COMMITTER_NAME", "ClaudeResumer")
    commit_env.setdefault("GIT_COMMITTER_EMAIL", "resumer@localhost")
    code, commit = _git(top, "commit-tree", tree, "-p", head, "-m", message, env=commit_env)
    if code != 0:
        return Snapshot(note=f"存快照失敗（git commit-tree）：{commit[:160]}")
    ref = f"{PREFIX}{stamp}-{(session_id or 'manual')[:8]}"
    code, text = _git(top, "update-ref", ref, commit)
    if code != 0:
        return Snapshot(note=f"存快照失敗（git update-ref）：{text[:160]}")
    prune(top)
    return Snapshot(ref=ref, commit=commit, files=len(changed),
                    note=f"接手前先存了快照：{len(changed)} 個檔案還沒 commit → {ref}")


def listing(cwd: str) -> list[str]:
    """這個 repo 的快照，新的在前。"""
    code, text = _git(cwd, "for-each-ref", "--sort=-refname", "--format=%(refname)", PREFIX)
    return [line for line in text.splitlines() if line.strip()] if code == 0 else []


def prune(cwd: str, keep: int = KEEP) -> int:
    """只留最新的 keep 份。回傳刪了幾份。"""
    removed = 0
    for ref in listing(cwd)[keep:]:
        if _git(cwd, "update-ref", "-d", ref)[0] == 0:
            removed += 1
    return removed
