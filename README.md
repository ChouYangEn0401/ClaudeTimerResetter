# ClaudeTimerResetter — Claude 可用性極簡測試

一支「現在 Claude 能不能用？」的最便宜探針。直接用
[ClaudeLogin 的 release wheel](https://github.com/ChouYangEn0401/ClaudeLogin/releases/tag/v0.2.0)，
不需要 clone 原始碼。

## 用法

```bat
setup.bat      :: 只需跑一次：建 .venv + 從 GitHub Release 裝 whl
test.bat       :: 測試（人看的輸出）
test.bat --json:: 測試（機器讀的輸出）
```

`test.bat` 發現沒有 `.venv` 會自己叫 `setup.bat`，所以第一次直接跑 `test.bat` 也行。

## 為什麼便宜

| 項目 | 設定 | 理由 |
|---|---|---|
| 模型 | `claude-haiku-4-5-20251001` | 目前最便宜的一檔 |
| 工具 | 全關 `tools=""` | 純文字進出；開工具是 agent 模式，貴 15~45 倍 |
| thinking | `MAX_THINKING_TOKENS=0` | 回一句 "hi" 不需要模型多想 |
| 權限 | `manual` | 工具全關，這裡只是明確表態不自動放行 |
| session | `persist=False` | 不保存、不重送歷史（重送歷史才是最貴的） |
| 提示 | `hi` | 不能更短了 |

**實測：$0.00038，約 4 秒。**

## 結束碼

| 碼 | 意思 | 該做什麼 |
|---|---|---|
| 0 | 可用 | — |
| 1 | 呼叫失敗（額度用完 / 逾時 / 模型錯誤） | 可以重試 |
| 2 | 找不到 `claude` 執行檔 | 裝官方 Claude Code |
| 3 | 未登入 / 認證失敗 | 執行 `claude` 登入 |
| 4 | 環境沒裝好 | 跑 `setup.bat` |

排程或腳本可以直接判斷 `%ERRORLEVEL%`，或用 `--json` 讀 `ok` 欄位。

## 注意

`.bat` 檔請保持**純 ASCII**——cmd.exe 用 OEM codepage（本機是 cp950）讀 `.bat`，
寫中文進去會讓整個腳本解析錯亂（實測踩過）。中文說明放在這份 README 與 `ping.py` 裡就好。
