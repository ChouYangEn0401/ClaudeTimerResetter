# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：ClaudeResumer.exe（一次性的對話接續排程器）。

用 build.bat 跑，不要直接叫 pyinstaller。
    build.bat          兩支都建
    build.bat resumer  只建這一支

入口是根目錄的 resumer.py（轉接到 claude_timer.resumer.app）。跟
ClaudeTimerResetter.exe 是刻意分開的兩支 exe（見 docs/design.md），所以打包設定也分成
兩份 spec，不共用；這支不需要 pystray（沒有工具列常駐模式），排掉可以少幾 MB。
"""
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

# claude_subscription 在 tasks.claude_binary() 裡才 import，靜態分析看不到，明列出來。
HIDDEN = [
    "claude_subscription",
]

a = Analysis(
    [os.path.join(ROOT, "resumer.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pystray", "PIL"],   # 這支用不到，排掉可以少幾 MB
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
    name="ClaudeResumer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,  # 沒有主控台，未捕捉的例外至少會跳視窗
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
