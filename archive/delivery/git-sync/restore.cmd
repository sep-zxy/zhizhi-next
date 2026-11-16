@echo off
setlocal
"C:\Code\zhizhi-e2e-20261020\runtime\python\python.exe" -B -s -X utf8 "%~dp0restore_delivery.py"
exit /b %ERRORLEVEL%
