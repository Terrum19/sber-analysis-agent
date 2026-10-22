# Отчёт об устранении рассинхронизации Frontend/Backend и переходе на Dual-Agent TDD

**Репозиторий:** SberAnalyticalAgent  
**Ветка:** `feature/F-01-frontend-ui`  
**Дата:** 22.10.2026  
**Автор:** AI Fullstack Developer (Pair Programming with User)

---

## 1. Что было сделано

В рамках устранения причин отклонения Pull Request ветки `feature/F-01-frontend-ui` («кнопки в никуда», Ghost UI, обманные клиентские заглушки) был проведён комплексный рефакторинг серверной и клиентской частей по методологии **TDD (Test-Driven Development)**:

1. **Реализован полный жизненный цикл CRUD дашбордов в FastAPI (`backend/app/api/routes/dashboards.py`)**:
   - `GET /api/dashboards` — параметр `session_id` сделан опциональным (`Query(default=None)`), что устранило падение общего реестра дашбордов с ошибкой `HTTP 422 Unprocessable Entity`.
   - `POST /api/dashboards` — создание дашборда со статусом `201 Created` с валидацией через `DashboardCreate` и нормализацией дефолтного заголовка.
   - `PUT /api/dashboards/{id}` — обновление заголовка и списка плиток со статусом `200 OK`.
   - `DELETE /api/dashboards/{id}` — физическое удаление дашборда и связанных плиток из SQLite со статусом `200 OK`.
   - `POST /api/dashboards/{id}/recalculate` — пересчет аналитических данных для плиток дашборда на основе реальных таблиц DuckDB с обновлением спецификаций Vega-Lite (`spec.data.values`), фиксацией времени снимка `snapshot_at` и сохранением в базу.

2. **Написан набор контрактных тестов по TDD (`backend/tests/contract/test_dashboards_crud_contract.py`)**:
   - `test_dashboards_full_crud_and_recalculate_lifecycle` — сквозной тест создания, чтения, фильтрации по сессиям, обновления, пересчета и удаления дашборда.
   - `test_dashboards_crud_not_found_handling` — строгая верификация статус-кодов `404 Not Found` для несуществующих ресурсов.
   - `test_dashboards_recalculate_with_duckdb` — физический пересчет данных из реальной DuckDB-таблицы с проверкой подстановки новых значений в спеку графика.
   - `test_dashboards_nested_snapshot_data_preservation` — проверка целостности сериализации сложных вложенных структур данных KPI и графиков.
   - `test_dashboards_default_title_and_empty_tiles` — проверка создания дашборда с дефолтным именем и пустым набором плиток.
   - `test_dashboards_partial_updates` — изолированное обновление только заголовка или только плиток.

3. **Исправлена сериализация структур данных ядра (`backend/app/core/dashboards/store.py` и `models.py`)**:
   - Функция `snapshot_records` переписана на рекурсивную обработку словарей и списков. Устранена критическая ошибка превращения вложенных структур данных (`{"records": [...]}`) в Python-строки repr (`"{'records': '...'}"`), ломавшая рендеринг KPI на фронтенде.
   - В `DashboardTile` добавлен дефолт `title = "Визуализация"`, тип `layout` расширен до `dict[str, Any]`, а тип `snapshot_data` обобщен до `Any = None`.

