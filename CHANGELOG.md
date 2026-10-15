# 更動記錄

## v1.5.1

- 接手前存了快照的話，送給接手那一段的話前面多一句：工作目錄裡還沒 commit 的檔案是上一段的進度、檔案都在原處，照原樣接著做，不要 git stash／reset／checkout 掉，做完一段就 commit（擁有者 2026-10-15 問「下一個 session 接手時會遇到什麼」）。

## v1.5.0

起因：擁有者 2026-10-15 看過 v1.4 的安全邊界之後的兩件事——「接管之前先 commit」做不到（髒的就不接，
而且到點接管是先關掉人家才檢查，髒的話等於把人關掉、又什麼都沒做）；「背景那一段沒有人看著」。

**接手前先存快照，不再因為工作目錄髒就不接**
- 送出前（接管的話在關掉持有者**之前**）把專案資料夾那一刻的檔案存成一個 commit：`refs/resumer/snapshots/<時間>-<對話前 8 碼>`。
  用暫時的 index 做，你的工作目錄、暫存區、分支都不動，`git push` 不會帶出去；`.gitignore` 照樣有效（`.env` 不會進去）。
  留最新 20 份；接管要等持有者閒下來、一輪一輪重試時，沒變就沿用同一份，不會把舊的擠掉。
- CLI `resume` 不再因為還沒 commit 就失敗（`--no-require-clean` 還收，但不做事）；job 多了 `snapshot`、`took_over` 欄位。
  `stash <id>` 碰到接手前本來就髒的，會提醒你接手前的樣子在哪一份快照。
- 視窗版的到點接管、「立刻接管」按鈕都一樣先存。

**做完傳一則到 Telegram（經 Notifier_TB）**
- 視窗版每一筆背景接續做完都講一聲（結果那一句、有沒有接管、快照在哪）；CLI 接管的一定傳，其他的加 `--notify`
  （ClawBot 派的它自己會說，不用加）。CLI 是另開一個小行程（`watch <id>`）等它做完。
- 不連 Telegram：往 Notifier_TB 的 socket 丟一個封包（設定讀它自己的 config.json；沒開著就幫它開，最多等 30 秒）。
  不想要就設環境變數 `CLAUDE_RESUMER_NOTIFY=0`。

## v1.4.0

起因：2026-10-15 03:51 一筆「時間到就強制接管」把一個**正在做事**的 VSCode 對話從中間砍斷；接手的
`claude --resume --print` 沒帶權限參數（預設「(不覆寫)」），拿到 headless 的 default——白名單以外的指令、工作目錄
以外的檔案全被拒，55 秒後 exit 0，被記成「已送出」。同一分鐘另一個對話（ISDPipelineAgent）也一樣停住。

**接手時照原本的來（新預設「(沿用原本)」／CLI `inherit`）**
- 從對話紀錄讀**最後一次在 VSCode／終端機裡**的 `permissionMode`、`requestedModel`、`effort`，明確帶
  `--permission-mode`、`--model`、`--effort`。接管跑出來的那幾段（`sdk-cli`）不算，不然接一次就永遠退回 default。
- `bypassPermissions` 永遠不沿用（背景沒人看著），改用 `auto` 並寫一句；讀不到就退回 `acceptEdits` 並寫明。
- 權限選單補上 `auto`；「(不覆寫)」移到最後，說明改成實話：它是 headless 的 default，不是沿用。
- CLI `resume` 與 `api.resume()` 的預設改成 `inherit`（`new` 沒有「原本」，照舊 `acceptEdits`）。

**只接管閒著、而且是人開著的對話**
- 到點的接管：持有者**正在跑**就不殺，等下一次重試（例如等額度用完它自己停下來）；持有者是**程式**
  （`claude -p`：PipelineAgent 的節點、另一筆 Resumer 工作）一律不碰，等它結束再補送。
- 人自己按的「立刻接管」「釋放佔用」看得到狀態，可以關正在跑的（CLI `release --even-busy`、
  `api.release(..., even_busy=True)`）；程式開的照樣不碰。
- 關的時候連子程序一起收（`taskkill /T`），收掉了哪些（`bash.exe×2、python.exe`）寫進訊息與紀錄，不留沒人管的孤兒。
- 接管之後送出的那句前面加一段說明：接手的那個知道自己是在背景、權限是什麼、被擋住就停下來寫清楚狀態。

**看結果判斷做了什麼**：視窗版也用 `--output-format json`；背景行程結束後讀結果，佇列寫
「完成（12 輪、$0.84）」／「完成（…），但被權限擋了 3 種：Bash(git fetch)…」／「claude 回報錯誤」。CLI 的
`job <id>` 在被擋時把同一句放進 `error`。

