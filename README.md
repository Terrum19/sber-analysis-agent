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

## Запуск

Нужна одна программа — [Docker Desktop](https://www.docker.com/products/docker-desktop/)
(на Linux подойдёт и Docker Engine с Compose 2.24+). Всё остальное делает скрипт запуска:

| Система | Что сделать |
|---|---|
| Mac | двойной клик по `start.command` |
| Windows | двойной клик по `start.bat` |
| Linux | `./start.sh` |

Скрипт сам запускает Docker, если тот не запущен, ставит и запускает
[Ollama](https://ollama.com) (вне контейнера: на Mac она работает на Metal, на
Windows — на видеокарте), поднимает платформу и открывает http://localhost:3000.
Повторный запуск пропускает то, что уже установлено и работает. Остановить —
`stop.command` / `stop.bat` / `./stop.sh` (данные и модель сохраняются). Ollama,
которую запустил скрипт, тоже останавливается; запущенную до него — нет.

Первый запуск — 10–20 минут и нужен интернет: собираются образы, а модель
(`qwen2.5-coder:7b`, около 5 ГБ) скачивает сама платформа. Пока модель
скачивается, над чатом виден прогресс; данные загружать уже можно. Памяти
рекомендуется от 16 ГБ: модели 7B самой нужно около 6 ГБ.

На Mac при первом открытии `start.command` система может спросить, доверяете
ли вы файлу: правый клик → «Открыть». На Linux скрипт берёт Ollama на этой
машине, а если её нет — ставит официальным установщиком (спросит пароль sudo)
и запускает на адресе моста docker: контейнерам её видно, локальной сети — нет.
Уже запущенную службу берёт, если она видна из контейнеров, и сам её не
перенастраивает. Если хостовую Ollama контейнерам не видно — Ollama в контейнере
(с NVIDIA Container Toolkit — на GPU и с моделью `LLM_MODEL_GPU`), и скрипт
говорит об этом.
Службы запускаются и без systemd (`service`).

## Для разработчиков

Модель для платформы готовит backend (`core/llm/ollama_setup.py`): при старте
скачивает `LLM_MODEL`, если её нет на сервере Ollama, и работает с её вариантом
с контекстом `LLM_NUM_CTX` (`<модель>-ctx<число>`); состояние — `GET /api/llm/status`.
Отключить — `LLM_PREPARE_MODEL=false`. `.env` не обязателен: значения по
умолчанию — в `docker-compose.yml` и `backend/app/config.py`, пример — `.env.example`.

```bash
make up          # docker compose, Ollama на хосте (http://host.docker.internal:11434)
make up-ollama   # Ollama в контейнере на CPU (профиль ollama)
make up-gpu      # Ollama в контейнере на GPU NVIDIA (профиль ollama-gpu), модель LLM_MODEL_GPU
make down
```

Свой сервер LLM (vLLM, GPU-машина в сети) для compose: `COMPOSE_LLM_BASE_URL` в
`.env`, например `http://10.0.0.5:8001/v1` (`LLM_BASE_URL` — только для `make dev`:
в контейнере `localhost` — сам backend). Ollama в контейнере доступна с этой
машины на `http://localhost:${OLLAMA_PORT:-11435}`. Демо-PostgreSQL и ClickHouse
для интеграционных тестов — профиль `integration`.

Нужны Docker Engine 25+ и Docker Compose 2.24+ (`start_interval` в healthcheck,
`depends_on.required`, необязательный `env_file`): `docker compose version`.

Без Docker:

```bash
# 1) LLM (замкнутый контур): Ollama или vLLM; контекст backend задаст сам (ниже)
ollama serve
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

Переменные окружения перекрывают `.env`: `LLM_MODEL_GPU=qwen2.5-coder:32b make up-gpu`.

### Длина контекста LLM

Промпт планировщика — около 12 тыс. символов, это 4–5 тыс. токенов. У Ollama
контекст по умолчанию 4096 токенов, и не влезшее начало промпта отбрасывается
без ошибки: модель не видит список инструментов. С Ollama backend решает это сам:
работает с вариантом модели с `num_ctx = LLM_NUM_CTX` (по умолчанию 16384), который
создаёт при старте. Вручную контекст нужен, только если `LLM_PREPARE_MODEL=false`
или сервер не Ollama:

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
WASM-песочница · React/Next.js + Zustand · Vega-Lite/ECharts · openpyxl + ReportLab
