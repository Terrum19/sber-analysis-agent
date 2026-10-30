"""Файл .session (#251): таблицы в parquet, новые имена у получателя, защита импорта."""

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.dashboards.models import Dashboard, DashboardTile
from app.core.dashboards.store import DashboardStore
from app.core.data.duckdb_manager import DuckDBManager
from app.core.memory import session_bundle, session_file
from app.core.memory.datasource_store import DatasourceRecord, DatasourceStore
from app.core.memory.session_memory import SessionMemory
from app.main import app
from app.models.research_step import ResearchStep

PASSWORD = "s3cret-Pa55"
SALES = "SELECT * FROM (VALUES ('msk', 10.5), ('spb', 7.0)) AS t(region, revenue)"
MONTHS = "SELECT * FROM (VALUES (1, 3), (2, 4)) AS t(month, orders)"


@pytest.fixture
def machines():
    """БД автора и получателя; SQLite сессий у них общий."""
    author, recipient = DuckDBManager(":memory:"), DuckDBManager(":memory:")
    yield author, recipient
    author.close()
    recipient.close()


def author_session(db: DuckDBManager, datasource_id: str | None = None) -> tuple[str, str]:
    """Сессия автора: ход с графиком и плитка дашборда на другой таблице."""
    chart = db.save_step_result("sales", SALES)
    tile = db.save_step_result("sales", MONTHS)
    memory = SessionMemory("author")
    memory.set_meta("Выручка", datasource_id)
    memory.start_turn("Выручка по регионам?")
    memory.record_step(ResearchStep(step_id=0, tool="group_by", arguments={}), chart)
    memory.record_chart("bar", chart, "Выручка")
    memory.finish_turn("ok", "Москва — 10.5", chart, ["region", "revenue"])
    DashboardStore().create(
        Dashboard(
            id="dash1",
            session_id="author",
            title="Продажи",
            tiles=[
                DashboardTile(
                    id="tile1",
                    title="По месяцам",
                    spec={"mark": "line", "usermeta": {"result_table": tile}},
                    result_table=tile,
                )
            ],
        )
    )
    return chart, tile


def feed(chart: str) -> list[dict]:
    """Лента браузера: график хода и чужая ссылка, которой в файле нет."""
    return [
        {"role": "user", "content": "Выручка по регионам?"},
        {
            "role": "agent",
            "content": "Москва — 10.5",
            "turn_id": SessionMemory("author").turns[0]["turn_id"],
            "chart": {"caption": "Выручка", "spec": {"usermeta": {"result_table": chart}}},
            "answer": {"result_table": chart},
        },
        {"role": "agent", "content": "чужое", "chart": {"result_table": "temp_result_v77"}},
    ]


def export(db: DuckDBManager, tmp_path, chart: str) -> bytes:
    path = tmp_path / "author.session"
    session_file.write("author", db, "sales", feed(chart), path)
    return path.read_bytes()


