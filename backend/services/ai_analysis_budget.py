"""Per-user daily budget for the AI analytics tier.

A run of that tier is one long call against the user's own provider quota,
so an administrator can cap how many a user starts per UTC day, install-wide
with a per-user override. The budget is checked, and the run logged, where a
run is queued: the tab's button and the MCP tool (through the API), and the
post-processing follow-up for users who opted in to automatic analysis.

The check and the log are deliberately one step apart rather than one
transaction: two requests that race past the cap together spend one extra
run, which is a cost worth less than a row lock on every read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from backend.models.ai_analysis import AiAnalysisRun
from backend.models.user import User
from backend.utils.config_manager import (
    AI_ANALYSIS_DAILY_LIMIT_KEY,
    config_manager,
    normalise_ai_analysis_daily_limit,
)
from backend.utils.time import utc_now


class AiAnalysisBudgetExhausted(Exception):
    """The user has started as many runs today as the cap allows."""

    def __init__(self, budget: "AiAnalysisBudget") -> None:
        super().__init__(budget.exhausted_message())
        self.budget = budget


@dataclass(frozen=True)
class AiAnalysisBudget:
    limit: int | None
    used: int
    resets_at: datetime

    @property
    def remaining(self) -> int | None:
        if self.limit is None:
            return None
        return max(self.limit - self.used, 0)

    @property
    def exhausted(self) -> bool:
        return self.limit is not None and self.used >= self.limit

    def exhausted_message(self) -> str:
        return (
            f"Your daily AI analysis budget of {self.limit} "
            f"{'run' if self.limit == 1 else 'runs'} is used up. It resets at "
            f"{self.resets_at:%H:%M} UTC."
        )

    def as_dict(self) -> dict:
        return {
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
            # Explicitly UTC so the client does not read it as local time.
            "resets_at": self.resets_at.replace(tzinfo=timezone.utc).isoformat(),
        }


def resolve_daily_limit(user: User) -> int | None:
    """The cap that applies to ``user``: their override, else the install's.

    A per-user 0 is an explicit "no cap" that overrides an install cap; a
    per-user null inherits.
    """
    override = user.ai_analysis_daily_limit
    if override is not None:
        return normalise_ai_analysis_daily_limit(override)
    return normalise_ai_analysis_daily_limit(
        config_manager.get(AI_ANALYSIS_DAILY_LIMIT_KEY)
    )


def day_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The UTC day ``now`` falls in: its start and the next midnight."""
    moment = now or utc_now()
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _runs_since_statement(user_id: int, start: datetime):
    return (
        select(func.count())
        .select_from(AiAnalysisRun)
        .where(AiAnalysisRun.user_id == user_id)
        .where(AiAnalysisRun.created_at >= start)
    )


def _budget(limit: int | None, used: int, resets_at: datetime) -> AiAnalysisBudget:
    return AiAnalysisBudget(limit=limit, used=int(used or 0), resets_at=resets_at)


async def get_budget(db, user: User) -> AiAnalysisBudget:
    start, resets_at = day_window()
    used = (await db.execute(_runs_since_statement(user.id, start))).scalar_one()
    return _budget(resolve_daily_limit(user), used, resets_at)


async def claim_run(db, user: User, recording_id: int, source: str) -> AiAnalysisBudget:
    """Log a run for ``user`` if the budget allows, else raise.

    The row is added to ``db`` and left for the caller to commit alongside the
    dispatch, so a dispatch that never reaches the broker spends nothing.
    """
    budget = await get_budget(db, user)
    if budget.exhausted:
        raise AiAnalysisBudgetExhausted(budget)
    db.add(AiAnalysisRun(user_id=user.id, recording_id=recording_id, source=source))
    return _budget(budget.limit, budget.used + 1, budget.resets_at)


def get_budget_sync(session, user: User) -> AiAnalysisBudget:
    start, resets_at = day_window()
    used = session.execute(_runs_since_statement(user.id, start)).scalar_one()
    return _budget(resolve_daily_limit(user), used, resets_at)


def claim_run_sync(session, user: User, recording_id: int, source: str) -> bool:
    """The worker-side claim: True when a run was logged, False when capped."""
    budget = get_budget_sync(session, user)
    if budget.exhausted:
        return False
    session.add(
        AiAnalysisRun(user_id=user.id, recording_id=recording_id, source=source)
    )
    return True
