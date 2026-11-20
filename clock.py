"""Часы системы. Подменяемые часы нужны для тестов и демо (сроки, автозакрытие)."""
from datetime import datetime, timedelta, timezone
from typing import Optional


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class ManualClock:
    """Часы, которые идут только когда их сдвигают: clock.advance(hours=5)."""

    def __init__(self, start: Optional[datetime] = None):
        self._now = start or datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)
