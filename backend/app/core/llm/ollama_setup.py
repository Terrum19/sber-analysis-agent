"""Подготовка модели на сервере Ollama при старте бэкенда (#167).

Пользователь не настраивает Ollama сам: бэкенд через её HTTP API
1. скачивает LLM_MODEL, если модели на сервере нет (/api/pull);
2. создаёт вариант модели с контекстом LLM_NUM_CTX (/api/create) и дальше
   работает с ним. У Ollama по умолчанию контекст 4096, длинный промпт она
   молча обрезает, а options.num_ctx в запросе к /v1 не действует (budget.py).
   Параметр num_ctx в самой модели действует и через /v1.

Сервер не Ollama (vLLM, облако: нет /api/version) — шаги пропускаются,
модель берётся из LLM_MODEL как есть. Пока Ollama недоступна, бэкенд
переподключается сам; что происходит, фронт видит в /api/llm/status.
Модели LLM_MODEL нет в реестре (опечатка) — состояние missing: скачивание
больше не повторяется, бэкенд только ждёт, что модель появится на сервере.
"""

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from typing import Literal

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

State = Literal["connecting", "pulling", "preparing", "missing", "ok"]

# Пауза между попытками достучаться до Ollama и повторить неудавшийся pull.
RETRY_S = 5.0
PULL_RETRY_S = 30.0
# Как часто проверять, что готовая Ollama всё ещё отвечает.
WATCH_S = 30.0
REQUEST_TIMEOUT = httpx.Timeout(10.0)
# Скачивание шлёт строку прогресса хотя бы раз в несколько секунд; create
# копирует только манифест, но на медленном диске может занять минуту.
PULL_TIMEOUT = httpx.Timeout(10.0, read=300.0)
CREATE_TIMEOUT = httpx.Timeout(10.0, read=120.0)
# Так Ollama отвечает на pull модели, которой нет в реестре.
MISSING_MODEL_ERRORS = ("file does not exist", "invalid model name")


@dataclass
class LLMStatus:
    state: State
    model: str
    progress: float | None = None
    detail: str | None = None


_status = LLMStatus("connecting", settings.llm_model)
# Модель, с которой работает LLMClient: вариант с контекстом или LLM_MODEL.
_active_model: str | None = None
# Модель, которой нет в реестре: её больше не скачиваем.
_missing_model: str | None = None


def status() -> dict:
    """Состояние LLM для /api/llm/status."""
    return asdict(_status)


def mark_ready() -> None:
    """Подготовка выключена (LLM_PREPARE_MODEL=false, фейковая LLM): LLM считаем готовой."""
    _set("ok")


def active_model() -> str:
    """Имя модели для запросов: подготовленный вариант или LLM_MODEL."""
    return _active_model or settings.llm_model


def _set(state: State, progress: float | None = None, detail: str | None = None) -> None:
    global _status
    _status = LLMStatus(state, settings.llm_model, progress, detail)


