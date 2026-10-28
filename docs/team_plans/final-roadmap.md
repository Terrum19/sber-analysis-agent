# План до финала (21–31 октября)

Сдаём 31.10: репозиторий + `docker compose`, жюри запускает сами.
Значит, побеждает то, что **поднимается с первого раза и отвечает на вопросы
по датасетам хакатона без падений**. Ниже — кто, что и когда делает, и
атомарные issues (0.5–1 день каждый), которые можно брать сразу.

## Команда и зоны

| Кто | GitHub | Зона ответственности |
|---|---|---|
| Александр | @Terrum19 | Агентный цикл: планировщик, оркестратор, восстановление, релиз |
| mb6ockatf | @mb6ockatf | Бэкенд дашбордов (#27), SSE, CI, docker |
| Дмитрий | @dmitriy-ya | Тулы, ClickHouse (#7), экспорт xlsx/pdf |
| matthew78 | @Trewwws | Фронтенд (#28) — только `frontend/` |
| thebulkapro | @thebulkapro | Данные и паспорт (#12), eval-набор, выбор LLM, README |

## Майлстоуны

| | Дата | Что значит «готово» |
|---|---|---|
| **M0 — Интеграция** | ср 21.10 | `feature/agent-planner` в `dev`, фронт-ветка содержит только `frontend/`, CI зелёный |
| **M1 — Реальные данные** | пт 23.10 | 9 датасетов хакатона грузятся, сессия работает с выбранной таблицей, eval-прогон есть, **LLM выбрана** |
| **M2 — Фичи готовы** | пн 26.10 | Дашборды (бэк+фронт), ClickHouse, PDF, кодогенерация, отказ/переформулировка, канвас |
| **M3 — Feature freeze** | ср 28.10 | Только баги. Eval ≥ 80 %, `make up` на чистой машине работает |
| **M4 — Release candidate** | пт 30.10 | `dev → main`, тег `v1.0.0`, README со скриншотами |
| Сдача | сб 31.10 | Только критические фиксы |

## Правила (обязательно)

- **Конвенции коммитов**: заголовок на английском, описание на русском,
  **никаких `Co-Authored-By: Claude` / «Generated with Claude»**, никаких `.claude/`
  и `CLAUDE.md` в коммитах. Проверка — `.github/scripts/check_conventions.py`.
- Ветка на issue: `feature/<ID>-<slug>` (например `feature/D-01-session-table`), PR в `dev`.
- PR живёт ≤ 1 дня; ветку старше 2 дней ребейзим на `dev`.
- Контракты (`backend/app/api/schemas/*`, `backend/app/models/*` ↔ `frontend/src/types/*`)
  меняем одним PR или фиксируем в issue до начала работы. **Поля — в snake_case**,
  как в Pydantic; фронт не держит дубли camelCase.
- Фронтенд-ветка трогает только `frontend/`. Моки фронта — только по флагу
  `NEXT_PUBLIC_USE_MOCKS=true`, без тихого фолбэка.
- Все LLM-зависимые тесты — на `FakeLLM` (`app/core/llm/fake_client.py`);
  реальная модель — только в тестах с маркером `real_llm`.
- После 28.10 новых фич не начинаем.

## График по дням

| Дата | Александр | mb6ockatf | Дмитрий | matthew78 | thebulkapro |
|---|---|---|---|---|---|
| ср 21 | A-01, A-02 | INF-01, INF-02 | C-01 | F-01 | D-05 |
| чт 22 | A-03 | B-01, B-02 | C-01, C-02 | F-02, F-03 | D-01, D-02 |
| пт 23 | A-04 | B-05 | E-01 | F-04 | D-03, D-04, Q-01 |
| сб 24 | B-03 | B-06, S-01 | E-01 | F-05, F-07 | Q-02 |
| вс 25 | B-04 | I-01 | E-02, E-03 | F-06 | Q-03 (решение по LLM) |
| пн 26 | A-05 | I-01, D-06 | T-01 | F-08 | Q-04 |
| вт 27 | A-06 | I-02 | T-01, T-02 | F-09, F-10 | Q-04, I-03 |
| ср 28 | **Bug bash (все)** — R-01 | | | | |
| чт 29 | фиксы eval | фиксы I-02 | T-02 | F-11 | скриншоты для I-03 |
| пт 30 | R-02 релиз | R-02 образы | фиксы | фиксы | финальный eval |

## Зависимости

```
INF-01 ─┐
A-01 ───┼─> A-03, A-04, B-03, D-01, F-02, S-01, T-01
F-01 ───┘
D-01 ─> D-02 ─> F-03 ;  D-01 ─> D-03 ─> D-04 ;  D-01 ─> Q-02
B-01 ─> B-02 ─> B-03 ─> B-04 ;  B-01 ─> B-06 ─> F-06 ;  B-06 ─> B-04 (формат события)
B-01 ─> B-05 ─> F-07 ─> F-08 ─> F-09
S-01 ─> F-05 ;  A-05 ─> F-04
E-01 ─> E-02 ─> E-03 ─> F-10
D-05 ─> Q-01 ─> Q-02 ─> Q-03 (LLM) ─> I-01 ─> I-02 ─> R-02
```

---

# Порядок исполнения

Issues заведены в GitHub (метка `roadmap`, майлстоуны M0–M4). Правило выбора
следующей задачи: **сначала то, что разблокирует других**, затем своё по порядку
ниже. Задача заблокирована — берём следующую незаблокированную из своего списка
и пишем в чат команды.

## Вся команда: волны и критический путь

**Критический путь** (сдвиг любого звена сдвигает релиз):
`#35 INF-01 → #37 A-01 → #51 D-01 → #57 Q-02 → #58 Q-03 → #78 I-01 → #79 I-02 → #82 R-02`.
Второй по длине: `#45 B-01 → #46 B-02 → #43 B-03 → #44 B-04` (дашборд из чата).

| Волна | Даты | Issues | Выход волны |
|---|---|---|---|
| 0. Интеграция | ср 21 | #35, #36, #38, **#37**, #67, #50, #60 | planner и фронт влиты в `dev`, датасеты загружаются |
| 1. Фундамент | чт 22 – пт 23 | #45, #46, #47 · #51, #52, #53, #54, #56 · #39, #40 · #61, #62 · #68, #69, #70 | сессия с таблицей, паспорт, хранилище и API дашбордов, ClickHouse, eval-набор |
| 2. Фичи | сб 24 – пн 26 | #49, #48, #43, #44 · #57, #58 · #63, #64 · #73, #71, #72, #74 · #78, #41, #55 | дашборды из чата, прогресс, PDF/XLSX, выбрана LLM, compose с моделью, отказы |
| 3. Доводка | вт 27 – ср 28 | #42, #65, #75, #76, #79, #59, **#81** (bug bash) | фичи заморожены, eval ≥ 80 % |
| 4. Релиз | чт 29 – пт 30 | #66, #77, #80, **#82** | `v1.0.0` |

**Контрольные точки (созвон в 10:00):**
- ср 21 — #37 влит? Если нет к вечеру, D-01, F-02, S-01 и T-01 стоят.
- пт 23 — M1: выбран ли список кандидатов LLM, работают ли #51 и #47.
- пн 26 — M2: дашборд создаётся из чата и открывается на канвасе; `make up` с моделью.
- ср 28 — bug bash #81, после него только фиксы.

## По участникам

### @Terrum19 — Александр
| # | Issue | Когда | Почему в таком порядке |
|---|---|---|---|
| 1 | #38 A-02 прибрать ветку | ср 21, утро | без этого PR планировщика не собрать |
| 2 | **#37 A-01 влить dev в planner** | ср 21 | блокирует #51, #68, #49, #65, #39, #40, #43 |
| 3 | ревью #51 D-01 | чт 22 | D-01 правит оркестратор |
| 4 | #39 A-03 исправление шагов (#33) | чт 22 | влияет на качество eval с первого прогона |
| 5 | #40 A-04 кодоген в планировщике | пт 23 | закрывает обязательную фичу «WASM» |
| 6 | #43 B-03 плитки в плане | сб 24 | нужен #46 (чт 22) |
| 7 | #44 B-04 сборка дашборда | вс 25 | разблокирует фронт #72 на деле |
| 8 | #41 A-05 отказы | пн 26 | до freeze, чтобы фронт успел #70 |
| 9 | #42 A-06 диалог с контекстом | вт 27 | сценарий 6 |
| 10 | #81 R-01 bug bash → фиксы eval | ср 28 – чт 29 | |
| 11 | #82 R-02 релиз | пт 30 | |

### @mb6ockatf
| # | Issue | Когда | Почему в таком порядке |
|---|---|---|---|
| 1 | **#35 INF-01 фикс проверки конвенций** | ср 21, утро | иначе push ветки планировщика красный |
| 2 | #36 INF-02 commit-msg хук | ср 21 | |
| 3 | #45 B-01 хранилище дашбордов | чт 22 | блокирует #46, #47, #48, #63 |
| 4 | #46 B-02 шаблоны | чт 22 | блокирует #43 у Александра |
| 5 | #47 B-05 REST API | пт 23 | блокирует фронт #73 и экспорт #64 |
| 6 | #49 S-01 события прогресса | сб 24 | нужен #37; блокирует #71 |
| 7 | #48 B-06 payload + KPI | сб 24 | блокирует #72 и формат события в #44 |
| 8 | #78 I-01 compose с Ollama | вс 25 – пн 26 | модель известна после #58 (вс 25); каркас compose можно начать раньше |
| 9 | #55 D-06 очистка temp_result | пн 26 | |
| 10 | #79 I-02 smoke в CI | вт 27 | |
| 11 | #81 R-01 → фиксы I-02 | ср 28 – чт 29 | |
| 12 | #82 R-02 образы и тег | пт 30 | |

### @dmitriy-ya — Дмитрий
| # | Issue | Когда | Почему в таком порядке |
|---|---|---|---|
| 1 | #60 C-01 коннектор ClickHouse | ср 21 – чт 22 | ни от кого не зависит, обязательная фича |
| 2 | #61 C-02 тесты и CI ClickHouse | чт 22 | |
| 3 | #62 E-01 PDF по ответу | пт 23 – сб 24 | блокирует #76 |
| 4 | #63 E-02 экспорт дашборда | вс 25 | нужен #45 |
| 5 | #64 E-03 эндпоинт экспорта | вс 25 | нужен #47; блокирует #76 |
| 6 | #65 T-01 тулы сценария 3 | пн 26 – вт 27 | нужен #37; к этому моменту есть провалы eval по сценарию 3 |
| 7 | #66 T-02 фиксы тулов по eval | вт 27 – чт 29 | по отчётам #59 |
| 8 | #81 R-01 bug bash | ср 28 | |

### @Trewwws — matthew78
| # | Issue | Когда | Почему в таком порядке |
|---|---|---|---|
| 1 | **#67 F-01 чистая фронт-ветка** | ср 21 | весь UI без этого не попадёт в `dev` |
| 2 | #68 F-02 типы чата | чт 22 | нужен #37 |
| 3 | #69 F-03 выбор таблицы, паспорт | чт 22 | нужен #52 |
| 4 | #70 F-04 уточнения и отказы | пт 23 | уточнения есть уже сейчас; отказы — заглушка до #41 |
| 5 | #73 F-07 страницы дашбордов | сб 24 | нужен #47 |
| 6 | #71 F-05 прогресс шагов | сб 24 | нужен #49 |
| 7 | #72 F-06 превью дашборда в чате | вс 25 | нужен #48 |
| 8 | #74 F-08 канвас drag&drop | пн 26 | нужен #73 |
| 9 | #75 F-09 «дообследуй» | вт 27 | нужен #74 |
| 10 | #76 F-10 кнопки экспорта | вт 27 | нужны #62, #64 |
| 11 | #81 R-01 → #77 F-11 полировка | ср 28 – чт 29 | |

### @thebulkapro
| # | Issue | Когда | Почему в таком порядке |
|---|---|---|---|
| 1 | #50 D-05 загрузка 9 датасетов | ср 21 | ни от кого не зависит; находит баги загрузки до всех остальных |
| 2 | #51 D-01 сессия с таблицей | чт 22 | нужен #37; блокирует #52, #53, #57 — **критический путь** |
| 3 | #52 D-02 API сессий | чт 22 | блокирует фронт #69 |
| 4 | #53 D-03 паспорт при подключении | пт 23 | |
| 5 | #54 D-04 «что передо мной» | пт 23 | сценарий 1 |
| 6 | #56 Q-01 eval-набор | пт 23 | если #37 задерживается — делать сразу после #50 |
| 7 | #57 Q-02 скрипт eval | сб 24 | критический путь |
| 8 | #58 Q-03 выбор LLM | вс 25 | разблокирует #78 |
| 9 | #59 Q-04 ежедневный eval | пн 26 – пт 30 | каждое утро, до созвона |
| 10 | #80 I-03 README | вт 27 – чт 29 | нужен #78 |
| 11 | #81 R-01 bug bash | ср 28 | |

## Номера issues

| ID | # | ID | # | ID | # | ID | # |
|---|---|---|---|---|---|---|---|
| INF-01 | #35 | B-01 | #45 | D-04 | #54 | F-01 | #67 |
| INF-02 | #36 | B-02 | #46 | D-06 | #55 | F-02 | #68 |
| A-01 | #37 | B-03 | #43 | Q-01 | #56 | F-03 | #69 |
| A-02 | #38 | B-04 | #44 | Q-02 | #57 | F-04 | #70 |
| A-03 | #39 | B-05 | #47 | Q-03 | #58 | F-05 | #71 |
| A-04 | #40 | B-06 | #48 | Q-04 | #59 | F-06 | #72 |
| A-05 | #41 | S-01 | #49 | C-01 | #60 | F-07 | #73 |
| A-06 | #42 | D-05 | #50 | C-02 | #61 | F-08 | #74 |
| E-01 | #62 | D-01 | #51 | T-01 | #65 | F-09 | #75 |
| E-02 | #63 | D-02 | #52 | T-02 | #66 | F-10 | #76 |
| E-03 | #64 | D-03 | #53 | I-01 | #78 | F-11 | #77 |
| R-01 | #81 | R-02 | #82 | I-02 | #79 | I-03 | #80 |

---

# Issues

Формат каждого issue:
- **Шапка** — исполнитель, срок, зависимости, эпик.
- **Сейчас** — что в коде на момент написания (`dev` @ `b649887`,
  `feature/agent-planner` @ `0a975da`, `feature/frontend` @ `000c5be`).
- **Где** — файлы. **Как** — шаги реализации. **Тесты**. **Готово, когда**.

---

## Инфраструктура и конвенции

### INF-01 · Не проверять в push-режиме коммиты, уже лежащие в dev/main
**Кто:** @mb6ockatf · **Срок:** 21.10 · **Зависит:** — · **Метки:** `ci`

**Сейчас.** `.github/workflows/conventions.yml`, шаг *Collect commits*: при push
берётся `git rev-list $BEFORE..$AFTER`. Когда в ветку вливают `dev`, в диапазон
попадает `c968899` (WASM, трейлер `Co-Authored-By: Claude`) — проверка падает
на каждой такой ветке. Переписывать историю `dev` не будем.

**Где.** `.github/workflows/conventions.yml`.

**Как.**
1. В ветке `elif git cat-file -e "$BEFORE^{commit}"` заменить сбор на
   ```bash
   EXCLUDE=$(git for-each-ref --format='%(refname)' refs/remotes/origin/dev refs/remotes/origin/main \
             | grep -v -x "refs/remotes/origin/$BRANCH")
   git rev-list --reverse "$BEFORE..$AFTER" --not $EXCLUDE > "$RUNNER_TEMP/commits"
   ```
   Для push в сам `dev`/`main` своя ветка из исключений убирается, иначе
   проверять будет нечего.
2. Режим `pull_request` (`BASE..HEAD_SHA`) не трогаем — он уже корректен.
3. Обновить комментарий в шапке workflow.

**Тесты.** Тестовая ветка от `dev` с пустым коммитом → push → проверка зелёная.
Тестовая ветка с коммитом, содержащим `Co-Authored-By: Claude …` → красная.

**Готово, когда**
- [ ] Push ветки с влитым свежим `dev` проходит проверку.
- [ ] Новый нарушающий коммит по-прежнему ловится (приложить ссылки на оба прогона в PR).

### INF-02 · Локальный commit-msg хук с теми же проверками
**Кто:** @mb6ockatf · **Срок:** 21.10 · **Зависит:** — · **Метки:** `ci`

**Сейчас.** Нарушения видны только в CI после push.

**Где.** `.githooks/commit-msg` (новый), `.github/scripts/check_conventions.py`,
`Makefile`, `CONTRIBUTING.md`.

**Как.**
1. В `check_conventions.py` вынести проверку текста в функцию
   `text_problems(message: str) -> list[str]` (тело — из `message_problems`,
   которая начинает вызывать её на `git log -1 --format=%B`).
2. Добавить режим `--message-file PATH`: читает файл, вызывает `text_problems`,
   выходит с кодом 1 при нарушениях.
3. `.githooks/commit-msg`:
   ```sh
   #!/bin/sh
   exec python3 "$(git rev-parse --show-toplevel)/.github/scripts/check_conventions.py" --message-file "$1"
   ```
   (`chmod +x`).
4. `Makefile`: цель `hooks: git config core.hooksPath .githooks`; добавить в `help`.
5. `CONTRIBUTING.md`: раздел «Конвенции коммитов» + `make hooks` первым шагом.

**Тесты.** `backend/tests/unit/test_conventions_script.py` — `text_problems`
на кириллическом заголовке, английском описании и трейлере Claude.

**Готово, когда**
- [ ] После `make hooks` коммит `git commit -m "Добавил фичу"` отклоняется локально.
- [ ] Коммит с трейлером Claude отклоняется локально.

---

## Агентный цикл (эпик #20)

### A-01 · Влить dev (WASM) в feature/agent-planner и открыть PR в dev
**Кто:** @Terrum19 · **Срок:** 21.10 · **Зависит:** INF-01 · **Эпик:** #20

**Сейчас.** `git merge-tree` даёт ~42 конфликта. Причина: в `dev` тулы в старом
формате (позиционные `columns`) + правки ревью `5eabace`/`0caa22f`, в planner —
типизированные `Args`, `kind`, ссылки `$step_N`.

**Где / как разрешать.**

| Файлы | Берём | Что донести с другой стороны |
|---|---|---|
| `backend/app/core/tools/**` (sql, stats, timeseries, domain, funnel), `tools/base.py`, `tools/registry.py`, `tests/unit/test_tools.py` | planner (`Args`, `execute(db, input_table, args)`) | из `dev`: экранирование имён источников (`table_name()`/кавычки из `0caa22f`), исправления логики из `5eabace` — пройтись по диффу `git diff 93d2338 5eabace -- backend/app/core/tools` |
| `sandbox/codegen_runner.py`, `sandbox/wasm_runtime.py`, `tools/run_wasm_python.py`, `prompts/codegen.py`, `backend/sandbox/**` | `dev` целиком | — |
| `agent/step_executor.py` | planner | ветка `needs_llm_codegen or not tool` → `run_generated_code(step, table, self.db)` из `dev` (сигнатура совпадает) |
| `agent/orchestrator.py` | planner | `NotImplementedError` больше не бросается кодогеном — оставить обработку `SandboxError` как обычной ошибки шага |
| `llm/prompts/step_planner.py` | planner | описание параметров тулов из `0caa22f` уже покрыто `describe()` — проверить, что не дублируется |
| `backend/pyproject.toml` | объединить зависимости обеих сторон | — |
| `ChangeLog` | обе записи, по дате | — |

**Как.**
1. `git checkout feature/agent-planner && git merge origin/dev`.
2. Разрешить по таблице, `ruff check app tests && pytest -q`.
3. Прогнать вручную `backend/run_llm_agent.py` на `data/sample_datasets/demo_events.csv` (FakeLLM).
4. PR `feature/agent-planner → dev`, описание по конвенции (без трейлеров).

**Готово, когда**
- [ ] `pytest` зелёный (включая `tests/unit/test_sandbox.py` из `dev`), `ruff` чистый, конвенции зелёные.
- [ ] PR влит в `dev`; #20 обновлён ссылкой на PR.

### A-02 · Прибрать ветку planner перед PR
**Кто:** @Terrum19 · **Срок:** 21.10 · **Зависит:** — · **Эпик:** #20

**Сейчас.** В корне `backend/` неотслеживаемые `test_llm_client_real.py`,
`test_orchestrator_real_llm.py`, `test_session_memory_sqlite.py`; изменён
`.env.example`; в planner нет `pytz` (в `dev` есть — придёт с A-01), из-за
этого падает `test_profile_survives_restart_with_all_types`; лог `backend/agent_run.log`.

**Где.** `backend/tests/integration/`, `backend/pyproject.toml` (`[tool.pytest.ini_options]`),
`.gitignore`, `.env.example`.

**Как.**
1. `test_session_memory_sqlite.py` → проверить, не дублирует ли
   `tests/unit/test_session_memory_persistence.py`; если нет — перенести в `tests/unit/`.
2. `test_*_real*.py` → `tests/integration/`, пометить
   `pytestmark = [pytest.mark.real_llm, pytest.mark.skipif(not os.getenv("LLM_BASE_URL"), reason=...)]`;
   зарегистрировать маркер `real_llm` в `pyproject.toml`.
3. `*.log` → `.gitignore`.
4. `.env.example`: оставить закомментированный пример Ollama и пустые значения,
   как сейчас; добавить переменные песочницы из `dev` (`WASM_*`, `CODEGEN_ATTEMPTS`).

**Готово, когда**
- [ ] `git status` чистый; `pytest -q` — 0 падений, `real_llm` пропускаются без `LLM_BASE_URL`.

### A-03 · Исправление шага по тексту ошибки, затем вопрос пользователю (#33)
**Кто:** @Terrum19 · **Срок:** 22.10 · **Зависит:** A-01 · **Эпик:** #20

**Сейчас.** `orchestrator._execute_plan` вызывает
`with_retry(self.executor.execute, step, …, attempts=STEP_RETRY_ATTEMPTS)`
(`agent/recovery.py`) — тот же шаг повторяется **с теми же аргументами** и паузой
1–2 с. Детерминированная ошибка (неверная колонка, тип) повторяется гарантированно,
после чего сразу `ClarificationNeeded`.

**Где.** `agent/orchestrator.py` (`_execute_plan`), `agent/planner.py`
(новый метод), `llm/prompts/step_planner.py` (новый промпт `STEP_FIX`),
`agent/recovery.py`.

**Как.**
1. `prompts/step_planner.py`: `STEP_FIX` — «шаг {step_json} упал с ошибкой
   {error}; колонки таблицы {columns}; верни исправленный шаг того же назначения».
2. `StepPlanner.fix_step(step, error, context) -> ResearchStep`:
   `complete_structured(SYSTEM_ANALYST, prompt, ResearchStep)`, сохранить
   `step_id`, прогнать через `_validate_step`.
3. В `_execute_plan` заменить `with_retry` на цикл:
   ```python
   for attempt in range(STEP_FIX_ATTEMPTS + 1):
       try:
           table = await self.executor.execute(step, current, step_tables=tables, source_table=source)
           break
       except (ValueError, duckdb.Error, SandboxError) as exc:
           if attempt == STEP_FIX_ATTEMPTS:
               ...  # текущая логика pending_recovery + ClarificationNeeded
           step = await self.planner.fix_step(step, str(exc), memory.build_context(question))
           steps[index] = step
   ```
   `STEP_FIX_ATTEMPTS = 2` — константа модуля. Сетевые ошибки LLM уже
   ретраятся в `LLMClient` — `with_retry` для шагов больше не нужен.
4. Логировать каждую попытку исправления (`logger.warning`) — это пойдёт в eval-отчёт.

**Тесты** (`tests/unit/test_recovery_and_cache.py`, `test_orchestrator_unit.py`):
- FakeLLM выдаёт план с несуществующей колонкой, затем исправленный шаг →
  ответ без `clarification_needed`, в `SessionMemory` нет `pending_recovery`.
- Обе попытки исправления неудачны → событие `clarification_needed`,
  `pending_recovery.attempts_made == 3`.

**Готово, когда**
- [ ] Тесты выше зелёные; #33 закрыт.
- [ ] #31 (кириллица в CSV) и #32 (параметры в промпте) проверены на `dev` и закрыты
      с комментарием, каким коммитом исправлены.

### A-04 · Кодогенерация как запасной путь планировщика
**Кто:** @Terrum19 · **Срок:** 23.10 · **Зависит:** A-01 · **Эпик:** #20, #26

**Сейчас.** В `dev` есть рабочая песочница (`WASMSandbox`, `run_generated_code`),
но в planner `validate_plan → _validate_step` возвращает ошибку
«кодогенерация не поддерживается» для любого шага с `needs_llm_codegen` или
без `tool` — LLM переспрашивается, и кодоген никогда не запускается.

**Где.** `agent/planner.py` (`_validate_step`), `llm/prompts/step_planner.py`,
`sandbox/wasm_runtime.py` (`WASMSandbox.available()`), `config.py`.

**Как.**
1. `config.py`: `codegen_enabled: bool = True` (+ `CODEGEN_ENABLED` в `.env.example`).
2. `_validate_step`: если `step.needs_llm_codegen or not step.tool`:
   - `settings.codegen_enabled and WASMSandbox().available()` → требовать
     непустой `purpose` (по нему пишется код), иначе ошибка `«шаг N: опиши purpose для кодогенерации»`;
   - иначе — текущая ошибка.
   Результат `available()` закэшировать на модуль (`functools.cache`), чтобы не
   дёргать node на каждый шаг.
3. Промпт планировщика: блок «Если ни один инструмент не подходит — шаг с
   `"tool": null, "needs_llm_codegen": true` и подробным `purpose`. Не используй
   кодогенерацию, если задача решается инструментами». Список тулов передавать
   как сейчас.
4. В `tool_kind` кодоген уже `transform` — оставить.

**Тесты.**
- `tests/unit/test_planner.py`: план с codegen-шагом валиден при доступной
  песочнице (monkeypatch `available → True`) и невалиден при `False`.
- `tests/integration/test_agent_cycle.py`: FakeLLM → план из codegen-шага →
  `run_generated_code` (monkeypatch `WASMSandbox.run` → готовый `result.csv`) →
  `temp_result_vN` → инсайт. Реальный Pyodide — под маркером `sandbox`.

**Готово, когда**
- [ ] Вопрос, для которого нет тула (например «посчитай медиану по скользящему окну 7 дней с весами»), отрабатывает через песочницу.
- [ ] При `CODEGEN_ENABLED=false` — понятный отказ, без трейсбека.

### A-05 · Неудобные вопросы: отказ и переформулировка
**Кто:** @Terrum19 · **Срок:** 26.10 · **Зависит:** A-01 · **Эпик:** #20

**Сейчас.** Любой вопрос сразу идёт в планировщик. На «какая погода завтра»
или «удали таблицу» агент строит план или падает в `_require_steps` →
`clarification_needed` с общим текстом.

**Где.** `llm/prompts/question_guard.py` (новый), `models/question_guard.py`
(новый), `agent/guard.py` (новый), `agent/orchestrator.py` (`run`),
`api/schemas/chat.py`, `docs/architecture.md`.

**Как.**
1. Модель:
   ```python
   class GuardVerdict(BaseModel):
       kind: Literal["ok", "out_of_scope", "no_data", "ambiguous", "unsafe"]
       reason: str = ""
       suggestions: list[str] = []   # 2–3 переформулировки под имеющиеся колонки
   ```
2. Промпт: вопрос + название таблицы + список колонок с описаниями из паспорта
   (`context["descriptions"]`) + последние 3 сообщения (чтобы «а теперь по месяцам»
   не считалось неоднозначным).
3. `QuestionGuard.check(question, context) -> GuardVerdict`. Дешёвый
   предфильтр без LLM: вопросы с `drop|delete|update|insert|удали|измени` → `unsafe`.
4. В `run()` перед `planner.plan` (после ветки `_morph`):
   - `out_of_scope` / `no_data` / `unsafe` → `{"type": "refusal", "data": {"reason", "suggestions"}}`, `done`, `memory.record_answer(...)`;
   - `ambiguous` → `clarification_needed` с `step_id=-1` и `suggestions` из вердикта;
   - `ok` → как сейчас.
5. `ChatEvent.type` += `"refusal"`; описать событие в `docs/architecture.md`
   и передать в F-04.

**Тесты.** `tests/unit/test_question_guard.py` (FakeLLM с заготовленными
вердиктами, предфильтр без LLM), `tests/unit/test_orchestrator_unit.py`
(отказ не вызывает `planner.plan`), контрактный тест события `refusal`.

**Готово, когда**
- [ ] 5 «неудобных» вопросов из eval-набора (Q-01) дают отказ или уточнение без SQL.
- [ ] Обычные вопросы eval не стали хуже (сравнение прогонов Q-02 до/после).

### A-06 · Контекст диалога: «а теперь то же самое для …» (сценарий 6)
**Кто:** @Terrum19 · **Срок:** 27.10 · **Зависит:** A-01 · **Эпик:** #20

**Сейчас.** `SessionMemory.build_context` отдаёт `summary` вида
«шаг 0: purpose; шаг 1: purpose» и последние 5 сообщений с ответами агента.
Вопрос пользователя в `messages` не пишется (`record_answer` — только агент),
прошлый план с аргументами не сохраняется — повторить его с другим фильтром LLM не может.

**Где.** `core/memory/session_memory.py`, `agent/orchestrator.py`,
`llm/prompts/step_planner.py`.

**Как.**
1. `SessionMemory.record_question(text)` — пишет `{"role": "user", ...}`;
   вызывать в начале `run()`.
2. `SessionMemory.record_plan(question, plan)` — хранит последний успешный план
   (`steps` с `tool/input/arguments/purpose`, `final_viz`) в `_state["last_plan"]`;
   вызывать после `_execute_plan`.
3. `build_context` отдаёт `previous_plan` (JSON последнего плана) и окно сообщений
   обеих ролей.
4. Промпт: блок «Предыдущий вопрос и план: … Если новый вопрос — уточнение
   предыдущего («а теперь…», «то же самое…», «а для…»), измени только нужные
   аргументы предыдущего плана».

**Тесты.** `tests/e2e/test_scenarios.py::test_scenario_6_follow_up`:
«выручка по месяцам» → «а теперь только для категории Одежда» → второй план
содержит `filter_rows` по категории + тот же `time_series`.

**Готово, когда**
- [ ] Сценарий 6 из eval проходит на выбранной модели в ≥ 4 из 5 вопросов.

### B-03 · Поля дашборда в ResearchPlan и промпт планировщика (Б5, Б6 из #27)
**Кто:** @Terrum19 · **Срок:** 24.10 · **Зависит:** A-01, B-02 · **Эпик:** #27

**Сейчас.** `ResearchPlan` (`models/research_step.py`) знает только
`dashboard_requested: bool`; оркестратор при `True` кладёт в дашборд один
итоговый график.

**Где.** `models/research_step.py`, `agent/planner.py` (`plan`, `validate_plan`),
`llm/prompts/step_planner.py`.

**Как.**
1. Модели:
   ```python
   class DashboardTilePlan(BaseModel):
       title: str
       step_id: int
       chart_type: str = "auto"      # bar|line|…|kpi|table
       is_kpi: bool = False
       insight_needed: bool = False

   class ResearchPlan(BaseModel):
       ...
       dashboard_title: str | None = None
       template_id: str | None = None
       dashboard_tiles: list[DashboardTilePlan] = []
   ```
2. `StepPlanner.plan`: если вопрос про дашборд (ключевые слова «дашборд»,
   «панель», «сводка» — простая проверка) — вызвать
   `templates.match(columns)` (B-02). Нашёлся шаблон → в промпт его плитки как
   обязательные шаги («сделай шаги под эти плитки»), `template_id` проставить
   в план после ответа. Не нашёлся → в промпт инструкцию составить 3–6 плиток.
3. `validate_plan`: каждая `dashboard_tiles[i].step_id` есть среди шагов плана;
   если `dashboard_requested` и плиток нет — ошибка для переспроса.

**Тесты.** `tests/unit/test_planner.py`: план с плитками на существующие шаги
валиден, на несуществующий — ошибка; при совпадении колонок проставлен `template_id`.

**Готово, когда**
- [ ] «Сделай дашборд с ежеквартальной статистикой продаж» на датасете продаж даёт план с ≥ 3 плитками.

### B-04 · Оркестратор собирает дашборд из результатов шагов (Б7, Б8 из #27)
**Кто:** @Terrum19 · **Срок:** 25.10 · **Зависит:** B-01, B-03 · **Эпик:** #27

**Сейчас.** В `run()`:
`DashboardGenerator().generate(request.session_id, [spec])` — один график, JSON-файл.

**Где.** `agent/orchestrator.py` (`run`, `Execution`), `agent/insight.py`,
`llm/prompts/insight_summary.py`, `core/dashboards/generator.py`.

**Как.**
1. `Execution` += `tables: dict[int, str]`, `plan: ResearchPlan` (заполняются в `_finish`).
2. Метод `_build_dashboard(question, execution, titles) -> dict`:
   для каждой `DashboardTilePlan`:
   - `table = execution.tables[tile.step_id]`;
   - `spec = build_vega_spec(tile.chart_type if != "auto" else select_chart(tile.title, table), table, db=self.db, titles=titles, title=tile.title)`;
   - `insight = await self.insight_gen.generate_tile_insight(tile.title, table, spec["usermeta"]["chart_type"]) if tile.insight_needed else None`;
   - собрать `DashboardTile(source_step_id=tile.step_id, result_table=table, spec=spec, insight=insight, is_kpi=tile.is_kpi, layout=…)`.
   Раскладка: KPI — в первый ряд по `w=3,h=2`, графики — по два в ряд `w=6,h=4`.
3. `InsightGenerator.generate_tile_insight(title, table, chart_type) -> str`:
   промпт `TILE_INSIGHT` (1–2 предложения), `llm.complete`, данные — `to_records(table, limit=30)`.
   Плитки без инсайта — без вызова LLM.
4. `DashboardGenerator.generate(session_id, title, tiles) -> Dashboard` (B-01) →
   событие `{"type": "dashboard", "data": DashboardPayload(...)}` (поля — в B-06).
5. При `dashboard_requested` общий график и инсайт **всё равно** отдаются
   (первым текстом идёт инсайт по результату `result_step`).

**Тесты.** `tests/integration/test_agent_cycle.py`: FakeLLM-план с 3 шагами и
3 плитками (одна KPI) → в SQLite дашборд с 3 плитками, у каждой `result_table`
и `snapshot_data`, в стриме `dashboard` после `chart`.

**Готово, когда**
- [ ] Тест зелёный; дашборд открывается через `GET /api/dashboards/{id}` (B-05).

---

## Бэкенд дашбордов (эпик #27)

### B-01 · DashboardStore на SQLite и расширение моделей (Б1, Б2, Б3)
**Кто:** @mb6ockatf · **Срок:** 22.10 · **Зависит:** — · **Эпик:** #27

**Сейчас.** `core/memory/artifact_registry.py` пишет JSON-файлы в
`storage/dashboards/*.json`, `list()` перебирает все файлы; модели
`core/dashboards/models.py` — `title, spec, sql, layout`.

**Где.** `core/dashboards/store.py` (новый), `core/dashboards/models.py`,
`core/dashboards/generator.py`, `core/memory/artifact_registry.py` (удалить или
оставить тонкой обёрткой над store).

**Как.**
1. `store.py`, `DashboardStore(path=settings.sqlite_path)` — тот же файл, что
   у `SessionMemory`; подключение как в `SessionMemory._connect`. Схема:
   ```sql
   CREATE TABLE IF NOT EXISTS dashboards (
     id TEXT PRIMARY KEY, session_id TEXT, title TEXT,
     template_id TEXT, created_at REAL, updated_at REAL);
   CREATE TABLE IF NOT EXISTS dashboard_tiles (
     id TEXT PRIMARY KEY, dashboard_id TEXT REFERENCES dashboards(id) ON DELETE CASCADE,
     order_index INTEGER, title TEXT, source_step_id INTEGER, result_table TEXT,
     spec_json TEXT, insight TEXT, is_kpi INTEGER,
     layout_json TEXT, snapshot_json TEXT, snapshot_at REAL);
   CREATE INDEX IF NOT EXISTS ix_dashboards_session ON dashboards(session_id);
   ```
   `PRAGMA foreign_keys = ON` при подключении.
2. Методы: `create(dashboard) -> str`, `get(id) -> Dashboard | None`,
   `list(session_id: str | None) -> list[DashboardSummary]`,
   `update(id, title=None, tiles=None)` (замена плиток в одной транзакции),
   `delete(id)`, `update_snapshot(tile_id, records)`.
3. Модели (`models.py`):
   ```python
   class DashboardTile(BaseModel):
       id: str = Field(default_factory=lambda: uuid4().hex[:12])
       title: str
       spec: dict[str, Any]
       source_step_id: int | None = None
       result_table: str | None = None
       insight: str | None = None
       is_kpi: bool = False
       layout: dict[str, int] = {"x": 0, "y": 0, "w": 6, "h": 4}
       snapshot_data: list[dict] | None = None
       snapshot_at: float | None = None

   class Dashboard(BaseModel):
       id: str = Field(default_factory=lambda: uuid4().hex[:12])
       session_id: str
       title: str
       template_id: str | None = None
       tiles: list[DashboardTile] = []
       created_at: float = Field(default_factory=time.time)
       updated_at: float = Field(default_factory=time.time)
   ```
4. `DashboardGenerator.generate(session_id, title, tiles, template_id=None) -> Dashboard`:
   для плиток с `result_table` — `snapshot_data = db.to_records(result_table, limit=SPEC_ROW_LIMIT)`,
   `snapshot_at = time.time()`; сохранить через store.
5. Старые JSON в `storage/dashboards/` не мигрируем (рантайм-данные).

**Тесты.** `tests/unit/test_dashboard_store.py`: CRUD, каскадное удаление плиток,
`list(session_id)` фильтрует, данные переживают новый экземпляр store
(файл во `tmp_path`), снимок пишется при `generate`.

**Готово, когда**
- [ ] Тесты зелёные, `ArtifactRegistry` нигде не используется для дашбордов.

### B-02 · Реестр шаблонов дашбордов (Б4)
**Кто:** @mb6ockatf · **Срок:** 22.10 · **Зависит:** B-01 · **Эпик:** #27

**Сейчас.** Шаблонов нет.

**Где.** `core/dashboards/templates.py` (новый).

**Как.**
1. Модели:
   ```python
   class TemplateTile(BaseModel):
       title: str
       purpose: str          # подсказка планировщику, какой шаг нужен
       chart_type: str
       is_kpi: bool = False
       insight_needed: bool = False

   class DashboardTemplate(BaseModel):
       id: str
       name: str
       required_roles: dict[str, list[str]]   # роль -> варианты имён/алиасов колонок
       tiles: list[TemplateTile]
   ```
   Роли, а не точные имена: `{"date": ["date", "order_date", "дата"], "amount": ["revenue", "amount", "сумма"]}`.
2. `match(columns: list[str], aliases: dict[str, list[str]] | None = None) -> DashboardTemplate | None`:
   шаблон подходит, если для каждой роли нашлась колонка (сравнение по имени
   и по алиасам из паспорта, без регистра). При нескольких — с наибольшим числом ролей.
3. 4 шаблона под датасеты хакатона: продажи (выручка по кварталам, топ товаров,
   KPI выручки и среднего чека), события приложения (DAU, воронка, удержание),
   техподдержка (обращения по статусам, время решения), доставка (сроки, доля опозданий).
   Колонки взять из `data/hk_datasets` после D-05.

**Тесты.** `tests/unit/test_dashboard_templates.py`: колонки продаж → `sales_quarterly`;
алиас из паспорта вместо имени → тот же шаблон; чужие колонки → `None`.

**Готово, когда**
- [ ] Для 4 датасетов хакатона `match` находит свой шаблон (проверка на реальных заголовках).

### B-05 · REST API дашбордов + пересчёт (Б9, Б11)
**Кто:** @mb6ockatf · **Срок:** 23.10 · **Зависит:** B-01 · **Эпик:** #27

**Сейчас.** `api/routes/dashboards.py`: `GET ?session_id=` (обязательный) и
`GET /{id}` (возвращает `{}` на несуществующий id).

**Где.** `api/routes/dashboards.py`, `api/schemas/dashboard.py`, `api/deps.py`.

**Как.**
1. Схемы (`api/schemas/dashboard.py`): `DashboardTileOut` = поля модели из B-01;
   `DashboardOut {id, session_id, title, template_id, tiles, created_at, updated_at}`;
   `DashboardSummary {id, session_id, title, tiles_count, created_at, updated_at}`;
   `DashboardCreate {session_id, title, tiles}`; `DashboardUpdate {title?, tiles?}`.
2. Эндпоинты:
   | Метод | Путь | Ответ |
   |---|---|---|
   | GET | `/api/dashboards?session_id=` (опц.) | `list[DashboardSummary]`, по `updated_at desc` |
   | GET | `/api/dashboards/{id}` | `DashboardOut` / 404 |
   | POST | `/api/dashboards` | `DashboardOut` (201) |
   | PUT | `/api/dashboards/{id}` | `DashboardOut` / 404 |
   | DELETE | `/api/dashboards/{id}` | 204 / 404 |
   | POST | `/api/dashboards/{id}/recalculate` | `DashboardOut` |
3. `recalculate`: для плиток с `result_table` — если таблица существует, перечитать
   `db.to_records`, пересобрать `spec` (`build_vega_spec(spec.usermeta.chart_type, data=...)`),
   обновить `snapshot_*`. Если таблицы нет (почищена D-06) — оставить снимок и
   вернуть в плитке `stale: true`.
4. `get_db` из `api/deps.py` — общий `app.state.db`.
5. ID плиток. В B-01 `dashboard_tiles.id` — первичный ключ на всю таблицу
   (на этом держится `update_snapshot(tile_id)`), а ID плитке выдаётся при
   создании объекта. Если `POST` сохранит присланные клиентом ID, сценарий
   «открыл дашборд → сохранил копию» упадёт с `IntegrityError` (500). Поэтому
   в `DashboardCreate.tiles` поля `id` нет (или оно игнорируется) — store
   выдаёт новые ID. В `PUT` ID сохраняются только у плиток этого же дашборда,
   плиткам с чужими или неизвестными ID выдаются новые.

**Тесты.** `tests/unit/test_dashboards_api.py` (`TestClient`): все эндпоинты,
404, `recalculate` после изменения данных в таблице меняет `snapshot_data`;
`POST` с плитками из `GET` другого дашборда — 201 и новые ID плиток.

**Готово, когда**
- [ ] Тесты зелёные; таблица эндпоинтов и схемы скопированы в issue F-07.

### B-06 · Превью плиток в SSE, KPI-спека, явный тип графика (Б10, Б12, Б13)
**Кто:** @mb6ockatf · **Срок:** 24.10 · **Зависит:** B-01 · **Эпик:** #27

**Сейчас.** `DashboardPayload.tiles: list[dict]` не типизирован; KPI-вида в
`vega_spec_builder` нет (`CHART_TYPES` без `kpi`).

**Где.** `api/schemas/chat.py`, `core/viz/vega_spec_builder.py`,
`core/viz/chart_selector.py`, `docs/visualization.md`.

**Как.**
1. `DashboardPayload { dashboard_id, title, tiles: list[DashboardTileOut] }` —
   `snapshot_data` в SSE не отправлять (данные уже в `spec.data.values`).
2. KPI: `chart_type="kpi"` → `_kpi(columns)`: одна метрика из первой строки,
   `mark: {"type": "text", "fontSize": 48}`, `encoding.text` = метрика;
   подпись — `title`. Добавить в `CHART_TYPES`, `_ENCODERS`, `_MARKS`.
3. `chart_selector.select_chart`: убрать TODO, явный `requested` уже уважается —
   добавить `"kpi"`, если результат — одна строка с одним числом.
4. Описать KPI-спеку в `docs/visualization.md`.

**Тесты.** `tests/unit/test_vega_spec_builder.py` (KPI), `tests/contract/test_chat_event_schema.py`
(событие `dashboard` валидируется по `DashboardPayload`). Покрытие viz-модулей остаётся 100 %.

**Готово, когда**
- [ ] Контрактный тест зелёный, пример события `dashboard` приложен к F-06.

### S-01 · SSE-события прогресса шагов
**Кто:** @mb6ockatf · **Срок:** 24.10 · **Зависит:** A-01, A-03 · **Эпик:** #20

**Сейчас.** Механизм доставки и контракт уже сделаны в A-03 (#39):
`ChatEvent.type` включает `"progress"`, `data` — `ProgressPayload`
(`api/schemas/chat.py`): `stage` (`planning`|`step`|`insight`|`chart`|`dashboard`),
`status` (`started`|`finished`|`failed`|`fixing`), необязательные `step_id`,
`tool`, `purpose`, `total_steps`, `elapsed_ms`, `attempt`, `error` (пустые
не сериализуются). План и шаги идут задачей `AgentOrchestrator._research`,
событие отправляется вызовом `emit(event)` — `run()` отдаёт его фронту из
очереди сразу. Шлётся только `step/fixing` (попытка исправления шага).
Контракт описан в `docs/architecture.md` («События стрима чата»).
Пока работает планировщик и шаги, остальных событий фронт не получает.

**Где.** `agent/orchestrator.py`, `tests/contract/`.

**Как.**
1. Добавить вызовы `emit` (по образцу `_fixing_event`):
   `planning started/finished(total_steps)` в `_research`, по каждому шагу
   `started/finished/failed(elapsed_ms)` в `_execute_plan`.
2. `insight started`, `chart started` — в `run()` после плана: там уже
   генератор, событие можно просто `yield`.

**Тесты.** `tests/contract/test_sse_serialization.py` — сериализация `progress`;
`tests/unit/test_orchestrator_unit.py` — порядок: `progress(planning)` → … → `text` → `chart` → `done`.

**Готово, когда**
- [ ] Все события из п. 1–2 есть в `docs/architecture.md` и скопированы в F-05.

---

## Данные и паспорт (эпик #12)

### D-05 · Загрузка 9 датасетов хакатона одной командой
**Кто:** @thebulkapro · **Срок:** 21.10 · **Зависит:** — · **Эпик:** #12

**Сейчас.** `data/hk_datasets/README.md` описывает ручное скачивание; скрипта
загрузки и проверки нет. Файлы до 200+ МБ, `;`, десятичная запятая.

**Где.** `scripts/load_hk_datasets.py` (новый), `Makefile`, `data/hk_datasets/README.md`.

**Как.**
1. Скрипт: аргументы `--dir data/hk_datasets --db $DUCKDB_PATH --no-llm`.
   Для каждого `*.csv`: `CSVConnector({table_name(file): path}).connect(db)`,
   замерить время; затем `SchemaEnricher(db).enrich(table)` (с `--no-llm` —
   только `profile_table` + `ProfileMemory.save_profile`), замерить время.
2. Вывести таблицу «файл → таблица, строк, колонок, загрузка с, профиль с, ошибки».
3. `make hk-data` (клонирование `HK_Datasets`, если папка пуста, + скрипт).
4. Результаты вставить в `data/hk_datasets/README.md`.
5. Каждую найденную проблему (не тот тип колонки, дата не распознана, падение) —
   отдельный issue с меткой `bug` и файлом-примером.

**Готово, когда**
- [ ] Все 9 файлов грузятся и профилируются; таблица в README; баги заведены.

### D-01 · Сессия работает с выбранной таблицей вместо жёсткого `raw`
**Кто:** @thebulkapro (ревью @Terrum19) · **Срок:** 22.10 · **Зависит:** A-01 · **Эпик:** #12

**Сейчас.**
- `orchestrator.py`: `SOURCE_TABLE = "raw"` — алиасы и глоссарий всегда от `raw`;
  `datasource = memory._state.get("datasource") or "raw"`.
- `session_memory.py`: `passport.load_profile(datasource or "raw")`.
- `SessionMemory.set_datasource` нигде не вызывается.
- `api/routes/chat.py` создаёт `AgentOrchestrator()` без `app.state.db` —
  отдельное соединение DuckDB на каждый запрос.

**Где.** `agent/orchestrator.py`, `core/memory/session_memory.py`,
`api/routes/chat.py`, `api/routes/sessions.py`.

**Как.**
1. `SessionMemory`: публичные `table` (property, читает `_state["datasource"]`)
   и `set_table(table: str, columns: list[str])` (переименовать `set_datasource`);
   убрать `"raw"` по умолчанию — `None` значит «таблица не выбрана».
2. `orchestrator.run()`: убрать `SOURCE_TABLE`; `table = memory.table`;
   если `None` — событие `refusal` (или `error`, если A-05 ещё не влит)
   «Сначала подключите данные» и `done`. Алиасы/глоссарий — от `table`.
3. `chat.py`: `def chat(payload, db = Depends(get_db))` → `AgentOrchestrator(db=db)`.
4. `sessions.py` `create_session`: если в `SessionCreate` передана `table` —
   `SessionMemory(session_id, db).set_table(table, columns_of(table))`.
5. e2e-тесты сейчас создают таблицу `raw` — оставить имя, но выбирать её явно
   через сессию.

**Тесты.** `tests/integration/test_memory_integration.py`: две таблицы, две
сессии с разными `table` — планировщик получает колонки своей таблицы;
сессия без таблицы → событие с просьбой подключить данные.

**Готово, когда**
- [ ] `grep -rn '"raw"' backend/app` пусто; тесты зелёные.

### D-02 · API выбора таблицы в сессии + 404 для неизвестной сессии
**Кто:** @thebulkapro · **Срок:** 22.10 · **Зависит:** D-01 · **Эпик:** #12

**Сейчас.** `sessions.py` хранит сессии в `_SESSIONS` (in-memory: после
рестарта `GET` → `KeyError` → 500), `SessionOut` без таблицы.

**Где.** `api/routes/sessions.py`, `api/schemas/chat.py`, `core/memory/session_memory.py`.

**Как.**
1. Схемы: `SessionCreate {datasource_id?, table?, title?}`;
   `SessionOut {session_id, datasource_id?, table?, title?, created_at}`;
   `SessionTableUpdate {table}`.
2. Убрать `_SESSIONS`: метаданные (`title`, `datasource_id`, `created_at`) — в
   `_state` `SessionMemory`; `SessionMemory.exists(session_id) -> bool`.
3. Эндпоинты: `GET /api/sessions` (список для сайдбара, по `updated_at desc`),
   `GET /api/sessions/{id}` (404), `PUT /api/sessions/{id}/table`
   (400, если таблицы нет в DuckDB; при смене таблицы — сбросить `last_chart`
   и `pending_recovery`), `GET /api/datasources/tables` — все пользовательские
   таблицы DuckDB без `temp_result_v*` и служебных.

**Тесты.** `tests/unit/test_sessions_api.py`: создание с таблицей, смена таблицы,
404, список переживает новый экземпляр приложения.

**Готово, когда**
- [ ] Схемы и эндпоинты скопированы в F-03.

### D-03 · Профиль и паспорт строятся автоматически при подключении
**Кто:** @thebulkapro · **Срок:** 23.10 · **Зависит:** D-01 · **Эпик:** #12

**Сейчас.** `POST /api/datasources/csv` и `/db` только создают таблицы/view;
`SchemaEnricher` вызывается лишь в тестах. Без паспорта `build_context`
не находит профиль, отбор колонок и описания не работают.

**Где.** `api/routes/datasources.py`, `api/schemas/datasource.py`,
`core/data/schema_enrichment.py`.

**Как.**
1. После `connector.connect(db)` для каждой таблицы:
   `await SchemaEnricher(db).enrich(table, comments=connector.column_comments(db, table))`.
   Эндпоинты сделать `async def` (сейчас синхронные).
2. Большие таблицы (> 1 млн строк или > 20 таблиц у БД): профиль — сразу,
   LLM-описания колонок — в фоне (`BackgroundTasks`), статус в `ProfileMemory`
   (`enrichment_status: pending|ready|failed`).
3. `DatasourceOut` += `profiles: list[TableBrief]`, где
   `TableBrief {table, rows, columns, period_from?, period_to?, enrichment_status, summary?}`
   (`summary` заполнит D-04).
4. LLM недоступна → паспорт без описаний, `SchemaEnricher.llm_available=False`
   уже это поддерживает; ответ 200, а не 500.

**Тесты.** `tests/unit/test_datasources_api.py`: CSV → в ответе `profiles` с
`rows` и периодом; `ProfileMemory.load_profile(table)` не `None`; с упавшим
FakeLLM — 200 и пустые описания.

**Готово, когда**
- [ ] Критерии приёмки #12 по профилю и паспорту выполнены.

### D-04 · «Что передо мной?»: LLM-описание, закэшированное в паспорте
**Кто:** @thebulkapro · **Срок:** 23.10 · **Зависит:** D-03 · **Эпик:** #12

**Сейчас.** Промпт `SCHEMA_DESCRIBE` (`llm/prompts/schema_describe.py`) нигде не
вызывается; вопрос «что передо мной» идёт в планировщик.

**Где.** `core/data/schema_enrichment.py`, `core/memory/profile_memory.py`,
`agent/orchestrator.py`.

**Как.**
1. `SchemaEnricher.describe_dataset(table) -> str`: профиль из `ProfileMemory`
   → `SCHEMA_DESCRIBE.format(profile=..., quality=format_quality(profile))`
   → `llm.complete`. Сохранить в паспорте (`ProfileMemory.save_summary(table, text, fingerprint)`);
   при совпадении отпечатка профиля — отдавать сохранённое без LLM.
2. Вызывать в D-03 после `enrich` (в фоне для больших таблиц) и класть в `TableBrief.summary`.
3. Оркестратор: детектор `is_overview_question(question)` (регулярка:
   «что передо мной», «что за данные», «за какой период», «сколько записей/строк»,
   «опиши данные») → событие `text` с `summary` + `chart` типа `table` по
   профилю колонок, без планировщика.

**Тесты.** `tests/unit/test_schema_enrichment.py`: второй вызов не идёт в LLM;
`tests/e2e/test_scenarios.py::test_scenario_1` — ответ содержит период и число строк.

**Готово, когда**
- [ ] Описание содержит суть, период, объём и замечания по качеству; #12 закрыт.

### D-06 · Очистка устаревших temp_result_vN
**Кто:** @mb6ockatf · **Срок:** 26.10 · **Зависит:** B-01 · **Эпик:** #20

**Сейчас.** `DuckDBManager.save_step_result` создаёт **постоянные** таблицы
`temp_result_vN`; они не удаляются никогда. На датасетах в сотни МБ файл
DuckDB растёт за демо на гигабайты.

**Где.** `core/data/duckdb_manager.py`, `main.py` (`lifespan`), `config.py`.

**Как.**
1. `DuckDBManager.cleanup_results(keep: set[str], older_than_s: int)`:
   удалить `temp_result_v*`, которых нет в `keep`. Время создания хранить в
   служебной таблице `_result_meta(name, created_at)` (заполнять в `save_step_result`).
2. `keep` = `result_table` всех плиток дашбордов (B-01) + `last_chart` всех сессий за 24 ч.
3. Запуск: при старте (`lifespan`) и фоновой задачей раз в `RESULT_TTL_S / 4`
   (`config.result_ttl_s = 6 * 3600`).

**Тесты.** `tests/unit/test_duckdb_manager.py`: старые таблицы удаляются,
таблицы плиток и свежие — нет.

**Готово, когда**
- [ ] После 50 вопросов подряд число `temp_result_v*` ограничено; дашборды открываются.

---

## Качество и выбор LLM

### Q-01 · Eval-набор вопросов по датасетам хакатона
**Кто:** @thebulkapro · **Срок:** 23.10 · **Зависит:** D-05

**Сейчас.** Есть e2e на FakeLLM (`tests/e2e/test_scenarios*.py`) с игрушечной
таблицей; качество на реальной модели и реальных данных не измеряется.

**Где.** `data/eval/questions.yaml` (новый), `data/eval/README.md`.

**Как.**
1. Формат записи:
   ```yaml
   - id: sales-03
     scenario: 3                 # 1..6 из README, 0 — «неудобный»
     dataset: 01_Продажи         # имя таблицы после D-05
     question: "Что происходит с выручкой каждый июль?"
     follow_up_of: null          # id предыдущего вопроса для сценария 6
     expect:
       tools_any: [seasonality, time_series]
       columns_all: [revenue, order_date]
       answer_contains: ["июл"]
       value: {sql: "SELECT ...", tolerance: 0.02}   # эталон считаем сами
       chart_type_any: [line, bar]
       refusal: false
   ```
2. ≥ 35 вопросов: по 5 на сценарии 1–6 (минимум на 5 разных датасетах) +
   5 неудобных (вне данных, удаление, личные данные, несуществующая колонка, бессмыслица).
3. Эталоны `value.sql` проверить руками в DuckDB.

**Готово, когда**
- [ ] Набор отревьюен @Terrum19 (план агента) и @dmitriy-ya (тулы).

### Q-02 · Скрипт прогона eval
**Кто:** @thebulkapro · **Срок:** 24.10 · **Зависит:** Q-01, D-01

**Где.** `scripts/eval.py` (новый), `Makefile`, `.github/workflows/agent-smoke.yml`.

**Как.**
1. Для каждого вопроса: новая сессия с `table=dataset` (D-01), прогон
   `AgentOrchestrator(db=...).run(ChatRequest(...))`, сбор всех событий,
   время до первого события и до `done`; `follow_up_of` — в той же сессии.
2. Проверки из `expect`: инструменты и колонки — из `SessionMemory` шагов
   (или из событий `progress` после S-01); `value` — сравнение с эталоном в
   `result_table`; `answer_contains` — по `text.headline + details`;
   `refusal` — по событию `refusal`.
3. Отчёт `docs/reports/eval_<YYYY-MM-DD>_<model>.md`: общий pass %, по
   сценариям, p50/p95, список провалов с причиной; JSON рядом.
4. `make eval MODEL=...`. В `agent-smoke.yml` — прогон на FakeLLM по 1 вопросу
   на сценарий, чтобы скрипт не ломался.

**Готово, когда**
- [ ] Отчёт генерируется и на FakeLLM, и на реальной модели (`LLM_BASE_URL`).

### Q-03 · Выбор модели по eval
**Кто:** @thebulkapro, решение @Terrum19 · **Срок:** 25.10 · **Зависит:** Q-02

**Как.**
1. Кандидаты через Ollama: `qwen2.5-coder:7b`, `qwen2.5-coder:14b`,
   `qwen2.5-coder:32b` (если хватает памяти), `qwen2.5:14b-instruct`.
2. Для каждого: `make eval`, пиковая VRAM/RAM, p50/p95.
3. Таблица в `docs/reports/llm_choice.md`; модель по умолчанию и запасная для
   машин без GPU.
4. Обновить `config.py` (`llm_model`), `.env.example`, README (раздел «Железо»).

**Готово, когда**
- [ ] Решение записано в issue и в `docs/reports/llm_choice.md`; I-01 разблокирован.

### Q-04 · Ежедневный eval и разбор провалов
**Кто:** @thebulkapro · **Срок:** 26–30.10 · **Зависит:** Q-03

**Как.** Каждый день утром — прогон на `dev` и выбранной модели; отчёт в чат
команды; каждый провал — issue с меткой `eval` и владельцем:
план/промпт → @Terrum19, тул → @dmitriy-ya, данные/паспорт → себе,
отображение → @Trewwws.

**Готово, когда**
- [ ] К 28.10 eval ≥ 80 %, 0 падений; к 30.10 — без регрессий.

---

## Коннекторы и экспорт

### C-01 · Коннектор ClickHouse (#7)
**Кто:** @dmitriy-ya · **Срок:** 22.10 · **Зависит:** — · **Эпик:** #7

**Сейчас.** `connectors/clickhouse.py` — `NotImplementedError`; в фабрике
(`connectors/factory.py`) уже зарегистрирован; интеграционный тест
`tests/integration/test_clickhouse_connector.py` и сервис ClickHouse 24.8 в
`backend-ci.yml` уже есть — тест сейчас пропускается.

**Где.** `core/data/connectors/clickhouse.py`, `backend/requirements.txt`,
`api/routes/datasources.py` (обработка ошибок).

**Как.** Образец — `connectors/postgres.py` (`attach`, `list_tables`,
`create_view`, `fail`, секреты).
1. Разбор DSN `clickhouse://user:pass@host:8123/db` (`urllib.parse`); пароль не
   попадает в `__repr__`, логи и тексты ошибок.
2. Основной путь — HTTP-интерфейс ClickHouse (порт 8123):
   - список таблиц: `SELECT name FROM system.tables WHERE database = {db}`
     (ограничение `settings.postgres_max_tables` → завести общий `db_max_tables`);
   - данные: `SELECT * FROM db.t FORMAT Parquet` → во временный файл
     `storage/uploads/<id>/<t>.parquet` → `CREATE OR REPLACE TABLE <view_name> AS SELECT * FROM read_parquet(...)`.
     Для больших таблиц — `LIMIT` из `settings.clickhouse_max_rows` с
     предупреждением в ответе.
   - HTTP-клиент: `urllib.request` (как в тесте) или `httpx`, без новых тяжёлых
     зависимостей.
3. Комментарии колонок: `SELECT name, comment FROM system.columns` →
   `column_comments()` (пойдут в паспорт через D-03).
4. Ошибки: `ClickHouseConnectionError` (→ 502), неверный DSN → `ValueError` (→ 400)
   в `datasources.py`, по образцу Postgres.
5. Имена view — через тот же механизм, что у Postgres (`existing_names`, суффиксы).

**Готово, когда**
- [ ] `test_clickhouse_tables_become_views` проходит (не skip) локально на `docker compose --profile integration up -d clickhouse`.
- [ ] `POST /api/datasources/db {"kind": "clickhouse", ...}` возвращает таблицы.

### C-02 · ClickHouse: тесты, CI и документация
**Кто:** @dmitriy-ya · **Срок:** 22.10 · **Зависит:** C-01 · **Эпик:** #7

**Где.** `tests/integration/test_clickhouse_connector.py`,
`tests/unit/test_connectors_clickhouse.py` (новый), `docs/data_connectors.md`.

**Как.**
1. Убрать `except NotImplementedError: pytest.skip(...)` из интеграционного теста;
   добавить тесты: кириллические имена колонок, пустая таблица, неверный пароль → понятная ошибка без пароля в тексте.
2. Юнит-тесты без сервера: разбор DSN, маскировка секрета, построение SQL
   (HTTP замокать).
3. `docs/data_connectors.md`: раздел ClickHouse (DSN, ограничения, лимит строк).

**Готово, когда**
- [ ] Job integration в CI выполняет тесты ClickHouse (не skip) и зелёный; #7 закрыт.

### E-01 · PDF-отчёт по ответу агента
**Кто:** @dmitriy-ya · **Срок:** 24.10 · **Зависит:** —

**Сейчас.** `core/export/pdf.py` — `NotImplementedError`; `artifacts.py`
отвечает 501 на `format=pdf`. Excel экспортирует только таблицу + столбчатую диаграмму.

**Где.** `core/export/pdf.py`, `core/export/charts.py` (новый),
`api/routes/artifacts.py`, `api/schemas/chat.py` (`ExportRequest`),
`backend/requirements.txt`, `backend/Dockerfile` (шрифты).

**Как.**
1. Рендер графика: `vl-convert-python` (`vl_convert.vegalite_to_png(spec, scale=2)`),
   без браузера и сети. Перед рендером убрать `width: "container"` → 640.
2. PDF: ReportLab (`platypus`: заголовок, `headline`, `details` списком,
   картинка графика, таблица первых 50 строк результата). Typst — только если
   успеваем; иначе убрать упоминание из README.
3. Кириллица: шрифт DejaVu Sans (`fonts-dejavu-core` в Dockerfile;
   `pdfmetrics.registerFont(TTFont("DejaVu", path))`).
4. `ExportRequest` += `question?`, `headline?`, `details?`, `spec?` —
   фронт передаёт то, что показал пользователю. Если не передано — только таблица.
5. `artifacts.py`: `format=pdf` → `PdfExporter().export_answer(...)`, `FileResponse(media_type="application/pdf")`.

**Тесты.** `tests/unit/test_export_pdf.py`: PDF открывается (`pypdf`), содержит
кириллический заголовок и картинку; API-тест на 200 и `application/pdf`.

**Готово, когда**
- [ ] PDF по ответу скачивается и читается, кириллица без «квадратиков».

### E-02 · Экспорт дашборда в PDF и XLSX
**Кто:** @dmitriy-ya · **Срок:** 25.10 · **Зависит:** E-01, B-01

**Где.** `core/export/pdf.py`, `core/export/excel.py`.

**Как.**
1. `PdfExporter.export_dashboard(dashboard: Dashboard, path)`: титульная страница
   (название, дата снимка), далее по плитке на блок: заголовок, картинка спеки
   (`charts.py`), инсайт; KPI — крупной цифрой в таблице сверху.
2. `ExcelExporter.export_dashboard(dashboard, path)`: лист «Сводка» (KPI +
   оглавление), лист на плитку с `snapshot_data`; тип диаграммы openpyxl по
   `spec.usermeta.chart_type` (`bar` → `BarChart`, `line` → `LineChart`,
   остальное — без диаграммы).
3. Исправить текущий `ExcelExporter.export`: сейчас диаграмма строится всегда
   по 2-й колонке, даже если она не числовая — выбирать первую числовую.

**Тесты.** `tests/unit/test_export_dashboard.py`: дашборд из 3 плиток (одна KPI)
в оба формата; в XLSX 4 листа.

**Готово, когда**
- [ ] Оба файла открываются в LibreOffice/Excel и Acrobat без ошибок.

### E-03 · Эндпоинт экспорта дашборда
**Кто:** @dmitriy-ya · **Срок:** 25.10 · **Зависит:** E-02, B-05

**Где.** `api/routes/dashboards.py` или `api/routes/artifacts.py`, `docs/architecture.md`.

**Как.**
1. `GET /api/dashboards/{id}/export?format=pdf|xlsx` → `FileResponse` с
   `filename="<title>_<дата>.<ext>"` (транслитерация или RFC 5987 для кириллицы
   в `Content-Disposition`).
2. 404 на несуществующий дашборд; 400 на неизвестный формат.

**Тесты.** API-тест на оба формата и ошибки.

**Готово, когда**
- [ ] Контракт эндпоинта скопирован в F-10.

---

## Тулы (эпик #25)

### T-01 · Тулы для сценария 3: аномалии, лаги, «каждый июль»
**Кто:** @dmitriy-ya · **Срок:** 27.10 · **Зависит:** A-01 · **Эпик:** #25

**Сейчас.** Есть `timeseries/seasonality.py`, `timeseries/forecast.py`,
`domain/seasonal_decompose.py`, `domain/trend_analysis.py`; нет детекции
аномалий, лаговой корреляции и сравнения одного периода по годам.

**Где.** `core/tools/stats/detect_anomalies.py`, `core/tools/timeseries/lag_correlation.py`,
`core/tools/timeseries/period_compare.py` (новые), `core/tools/__init__.py`/`registry.py`.

**Как.** Формат — как у существующих тулов после A-01: класс `BaseTool`,
вложенный `Args(ToolArgs)` с `Ident`/`Expr`, `kind`, `execute(db, input_table, args) -> str`
через `db.save_step_result`.
1. `detect_anomalies` (`analysis`): `Args{value: Ident, by: Ident | None, period: Ident | None, method: Literal["iqr","zscore"]="iqr", threshold: float=1.5}`;
   SQL с оконными функциями (`quantile_cont` по группе), результат — строки-выбросы
   + колонка `score`.
2. `lag_correlation` (`analysis`): `Args{date: Ident, x: Ident, y: Ident, max_lag: int=12, interval: Literal["day","week","month"]="month"}`;
   агрегирование по `date_trunc`, `corr(x, lag(y, k))` для k = 0..max_lag → таблица `lag, corr`.
3. `period_compare` (`transform`): `Args{date: Ident, value: Ident, part: Literal["month","quarter","weekday"], agg="sum"}`;
   `date_part(part, date)`, `year(date)` → таблица `year, part, value` + отклонение от среднего по `part`.
4. Описания тулов — по-русски, с примером, когда использовать (это видит планировщик).

**Тесты.** `tests/unit/test_advanced_tools.py`: синтетические ряды с известным
выбросом, лагом 3 и всплеском в июле.

**Готово, когда**
- [ ] Вопросы сценария 3 из eval выбирают эти тулы и проходят.

### T-02 · Исправить тулы, проваленные в eval
**Кто:** @dmitriy-ya · **Срок:** 27–29.10 · **Зависит:** Q-04 · **Эпик:** #25

**Как.** Взять issues с метками `eval` + «тул» из отчётов Q-04. Типовые причины:
неверное имя аргумента в `describe()`, отсутствие приведения типов
(`TRY_CAST` для чисел с запятой), пустой результат без понятной ошибки.
На каждый провал — регрессионный тест в `tests/unit/test_tools.py`.

**Готово, когда**
- [ ] Все провалы категории «тул» закрыты или прокомментированы с причиной.

---

## Фронтенд (эпик #28) — только `frontend/`

### F-01 · Очистить фронт-ветку: только frontend/, моки только по флагу
**Кто:** @Trewwws · **Срок:** 21.10 · **Зависит:** — · **Эпик:** #28

**Сейчас.** `feature/frontend` отстаёт от `dev` на 12 коммитов и меняет 31 файл
вне `frontend/` (бэкенд, `Makefile`, `infra`). `api/client.ts` при любой ошибке
бэкенда **тихо** переключается на моки (`mockStreamChat` и т.д.) — на демо это
скроет падение бэкенда фейковым ответом.

**Где.** Новая ветка `feature/F-01-frontend-ui` от `dev`; `frontend/src/api/client.ts`.

**Как.**
1. `git checkout -b feature/F-01-frontend-ui origin/dev && git checkout origin/feature/frontend -- frontend/`.
2. `client.ts`: моки — только если `isMockMode()` (флаг `NEXT_PUBLIC_USE_MOCKS=true`
   или `localStorage.sber_force_mock`); в `catch` — пробрасывать ошибку
   (`throw new ApiError(status, detail)`), UI показывает её (F-11).
3. Типы: убрать дубли camelCase в `types/dashboard.ts` — только snake_case, как в Pydantic.
4. `npm run lint && npm run build`.

**Готово, когда**
- [ ] `git diff --stat origin/dev...` — только `frontend/`; lint и build зелёные; PR влит.
- [ ] Остановленный бэкенд даёт в UI ошибку, а не мок-ответ.

### F-02 · Синхронизировать типы чата с контрактами dev
**Кто:** @Trewwws · **Срок:** 22.10 · **Зависит:** A-01, F-01 · **Эпик:** #28

**Сейчас.** `types/chat.ts` знает `text | chart | dashboard | error | done`;
нет `clarification_needed`, `ChatRequest.recovery_response`.

**Где.** `frontend/src/types/chat.ts`, `frontend/src/types/datasource.ts`,
`frontend/src/api/client.ts` (`streamChat`).

**Как.**
1. Добавить `ClarificationEvent {type: "clarification_needed", data: {step_id, error, suggestions: string[]}}`;
   заготовить `RefusalEvent` (A-05) и `ProgressEvent` (S-01) по контрактам из этих issues.
2. `streamChat(sessionId, question, onEvent, onClose, recoveryResponse?)` — передаёт
   `recovery_response`.
3. `types/datasource.ts` ← `api/schemas/datasource.py`.

**Готово, когда**
- [ ] С бэкендом на FakeLLM (`USE_FAKE_LLM=true`): вопрос → инсайт → график без моков.

### F-03 · Выбор таблицы в сессии и паспорт после загрузки
**Кто:** @Trewwws · **Срок:** 22.10 · **Зависит:** D-02 · **Эпик:** #28

**Где.** `components/chat/DatasourcePanel.tsx`, `store/sessionStore.ts`,
`api/client.ts`, `components/layout/TopHeader.tsx`.

**Как.**
1. `client.ts`: `listTables()`, `setSessionTable(sessionId, table)`, `listSessions()` (контракты из D-02).
2. После загрузки CSV / подключения БД — карточки из `DatasourceOut.profiles`
   (D-03): таблица, строки, период, `summary`; кнопка «Работать с этой таблицей» →
   `setSessionTable`.
3. В шапке сессии — текущая таблица с выпадающим списком смены.
4. Форма подключения БД: `kind` = `postgres | clickhouse` (C-01), подсказка формата DSN.
5. `enrichment_status = pending` → бейдж «описание готовится».

**Готово, когда**
- [ ] Загрузка CSV → описание «что передо мной» видно без вопроса; смена таблицы меняет ответы агента.

### F-04 · UI уточнений и отказов
**Кто:** @Trewwws · **Срок:** 23.10 · **Зависит:** F-02 · **Эпик:** #28

**Где.** `components/chat/ChatPanel.tsx`, новый `components/chat/ClarificationCard.tsx`.

**Как.**
1. `clarification_needed` → карточка: текст ошибки человеческим языком,
   кнопки из `suggestions`. Кнопка «Пропустить этот шаг…» →
   `streamChat(..., recoveryResponse=JSON.stringify({action: "skip_step"}))`;
   «Уточнить колонки» / «Переформулировать» → поле ввода, ответ уходит как
   `recovery_response` свободным текстом (формат см. `_apply_recovery`).
2. `refusal` (A-05) → сообщение-отказ отдельного стиля + чипы-переформулировки
   (клик отправляет как новый вопрос).
3. Пока карточка активна — основное поле ввода подсказывает «ответьте на уточнение или задайте новый вопрос».

**Готово, когда**
- [ ] Сценарий «сбой шага → кнопка → продолжение» работает с реальным бэкендом.

### F-05 · Прогресс шагов в чате
**Кто:** @Trewwws · **Срок:** 24.10 · **Зависит:** S-01 · **Эпик:** #28

**Где.** `components/chat/ChatPanel.tsx`, новый `components/chat/StepProgress.tsx`.

**Как.** По событиям `progress`: «Планирую…» → список шагов
(`purpose`, статус-иконка, время). После `done` — свёрнут в строку «5 шагов · 12 с»,
по клику раскрывается. Шаг со статусом `fixing` — «исправляю (попытка
`attempt`)», текст `error` — во всплывающей подсказке; если затем пришёл
`finished` — «исправлен». Событие `step/fixing` бэкенд уже шлёт (A-03),
контракт — `ProgressPayload` в `docs/architecture.md` («События стрима чата»).

**Готово, когда**
- [ ] До прихода инсайта видно, что агент делает.

### F-06 · Превью дашборда в чате из SSE
**Кто:** @Trewwws · **Срок:** 25.10 · **Зависит:** B-06 · **Эпик:** #28

**Где.** `components/chat/ChatPanel.tsx`, `components/canvas/DashboardTile.tsx`, `lib/tileLayout.ts`.

**Как.** Событие `dashboard` → компактная сетка плиток из `data.tiles`
(`DashboardTile` в режиме `preview`: график/KPI + инсайт), кнопки «Открыть
дашборд» (`/dashboard/[id]`) и «Открыть на канвасе». Никакого `GET` после события.

**Готово, когда**
- [ ] Дашборд из чата рендерится сразу, ссылка ведёт на полную страницу.

### F-07 · Страницы дашбордов на реальном API
**Кто:** @Trewwws · **Срок:** 24.10 · **Зависит:** B-05 · **Эпик:** #28

**Где.** `api/client.ts`, `app/dashboard/[id]/page.tsx`, `app/dashboards/page.tsx`,
`components/layout/LeftSidebar.tsx`.

**Как.**
1. Методы `client.ts` (уже есть заготовки) привести к контракту B-05: пути,
   коды ответов (404 → экран «не найден», 204 на DELETE).
2. `/dashboard/[id]`: заголовок, «Снимок от …» (`snapshot_at`), кнопка
   «Обновить данные» → `recalculate`; плитки со `stale: true` — пометка «данные устарели».
3. `/dashboards`: список (`GET /api/dashboards`), удаление с подтверждением.
4. Сайдбар: ссылка на `/dashboards` и последние 5 дашбордов.

**Готово, когда**
- [ ] Дашборд, созданный в чате, открывается после перезагрузки страницы и после рестарта бэкенда.

### F-08 · Канвас: drag&drop, resize, сохранение раскладки
**Кто:** @Trewwws · **Срок:** 26.10 · **Зависит:** F-07 · **Эпик:** #28

**Сейчас.** `react-grid-layout` уже в зависимостях, `Canvas.tsx` и
`lib/tileLayout.ts` есть в ветке.

**Где.** `components/canvas/Canvas.tsx`, `store/canvasStore.ts`.

**Как.**
1. `ResponsiveGridLayout` (12 колонок, `rowHeight` 60), `layout` плиток ↔
   `{x, y, w, h}` из API; `onLayoutChange` → `canvasStore.updateTileLayout`.
2. Сохранение: debounce 800 мс → `updateDashboard(id, {tiles})`; индикатор
   «Сохранено / Сохраняю / Ошибка».
3. Действия плитки (`ItemActionMenu`): переименовать, удалить, сменить тип
   графика (через спеку без LLM — тип в `usermeta.chart_type`, перестроение — `POST /recalculate`).
4. Минимальные размеры: KPI 2×2, график 4×3.

**Готово, когда**
- [ ] Раскладка переживает перезагрузку; на 1280px и 1920px ничего не налезает.

### F-09 · Клик по плитке/точке → «дообследуй»
**Кто:** @Trewwws · **Срок:** 27.10 · **Зависит:** F-08 · **Эпик:** #28

**Где.** `components/charts/ChartRenderer.tsx`, `components/canvas/DashboardTile.tsx`, `store/sessionStore.ts`.

**Как.**
1. Vega: `view.addEventListener("click", (e, item) => item?.datum)` в
   `react-vega` (`onNewView`); ECharts — `onEvents={{click}}`.
2. Из `datum` и `spec.usermeta.columns` собрать вопрос:
   «Дообследуй: {title}, {поле} = {значение}. Почему так и что на это влияет?»
   и отправить в чат сессии плитки (`session_id` дашборда), открыв чат-панель.
3. Пункт «Дообследовать» в меню плитки — без конкретной точки.

**Готово, когда**
- [ ] Клик по столбцу «Июль» отправляет вопрос, агент отвечает про июль.

### F-10 · Кнопки экспорта xlsx/pdf
**Кто:** @Trewwws · **Срок:** 27.10 · **Зависит:** E-01, E-03 · **Эпик:** #28

**Где.** `components/chat/ChatPanel.tsx`, `app/dashboard/[id]/page.tsx`, `api/client.ts`.

**Как.**
1. У ответа агента: «Excel» / «PDF» → `POST /api/artifacts/export`
   (`result_ref = spec.usermeta.result_table`, `headline`, `details`, `spec`).
2. У дашборда: `GET /api/dashboards/{id}/export?format=…`.
3. Скачивание через `blob` + `a[download]`, имя файла из `Content-Disposition`;
   спиннер и ошибка в тосте.

**Готово, когда**
- [ ] Оба формата скачиваются для ответа и для дашборда.

### F-11 · Полировка UX
**Кто:** @Trewwws · **Срок:** 29.10 · **Зависит:** — · **Эпик:** #28

**Как.** Чек-лист:
- [ ] пустые состояния: нет данных, нет сессий, нет дашбордов — с призывом к действию;
- [ ] загрузчики на всех запросах, скелетоны на страницах дашбордов;
- [ ] ошибки сети и 4xx/5xx — понятный текст + «Повторить»;
- [ ] все тексты через `i18n/dictionary.ts`, без английских заглушек;
- [ ] 0 ошибок и предупреждений в консоли на сквозном сценарии R-01;
- [ ] светлая и тёмная тема у графиков (`lib/chartTheme.ts`);
- [ ] `next build` без предупреждений.

---

## Сборка и релиз

### I-01 · docker compose с Ollama и выбранной моделью
**Кто:** @mb6ockatf · **Срок:** 26.10 · **Зависит:** Q-03

**Сейчас.** `docker-compose.yml`: backend, frontend, postgres, clickhouse
(профиль `integration`); LLM ожидается на хосте. `node` для песочницы в образ
backend уже копируется (`backend/Dockerfile`, стадия `node:20-slim`).

**Где.** `docker-compose.yml`, `docker-compose.release.yml`, `backend/Dockerfile`,
`infra/ollama/entrypoint.sh` (новый), `.env.example`, `Makefile`.

**Как.**
1. Сервис `ollama` (`ollama/ollama`, том `ollama-models`), entrypoint:
   `ollama serve &` → `ollama pull $LLM_MODEL` (если нет) → `wait`;
   healthcheck `ollama list | grep $LLM_MODEL`.
2. Профили: `gpu` (`deploy.resources.reservations.devices`) и `cpu`
   (запасная модель из Q-03). `make up` = `cpu` по умолчанию, `make up-gpu`.
3. Backend: `LLM_BASE_URL=http://ollama:11434/v1`, `depends_on: ollama: service_healthy`.
4. Песочница: проверить, что пакеты Pyodide скачиваются на этапе сборки
   (`backend/sandbox/fetch_packages.py`, см. `docs/sandbox.md`), а не при первом запуске без сети.
5. Опционально `make demo-data`: монтирует `data/sample_datasets` и загружает их при старте.

**Готово, когда**
- [ ] На чистой машине: `git clone` → `cp .env.example .env` → `make up` → рабочий UI с ответами модели.

### I-02 · Smoke-тест чистого запуска в CI
**Кто:** @mb6ockatf · **Срок:** 27.10 · **Зависит:** I-01

**Где.** `.github/workflows/docker.yml` (или новый `e2e-docker.yml`), `scripts/smoke.sh` (новый).

**Как.** Job: `docker compose up -d --build` с `USE_FAKE_LLM=true` и без ollama
(профиль `ci`) → ждать healthcheck'и → `smoke.sh`: `POST /api/datasources/csv`
с `demo_events.csv` → `POST /api/sessions` → `POST /api/chat` (читать SSE до `done`)
→ `GET /` фронта 200.

**Готово, когда**
- [ ] Job зелёный на `dev` и обязателен для PR в `main`.

### I-03 · README для жюри
**Кто:** @thebulkapro · **Срок:** 29.10 · **Зависит:** I-01

**Где.** `README.md`, `docs/roadmap.md`, `docs/img/` (скриншоты).

**Как.** Разделы: что это (3 предложения) → быстрый старт (≤ 3 команды) →
требования к железу и выбор модели (из Q-03) → проход по 6 сценариям со
скриншотами и примерами вопросов на датасетах хакатона → архитектура (ссылка
на `docs/architecture.md`) → ограничения → результаты eval (ссылка на последний отчёт).
Отметить выполненное в `docs/roadmap.md`.

**Готово, когда**
- [ ] Человек вне команды поднимает проект по README без вопросов (проверить на ком-то).

### R-01 · Bug bash по сквозному сценарию
**Кто:** все · **Срок:** 28.10

**Как.** Каждый проходит сценарий на своей машине через `make up`:
CSV хакатона → «что передо мной» → вопрос → график → «покажи иначе» →
«а теперь для X» → дашборд → канвас (перетащить, сохранить) → клик «дообследуй» →
PDF и XLSX → подключение ClickHouse → неудобный вопрос.
Каждый баг — issue с меткой `bug`, шагами воспроизведения и владельцем по зоне.

### R-02 · Релиз v1.0.0
**Кто:** @Terrum19, @mb6ockatf · **Срок:** 30.10

**Как.** PR `dev → main` (закрыть #30) с зелёными CI и I-02 → тег `v1.0.0` →
`release.yml` публикует образы → `docker-compose.release.yml` ссылается на тег →
`ChangeLog` за период → README ссылается на релиз.

**Готово, когда**
- [ ] Чистый запуск из тега проходит I-02.

---

## Что закрыть сейчас

- #26 (WASM) — влит в `dev`, закрыть.
- Ветки `feature/tools`, `feature/wasm-sandbox`, `feature/WASM-playground` — удалить после проверки, что всё влито.
- #31, #32 — проверить в A-03.
