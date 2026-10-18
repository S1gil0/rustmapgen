@echo off
rem Double-click to install: runs install.ps1 without changing the system's script policy.
echo RustMapGen installer: the first install takes a few minutes. Each step is shown as it starts.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
