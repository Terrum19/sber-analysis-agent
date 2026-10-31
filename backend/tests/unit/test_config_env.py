"""Тесты конфигурации и env-переменных."""

from pathlib import Path

from app.config import Settings, settings

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_wasm_settings_defaults():
    # Дефолты кода, а не локального .env разработчика.
    defaults = Settings(_env_file=None)
    assert defaults.wasm_timeout_s == 60
    assert defaults.wasm_mem_mb == 2048
    assert defaults.agent_max_steps == 8
    assert defaults.toolgen_enabled is True
    assert defaults.toolgen_attempts == 2
    assert defaults.toolgen_sample_rows == 500
    assert defaults.toolgen_max_tools == 25
    assert defaults.toolgen_max_per_turn == 2
    assert defaults.toolgen_store is True
    assert defaults.toolgen_max_sql_chars == 4000
    assert defaults.toolgen_max_code_chars == 6000


def test_llm_settings_defaults():
    # Дефолты кода, а не локального .env разработчика.
    defaults = Settings(_env_file=None)
    assert defaults.llm_base_url == "http://localhost:11434/v1"
    assert defaults.llm_model == "qwen2.5-coder:14b"


def test_insight_stream_is_on_by_default(monkeypatch):
    monkeypatch.delenv("INSIGHT_STREAM", raising=False)
    assert Settings(_env_file=None).insight_stream is True


def test_insight_stream_can_be_disabled_from_env(monkeypatch):
    monkeypatch.setenv("INSIGHT_STREAM", "false")
    assert Settings(_env_file=None).insight_stream is False


def test_legacy_reasoning_env_name_is_still_read(monkeypatch):
    # Старые .env задавали LLM_REASONING: поле молча оставалось пустым,
    # и рассуждения съедали весь max_tokens (пустой structured-ответ).
    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    monkeypatch.setenv("LLM_REASONING", "low")
    assert Settings(_env_file=None).llm_reasoning_effort == "low"


def test_reasoning_effort_env_name_has_priority(monkeypatch):
    monkeypatch.setenv("LLM_REASONING", "high")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "minimal")
    assert Settings(_env_file=None).llm_reasoning_effort == "minimal"


def test_reasoning_effort_off_means_disabled():
    assert Settings(_env_file=None, llm_reasoning_effort="off").llm_reasoning_effort == "none"


def test_empty_reasoning_effort_is_not_passed():
    assert Settings(_env_file=None, llm_reasoning_effort="").llm_reasoning_effort is None


def test_storage_dir_defaults():
    assert settings.storage_dir == str(REPO_ROOT / "backend" / "storage")


def test_relative_paths_resolve_from_repo_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    defaults = Settings(_env_file=None, wasm_runtime_dir="backend/sandbox")
    assert defaults.duckdb_path == str(REPO_ROOT / "backend/storage/duckdb/main.duckdb")
    assert defaults.sqlite_path == str(REPO_ROOT / "backend/storage/sessions/sessions.sqlite")
    assert defaults.wasm_runtime_dir == str(REPO_ROOT / "backend" / "sandbox")


def test_absolute_and_special_paths_kept(tmp_path):
    custom = Settings(
        _env_file=None, duckdb_path=":memory:", sqlite_path=str(tmp_path / "s.sqlite")
    )
    assert custom.duckdb_path == ":memory:"
    assert custom.sqlite_path == str(tmp_path / "s.sqlite")
    assert Settings(_env_file=None).wasm_runtime_dir == ""
