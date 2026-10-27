#!/usr/bin/env bash
# Полный цикл run_full_llm_cycle.py на наборе вопросов через записывающий прокси.
#
#   scripts/llm_bench/run_variant.sh <имя-варианта> [VAR=значение ...]
#
# Переменные окружения после имени варианта уходят в прогон, например:
#   run_variant.sh baseline LLM_TIMEOUT_S=1800
#   run_variant.sh json_schema LLM_TIMEOUT_S=1800 LLM_STRUCTURED_MODE=json_schema LLM_MAX_TOKENS=1024
# Результаты: scripts/llm_bench/results/<вариант>/ (calls.jsonl, run/, stdout.log, summary.md).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PY="${PY:-$REPO/../sberAnalyticalAgent/backend/.venv/bin/python}"
QUESTIONS="${QUESTIONS:-$HERE/questions/demo_events.txt}"
CSV="${CSV:-$REPO/data/sample_datasets/demo_events.csv}"
# Свободный порт: занятый старым прокси порт молча отдал бы трафик чужому логу.
PORT="${PORT:-$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')}"

name="$1"; shift

# Реплей промптов планировщика в разных режимах structured output:
#   run_variant.sh replay <calls.jsonl> <out.jsonl> [аргументы bench_structured.py]
if [ "$name" = "replay" ]; then
    calls="$1"; outfile="$2"; shift 2
    cd "$REPO/backend"
    exec env USE_FAKE_LLM=true "$PY" "$HERE/bench_structured.py" \
        --calls "$calls" --out "$outfile" "$@"
fi

out="$HERE/results/$name"
mkdir -p "$out"
: > "$out/calls.jsonl"

python3 "$HERE/record_proxy.py" --port "$PORT" --log "$out/calls.jsonl" &
proxy=$!
trap 'kill $proxy 2>/dev/null || true' EXIT
sleep 1
# Прокси должен отвечать до старта прогона, иначе все ходы упадут с Connection error.
curl -sf "http://127.0.0.1:$PORT/api/version" > /dev/null || { echo "прокси на $PORT не отвечает"; exit 2; }

start=$(date +%s)
set +e
(cd "$REPO" && env LLM_BASE_URL="http://127.0.0.1:$PORT/v1" "$@" \
    "$PY" scripts/run_full_llm_cycle.py --csv "$CSV" --questions "$QUESTIONS" \
    --run-dir "$out/run" --no-export) > "$out/stdout.log" 2>&1
code=$?
set -e
echo "exit=$code wall=$(( $(date +%s) - start ))s" | tee -a "$out/stdout.log"
python3 "$HERE/summarize_calls.py" "$out/calls.jsonl" > "$out/summary.md" || true
