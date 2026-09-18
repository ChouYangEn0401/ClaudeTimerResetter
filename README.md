# ClaudeTimerResetter

兩個給 Claude Code 訂閱用戶的小工具，都是 Windows 桌面程式，都只做一件很窄的事。

| 工具 | 解決的問題 | 一句話 |
|---|---|---|
| **ClaudeTimerResetter**（resetter） | 工作到一半額度用完，只能乾等視窗重置 | 在你**預期會重度使用之前**，先打一次最便宜的探針（約 $0.00025），把 5 小時額度視窗的重置時鐘**提早啟動** |
| **ClaudeResumer**（resumer） | 某個對話被 session limit 卡住，要守在電腦前等它恢復 | 你告訴它幾點恢復，**時間一到自動把那個對話接著送出去**，送完自己關掉 |

兩支是**獨立的 exe**、各自獨立使用，沒有先後關係，也不需要都裝。
為什麼分成兩支而不是一個工具兩個分頁 → [docs/design.md](docs/design.md)。

目前版本 **v1.1.0**（更動見 [CHANGELOG.md](CHANGELOG.md)）。

---

## 先決條件

那台電腦要先裝好官方 **Claude Code CLI 並登入過**（命令列打 `claude` 要能跑）。
這一步本專案管不到，只能偵測、不能取代。

其餘：Windows 10/11。用 exe 的話**不需要裝 Python**；要從原始碼跑才需要 Python 3.10+。

---

## 五分鐘上手

### 只想用 → 拿 exe

```bat
git clone https://github.com/ChouYangEn0401/ClaudeTimerResetter.git
cd ClaudeTimerResetter
scripts\build.bat
```

跑完 `dist\` 裡會有兩個檔案，複製到任何一台 Windows 都能雙擊執行：

- `ClaudeTimerResetter.exe` — 雙擊開主控台，設定排程規則後按「安裝／更新設定」。
  之後它會在背景自動跑，**不用開著視窗**。詳見 [docs/resetter.md](docs/resetter.md)
- `ClaudeResumer.exe` — 被卡住的當下雙擊打開，挑對話、填時間、加入佇列。
  用完關掉，不常駐、不需要安裝。詳見 [docs/resumer.md](docs/resumer.md)

只想單獨建其中一支、用雙擊就好，不想打指令：`scripts\build-resetter.bat`、
`scripts\build-resumer.bat`。

### 想改程式 → 從原始碼跑

```bat
scripts\setup.bat                          :: 只需跑一次：建 .venv、裝相依
.venv\Scripts\python resetter.py           :: 開 resetter 主控台
.venv\Scripts\python resumer.py            :: 開 resumer
```

另外兩個命令列工具（不需要 GUI）：

```bat
scripts\usage.bat        :: 現在用掉多少額度、幾點重置。一定不花錢（純 HTTPS GET，不經過模型）
scripts\test.bat         :: 該刷新就刷新一次，不該刷新就不打。跟排程走完全同一條路
```

`scripts\` 底下的 `.bat` 發現沒有 `.venv` 都會自己叫 `setup.bat`，第一次直接跑也行。
參數與結束碼見 [docs/resetter.md](docs/resetter.md#命令列工具)。

---

## 文件

| 想知道 | 看這裡 |
|---|---|
| 排程規則怎麼設？兩種背景執行方式差在哪？怎麼解除安裝？ | [docs/resetter.md](docs/resetter.md) |
| 怎麼排一筆接續？為什麼「對話不能開著」？權限模式選哪個？ | [docs/resumer.md](docs/resumer.md) |
| 怎麼建 exe、發佈、改程式？ | [docs/build.md](docs/build.md) |
| 為什麼是兩支工具？為什麼這麼便宜？當初踩過哪些坑？ | [docs/design.md](docs/design.md) |

---

## 專案結構

```
resetter.py / resumer.py     兩支 exe 的進入點（只做轉接，實作在套件裡）
claude_timer/
  core/       不帶畫面的核心：usage（查用量）、ping（探針）、rules（排程規則）、
              scheduler（Windows 工作排程器）、autostart（開機捷徑）
  ui/         兩個 GUI 共用的外觀：theme（配色字級）、scroll（可捲動版面）
  resetter/   app（主控台 GUI）、service（安裝與 tick）、tray（工具列常駐）
  resumer/    app（主視窗）、sessions（對話清單）、transcript（對話結構化）、
              viewer（內容預覽）、tasks（一筆接續任務）
packaging/    兩份手寫的 PyInstaller spec（進版控，理由見 docs/build.md）
docs/         說明文件
scripts/      setup / test / usage / build（含雙擊用的 build-resetter / build-resumer）
```

設定與紀錄統一放 `%LOCALAPPDATA%\ClaudeTimerResetter\`（規則、狀態、執行紀錄），
不放在 exe 旁邊——exe 可能被放在會被清掉的位置。

---

## 注意

`.bat` 檔請保持**純 ASCII**。cmd.exe 用 OEM codepage（繁中機器是 cp950）讀 `.bat`，
寫中文進去會讓整個腳本解析錯亂（實測踩過）。中文說明放在這份 README、`docs/` 與
Python 原始碼裡就好。
