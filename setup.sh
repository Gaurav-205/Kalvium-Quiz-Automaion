#!/usr/bin/env sh
# One-time setup on macOS / Linux: a private Python environment, kalbot, and a browser for Playwright.
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Python 3.10+ is required"; exit 1; }
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
if [ "$(uname)" = "Darwin" ]; then
  .venv/bin/python -m playwright install chrome
else
  # Google Chrome needs root on Linux; Playwright's Chromium does not (set browser.channel: chromium).
  .venv/bin/python -m playwright install chromium
  echo "Linux: add  browser: {channel: chromium}  to config.yaml (or install Google Chrome)."
fi
[ -f .env ] || cp .env.example .env
echo
echo "Setup done. Next:"
echo "  1. Put your Gemini API key in .env (GEMINI_API_KEY=...)"
echo "  2. source .venv/bin/activate"
echo "  3. kalbot doctor   then   kalbot discover"
