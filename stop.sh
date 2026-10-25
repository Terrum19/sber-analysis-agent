#!/usr/bin/env bash
# Остановить платформу, включая Ollama в контейнере и Ollama, которую запустил
# start.sh (её метка — .ollama-started). Ollama, запущенную до start.sh, не трогаем.
# Данные и модели остаются.
set -euo pipefail
cd "$(dirname "$0")"
docker compose --profile ollama --profile ollama-gpu --profile integration down
if [ -f .ollama-started ]; then
	read -r how pid <.ollama-started || true
	case ${how:-} in
	app) osascript -e 'quit app "Ollama"' || true ;;
	service)
		if [ -d /run/systemd/system ]; then
			sudo systemctl stop ollama || true
		else
			sudo service ollama stop || true
		fi
		;;
	pid) ps -p "$pid" -o comm= 2>/dev/null | grep -q ollama && kill "$pid" || true ;;
	esac
	rm -f .ollama-started
	echo "Ollama остановлена."
fi
echo "Платформа остановлена."