def _with_tag(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


def variant_name(model: str, num_ctx: int) -> str:
    """Имя варианта модели с контекстом: qwen2.5-coder:7b -> qwen2.5-coder:7b-ctx16384."""
    return f"{_with_tag(model)}-ctx{num_ctx}"


def server_root(base_url: str) -> str:
    """Корень сервера без /v1: http://host:11434/v1 -> http://host:11434."""
    return base_url.rstrip("/").removesuffix("/v1")


class NotOllama(Exception):
    """Сервер отвечает, но это не Ollama: модель готовить не нужно."""


class ModelMissing(RuntimeError):
    """Модели нет в реестре Ollama: повтор скачивания не поможет."""


async def _version(http: httpx.AsyncClient, root: str) -> str:
    response = await http.get(f"{root}/api/version", timeout=REQUEST_TIMEOUT)
    if response.status_code >= 500:
        # Ollama или прокси перед ней ещё не готовы (502/503): повторить,
        # а не счесть сервер чужим — иначе подготовка не запустится никогда.
        response.raise_for_status()
    if response.status_code != 200:
        raise NotOllama
    try:
        return response.json()["version"]
    except (ValueError, KeyError, TypeError) as exc:
        raise NotOllama from exc


async def _models(http: httpx.AsyncClient, root: str) -> set[str]:
    response = await http.get(f"{root}/api/tags", timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return {_with_tag(model["name"]) for model in response.json().get("models", [])}


async def _pull(http: httpx.AsyncClient, root: str, model: str) -> None:
    """Скачать модель, обновляя прогресс; ошибка сервера — RuntimeError."""
    _set("pulling", progress=0.0)
    # Ollama шлёт прогресс по каждому слою: доля — по сумме слоёв.
    layers: dict[str, tuple[int, int]] = {}
    async with http.stream(
        "POST", f"{root}/api/pull", json={"model": model}, timeout=PULL_TIMEOUT
    ) as response:
        if response.is_error:
            await response.aread()
            raise _pull_error(_error_text(response))
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("error"):
                raise _pull_error(event["error"])
            if event.get("digest") and event.get("total"):
                layers[event["digest"]] = (event.get("completed", 0), event["total"])
                done = sum(completed for completed, _ in layers.values())
                total = sum(size for _, size in layers.values())
                _set("pulling", progress=round(done / total, 3))


def _error_text(response: httpx.Response) -> str:
    try:
        return response.json()["error"]
    except (ValueError, KeyError, TypeError):
        return f"HTTP {response.status_code}"


def _pull_error(message: str) -> RuntimeError:
    if any(marker in message for marker in MISSING_MODEL_ERRORS):
        return ModelMissing(message)
    return RuntimeError(message)


async def _create(http: httpx.AsyncClient, root: str, model: str, variant: str) -> None:
    """Создать вариант модели с num_ctx; старые Ollama без поля from — через Modelfile."""
    _set("preparing")
    num_ctx = settings.llm_num_ctx
    payloads = [
        {"model": variant, "from": model, "parameters": {"num_ctx": num_ctx}, "stream": False},
        {
            "model": variant,
            "modelfile": f"FROM {model}\nPARAMETER num_ctx {num_ctx}",
            "stream": False,
        },
    ]
    error: Exception | None = None
    for payload in payloads:
        try:
            response = await http.post(f"{root}/api/create", json=payload, timeout=CREATE_TIMEOUT)
            response.raise_for_status()
            return
        except httpx.HTTPStatusError as exc:
            error = exc
    raise RuntimeError(f"Ollama не создала {variant}: {error}")


async def prepare(http: httpx.AsyncClient) -> bool:
    """Один проход подготовки. True — LLM готова, False — повторить позже."""
    global _active_model, _missing_model
    root = server_root(settings.llm_base_url)
    model = _with_tag(settings.llm_model)
    try:
        version = await _version(http, root)
    except NotOllama:
        logger.info("Сервер LLM %s — не Ollama, модель берём как есть", root)
        _active_model = None
        _set("ok")
        return True
    except httpx.HTTPError as exc:
        _set("connecting", detail=str(exc) or type(exc).__name__)
        return False

    try:
        models = await _models(http, root)
        if model not in models and model == _missing_model:
            _set("missing", detail=_status.detail)
            return False
        if model not in models:
            logger.info("Ollama %s: скачиваю %s", version, model)
            await _pull(http, root, model)
        variant = variant_name(model, settings.llm_num_ctx)
        if variant not in models:
            try:
                await _create(http, root, model, variant)
            except (RuntimeError, httpx.HTTPError):
                # Без варианта работаем на LLM_MODEL: контекст тогда задаёт
                # сервер (OLLAMA_CONTEXT_LENGTH), обрезку заметит budget.
                logger.warning(
                    "Вариант %s не создан, работаем на %s", variant, model, exc_info=True
                )
                variant = model
    except httpx.TransportError as exc:
        _set("connecting", detail=str(exc) or type(exc).__name__)
        return False
    except ModelMissing as exc:
        logger.error("Ollama: модели %s нет в реестре, проверьте LLM_MODEL: %s", model, exc)
        _missing_model = model
        _set("missing", detail=str(exc))
        return False
    except (RuntimeError, httpx.HTTPStatusError) as exc:
        # Например, нет интернета для скачивания: повторим, причина — в detail.
        logger.warning("Ollama: модель %s не подготовлена: %s", model, exc)
        _set("pulling", progress=_status.progress, detail=str(exc))
        return False

    _active_model = variant
    _set("ok")
    logger.info("Ollama %s: модель готова — %s", version, variant)
    return True


async def prepare_once(http: httpx.AsyncClient) -> bool:
    try:
        return await prepare(http)
    except Exception as exc:
        logger.exception("Ollama: подготовка модели %s сорвалась", settings.llm_model)
        _set(_status.state, progress=_status.progress, detail=str(exc) or type(exc).__name__)
        return False


async def prepare_forever() -> None:
    """Готовить модель, пока не получится, затем следить, что Ollama жива.

    Ollama пропала (закрыли приложение, перезапуск) — снова connecting и
    подготовка заново: модель могли удалить, пока сервера не было.
    """
    from app.core.llm.client import LLMClient

    async with httpx.AsyncClient() as http:
        while True:
            while not await prepare_once(http):
                await asyncio.sleep(
                    PULL_RETRY_S if _status.state in ("pulling", "missing") else RETRY_S
                )
            # Контекст сервера — до первого вопроса: от него считается бюджет промпта.
            try:
                await LLMClient().warm_up()
            except Exception:
                logger.warning("LLM: контекст сервера не узнали при старте", exc_info=True)
            while True:
                await asyncio.sleep(WATCH_S)
                try:
                    await _version(http, server_root(settings.llm_base_url))
                except NotOllama:
                    continue
                except httpx.HTTPError as exc:
                    logger.warning("Ollama перестала отвечать: %s", exc)
                    _set("connecting", detail=str(exc) or type(exc).__name__)
                    break
