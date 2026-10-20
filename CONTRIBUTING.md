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
