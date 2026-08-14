@echo off
cd /d %~dp0
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
if not exist .env copy .env.example .env
echo Configure Vertex AI DeepSeek or DEEPSEEK_API_KEY in .env before extraction.
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
