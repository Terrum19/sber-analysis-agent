#!/usr/bin/env bash
# Остановить платформу, включая Ollama в контейнере. Данные и модели остаются.
set -euo pipefail
cd "$(dirname "$0")"
docker compose --profile ollama --profile ollama-gpu --profile integration down
echo "Платформа остановлена."
