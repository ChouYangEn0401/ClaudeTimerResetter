@echo off
REM ===================================================================
REM  build_installer.bat - build ClaudeTimerResetter.exe with PyInstaller.
REM  Reuses the same .venv as setup.bat/test.bat, adds build-only deps
REM  (pyinstaller, pystray, pillow) on top of it.
REM  Output: dist\ClaudeTimerResetter.exe  -- this is the file to share.
REM  Keep this file ASCII-only (see note in test.bat).
REM ===================================================================
setlocal
cd /d "%~dp0"

call setup.bat || exit /b 1

".venv\Scripts\python.exe" -m pip install -q pyinstaller pystray pillow || exit /b 1

".venv\Scripts\pyinstaller.exe" --noconfirm --onefile --windowed ^
    --name ClaudeTimerResetter ^
    --hidden-import pystray._win32 ^
    --distpath dist --workpath build --specpath build ^
    installer.py || exit /b 1

echo.
echo [OK] built: dist\ClaudeTimerResetter.exe
exit /b 0