def entries(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def packed(parts: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return out.getvalue()


def load(db: DuckDBManager, tmp_path, data: bytes) -> session_file.ImportedFile:
    path = tmp_path / "upload.session"
    path.write_bytes(data)
    return session_file.read(path, db)


def result_tables(db: DuckDBManager) -> list[str]:
    rows = db.query(
        "SELECT table_name AS name FROM information_schema.tables"
        " WHERE table_name LIKE 'temp_result_v%' ORDER BY table_name"
    )
    return [row["name"] for row in rows]


def test_round_trip_loads_tables_under_new_names(machines, tmp_path) -> None:
    author, recipient = machines
    chart, tile = author_session(author)
    own = [recipient.save_step_result("x", "SELECT 42 AS answer") for _ in range(3)]
    data = export(author, tmp_path, chart)

    result = load(recipient, tmp_path, data)

    memory = SessionMemory(result.session_id)
    turn = memory.turns[0]
    new_chart = turn["result_table"]
    assert turn["imported"] is True
    assert new_chart == turn["chart"]["result_table"]
    assert new_chart not in own + [chart, tile]
    assert recipient.query(f"SELECT * FROM {new_chart} ORDER BY region") == author.query(
        f"SELECT * FROM {chart} ORDER BY region"
    )
    assert memory.owns_result(new_chart)
    assert SessionMemory.list_sessions(10, 0)[0]["imported"]
    dashboard = DashboardStore().get(result.dashboards["dash1"])
    new_tile = dashboard.tiles[0].result_table
    assert new_tile not in (None, tile, new_chart)
    assert dashboard.tiles[0].spec["usermeta"]["result_table"] == new_tile
    assert recipient.query(f"SELECT * FROM {new_tile} ORDER BY month") == author.query(
        f"SELECT * FROM {tile} ORDER BY month"
    )
    messages = result.session["feed"]
    assert messages[1]["chart"]["spec"]["usermeta"]["result_table"] == new_chart
    assert messages[1]["answer"]["result_table"] == new_chart
    assert messages[2]["chart"]["result_table"] is None
    assert result.session["table"] == "sales"
    assert [recipient.query(f"SELECT * FROM {name}") for name in own] == [[{"answer": 42}]] * 3
    assert SessionMemory("author").turns[0]["result_table"] == chart


def test_table_dropped_by_retention_is_listed_and_cleared(machines, tmp_path) -> None:
    author, recipient = machines
    chart, tile = author_session(author)
    author.execute(f"DROP TABLE {tile}")

    data = export(author, tmp_path, chart)
    result = load(recipient, tmp_path, data)

    manifest = json.loads(entries(data)["manifest.json"])
    assert (manifest["tables"], manifest["missing"]) == ([chart], [tile])
    dashboard = DashboardStore().get(result.dashboards["dash1"])
    assert dashboard.tiles[0].result_table is None
    assert SessionMemory(result.session_id).turns[0]["result_table"] is not None


def test_file_has_no_password(machines, tmp_path) -> None:
    author, _ = machines
    dsn = f"postgresql://analyst:{PASSWORD}@db.local:5432/sales"
    DatasourceStore().save(DatasourceRecord(identity="pg_1", kind="postgres", dsn=dsn))
    chart, _ = author_session(author, "pg_1")

    parts = entries(export(author, tmp_path, chart))

    assert all(PASSWORD.encode() not in data for data in parts.values())
    assert json.loads(parts["session.json"])["source"]["dsn"].startswith("postgresql://analyst:")


def broken(parts: dict[str, bytes], case: str) -> dict[str, bytes]:
    manifest = json.loads(parts["manifest.json"])
    if case == "extra":
        return {**parts, "notes.txt": b"hi"}
    if case == "traversal":
        return {**parts, "../evil.parquet": b"x"}
    if case == "unlisted":
        return {**parts, "results/temp_result_v99.parquet": b"x"}
    if case == "missing":
        return {name: data for name, data in parts.items() if not name.startswith("results/")}
    if case == "bad_name":
        manifest["tables"] = ["sales; DROP TABLE x"]
    if case == "newer":
        manifest["format"] = 9
    if case == "no_session":
        return {name: data for name, data in parts.items() if name != "session.json"}
    return {**parts, "manifest.json": json.dumps(manifest).encode()}


@pytest.mark.parametrize(
    ("case", "text"),
    [
        ("extra", "лишнее: notes.txt"),
        ("traversal", "лишнее: ../evil.parquet"),
        ("unlisted", "лишнее: results/temp_result_v99.parquet"),
        ("missing", "не хватает"),
        ("bad_name", "список таблиц"),
        ("newer", "обновите приложение"),
        ("no_session", "не хватает"),
    ],
)
def test_unexpected_contents_are_rejected(machines, tmp_path, case, text) -> None:
    author, recipient = machines
    chart, _ = author_session(author)
    data = packed(broken(entries(export(author, tmp_path, chart)), case))

    with pytest.raises((session_file.SessionFileError, session_bundle.BundleFormatError)) as error:
        load(recipient, tmp_path, data)

    assert text in str(error.value)
    assert result_tables(recipient) == []


def test_not_a_zip_is_rejected(machines, tmp_path) -> None:
    _, recipient = machines
    with pytest.raises(session_file.SessionFileError, match="не файл сессии"):
        load(recipient, tmp_path, b"not a zip")


def test_corrupted_zip_entry_is_rejected(machines, tmp_path) -> None:
    author, recipient = machines
    chart, _ = author_session(author)
    data = bytearray(export(author, tmp_path, chart))
    start = data.index(b"session.json") + len("session.json")
    data[start + 10 : start + 40] = bytes(30)

    with pytest.raises(session_file.SessionFileError):
        load(recipient, tmp_path, bytes(data))

    assert result_tables(recipient) == []


def test_unpacked_size_limit_stops_zip_bomb(machines, tmp_path, monkeypatch) -> None:
    author, recipient = machines
    chart, tile = author_session(author)
    parts = entries(export(author, tmp_path, chart))
    parts[f"results/{tile}.parquet"] = bytes(5 << 20)
    monkeypatch.setattr(session_file, "MAX_UNPACKED_BYTES", 1 << 20)

    with pytest.raises(session_file.SessionFileTooLarge):
        load(recipient, tmp_path, packed(parts))

    assert result_tables(recipient) == []


def test_broken_table_removes_tables_already_loaded(machines, tmp_path) -> None:
    author, recipient = machines
    chart, tile = author_session(author)
    parts = entries(export(author, tmp_path, chart))
    parts[f"results/{tile}.parquet"] = b"not parquet"
    sessions = SessionMemory.list_sessions(10, 0)

    with pytest.raises(session_file.SessionFileError, match="повреждена"):
        load(recipient, tmp_path, packed(parts))

    assert result_tables(recipient) == []
    assert SessionMemory.list_sessions(10, 0) == sessions


def test_export_larger_than_import_limit_is_refused(machines, tmp_path, monkeypatch) -> None:
    author, _ = machines
    chart, _ = author_session(author)
    monkeypatch.setattr(session_file, "MAX_FILE_BYTES", 100)

    with pytest.raises(session_file.SessionFileTooLarge):
        export(author, tmp_path, chart)


@pytest.fixture
def client(machines):
    author, _ = machines
    app.dependency_overrides[get_db] = lambda: author
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_sessions_api_export_and_import_file(client, machines, monkeypatch) -> None:
    author, _ = machines
    chart, _ = author_session(author)

    saved = client.post("/api/sessions/author/export-file", json={"table": "sales", "feed": []})
    imported = client.post(
        "/api/sessions/import-file", files={"file": ("a.session", saved.content)}
    )
    garbage = client.post("/api/sessions/import-file", files={"file": ("a.session", b"zip?")})
    monkeypatch.setattr(session_file, "MAX_FILE_BYTES", 10)
    large = client.post("/api/sessions/import-file", files={"file": ("a.session", saved.content)})

    assert saved.status_code == 200
    assert imported.status_code == 200
    body = imported.json()
    assert body["session"]["feed"] == []
    assert SessionMemory(body["session_id"]).turns[0]["result_table"] not in (None, chart)
    assert garbage.status_code == 400
    assert large.status_code == 413
    assert client.post("/api/sessions/missing/export-file", json={}).status_code == 404
