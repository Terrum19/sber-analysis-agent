# Исследование агентного цикла SberAnalyticalAgent (28.10.2026)

**Цель:** понять, почему текущий агент не гарантирует ответ за 15 минут на модели ≤40B, что делают лидеры и какие изменения дают максимальный эффект.

**Связанный план:** [`implementation_plan.md`](../../implementation_plan.md).

---

## 1. Краткий вывод

1. Архитектура агента сильнее большинства открытых BI-агентов: plan-and-execute, типизированные тулы, `temp_result_vN`, паспорт данных, recovery, SSE-прогресс, eval, дашборды.
2. Главная проблема — **нет общего бюджета хода**. `LLM_TIMEOUT_S=600`, `LLM_MAX_RETRIES=1`, до 2 попыток structured output, `PLAN_FIX_ATTEMPTS=1`, `STEP_FIX_ATTEMPTS=2`, `MAX_REPLANS=2`. Один план теоретически может стоить 20–40 минут.
3. `AGENT_MAX_STEPS` не используется, `LLM_MAX_TOKENS=None`, SQL выполняется синхронно в event loop, таймаута SQL нет.
4. 40 тулов занимают ~9,7 тыс. символов промпта; `AGENT_TOOL_TOPK` выключен после регресса; schema linking лексический, без эмбеддингов.
5. Семантического слоя нет: только глоссарий и названия колонок. Это причина неоднозначности «выручка/клиенты/возвраты» и регресса `revenue_analysis` без `date_column`.
6. P0 должен быть **deadline-first**: бюджет, лимиты, SQL вне event loop, preflight, быстрые пути, параллельные плитки, vLLM-профиль, метрики.
7. P1 — качество: semantic layer, DAG, typed schemas, retrieval, model pool, cache, расширенный eval.

---

## 2. Текущий механизм

```
вопрос
  -> small talk / overview / chart_morph (без LLM)
  -> SessionMemory.build_context
  -> StepPlanner.plan (LLM, ResearchPlan)
       tools = tool_registry.describe()  # 40 тулов, ~9,7 тыс. символов
       columns = semantic_select top-5   # лексические основы слов
       history = последние 3 успешных хода / reply_to
  -> validate_plan (имена тулов, ссылки, parse_args)
  -> StepExecutor (последовательно)
       tool.arun -> DuckDBManager.query/save_step_result (sync)
       ошибка -> StepPlanner.fix_step (LLM) до 2 раз
       lookup -> replan (LLM) до 2 раз
  -> InsightGenerator.generate (LLM)
       первые 50 строк + SQL-факты + шаги плана
  -> chart_selector + vega_spec_builder (детерминированно)
  -> DashboardGenerator (плитки; insight_needed -> LLM последовательно)
  -> SSE progress/text/chart/dashboard/done
```

### Сильные стороны

- Plan-and-execute вместо ReAct: меньше вызовов, лучше предсказуемость.
- Типизированные тулы, SQL-валидация (`identifier`, `literal`, `expression`, `_forbidden`).
- JSON schema вместо instructor TOOLS: эксперимент V2 дал ×3,2 по времени и 44→16 вызовов.
- Факты считает SQL, а не LLM (H-Q1).
- Паспорт данных и семантический отбор колонок.
- `chart_morph` без LLM.
- SSE-прогресс, recovery, `pending_recovery`, `reply_to`.

---

## 3. Проблемы

### P0 — критично для 15 минут

| ID | Проблема | Доказательство | Влияние |
|---|---|---|---|
| A | Нет общего дедлайна | `llm_timeout_s=600`, retries, 2 попытки structured, fix/replan | 20–40 мин на ход |
| B | Нет лимитов шагов/токенов/вызовов | `agent_max_steps` не используется, `llm_max_tokens=None` | бесконечный план/генерация |
| C | SQL в event loop, без timeout | `BaseTool.arun` → sync `run`; `DuckDBManager` lock; `statement_timeout` не поддерживается в DuckDB 1.5.6 | SSE/все сессии блокируются |
| D | Поздняя валидация | `arguments: dict`, schema не знает колонок; `validate_plan` без схемы | ошибка → дорогой `fix_step` |
| E | 40 тулов, лексический linking | 9 680 символов; `agent_tool_topk=False` | prefill доминирует, неверные тулы |
| F | Нет быстрых путей | всё идёт в planner + insight | 30–180 с на простой вопрос |
| G | Последовательные плитки | `_dashboard_tile` в цикле | +20–120 с |
| H | Конфиг/зависимости/модель | `.env` `json_object` → Settings падает; URL `/chat/completions`; 7B/14B/27B; нет Pillow | чистый запуск/PDF падают |
| I | Нет бюджетной наблюдаемости | TurnLog без llm_calls/tokens; eval без p95-гейта | нельзя доказать 15 минут |

### P1 — качество

| ID | Проблема | Влияние |
|---|---|---|
| A | Нет semantic layer | метрики/измерения/связи трактуются заново |
| B | Линейный план, нет DAG | независимые ветки последовательны |
| C | Одна модель на всё | 30–40B тратится на простые вызовы |
| D | Нет кешей | повторный prefill/расчёты |
| E | Multi-table/ambiguity/security | join и уточнения не управляются |

---

## 4. Что делают лидеры

