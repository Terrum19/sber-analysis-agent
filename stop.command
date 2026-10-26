#!/usr/bin/env bash
# Mac: двойной клик останавливает платформу (логика — в stop.sh).
exec "$(dirname "$0")/stop.sh"
