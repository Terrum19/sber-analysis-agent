# Contributing

Первым шагом после клонирования подключите git-хуки репозитория:

```bash
make hooks
```

## Конвенции коммитов

- Заголовок коммита — на английском, описание (если есть) — на русском.
- В сообщении нет упоминаний Claude (`Co-Authored-By: Claude …`,
  `Generated with Claude`, `noreply@anthropic.com`).
- В коммите нет служебных файлов `.claude/` и `CLAUDE.md`.

Хук `commit-msg` (`.githooks/commit-msg`, подключается `make hooks`)
проверяет сообщение до создания коммита теми же правилами, что и CI
(`.github/scripts/check_conventions.py`, workflow Conventions). Файлы
коммита проверяет только CI.

Тот же job Conventions ищет секреты (пароли, токены, ключи) во всей
истории git с помощью gitleaks. Он запускается на каждый push в любую
ветку и на PR из форков; PR из веток этого репозитория уже проверены на
push, и для них job пропускается.

Хук `pre-commit` (`.githooks/pre-commit`, подключается тем же
`make hooks`) запускает `ruff check` и `ruff format --check` на
индексированных `.py`-файлах `backend/` с конфигом
`backend/pyproject.toml`, как Backend CI. Версия ruff закреплена в
`backend/requirements.txt`. Если ruff не установлен (ни в `PATH`, ни
как `python3 -m ruff`), хук предупреждает и пропускает коммит.

## Конвенции

- **Тулы** добавляются в `backend/app/core/tools/{sql,stats,timeseries}/`
  как класс `BaseTool` и регистрируются в `ToolRegistry`. Оркестратор не меняется.
- **Коннекторы** источников — в `backend/app/core/data/connectors/`,
  реализуют `BaseConnector`. Новый источник = новый файл.
- **Промпты** — только в `backend/app/core/llm/prompts/` (см. docs/prompts.md).
- **Контракты** (Pydantic) — в `backend/app/models/` и `backend/app/api/schemas/`;
  фронт-дубли типов — в `frontend/src/types/` (держать в синхроне).
- **Рантайм** (`backend/storage/`, `models/`, `.env`) — в `.gitignore`.

## Проверки

```bash
make lint   # ruff + next lint
make test   # pytest
```

## Smoke-тест чистого запуска

Workflow Smoke (`.github/workflows/smoke.yml`, #79) на каждый PR и на push в
`dev`/`main`, меняющий `backend/`, `frontend/`, `infra/`, `data/`,
compose-файлы, `.env.example`, `.gitattributes`, `.githooks/` или скрипты
запуска (`*.sh`, `*.command`), поднимает `docker compose up -d --build backend frontend` с
надстройкой `docker-compose.ci.yml`: без профиля Ollama не стартует, бэкенд
отвечает фейковой LLM (`USE_FAKE_LLM=true`). Затем `scripts/smoke.sh`
загружает `data/sample_datasets/demo_events.csv`, создаёт сессию над первой
таблицей из ответа, читает SSE чата до события `done` (событие `error`,
обрыв или таймаут — провал) и проверяет, что фронтенд отдаёт `GET /` 200.

Локально, на уже поднятом стеке:

```bash
make smoke
```

Переменные: `BACKEND_URL` (по умолчанию `http://localhost:8000`),
`FRONTEND_URL` (`http://localhost:3000`), `SMOKE_CSV`, `SMOKE_TIMEOUT`
(секунды, 120), `SKIP_FRONTEND=1` — только бэкенд (например, против
`uvicorn` с `USE_FAKE_LLM=true`).
