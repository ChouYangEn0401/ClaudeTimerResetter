@echo off
REM ===================================================================
REM  build_resumer.bat - build ClaudeResumer.exe with PyInstaller.
REM  Reuses the same .venv as setup.bat/test.bat.
REM  Output: dist\ClaudeResumer.exe -- independent from ClaudeTimerResetter.exe.
REM  Keep this file ASCII-only (see note in test.bat).
REM ===================================================================
setlocal
cd /d "%~dp0"

call setup.bat || exit /b 1

".venv\Scripts\python.exe" -m pip install -q pyinstaller || exit /b 1

".venv\Scripts\pyinstaller.exe" --noconfirm --onefile --windowed ^
    --name ClaudeResumer ^
    --distpath dist --workpath build --specpath build ^
    resumer.py || exit /b 1

echo.
echo [OK] built: dist\ClaudeResumer.exe
exit /b 0
