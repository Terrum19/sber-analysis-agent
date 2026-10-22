# WASM-песочница и кодогенерация

Когда ни один тул из реестра не подходит под шаг плана, планировщик ставит
`needs_llm_codegen = true` и `tool = null`, а в `purpose` описывает, что
посчитать. Шаг с неизвестным именем тула обрабатывается так же: ошибки
«инструмент не найден» нет.

## Поток

1. `StepExecutor.execute` передаёт шаг в `run_generated_code`
   (`backend/app/core/sandbox/codegen_runner.py`).
2. Раннер собирает промпт `CODEGEN` (`app/core/llm/prompts/codegen.py`):
   цель шага, колонки, схема входной таблицы и первые 5 строк.
3. `LLMClient.complete` возвращает код; раннер вырезает его из блока
   ` ```python `.
4. Входная таблица целиком выгружается в `input.csv` во временной папке
   запуска (`STORAGE_DIR/sandbox/run_*`), код исполняется в
   `WASMSandbox` (`app/core/sandbox/wasm_runtime.py`).
5. В коде уже есть `df` (pandas.DataFrame из `input.csv`); результат
   присваивается переменной `result` (DataFrame, Series, словарь колонок
   или список словарей). Раннер Pyodide пишет его в `result.csv`.
6. `result.csv` сохраняется в DuckDB как `temp_result_vN` и идёт дальше по
   цепочке (инсайт, график, дашборд). Папка запуска удаляется.
7. Если код упал, LLM получает текст ошибки (`CODEGEN_FIX`) и пишет
   исправление; всего `CODEGEN_ATTEMPTS` попыток (по умолчанию 2), затем
   ошибка `SandboxError` уходит в оркестратор, и он сразу просит
   пользователя уточнить (`clarification_needed`): шаг кодогенерации
   планировщик не исправляет, свой код раннер уже исправлял сам.

Тул `run_wasm_python` (`app/core/tools/run_wasm_python.py`, в реестр не
зарегистрирован) исполняет готовый код через ту же песочницу: таблица
`target_table` приходит как `df`, `result` становится `temp_result_vN`;
скалярный результат или список значений кладётся в колонку `output_col`.

Типы колонок результата DuckDB определяет заново по CSV (`read_csv_auto`).

## Рантайм

Python 3.13 в Pyodide 0.29.5 под Node.js 20 или новее (`backend/sandbox/`):

- `runner.mjs` — загрузка Pyodide, монтирование папки запуска, изоляция,
  запуск кода; обмен с бэкендом — JSON через stdin/stdout;
- `package.json`, `package-lock.json` — версия Pyodide;
- `fetch_packages.py` — скачивает колёса numpy, pandas, scipy,
  scikit-learn, statsmodels и их зависимостей (с проверкой sha256) в
  `node_modules/pyodide`, чтобы в рантайме сеть не была нужна.

Установка (один раз; в Docker-образе и CI делается на сборке):

```sh
cd backend/sandbox
npm ci
python3 fetch_packages.py
```

Без рантайма `WASMSandbox.run` поднимает `SandboxError`, а тесты, которым
нужен настоящий Pyodide, пропускаются.

## Изоляция

- Код видит только файловую систему Pyodide в памяти и папку запуска,
  смонтированную как `/sandbox`. Файлы хоста за её пределами недоступны.
- Node.js запускается с моделью разрешений (`--experimental-permission`
  на Node 20-21, `--permission` с 22-й версии): чтение — только
  `runner.mjs`, `package.json`, `node_modules/pyodide` и папка запуска,
  запись — только папка запуска; дочерние процессы, воркеры и нативные
  аддоны запрещены.
- Внутри Pyodide отключены мост в JavaScript (`js`, `pyodide_js`,
  `pyodide.code`, `pyodide.ffi`, `pyodide.http`, `micropip`), сокеты,
  `subprocess`, `os.system`/`os.popen`/`exec*`/`spawn*`, симлинки и
  жёсткие ссылки. В JS удалены `fetch`, `WebSocket`, `XMLHttpRequest`.
- Если ядро разрешает непривилегированные user namespaces, процесс ещё и
  запускается через `unshare -rn` в пустом сетевом пространстве имён.
  Там, где это запрещено (Docker, Ubuntu 24.04 с AppArmor), остаются
  остальные слои.
- Окружение процесса — только `PATH` и `HOME` (папка запуска).

`tests/unit/test_sandbox.py` проверяет, что из песочницы нельзя прочитать
или записать файлы вне папки, создать симлинк, запустить процесс, открыть
сокет или HTTP-соединение и достучаться до JavaScript.

## Ресурсы

| Настройка | По умолчанию | Смысл |
| --- | --- | --- |
| `WASM_TIMEOUT_S` | 60 | жёсткий таймаут запуска, включая загрузку Pyodide |
| `WASM_MEM_MB` | 2048 | предел RSS процесса |
| `WASM_MAX_PARALLEL` | 1 | сколько песочниц работает одновременно |
| `WASM_NODE_BIN` | `node` | бинарник Node.js |
| `WASM_RUNTIME_DIR` | `backend/sandbox` | папка рантайма |
| `CODEGEN_ATTEMPTS` | 2 | попыток сгенерировать рабочий код |

- Предел RSS — `WASM_MEM_MB`, но не больше свободной RAM системы
  (`MemAvailable`) на момент запуска. Бэкенд опрашивает RSS процесса
  каждые 100 мс и при превышении или таймауте убивает всю группу
  процессов (SIGKILL).
- `RLIMIT_AS`/`RLIMIT_DATA` не подходят: V8 резервирует под WASM
  гигабайты адресного пространства и с ними не стартует даже пустой
  запуск. Память самого WASM в Pyodide ограничена 4 ГБ (wasm32).
- Процесс запускается через `nice -n 10`, чтобы не мешать интерактивной
  работе.
- Загрузка Pyodide с pandas занимает около 10 с и 370 МБ RSS, со scipy и
  scikit-learn — до 20 с и около 1 ГБ.
