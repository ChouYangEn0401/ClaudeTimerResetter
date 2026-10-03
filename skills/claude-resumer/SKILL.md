---
name: claude-resumer
description: Schedule Claude Code work on this Windows machine — resume a stopped conversation (e.g. after the usage limit resets) or start a brand-new conversation in a project, now or at a set time, and read back the result. Use when someone asks to "continue that session at 6am", "run this task in project X tonight", or to check what a scheduled run did.
---

# claude-resumer

A command-line tool in the ClaudeTimerResetter repo. It runs `claude --print` for you, either now or at a time
(through a one-shot Windows scheduled task, so nothing has to stay open), and keeps a record you can query.

Run it as `<ClaudeTimerResetter>\scripts\ctr.bat <subcommand> --json` (or `.venv\Scripts\python -m claude_timer.cli`).
Always pass `--json` when a program reads the output.

## When to use which

| Goal | Command |
|------|---------|
| Find the conversation to continue | `sessions --cwd <project folder>` (newest first; `busy: true` means it is open right now) |
| See what a conversation was doing | `show <session_id>` |
| Continue a conversation (now or later) | `resume <session_id> --prompt "continue" [--at 06:00]` |
| Start a fresh conversation for a new task | `new --cwd <project folder> --prompt "<task>" [--at +30]` |
| Did it finish? What did it say / change / cost? | `job <id>` |
| Undo what a resume changed | `stash <id>` |
| Stop something | `cancel <id>` or `cancel --all` |

`--at` accepts `+30` (minutes from now), `09:00` (today, or tomorrow if already past), `2026-10-04 09:00`.

## Exit codes

0 ok · 1 failed (see `error`) · 2 not found · 3 waiting (the conversation is open elsewhere; it retries every
2 minutes for an hour) · 4 bad arguments.

## Things that matter

- **Never runs where you are working without asking.** `new` runs in its own git worktree (branch `resume/<id>`)
  by default; `resume` refuses a dirty working tree unless `--no-require-clean`. Use `job <id>` to see `changes`.
- **The request is pinned.** What to run is hashed when the job is created; a job whose stored content was edited
  later is refused at fire time.
- **Permissions.** Default `--permission-mode acceptEdits`. Use `plan` for read-only thinking. Avoid
  `bypassPermissions` unless the person explicitly asked for it. `--allowed-tools`, `--disallowed-tools`,
  `--max-budget-usd` are passed straight to `claude`.
- **A conversation can only have one owner.** If it is open in VSCode when the time comes, the job waits (exit 3)
  and sends as soon as it is closed.
- This tool does not decide *who* may schedule work. Callers that act for a remote user (for example ClawBot over
  Telegram) must check that themselves before calling it.
