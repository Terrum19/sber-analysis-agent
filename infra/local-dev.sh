#!/usr/bin/env bash
# Полный локальный подъём в замкнутом контуре (без облака):
# 1) Ollama/vLLM с Qwen  2) backend  3) frontend
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
elif [ -f "backend/.venv/bin/activate" ]; then
  source backend/.venv/bin/activate
fi

echo "==> Backend"
(cd backend && uvicorn app.main:app --reload --port 8000) &
BACKEND_PID=$!

echo "==> Frontend"
(cd frontend && npm run dev) &
FRONTEND_PID=$!

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
