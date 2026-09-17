@echo off
REM ===================================================================
REM  build.bat - build the two exes from a fresh clone, anywhere.
REM
REM    build.bat              build both
REM    build.bat resetter     build ClaudeTimerResetter.exe only
REM    build.bat resumer      build ClaudeResumer.exe only
REM
REM  ("installer" still works as an alias for "resetter".)
REM
REM  Does everything from scratch: creates .venv, installs runtime deps
REM  (requirements.txt) and build deps (requirements-build.txt), then
REM  runs PyInstaller against the checked-in spec files in packaging\.
REM  Output: dist\ClaudeTimerResetter.exe and dist\ClaudeResumer.exe
REM
REM  Requirements on the machine: Windows, Python 3.10+ on PATH as "py".
REM  NOTE: keep this file ASCII-only. cmd.exe reads .bat in the OEM
REM        codepage (cp950 here), so non-ASCII text corrupts the script.
REM ===================================================================
setlocal
cd /d "%~dp0"

set TARGET=%~1
if "%TARGET%"=="" set TARGET=all
if /i "%TARGET%"=="installer" set TARGET=resetter
if /i not "%TARGET%"=="all" if /i not "%TARGET%"=="resetter" if /i not "%TARGET%"=="resumer" (
    echo [X] unknown target "%TARGET%" - use: all ^| resetter ^| resumer
    exit /b 4
)

echo [1/3] environment ...
call "%~dp0setup.bat" || exit /b 1

echo [2/3] build dependencies ...
".venv\Scripts\python.exe" -m pip install -q -r requirements-build.txt || exit /b 1

echo [3/3] PyInstaller ...
if /i not "%TARGET%"=="resumer" (
    ".venv\Scripts\python.exe" -m PyInstaller --noconfirm ^
        --distpath dist --workpath build ^
        packaging\ClaudeTimerResetter.spec || exit /b 1
)
if /i not "%TARGET%"=="resetter" (
    ".venv\Scripts\python.exe" -m PyInstaller --noconfirm ^
        --distpath dist --workpath build ^
        packaging\ClaudeResumer.spec || exit /b 1
)

echo.
echo [OK] done. Artifacts in dist\:
if exist "dist\ClaudeTimerResetter.exe" echo      dist\ClaudeTimerResetter.exe
if exist "dist\ClaudeResumer.exe"       echo      dist\ClaudeResumer.exe
exit /b 0
