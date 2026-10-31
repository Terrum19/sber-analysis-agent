"""Файл ``.session``: сессия целиком с таблицами результатов (#251).

Полный снапшот: открывается без доступа к источнику автора. zip из трёх частей:

- ``manifest.json`` — манифест бандла, итоговые таблицы в файле (``tables``) и
  таблицы, которых у автора уже нет (``missing``: удалены ретеншном);
- ``session.json`` — бандл с таблицами результата (``build(results=True)``),
  лента из браузера (``feed``) и активная таблица автора (``table``);
- ``results/<temp_result_vN>.parquet`` — итоговые таблицы ходов и плиток,
  выгруженные ``COPY … (FORMAT parquet)``.

Пароля в файле нет: бандл собирает тот же ``session_bundle.build``.

Импорт (``read``) сначала проверяет файл целиком: размер, сумму распакованного
(и по заголовкам zip, и по факту чтения), только ожидаемые имена внутри,
версию формата и сам бандл. Потом таблицы загружаются в **новые**
``temp_result_vN`` (следующие свободные номера), и ссылки на них в ходах,
плитках и ленте переписываются по словарю «таблица автора -> новая». Ссылка на
таблицу, которой нет в файле, обнуляется: чужие таблицы получателя импорт не
трогает. Упал импорт — загруженные таблицы удаляются, сессия не создаётся.
"""

import json
import re
import tempfile
import zipfile
import zlib
from pathlib import Path
from typing import IO, Any

import duckdb

from app.core.memory import session_bundle

MAX_FILE_BYTES = 100 << 20
MAX_UNPACKED_BYTES = 200 << 20
MAX_JSON_BYTES = 20 << 20
MAX_TABLES = 1000
CHUNK = 1 << 20
MANIFEST = "manifest.json"
SESSION = "session.json"
RESULTS = "results/"
RESULT_TABLE = re.compile(r"temp_result_v\d+")


class SessionFileError(ValueError):
    """Файл не открывается: не файл сессии, повреждён или в нём лишнее."""


class SessionFileTooLarge(SessionFileError):
    """Файл или его распакованное содержимое больше лимита."""


class ImportedFile(session_bundle.Imported):
    # session.json с переписанными таблицами: из него фронт берёт ленту.
    session: dict[str, Any]


def result_tables(bundle: dict) -> list[str]:
    """Итоговые таблицы ходов и плиток бандла, без повторов."""
    names = []
    for turn in bundle["turns"]:
        names += [turn.get("result_table"), (turn.get("chart") or {}).get("result_table")]
    for dashboard in bundle["dashboards"]:
        names += [tile.get("result_table") for tile in dashboard["tiles"]]
    return [name for name in dict.fromkeys(names) if name and RESULT_TABLE.fullmatch(name)]


def quoted(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def write(session_id: str, db, table: str | None, feed: list, target: Path) -> None:
    """Записать файл сессии в ``target``; ``KeyError`` — сессии нет.

    Таблица, которой уже нет в DuckDB, не пишется и попадает в ``missing``.
    Файл больше MAX_FILE_BYTES — ``SessionFileTooLarge``: получатель его не откроет.
    """
    bundle = session_bundle.build(session_id, db, results=True)
    tables, missing = [], []
    with (
        tempfile.TemporaryDirectory() as folder,
        zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive,
    ):
        for name in result_tables(bundle):
            path = Path(folder, f"{name}.parquet")
            try:
                db.execute(f'COPY "{name}" TO {quoted(path)} (FORMAT parquet)')
            except duckdb.Error:
                missing.append(name)
                continue
            archive.write(path, RESULTS + path.name, compress_type=zipfile.ZIP_STORED)
            path.unlink()
            tables.append(name)
            check_size(target)
        manifest = {**bundle["manifest"], "tables": tables, "missing": missing}
        archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False))
        session = {**bundle, "table": table, "feed": feed}
        archive.writestr(SESSION, json.dumps(session, ensure_ascii=False))
    check_size(target)


def check_size(target: Path) -> None:
    if target.stat().st_size > MAX_FILE_BYTES:
        raise SessionFileTooLarge(
            f"Файл сессии больше {MAX_FILE_BYTES >> 20} МБ: в нём слишком большие таблицы."
        )


def receive(source: IO[bytes], target: Path) -> None:
    """Сохранить загруженный файл, не больше MAX_FILE_BYTES."""
    written = 0
    with target.open("wb") as output:
        while chunk := source.read(CHUNK):
            written += len(chunk)
            if written > MAX_FILE_BYTES:
                raise SessionFileTooLarge(f"Файл сессии больше {MAX_FILE_BYTES >> 20} МБ.")
            output.write(chunk)


