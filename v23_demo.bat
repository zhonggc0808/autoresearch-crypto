@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_v2_3_combo_demo.ps1" %*
exit /b %ERRORLEVEL%
