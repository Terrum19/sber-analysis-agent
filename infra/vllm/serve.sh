#!/usr/bin/env bash
# Запуск Qwen через vLLM (GPU) в OpenAI-совместимом режиме.
# Альтернатива для CPU/NPU: Ollama (ollama run qwen2.5-coder:14b).
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen2.5-Coder-14B-Instruct}"
GPU_MEM="${GPU_MEM:-0.85}"
# Контекст в токенах; должен совпадать с LLM_NUM_CTX бэкенда (#102).
MAX_MODEL_LEN="${MAX_MODEL_LEN:-${LLM_NUM_CTX:-16384}}"

exec python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --gpu-memory-utilization "$GPU_MEM" \
  --max-model-len "$MAX_MODEL_LEN" \
  --port 8001
