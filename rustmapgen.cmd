@echo off
rem Double-click to start the panel, or run any rustmapgen command: rustmapgen.cmd generate --recipe large
rem The panel ends with 3 once it has updated itself: it is started again here. This file never changes in an
rem update (Windows reads a running batch file as it goes), so what it does is kept to the least.
setlocal
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Not installed yet: run install.cmd first.
  popd
  pause
  exit /b 1
)
set RUSTMAPGEN_LAUNCHER=1
:again
if "%~1"=="" (
  ".venv\Scripts\python.exe" -m rustmapgen panel
) else (
  ".venv\Scripts\python.exe" -m rustmapgen %*
)
set "code=%errorlevel%"
if not "%code%"=="3" goto done
if "%~1"=="" goto again
if /i "%~1"=="panel" goto again
:done
popd
exit /b %code%
