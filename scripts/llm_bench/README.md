# llm_bench — замеры агента на настоящей LLM

## Диалоги и вывод (eval для R-01)

Наборы вопросов (`questions/`) к `data/sample_datasets/demo_events.csv`:

| Файл | Что проверяет |
|---|---|
| `demo_events.txt` | 7 самостоятельных вопросов: агрегат, группировка, время, топ, статистика |
| `followups.txt` | 10 ходов: самостоятельные вопросы вперемешку с продолжениями («а из них…», «только по…») без `reply_to` |
| `replies.txt` | те же продолжения как ответы на прошлый ход (`↩` = `reply_to`, #166) и цепочка из трёх ответов |

**1. Прогон диалога** — полный цикл, каждый вызов LLM пишется в `calls.jsonl`:

```bash
# локальная Ollama
scripts/llm_bench/run_dialog.sh q7 scripts/llm_bench/questions/replies.txt LLM_MODEL=qwen2.5-coder:7b
# OpenRouter: ключ из окружения, повтор 429/5xx в прокси
UPSTREAM=https://openrouter.ai/api RETRY=12 LLM_API_KEY=... \
  scripts/llm_bench/run_dialog.sh nemotron scripts/llm_bench/questions/replies.txt \
  LLM_MODEL=nvidia/nemotron-3-nano-30b-a3b
```

**2. Планы и ответы по ходам:**

```bash
python3 scripts/llm_bench/show_turns.py scripts/llm_bench/results/nemotron/calls.jsonl
```

`[reply]` — ход-ответ (планировщик получил черновик плана), `[hist]` — обычный ход.

**3. Повтор стадии вывода** на таблицах прогона — чтобы сравнить версии
вывода без шума от планов. `--backend` — backend нужной версии (например,
worktree другой ветки):

```bash
LLM_BASE_URL=... LLM_MODEL=... python scripts/llm_bench/replay_insight.py \
  scripts/llm_bench/results/nemotron/run old.json --repeats 3 --backend ../other-worktree/backend
LLM_BASE_URL=... LLM_MODEL=... python scripts/llm_bench/replay_insight.py \
  scripts/llm_bench/results/nemotron/run new.json --repeats 3
```

**4. Оценка** — ожидаемый ответ берётся из таблицы хода, а не из эталона,
поэтому оценка не зависит от плана:

```bash
python3 scripts/llm_bench/grade_insight.py old.json new.json
```

✅ — верно, ⚠️ — верно, но голое число без слов, ❌ — неверно. Правила — в
docstring `grade_insight.py`. Оценка автоматическая и грубая: спорные
заголовки стоит просмотреть глазами в выводе по ходам.

## Структурированный вывод и потоки

- `run_variant.sh` — полный цикл на `demo_events.txt` через прокси, вариант настроек в env.
- `bench_structured.py` — реплей промптов планировщика в разных режимах structured output.
- `bench_threads.py`, `Modelfile.t14` — подбор числа потоков Ollama на CPU.
- `summarize_calls.py` — сводка по `calls.jsonl` (токены, время, ошибки).
- `record_proxy.py` — записывающий прокси; `--retry N` повторяет ошибки провайдера.
