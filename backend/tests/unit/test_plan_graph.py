"""DAG плана: вывод зависимостей, циклы и слои."""

from app.core.agent.plan_graph import PlanGraph
from app.models.research_step import ResearchPlan, ResearchStep


def plan(*steps: ResearchStep) -> ResearchPlan:
    return ResearchPlan(steps=list(steps))


def test_prev_and_step_refs_are_inferred() -> None:
    graph = PlanGraph.from_plan(
        plan(
            ResearchStep(step_id=0, tool="filter_rows", input="$source", arguments={"column": "a", "value": 1}),
            ResearchStep(step_id=1, tool="group_by", input="$prev", arguments={"by": [], "function": "COUNT"}),
            ResearchStep(step_id=2, tool="sort_limit", input="$step_0", arguments={"order_by": "a"}),
        )
    )
    assert graph.nodes[1].depends_on == frozenset({0})
    assert graph.nodes[2].depends_on == frozenset({0})
    assert graph.validate() == []


def test_explicit_dependency_is_kept() -> None:
    graph = PlanGraph.from_plan(
        plan(
            ResearchStep(step_id=0, tool="aggregate", input="$source", arguments={"column": "a"}),
            ResearchStep(
                step_id=1,
                tool="group_by",
                input="$source",
                depends_on=[0],
                arguments={"by": [], "function": "COUNT"},
            ),
        )
    )
    assert graph.nodes[1].depends_on == frozenset({0})
    assert graph.layers() == [
        [graph.nodes[0].step],
        [graph.nodes[1].step],
    ]


def test_missing_and_self_dependencies_are_errors() -> None:
    graph = PlanGraph.from_plan(
        plan(
            ResearchStep(step_id=0, tool="aggregate", depends_on=[0], arguments={"column": "a"}),
            ResearchStep(step_id=1, tool="aggregate", depends_on=[9], arguments={"column": "a"}),
        )
    )
    errors = graph.validate()
    assert any("самого себя" in error for error in errors)
    assert any("зависимости 9" in error for error in errors)


def test_cycle_is_reported() -> None:
    graph = PlanGraph.from_plan(
        plan(
            ResearchStep(step_id=0, tool="aggregate", depends_on=[1], arguments={"column": "a"}),
            ResearchStep(step_id=1, tool="aggregate", depends_on=[0], arguments={"column": "a"}),
        )
    )
    assert "цикл" in " ".join(graph.validate())


def test_dependents_are_direct() -> None:
    graph = PlanGraph.from_plan(
        plan(
            ResearchStep(step_id=0, tool="aggregate", arguments={"column": "a"}),
            ResearchStep(step_id=1, tool="sort_limit", depends_on=[0], arguments={"order_by": "cnt"}),
            ResearchStep(step_id=2, tool="sort_limit", depends_on=[0], arguments={"order_by": "cnt"}),
        )
    )
    assert graph.dependents(0) == {1, 2}
