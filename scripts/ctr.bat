@echo off
REM ===================================================================
REM  ctr.bat - ClaudeResumer command line (python -m claude_timer.cli).
REM    ctr sessions --cwd C:\path\to\project
REM    ctr resume <session> --prompt continue --at 06:00
REM    ctr new --cwd C:\path\to\project --prompt "task" --at +30
REM    ctr job <id>
REM  Add --json for programs. Full help: docs\cli.md
REM  Exit codes: 0 ok / 1 failed / 2 not found / 3 waiting / 4 bad input
REM  NOTE: keep this file ASCII-only (cmd.exe reads .bat in the OEM codepage).
REM ===================================================================
setlocal
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" (
    echo [!] no .venv found - creating it ...
    call "%~dp0setup.bat" || exit /b 4
)

".venv\Scripts\python.exe" -m claude_timer.cli %*
exit /b %ERRORLEVEL%
