"""Empirical challenger 2 test suite for Milestone 2.

Empirically verifies:
1. TablePassport rendering of TableBrief metadata (rows, columns, period_from, period_to,
   description_status, warnings) and workWithTable behavior (with/without sessionId).
2. ChatPanel active table guard: warning banner rendered, input disabled, button disabled,
   suggestions disabled, retry disabled, sendQuestion blocked when !activeTable.
3. ClarificationCard payload formatting and backend recovery expectations:
   - skip_step ({"action":"skip_step"})
   - raw text user answer (as sent by ChatPanel)
   - {"action":"user_answer","text":"..."} vs {"action":"user_answer","answer":"..."} contract analysis.
"""

import asyncio
import contextlib
import json
import re
import subprocess
from pathlib import Path

from app.api.schemas.chat import ChatRequest
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.data.duckdb_manager import DuckDBManager
from app.models.research_step import ResearchPlan, ResearchStep

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIR = REPO_ROOT / "frontend"


def test_empirical_table_passport_metadata_rendering_via_tsx() -> None:
    """Renders TablePassport using ReactDOMServer in Node.js/tsx across multiple TableBrief variations."""
    node_script = """
import React from 'react';
(global as any).React = React;
import ReactDOMServer from 'react-dom/server';
import { AppRouterContext } from 'next/dist/shared/lib/app-router-context.shared-runtime';
import TablePassport from './src/components/chat/TablePassport';

const mockRouter = {
  back: () => {},
  forward: () => {},
  refresh: () => {},
  push: () => {},
  replace: () => {},
  prefetch: () => {}
};

function renderPassport(brief, props = {}) {
  return ReactDOMServer.renderToStaticMarkup(
    React.createElement(AppRouterContext.Provider, { value: mockRouter },
      React.createElement(TablePassport, { brief, ...props })
    )
  );
}

// 1. Zero values for rows & columns
const htmlZeros = renderPassport({
  table: 'zero_table',
  rows: 0,
  columns: 0,
  description_status: 'none'
});
if (!htmlZeros.includes('zero_table')) throw new Error('Missing table name');
if (!htmlZeros.includes('строк: 0')) throw new Error('rows: 0 must be rendered');
if (!htmlZeros.includes('колонок: 0')) throw new Error('columns: 0 must be rendered');
if (htmlZeros.includes('Работать с этой таблицей')) throw new Error('Action button rendered when showAction is false');

// 2. Large numbers and period
const htmlRich = renderPassport({
  table: 'rich_table',
  rows: 2500000,
  columns: 35,
  period_from: '2023-01-01',
  period_to: '2023-12-31',
  description_status: 'pending',
  summary: 'Financial transactions dataset',
  warnings: ['Warning: Missing indexes', 'Warning: High NULL ratio']
}, { showAction: true });

if (!htmlRich.includes('rich_table')) throw new Error('Missing table name');
if (!htmlRich.includes('строк: 2\\u00a0500\\u00a0000') && !htmlRich.includes('2 500 000') && !htmlRich.includes('2,500,000')) {
  throw new Error('rows formatting failed: ' + htmlRich);
}
if (!htmlRich.includes('колонок: 35')) throw new Error('Missing columns: 35');
if (!htmlRich.includes('01.01.2023 — 31.12.2023')) throw new Error('Missing period formatting');
if (!htmlRich.includes('описание колонок готовится')) throw new Error('Missing pending status badge');
if (!htmlRich.includes('Financial transactions dataset')) throw new Error('Missing summary');
if (!htmlRich.includes('<li>Warning: Missing indexes</li>')) throw new Error('Missing warning 1');
if (!htmlRich.includes('<li>Warning: High NULL ratio</li>')) throw new Error('Missing warning 2');
if (!htmlRich.includes('Работать с этой таблицей')) throw new Error('Missing action button');

// 3. Failed description status
const htmlFailed = renderPassport({
  table: 'failed_table',
  description_status: 'failed'
});
if (!htmlFailed.includes('описание колонок разобрано не полностью')) {
  throw new Error('Missing failed status badge');
}

// 4. Ready / None description status (no badges)
const htmlReady = renderPassport({
  table: 'ready_table',
  description_status: 'ready'
});
if (htmlReady.includes('описание колонок')) throw new Error('Ready table should not have status badge');

// 5. Partial period (only from or only to)
const htmlPartial = renderPassport({
  table: 'partial_table',
  period_from: '2024-05-01'
});
if (!htmlPartial.includes('01.05.2024 — …')) throw new Error('Partial period format error: ' + htmlPartial);

// 6. Non-date string fallback
const htmlInvalidDate = renderPassport({
  table: 'raw_date_table',
  period_from: 'invalid-date-string'
});
if (!htmlInvalidDate.includes('invalid-date-string — …')) throw new Error('Invalid date fallback failed: ' + htmlInvalidDate);

console.log('TABLE_PASSPORT_METADATA_SUCCESS');
"""
    res = subprocess.run(
        ["npx", "tsx", "-e", node_script],
        cwd=FRONTEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"TablePassport rendering test failed:\n{res.stderr}\n{res.stdout}"
    assert "TABLE_PASSPORT_METADATA_SUCCESS" in res.stdout


def test_empirical_work_with_table_behavior_via_tsx() -> None:
    """Verifies workWithTable behavior: switches table in existing session vs creates new session."""
    node_script = """
const storageMap = new Map();
const mockStorage = {
  getItem: (k) => storageMap.get(k) ?? null,
  setItem: (k, v) => storageMap.set(k, String(v)),
  removeItem: (k) => storageMap.delete(k),
  clear: () => storageMap.clear(),
};
(globalThis as any).window = { localStorage: mockStorage, location: { href: '' } };
(globalThis as any).localStorage = mockStorage;

async function run() {
  const sessionStoreMod = await import('./src/store/sessionStore');
  const useSessionStore = sessionStoreMod.default.useSessionStore || sessionStoreMod.useSessionStore;
  const startSessionMod = await import('./src/hooks/useStartSession');
  const workWithTable = startSessionMod.default?.workWithTable || startSessionMod.workWithTable;

  const store = useSessionStore.getState();
  store.databases = [{ id: 'db1', title: 'Database 1', tables: ['tbl_1', 'tbl_2'], sessions: [] }];

  let fetchCalls = [];
  (globalThis as any).fetch = async (url, options) => {
    const body = options?.body ? JSON.parse(options.body) : null;
    fetchCalls.push({ url, body, method: options?.method });
    if (url.includes('/api/sessions/existing_s1')) {
      return { ok: true, json: async () => ({ session_id: 'existing_s1', table: 'tbl_2' }) };
    }
    if (url.endsWith('/api/sessions')) {
      return { ok: true, json: async () => ({ session_id: 'new_s99', table: 'tbl_1', datasource_id: 'db1' }) };
    }
    return { ok: false, status: 404 };
  };

  // Case 1: with existing sessionId -> calls PATCH /api/sessions/{id}
  await workWithTable({ id: 'db1', title: 'Database 1', tables: ['tbl_1', 'tbl_2'], sessions: [] }, 'tbl_2', 'existing_s1');
  if (fetchCalls.length !== 1) throw new Error('Expected 1 fetch call, got ' + fetchCalls.length);
  if (fetchCalls[0].method !== 'PATCH' || !fetchCalls[0].url.includes('existing_s1')) {
    throw new Error('Expected PATCH call for existing_s1, got ' + JSON.stringify(fetchCalls[0]));
  }
  if (fetchCalls[0].body.table !== 'tbl_2') throw new Error('Expected table tbl_2 in payload');
  if (useSessionStore.getState().sessionActiveTable['existing_s1'] !== 'tbl_2') {
    throw new Error('Store sessionActiveTable not updated');
  }

  // Case 2: without sessionId -> calls POST /api/sessions and navigates
  fetchCalls = [];
  await workWithTable({ id: 'db1', title: 'Database 1', tables: ['tbl_1', 'tbl_2'], sessions: [] }, 'tbl_1');
  if (fetchCalls.length !== 1) throw new Error('Expected 1 fetch call for new session');
  if (fetchCalls[0].method !== 'POST' || !fetchCalls[0].url.endsWith('/api/sessions')) {
    throw new Error('Expected POST call for /api/sessions, got ' + JSON.stringify(fetchCalls[0]));
  }
  if (fetchCalls[0].body.table !== 'tbl_1') throw new Error('Expected table tbl_1 in create session payload');
  if (useSessionStore.getState().sessionActiveTable['new_s99'] !== 'tbl_1') {
    throw new Error('New session table not set in store');
  }
  if ((globalThis as any).window.location.href !== '/session/new_s99') {
    throw new Error('Expected navigation to /session/new_s99, got ' + (globalThis as any).window.location.href);
  }

  console.log('WORK_WITH_TABLE_SUCCESS');
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""
    res = subprocess.run(
        ["npx", "tsx", "-e", node_script],
        cwd=FRONTEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"workWithTable test failed:\n{res.stderr}\n{res.stdout}"
    assert "WORK_WITH_TABLE_SUCCESS" in res.stdout


def test_empirical_chat_panel_active_table_guard() -> None:
    """Verifies that ChatPanel completely blocks query submission and displays warning when !activeTable."""
    chat_file = FRONTEND_DIR / "src/components/chat/ChatPanel.tsx"
    assert chat_file.exists()
    content = chat_file.read_text(encoding="utf-8")

    # 1. Warning banner rendered when !activeTable
    banner_match = re.search(
        r"\{!activeTable\s*&&\s*\(\s*<div[^>]*border-amber-500/30[^>]*>.*?<span>\{t\(\"chat\.noTableWarning\"\)\}</span>\s*</div>\s*\)\}",
        content,
        re.DOTALL,
    )
    assert banner_match is not None, "Warning banner for !activeTable not found in ChatPanel"

    # 2. Textarea disabled when !activeTable
    assert "disabled={streaming || !activeTable}" in content, (
        "Textarea must be disabled when !activeTable"
    )

    # 3. Textarea placeholder indicates missing table
    assert '!activeTable\n                ? t("chat.noTableWarning")' in content or (
        '!activeTable ? t("chat.noTableWarning")' in content
    ), "Textarea placeholder must show noTableWarning when !activeTable"

    # 4. Enter key handler guarded
    assert "if (activeTable) sendQuestion();" in content, (
        "Enter key must not submit question if !activeTable"
    )

    # 5. Submit button disabled when !activeTable
    assert "disabled={!input.trim() || !activeTable}" in content, (
        "Submit button must be disabled when !activeTable"
    )

    # 6. Suggestion buttons disabled when !activeTable
    assert "disabled={!activeTable}" in content, (
        "Suggestion buttons must be disabled when !activeTable"
    )

    # 7. Retry button disabled when !activeTable
    retry_pattern = r"<button[^>]*onClick=\{\(\)\s*=>\s*sendQuestion\(lastFailedQuestion\)\}[^>]*disabled=\{!activeTable\}"
    assert re.search(retry_pattern, content, re.DOTALL), (
        "Retry button must be disabled when !activeTable"
    )

    # 8. sendQuestion early-exit guard
    send_guard = re.search(
        r"function\s+sendQuestion[^{]*\{[^}]*if\s*\(!activeTable\)\s*\{\s*setTableError\(t\(\"chat\.noTableWarning\"\)\);\s*return;\s*\}",
        content,
        re.DOTALL,
    )
    assert send_guard is not None, "sendQuestion must abort and set tableError if !activeTable"


def test_empirical_clarification_card_rendering_and_skip_step_via_tsx() -> None:
    """Verifies ClarificationCard UI rendering for suggestions, input mode, and onSkip vs onAnswer."""
    node_script = """
import React from 'react';
(global as any).React = React;
import ReactDOMServer from 'react-dom/server';
import ClarificationCard from './src/components/chat/ClarificationCard';

const data = {
  step_id: 1,
  error: 'Column not found: total_cost',
  suggestions: ['Пропустить этот шаг', 'Использовать колонку amount']
};

// 1. Active ClarificationCard renders suggestions
const htmlActive = ReactDOMServer.renderToStaticMarkup(
  React.createElement(ClarificationCard, {
    data,
    active: true,
    onSkip: () => {},
    onAnswer: () => {}
  })
);

if (!htmlActive.includes('Не получилось выполнить шаг')) throw new Error('Missing title');
if (!htmlActive.includes('Column not found: total_cost')) throw new Error('Missing error message');
if (!htmlActive.includes('Пропустить этот шаг')) throw new Error('Missing skip suggestion');
if (!htmlActive.includes('Использовать колонку amount')) throw new Error('Missing answer suggestion');

// 2. Inactive ClarificationCard does NOT render suggestion buttons
const htmlInactive = ReactDOMServer.renderToStaticMarkup(
  React.createElement(ClarificationCard, {
    data,
    active: false,
    onSkip: () => {},
    onAnswer: () => {}
  })
);
if (htmlInactive.includes('Пропустить этот шаг')) throw new Error('Inactive card should not show buttons');
if (!htmlInactive.includes('Column not found: total_cost')) throw new Error('Inactive card must still show error text');

console.log('CLARIFICATION_CARD_SUCCESS');
"""
    res = subprocess.run(
        ["npx", "tsx", "-e", node_script],
        cwd=FRONTEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"ClarificationCard render test failed:\n{res.stderr}\n{res.stdout}"
    assert "CLARIFICATION_CARD_SUCCESS" in res.stdout


def test_empirical_backend_skip_step_recovery() -> None:
    """Verifies that backend AgentOrchestrator successfully processes recovery_response={'action':'skip_step'}."""
    db = DuckDBManager(":memory:")
    db.execute(
        """
        CREATE TABLE raw AS SELECT * FROM (
            VALUES (1, 10.0), (2, 20.0), (3, 30.0)
        ) AS t(id, amount)
        """
    )

    class FakeMemory:
        def __init__(self):
            self.steps = []
            self.pending = None

        def record_step(self, step, result_table: str):
            self.steps.append((step.step_id, result_table))

        def set_pending_recovery(self, data: dict):
            self.pending = data

        def build_context(self, question: str):
            return {"columns": ["id", "amount"], "summary": ""}

    memory = FakeMemory()
    orch = AgentOrchestrator(db=db)

    pending = {
        "question": "Посчитай средний amount",
        "failed_step_id": 0,
        "plan_steps": [
            ResearchStep(
                step_id=0,
                tool="filter_rows",
                arguments={"table": "raw", "column": "non_existent", "operator": ">", "value": 0},
            ).model_dump(),
            ResearchStep(
                step_id=1, tool="describe_stats", arguments={"column": "amount"}
            ).model_dump(),
        ],
        "input_table": "raw",
        "source_table": "raw",
        "step_tables": {},
        "error": "Column non_existent does not exist",
    }

    req = ChatRequest(
        session_id="test_sess",
        question="Пропустить этот шаг",
        recovery_response=json.dumps({"action": "skip_step"}),
    )

    execution = asyncio.run(orch._apply_recovery(memory, pending, req))
    assert execution.result_table is not None
    rows = db.to_records(execution.result_table)
    assert len(rows) == 1
    assert rows[0]["mean"] == 20.0
    db.close()


def test_empirical_recovery_payload_formatting_contract_verification() -> None:
    """Adversarial challenge test for recovery_response payload formats:

    1. ChatPanel sends free text (e.g. 'amount') for user answers, and RECOVERY_SKIP_STEP for skip.
    2. Backend orchestrator._apply_recovery parses:
       - '{"action":"skip_step"}' -> action = 'skip_step' (HANDLED)
       - raw string 'my answer' -> action = None, free_text = 'my answer' (HANDLED)
       - '{"action":"user_answer","text":"my answer"}' -> action = 'user_answer', free_text = 'my answer' (HANDLED)
       - '{"action":"user_answer","answer":"my answer"}' (PROJECT.md contract) -> free_text = '' (SILENTLY DROPPED!)
    """
    # Verify ChatPanel wiring
    chat_file = FRONTEND_DIR / "src/components/chat/ChatPanel.tsx"
    chat_content = chat_file.read_text(encoding="utf-8")
    assert 'onSkip={() => sendQuestion(t("chat.skipStep"), RECOVERY_SKIP_STEP)}' in chat_content
    assert (
        "onAnswer={(text) => sendQuestion(text, m.clarification!.step_id >= 0 ? text : undefined)}"
        in chat_content
    )

    types_file = FRONTEND_DIR / "src/types/chat.ts"
    types_content = types_file.read_text(encoding="utf-8")
    assert (
        'export const RECOVERY_SKIP_STEP = JSON.stringify({ action: "skip_step" });'
        in types_content
    )

    # Backend test for payload parsing
    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE raw (id INT, amount DOUBLE); INSERT INTO raw VALUES (1, 10.0);")

    class RecordingMemory:
        def __init__(self):
            self.last_question = None

        def build_context(self, question: str):
            self.last_question = question
            return {"columns": ["id", "amount"], "summary": ""}

    class SpyPlanner:
        def __init__(self):
            self.planned_question = None

        async def plan(self, question: str, context: dict):
            self.planned_question = question
            return ResearchPlan(steps=[])

        async def fix_step(self, step, error, *args):
            raise ValueError("not fixed")

    pending = {
        "question": "исходный запрос",
        "failed_step_id": 0,
        "plan_steps": [
            ResearchStep(
                step_id=0, tool="inspect_temp_table", arguments={"table": "raw"}
            ).model_dump()
        ],
        "input_table": "raw",
        "source_table": "raw",
        "step_tables": {},
        "error": "ошибка",
    }

    # Case A: Free text answer (what ChatPanel sends)
    mem_a = RecordingMemory()
    planner_a = SpyPlanner()
    orch_a = AgentOrchestrator(db=db, planner=planner_a)
    req_a = ChatRequest(
        session_id="s1", question="исходный запрос", recovery_response="колонка amount"
    )
    with contextlib.suppress(Exception):
        asyncio.run(orch_a._apply_recovery(mem_a, pending, req_a))
    assert mem_a.last_question == "исходный запрос\nУточнение пользователя: колонка amount", (
        "Free text user answer must be properly appended to clarification context"
    )

    # Case B: JSON with 'text'
    mem_b = RecordingMemory()
    planner_b = SpyPlanner()
    orch_b = AgentOrchestrator(db=db, planner=planner_b)
    req_b = ChatRequest(
        session_id="s1",
        question="исходный запрос",
        recovery_response=json.dumps({"action": "user_answer", "text": "колонка amount"}),
    )
    with contextlib.suppress(Exception):
        asyncio.run(orch_b._apply_recovery(mem_b, pending, req_b))
    assert mem_b.last_question == "исходный запрос\nУточнение пользователя: колонка amount", (
        "JSON with 'text' key must be properly appended to clarification context"
    )

    # Case C: JSON with 'answer' (from PROJECT.md line 46 contract)
    # Documents empirical contract finding: backend reads parsed.get("text", "") instead of "answer"
    mem_c = RecordingMemory()
    planner_c = SpyPlanner()
    orch_c = AgentOrchestrator(db=db, planner=planner_c)
    req_c = ChatRequest(
        session_id="s1",
        question="исходный запрос",
        recovery_response=json.dumps({"action": "user_answer", "answer": "колонка amount"}),
    )
    with contextlib.suppress(Exception):
        asyncio.run(orch_c._apply_recovery(mem_c, pending, req_c))

    # Now that orchestrator.py line 560 supports parsed.get("text") or parsed.get("answer"),
    # mem_c.last_question properly receives the user answer from the contract-defined JSON payload!
    assert mem_c.last_question == "исходный запрос\nУточнение пользователя: колонка amount", (
        "Contract payload with 'answer' key is now properly supported in recovery"
    )

    db.close()
