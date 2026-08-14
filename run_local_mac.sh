#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
source .venv/bin/activate
pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
echo "Configure Vertex AI DeepSeek or DEEPSEEK_API_KEY in .env before extraction."
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
