# SberAnalyticalAgent

**AI-Native BI-платформа**: диалоговый агент на базе LLM, позволяющий
продуктовым аналитикам и руководителям через естественный язык исследовать
данные, получать визуализации и собирать дашборды — без участия разработчика.

Работает в **замкнутом контуре**: локальная LLM (Qwen 2.5-Coder через
Ollama/vLLM), DuckDB как единый SQL-движок, WASM-песочница для кодогенерации.
Никаких облачных сервисов внутри.

## Закрываемые сценарии

1. Первичное знакомство с неизвестными данными («Что передо мной?»)
2. Ответы на вопросы по содержанию («Какие значения встречаются чаще?»)
3. Группировки и аномалии («Что происходит с X каждый июль?»)
4. Авто-визуализация («Покажи наглядно, как это распределено»)
5. Дашборды по запросу («Создай дашборд с ежеквартальной статистикой»)
6. Диалог с контекстом («А теперь то же самое в другом виде»)

## Быстрый старт (локально)

```bash
# 1) LLM (замкнутый контур): Ollama или vLLM; контекст — как LLM_NUM_CTX в .env
ollama pull qwen2.5-coder:14b && OLLAMA_CONTEXT_LENGTH=16384 ollama serve
# или GPU: MODEL=Qwen/Qwen2.5-Coder-14B-Instruct bash infra/vllm/serve.sh

# 2) Зависимости
cd backend && pip install -r requirements.txt && cd ..
cd frontend && npm install && cd ..

# 3) Демо-данные
python scripts/seed_demo_data.py

# 4) Запуск
bash infra/local-dev.sh
# backend: http://localhost:8000  frontend: http://localhost:3000
```

Или одной командой в Docker — Ollama с моделью поднимается вместе с платформой:

```bash
cp .env.example .env
make up        # Ollama на CPU, модель LLM_MODEL
make up-gpu    # или Ollama на GPU NVIDIA (нужен NVIDIA Container Toolkit), модель LLM_MODEL_GPU
make down
```

Нужны Docker Engine 25+ и Docker Compose 2.20+ (`start_interval` в healthcheck,
`depends_on.required`): `docker version`, `docker compose version`.

Первый запуск скачивает модель в том `ollama-models` (несколько минут, несколько ГБ);
backend ждёт, пока Ollama не станет healthy: скачаны `LLM_MODEL` и `EMBED_MODEL`,
если он задан. Дальше модели берутся из тома. Ollama доступна только с этой
машины: `http://localhost:${OLLAMA_PORT:-11434}`.

Переменные окружения перекрывают `.env`: `LLM_MODEL_GPU=qwen2.5-coder:32b make up-gpu`.

### Длина контекста LLM

Промпт планировщика — около 12 тыс. символов, это 4–5 тыс. токенов. У Ollama
контекст по умолчанию 4096 токенов, и не влезшее начало промпта отбрасывается
без ошибки: модель не видит список инструментов. Контекст задаётся на сервере
и должен быть не меньше `LLM_NUM_CTX` (по умолчанию 16384):

- Ollama, запущенная руками: `OLLAMA_CONTEXT_LENGTH=16384 ollama serve`.
- Ollama как служба systemd: `sudo systemctl edit ollama`, в `[Service]` —
  `Environment="OLLAMA_CONTEXT_LENGTH=16384"`, затем `sudo systemctl restart ollama`.
- Или модель с контекстом в Modelfile: `FROM qwen2.5-coder:14b` и
  `PARAMETER num_ctx 16384`, `ollama create qwen2.5-coder-16k -f Modelfile`,
  `LLM_MODEL=qwen2.5-coder-16k`.
- vLLM: `infra/vllm/serve.sh` передаёт `--max-model-len` (`MAX_MODEL_LEN`, по
  умолчанию `LLM_NUM_CTX`).

`options.num_ctx` в запросе к OpenAI-совместимому `/v1` Ollama игнорирует. Проверка:
`python3 scripts/check_llm_client.py` отправляет промпт 12 тыс. символов с паролем
в начале и печатает `prompt_tokens` и виден ли пароль. Бэкенд при первом ответе
спрашивает контекст у сервера и пишет warning, если он меньше `LLM_NUM_CTX`, а
каждый обрезанный промпт — warning «Промпт обрезан сервером LLM».

## Структура репозитория

Подробности — в [docs/repo_structure.md](docs/repo_structure.md).

```
backend/    FastAPI + ядро (агент, DuckDB, тулы, память, визуализация)
frontend/   Next.js + React + Tailwind (чат + канвас + дашборды)
infra/      локальный запуск, vLLM, nginx
scripts/    seed-данные, скачивание модели, миграции
docs/       архитектура, промпты, память, коннекторы, roadmap
data/       демо-датасеты, бизнес-глоссарий, hk_datasets/ (датасеты хакатона, вне git)
```

## Тесты

```bash
cd backend && pytest
```

## Технологии

Qwen 2.5-Coder (Ollama/vLLM) · DuckDB · FastAPI · Instructor + Pydantic ·
WASM-песочница · React/Next.js + Zustand · Vega-Lite/ECharts · openpyxl + Typst/ReportLab
