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


def test_llm_settings_defaults():
    # Дефолты кода, а не локального .env разработчика.
    defaults = Settings(_env_file=None)
    assert defaults.llm_base_url == "http://localhost:11434/v1"
    assert defaults.llm_model == "qwen2.5-coder:14b"


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
