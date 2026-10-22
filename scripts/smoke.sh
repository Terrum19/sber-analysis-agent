#!/usr/bin/env bash
# Дымовой тест чистого запуска (#79): CSV -> сессия -> чат (SSE до done) -> фронтенд.
# Стек уже поднят: в CI — docker compose с docker-compose.ci.yml (USE_FAKE_LLM=true).
# Переменные: BACKEND_URL, FRONTEND_URL, SMOKE_CSV, SMOKE_TIMEOUT (секунды на
# ожидание /health и на ответ чата), SKIP_FRONTEND=1 — не проверять фронтенд.
set -euo pipefail

BACKEND_URL=${BACKEND_URL:-http://localhost:8000}
FRONTEND_URL=${FRONTEND_URL:-http://localhost:3000}
SMOKE_TIMEOUT=${SMOKE_TIMEOUT:-120}
SKIP_FRONTEND=${SKIP_FRONTEND:-0}
root=$(cd "$(dirname "$0")/.." && pwd)
SMOKE_CSV=${SMOKE_CSV:-$root/data/sample_datasets/demo_events.csv}

fail() {
	echo "smoke: ОШИБКА: $*" >&2
	exit 1
}

json_field() {
	python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"
}

echo "smoke: жду $BACKEND_URL/health"
deadline=$((SECONDS + SMOKE_TIMEOUT))
until curl -fsS "$BACKEND_URL/health" >/dev/null 2>&1; do
	((SECONDS < deadline)) || fail "бэкенд не ответил на /health за ${SMOKE_TIMEOUT} с"
	sleep 2
done

echo "smoke: загружаю $(basename "$SMOKE_CSV")"
datasource=$(curl -fsS -F "files=@$SMOKE_CSV;type=text/csv" "$BACKEND_URL/api/datasources/csv") ||
	fail "POST /api/datasources/csv"
table=$(python3 -c 'import json, sys; print(json.load(sys.stdin)["tables"][0])' <<<"$datasource") ||
	fail "в ответе нет tables[0]: $datasource"
echo "smoke: таблица $table"

session=$(curl -fsS -H 'Content-Type: application/json' \
	-d "{\"table\": \"$table\"}" "$BACKEND_URL/api/sessions") ||
	fail "POST /api/sessions"
session_id=$(json_field session_id <<<"$session") || fail "в ответе нет session_id: $session"
echo "smoke: сессия $session_id"

echo "smoke: спрашиваю чат"
stream=$(mktemp)
trap 'rm -f "$stream"' EXIT
curl -fsSN --max-time "$SMOKE_TIMEOUT" -H 'Content-Type: application/json' \
	-d "{\"session_id\": \"$session_id\", \"question\": \"Сколько всего строк в таблице?\"}" \
	"$BACKEND_URL/api/chat" >"$stream" || fail "POST /api/chat оборвался (curl $?)"
if grep -q '^data: {"type":"error"' "$stream"; then
	cat "$stream" >&2
	fail "чат прислал событие error"
fi
grep -q '^data: {"type":"done"' "$stream" || {
	cat "$stream" >&2
	fail "в потоке чата нет события done"
}
echo "smoke: чат ответил ($(grep -c '^data: ' "$stream") событий)"

if [[ $SKIP_FRONTEND != 1 ]]; then
	code=$(curl -s -o /dev/null -w '%{http_code}' "$FRONTEND_URL/")
	[[ $code == 200 ]] || fail "фронтенд $FRONTEND_URL/ вернул HTTP $code"
	echo "smoke: фронтенд HTTP 200"
fi

echo "smoke: OK"
