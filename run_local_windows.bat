@echo off
cd /d %~dp0
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
if not exist .env copy .env.example .env
echo Edit .env and fill GEMINI_API_KEY before using Gemini extraction.
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
