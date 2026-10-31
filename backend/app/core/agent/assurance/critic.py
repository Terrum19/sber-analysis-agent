"""Критик ответа (A-29): один структурированный вызов LLM и проверка вердикта кодом.

Модель ≤ 70B, проверяющая собственную работу, склонна либо одобрять всё,
либо придумывать претензии. Поэтому:
- улики посчитаны кодом (evidence.py), вопросы — чек-лист с фиксированными id;
- вердикт проверяет settle(): чужие id отбрасываются, material без улик —
  minor, extend_plan / replan без material — только оговорка;
- во втором раунде (после исправления) — только ok или caveat.
"""

import logging

from app.core.llm.client import LLMClient
from app.core.llm.prompts.review import REVIEW_FINAL, REVIEW_PROMPT, REVIEW_SYSTEM
from app.models.review import CHECKS, REPAIRS, Review, ReviewCheck

logger = logging.getLogger(__name__)

# Повтор вызова критика, если модель не ответила или ответила не по схеме.
REVIEW_ATTEMPTS = 2


class Critic:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def review(self, evidence: str, round: int = 1, previous: Review | None = None) -> Review:
        """Вердикт по уликам; не получилось за REVIEW_ATTEMPTS — исключение."""
        checks = "\n".join(f"- {key}: {title}" for key, title in CHECKS.items())
        prompt = REVIEW_PROMPT.format(evidence=evidence, checks=checks)
        if round > 1 and previous is not None:
            prompt += REVIEW_FINAL.format(previous=describe_issues(previous))
        error: Exception | None = None
        for attempt in range(1, REVIEW_ATTEMPTS + 1):
            try:
                raw = await self.llm.complete_structured(REVIEW_SYSTEM, prompt, Review)
            except Exception as exc:  # noqa: BLE001 — повторяем любой сбой модели
                logger.warning("критик: попытка %d не удалась: %s", attempt, exc)
                error = exc
                prompt += f"\n\nПрошлый ответ отклонён: {exc}. Ответь строго по схеме."
                continue
            settled = settle(raw, round)
            logger.info(
                "критик, раунд %d: вердикт %s -> %s, проблем %d",
                round,
                raw.verdict,
                settled.verdict,
                len(settled.issues),
            )
            return settled
        raise RuntimeError(f"критик не ответил: {error}")


def settle(review: Review, round: int = 1) -> Review:
    """Привести ответ критика к правилам, которые модель могла нарушить."""
    seen: dict[str, ReviewCheck] = {}
    for check in review.checks:
        if check.id in CHECKS and check.id not in seen:
            seen[check.id] = check
    checks = [seen.get(key) or ReviewCheck(id=key) for key in CHECKS]
    issues = []
    for issue in review.issues:
        if issue.check not in CHECKS or not issue.message.strip():
            continue
        if issue.severity == "material" and not issue.evidence.strip():
            issue = issue.model_copy(update={"severity": "minor"})
        issues.append(issue)
    material = any(issue.severity == "material" for issue in issues)
    verdict = review.verdict
    if verdict in ("extend_plan", "replan") and not material:
        verdict = "caveat"
    if verdict == "fix_insight" and not issues:
        verdict = "ok"
    if round > 1 and verdict in REPAIRS:
        verdict = "caveat"
    caveats = [text.strip() for text in review.caveats if text.strip()]
    if verdict == "caveat" and not caveats:
        caveats = [issue.message for issue in issues]
        if not caveats:
            verdict = "ok"
    # Пункт с проблемой, у которого нет записи в issues, всё равно виден в чек-листе.
    flagged = {issue.check for issue in issues}
    checks = [
        check.model_copy(update={"status": "issue"})
        if check.id in flagged and check.status != "issue"
        else check
        for check in checks
    ]
    return Review(checks=checks, issues=issues, verdict=verdict, caveats=caveats)


def describe_issues(review: Review) -> str:
    lines = [
        f"- [{issue.check}, {issue.severity}] {issue.message}"
        + (f" (основание: {issue.evidence})" if issue.evidence else "")
        + (f" — исправить: {issue.fix}" if issue.fix else "")
        for issue in review.issues
    ]
    return "\n".join(lines) or "- " + "; ".join(review.caveats or ["замечаний нет"])


def review_payload(review: Review, round: int, action: str) -> dict:
    """Данные события review для фронта."""
    return {
        "round": round,
        "verdict": review.verdict,
        "action": action,
        "checks": [
            {
                "id": check.id,
                "title": CHECKS[check.id],
                "status": check.status,
                "evidence": check.evidence,
            }
            for check in review.checks
        ],
        "issues": [issue.model_dump() for issue in review.issues],
        "caveats": list(review.caveats),
    }
