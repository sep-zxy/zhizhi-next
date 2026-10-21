@echo off
setlocal
"C:\Code\zhizhi-e2e-20261020\runtime\python\python.exe" -B -s -X utf8 "%~dp0verify_release.py"
exit /b %ERRORLEVEL%
