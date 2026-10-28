"""Параллельный executor DAG: слои, изоляция ошибок."""

import asyncio

from app.core.agent.parallel_executor import ParallelPlanExecutor
from app.core.agent.plan_graph import PlanGraph
from app.core.agent.step_executor import StepExecutor
from app.core.data.duckdb_manager import DuckDBManager
from app.models.research_step import ResearchPlan, ResearchStep


def db() -> DuckDBManager:
    manager = DuckDBManager(":memory:")
    manager.execute(
        "CREATE TABLE raw AS SELECT * FROM (VALUES ('msk', 10.0), ('spb', 4.0), ('msk', 2.5)) "
        "AS rows(region, amount)"
    )
    return manager


def test_independent_steps_run_in_one_layer() -> None:
    manager = db()
    steps = [
        ResearchStep(
            step_id=0,
            tool="group_by",
            input="$source",
            arguments={"by": ["region"], "column": "amount", "function": "SUM"},
        ),
        ResearchStep(
            step_id=1,
            tool="group_by",
            input="$source",
            arguments={"by": ["region"], "column": "amount", "function": "AVG"},
        ),
    ]
    plan = ResearchPlan(steps=steps)
    graph = PlanGraph.from_plan(plan)
    events: list[dict] = []
    execution, tables = asyncio.run(
        ParallelPlanExecutor(StepExecutor(manager)).execute(
            graph, "raw", "raw", events.append, plan=plan
        )
    )
    assert set(tables) == {0, 1}
    assert execution.final_viz == "auto"
    assert [event["data"]["status"] for event in events].count("finished") == 2
    manager.close()


def test_failed_node_blocks_dependents() -> None:
    manager = db()
    steps = [
        ResearchStep(
            step_id=0,
            tool="group_by",
            input="$source",
            arguments={"by": ["region"], "column": "missing", "function": "SUM"},
        ),
        ResearchStep(
            step_id=1,
            tool="sort_limit",
            input="$step_0",
            depends_on=[0],
            arguments={"order_by": "sum_amount", "limit": 1},
        ),
    ]
    plan = ResearchPlan(steps=steps)
    graph = PlanGraph.from_plan(plan)
    execution, tables = asyncio.run(
        ParallelPlanExecutor(StepExecutor(manager)).execute(graph, "raw", "raw", plan=plan)
    )
    assert tables == {}
    assert execution.result_table == "raw"
    manager.close()
