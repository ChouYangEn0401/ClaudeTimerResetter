# 開發與打包

## 需要什麼

Windows、Python 3.10 以上、`py` 指令叫得到。就這兩個——不需要事先裝 pyinstaller，
也不需要先跑 `setup.bat`。

## 建 exe

```bat
git clone https://github.com/ChouYangEn0401/ClaudeTimerResetter.git
cd ClaudeTimerResetter
build.bat
```

`build.bat` 自己會做完三件事：建 `.venv` 並裝 `requirements.txt`（執行期相依）→ 裝
`requirements-build.txt`（只有建置才需要的 pyinstaller / pystray / pillow）→ 用
`packaging\` 底下的 spec 跑 PyInstaller。

只建其中一支：

```bat
build.bat resetter     :: 只建 dist\ClaudeTimerResetter.exe
build.bat resumer      :: 只建 dist\ClaudeResumer.exe
```

（`build.bat installer` 仍然可以用，是 `resetter` 的別名。舊的
`build_installer.bat` / `build_resumer.bat` 兩個轉呼叫用的檔案已經移除。）

## 從原始碼跑

```bat
setup.bat                                   :: 只需跑一次
.venv\Scripts\python resetter.py            :: resetter 主控台
.venv\Scripts\python resetter.py --tick     :: 排程用的靜默進入點（手動跑只為除錯）
.venv\Scripts\python resetter.py --tray     :: 工具列常駐
.venv\Scripts\python resumer.py             :: resumer（沒有參數）
.venv\Scripts\python -m claude_timer.core.usage    :: 等同 usage.bat
.venv\Scripts\python -m claude_timer.core.ping     :: 等同 test.bat
```

功能跟 exe 完全一樣——exe 只是把 Python 跟相依包進去，讓沒裝 Python 的電腦也能用。
開發時用原始碼跑比較快（不用每次重建）。

**VSCode 提醒**：兩支都是 Tkinter GUI，要用 `.venv` 的 python 跑
（`Ctrl+Shift+P` → `Python: Select Interpreter` 選 `.venv`，或直接打完整路徑）。
用系統 python 跑會說找不到 `claude_subscription`。

## 程式怎麼分層

```
resetter.py / resumer.py   進入點，只做轉接；PyInstaller 的 script 入口就是這兩個
claude_timer/
  paths.py     所有「東西放哪裡」的單一來源（安裝目錄、各種紀錄檔、~/.claude 底下的路徑）
  core/        不帶畫面，CLI 跟兩個 GUI 共用
    usage.py       零成本讀用量（純 HTTPS GET）
    ping.py        先查用量再決定要不要打探針
    rules.py       排程規則引擎（interval / daily_times / burst）
    scheduler.py   Windows 工作排程器包裝（schtasks + XML）
    autostart.py   開機自動啟動捷徑
  ui/
    theme.py     配色、字級、ttk 樣式、高 DPI、視窗尺寸貼合螢幕
    scroll.py    可捲動版面容器（內容超過視窗高度就出現捲軸）
  resetter/
    app.py       主控台 GUI + --tick/--tray 分流
    service.py   跑規則、寫紀錄、安裝／解除安裝（GUI、排程、常駐三條路共用）
    tray.py      工具列常駐驅動
  resumer/
    app.py         主視窗
    sessions.py    掃對話清單、偵測「誰正開著這個對話」
    transcript.py  把 .jsonl 讀成有結構的對話（分類、合併、分段、摘要）
    viewer.py      內容預覽視窗
    tasks.py       一筆接續任務（送出、盯行程、失敗處理）
    journal.py     resumer 的檔案紀錄
```

規則：**畫面不碰邏輯，邏輯不碰畫面**。`core/` 跟 `resumer/transcript.py`、
`resumer/sessions.py` 都可以單獨 import 來測，不需要開 Tk。

三條執行路徑（GUI 的「立即測試」、工作排程器的 `--tick`、工具列常駐的計時器）都走
`resetter/service.py` 的 `run_due_rules()`，排程語意只有一份——以前 GUI 跟 tray 各抄了
一份幾乎一樣的迴圈，改一邊忘另一邊就會不一致。

## 為什麼打包設定是 checked-in 的 spec

`packaging/ClaudeTimerResetter.spec` 跟 `packaging/ClaudeResumer.spec` 是手寫、進版控的
（`.gitignore` 特地開了例外：`*.spec` 忽略，但 `!packaging/*.spec` 留下）。

理由是 hidden import。`claude_subscription` 是「用到才 import」（寫在函式內）、
`pystray._win32` 是執行期才決定的後端——這類 import 一旦漏掉，exe 是**跑起來才會炸**，
而且兩支都用 `--windowed` 打包、沒有主控台，錯誤訊息很難追。把它們明列在 spec 裡、
跟原始碼放在同一個 commit，就不會出現「你那台建出來的 exe 少了某個模組」這種只在別人
機器上壞掉的問題。

兩份 spec 的差別：`ClaudeResumer.spec` 多了 `excludes=["pystray", "PIL"]`，因為接續
排程器沒有工具列常駐模式，排掉之後 exe 小幾 MB。

## 發佈

`.gitignore` 排除了 `build/` 跟 `dist/`，repo 裡不會有 exe。要把建好的 exe 發給別人，
走 GitHub Release 附檔比較合適，不要 commit 進來——二十幾 MB 的二進位檔每次重建都會
讓 repo 長大一次。

## 寫 .bat 的注意事項

`.bat` 檔請保持**純 ASCII**。cmd.exe 用 OEM codepage（繁中機器是 cp950）讀 `.bat`，
寫中文進去會讓整個腳本解析錯亂（實測踩過）。中文說明放在 `docs/` 跟 Python 原始碼裡。
