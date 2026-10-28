import logging
from logging.handlers import RotatingFileHandler

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings, settings
from app.core import logging_setup
from app.core.logging_setup import (
    LOG_FILE_BACKUPS,
    LOG_FILE_MAX_BYTES,
    ConsoleHandler,
    TurnContext,
    TurnContextFilter,
    bound_turn,
    configure_logging,
    public_url,
    set_turn_id,
    startup_summary,
)


@pytest.fixture
def clean_root(_restore_root_logging):
    watched = [*logging_setup.NOISY_LOGGERS, *logging_setup.SERVER_LOGGERS]
    saved_loggers = {
        name: (
            logging.getLogger(name).level,
            list(logging.getLogger(name).handlers),
            logging.getLogger(name).propagate,
        )
        for name in watched
    }
    yield _restore_root_logging
    for name, (level, handlers, propagate) in saved_loggers.items():
        named = logging.getLogger(name)
        named.setLevel(level)
        named.handlers[:] = handlers
        named.propagate = propagate


def record(message: str = "сообщение") -> logging.LogRecord:
    return logging.LogRecord("app.test", logging.INFO, __file__, 1, message, None, None)


def test_record_without_turn_gets_placeholders() -> None:
    entry = record()
    assert TurnContextFilter().filter(entry)
    assert (entry.session_id, entry.turn_id) == ("-", "-")


def test_bound_turn_marks_records_and_resets() -> None:
    turn = TurnContext(session_id="s1")
    with bound_turn(turn):
        set_turn_id("t1")
        entry = record()
        TurnContextFilter().filter(entry)
    assert (entry.session_id, entry.turn_id) == ("s1", "t1")
    outside = record()
    TurnContextFilter().filter(outside)
    assert outside.turn_id == "-"


def test_set_turn_id_without_turn_is_ignored() -> None:
    set_turn_id("t1")
    entry = record()
    TurnContextFilter().filter(entry)
    assert entry.turn_id == "-"


def test_set_turn_id_keeps_known_id_when_empty() -> None:
    turn = TurnContext(session_id="s1", turn_id="t1")
    with bound_turn(turn):
        set_turn_id(None)
    assert turn.turn_id == "t1"


def test_configure_logging_writes_formatted_file(clean_root, tmp_path) -> None:
    log_file = configure_logging("INFO", str(tmp_path))
    assert log_file == tmp_path / "logs" / "backend.log"
    file_handlers = [
        handler for handler in clean_root.handlers if isinstance(handler, RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    assert file_handlers[0].maxBytes == LOG_FILE_MAX_BYTES == 5 * 1024 * 1024
    assert file_handlers[0].backupCount == LOG_FILE_BACKUPS == 3
    with bound_turn(TurnContext(session_id="s1", turn_id="t1")):
        logging.getLogger("app.core.agent.orchestrator").info("ход начат")
    logging.getLogger("app.core.agent.orchestrator").debug("не видно на INFO")
    file_handlers[0].flush()
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0].endswith(" INFO [s1/t1] app.core.agent.orchestrator: ход начат")


def test_configure_logging_quiets_noisy_libraries(clean_root, tmp_path) -> None:
    configure_logging("DEBUG", str(tmp_path))
    assert clean_root.level == logging.DEBUG
    for name in ("httpx", "httpcore", "openai", "instructor"):
        assert logging.getLogger(name).level == logging.WARNING
    for name in ("uvicorn", "uvicorn.error"):
        assert logging.getLogger(name).handlers == []
        assert logging.getLogger(name).propagate


def test_configure_logging_twice_keeps_one_set_of_handlers(clean_root, tmp_path) -> None:
    before = len(clean_root.handlers)
    configure_logging("INFO", str(tmp_path))
    configure_logging("WARNING", str(tmp_path))
    assert len(clean_root.handlers) == before + 2
    assert clean_root.level == logging.WARNING


def test_unwritable_log_dir_falls_back_to_console(clean_root, tmp_path, capsys) -> None:
    blocker = tmp_path / "storage"
    blocker.write_text("файл, а не папка")
    assert configure_logging("INFO", str(blocker)) is None
    assert not any(isinstance(handler, RotatingFileHandler) for handler in clean_root.handlers)
    assert "Файл логов" in capsys.readouterr().err


def test_console_handler_follows_current_stderr(capsys) -> None:
    handler = ConsoleHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.emit(record("в консоль"))
    assert capsys.readouterr().err == "в консоль\n"


def test_public_url_drops_credentials_and_query() -> None:
    assert public_url("https://user:secret@llm.example:8443/v1?key=abc") == (
        "https://llm.example:8443/v1"
    )
    assert public_url("http://localhost:11434/v1") == "http://localhost:11434/v1"


def test_startup_summary_has_config_without_secrets(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "llm_api_key", SecretStr("sk-very-secret"))
    monkeypatch.setattr(settings, "llm_base_url", "https://user:pw@llm.example/v1")
    monkeypatch.setenv("USE_FAKE_LLM", "true")
    line = startup_summary(settings, tmp_path / "backend.log")
    for part in (
        f"модель={settings.llm_model}",
        "адрес LLM=https://llm.example/v1",
        f"structured={settings.llm_structured_mode}",
        f"контекст={settings.llm_num_ctx}",
        f"таймаут={settings.llm_timeout_s} с",
        "fake_llm=True",
        "backend.log",
    ):
        assert part in line
    assert "sk-very-secret" not in line
    assert "user:pw" not in line
    assert "файл логов=нет" in startup_summary(settings, None)


def test_log_level_setting_is_case_insensitive() -> None:
    assert Settings(log_level="debug").log_level == "DEBUG"
    with pytest.raises(ValidationError):
        Settings(log_level="verbose")
