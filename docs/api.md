# ClaudeResumer 的 API：每一顆按鈕在哪裡

> 本人（2026-10-08）：畫面上每一個按鈕某種程度上都是一個功能，應該抽成可以被呼叫的介面，
> 讓 agent 在需要的時刻決定用哪一個去做搶佔或 continue；先把 API 開出來、確認函式在哪裡。

同一件事有三種叫法，走的是同一條程式：

| 叫法 | 怎麼叫 | 給誰 |
|---|---|---|
| 視窗 | `ClaudeResumer.exe`（或 `python resumer.py`） | 你坐在電腦前 |
| CLI | `scripts\ctr.bat <子指令> --json`（＝`python -m claude_timer.cli`） | ClawBot、排程、任何語言的程式 |
| Python | `from claude_timer import api` | 同一台電腦上的 Python 程式（例如之後的 PipelineAgent 整合） |

CLI 與 Python 都經過 `claude_timer/cli.py` 的 `run()`：同一組參數、同一個結果。
視窗版的佇列只活在視窗裡（視窗就是那個計時器）；CLI／Python 排的工作交給 Windows 工作排程器，視窗關著也會跑。

## 回傳值（CLI 與 Python 一樣）

| 結束碼 | `Result` | 意思 |
|---|---|---|
| 0 | `r.ok` | 成功（排好了／送出了／做完了） |
| 1 | — | 失敗，原因在 `r.error`（CLI 的 `error`） |
| 2 | — | 找不到（對話、工作） |
| 3 | `r.waiting` | 等待中：對話正被別的 Claude Code 開著，排程會自己補試 |
| 4 | — | 參數不對 |

`r.data` 就是 `--json` 印出來的那個物件，欄位見 [cli.md](cli.md)。Python 版**不丟例外**。

## 按鈕 → 函式 → CLI → 實作在哪

### ① 選哪一個對話

| 視窗上的 | Python | CLI | 實作 |
|---|---|---|---|
| 對話清單、「重新整理」「載入更多」 | `api.sessions(cwd=None, limit=200)` | `sessions [--cwd 資料夾] [--limit N]` | `resumer/sessions.py` `scan_sessions()`；視窗 `App._reload_sessions` |
| 「藏起暫存與程式用的」（勾掉＝全部列） | `api.sessions(include_hidden=True)` | `sessions --all` | `SessionInfo.hidden_by_default`、`classify_origin()` |
| 「查看內容」（雙擊） | `api.show(session_id, turns=3)` | `show <id> [--turns N]` | `resumer/transcript.py` `read()`／`Turn`；視窗 `viewer.ConversationViewer` |
| 「檢查可否送出」 | `api.check(session_id)` | `check <id>` | `tasks.preflight()` ＋ `sessions.session_holder()`；視窗 `App._diagnose` |
| 「釋放佔用（不送出）」 ⚠ | `api.release(session_id)` | `release <id>` | `sessions.release_session()`；視窗 `App._release_selected` |

### ② 時間到要送出什麼

| 視窗上的 | Python | CLI | 實作 |
|---|---|---|---|
| 「接著送出的內容」 | `prompt=` | `--prompt` | — |
| 「附加檔案…」 | 在 prompt 裡加 `@"路徑"` | 同左 | 視窗 `App._attach_file` |
| 「送出時間」 | `at="06:00"`／`"+30"`／`"2026-10-04 09:00"` | `--at` | `cli.parse_at()`；視窗 `App._parse_time` |
| 「被佔用時：等它關掉再補送」 | 預設 | 預設 | `jobs.BUSY_WAIT` |
| 「被佔用時：時間到就強制接管」 ⚠ | `take_over=True` | `--take-over` | `jobs.BUSY_TAKEOVER`；`cli.fire()`、`Task.fire()` 到點才 `release_session()` |
| 「被佔用時：分支接續（未實測）」 | `fork=True` | `--fork` | `launch.build_argv(fork_session=True)`＝`claude --resume <id> --fork-session` |
| 「權限模式」 | `permission_mode=` | `--permission-mode` | `launch.build_argv()` |
| 「加入排程佇列」 | `api.resume(id, prompt, at=…)` | `resume <id> --prompt … --at …` | CLI：`jobs.Job`＋`core/scheduler.install_once()`（一次性工作 `ClaudeResumer-<id>`）；視窗：`tasks.Task`（佇列在視窗裡） |

### ③ 排程佇列

| 視窗上的 | Python | CLI | 實作 |
|---|---|---|---|
| 「移除選取」 | `api.cancel(job_id)`／`api.cancel(all_jobs=True)` | `cancel <id>`／`cancel --all` | `cli.cmd_cancel`；視窗 `App._remove_task` |
| 「立即送出」 | `api.resume(id, prompt)`（不給 at）或 `api.fire(job_id)` | `resume …`（不給 `--at`）／`fire <id>` | `cli.fire()`；視窗 `Task.fire()`；兩邊都經 `launch.start_detached()` |
| 「改成『到時接管』」 | 建立時就給 `take_over=True`（CLI 的工作內容是釘死的，要改就 cancel 再排一次） | 同左 | 視窗 `App._mark_take_over_selected` |
| 「立刻接管並送出」 ⚠ | `api.release(id)` 再 `api.resume(id, prompt)` | `release <id>` 再 `resume …` | 視窗 `App._force_take_over_selected` |
| 「全部送出後自動關閉視窗」 | （只有視窗有） | — | — |

### 視窗沒有、CLI／Python 才有的

| 做什麼 | Python | CLI |
|---|---|---|
| 在專案開一個**全新的對話**做新任務（預設在獨立的 git worktree＋branch） | `api.new(cwd, prompt, at=None)` | `new --cwd … --prompt …` |
| 工作清單／一筆的結果（它最後說了什麼、花多少、改了什麼） | `api.jobs()`／`api.job(id)` | `jobs`／`job <id>` |
| 把一筆接續做的改動收起來 | `api.stash(id)` | `stash <id>` |

⚠＝會關掉一個正在用的 Claude Code 行程，那個對話沒存或正在跑的東西會一起沒掉。**呼叫端自己決定要不要問人**：
這裡不管誰能下令（ClawBot 用驗證碼與風險分級把關；agent 要用之前應該先問人）。

## 另一支工具（resetter）

| 做什麼 | 叫法 | 實作 |
|---|---|---|
| 現在用掉多少額度、幾點重置（不花錢） | `scripts\usage.bat [--json]` | `core/usage.py` |
| 該刷新就打一次最便宜的探針 | `scripts\test.bat` | `core/ping.py` |
| 排程規則、安裝／解除 | `ClaudeTimerResetter.exe` | `resetter/service.py`、`core/rules.py`、`core/scheduler.py` |

## 還沒驗證的

- **分支接續**（`fork=True`／`--fork`）：`claude` 2.1.293 有 `--fork-session`，但它能不能在原本的對話還開著時接續，
  沒有在真的 claude 上試過——試的時候要拿一個不重要的對話，不要拿正在做事的。
