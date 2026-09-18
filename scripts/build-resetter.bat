@echo off
REM ===================================================================
REM  build-resetter.bat - double-click to build just ClaudeTimerResetter.exe.
REM  Same as running: scripts\build.bat resetter
REM  Output: dist\ClaudeTimerResetter.exe
REM  Keep this file ASCII-only (see note in test.bat).
REM ===================================================================
call "%~dp0build.bat" resetter
exit /b %ERRORLEVEL%
