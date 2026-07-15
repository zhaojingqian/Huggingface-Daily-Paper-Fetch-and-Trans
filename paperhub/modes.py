#!/usr/bin/env python3
"""Canonical fetch-mode configuration and calendar semantics."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class ModeSpec:
    name: str
    limit: int
    trigger_day: Optional[int]
    trigger_hour: int

    def key_for(self, value: date) -> str:
        if self.name == "daily":
            return value.strftime("%Y-%m-%d")
        if self.name == "weekly":
            year, week, _ = value.isocalendar()
            return f"{year}-W{week:02d}"
        if self.name == "monthly":
            return value.strftime("%Y-%m")
        raise ValueError(f"unsupported fetch mode: {self.name}")

    def current_key(self, now: Optional[datetime] = None) -> str:
        return self.key_for((now or datetime.now()).date())

    def recent_keys(self, days: int, today: Optional[date] = None) -> Tuple[str, ...]:
        current = today or datetime.now().date()
        return tuple(sorted({self.key_for(current - timedelta(days=offset)) for offset in range(days)}))

    def pending_refetch_key(self, now: Optional[datetime] = None) -> Optional[str]:
        """Return the current key while its first scheduled fetch is still pending."""
        current = now or datetime.now()
        if self.name == "daily":
            pending = current.hour < self.trigger_hour
        elif self.name == "weekly":
            pending = current.weekday() != self.trigger_day or current.hour < self.trigger_hour
        elif self.name == "monthly":
            pending = current.day < self.trigger_day or (
                current.day == self.trigger_day and current.hour < self.trigger_hour
            )
        else:
            pending = False
        return self.current_key(current) if pending else None


FETCH_MODE_SPECS: Dict[str, ModeSpec] = {
    "daily": ModeSpec("daily", limit=3, trigger_day=None, trigger_hour=23),
    "weekly": ModeSpec("weekly", limit=10, trigger_day=6, trigger_hour=2),
    "monthly": ModeSpec("monthly", limit=10, trigger_day=28, trigger_hour=2),
}
FETCH_MODES = tuple(FETCH_MODE_SPECS)
CONTENT_MODES = FETCH_MODES + ("topic",)


def mode_spec(mode: str) -> ModeSpec:
    try:
        return FETCH_MODE_SPECS[mode]
    except KeyError as exc:
        raise ValueError(f"unsupported fetch mode: {mode}") from exc

