# -*- coding: utf-8 -*-
"""cli.py — ClaudeResumer 的命令列版：不開視窗，任何程式（ClawBot、別的 agent、腳本）都能叫（2026-10-03）。

    python -m claude_timer.cli sessions [--cwd 資料夾] [--limit N] [--all]  本機的對話清單（預設藏暫存／程式用的）
    python -m claude_timer.cli show <session> [--turns N]                  一個對話的摘要；--turns 多印最近 N 輪（不花錢）
    python -m claude_timer.cli check <session>                             送出前檢查（不花錢）
    python -m claude_timer.cli release <session>                           把佔用這個對話的 Claude Code 關掉，不送出（不花錢）
    python -m claude_timer.cli resume <session> --prompt … [--at 時間]      接續舊對話（沒給 --at 就現在送）
                                   [--take-over | --fork]                  到點被佔用時：關掉持有者再送／不搶、開新的對話 id
    python -m claude_timer.cli new --cwd 資料夾 --prompt … [--at 時間]      在專案開一個全新的對話做新任務
    python -m claude_timer.cli jobs | job <id> | cancel <id>|--all         看排程、看結果、取消
    python -m claude_timer.cli stash <id>                                  把那筆接續做的改動收起來（git stash，找得回來）
    python -m claude_timer.cli fire <id>                                   （Windows 工作排程器呼叫的，平常不用打）

每個子指令都可以加 --json（給程式讀）。結束碼：0 成功／1 失敗／2 找不到（對話、job）／3 等待中（對話被佔用、
還沒到時間）／4 參數不對。完整的說明在 docs/cli.md，給 agent 讀的在 skills/claude-resumer/SKILL.md。

排程不靠任何常駐程式：有 --at 的 job 會在 Windows 工作排程器註冊一次性工作 ClaudeResumer-<id>，到點跑
`fire <id>`；對話被佔用時每 2 分鐘補試、最多 1 小時；電腦那時是關的，開機後補跑。

「跑的是什麼」的限制做在這裡（誰能下令由呼叫的那一方決定，例如 ClawBot 的驗證碼與風險分級）：
- 內容釘死：建立時算 content_hash（含 busy_policy），fire 時不一樣就不跑。
- 被佔用時預設只等不搶：--take-over 是建立時就講好的（到點才關掉持有者），release 是現在就關、不送。
- new 預設 --sandbox worktree：在獨立的 git worktree＋branch（resume/<id>）跑，不碰你的工作目錄。
- resume 預設 --require-clean：工作目錄要乾淨，記下起點 commit，做完可以 `stash <id>` 收回來。
- --allowed-tools／--disallowed-tools／--max-budget-usd 原樣交給 claude。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from . import __version__
from .core import scheduler
from .paths import ensure_install_dir, install_dir, projects_dir
from .resumer import jobs as store
from .resumer import sessions as sess
from .resumer import transcript
from .resumer.launch import CONFIRM_SECONDS, build_argv, start_detached
from .resumer.tasks import PERMISSION_MODES, preflight

EXIT_OK, EXIT_FAILED, EXIT_NOT_FOUND, EXIT_WAIT, EXIT_BAD = 0, 1, 2, 3, 4
ROOT = Path(__file__).resolve().parent.parent
MODES = [m for m in PERMISSION_MODES if not m.startswith("(")] + ["auto", "default"]


class CliError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _no_schtasks() -> bool:
    return os.environ.get("CLAUDE_TIMER_NO_SCHTASKS") == "1"


def _confirm_seconds() -> float:
    try:
        return float(os.environ.get("CLAUDE_TIMER_CONFIRM_SECONDS", CONFIRM_SECONDS))
    except ValueError:
        return CONFIRM_SECONDS


def _git(cwd: str, *args: str) -> Tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        return 1, str(e)
    return r.returncode, (r.stdout or r.stderr).strip()


def _is_repo(cwd: str) -> bool:
    return _git(cwd, "rev-parse", "--is-inside-work-tree")[0] == 0


def parse_at(value: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """+30（分鐘後）、09:00（今天，過了就明天）、2026-10-04 09:00；空字串＝現在送。"""
    now = now or datetime.now()
    value = (value or "").strip()
    if not value:
        return None
    try:
        if value.startswith("+"):
            return now + timedelta(minutes=float(value[1:]))
        if len(value) <= 5 and ":" in value:
            hh, mm = (int(x) for x in value.split(":"))
            at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            return at if at > now else at + timedelta(days=1)
        return datetime.fromisoformat(value.replace(" ", "T"))
    except (ValueError, TypeError):
        raise CliError(EXIT_BAD, f"看不懂時間：{value}（用 +30、09:00 或 2026-10-04 09:00）")


def find_session(session_id: str) -> sess.SessionInfo:
    root = projects_dir()
    for path in root.glob(f"*/{session_id}.jsonl") if root.exists() else []:
        st = path.stat()
        info = sess._peek(path, st.st_mtime, st.st_size)
        if info is not None:
            return info
    raise CliError(EXIT_NOT_FOUND, f"找不到對話 {session_id}（先用 sessions 看清單）")


def _session_dict(s: sess.SessionInfo, holders: dict) -> dict:
    holder = holders.get(s.session_id)
    return {"session_id": s.session_id, "cwd": s.cwd, "project": s.project, "label": s.display, "title": s.title,
            "branch": s.branch, "entrypoint": s.entrypoint, "origin": s.origin, "source": s.source_label,
            "last_prompt": s.last_prompt, "last_active": datetime.fromtimestamp(s.mtime).isoformat(timespec="seconds"),
            "relative": s.relative_time, "busy": bool(holder),
            "holder": sess.describe_holder(holder) if holder else ""}


# ── jobs ──────────────────────────────────────────────────────────────

def refresh(job: store.Job) -> store.Job:
    """sent 的 job：log 裡有結果就是 done；行程不在了又沒結果就是 failed。"""
    if job.status != store.SENT:
        return job
    data = store.read_result(job)
    if data is not None:
        job.status = store.FAILED if data.get("is_error") else store.DONE
        job.result = str(data.get("result") or "")[:4000]
        job.cost_usd = float(data.get("total_cost_usd") or 0.0)
        job.session_id = data.get("session_id") or job.session_id
        job.finished_at = datetime.now().isoformat(timespec="seconds")
        if data.get("is_error"):
            job.error = f"claude 回報錯誤：{data.get('subtype')}"
        store.update(job)
    elif not store.alive(job):
        job.status = store.FAILED
        job.error = "claude 結束了，但 log 裡沒有結果：" + (store.log_tail(job) or "（沒有輸出）")
        job.finished_at = datetime.now().isoformat(timespec="seconds")
        store.update(job)
    return job


def changes(job: store.Job) -> str:
    if not job.start_commit or not job.run_cwd:
        return ""
    code, text = _git(job.run_cwd, "diff", "--stat", job.start_commit)
    return text if code == 0 else ""


def job_view(job: store.Job) -> dict:
    d = job.to_dict()
    d["changes"] = changes(job) if job.status in (store.SENT, store.DONE, store.FAILED) else ""
    return d


def fire(job: store.Job) -> int:
    """把一筆 job 送出去。已經送過（或結束、取消）的不再送——工作排程器重複觸發也安全。"""
    if job.status in store.FINAL or job.status == store.SENT:
        return EXIT_OK
    if job.content_hash != job.compute_hash():
        return _fail(job, "這筆排程的內容跟建立時不一樣（檔案被改過），不執行。")
    run_cwd, start_commit, branch = job.cwd, "", ""
    if job.kind == "resume":
        try:
            session = find_session(job.session_id)
        except CliError as e:
            return _fail(job, e.message)
        problems = preflight(session)
        if problems:
            return _fail(job, "；".join(problems))
        holder = sess.session_holder(job.session_id)
        fork = job.busy_policy == store.BUSY_FORK
        if holder and job.busy_policy == store.BUSY_TAKEOVER:
            # 排程時就講好「到點若被佔用就接管」：關掉持有它的行程，鎖釋放了才送
            ok, message = sess.release_session(job.session_id)
            if not ok:
                job.status, job.error = store.WAITING, f"到時接管失敗：{message}；排程會再試"
                store.update(job)
                return EXIT_WAIT
            holder = None
        if holder and not fork:
            job.status, job.error = store.WAITING, f"{sess.describe_holder(holder)}；關掉之後會自動補送"
            store.update(job)
            return EXIT_WAIT
        run_cwd = session.cwd
        if _is_repo(run_cwd):
            if job.require_clean and _git(run_cwd, "status", "--porcelain")[1]:
                return _fail(job, "工作目錄有還沒 commit 的改動：先 commit 或 stash，接續做的改動才收得回來"
                                  "（真的不在意就用 --no-require-clean 重排）。")
            start_commit = _git(run_cwd, "rev-parse", "HEAD")[1]
        argv = build_argv(job.prompt, resume=job.session_id, permission_mode=job.permission_mode,
                          allowed_tools=job.allowed_tools, disallowed_tools=job.disallowed_tools,
                          max_budget_usd=job.max_budget_usd or None, output_json=True, fork_session=fork)
    else:
        if not Path(job.cwd).is_dir():
            return _fail(job, f"資料夾不存在：{job.cwd}")
        if job.sandbox == "worktree":
            if not _is_repo(job.cwd):
                return _fail(job, "不是 git repo，沒辦法開獨立的 worktree（真的要直接在資料夾裡跑就用 --sandbox none）。")
            start_commit = _git(job.cwd, "rev-parse", "HEAD")[1]
            branch = f"resume/{job.id}"
            run_cwd = str(install_dir() / "worktrees" / job.id)
            code, text = _git(job.cwd, "worktree", "add", "-b", branch, run_cwd, start_commit)
            if code != 0:
                return _fail(job, f"開 worktree 失敗：{text}")
        elif _is_repo(job.cwd):
            start_commit = _git(job.cwd, "rev-parse", "HEAD")[1]
        argv = build_argv(job.prompt, session_id=job.session_id, permission_mode=job.permission_mode,
                          allowed_tools=job.allowed_tools, disallowed_tools=job.disallowed_tools,
                          max_budget_usd=job.max_budget_usd or None, output_json=True)
    ensure_install_dir()
    try:
        proc = start_detached(argv, run_cwd, job.log_path)
    except (OSError, ImportError) as e:
        return _fail(job, f"啟動 claude 失敗：{e}")
    job.pid, job.pid_created = proc.pid, store.process_created(proc.pid) or 0
    job.status, job.error = store.SENT, ""
    job.run_cwd, job.start_commit, job.branch = run_cwd, start_commit, branch
    job.fired_at = datetime.now().isoformat(timespec="seconds")
    store.update(job)
    if not _no_schtasks():
        scheduler.delete_once(job.id)
    deadline = time.monotonic() + _confirm_seconds()
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.2)
    if proc.poll() is not None:
        refresh(job)
        return EXIT_FAILED if job.status == store.FAILED else EXIT_OK
    return EXIT_OK


def _fail(job: store.Job, message: str) -> int:
    job.status, job.error = store.FAILED, message
    job.finished_at = datetime.now().isoformat(timespec="seconds")
    store.update(job)
    return EXIT_FAILED


def schedule_or_fire(job: store.Job, at: Optional[datetime], dry_run: bool) -> Tuple[int, str]:
    job.content_hash = job.compute_hash()
    store.update(job)
    if at is None:
        return fire(store.get(job.id)), ""
    xml = scheduler.install_once(job.id, at, sys.executable, str(ROOT), dry_run=dry_run or _no_schtasks())
    return EXIT_OK, xml


# ── subcommands ───────────────────────────────────────────────────────

def cmd_sessions(a) -> Tuple[int, object, str]:
    found, total = sess.scan_sessions(a.limit)
    if a.cwd:
        # 指定了資料夾就全部給（呼叫端要的就是「這個專案的」，不管它是程式開的還是人開的）
        want = os.path.normcase(os.path.abspath(a.cwd))
        found = [s for s in found if s.cwd and os.path.normcase(os.path.abspath(s.cwd)) == want]
    elif not a.all:
        found = [s for s in found if not s.hidden_by_default]
    holders = sess.active_holders()
    rows = [_session_dict(s, holders) for s in found]
    text = "\n".join(f"{r['session_id']}  {r['relative']:<8} {r['label'][:24]:<24} {r['branch'][:16]:<16} "
                     f"{r['source']:<12} {'● 開啟中 ' if r['busy'] else ''}"
                     f"{r['title'][:40]}｜{r['last_prompt'][:40]}" for r in rows) or "（沒有對話）"
    return EXIT_OK, {"sessions": rows, "total": total}, text


def _turn_dict(turn: transcript.Turn) -> dict:
    """一輪＝你說的那段＋它最後回的那段；中間的工具與思考只給一行摘要（要看去開視窗版）。"""
    return {"n": turn.index + 1,
            "when": turn.start.isoformat(timespec="seconds") if turn.start else "",
            "prompt": turn.prompt.text if turn.prompt else "",
            "reply": turn.reply.text if turn.reply else "",
            "middle": turn.middle_label()}


def cmd_show(a):
    s = find_session(a.session)
    t = transcript.read(s.jsonl)
    if not t.ok:
        raise CliError(EXIT_FAILED, t.reason)
    d = t.digest
    data = {"session_id": s.session_id, "cwd": s.cwd, "title": s.title,
            "started": d.started.isoformat(timespec="seconds") if d.started else "",
            "ended": d.ended.isoformat(timespec="seconds") if d.ended else "",
            "turns": d.turns, "human": d.human, "assistant": d.assistant, "tool_calls": d.tool_calls,
            "top_tools": [list(x) for x in d.top_tools], "opening": d.opening,
            "last_human": d.last_human, "last_assistant": d.last_assistant}
    text = (f"{s.title}\n{d.span_label()}｜{d.volume_label()}\n"
            f"最後你說：{d.last_human[:200]}\n它回：{d.last_assistant[:300]}")
    if a.turns:
        # 最近 N 輪：給人在命令列看、也給 agent 當「這個對話做到哪」的脈絡
        picked = t.turns[-a.turns:]
        data["last_turns"] = [_turn_dict(x) for x in picked]
        blocks = []
        for x in picked:
            when = x.start.strftime("%m-%d %H:%M") if x.start else "時間不明"
            lines = [f"── 第 {x.index + 1} 輪｜{when}"]
            if x.prompt:
                lines.append(f"你：{x.prompt.text}")
            if x.middle:
                lines.append(f"（中間過程：{x.middle_label()}）")
            lines.append(f"它：{x.reply.text}" if x.reply else "（這一輪還沒有回覆）")
            blocks.append("\n".join(lines))
        text += "\n\n" + "\n\n".join(blocks)
    return EXIT_OK, data, text


def cmd_check(a):
    s = find_session(a.session)
    problems = preflight(s)
    holder = sess.session_holder(s.session_id)
    if holder:
        problems.append(f"{sess.describe_holder(holder)}（送出時要關掉、用 release 釋放，或等它自動補送）")
    data = {"session_id": s.session_id, "ok": not problems, "problems": problems, "busy": bool(holder),
            "holder": sess.describe_holder(holder) if holder else "",
            "holder_state": sess.holder_state(holder) if holder else ""}
    code = EXIT_OK if not problems else (EXIT_WAIT if holder and len(problems) == 1 else EXIT_FAILED)
    return code, data, "可以送出" if not problems else "\n".join(problems)


def cmd_release(a):
    """把佔用這個對話的 Claude Code 行程關掉，不送任何東西。用完了、想讓之後的接續接得上，就用這個。"""
    s = find_session(a.session)
    holder = sess.session_holder(s.session_id)
    if holder is None:
        return EXIT_OK, {"session_id": s.session_id, "was_busy": False, "released": True,
                         "message": "目前沒有被佔用"}, "目前沒有被佔用，不用釋放"
    described = sess.describe_holder(holder)
    ok, message = sess.release_session(s.session_id)
    data = {"session_id": s.session_id, "was_busy": True, "released": ok, "message": message, "holder": described}
    return (EXIT_OK if ok else EXIT_FAILED), data, f"{described}\n{message}"


def _common_job(a, kind: str, cwd: str, session_id: str) -> store.Job:
    if a.permission_mode and a.permission_mode not in MODES:
        raise CliError(EXIT_BAD, f"permission-mode 只能是 {'/'.join(MODES)}")
    return store.Job(id=store.new_id(), kind=kind, cwd=cwd, prompt=a.prompt, session_id=session_id,
                     permission_mode=a.permission_mode, allowed_tools=a.allowed_tools or "",
                     disallowed_tools=a.disallowed_tools or "", max_budget_usd=a.max_budget_usd or 0.0)


def cmd_resume(a):
    s = find_session(a.session)
    if a.take_over and a.fork:
        raise CliError(EXIT_BAD, "--take-over 跟 --fork 只能選一個（一個是關掉持有者，一個是不搶、開新 id）")
    job = _common_job(a, "resume", s.cwd, s.session_id)
    job.require_clean = not a.no_require_clean
    job.busy_policy = store.BUSY_TAKEOVER if a.take_over else (store.BUSY_FORK if a.fork else store.BUSY_WAIT)
    at = parse_at(a.at)
    job.at = at.isoformat(timespec="seconds") if at else ""
    code, xml = schedule_or_fire(job, at, a.dry_run)
    job = refresh(store.get(job.id))
    data = job_view(job)
    if xml and a.dry_run:
        data["task_xml"] = xml
    return code, data, _job_line(job)


def cmd_new(a):
    cwd = os.path.abspath(a.cwd)
    if not Path(cwd).is_dir():
        raise CliError(EXIT_BAD, f"資料夾不存在：{cwd}")
    job = _common_job(a, "new", cwd, str(uuid.uuid4()))
    job.sandbox, job.name = a.sandbox, a.name or ""
    at = parse_at(a.at)
    job.at = at.isoformat(timespec="seconds") if at else ""
    code, xml = schedule_or_fire(job, at, a.dry_run)
    job = refresh(store.get(job.id))
    data = job_view(job)
    if xml and a.dry_run:
        data["task_xml"] = xml
    return code, data, _job_line(job)


def _job_line(job: store.Job) -> str:
    when = f" 排在 {job.at}" if job.at and job.status == store.SCHEDULED else ""
    extra = f"｜{job.error}" if job.error else (f"｜{job.result[:120]}" if job.result else "")
    return f"[{job.id}] {job.kind} {Path(job.cwd).name}：{job.status}{when}{extra}"


def cmd_jobs(a):
    rows = [job_view(refresh(j)) for j in store.all_jobs()[: a.limit]]
    return EXIT_OK, {"jobs": rows}, "\n".join(_job_line(store.get(r["id"])) for r in rows) or "（沒有排程）"


def cmd_job(a):
    job = store.get(a.id)
    if job is None:
        raise CliError(EXIT_NOT_FOUND, f"沒有這筆：{a.id}")
    job = refresh(job)
    data = job_view(job)
    text = _job_line(job) + (f"\n改動：\n{data['changes']}" if data["changes"] else "")
    code = {store.DONE: EXIT_OK, store.FAILED: EXIT_FAILED, store.WAITING: EXIT_WAIT}.get(job.status, EXIT_OK)
    return code, data, text


def cmd_cancel(a):
    targets = [j for j in store.all_jobs() if j.status not in store.FINAL] if a.all else [store.get(a.id)]
    if not a.all and targets[0] is None:
        raise CliError(EXIT_NOT_FOUND, f"沒有這筆：{a.id}")
    done = []
    for job in targets:
        if job.status in store.FINAL:
            continue
        if not _no_schtasks():
            scheduler.delete_once(job.id)
        if job.status == store.SENT and store.alive(job):
            subprocess.run(["taskkill", "/PID", str(job.pid), "/T", "/F"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        job.status = store.CANCELLED
        job.finished_at = datetime.now().isoformat(timespec="seconds")
        store.update(job)
        done.append(job.id)
    return EXIT_OK, {"cancelled": done}, f"取消了 {len(done)} 筆" + (f"：{', '.join(done)}" if done else "")


def cmd_fire(a):
    job = store.get(a.id)
    if job is None:
        raise CliError(EXIT_NOT_FOUND, f"沒有這筆：{a.id}")
    code = fire(job)
    job = store.get(a.id)
    return code, job_view(job), _job_line(job)


def cmd_stash(a):
    job = store.get(a.id)
    if job is None:
        raise CliError(EXIT_NOT_FOUND, f"沒有這筆：{a.id}")
    if not job.run_cwd or not _is_repo(job.run_cwd):
        raise CliError(EXIT_FAILED, "這筆不是在 git repo 裡跑的，沒有東西可以收。")
    if job.branch:
        return EXIT_OK, {"branch": job.branch, "worktree": job.run_cwd}, (
            f"這筆在獨立的 worktree 跑（branch {job.branch}），你的工作目錄沒有被動到；不要的話刪掉那個 branch 就好。")
    code, text = _git(job.run_cwd, "stash", "push", "-u", "-m", f"resume-{job.id}")
    if code != 0:
        raise CliError(EXIT_FAILED, f"git stash 失敗：{text}")
    return EXIT_OK, {"stashed": True, "message": text}, f"收起來了（git stash list 找得到 resume-{job.id}）：{text}"


# ── argparse ──────────────────────────────────────────────────────────

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m claude_timer.cli",
                                description="ClaudeResumer 命令列版：接續舊對話、在專案開新對話、排時間（docs/cli.md）")
    p.add_argument("--version", action="version", version=f"claude_timer {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_text):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--json", action="store_true", help="輸出 JSON（給程式讀）")
        sp.set_defaults(fn=fn)
        return sp

    sp = add("sessions", cmd_sessions, "本機的對話清單")
    sp.add_argument("--cwd", default="")
    sp.add_argument("--limit", type=int, default=sess.DEFAULT_LIMIT)
    sp.add_argument("--all", action="store_true", help="連暫存資料夾、程式自己工作區裡的對話也列（預設藏起來）")
    sp = add("show", cmd_show, "一個對話的摘要")
    sp.add_argument("session")
    sp.add_argument("--turns", type=int, default=0, help="另外印出最近 N 輪（你說什麼、它最後回什麼）")
    add("check", cmd_check, "送出前檢查").add_argument("session")

    def task_flags(sp):
        sp.add_argument("--prompt", required=True)
        sp.add_argument("--at", default="", help="+30、09:00、2026-10-04 09:00；不給就現在送")
        sp.add_argument("--permission-mode", default="acceptEdits")
        sp.add_argument("--allowed-tools", default="")
        sp.add_argument("--disallowed-tools", default="")
        sp.add_argument("--max-budget-usd", type=float, default=0.0)
        sp.add_argument("--dry-run", action="store_true", help="排時間時只產生工作排程的 XML，不真的註冊")

    sp = add("resume", cmd_resume, "接續舊對話")
    sp.add_argument("session")
    task_flags(sp)
    sp.add_argument("--no-require-clean", action="store_true", help="工作目錄有沒 commit 的改動也照送")
    sp.add_argument("--take-over", action="store_true",
                    help="到點時對話若正被別的 Claude Code 開著（例如 VSCode），關掉它再送（破壞性；預設是等它關掉）")
    sp.add_argument("--fork", action="store_true",
                    help="不搶佔：用 --fork-session 續在一個新的對話 id 底下，原本的可以留著開（未實測）")
    add("release", cmd_release, "把佔用這個對話的 Claude Code 關掉，不送出").add_argument("session")
    sp = add("new", cmd_new, "在專案開一個全新的對話")
    sp.add_argument("--cwd", required=True)
    sp.add_argument("--name", default="")
    sp.add_argument("--sandbox", choices=("worktree", "none"), default="worktree")
    task_flags(sp)
    add("jobs", cmd_jobs, "看排程").add_argument("--limit", type=int, default=50)
    add("job", cmd_job, "看一筆的結果").add_argument("id")
    sp = add("cancel", cmd_cancel, "取消")
    sp.add_argument("id", nargs="?", default="")
    sp.add_argument("--all", action="store_true")
    add("fire", cmd_fire, "（工作排程器用）送出一筆").add_argument("id")
    add("stash", cmd_stash, "把那筆接續做的改動收起來").add_argument("id")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    parser = _parser()
    try:
        a = parser.parse_args(argv)
    except SystemExit as e:
        return EXIT_BAD if e.code not in (0, None) else EXIT_OK
    if a.cmd == "cancel" and not a.all and not a.id:
        parser.error("cancel 要給 <id> 或 --all")
    try:
        code, data, text = a.fn(a)
    except CliError as e:
        code, data, text = e.code, {"error": e.message}, f"❌ {e.message}"
    if a.json:
        print(json.dumps(data, ensure_ascii=False, default=str))
    else:
        print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
