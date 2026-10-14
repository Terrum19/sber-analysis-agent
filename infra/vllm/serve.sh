#!/usr/bin/env bash
# Запуск Qwen через vLLM (GPU) в OpenAI-совместимом режиме.
# Альтернатива для CPU/NPU: Ollama (ollama run qwen2.5-coder:14b).
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen2.5-Coder-14B-Instruct}"
GPU_MEM="${GPU_MEM:-0.85}"

exec python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --gpu-memory-utilization "$GPU_MEM" \
  --port 8001
