@echo off
REM One-time setup on Windows: Python packages + Playwright's Chrome driver.
cd /d "%~dp0"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chrome
echo.
echo Setup done. Set your key once (then open a NEW terminal):
echo     setx GEMINI_API_KEY "your-key-here"
echo Then run:  python main.py --discover
