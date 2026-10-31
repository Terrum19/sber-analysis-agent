"""DAG плана: зависимости шагов, топологические слои и валидация (P1).

Линейный план — частный случай: ``$prev`` выводит зависимость от
последнего transform-шага. Явный ``depends_on`` позволяет независимым
плиткам дашборда читать ``$source`` параллельно.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.agent.step_executor import tool_kind
from app.models.research_step import PREVIOUS, ResearchPlan, ResearchStep


@dataclass(frozen=True)
class PlanNode:
    step: ResearchStep
    depends_on: frozenset[int]


@dataclass(frozen=True)
class PlanGraph:
    nodes: dict[int, PlanNode]
    order: tuple[int, ...]

    @classmethod
    def from_plan(cls, plan: ResearchPlan) -> PlanGraph:
        """Вывести зависимости: $prev -> последний transform, $step_N -> N."""
        nodes: dict[int, PlanNode] = {}
        last_transform: int | None = None
        for step in plan.steps:
            depends = set(step.depends_on)
            if step.input == PREVIOUS and last_transform is not None:
                depends.add(last_transform)
            ref = _step_ref(step.input)
            if ref is not None:
                depends.add(ref)
            nodes[step.step_id] = PlanNode(step=step, depends_on=frozenset(depends))
            if tool_kind(step) == "transform":
                last_transform = step.step_id
        return cls(nodes=nodes, order=tuple(step.step_id for step in plan.steps))

    def validate(self) -> list[str]:
        """Проверить ссылки, самоссылки, $prev без transform и циклы."""
        errors: list[str] = []
        for step_id in self.order:
            node = self.nodes[step_id]
            if (
                node.step.input == PREVIOUS
                and not node.depends_on
                and self.order.index(step_id) == 0
            ):
                # $prev у первого шага — это источник, такие планы допустимы.
                continue
            for dependency in sorted(node.depends_on):
                if dependency == step_id:
                    errors.append(f"шаг {step_id}: зависит от самого себя")
                elif dependency not in self.nodes:
                    errors.append(f"шаг {step_id}: зависимости {dependency} нет в плане")
        order = self._topological_order()
        if order is None:
            errors.append("в плане есть цикл зависимостей")
        return errors

    def layers(self) -> list[list[ResearchStep]]:
        """Шаги по слоям: слой можно выполнять параллельно (зависимости готовы)."""
        remaining = set(self.order)
        done: set[int] = set()
        layers: list[list[ResearchStep]] = []
        while remaining:
            layer = [
                step_id
                for step_id in self.order
                if step_id in remaining and self.nodes[step_id].depends_on <= done
            ]
            if not layer:
                break
            layers.append([self.nodes[step_id].step for step_id in layer])
            remaining.difference_update(layer)
            done.update(layer)
        return layers

    def dependents(self, step_id: int) -> set[int]:
        """Шаги, которые прямо зависят от данного."""
        return {other_id for other_id, node in self.nodes.items() if step_id in node.depends_on}

    def _topological_order(self) -> tuple[int, ...] | None:
        remaining = set(self.order)
        done: set[int] = set()
        order: list[int] = []
        while remaining:
            layer = sorted(
                step_id for step_id in remaining if self.nodes[step_id].depends_on <= done
            )
            if not layer:
                return None
            order.extend(layer)
            done.update(layer)
            remaining.difference_update(layer)
        return tuple(order)


def _step_ref(value: str) -> int | None:
    if value.startswith("$step_") and value[6:].isdigit():
        return int(value[6:])
    if value.startswith("step_") and value[5:].isdigit():
        return int(value[5:])
    return None
