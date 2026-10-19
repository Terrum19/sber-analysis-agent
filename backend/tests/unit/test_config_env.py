"""Тесты конфигурации и env-переменных."""

from app.config import Settings, settings


def test_wasm_settings_defaults():
    assert settings.wasm_timeout_s == 30
    assert settings.wasm_mem_mb == 512
    assert settings.agent_max_steps == 12


def test_llm_settings_defaults():
    # Дефолты кода, а не локального .env разработчика.
    defaults = Settings(_env_file=None)
    assert defaults.llm_base_url == "http://localhost:11434/v1"
    assert defaults.llm_model == "qwen2.5-coder:14b"


def test_storage_dir_defaults():
    assert settings.storage_dir == "backend/storage"
