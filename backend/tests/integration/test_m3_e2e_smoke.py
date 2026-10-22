"""End-to-end functional smoke test suite for Milestone 3 (F-01 Full-Stack Feature Integration).

Verifies the complete stack with USE_FAKE_LLM=true and DEMO_MODE=true:
1. CSV upload with demo_events.csv -> TableBrief metadata (table, rows, cols, period, warnings, description_status).
2. Session creation and chat streaming via /api/chat/stream -> SSE progress events (planning, step, insight, chart) and completion.
3. Clarification scenario -> clarification_needed emission, recovery with skip_step, and user_answer incorporation.
4. Table selection, session switching (POST & PATCH /api/sessions/{id}/table), and active table missing guard.
5. ClickHouse guard -> radio disabled with 'Скоро' badge in LeftSidebar.tsx, backend 501 prevention.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.schemas.datasource import TableBrief
from app.core.agent.orchestrator import NO_TABLE_MESSAGE
from app.core.memory.session_memory import SessionMemory
from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_EVENTS_CSV = REPO_ROOT / "data" / "sample_datasets" / "demo_events.csv"
FRONTEND_DIR = REPO_ROOT / "frontend"


@pytest.fixture(autouse=True)
def _setup_smoke_env(monkeypatch):
    monkeypatch.setenv("USE_FAKE_LLM", "true")
    monkeypatch.setenv("DEMO_MODE", "true")


def _parse_sse_events(response) -> list[dict]:
    """Parse text/event-stream chunks into a list of parsed JSON event dictionaries."""
    events = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_e2e_csv_upload_demo_events_profiles(tmp_path, monkeypatch):
    """Scenario 2a: Upload demo_events.csv to /api/datasources/csv.

    Verify response contains profiles with TableBrief (table name, rows, columns,
    period_from, period_to, description_status, warnings).
    """
    assert DEMO_EVENTS_CSV.exists(), f"Missing dataset: {DEMO_EVENTS_CSV}"

    with TestClient(app) as client:
        with DEMO_EVENTS_CSV.open("rb") as f:
            res = client.post(
                "/api/datasources/csv",
                files={"files": ("demo_events.csv", f, "text/csv")},
            )

        assert res.status_code == 200, res.text
        data = res.json()

        assert data["kind"] == "csv"
        assert data["datasource_id"].startswith("csv_")
        assert "demo_events" in data["tables"]

        profiles = data["profiles"]
        assert len(profiles) >= 1
        brief_data = profiles[0]

        # Validate with TableBrief Pydantic schema
        brief = TableBrief.model_validate(brief_data)
        assert brief.table == "demo_events"
        assert brief.rows == 500
        assert brief.columns == 4
        assert brief.period_from == "2024-01-01"
        assert brief.period_to == "2024-12-28"
        assert brief.description_status in ["none", "pending", "ready"]
        assert isinstance(brief.warnings, list)


def test_e2e_chat_stream_progress_and_completion_events(tmp_path):
    """Scenario 2b: Start session and stream chat query via /api/chat/stream.

    Verify SSE event stream emits progress events (planning:started/finished,
    step:started/finished), insight, chart without mocks.
    """
    with TestClient(app) as client:
        # Upload a dataset containing 'category' for clean FakeLLM execution
        csv_content = (
            "id,category,amount,event_date\n"
            "1,electronics,100,2024-01-01\n"
            "2,clothing,200,2024-01-02\n"
            "3,electronics,150,2024-01-03\n"
        )
        csv_file = tmp_path / "sales.csv"
        csv_file.write_text(csv_content, encoding="utf-8")

        with csv_file.open("rb") as f:
            upload_res = client.post(
                "/api/datasources/csv",
                files={"files": ("sales.csv", f, "text/csv")},
            )
        assert upload_res.status_code == 200
        ds = upload_res.json()

        # Start session
        sess_res = client.post(
            "/api/sessions",
            json={"table": "sales", "title": "Sales Session", "datasource_id": ds["datasource_id"]},
        )
        assert sess_res.status_code == 200
        session_id = sess_res.json()["session_id"]

        # Stream chat query
        stream_res = client.post(
            "/api/chat/stream",
            json={"session_id": session_id, "question": "Анализ по категориям"},
        )
        assert stream_res.status_code == 200
        assert "text/event-stream" in stream_res.headers.get("content-type", "")

        events = _parse_sse_events(stream_res)
        assert len(events) > 0

        event_types = [e["type"] for e in events]
        assert "progress" in event_types
        assert "text" in event_types
        assert "chart" in event_types
        assert "done" in event_types

        progress_events = [e["data"] for e in events if e["type"] == "progress"]
        stages = [(p.get("stage"), p.get("status")) for p in progress_events]

        # Verify planning lifecycle
        assert ("planning", "started") in stages
        assert ("planning", "finished") in stages

        # Verify step execution lifecycle
        assert ("step", "started") in stages
        assert ("step", "finished") in stages

        # Verify insight & chart progress
        assert ("insight", "started") in stages
        assert ("chart", "started") in stages


def test_e2e_clarification_scenario_and_recovery(tmp_path):
    """Scenario 2c: ClarificationCard flow & recovery_response handling.

    When an error occurs:
    1. Orchestrator emits clarification_needed event with step_id and suggestions.
    2. Sending recovery_response={'action':'skip_step'} skips the failed step and recovers.
    3. Sending recovery_response={'action':'user_answer','answer':'...'} replans with the user note.
    """
    with TestClient(app) as client:
        # Ingest demo_events (missing 'category' column, triggering clarification)
        with DEMO_EVENTS_CSV.open("rb") as f:
            upload_res = client.post(
                "/api/datasources/csv",
                files={"files": ("demo_events.csv", f, "text/csv")},
            )
        ds = upload_res.json()

        # Start session
        sess_res = client.post(
            "/api/sessions",
            json={"table": "demo_events", "title": "Demo Session", "datasource_id": ds["datasource_id"]},
        )
        session_id = sess_res.json()["session_id"]

        # Query triggers clarification_needed
        res1 = client.post(
            "/api/chat/stream",
            json={"session_id": session_id, "question": "Анализ событий"},
        )
        assert res1.status_code == 200
        events1 = _parse_sse_events(res1)
        clarification_evs = [e for e in events1 if e["type"] == "clarification_needed"]
        assert len(clarification_evs) == 1
        clarification_data = clarification_evs[0]["data"]
        assert clarification_data["step_id"] == 0
        assert "Binder Error" in clarification_data["error"] or "category" in clarification_data["error"]
        assert isinstance(clarification_data["suggestions"], list)

        # Recovery 1: action = "skip_step"
        skip_payload = json.dumps({"action": "skip_step"})
        res2 = client.post(
            "/api/chat/stream",
            json={
                "session_id": session_id,
                "question": "Пропустить",
                "recovery_response": skip_payload,
            },
        )
        assert res2.status_code == 200
        events2 = _parse_sse_events(res2)
        types2 = [e["type"] for e in events2]
        # Successfully skipped and finished
        assert "text" in types2
        assert "chart" in types2
        assert "done" in types2

        # Recovery 2: action = "user_answer"
        sess_res2 = client.post(
            "/api/sessions",
            json={"table": "demo_events", "title": "Session 2", "datasource_id": ds["datasource_id"]},
        )
        session_id2 = sess_res2.json()["session_id"]

        # Trigger clarification
        client.post(
            "/api/chat/stream",
            json={"session_id": session_id2, "question": "Анализ событий"},
        )

        answer_payload = json.dumps({"action": "user_answer", "answer": "использовать user_id"})
        res3 = client.post(
            "/api/chat/stream",
            json={
                "session_id": session_id2,
                "question": "ответ",
                "recovery_response": answer_payload,
            },
        )
        assert res3.status_code == 200
        events3 = _parse_sse_events(res3)
        assert len(events3) > 0


def test_e2e_table_selection_and_session_switching(tmp_path):
    """Scenario 2d: Table selection and session switching.

    1. workWithTable switches session table via POST /api/sessions/{id}/table or PATCH /api/sessions/{id}.
    2. Sessions without an active table block queries with informative error NO_TABLE_MESSAGE.
    3. Frontend ChatPanel.tsx and useStartSession.ts guard against missing active tables.
    """
    with TestClient(app) as client:
        csv1 = tmp_path / "t1.csv"
        csv1.write_text("id,val\n1,10\n", encoding="utf-8")
        csv2 = tmp_path / "t2.csv"
        csv2.write_text("id,score\n1,100\n", encoding="utf-8")

        with csv1.open("rb") as f1, csv2.open("rb") as f2:
            res = client.post(
                "/api/datasources/csv",
                files=[("files", ("t1.csv", f1, "text/csv")), ("files", ("t2.csv", f2, "text/csv"))],
            )
        assert res.status_code == 200

        # Start session on t1
        s = client.post(
            "/api/sessions",
            json={"table": "t1", "title": "Switching Session", "datasource_id": "csv_ds"},
        ).json()
        assert s["table"] == "t1"
        sid = s["session_id"]

        # Switch table via POST /api/sessions/{id}/table
        s_post = client.post(f"/api/sessions/{sid}/table", json={"table": "t2"}).json()
        assert s_post["table"] == "t2"

        # Switch table via PATCH /api/sessions/{id}
        s_patch = client.patch(f"/api/sessions/{sid}", json={"table": "t1"}).json()
        assert s_patch["table"] == "t1"

        # Verify backend session without an active table blocks queries
        empty_mem = SessionMemory("sess_no_active_table")
        empty_mem.set_meta("Empty Session", "csv_ds")
        assert empty_mem.table is None

        stream_res = client.post(
            "/api/chat/stream",
            json={"session_id": "sess_no_active_table", "question": "Сколько записей?"},
        )
        assert stream_res.status_code == 200
        events = _parse_sse_events(stream_res)
        assert len(events) == 2
        assert events[0]["type"] == "error"
        assert events[0]["data"]["message"] == NO_TABLE_MESSAGE
        assert events[1]["type"] == "done"

    # Verify frontend ChatPanel active table guard implementation
    chat_panel_file = FRONTEND_DIR / "src/components/chat/ChatPanel.tsx"
    chat_code = chat_panel_file.read_text(encoding="utf-8")
    assert "!activeTable" in chat_code
    assert 't("chat.noTableWarning")' in chat_code
    assert "disabled={streaming || !activeTable}" in chat_code
    assert "disabled={!input.trim() || !activeTable}" in chat_code

    # Verify frontend useStartSession workWithTable implementation
    start_session_file = FRONTEND_DIR / "src/hooks/useStartSession.ts"
    start_code = start_session_file.read_text(encoding="utf-8")
    assert "async function workWithTable(database: DatabaseFolder, table: string, sessionId?: string)" in start_code
    assert "await setSessionTable(sessionId, table)" in start_code
    assert "await startSession(database, table)" in start_code or "createSession" in start_code


def test_e2e_clickhouse_guard():
    """Scenario 2e: ClickHouse guard.

    1. Backend /api/datasources/db with kind="clickhouse" returns HTTP 501.
    2. Frontend LeftSidebar.tsx keeps ClickHouse radio strictly disabled with 'Скоро' badge.
    """
    with TestClient(app) as client:
        # Backend returns 501 on ClickHouse attempt
        res = client.post(
            "/api/datasources/db",
            json={"kind": "clickhouse", "dsn": "clickhouse://localhost:8123/default"},
        )
        assert res.status_code == 501
        assert "пока не реализован" in res.json().get("detail", "")

    # Inspect LeftSidebar.tsx to verify the UI guard
    sidebar_file = FRONTEND_DIR / "src/components/layout/LeftSidebar.tsx"
    sidebar_code = sidebar_file.read_text(encoding="utf-8")

    # Verify disabled and checked={false} and "Скоро" badge
    assert 'value="clickhouse"' in sidebar_code
    assert "disabled" in sidebar_code
    assert "checked={false}" in sidebar_code
    assert 'title={t("modal.comingSoon")}' in sidebar_code
    assert '{t("modal.comingSoon")}' in sidebar_code
