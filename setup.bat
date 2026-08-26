@echo off
REM ===================================================================
REM  setup.bat - one-time environment setup.
REM  Creates .venv and installs claude_subscription from the GitHub
REM  release wheel (pure stdlib, no third-party deps).
REM  Keep this file ASCII-only (see note in test.bat).
REM ===================================================================
setlocal
cd /d "%~dp0"

set WHL=https://github.com/ChouYangEn0401/ClaudeLogin/releases/download/v0.2.0/claude_subscription-0.2.0-py3-none-any.whl

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv || exit /b 1
)
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q "%WHL%" || exit /b 1

echo.
echo [OK] environment ready. Run test.bat to check Claude.
exit /b 0
