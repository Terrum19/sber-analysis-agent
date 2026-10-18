"""Shared validation and step counting for funnel tools."""

from app.core.tools.base import identifier
from app.core.tools.stats._common import literal, rows_result


def columns(columns: list[str], minimum: int, maximum: int, name: str) -> None:
    if not minimum <= len(columns) <= maximum:
        raise ValueError(f"{name} requires between {minimum} and {maximum} columns")
    for column in columns:
        identifier(column)


def ordered_steps(db, table: str, step_col: str, order_col: str, user_col: str | None) -> list:
    """Infer the step order when the plan does not give it.

    With a user column, step A goes before step B when more users reach A
    first than B first; steps are ranked by the number of such pairwise wins,
    so a single stray early event does not reorder the funnel. Without a user
    column, steps are ordered by their earliest event.
    """
    table, step, order = identifier(table), identifier(step_col), identifier(order_col)
    if user_col is None:
        rows = db.query(
            f"SELECT {step} AS step, MIN({order}) AS first_order FROM {table} "
            f"WHERE {step} IS NOT NULL GROUP BY {step} ORDER BY first_order, step"
        )
        return [row["step"] for row in rows]
    user = identifier(user_col)
    firsts = (
        f"SELECT {user} AS user_id, {step} AS step, MIN({order}) AS first_order "
        f"FROM {table} WHERE {user} IS NOT NULL AND {step} IS NOT NULL "
        f"GROUP BY {user}, {step}"
    )
    steps = db.query(f"SELECT step, COUNT(*) AS users FROM ({firsts}) GROUP BY step ORDER BY step")
    margins = db.query(
        f"WITH firsts AS ({firsts}) "
        "SELECT earlier.step AS step, later.step AS other, "
        "SUM(CASE WHEN earlier.first_order < later.first_order THEN 1 "
        "WHEN earlier.first_order > later.first_order THEN -1 ELSE 0 END) AS margin "
        "FROM firsts AS earlier JOIN firsts AS later "
        "ON earlier.user_id = later.user_id AND earlier.step <> later.step "
        "GROUP BY earlier.step, later.step"
    )
    wins = {row["step"]: 0 for row in steps}
    for row in margins:
        wins[row["step"]] += (row["margin"] > 0) - (row["margin"] < 0)
    users = {row["step"]: row["users"] for row in steps}
    return sorted(wins, key=lambda value: (-wins[value], -users[value], str(value)))


def step_counts(
    db,
    table: str,
    step_col: str,
    order_col: str,
    user_col: str | None,
    steps: object = None,
) -> list[tuple[object, int]]:
    """Count each funnel step in order.

    With a user column a user is counted at step k only after reaching steps
    1..k-1 in that order (at the same moment or later). Without one, every
    event of the step is counted.
    """
    if steps is None:
        steps = ordered_steps(db, table, step_col, order_col, user_col)
    if not isinstance(steps, (list, tuple)) or not steps:
        raise ValueError("funnel requires a non-empty list of steps")
    steps = list(steps)
    table, step, order = identifier(table), identifier(step_col), identifier(order_col)
    if user_col is None:
        rows = db.query(
            f"SELECT {step} AS step, COUNT(*) AS count FROM {table} "
            f"WHERE {step} IN ({', '.join(literal(value) for value in steps)}) GROUP BY {step}"
        )
        counts = {str(row["step"]): int(row["count"]) for row in rows}
        return [(value, counts.get(str(value), 0)) for value in steps]
    user = identifier(user_col)
    first_step = (
        f"reached_1 AS (SELECT {user} AS user_id, MIN({order}) AS reached_at FROM {table} "
        f"WHERE {step} = {literal(steps[0])} AND {user} IS NOT NULL GROUP BY {user})"
    )
    reached = [first_step]
    for position, value in enumerate(steps[1:], start=2):
        reached.append(
            f"reached_{position} AS (SELECT events.{user} AS user_id, "
            f"MIN(events.{order}) AS reached_at FROM {table} AS events "
            f"JOIN reached_{position - 1} AS previous ON events.{user} = previous.user_id "
            f"AND events.{order} >= previous.reached_at "
            f"WHERE events.{step} = {literal(value)} GROUP BY events.{user})"
        )
    counts_sql = " UNION ALL ".join(
        f"SELECT {position} AS position, COUNT(*) AS count FROM reached_{position}"
        for position in range(1, len(steps) + 1)
    )
    rows = db.query(f"WITH {', '.join(reached)} {counts_sql} ORDER BY position")
    return [(value, int(row["count"])) for value, row in zip(steps, rows)]


__all__ = ["columns", "identifier", "ordered_steps", "rows_result", "step_counts"]
