"""Dry-run: EXPLAIN ловит отсутствующую колонку до выполнения."""

from app.core.agent.dry_run import DryRunValidator
from app.core.data.duckdb_manager import DuckDBManager
from app.models.research_step import ResearchPlan, ResearchStep


def test_explain_catches_missing_column() -> None:
    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE raw AS SELECT 1 AS amount")
    plan = ResearchPlan(
        steps=[
            ResearchStep(
                step_id=0,
                tool="group_by",
                input="$source",
                arguments={"by": [], "column": "missing", "function": "SUM"},
            )
        ]
    )
    errors = DryRunValidator(db).validate(plan, "raw")
    assert errors
    assert "missing" in errors[0]
    db.close()


def test_explain_accepts_a_valid_sql() -> None:
    db = DuckDBManager(":memory:")
    db.execute("CREATE TABLE raw AS SELECT 1 AS amount")
    validator = DryRunValidator(db)
    assert validator.validate_sql("SELECT sum(amount) FROM raw") == []
    db.close()
