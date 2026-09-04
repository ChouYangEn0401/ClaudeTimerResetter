# ClaudeTimerResetter — Claude 可用性極簡測試

一支「現在 Claude 能不能用？」的最便宜探針。直接用
[ClaudeLogin 的 release wheel](https://github.com/ChouYangEn0401/ClaudeLogin/releases/tag/v0.2.0)，
不需要 clone 原始碼。

## 用法

```bat
setup.bat       :: 只需跑一次：建 .venv + 從 GitHub Release 裝 whl
test.bat        :: 該刷新就刷新，不該刷新就不花錢（見下面「什麼時候才真的打」）
usage.bat       :: 只看額度用量跟重置時間，一定不花錢
test.bat --json :: 同上，機器讀的輸出（usage.bat 也吃 --json）

test.bat --force        :: 不看用量，一定打（等於舊版無條件行為）
test.bat --no-wait      :: 該等的時候不原地等，直接結束（排程用）
test.bat --max-wait 300 :: 最多原地等 300 秒（預設 920）
```

兩支 `.bat` 發現沒有 `.venv` 都會自己叫 `setup.bat`，所以第一次直接跑也行。

## 這個專案有四個進入點

| 要做什麼 | 在原始碼裡跑（VSCode / 命令列） | 用打包好的 exe |
|---|---|---|
| 看現在用了多少、幾點重置 | `usage.bat` | （沒有 exe，用 .bat） |
| 該刷新就刷新一次 | `test.bat` | （沒有 exe，用 .bat） |
| 設定背景排程、反覆自動刷新 | `.venv\Scripts\python installer.py` | 雙擊 `ClaudeTimerResetter.exe` |
| 排定「幾點幫我把某個對話接下去」 | `.venv\Scripts\python resumer.py` | 雙擊 `ClaudeResumer.exe` |

兩種跑法功能完全一樣——exe 只是把 Python 跟相依包進去，讓沒裝 Python 的電腦也能用。
自己開發的時候用原始碼跑比較快（不用每次重建 exe）。

### 在 VSCode / 命令列裡跑（開發時）

```bat
setup.bat                                  :: 第一次先跑這個
.venv\Scripts\python installer.py          :: 開排程主控台 GUI
.venv\Scripts\python resumer.py            :: 開對話接續排程器 GUI
```

`installer.py` 靠參數分流成三個模式，平常只會用到第一個：

| 指令 | 做什麼 | 誰會叫它 |
|---|---|---|
| `python installer.py` | 開 GUI 主控台：編規則、選交付方式、安裝/解除安裝 | 你 |
| `python installer.py --tick` | 靜默跑一次「有沒有規則到期」，到期才刷新 | Windows 工作排程器，每分鐘一次 |
| `python installer.py --tray` | 工具列常駐模式 | 開機自動啟動的捷徑 |

`--tick` 跟 `--tray` 是給機器叫的，手動跑只會用來除錯。兩個都不會印東西到螢幕，
結果寫在 `%LOCALAPPDATA%\ClaudeTimerResetter\` 底下的 log。

`resumer.py` 沒有參數，就是開 GUI。

**VSCode 提醒**：這兩支都是 Tkinter GUI，要在整合終端機裡用 `.venv` 的 python 跑
（`Ctrl+Shift+P` → `Python: Select Interpreter` 選 `.venv`，或直接打完整路徑）。
用系統 python 跑會說找不到 `claude_subscription`。

### 用 exe 跑（給別台電腦，或不想開終端機時）

```bat
build.bat            :: 建置，輸出 dist\ClaudeTimerResetter.exe 跟 dist\ClaudeResumer.exe
```

兩支 exe 都是獨立的單一檔案，複製到隨便哪台 Windows 都能雙擊執行，**不需要裝 Python**。
唯一的前提是那台電腦要先裝好官方 Claude Code CLI 並登入過。

- `ClaudeTimerResetter.exe`：雙擊開主控台。裝好排程之後它會把自己複製到
  `%LOCALAPPDATA%\ClaudeTimerResetter\`，之後背景跑的是那一份，原始下載的那份可以刪。
- `ClaudeResumer.exe`：雙擊開排程器，用完自己關掉，不常駐、不需要安裝。

## 什麼時候才真的打

`ping.py` 不是無腦打一次 API。它先花 0 元查一次用量（走 `usage.py`），再決定要不要
花那 $0.00025：

| 5 小時視窗的狀態 | 做什麼 | 為什麼 |
|---|---|---|
| `resets_at` 是 `null`（還沒開始計時） | **打** | 打下去重置時鐘才會開始跑，這正是這支工具的目的 |
| 再 15 分鐘內就要重置 | **原地等它重置，時間到再確認一次才打** | 現在打是打在舊視窗上，等一下就白花了 |
| 已經在跑而且還早 | **不打** | 重置時鐘早就啟動了，再打一次不會讓它提早結束 |
| 用量查不到（沒登入、斷網） | **打** | 寧可多花 $0.00025，也不要漏掉一次該做的刷新 |

輸出會直接講它做了什麼：

```
[SKIP] 已經刷新過了，這次不打、不花錢
     原因       : 5 小時視窗還在跑（剩 4h45m），重置時鐘早就啟動了
     下次重置   : 2026-08-28T17:50:00+08:00｜剩 4h45m
```

有刷新的話才會印完整資訊（模型、回覆、花費、耗時，加上兩個視窗的用量與重置時間）。

### 排程為什麼不是用 sleep 等

Task Scheduler 那個工作有 `ExecutionTimeLimit` PT5M（見 `scheduler.py`），單次 tick
卡在 sleep 裡等 15 分鐘會被系統砍掉。所以 `installer.py --tick` 跟 tray 都用
`ping.refresh(max_wait_seconds=0)`：ping 回報 `waiting` 時，呼叫端用
`rules.rollback()` 把這條規則的觸發還原，讓下一分鐘的 tick 重新判定一次。

等於用既有的 tick 節奏做「時間到再確認一次」，語意跟原地等一樣，但不會被砍。
手動在命令列跑 `test.bat` 則預設允許原地等（最多 15 分鐘多一點）。

## 為什麼便宜

| 項目 | 設定 | 理由 |
|---|---|---|
| 模型 | `claude-haiku-4-5-20251001` | 目前最便宜的一檔 |
| 工具 | 全關 `tools=""` | 純文字進出；開工具是 agent 模式，貴 15~45 倍 |
| thinking | `MAX_THINKING_TOKENS=0` | 回一個 "k" 不需要模型多想 |
| 權限 | `manual` | 工具全關，這裡只是明確表態不自動放行 |
| session | `persist=False` | 不保存、不重送歷史（重送歷史才是最貴的） |
| 提示 | system `Reply with exactly: k` ＋ prompt `k` | 重點不是提示多短，是回覆多短 |

**實測：$0.000254，約 4 秒。**

### 真正省到錢的那一步：讓它別多話

haiku 的 output 單價是 input 的 5 倍，所以把提示從 `hi` 縮到 `k` 其實幫不上忙，
真正貴的是模型回了一整句寒暄。實測同一台電腦、連續兩次：

| 組合 | input | output | 單次 |
|---|---|---|---|
| 舊：system 一句中文＋prompt `hi` | 263 | 23 | $0.000378 |
| 新：system `Reply with exactly: k`＋prompt `k` | 234 | 4 | $0.000254 |

**省 33%。** 兩件事一起做的：系統提示從中文換英文（同意思的英文 token 比較少），
以及明講「只回 k」把 output 從 23 個 token 壓到 4 個。

## 結束碼

| 碼 | 意思 | 該做什麼 |
|---|---|---|
| 0 | 可用 | — |
| 1 | 呼叫失敗（額度用完 / 逾時 / 模型錯誤） | 可以重試 |
| 2 | 找不到 `claude` 執行檔 | 裝官方 Claude Code |
| 3 | 未登入 / 認證失敗 | 執行 `claude` 登入 |
| 4 | 環境沒裝好 | 跑 `setup.bat` |

排程或腳本可以直接判斷 `%ERRORLEVEL%`，或用 `--json` 讀 `ok` 欄位。

「沒刷新」不是錯誤：`--json` 的 `action` 欄位會是 `refreshed` / `skipped` / `waiting`
三者之一，三種都回結束碼 0。要區分「有沒有真的打」請讀 `action`，不要讀結束碼。

## 額度用量跟重置時間（`usage.py` / `usage.bat`）

這支跟探針是**互補的兩件事**，別搞混：

| | 做什麼 | 花費 |
|---|---|---|
| `ping.py` | **啟動** 5 小時視窗的重置時鐘 | $0.000254 |
| `usage.py` | **讀出** 目前用了幾 %、幾點重置 | $0（不吃 token） |

資料來源是 Claude Code 自己 `/usage` 用的同一支端點 `GET https://api.anthropic.com/api/oauth/usage`，
用本機 `~/.claude/.credentials.json` 裡的訂閱 OAuth token 呼叫。
純 HTTPS GET、不經過模型，所以完全不計費，想查幾次都行。

```
[OK] 額度用量（這次查詢不花錢）
    5 小時視窗 : 已用  71%｜重置 2026-08-28T12:30:00+08:00｜剩 1h52m
    7 天視窗   : 已用  71%｜重置 2026-08-31T10:00:00+08:00｜剩 71h22m
```

### 對計時器最有用的一個欄位：`window_open`

`five_hour.resets_at` 是 `null` 代表這個 5 小時視窗**還沒被任何一次呼叫開啟**。
這對排程很重要：視窗已經在跑的時候再打探針是白花錢，因為重置時鐘早就啟動了。
`usage.py --json` 把這件事直接給成 `window_open` 布林值。

### 限制

- token 過期會回結束碼 3；開一次 Claude Code 它就會自動換新。
- 這不是公開文件化的 API，是 Claude Code 內部用的。欄位名稱可能會變，
  所以 `usage.py` 只挑 `five_hour` / `seven_day` 兩個欄位，其他一律當作不存在。

## 設計理念：為什麼是兩個獨立工具

這個專案後來長出兩個方向不同的小工具，值得記錄一下為什麼會是這樣，免得以後看不懂
「明明都是排程，為什麼要分開」：

- **ClaudeTimerResetter.exe**（`installer.py`，也叫 resetter）——反覆執行同一件便宜的事：
  提前刷新用量視窗的重置時鐘。
- **ClaudeResumer.exe**（`resumer.py`）——一次性的事：真的被限額卡住某個對話時，排定
  「重置時間一到，自動幫我把那個對話接著送出去」，送完就關掉，不常駐。

兩者要解決的問題不一樣，所以刻意做成兩支獨立的 exe，而不是合成一個工具的兩個分頁。

### 時機點的含義不同——這是兩支不能混用的根本原因

兩支都在「捕捉一個時間點」，但那個時間點的**意義完全不一樣**：

| | resetter（installer.py） | resumer（resumer.py） |
|---|---|---|
| 立場 | **主動排重置**：token 已經卡住，我去把重置時鐘催起來 | **已知重置點、準時送**：我已經知道幾點會恢復 |
| 時間點是誰決定的 | 程式**算出來**的（提前量、視窗週期） | **人**判斷的（Claude 告訴你的重置時間） |
| 該不該查用量 | **該**。看用量、算時間、決定打不打，是它的本分 | **不該**。你設的時間就是最終決定，不做二次確認 |
| 時間到做什麼 | 查用量 → 視窗還沒開就打探針、快重置就等一下再打 | 直接 fire 那個對話，就這樣 |

早期版本錯把 resetter 那套「查用量、看有沒有重置、沒重置就壓著等」塞進了 resumer，
結果就是「Claude 說某點重置、resumer 卻自己算出還要 4 小時、於是把你排好的任務壓著
不送」。那是把兩支工具的職責搞混了——現在 resumer 已經拿掉整個用量閘門，時間到就送。

### 為什麼排程規則要能填任意時間點，而不是只有「每隔 N 分鐘」

起因是這樣的使用情境：Claude 的用量視窗有固定的重置週期（例如 5 小時），但重度工作
常常 2~3 小時就把額度用完。如果照平常習慣、坐到位子上才開始用 AI，額度用到一半就會
撞牆，只能乾等視窗重置，正好卡在最需要它的時候。

比較好的做法是反過來：在預期會重度使用「之前」，先用這支探針提前把重置時鐘啟動。
這樣等真正進入重度工作、額度快用完的時間點，剛好又迎來下一輪重置，工作不會被打斷。
所以每一次觸發時間點的意義，其實是「預估到這個時候額度差不多要用完了，該催下一輪
重置」，**而不是**「這個時間點才開始工作」——這是最主要的設計出發點，兩者常常對不上，
尤其實際到工位的時間本身就有彈性，觸發時間需要抓一個提前量，不能卡在表定時間本身。

實務例子（兩個帳號、各自獨立算）：假設表定上班時間 8:00、彈性到 9:00——

- 公司帳號在上班時段重度使用，提早 2.5 小時預熱：第一次觸發 `05:30`，之後每 5 小時
  （視窗長度）一次：`10:30, 15:30, 20:30, ...`
- 個人帳號是工作空檔（例如把公司帳號的 AI 丟去跑、自己趁機遠端到另一台機器用）才用，
  提早 1 小時預熱：第一次觸發 `07:00`，之後同樣每 5 小時一次：`12:00, 17:00, ...`

這種「先算好一串具體時間點，兩個帳號各自一組」的用法，就是 `daily_times` 規則型別
直接支援任意時間點清單（而不是把使用者鎖死在單一「每隔 N 分鐘」模式）的理由；兩個帳號
會在各自的機器上各自安裝一份，排程互不影響。

## 給其他電腦：安裝器（ClaudeTimerResetter.exe）

拿到一台新電腦，不想手動裝 venv、手動去工作排程器設定的話，用這支安裝器。

**前提**：目標電腦要先裝好官方 Claude Code CLI 並登入過（`claude` 指令要能跑），這是
安裝器管不到的——它只能偵測、不能取代這一步。

建置見下面的「怎麼打包」；`dist\ClaudeTimerResetter.exe` 就是要分享出去的檔案，
對方直接拿去雙擊執行即可，不需要另外裝 Python（`claude_subscription` 已經打包進去了）。

### 排程規則（可以自由組合，不限制單一模式）

雙擊 exe 開主控台，「新增規則」可以加以下四種：

| 型別 | 例子 | 對應輸入 |
|---|---|---|
| 固定間隔 | 每隔 480 分鐘執行一次 | 每隔幾分鐘 = `480` |
| 每天固定時間點 | 每天 5, 10, 15, 20 點執行 | 時間 = `05:00, 10:00, 15:00, 20:00` |
| 起始後重複 N 次 | 6 點後每 15 分鐘一次，重複 5 次 | 起始 `06:00`、每隔 `15`、停止條件「count」、上限 `5` |
| 起始後直到成功 | 6 點後每 15 分鐘重試，直到成功為止 | 起始 `06:00`、每隔 `15`、停止條件「until_success」、上限 `50`（防呆用，避免萬一一直失敗整天無限重打） |

規則可以同時存在多條，各自獨立判斷、互不影響。

### 兩種交付方式，二選一

- **工作排程器**：註冊進 Windows Task Scheduler 背景執行，不用保持視窗開著，不需要
  系統管理員權限。電腦睡眠或剛開機時錯過的規則會盡快補跑一次（`StartWhenAvailable`）。
- **工具列常駐**：程式留在系統工具列跑（可勾選「開機時自動啟動」）。想看目前狀態、
  立即測試、或結束程式都在工具列圖示的右鍵選單。

兩種模式互斥：切換其中一種，安裝器會自動把另一種的殘留（工作排程項目 / 開機啟動捷徑 /
常駐行程）清乾淨，不會兩邊同時打。

### 解除安裝

開同一支 `ClaudeTimerResetter.exe`，點「解除安裝」。會把工作排程項目、開機自動啟動
捷徑、常駐行程、`%LOCALAPPDATA%\ClaudeTimerResetter` 資料夾（含複製過去的 exe 本體、
規則設定、執行紀錄）全部清掉。清完之後，把你當初下載/建置的那份 exe 刪掉即可，系統上
不會留下任何檔案。

## 對話接續排程器（ClaudeResumer.exe）

跟上面的安裝器是完全獨立的工具、獨立的 exe。用途不是「反覆探測 Claude 能不能用」，
而是「被 session limit 卡住某個特定對話時，排定重置時間一到就自動幫我接著送出去」。

建置見下面的「怎麼打包」，輸出 `dist\ClaudeResumer.exe`。

用法：

1. Claude Code 跳出 `You've hit your session limit`、告訴你大概幾點重置時，打開
   `ClaudeResumer.exe`
2. 上方清單會列出本機（`~/.claude/projects/`）所有對話，依最後活動時間排序，可以打字
   篩選（比對專案路徑或第一句話），選出被卡住的那個。**正開在 VSCode 裡的對話會標
   `● 開啟中`**（見下面「怎麼保證不會被佔用」）
3. 填要接續送出的內容（預設 `continue`，可以自己改，也可以用「附加檔案」把檔案路徑
   用 `@` 語法帶進 prompt）、設定時間、選「自動送出」或「時間到手動確認再送出」
4. 「加入排程佇列」——可以一次排多筆

### 幾個操作上的細節

- **送出時間可以精準到秒**：勾「精準到秒 (HH:MM:SS)」，時間欄就從 `HH:MM` 變成
  `HH:MM:SS`，測試的時候不用等整分。旁邊即時顯示「現在 HH:MM:SS」，還有「＝現在」／
  「+1 分」兩顆快速鈕。時間欄本來就同時吃 `HH:MM` 跟 `HH:MM:SS` 兩種格式。
- **送完要不要自動關**：右下角「全部確認送出後自動關閉視窗」預設勾著（每一筆都確認
  送出後 3 秒自動關）。**想留著看結果就取消勾選**，送完也不關。任何一筆失敗一律不會
  自動關（否則看不到失敗原因）。

送出的當下是開一個獨立於本程式的 `claude.exe --resume <session-id> --print "<prompt>"`
行程，跑在**那個對話自己的專案資料夾**裡。送出後盯它 60 秒：撐過去就當作送出成功
（剩下的讓它在背景跑完），60 秒內就死掉的話標成失敗，並把錯誤印在視窗下方。

**時間一到就直接送，不查用量、不壓著等。** 你在這裡設的時間，本來就是「你判斷 token
會恢復」的那個時間點，它就是最終決定，這支工具不會再去二次確認用量、更不會自作主張把
任務壓著不送。這正是它跟 resetter 最根本的差別（見下面「時機點的含義不同」）。

### 關掉 resumer 之後，那個對話還會跑完嗎？會。

送出的是一個 `DETACHED_PROCESS` 的**獨立行程**，不是 resumer 的附屬子行程。所以確認
送出（撐過 60 秒）之後就可以把 ClaudeResumer.exe 關掉，被接續的那個對話會在背景自己
跑完。輸出寫在 `%LOCALAPPDATA%\ClaudeTimerResetter\resume-<id>.log`。

### 跟我平常在 VSCode 裡用的 Claude 是同一個嗎？

- **對話記錄：同一份。** VSCode 的 Claude Code 擴充跟 CLI 的 `claude` 共用
  `~/.claude/projects/<專案>/<id>.jsonl`。resumer 用 `--resume <session-id>` 加上正確的
  專案資料夾（`cwd`）指向的就是同一個對話檔，所以事後回 VSCode 重新開那個對話，
  **看得到 resumer 送出後跑的那一段**。
- **執行檔：通常就是 VSCode 擴充裡那支。** `find_claude_binary()` 的搜尋順序，最後一項
  就是去撈 `.vscode/extensions/anthropic.claude-code-*/.../claude.exe`。只要你 PATH 上
  沒有另外裝獨立 CLI，它抓到的就是同一支 binary。

### 怎麼保證不會「被佔用」，又能回 VSCode 接續（最關鍵的一件事）

Claude Code 的一個 session **同一時間只能有一個持有者**。所以流程是這樣配合的：

1. **送出的當下，那個對話不能開在 VSCode（或任何地方）裡。** 若正被佔用，`--resume`
   會回 `Session ID ... is already in use` 然後秒退，等於白送。
2. resumer 會在**不花任何 token** 的前提下自動偵測這件事：每個正在跑的 Claude Code
   行程（含 VSCode 擴充）都會在 `~/.claude/sessions/<pid>.json` 寫一份自己現在開著哪個
   `sessionId`。resumer 讀這些檔就知道某個對話是不是正被佔用——清單上會標 `● 開啟中`，
   「檢查可否傳送」按鈕也會直接告訴你是誰（哪個 pid / 是不是 VSCode）佔著。
3. 送出時如果偵測到被佔用，**不會白送**：那筆任務會被擋下、標成「被佔用」，並提醒你把
   VSCode 那個對話關掉。**自動送出模式下，你一關掉，下一秒它就自己補送**，不用你重排。
4. 送出成功、確認之後（撐過 60 秒），那個接續行程已經是獨立背景行程，這時你**再回
   VSCode 打開同一個對話**就能接著看它跑——因為 `--resume` 是往同一個對話檔接著寫
   （實測不會分岔成新檔）。

反過來的擔心（「背景在跑時我開 VSCode 會不會中斷它」）也是同一條規則：真正在背景送的
那一瞬間很短（就是送出的那一下），撐過確認期後看到的都是已經寫進對話檔的結果，回
VSCode 開來看不會把它弄斷。保險起見，**送出中～確認完成前先別搶著開那個對話**就好。

### 中途要 permission 確認怎麼辦？（重要限制）

resumer 是 `-p`/`--print` 的**非互動**執行，**沒有人能回應中途的權限提示**——而且那個
行程不在 VSCode 裡，也不會跳到 VSCode 讓你按。所以如果那個對話接下來需要工具權限確認，
它可能就卡住或中止。

對策就是表單裡的**「權限模式」下拉**（現在每個選項都有中文說明，選了會顯示在下方）：

| 選項 | 意思 | 什麼時候用 |
|---|---|---|
| `(不覆寫)`（預設） | 不加參數，沿用對話 / 專案原本設定，最保守 | 你確定它不會需要新權限時 |
| `acceptEdits` | 自動同意「改檔」類的權限，不停下來問 | **會動到檔案的接續最常用這個** |
| `bypassPermissions` | 略過所有權限檢查，什麼都不問（含執行指令） | 完全信任那個對話接下來要做的事時 |
| `dontAsk` | 不主動詢問權限 | 不確定就選 acceptEdits 比較好懂 |
| `plan` | 只規劃、不實際動手 | 只想讓它接著「想 / 規劃」，還不要真的執行 |

預設保持 `(不覆寫)`。如果送出後發現對話卡在權限提示上什麼都沒動，多半就是這裡要改成
`acceptEdits`。

### 之前為什麼沒有反應

第一版是 fire-and-forget，而且錯在幾個地方剛好都不會出聲——`build_resumer.bat` 是
`--windowed` 打包，沒有主控台，例外訊息無處可去，看起來就像「時間到了但什麼都沒發生」。
修掉的是：

| 問題 | 後果 | 現在 |
|---|---|---|
| 沒給子行程 `cwd` | Claude Code 的 session 是**按專案目錄**存的（`~/.claude/projects/<專案>/<id>.jsonl`），在別的目錄下 `--resume` 那個 id 會直接找不到對話 | 一律在 `session["cwd"]` 底下執行 |
| 沒給 `stdin` | `DETACHED_PROCESS` 之下沒有主控台，繼承來的 stdin 是無效 handle | `stdin=DEVNULL` |
| `Popen` 一成功就標「已送出」 | 行程起得來不代表事情有做成；`session 已被佔用`、`找不到這個 session` 都是起得來但秒退 | 盯 60 秒，秒退就標失敗並印出 log 尾巴 |
| 例外沒有人接 | `找不到 claude 執行檔`、套件沒裝 等等全部靜靜消失在 Tk 的 callback 裡 | 全部接起來寫進畫面上的「狀況 / 錯誤」欄與 `resumer.log` |
| 送出後 2 秒就自動關閉 | 就算有錯誤訊息也來不及看到 | 只有全部確認送出才自動關閉，而且可以取消勾選讓它送完也不關；有失敗一律留著 |
| 時間到還去查用量、沒重置就壓著等 | 明明時間到了卻不送——那是 resetter 的職責，混進來就變成推翻你設好的時間 | 整段用量閘門移除，時間到直接送 |
| 對話正開在 VSCode 裡才發現被鎖 | 白送一次、還不知道為什麼 | 送出前零成本偵測，被佔用就擋下並自動補送（見上面「怎麼保證不會被佔用」） |

「檢查可否傳送（不花錢）」按鈕：事先確認 claude 執行檔找得到、對話紀錄檔還在、專案
資料夾還在、**而且現在沒有被 VSCode 佔用**，免得等到半夜排程時間到才發現。

## 怎麼打包（換一台電腦、或 clone 完想直接建 exe）

```bat
git clone https://github.com/ChouYangEn0401/ClaudeTimerResetter.git
cd ClaudeTimerResetter
build.bat
```

就這樣，沒有第二步。`build.bat` 會自己做完全部三件事：

1. 沒有 `.venv` 就建一個，裝 `requirements.txt`（執行期相依）
2. 裝 `requirements-build.txt`（只有建置才需要的 pyinstaller / pystray / pillow）
3. 用 `packaging\` 底下的 spec 跑 PyInstaller

只建其中一支：

```bat
build.bat installer    :: 只建 dist\ClaudeTimerResetter.exe
build.bat resumer      :: 只建 dist\ClaudeResumer.exe
```

`build_installer.bat` / `build_resumer.bat` 還在，但現在只是轉呼叫 `build.bat`，
留著是為了不用改掉既有的習慣。

**那台電腦需要的東西**：Windows、Python 3.10 以上而且 `py` 指令叫得到。就這兩個。
不需要事先裝 pyinstaller，也不需要先跑 `setup.bat`。

### 為什麼打包設定是 checked-in 的 spec 檔，而不是一串命令列參數

`packaging/ClaudeTimerResetter.spec` 跟 `packaging/ClaudeResumer.spec` 是手寫、進版控的。
`.gitignore` 特地為它們開了例外（`*.spec` 忽略，但 `!packaging/*.spec` 留下）。

理由是 hidden import。`claude_subscription` 在 `ping.py` / `resumer.py` 裡是「用到才
import」（寫在函式內），`pystray._win32` 則是執行期才決定的後端——這類 import 一旦被
漏掉，exe 是**跑起來才會炸**，而且兩支都用 `--windowed` 打包、沒有主控台，錯誤訊息很
難追。把它們明列在 spec 裡並跟原始碼放在同一個 commit，就不會出現「你那台建出來的
exe 少了某個模組」這種只在別人機器上壞掉的問題。

兩份 spec 的差別：`ClaudeResumer.spec` 多了 `excludes=["pystray", "PIL"]`，因為接續
排程器沒有工具列常駐模式，排掉之後 exe 從 18 MB 降到 10 MB。

建完可以確認一下該進去的都進去了：

```bat
.venv\Scripts\python -c "from PyInstaller.archive.readers import ZlibArchiveReader; print(sorted(k for k in ZlibArchiveReader('build/ClaudeTimerResetter/PYZ-00.pyz').toc if 'claude' in k or k in ('usage','ping','rules','tray')))"
```

### dist\ 沒有進版控

`.gitignore` 排除了 `build/` 跟 `dist/`，所以 repo 裡不會有 exe。要把建好的 exe 發給
別人，走 GitHub Release 附檔比較合適，不要 commit 進來——28 MB 的二進位檔每次重建都
會讓 repo 長大一次。

## 注意

`.bat` 檔請保持**純 ASCII**——cmd.exe 用 OEM codepage（本機是 cp950）讀 `.bat`，
寫中文進去會讓整個腳本解析錯亂（實測踩過）。中文說明放在這份 README 與 `ping.py` 裡就好。
