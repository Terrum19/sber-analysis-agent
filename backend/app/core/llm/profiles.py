"""Пул моделей по ролям: planner / fast / sql / fallback (P1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import SecretStr

from app.config import Settings, settings

if TYPE_CHECKING:
    from app.core.llm.client import LLMClient

Role = Literal["planner", "fast", "sql", "fallback"]


@dataclass(frozen=True)
class ModelProfile:
    role: Role
    model: str
    base_url: str
    api_key: SecretStr
    context: int
    max_tokens: int
    quantization: str | None = None
    timeout_s: int = 300


class ModelPool:
    """Ленивый пул LLMClient: роль -> клиент; при сбое — fallback."""

    def __init__(self, profiles: list[ModelProfile]) -> None:
        self._profiles = {profile.role: profile for profile in profiles}
        self._clients: dict[str, LLMClient] = {}

    def active(self, role: str) -> ModelProfile:
        """Профиль роли с цепочкой отката: role -> planner -> fallback."""
        for name in (role, "planner", "fallback"):
            profile = self._profiles.get(name)
            if profile is not None:
                return profile
        raise KeyError(f"в пуле нет ролей {role}/planner/fallback")

    def get(self, role: str) -> LLMClient:
        """Клиент роли; одинаковые профили переиспользуют один клиент."""
        profile = self.active(role)
        key = (profile.model, profile.base_url, profile.timeout_s, profile.max_tokens)
        if key not in self._clients:
            from app.core.llm.client import LLMClient

            try:
                self._clients[key] = LLMClient(
                    model=profile.model,
                    base_url=profile.base_url,
                    api_key=profile.api_key,
                    timeout_s=profile.timeout_s,
                    max_tokens=profile.max_tokens,
                )
            except Exception:  # недоступная роль откатывается на fallback
                # Роль недоступна (битый URL/ключ): откат на fallback.
                if profile.role != "fallback":
                    return self.get("fallback")
                raise
        return self._clients[key]

    @classmethod
    def from_settings(cls, config: Settings | None = None) -> ModelPool:
        """Собрать профили из переменных LLM_MODEL_* / LLM_BASE_URL_*."""
        config = config or settings
        base = config.llm_base_url
        profiles = [
            ModelProfile(
                role="planner",
                model=config.llm_model_planner or config.llm_model,
                base_url=base,
                api_key=config.llm_api_key,
                context=config.llm_num_ctx,
                max_tokens=config.llm_max_tokens,
                timeout_s=config.llm_timeout_s,
            ),
            ModelProfile(
                role="fast",
                model=config.llm_model_fast or config.llm_model,
                base_url=config.llm_base_url_fast or base,
                api_key=config.llm_api_key,
                context=config.llm_num_ctx,
                max_tokens=config.llm_max_tokens,
                timeout_s=config.llm_timeout_s,
            ),
            ModelProfile(
                role="sql",
                model=config.llm_model_sql or config.llm_model,
                base_url=config.llm_base_url_sql or base,
                api_key=config.llm_api_key,
                context=config.llm_num_ctx,
                max_tokens=config.llm_max_tokens,
                timeout_s=config.llm_timeout_s,
            ),
            ModelProfile(
                role="fallback",
                model=config.llm_model,
                base_url=base,
                api_key=config.llm_api_key,
                context=config.llm_num_ctx,
                max_tokens=config.llm_max_tokens,
                timeout_s=config.llm_timeout_s,
            ),
        ]
        return cls(profiles)


class ModelRouter:
    """Простые правила выбора роли модели по типу задачи (P1)."""

    def role_for(self, task: str) -> str:
        lowered = task.lower()
        if lowered in ("simple", "insight", "tile"):
            return "fast"
        if lowered in ("sql", "sql_generation", "codegen"):
            return "sql"
        return "planner"


def profile_for(pool: ModelPool, task: str) -> ModelProfile:
    """Активный профиль под задачу (с учётом fallback пула)."""
    return pool.active(ModelRouter().role_for(task))
