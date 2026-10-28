#!/usr/bin/env bash
# Mac: двойной клик открывает Terminal и запускает платформу (логика — в start.sh).
exec "$(dirname "$0")/start.sh"
