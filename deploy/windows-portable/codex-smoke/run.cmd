@echo off
setlocal
if "%~1"=="" goto usage
if "%~2"=="" goto usage
"C:\Code\zhizhi-e2e-20261020\runtime\python\python.exe" -I -B -X utf8 "%~dp0smoke.py" --codex-exe "%~1" --codex-home "%~2" --approve-existing-chatgpt-login
exit /b %ERRORLEVEL%
:usage
echo Usage: run.cmd "absolute existing official codex.exe" "absolute existing Codex home"
exit /b 2