## v1.3.1

**Python API（`claude_timer/api.py`）**：視窗上每一顆按鈕、CLI 每一個子指令都對到一個函式（sessions／show／check／
release／resume／new／jobs／job／cancel／stash／fire），回 `Result(code, data, text)`，跟 CLI 走同一條程式
（`cli.run()`）、不丟例外。對照表（按鈕 → 函式 → CLI → 實作在哪）在 `docs/api.md`。

**修正：好幾個 CLI 行程同時寫 `resume_jobs.json` 會掉資料**。以前只有行程內的鎖：兩個行程各寫各的，後寫的蓋掉
先寫的那一筆；剛好在別人換檔那一瞬間讀檔還會當成空的、整份清掉。現在加了跨行程的檔案鎖，讀到一半遇到換檔會等一下
再讀，壞掉的檔案改名留證據。（追 ClawBot 的連動測試間歇失敗時找到的。）

## v1.3.0

三件事都來自實際用了一陣子的回饋（2026-10-08）：預覽找不到內容、同名專案分不清、佔用釋放不掉。

**對話預覽：一輪一輪看「你說 → 它最後回」**
- 以前照時間差切段，一段裡還是幾十則「⚙ 工具動作」洗版。現在以「你說一句」為界切輪：你的整段、
  「▸ 中間過程：⚙ Bash ×12、Edit ×3｜中途回覆 2 則」預設收起來、它最後回的整段；沒有回覆的那一輪
  標「可能就是卡在這裡」。左邊大綱列每一輪你說的第一句；很長的對話只展開最近 10 輪；搜尋只留下有
  那段字的輪並自動展開中間過程。
- CLI `show <session> --turns N` 多印最近 N 輪（JSON 的 `last_turns`），給 agent 當「做到哪」的脈絡。

**清單：分得出誰是誰**
- 每一列多讀 .jsonl 的 git 分支與 entrypoint；資料夾分成專案／分身（worktree）／程式用（LOCALAPPDATA）／
  暫存（TEMP）；同名的專案資料夾帶上一層資料夾（`work/WebApp`、`play/WebApp`）。
- 視窗版多「分支」「來源」兩欄（`VSCode`、`終端`、`程式(-p)`、`分身·VSCode`…），「藏起暫存與程式用的」
  預設勾著（ClawBot 的 AI 秘書、探針、resumer 自己 new 出來的對話不再混在清單裡）；「已選」那行寫
  完整路徑與分支。
- CLI `sessions` 預設也藏暫存／程式用的，`--all` 才列；指定 `--cwd` 時不過濾。JSON 多 `label`／`branch`／
  `entrypoint`／`origin`／`source`，原本的 `project` 不變。

**佔用：偵測修正、只釋放、到時接管**
- **殘留鎖修掉**：`~/.claude/sessions/<pid>.json` 在行程結束後不一定會清掉、pid 又會被重用，以前只看
  「pid 活著而且叫 claude」，VSCode 開關幾次之後一個早就關掉的對話會一直顯示被佔用。現在要跟
  Claude Code 寫的 `procStart`（行程建立時間）對得上才算持有者；描述多了「閒置中（最後動作 N 分鐘前）」。
- **「釋放佔用（不送出）」**（視窗版 ①、CLI `release <session>`）：只把佔用的行程關掉，不送任何東西。
- **「被佔用時」是排程時就講好的**（視窗版 ② 三選一；CLI `resume --take-over`／`--fork`）：等它關掉再補送
  （預設）／時間到就強制接管／分支接續（`--fork-session`，原本的對話框可以留著，**未實測**能不能繞過鎖）。
  ③ 的「強制接管並送出」改名「立刻接管並送出」並寫明是現在就動手；另加「改成到時接管」。
- `core/procs.py`：pid＋建立時間的比對，job 與持有者共用。

**測試**：`tests/test_transcript.py`、`tests/test_sessions.py`、`tests/test_holders.py`，`tests/test_cli.py`
多了到時接管、--fork、release。

## v1.2.0

**ClaudeResumer 命令列版（`python -m claude_timer.cli`、`scripts\ctr.bat`）**
- 子指令：`sessions`、`show`、`check`、`resume`、`new`、`jobs`、`job`、`cancel`、`stash`、`fire`，都有 `--json`，
  結束碼寫成契約（0／1／2／3／4）。說明在 `docs/cli.md`，給 agent 讀的在 `skills/claude-resumer/SKILL.md`。
