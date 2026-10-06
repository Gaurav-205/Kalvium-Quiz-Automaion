@echo off
REM Runs kalbot from this folder's virtual environment (created by setup.bat).
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Run setup.bat first.
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m kalbot %*
