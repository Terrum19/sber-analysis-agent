"""Тесты конфигурации и env-переменных."""

import os
import pytest

from app.config import settings


def test_wasm_settings_defaults():
    assert settings.wasm_timeout_s == 30
    assert settings.wasm_mem_mb == 512
    assert settings.agent_max_steps == 12


def test_llm_settings_defaults():
    assert settings.llm_base_url == "http://localhost:11434/v1"
    assert settings.llm_model == "qwen2.5-coder:14b"


def test_storage_dir_defaults():
    assert settings.storage_dir == "backend/storage"
