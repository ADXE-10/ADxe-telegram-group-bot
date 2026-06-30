@echo off
setlocal

if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found.
  echo Run: python -m venv .venv
  echo Then: .venv\Scripts\pip install -r requirements.txt
  exit /b 1
)

if not exist ".env" (
  echo .env not found. Copy .env.example to .env and set TELEGRAM_BOT_TOKEN.
  exit /b 1
)

set "PYTHONPATH=%CD%\src"
".venv\Scripts\python.exe" -m group_bot.bot
