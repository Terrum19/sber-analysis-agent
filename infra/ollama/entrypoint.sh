#!/bin/sh
# Entrypoint контейнера Ollama (docker-compose.yml, сервисы ollama-cpu и ollama-gpu).
# Поднимает сервер, при первом запуске скачивает модель $LLM_MODEL (и модель
# эмбеддингов $EMBED_MODEL, если задана) в том ollama-models, затем ждёт сервер.
# Повторные запуски модель не качают: она уже лежит в томе.
set -eu

: "${LLM_MODEL:?задайте LLM_MODEL в .env}"

with_tag() {
	case "$1" in
	*:*) printf '%s\n' "$1" ;;
	*) printf '%s:latest\n' "$1" ;;
	esac
}

has_model() {
	ollama list | awk -v name="$(with_tag "$1")" 'NR > 1 && $1 == name { found = 1 } END { exit !found }'
}

pull_missing() {
	if has_model "$1"; then
		echo "ollama: модель $1 уже скачана"
	else
		echo "ollama: скачиваю $1"
		ollama pull "$1"
	fi
}

ollama serve &
server=$!
trap 'kill -TERM "$server" 2>/dev/null; wait "$server"; exit 0' TERM INT

until ollama list >/dev/null 2>&1; do
	if ! kill -0 "$server" 2>/dev/null; then
		echo "ollama: сервер не запустился" >&2
		exit 1
	fi
	sleep 1
done

pull_missing "$LLM_MODEL"
if [ -n "${EMBED_MODEL:-}" ]; then
	pull_missing "$EMBED_MODEL"
fi

wait "$server"
