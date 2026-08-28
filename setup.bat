@echo off
REM ===================================================================
REM  setup.bat - one-time environment setup.
REM  Creates .venv and installs the runtime deps listed in
REM  requirements.txt (claude_subscription from a GitHub release wheel;
REM  pure stdlib, no third-party deps). Safe to re-run.
REM  Build-only deps live in requirements-build.txt - see build.bat.
REM  Keep this file ASCII-only (see note in test.bat).
REM ===================================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv || exit /b 1
)
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || exit /b 1

echo.
echo [OK] environment ready. Run test.bat to check Claude.
exit /b 0