- **`new`：在專案開一個全新的對話做新任務**（以前 resumer 只能接舊對話）。新的 session id 先決定好，之後可以再接。
- 排程不靠視窗開著：每筆 job 一個一次性的 Windows 工作排程 `ClaudeResumer-<id>`；被佔用每 2 分鐘補試、最多 1 小時；
  電腦那時是關的，開機後補跑。job 存在 `%LOCALAPPDATA%\ClaudeTimerResetter
esume_jobs.json`。
- 跑的時候能碰到什麼：內容釘死（content_hash 不一樣就不跑）；`new` 預設在獨立 git worktree＋branch 跑；
  `resume` 預設要求工作目錄乾淨並記下起點 commit，`job` 看得到改動、`stash` 收得回來；工具限制與預算原樣交給 claude。
- 送出的邏輯抽成 `claude_timer/resumer/launch.py`，視窗版和命令列版共用同一份。
- 新增 `tests/test_cli.py`（假的 claude、暫存資料夾，不碰工作排程器）。

## v1.1.1

**bat 檔**
- 四個 `.bat`（`setup` / `build` / `test` / `usage`）搬進 `scripts/`，根目錄只留原始碼
  跟進入點，跟一般開源專案把建置腳本收進 `scripts/`、根目錄放程式碼的慣例一致。
- 補回雙擊建置單一 exe 的捷徑：`scripts/build-resetter.bat`、`scripts/build-resumer.bat`
  （v1.1.0 把舊的 `build_installer.bat` / `build_resumer.bat` 直接刪掉，這次照原本
  「先跑 setup、再跑 build」的慣例補回來，只是換了資料夾跟名字）。
- 所有文件與程式內的 `.bat` 路徑提示同步改成 `scripts\xxx.bat`。

**文件**
- `docs/design.md` 重新分節：「架構決策」（現在仍成立、改動前要先看）跟「踩過的坑」
  （已修好的歷史問題）分開，不再是一路排下來的問答集。

## v1.1.0

**畫面**
- 兩個 GUI 都改成可捲動版面：內容比視窗高時出現捲軸，不會再有「下半部看不到」的情況。
- 視窗開啟時自動貼合螢幕可用空間，最小尺寸放寬（resumer 720x520、resetter 640x480）。
- 兩支共用同一份外觀（`claude_timer/ui/theme.py`）：配色、字級、高 DPI 處理只有一份。
- 對話清單與排程佇列從「用 `|` 接起來的一行字串」換成有欄位的表格。

**resumer 的對話預覽**
- 改成結構化呈現：摘要卡（期間、則數、常用工具、最後一次你說了什麼 / 它回了什麼）
  ＋時段大綱（照時間切段，可點擊跳轉）＋可摺疊的訊息。
- 工具呼叫併成一行 `⚙ Bash ×12、Edit ×3`；思考過程預設收起來，可切換。
- 新增搜尋（命中反白）、「跳到最新」。
- 對話清單改用 Claude Code 自己的標題（`ai-title` / `custom-title`）與最後一句
  使用者輸入，比原本的「檔案裡第一行」好認；並顯示相對時間與「● 開啟中」。
- 清單預設載入最近 200 個對話，可按「載入更多」（原本每次都全掃）。

**resetter 的主控台**
- 新增「目前狀態」卡：直接寫現在裝的是哪一種執行方式、安裝在哪、有幾條規則。
- 新增規則的對話框改用中文選項＋說明＋範例，並即時顯示「這條規則的意思」。
- 每個選項旁邊補上什麼時候該用它的說明。

**專案結構**
- 原始碼收進 `claude_timer/` 套件（`core` / `ui` / `resetter` / `resumer`），
  根目錄只留 `resetter.py`、`resumer.py` 兩個進入點與 `.bat`。
- `installer.py` → `claude_timer/resetter/app.py`（GUI）＋ `service.py`（安裝與排程 tick）；
  tray 不再自己抄一份規則迴圈，三條執行路徑共用 `service.run_due_rules()`。
- 路徑集中到 `claude_timer/paths.py`；版本號單一來源 `claude_timer/__init__.py`。
- `test.bat` / `usage.bat` 改叫 `python -m claude_timer.core.ping` / `.usage`。
- 移除 `build_installer.bat` / `build_resumer.bat`（改用 `build.bat resetter|resumer`）。

**文件**
- README 拆成入口＋`docs/resetter.md`、`docs/resumer.md`、`docs/build.md`、`docs/design.md`。

## v1.0.1

- resumer 字體與視窗放大，改善可讀性。

## v1.0.0

- 排程改成定時觸發（有 `daily_times` 規則時排那幾個時刻，不再每分鐘輪詢）。
- resumer 新增「強制接管並送出」。
- resumer 新增對話內容預覽視窗。
- resumer 時間到直接送出，移除用量閘門；零成本偵測 session 被佔用。
- 可攜的 PyInstaller 打包設定（`build.bat` + checked-in spec）。
