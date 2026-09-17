# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：ClaudeTimerResetter.exe（提前重置排程器：主控台 + 背景 tick）。

用 build.bat 跑，不要直接叫 pyinstaller——build.bat 會先把 .venv 跟建置相依裝好。
    build.bat            兩支都建
    build.bat resetter   只建這一支

入口是根目錄的 resetter.py（轉接到 claude_timer.resetter.app）。claude_timer 套件
本身走一般的 import，PyInstaller 靜態分析抓得到，所以 HIDDEN 只放它看不出來的那幾個。

刻意寫成 checked-in 的 spec 而不是一長串命令列參數，理由是：換一台電腦 clone 下來
之後，打包設定跟原始碼是同一份、同一個 commit，不會發生「他那台建出來的 exe 少了
某個 hidden import」這種只在別人機器上壞掉的問題。
"""
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

# 靜態分析看不出來的 import：claude_subscription 是「用到才 import」（寫在函式內），
# pystray 的後端則是執行期才決定。這兩個掉了的話，exe 要到執行時才炸，而 --windowed
# 沒有主控台，錯誤訊息會很難追。
HIDDEN = [
    "claude_subscription",
    "pystray._win32",
]

a = Analysis(
    [os.path.join(ROOT, "resetter.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ClaudeTimerResetter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                    # GUI 工具；--tick 的輸出本來就是寫檔不是印螢幕
    disable_windowed_traceback=False,  # 留著：沒有主控台時，未捕捉的例外至少會跳視窗
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
