import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.config import Settings

LOG_FORMAT = "%(asctime)s %(levelname)s [%(session_id)s/%(turn_id)s] %(name)s: %(message)s"
LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "backend.log"
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUPS = 3
NO_CONTEXT = "-"
NOISY_LOGGERS = ("httpx", "httpcore", "openai", "instructor", "multipart", "python_multipart")
SERVER_LOGGERS = ("uvicorn", "uvicorn.error")

logger = logging.getLogger(__name__)


@dataclass
class TurnContext:
    session_id: str = NO_CONTEXT
    turn_id: str = NO_CONTEXT


_current_turn: ContextVar[TurnContext | None] = ContextVar("turn_context", default=None)
_installed_handlers: list[logging.Handler] = []


@contextmanager
def bound_turn(turn: TurnContext) -> Iterator[None]:
    token = _current_turn.set(turn)
    try:
        yield
    finally:
        _current_turn.reset(token)


def set_turn_id(turn_id: str | None) -> None:
    turn = _current_turn.get()
    if turn is not None and turn_id:
        turn.turn_id = turn_id


class TurnContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        turn = _current_turn.get() or TurnContext()
        record.session_id = turn.session_id
        record.turn_id = turn.turn_id
        return True


class ConsoleHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stderr
        super().emit(record)


def configure_logging(level: str, storage_dir: str) -> Path | None:
    root = logging.getLogger()
    for handler in _installed_handlers:
        root.removeHandler(handler)
        handler.close()
    _installed_handlers.clear()
    log_file = Path(storage_dir) / LOG_DIR_NAME / LOG_FILE_NAME
    handlers: list[logging.Handler] = [ConsoleHandler()]
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=LOG_FILE_MAX_BYTES,
                backupCount=LOG_FILE_BACKUPS,
                encoding="utf-8",
            )
        )
        file_error = None
    except OSError as error:
        file_error = error
    formatter = logging.Formatter(LOG_FORMAT)
    context_filter = TurnContextFilter()
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(context_filter)
        root.addHandler(handler)
        _installed_handlers.append(handler)
    root.setLevel(level)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in SERVER_LOGGERS:
        server_logger = logging.getLogger(name)
        server_logger.handlers.clear()
        server_logger.propagate = True
    if file_error is not None:
        logger.warning("Файл логов %s недоступен, логи только в консоли: %s", log_file, file_error)
        return None
    return log_file


def public_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def startup_summary(settings: Settings, log_file: Path | None) -> str:
    fake_llm = os.getenv("USE_FAKE_LLM", "false").lower() == "true"
    deadline = f"{settings.agent_turn_timeout_s} с" if settings.agent_turn_timeout_s else "нет"
    return (
        f"старт: модель={settings.llm_model} адрес LLM={public_url(settings.llm_base_url)}"
        f" structured={settings.llm_structured_mode} контекст={settings.llm_num_ctx}"
        f" таймаут={settings.llm_timeout_s} с повторы={settings.llm_max_retries}"
        f" max_tokens={settings.llm_max_tokens} бюджет_хода={deadline}"
        f" максимум_шагов={settings.agent_max_steps}"
        f" fake_llm={fake_llm}"
        f" уровень логов={settings.log_level} файл логов={log_file or 'нет'}"
    )
