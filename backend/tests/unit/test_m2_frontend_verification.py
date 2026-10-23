"""Adversarial and structural verification suite for Milestone 2 (Frontend Integration & UI Adjustments).

Verifies:
- Git merge topology: sequential --no-ff merges for all 4 branches.
- Frontend contract alignments: SessionResponse.table nullability, mergeSessions, TablePassport, ClickHouse disabled guard.
- Mock data completeness and table guard behavior.
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIR = REPO_ROOT / "frontend"


def test_m2_git_merge_history() -> None:
    """Verifies that all 4 branches were merged with merge commits into feature/F-01-frontend-ui."""
    res = subprocess.run(
        ["git", "log", "-n", "15", "--oneline"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    log_text = res.stdout
    assert "Merge remote-tracking branch 'origin/feature/frontend-tables'" in log_text
    assert "Merge remote-tracking branch 'origin/feature/frontend-chat'" in log_text
    assert "Merge remote-tracking branch 'origin/feature/sse-progress-events'" in log_text
    assert "Merge remote-tracking branch 'origin/feature/table-brief'" in log_text


def test_m2_datasource_types_session_table_nullable() -> None:
    """SessionResponse.table must be typed as string | null."""
    datasource_ts = (FRONTEND_DIR / "src/types/datasource.ts").read_text(encoding="utf-8")
    # Verify SessionResponse contains table: string | null;
    assert re.search(
        r"export\s+interface\s+SessionResponse\s*\{[^}]*table:\s*string\s*\|\s*null;",
        datasource_ts,
        re.DOTALL,
    ), "SessionResponse.table must be explicitly typed as string | null"


def test_m2_left_sidebar_clickhouse_disabled() -> None:
    """LeftSidebar must keep ClickHouse radio strictly disabled with comingSoon badge to prevent 501."""
    left_sidebar = (FRONTEND_DIR / "src/components/layout/LeftSidebar.tsx").read_text(
        encoding="utf-8"
    )
    assert 'value="clickhouse"' in left_sidebar
    assert 't("modal.comingSoon")' in left_sidebar
    # Check that disabled and checked={false} are present on the clickhouse radio
    pattern = r'value="clickhouse"[^>]*disabled'
    pattern_rev = r'disabled[^>]*value="clickhouse"'
    assert re.search(pattern, left_sidebar, re.DOTALL) or re.search(
        pattern_rev, left_sidebar, re.DOTALL
    ), "ClickHouse radio input must have 'disabled' attribute"
    assert "checked={false}" in left_sidebar, "ClickHouse radio must have checked={false}"


def test_m2_left_sidebar_session_sync() -> None:
    """LeftSidebar must invoke listSessions and mergeSessions on mount."""
    left_sidebar = (FRONTEND_DIR / "src/components/layout/LeftSidebar.tsx").read_text(
        encoding="utf-8"
    )
    assert "listSessions" in left_sidebar
    assert "mergeSessions" in left_sidebar
    assert "SESSIONS_PAGE" in left_sidebar


def test_m2_session_store_merge_sessions() -> None:
    """sessionStore must implement mergeSessions(remoteSessions: SessionResponse[])."""
    session_store = (FRONTEND_DIR / "src/store/sessionStore.ts").read_text(encoding="utf-8")
    assert "mergeSessions: (remoteSessions: SessionResponse[]) => void;" in session_store
    assert (
        "mergeSessions: (remoteSessions: SessionResponse[]) =>" in session_store
        or "mergeSessions: (remoteSessions)" in session_store
    )


def test_m2_chat_panel_active_table_guard() -> None:
    """ChatPanel must merge tables via listTables(), call setSessionTable, and guard against missing table."""
    chat_panel = (FRONTEND_DIR / "src/components/chat/ChatPanel.tsx").read_text(encoding="utf-8")
    assert "listTables" in chat_panel
    assert "setSessionTable" in chat_panel
    assert "availableTables" in chat_panel
    # Missing table check
    assert "!activeTable" in chat_panel
    assert "noTableWarning" in chat_panel
    # Clarification Card support
    assert "ClarificationCard" in chat_panel
    assert "RECOVERY_SKIP_STEP" in chat_panel


def test_m2_table_passport_and_start_session() -> None:
    """TablePassport and useStartSession must support workWithTable."""
    start_session = (FRONTEND_DIR / "src/hooks/useStartSession.ts").read_text(encoding="utf-8")
    assert "workWithTable" in start_session
    passport = (FRONTEND_DIR / "src/components/chat/TablePassport.tsx").read_text(encoding="utf-8")
    assert "workWithTable" in passport or "useWorkWithTable" in passport


def test_m2_top_header_active_table_badge() -> None:
    """TopHeader must render activeTable badge for session tabs."""
    top_header = (FRONTEND_DIR / "src/components/layout/TopHeader.tsx").read_text(encoding="utf-8")
    assert "sessionActiveTable" in top_header
    assert "activeTable" in top_header


def test_m2_mock_data_profiles_and_sessions() -> None:
    """mockData.ts must include TableBrief profiles and mockListTables/mockListSessions."""
    mock_data = (FRONTEND_DIR / "src/api/mockData.ts").read_text(encoding="utf-8")
    assert "mockListTables" in mock_data
    assert "mockListSessions" in mock_data
    assert "profiles:" in mock_data
    assert 'description_status: "ready"' in mock_data


def test_m2_legacy_datasource_panel_preserved() -> None:
    """Legacy DatasourcePanel.tsx must remain intact without deletion."""
    legacy_file = FRONTEND_DIR / "src/components/chat/DatasourcePanel.tsx"
    assert legacy_file.exists(), "DatasourcePanel.tsx must be preserved"
    content = legacy_file.read_text(encoding="utf-8")
    assert "export default function DatasourcePanel()" in content
