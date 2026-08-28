@echo off
REM ===================================================================
REM  test.bat - cheapest possible "is Claude usable right now" check.
REM    model      : claude-haiku-4-5-20251001 (cheapest Claude model)
REM    tools      : none  (plain text in/out - agent mode is 15-45x)
REM    thinking   : off   (MAX_THINKING_TOKENS=0)
REM    permission : manual
REM    prompt     : "hi"
REM  Cost ~ $0.0004 per run, ~6s.
REM
REM  Exit codes: 0 usable / 1 call failed / 2 claude not found
REM              3 not logged in / 4 environment not set up
REM  NOTE: keep this file ASCII-only. cmd.exe reads .bat in the OEM
REM        codepage (cp950 here), so non-ASCII text corrupts the script.
REM ===================================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [!] no .venv found - creating it ...
    call "%~dp0setup.bat" || exit /b 4
)

".venv\Scripts\python.exe" ping.py %*
exit /b %ERRORLEVEL%
