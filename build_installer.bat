@echo off
REM ===================================================================
REM  build_installer.bat - kept for muscle memory; delegates to build.bat.
REM  Output: dist\ClaudeTimerResetter.exe
REM  Keep this file ASCII-only (see note in test.bat).
REM ===================================================================
call "%~dp0build.bat" installer
exit /b %ERRORLEVEL%
