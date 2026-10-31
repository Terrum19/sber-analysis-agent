#!/usr/bin/env bash
# Диалог (файл вопросов) полным циклом через записывающий прокси.
#
#   scripts/llm_bench/run_dialog.sh <имя> <файл-вопросов> [VAR=значение ...]
#
# Вопросы одного файла — одна сессия; вопрос с «↩» отвечает на прошлый ход
# (reply_to). Провайдер — UPSTREAM (по умолчанию локальная Ollama), ключ —
# LLM_API_KEY из окружения. Для OpenRouter:
#   UPSTREAM=https://openrouter.ai/api RETRY=12 LLM_API_KEY=... \
#     run_dialog.sh nemotron questions/replies.txt LLM_MODEL=nvidia/nemotron-3-nano-30b-a3b
# Результаты: scripts/llm_bench/results/<имя>/ (calls.jsonl, run/, stdout.log).
# Дальше: show_turns.py — планы и ответы по ходам, replay_insight.py — повтор вывода.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PY="${PY:-$REPO/backend/.venv/bin/python}"
CSV="${CSV:-$REPO/data/sample_datasets/demo_events.csv}"
UPSTREAM="${UPSTREAM:-http://localhost:11434}"
RETRY="${RETRY:-0}"

name="$1"; questions="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; shift 2
out="$HERE/results/$name"
# Свежий каталог: память сессии и DuckDB прошлого прогона попали бы в контекст.
rm -rf "$out"
mkdir -p "$out"
: > "$out/calls.jsonl"

port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
python3 "$HERE/record_proxy.py" --port "$port" --upstream "$UPSTREAM" --retry "$RETRY" \
    --log "$out/calls.jsonl" &
proxy=$!
trap 'kill $proxy 2>/dev/null || true' EXIT
sleep 1
curl -sf "http://127.0.0.1:$port/v1/models" > /dev/null || { echo "прокси на $port не отвечает"; exit 2; }

start=$(date +%s)
set +e
(cd "$REPO" && env LLM_BASE_URL="http://127.0.0.1:$port/v1" "$@" \
    "$PY" scripts/run_full_llm_cycle.py --csv "$CSV" --table "$(basename "$CSV" .csv)" \
    --questions "$questions" --run-dir "$out/run" --no-export) > "$out/stdout.log" 2>&1
code=$?
set -e
echo "exit=$code wall=$(( $(date +%s) - start ))s" | tee -a "$out/stdout.log"
