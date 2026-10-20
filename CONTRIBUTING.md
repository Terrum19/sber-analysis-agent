# Contributing

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
