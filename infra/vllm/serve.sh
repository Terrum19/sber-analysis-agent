#!/usr/bin/env bash
# Запуск Qwen через vLLM (GPU) в OpenAI-совместимом режиме.
# Альтернатива для CPU/NPU: Ollama (ollama run qwen2.5-coder:14b).
set -euo pipefail

# Целевая модель GPU-профиля: MoE 30B (3.3B active) в 4-bit.
MODEL="${MODEL:-Qwen/Qwen3-30B-A3B-Instruct-2507}"
GPU_MEM="${GPU_MEM:-0.90}"
# Контекст в токенах; должен совпадать с LLM_NUM_CTX бэкенда (#102).
MAX_MODEL_LEN="${MAX_MODEL_LEN:-${LLM_NUM_CTX:-16384}}"
# Prefix caching переиспользует общий префикс промпта планировщика/тулов.
ENABLE_PREFIX_CACHING="${ENABLE_PREFIX_CACHING:-true}"
# Сколько запросов одновременно держит vLLM (плитки дашборда идут параллельно).
MAX_NUM_SEQS="${MAX_NUM_SEQS:-4}"
# 4-bit checkpoint: awq_marlin / gptq_marlin / bitsandbytes; пусто — как в checkpoint.
QUANTIZATION="${QUANTIZATION:-}"

ARGS=(
  --model "$MODEL"
  --gpu-memory-utilization "$GPU_MEM"
  --max-model-len "$MAX_MODEL_LEN"
  --max-num-seqs "$MAX_NUM_SEQS"
  --port "${PORT:-8001}"
)
if [ "$ENABLE_PREFIX_CACHING" = "true" ]; then
  ARGS+=(--enable-prefix-caching)
fi
if [ -n "$QUANTIZATION" ]; then
  ARGS+=(--quantization "$QUANTIZATION")
fi

exec python -m vllm.entrypoints.openai.api_server "${ARGS[@]}"
