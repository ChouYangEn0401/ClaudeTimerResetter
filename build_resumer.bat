@echo off
REM ===================================================================
REM  build_resumer.bat - kept for muscle memory; delegates to build.bat.
REM  Output: dist\ClaudeResumer.exe
REM  Keep this file ASCII-only (see note in test.bat).
REM ===================================================================
call "%~dp0build.bat" resumer
exit /b %ERRORLEVEL%
