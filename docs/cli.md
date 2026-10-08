# ClaudeResumer 命令列版（CLI）

ClaudeResumer 的視窗版要「保持開著才會準時送」；命令列版把排程交給 Windows 工作排程器，**任何程式都能叫**：
你在 cmd 裡、ClawBot（Telegram／本機畫面）、別的專案的 agent 都用同一套指令。給 agent 讀的精簡版在
[skills/claude-resumer/SKILL.md](../skills/claude-resumer/SKILL.md)；Python 程式直接 `from claude_timer import api`，
每一顆按鈕對到哪個函式見 [api.md](api.md)。

```bat
scripts\ctr.bat sessions --cwd C:\path\to\project       :: 這個專案的對話清單
scripts\ctr.bat resume <session> --prompt continue --at 06:00
scripts\ctr.bat new --cwd C:\path\to\project --prompt "加一個關於頁" --at +30
scripts\ctr.bat job <id>                                 :: 做完了沒、它最後說了什麼、花多少、改了什麼
```

`scripts\ctr.bat` 等於 `.venv\Scripts\python -m claude_timer.cli`。每個子指令都可以加 `--json`。

## 子指令

| 子指令 | 做什麼 | 花錢嗎 |
|--------|--------|--------|
| `sessions [--cwd 資料夾] [--limit N] [--all]` | 本機的對話（標題、最後一句、最後活動、分支、來源、是不是正開著）。預設藏起暫存資料夾與程式自己工作區裡的對話，`--all` 才列；指定 `--cwd` 時不過濾 | 不花 |
| `show <session> [--turns N]` | 一個對話的摘要：期間、幾輪、則數、最後你說了什麼、它回了什麼；`--turns N` 多印最近 N 輪（你說的整段、中間過程一行摘要、它最後回的整段） | 不花 |
| `check <session>` | 送出前檢查：執行檔、對話檔、資料夾、有沒有被佔用（佔用的那個閒置多久） | 不花 |
| `release <session>` | 把佔用這個對話的 Claude Code 行程關掉，**不送出**（破壞性：那個對話沒存的東西會沒掉） | 不花 |
| `resume <session> --prompt … [--at 時間] [--take-over \| --fork]` | 接續舊對話；沒給 `--at` 就現在送。`--take-over`＝到點若被佔用就關掉持有者再送；`--fork`＝不搶，用 `--fork-session` 續在新的對話 id 底下（未實測） | 花 |
| `new --cwd 資料夾 --prompt … [--at 時間]` | 在專案開一個**全新的對話**做新任務（新的 session id 先決定好，之後可以再 resume 它） | 花 |
| `jobs` / `job <id>` | 排程清單／一筆的結果（含 `git diff --stat` 的改動摘要） | 不花 |
| `cancel <id>` / `cancel --all` | 取消（已經送出、還在跑的會連同子行程一起收掉） | — |
| `stash <id>` | 把那筆接續做的改動收起來（`git stash -u`，名字是 `resume-<id>`，找得回來） | — |
| `fire <id>` | 工作排程器到點呼叫的；平常不用自己打 | 花 |

時間（`--at`）：`+30`（30 分鐘後）、`09:00`（今天，過了就明天）、`2026-10-04 09:00`。

共用選項：`--permission-mode`（預設 `acceptEdits`；`plan`／`bypassPermissions`／`dontAsk`／`auto`／`default`）、
`--allowed-tools`、`--disallowed-tools`、`--max-budget-usd`（都原樣交給 claude）、`--dry-run`（排時間時只產生
工作排程的 XML，不真的註冊）。`resume` 另有 `--no-require-clean`；`new` 另有 `--sandbox worktree|none`、`--name`。

## 結束碼

| 碼 | 意思 |
|----|------|
| 0 | 成功（排好了／送出了／做完了） |
| 1 | 失敗（原因在 `error`） |
| 2 | 找不到（對話、job） |
| 3 | 等待中（對話正被別的 Claude Code 開著；排程會自動補試） |
| 4 | 參數不對 |

## 一筆 job 的一生

`scheduled` →（到點）→ `waiting`（被佔用，每 2 分鐘補試、最多 1 小時）→ `sent`（claude 在背景跑）→ `done`／`failed`；
任何時候都可以 `cancelled`。資料在 `%LOCALAPPDATA%\ClaudeTimerResetter\resume_jobs.json`，每筆的 claude 輸出在
`job-<id>.log`（最後是一個結果 JSON）。

排程不靠任何常駐程式：每筆有 `--at` 的 job 在工作排程器有一個一次性工作 `ClaudeResumer-<id>`，到點跑
`fire <id>`；送出成功就刪掉那個工作。電腦那時是關的，開機後會補跑（StartWhenAvailable）。

## 「跑的是什麼」的限制

誰能下令是呼叫的那一方決定的（例如 ClawBot 要驗證碼、有風險分級）；這裡管的是**跑起來能碰到什麼**：

- **內容釘死**：建立時把種類、對話、資料夾、要說的話、權限模式、工具限制、預算、隔離方式、被佔用時怎麼辦
  算成 `content_hash`；`fire` 時再算一次，不一樣就不跑（排程檔被改過的任務不會被當成原本那一筆執行）。
- **被佔用時預設只等不搶**：`--take-over` 是建立時就講好的，到點才關掉持有者（釋放失敗退回 `waiting` 讓排程再試）；
  `release` 是現在就關、不送。兩者都是破壞性的，呼叫端（例如 ClawBot）要自己決定給不給。
- **`new` 預設在獨立的 git worktree 跑**：`%LOCALAPPDATA%\ClaudeTimerResetter\worktrees\<id>`，branch `resume/<id>`，
  你的工作目錄完全不會被動到；看過 diff 再決定要不要 merge。不是 git repo 就要明講 `--sandbox none`。
- **`resume` 預設要求工作目錄乾淨**：Claude Code 的對話綁資料夾，只能在原本的地方跑，所以先確定沒有你還沒
  commit 的改動、記下起點 commit；做完 `job <id>` 看得到改了什麼，不要的話 `stash <id>` 收起來。
- **工具限制、預算**：`--allowed-tools`／`--disallowed-tools`／`--max-budget-usd` 原樣交給 claude。

## 鐵律（跟視窗版一樣）

送出的那一刻，那個對話不能開著（被 VSCode 開著時 `--resume` 會秒退）。CLI 會零成本偵測：被佔用就標
`waiting`、不白送，排程會自己補試。偵測看 `~/.claude/sessions/<pid>.json`，而且要跟行程建立時間對得上
（pid 會被重用，殘留檔不算）。

## 給別的程式接的時候

- 一律 `--json`，讀 stdout 最後一行；結束碼照上表。
- `sessions` 的 `project` 欄位是資料夾名（不變）；要給人看用 `label`（同名時帶上一層資料夾）、`branch`、`source`。
- 要讓 agent 知道「這個對話做到哪」：`show <session> --turns 3`，讀 `last_turns[]` 的 `prompt`／`reply`。
- 要幫人清掉佔用：`release <session>`；要排一筆「到點一定要送」：`resume … --take-over`。
  這兩個都會關掉別人正在用的對話，由呼叫端決定要不要驗證碼。
