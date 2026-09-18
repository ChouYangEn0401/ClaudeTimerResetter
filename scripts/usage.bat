@echo off
REM ===================================================================
REM  usage.bat - read how much quota is used and when it resets.
REM    cost   : $0 - plain HTTPS GET to the same endpoint Claude Code
REM             uses for /usage. No model call, no tokens.
REM    source : OAuth token from %USERPROFILE%\.claude\.credentials.json
REM
REM  Exit codes: 0 read ok / 1 read failed / 3 token expired or not
REM              logged in / 4 environment not set up
REM  NOTE: keep this file ASCII-only. cmd.exe reads .bat in the OEM
REM        codepage (cp950 here), so non-ASCII text corrupts the script.
REM ===================================================================
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [!] no .venv found - creating it ...
    call "%~dp0setup.bat" || exit /b 4
)

".venv\Scripts\python.exe" -m claude_timer.core.usage %*
exit /b %ERRORLEVEL%