| Источник | Идея | Результат | Что взять |
|---|---|---|---|
| CHESS | Schema selector, IR, candidate generator, unit tester | 71,10% BIRD, −83% вызовов, ×5 токенов | schema pruning + validation, не 4 агента на вопрос |
| XiYan-SQL | M-Schema, multi-generator, refiner, selection | 75,63% BIRD; 32B 69,03% | компактная схема, refiner для трудных |
| Alpha-SQL | MCTS, 32B zero-shot | 69,7% BIRD | test-time search только для трудных |
| OmniSQL | SynSQL-2.5M, 7/14/32B | сопоставим с GPT-4o | доменный fine-tune — P2 |
| Spider 2.0 | 632 enterprise-задачи | o1-preview 21,3% | реалистичный eval |
| WrenAI | MDL, dry-plan validation, AI context layer | — | semantic manifest + dry-run |
| SuperSonic | semantic layer + rule/LLM parser + corrector | — | гибридный парсер и корректор |
| LLMCompiler | planner + task fetching + parallel executor | 3,7× latency, 6,7× cost, +9% | DAG и параллелизм |
| ReWOO | plan without observations | 5× токенов, +4% | plan-once, не ReAct |
| Anthropic | простейшее решение, routing, ACI | — | routing и простые workflow |
| 12-factor agents | own context, structured outputs, compact errors | — | компактные ошибки и тулы |
| FrugalGPT / RouteLLM | каскад моделей | 98% / >2× cost | fast + planner модель |
| vLLM | APC, structured outputs, spec decode | — | prefix caching, JSON schema, 4-bit |

### Модели ≤40B

- **Qwen3-30B-A3B-Instruct-2507** — 30,5B total / 3,3B active, 262k context; лучший latency/quality для 15 минут.
- **Qwen3-32B** — 32,8B, 32k native, hybrid thinking; quality fallback.
- **Gemma 3 27B** — 27,4B, 128k, 140 языков.
- **gpt-oss-20b** — 21B total / 3,6B active, 128k; быстрый, но русский слабее.
- **Qwen2.5-Coder-32B / XiYanSQL-32B / OmniSQL-32B** — SQL-специалисты для P2.

---

## 5. Рекомендации

### P0

1. Turn budget 870 с + stage-бюджеты + graceful degradation.
2. `LLM_MAX_TOKENS=1536`, `AGENT_MAX_STEPS=8`, `AGENT_MAX_LLM_CALLS=6`, `STEP_FIX_ATTEMPTS=1`, `MAX_REPLANS=1`.
3. SQL в `asyncio.to_thread`, `DuckDBManager.interrupt()`, query timeout.
4. Preflight колонок, enum тулов, `maxItems`, time-hint validation.
5. Router для простых вопросов + шаблонный инсайт одной ячейки.
6. Параллельные инсайты плиток.
7. vLLM prefix caching, 4-bit, `max-model-len 16384`, `max-num-seqs 4`.
8. Конфиг/зависимости/health: `json_object`, нормализация URL, Pillow, единая модель.
9. TurnMetrics + eval-гейт p95 ≤900 с.

### P1

1. Semantic layer: metrics, dimensions, time grains, joins, synonyms + dry-run.
2. DAG-план и параллельный executor.
3. Typed tool schemas + embeddings/reranker.
4. Model pool: fast + planner + SQL.
5. Plan/result/semantic cache.
6. Eval ≥150 вопросов + observability.
7. Multi-table/ambiguity/prompt injection.

---

## 6. Источники

- CHESS: [GitHub](https://github.com/ShayanTalaei/CHESS), [arXiv:2405.16755](https://arxiv.org/abs/2405.16755)
- XiYan-SQL: [GitHub](https://github.com/XGenerationLab/XiYan-SQL), [arXiv:2411.08599](https://arxiv.org/abs/2411.08599)
- Alpha-SQL: [arXiv:2502.17248](https://arxiv.org/abs/2502.17248)
- OmniSQL: [arXiv:2503.02240](https://arxiv.org/abs/2503.02240)
- Spider 2.0: [arXiv:2411.07763](https://arxiv.org/abs/2411.07763)
- WrenAI: [Canner/WrenAI](https://github.com/Canner/WrenAI)
- SuperSonic: [tencentmusic/supersonic](https://github.com/tencentmusic/supersonic)
- DB-GPT: [eosphoros-ai/DB-GPT](https://github.com/eosphoros-ai/DB-GPT)
- Dataherald: [Dataherald/dataherald](https://github.com/Dataherald/dataherald)
- Vanna: [vanna-ai/vanna](https://github.com/vanna-ai/vanna) (архивирован)
- LLMCompiler: [arXiv:2312.04511](https://arxiv.org/abs/2312.04511)
- ReWOO: [arXiv:2305.18323](https://arxiv.org/abs/2305.18323)
- Anthropic: [Building effective agents](https://www.anthropic.com/research/building-effective-agents)
- 12-factor agents: [humanlayer/12-factor-agents](https://github.com/humanlayer/12-factor-agents)
- FrugalGPT: [arXiv:2305.05176](https://arxiv.org/abs/2305.05176)
- RouteLLM: [arXiv:2406.18665](https://arxiv.org/abs/2406.18665)
- vLLM APC: [docs](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching.html)
- vLLM Structured Outputs: [docs](https://docs.vllm.ai/en/latest/features/structured_outputs.html)
- Qwen3-32B: [HF](https://huggingface.co/Qwen/Qwen3-32B)
- Qwen3-30B-A3B-Instruct-2507: [HF](https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507)
- gpt-oss-20b: [HF](https://huggingface.co/openai/gpt-oss-20b)
- Gemma 3 27B: [HF](https://huggingface.co/google/gemma-3-27b-it)
- InfiAgent-DABench: [arXiv:2401.05507](https://arxiv.org/abs/2401.05507)
- Data Interpreter: [arXiv:2402.18679](https://arxiv.org/abs/2402.18679)
- DSBench: [arXiv:2409.07703](https://arxiv.org/abs/2409.07703)
- DeepAnalyze: [arXiv:2510.16872](https://arxiv.org/abs/2510.16872)