4. **Ликвидированы фиктивные заглушки и обманки (Silent Fallbacks) в интерфейсе**:
   - В [`Canvas.tsx`](file:///home/matthew78/Documents/sber_hackaton_ai_native_system/sber-analysis-agent/frontend/src/components/canvas/Canvas.tsx) и [`dashboard/[id]/page.tsx`](file:///home/matthew78/Documents/sber_hackaton_ai_native_system/sber-analysis-agent/frontend/src/app/dashboard/%5Bid%5D/page.tsx):
     * **Полностью удален `setTimeout 600ms`:** кнопка «Обновить данные» теперь вызывает реальный метод `recalculateDashboard(id)`.
     * **Удалены `window.print()` и аварийный дамп CSV из памяти:** при сетевых сбоях экспорта больше нет маскировки ошибки под успешную выгрузку; пользователь видит честное системное уведомление с серверным текстом ошибки (`err.detail`).
     * Кнопка «Сохранить» переведена на реальные вызовы `createDashboard` / `updateDashboard`, снабжена состоянием `isSaving`, спиннером и блокировкой от повторных кликов.
   - В [`dashboards/page.tsx`](file:///home/matthew78/Documents/sber_hackaton_ai_native_system/sber-analysis-agent/frontend/src/app/dashboards/page.tsx) кнопка удаления привязана к вызову `deleteDashboard(id)`.
   - В [`LeftSidebar.tsx`](file:///home/matthew78/Documents/sber_hackaton_ai_native_system/sber-analysis-agent/frontend/src/components/layout/LeftSidebar.tsx) радиокнопка ClickHouse заблокирована (`disabled`) со стилем неактивности и бейджем **«Скоро»**, предотвращая клики по нереализованному коннектору СУБД.

5. **Разработана стратегическая документация и регламенты**:
   - [`docs/frontend_backend_sync_strategy.md`](file:///home/matthew78/Documents/sber_hackaton_ai_native_system/sber-analysis-agent/docs/frontend_backend_sync_strategy.md) — матрица 14 разрывов кодовой базы и результаты раунда Adversarial-критики.
   - [`docs/dual_agent_tdd_plan.md`](file:///home/matthew78/Documents/sber_hackaton_ai_native_system/sber-analysis-agent/docs/dual_agent_tdd_plan.md) — архитектурный план и регламент процесса Dual-Agent TDD с конфигурацией CI/CD.

---

## 2. Соответствие архитектуре платформы

| Компонент / Сценарий | Прежнее состояние (Ghost UI) | Новое состояние (TDD) | Статус |
|---|---|---|---|
| **Создание дашборда** (`POST /api/dashboards`) | Роут отсутствовал (`HTTP 405`) | Полноценный роут, валидация DTO, статус 201 | ✅ Полное |
| **Редактирование плиток** (`PUT /api/dashboards/{id}`) | Роут отсутствовал (`HTTP 405`) | Обновление `tiles` и `title` в SQLite, статус 200 | ✅ Полное |
| **Удаление дашборда** (`DELETE /api/dashboards/{id}`) | Роут отсутствовал (`HTTP 405`) | Физическое каскадное удаление плиток и дашборда | ✅ Полное |
| **Реестр дашбордов** (`GET /api/dashboards`) | Ошибка `422 Unprocessable Entity` | Параметр `session_id` опционален, статус 200 | ✅ Полное |
| **Пересчет данных** (`POST /api/dashboards/{id}/recalculate`) | Роут отсутствовал (`HTTP 405`), в UI висел `setTimeout` | Реальный SQL-пересчет через DuckDB и обновление Vega-спеки | ✅ Полное |
| **Индикация состояний UI** | Только «Happy path», кнопки не блокировались | 4 состояния: Idle, Loading (спиннер + disabled), Success, Error | ✅ Полное |
| **Обработка сбоев экспорта** | `window.print()` и локальный дамп CSV | Честный вывод серверного текста ошибки в Toast | ✅ Полное |
| **Селектор СУБД ClickHouse** | Активен в UI, падал с ошибкой 501 | Честный `disabled` со стилем и бейджем «Скоро» | ✅ Полное |

---

## 3. Выявленные и исправленные дефекты

| # | Компонент | Дефект | Последствие | Исправление |
|---|---|---|---|---|
| 1 | `backend/app/api/routes/dashboards.py` | Отсутствовали методы `POST`, `PUT`, `DELETE`, `recalculate` | Нажатие «Сохранить» или «Удалить» в UI возвращало `HTTP 405 Method Not Allowed` | Добавлены полнофункциональные эндпоинты с делегированием в `DashboardStore` |
| 2 | `backend/app/api/routes/dashboards.py` | Параметр `session_id: str` был объявлен как обязательный query-параметр | Страница `/dashboards` падала с ошибкой `422 Unprocessable Entity` | Параметр изменен на `session_id: str | None = Query(default=None)` |
| 3 | `backend/app/core/dashboards/store.py` | Некорректная сериализация `snapshot_records` для вложенных словарей | Вложенные структуры превращались в Python-строки `"{'records': [...]}"`, ломая KPI | Реализована рекурсивная обработка списков и словарей с сериализацией дат и Decimal |
| 4 | `frontend/src/components/canvas/Canvas.tsx` | Фиктивный таймер `setTimeout 600ms` на кнопке пересчета | Имитация работы сервера без реального обновления данных в DuckDB | Заменен на вызов реального эндпоинта `recalculateDashboard(id)` |
| 5 | `Canvas.tsx` / `dashboard/[id]/page.tsx` | Вызов `window.print()` и клиентский дамп CSV в блоке `catch` | Маскировка серверных ошибок; пользователь получал псевдо-выгрузку вместо диагностики | Удалены silent fallbacks; выводится честное системное уведомление об ошибке |
| 6 | `frontend/src/components/layout/LeftSidebar.tsx` | Радиокнопка ClickHouse была активна при отсутствии коннектора на сервере | Пользователь получал неожиданную ошибку `501 Not Implemented` | Кнопка заблокирована (`disabled`) с бейджем «Скоро» и поясняющим тултипом |
| 7 | `backend/app/core/dashboards/models.py` | Строгие ограничения на поля `DashboardTile` (без дефолтного `title` и снимков) | Ошибки валидации 422 при сохранении дашбордов из интерфейса | Добавлен дефолт `title = "Визуализация"`, расширен тип layout и snapshot_data |

---

## 4. Регламент разработки: Переход на Dual-Agent TDD

Для предотвращения повторного появления «кнопок в никуда» в проект внедрён регламент двухагентной разработки:

```
[Постановка задачи]
        │
        ▼
[1. Backend Agent (TDD)] ──► Пишет тест в tests/contract/ (RED) ──► Реализует роут (GREEN)
        │
        ▼
[2. Gatekeeper CI] ────────► Экспорт openapi.json ──► Генерация типов TypeScript (schema.d.ts)
        │
        ▼
[3. Frontend Agent] ───────► Привязка UI с 4 состояниями (Idle, Loading, Success, Error)
        │
        ▼
[4. Adversarial Critic] ───► Проверка 5 точек отказа ──► Блокировка мерджа при наличии заглушек
```

* **Главное правило:** Запрещено верстать интерактивные элементы интерфейса без предварительно написанного и зеленого контрактного теста на бэкенде.
* **Правило заглушек:** Если функциональность еще не реализована на сервере, кнопка в UI обязана быть `disabled` с бейджем «В разработке». Никаких кликов с падением на 405/501!

---

## 5. Оставшиеся расхождения (не критичные для текущего PR)

| Компонент | Проблема | Текущее состояние в UI | Статус |
|---|---|---|---|
| `PDFExporter` | Серверная генерация PDF находится в стадии разработки | При вызове выводится понятное сообщение об ошибке; вызов `window.print()` убран | `TODO(round-2)` |
| `ClickHouse Connector` | Драйвер ClickHouse находится в разработке | Селектор в модалке заблокирован (`disabled`) с бейджем «Скоро» | `TODO(round-2)` |
| `DuckDB Query Cancellation` | Отмена тяжелых SQL-запросов при прерывании SSE-соединения | Запрос выполняется до завершения шага под db.lock | Спроектировано в стратегии (Tier 1) |
| `Session History on Reload` | Восстановление переписки из SQLite при перезагрузке страницы (F5) | Метаданные сохраняются, расширение DTO спроектировано | Спроектировано в стратегии (Tier 1) |

---

## 6. Итог и результаты верификации

- **Все «кнопки в никуда» устранены:** CRUD дашбордов полностью работоспособен от базы данных SQLite/DuckDB до экранных форм Canvas и Dashboards.
- **Обманные заглушки и silent fallbacks ликвидированы:** удалены `setTimeout 600ms`, `window.print()` и псевдо-дампы CSV.
- **Полное покрытие контрактными тестами:**
  ```bash
  .venv/bin/pytest backend/tests/contract/test_dashboards_crud_contract.py -v
  ```
  Результат: **6 passed из 6** (100% успешно).
- **Регрессионное тестирование бэкенда:**
  ```bash
  .venv/bin/pytest backend/tests/contract/ backend/tests/unit/ -q
  ```
  Результат: **578 passed**, 5 skipped, 0 failed.
- **Статический анализ и сборка фронтенда:**
  ```bash
  cd frontend && npm run lint && npm run build
  ```
  Результат: **0 warnings, 0 errors**. Все 5 роутов приложения успешно скомпилированы.

Ветка `feature/F-01-frontend-ui` полностью готова к повторному код-ревью и слиянию.