def read(path: Path, db) -> ImportedFile:
    """Импортировать файл сессии в новую сессию; существующие сессии и таблицы не меняются."""
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise SessionFileError("Это не файл сессии.") from None
    try:
        with archive, tempfile.TemporaryDirectory() as folder:
            manifest = read_json(archive, MANIFEST)
            session_bundle.check_format(manifest)
            names = manifest_tables(manifest)
            check_entries(archive, names)
            session = read_json(archive, SESSION)
            session_bundle.parse(session)
            loaded: dict[str, str] = {}
            try:
                for index, name in enumerate(names):
                    part = Path(folder, f"{index}.parquet")
                    extract(archive, RESULTS + f"{name}.parquet", part)
                    try:
                        loaded[name] = db.save_step_result(
                            name, f"SELECT * FROM read_parquet({quoted(part)})"
                        )
                    except duckdb.Error:
                        raise SessionFileError(f"Таблица «{name}» в файле повреждена.") from None
                    part.unlink()
                imported = session_bundle.load(session, db, loaded)
            except BaseException:
                for table in loaded.values():
                    db.execute(f'DROP TABLE IF EXISTS "{table}"')
                raise
    except (zipfile.BadZipFile, zlib.error, EOFError):
        raise SessionFileError("Файл сессии повреждён.") from None
    return ImportedFile(**imported.model_dump(), session=rename(session, loaded))


def read_json(archive: zipfile.ZipFile, name: str) -> Any:
    try:
        info = archive.getinfo(name)
    except KeyError:
        raise SessionFileError(f"Это не файл сессии: в нём нет {name}.") from None
    if info.file_size > MAX_JSON_BYTES:
        raise SessionFileTooLarge(f"{name} в файле сессии слишком большой.")
    with archive.open(info) as source:
        data = source.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise SessionFileTooLarge(f"{name} в файле сессии слишком большой.")
    try:
        return json.loads(data)
    except ValueError:
        raise SessionFileError(f"Файл сессии повреждён: {name} не читается.") from None


def manifest_tables(manifest: dict) -> list[str]:
    """Таблицы из манифеста: только temp_result_vN, без повторов."""
    names = manifest.get("tables", [])
    if (
        not isinstance(names, list)
        or len(names) > MAX_TABLES
        or len(set(map(str, names))) != len(names)
        or not all(isinstance(name, str) and RESULT_TABLE.fullmatch(name) for name in names)
    ):
        raise SessionFileError("Файл сессии повреждён: список таблиц в манифесте неверный.")
    return names


def check_entries(archive: zipfile.ZipFile, tables: list[str]) -> None:
    """Внутри только манифест, сессия и таблицы из манифеста; распакованное — не больше лимита."""
    expected = {MANIFEST, SESSION, *(RESULTS + f"{name}.parquet" for name in tables)}
    entries = [info.filename for info in archive.infolist()]
    extra = [name for name in entries if name not in expected]
    if extra:
        raise SessionFileError(f"В файле сессии лишнее: {extra[0]}.")
    if len(entries) != len(set(entries)) or set(entries) != expected:
        raise SessionFileError("Файл сессии повреждён: части повторяются или их не хватает.")
    if sum(info.file_size for info in archive.infolist()) > MAX_UNPACKED_BYTES:
        raise SessionFileTooLarge(
            f"Распакованный файл сессии больше {MAX_UNPACKED_BYTES >> 20} МБ."
        )


def extract(archive: zipfile.ZipFile, name: str, target: Path) -> None:
    """Распаковать часть zip, считая байты по факту: заголовок zip может врать."""
    written = 0
    with archive.open(name) as source, target.open("wb") as output:
        while chunk := source.read(CHUNK):
            written += len(chunk)
            if written > MAX_UNPACKED_BYTES:
                raise SessionFileTooLarge(
                    f"Распакованный файл сессии больше {MAX_UNPACKED_BYTES >> 20} МБ."
                )
            output.write(chunk)


def rename(value: Any, tables: dict[str, str]) -> Any:
    """Копия JSON, где каждое ``result_table`` — новое имя таблицы или None."""
    if isinstance(value, list):
        return [rename(item, tables) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: session_bundle.renamed(item, tables) if key == "result_table" else rename(item, tables)
        for key, item in value.items()
    }
