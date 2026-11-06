@echo off
rem Double-click to open Minecraft Font Builder (Windows).
rem The first start sets up a Python environment in the .venv folder.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>nul || (echo Minecraft Font Builder needs Python 3.10 or newer: https://www.python.org/downloads/ & pause & exit /b 1)
  echo Setting up ^(only the first time, about a minute^)...
  py -3 -m venv .venv || (pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet . || (rmdir /s /q .venv & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m mcfont gui %*
pause