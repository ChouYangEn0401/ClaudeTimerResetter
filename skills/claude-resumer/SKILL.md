---
name: claude-resumer
description: Schedule Claude Code work on this Windows machine — resume a stopped conversation (e.g. after the usage limit resets) or start a brand-new conversation in a project, now or at a set time, and read back the result. Use when someone asks to "continue that session at 6am", "run this task in project X tonight", or to check what a scheduled run did.
---

# claude-resumer

A command-line tool in the ClaudeTimerResetter repo. It runs `claude --print` for you, either now or at a time
(through a one-shot Windows scheduled task, so nothing has to stay open), and keeps a record you can query.

Run it as `<ClaudeTimerResetter>\scripts\ctr.bat <subcommand> --json` (or `.venv\Scripts\python -m claude_timer.cli`).
Always pass `--json` when a program reads the output. From Python on this machine, `from claude_timer import api`
gives the same calls as functions returning `Result(code, data, text)` (see `docs/api.md` for which button maps to which call).

## When to use which

| Goal | Command |
|------|---------|
| Find the conversation to continue | `sessions --cwd <project folder>` (newest first; `busy: true` means it is open right now; `label`/`branch`/`source` tell same-named folders, worktrees and `-p` sessions apart; without `--cwd`, temp and tool-owned folders are hidden unless `--all`) |
| See what a conversation was doing | `show <session_id> [--turns 3]` (`last_turns[]`: the person's prompt, a one-line summary of the tool work, the final reply of each turn) |
| Continue a conversation (now or later) | `resume <session_id> --prompt "continue" [--at 06:00]` |
| …and make sure it sends even if the conversation is still open at that time | add `--take-over` (kills the holder at fire time — destructive, so the caller must have asked the person) |
| …without taking the open conversation away | add `--fork` (`--fork-session`: continues under a new session id; **untested** whether it bypasses the lock) |
| Free a conversation that is held open but no longer used | `release <session_id>` (kills the holding Claude Code process, sends nothing) |
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
- **Permissions.** `resume` defaults to `--permission-mode inherit`: the mode, model and effort the conversation last
  used interactively (read from its transcript; a previous take-over's `sdk-cli` turns do not count;
  `bypassPermissions` is never inherited -- it becomes `auto`). `new` defaults to `acceptEdits`. Use `plan` for
  read-only thinking. Avoid `bypassPermissions` unless the person explicitly asked for it. `--allowed-tools`,
  `--disallowed-tools`, `--max-budget-usd` are passed straight to `claude`.
- **Judge by the result, not the exit code.** A finished job whose `error` says "被權限擋了 N 種" ran but was blocked;
  tell the person what was blocked instead of reporting success.
- **A conversation can only have one owner.** If it is open in VSCode when the time comes, the job waits (exit 3)
  and sends as soon as it is closed. `check <session_id>` tells you who holds it and for how long it has been
  idle (`holder_state`). `--take-over` and `release` end that holder's process tree: anything unsaved in that
  conversation is lost, so only use them when the person asked for exactly that. A scheduled take-over never kills a
  holder that is busy (it retries until it is idle) or one that is a program (`claude -p`, e.g. a PipelineAgent
  node); `release` kills a busy one only with `--even-busy`.
- This tool does not decide *who* may schedule work. Callers that act for a remote user (for example ClawBot over
  Telegram) must check that themselves before calling it.
