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
# 1) LLM (замкнутый контур): Ollama или vLLM
ollama pull qwen2.5-coder:14b && ollama serve
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

Первый запуск скачивает модель в том `ollama-models` (несколько минут, несколько ГБ);
backend ждёт, пока Ollama не станет healthy. Дальше модель берётся из тома.

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
