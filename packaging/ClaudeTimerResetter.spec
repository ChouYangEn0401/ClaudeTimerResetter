# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：ClaudeTimerResetter.exe（背景排程 + 探針）。

用 build.bat 跑，不要直接叫 pyinstaller——build.bat 會先把 .venv 跟建置相依裝好。
    build.bat            兩支都建
    build.bat installer  只建這一支

刻意寫成 checked-in 的 spec 而不是一長串命令列參數，理由是：換一台電腦 clone 下來
之後，打包設定跟原始碼是同一份、同一個 commit，不會發生「他那台建出來的 exe 少了
某個 hidden import」這種只在別人機器上壞掉的問題。
"""
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

# ping.py 裡 claude_subscription 是「用到才 import」（放在函式內），resumer/ping 之間
# 也是同目錄 import。PyInstaller 靜態分析抓得到，但這裡明列出來當保險：這幾個名字掉了
# 的話，exe 要到執行時才會炸，而 --windowed 沒有主控台，錯誤訊息會很難追。
HIDDEN = [
    "claude_subscription",
    "usage",
    "ping",
    "rules",
    "scheduler",
    "autostart",
    "tray",
    "pystray._win32",   # pystray 靠執行期挑後端，靜態分析看不出來要哪一個
]

a = Analysis(
    [os.path.join(ROOT, "installer.py")],
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
