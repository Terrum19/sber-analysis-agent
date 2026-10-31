#!/usr/bin/env bash
# Скачивание весов Qwen 2.5-Coder в models/ (вне git, крупные бинарники).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p models

echo "Вариант 1 (рекомендуется): Ollama"
echo "  ollama pull qwen2.5-coder:14b"
echo "  OLLAMA_CONTEXT_LENGTH=16384 ollama serve   # контекст = LLM_NUM_CTX, см. README"
echo "Вариант 2: HuggingFace (git-lfs)"
echo "  huggingface-cli download Qwen/Qwen2.5-Coder-14B-Instruct"
echo "Вариант 3: квантование под слабое железо (GGUF)"
echo "  huggingface-cli download Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
