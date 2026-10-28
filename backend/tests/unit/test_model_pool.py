"""Model pool: роли, fallback и переиспользование клиента."""

from pydantic import SecretStr

from app.config import Settings
from app.core.llm.profiles import ModelPool, ModelProfile


def profile(role: str, model: str) -> ModelProfile:
    return ModelProfile(
        role=role,
        model=model,
        base_url="http://localhost:11434/v1",
        api_key=SecretStr("not-needed"),
        context=8192,
        max_tokens=512,
        timeout_s=10,
    )


def test_role_falls_back_to_planner() -> None:
    pool = ModelPool([profile("planner", "p"), profile("fallback", "f")])
    assert pool.active("fast").model == "p"
    assert pool.active("sql").model == "p"


def test_get_reuses_equal_profiles() -> None:
    pool = ModelPool(
        [profile("planner", "same"), profile("fast", "same"), profile("fallback", "same")]
    )
    assert pool.get("planner") is pool.get("fast")


def test_model_router_maps_tasks_to_roles() -> None:
    from app.core.llm.profiles import ModelRouter

    router = ModelRouter()
    assert router.role_for("simple") == "fast"
    assert router.role_for("sql_generation") == "sql"
    assert router.role_for("complex") == "planner"


def test_from_settings_uses_role_overrides() -> None:
    settings = Settings(
        _env_file=None,
        llm_model="base",
        llm_model_fast="small",
        llm_model_sql="coder",
        llm_base_url_fast="http://fast.test/v1",
    )
    pool = ModelPool.from_settings(settings)
    assert pool.active("fast").model == "small"
    assert pool.active("sql").model == "coder"
    assert pool.active("planner").model == "base"
    assert pool.active("fast").base_url == "http://fast.test/v1"
