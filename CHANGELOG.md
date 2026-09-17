# 更動記錄

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
