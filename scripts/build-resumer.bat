@echo off
REM ===================================================================
REM  build-resumer.bat - double-click to build just ClaudeResumer.exe.
REM  Same as running: scripts\build.bat resumer
REM  Output: dist\ClaudeResumer.exe
REM  Keep this file ASCII-only (see note in test.bat).
REM ===================================================================
call "%~dp0build.bat" resumer
exit /b %ERRORLEVEL%
