#!/usr/bin/env bash
# Запуск платформы одним файлом (#167). Mac — двойной клик по start.command,
# Linux — ./start.sh. Нужен только Docker (Docker Desktop или Docker Engine);
# остальное скрипт делает сам и при повторном запуске пропускает готовое:
#   - запускает Docker, если он установлен, но не запущен;
#   - Mac: ставит и запускает Ollama (вне контейнера — с Metal);
#   - Linux: берёт Ollama на хосте, если она есть и видна из контейнеров,
#     иначе — Ollama в контейнере (профиль ollama, с NVIDIA — ollama-gpu);
#   - поднимает docker compose и открывает http://localhost:3000.
# Модель backend скачивает сам; прогресс — в окне чата (/api/llm/status).
# START_DRY_RUN=1 — только выбрать, где будет Ollama, без docker compose up.
set -euo pipefail
cd "$(dirname "$0")"

APP_URL=http://localhost:3000
OLLAMA_URL=http://localhost:11434
COMPOSE_MIN_MINOR=24 # Compose 2.24+: необязательный env_file
OS=$(uname -s)

say() { printf '\n==> %s\n' "$*"; }

fail() {
	printf '\nОШИБКА: %s\n' "$*" >&2
	# Окно Terminal после двойного клика не должно закрыться молча.
	if [ -t 0 ]; then read -r -p "Нажмите Enter, чтобы закрыть окно" _ || true; fi
	exit 1
}

# wait_for <секунды> <команда...> — ждать, пока команда не выполнится успешно.
wait_for() {
	local seconds=$1
	shift
	local waited=0
	until "$@" >/dev/null 2>&1; do
		((waited < seconds)) || return 1
		sleep 2
		waited=$((waited + 2))
	done
}

ensure_docker() {
	if ! command -v docker >/dev/null 2>&1; then
		[ "$OS" = Darwin ] && open https://www.docker.com/products/docker-desktop/ || true
		fail "Docker не установлен. Установите Docker Desktop: https://www.docker.com/products/docker-desktop/ — и запустите этот файл снова."
	fi
	if docker info >/dev/null 2>&1; then
		return
	fi
	if docker info 2>&1 | grep -qi "permission denied"; then
		fail "Нет прав на Docker. Выполните: sudo usermod -aG docker \$USER — затем выйдите из системы, войдите снова и повторите."
	fi
	say "Запускаю Docker"
	case $OS in
	Darwin) open -a Docker ;;
	Linux)
		if systemctl --user cat docker-desktop >/dev/null 2>&1; then
			systemctl --user start docker-desktop
		else
			sudo systemctl start docker
		fi
		;;
	esac
	wait_for 180 docker info || fail "Docker не запустился за 3 минуты. Запустите Docker Desktop вручную и повторите."
}

check_compose() {
	local version major minor
	version=$(docker compose version --short 2>/dev/null) ||
		fail "Нет docker compose v2. Обновите Docker Desktop (или поставьте пакет docker-compose-plugin)."
	IFS=. read -r major minor _ <<<"${version#v}"
	if ((major < 2 || (major == 2 && minor < COMPOSE_MIN_MINOR))); then
		fail "Нужен Docker Compose 2.$COMPOSE_MIN_MINOR или новее, сейчас $version. Обновите Docker."
	fi
}

ollama_up() { curl -fsS -m 3 "$OLLAMA_URL/api/version" >/dev/null 2>&1; }

mac_ollama_app() {
	local app
	for app in /Applications/Ollama.app "$HOME/Applications/Ollama.app"; do
		[ -d "$app" ] && printf '%s\n' "$app" && return
	done
	return 1
}

ensure_mac_ollama() {
	ollama_up && return
	local app
	if ! app=$(mac_ollama_app); then
		say "Устанавливаю Ollama"
		local tmp dest=/Applications
		[ -w "$dest" ] || dest="$HOME/Applications"
		mkdir -p "$dest"
		tmp=$(mktemp -d)
		curl -fL --progress-bar -o "$tmp/Ollama.zip" https://ollama.com/download/Ollama-darwin.zip ||
			fail "Не удалось скачать Ollama. Проверьте интернет или поставьте её с https://ollama.com/download"
		ditto -x -k "$tmp/Ollama.zip" "$dest"
		rm -rf "$tmp"
		app="$dest/Ollama.app"
	fi
	say "Запускаю Ollama"
	open "$app"
	wait_for 90 ollama_up || fail "Ollama не ответила на $OLLAMA_URL. Запустите приложение Ollama вручную и повторите."
}

# Linux: Ollama на хосте по умолчанию слушает только 127.0.0.1 — из контейнеров
# её видно, только если она слушает и адрес моста docker (OLLAMA_HOST=0.0.0.0).
host_ollama_visible() {
	local gateway
	gateway=$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null) || return 1
	[ -n "$gateway" ] && curl -fsS -m 3 "http://$gateway:11434/api/version" >/dev/null 2>&1
}

use_container_ollama() {
	local profile=ollama
	if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
		profile=ollama-gpu
	fi
	export COMPOSE_PROFILES=$profile
	export COMPOSE_LLM_BASE_URL=http://ollama:11434/v1
	say "LLM: Ollama в контейнере (профиль $profile)"
}

choose_linux_ollama() {
	if ! ollama_up && command -v ollama >/dev/null 2>&1; then
		say "Запускаю Ollama"
		if systemctl cat ollama >/dev/null 2>&1; then
			sudo systemctl start ollama
		else
			nohup ollama serve >"${TMPDIR:-/tmp}/ollama-serve.log" 2>&1 &
		fi
		wait_for 60 ollama_up || true
	fi
	if ! ollama_up; then
		use_container_ollama
		return
	fi
	if host_ollama_visible; then
		say "LLM: Ollama на этой машине ($OLLAMA_URL)"
		return
	fi
	printf '\nOllama на этой машине слушает только 127.0.0.1, контейнеры её не видят.\n'
	printf 'Чтобы работать с ней, а не с Ollama в контейнере, выполните один раз:\n'
	printf '  sudo systemctl edit ollama   # добавьте: [Service] Environment="OLLAMA_HOST=0.0.0.0"\n'
	printf '  sudo systemctl restart ollama\n'
	use_container_ollama
}

ensure_docker
check_compose
case $OS in
Darwin) ensure_mac_ollama ;;
Linux) choose_linux_ollama ;;
*) fail "Этот скрипт — для Mac и Linux. На Windows запустите start.bat." ;;
esac

if [ "${START_DRY_RUN:-0}" = 1 ]; then
	echo "dry-run: COMPOSE_PROFILES=${COMPOSE_PROFILES:-} COMPOSE_LLM_BASE_URL=${COMPOSE_LLM_BASE_URL:-}"
	exit 0
fi

say "Собираю и запускаю платформу. Первый запуск — 10–20 минут: сборка образов и модель на несколько ГБ"
docker compose up -d --build --wait --wait-timeout 3600 ||
	fail "Платформа не запустилась. Подробности: docker compose logs"

say "Готово: $APP_URL"
case $OS in
Darwin) open "$APP_URL" ;;
Linux) command -v xdg-open >/dev/null 2>&1 && xdg-open "$APP_URL" >/dev/null 2>&1 || true ;;
esac
echo "Модель при первом запуске ещё может скачиваться — статус виден над чатом."
echo "Остановить платформу: stop.sh (Mac — stop.command)."
