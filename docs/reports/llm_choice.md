# Выбор LLM для SberAnalyticalAgent (P0, 28.10.2026)

**Контур:** замкнутый, GPU ≥ 24 ГБ, vLLM, 4-bit. Критерий — ход ≤ 15 минут
(end-to-end) при сохранении качества плана и SQL.

## Кандидаты

| Модель | Параметры | Лицензия | Контекст | Профиль |
|---|---|---|---|---|
| **Qwen3-30B-A3B-Instruct-2507** | 30,5B total / 3,3B active (MoE) | Apache 2.0 | 262k | primary: лучшая latency на 24 ГБ |
| Qwen3-32B | 32,8B dense | Apache 2.0 | 32k / 131k YaRN | quality fallback, медленнее |
| Gemma 3 27B | 27,4B | Gemma license | 128k | альтернатива, 140 языков |
| gpt-oss-20b | 21B / 3,6B active | Apache 2.0 | 128k | лёгкий MoE fallback |
| Qwen2.5-Coder-14B | 14B | Apache 2.0 | 32k | Ollama/CPU fallback |

## Решение

1. **Primary:** `Qwen/Qwen3-30B-A3B-Instruct-2507` в 4-bit AWQ/GPTQ через vLLM.
   MoE даёт latency ближе к 3–7B при качестве 30B; 24 ГБ VRAM достаточно для
   `max-model-len=16384`, `max-num-seqs=4`, prefix caching.
2. **Quality fallback:** Qwen3-32B dense (4-bit) — если MoE не проходит eval по
   pass rate; включается через `LLM_MODEL`/`LLM_MODEL_PLANNER`.
3. **SQL/code:** Qwen2.5-Coder-14B (Ollama) или XiYanSQL/OmniSQL-32B после
   проверки лицензии и наличия 4-bit checkpoint.
4. **Fast role (P1):** Qwen3-4B-Instruct для инсайтов/плиток; включается
   `LLM_MODEL_FAST`.

## Замеры и гейты

Методика: `scripts/eval.py --questions data/eval/hk/questions.yaml
--columns-recall-only --repeat 3 --max-p95-s 900 --model <model>`; метрики
`done.total_ms`, `llm_calls`, `prompt_tokens` из события `done`, p50/p95 по
этапам. Железо и версия vLLM фиксируются в отчёте прогона.

| Гейт | Порог |
|---|---|
| p95 до `done` | ≤ 900 с |
| p50 до `done` | ≤ 300 с |
| pass rate | не ниже baseline −5 п.п. |
| `budget_exceeded` | ≤ 1% ходов |

## vLLM-профиль

`infra/vllm/serve.sh`:

```bash
MODEL=Qwen/Qwen3-30B-A3B-Instruct-2507 \
QUANTIZATION=awq_marlin \
MAX_MODEL_LEN=16384 \
MAX_NUM_SEQS=4 \
ENABLE_PREFIX_CACHING=true \
bash infra/vllm/serve.sh
```

`LLM_BASE_URL=http://<gpu-host>:8001/v1`, `LLM_MODEL=Qwen/Qwen3-30B-A3B-Instruct-2507`,
`LLM_NUM_CTX=16384`, `LLM_MAX_TOKENS=1536`, `LLM_STRUCTURED_MODE=json_schema`.

## Риски

- 30–40B в 24 ГБ без 4-bit не помещается — обязателен AWQ/GPTQ checkpoint.
- Dense 32B может не уложиться в p95 — primary остаётся MoE.
- vLLM без нужного `--quantization` падает на старте — проверить версию и
  checkpoint; fallback — Ollama 14B.
