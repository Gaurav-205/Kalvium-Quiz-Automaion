@echo off
REM One-time setup on Windows: a private Python environment, kalbot, and Playwright's Chrome driver.
cd /d "%~dp0"
where python >nul 2>nul || (echo Python not found. Install Python 3.10+ from python.org and tick "Add python.exe to PATH". & exit /b 1)
if not exist .venv\Scripts\python.exe python -m venv .venv || goto :error
.venv\Scripts\python.exe -m pip install --upgrade pip || goto :error
.venv\Scripts\python.exe -m pip install -e ".[dev]" || goto :error
.venv\Scripts\python.exe -m playwright install chrome
if not exist .env copy .env.example .env >nul
if not exist config.yaml copy config.example.yaml config.yaml >nul
if not exist submissions.yaml copy submissions.example.yaml submissions.yaml >nul
echo.
echo Setup done. Next:
echo   1. notepad .env       and paste your Gemini API key after GEMINI_API_KEY=
echo   2. .\kalbot.bat doctor      checks everything
echo   3. .\kalbot.bat discover    log in once and explore (read-only)
echo   4. .\kalbot.bat run --auto --fast   complete your coursework!
exit /b 0
:error
echo Setup failed. Check the messages above.
exit /b 1
