"""Аналитика: отчёты по очереди, просрочкам, нагрузке и качеству решения."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .base import Subsystem
from .enums import OPEN_STATUSES, Role, Status
from .models import Ticket, User

REPORT_ROLES = (Role.DISPATCHER, Role.MANAGER, Role.ADMIN)


@dataclass
class QueueSummary:
    by_status: dict[str, int] = field(default_factory=dict)
    by_group: dict[str, int] = field(default_factory=dict)      # открытые заявки по группам
    unassigned: int = 0


@dataclass
class QualityReport:
    closed_or_resolved: int = 0
    rated: int = 0
    average_rating: Optional[float] = None
    average_rating_by_group: dict[str, float] = field(default_factory=dict)
    in_time_percent: Optional[float] = None          # доля заявок, решённых в срок
    average_resolution_hours: Optional[float] = None
    reopened_percent: Optional[float] = None         # доля заявок, открывавшихся повторно


class Analytics(Subsystem):
    def queue_summary(self, actor_id: int) -> QueueSummary:
        self.hd.accounts.require(actor_id, *REPORT_ROLES)
        report = QueueSummary()
        for status in Status:
            count = self.db.scalar("SELECT COUNT(*) FROM tickets WHERE status = ?", (status,))
            if count:
                report.by_status[status.label] = count
        marks = ", ".join("?" for _ in OPEN_STATUSES)
        rows = self.db.execute(
            f"SELECT COALESCE(g.name, 'Без группы') AS name, COUNT(*) AS n FROM tickets t "
            f"LEFT JOIN groups g ON g.id = t.group_id WHERE t.status IN ({marks}) GROUP BY name ORDER BY name",
            OPEN_STATUSES,
        ).fetchall()
        report.by_group = {row["name"]: row["n"] for row in rows}
        report.unassigned = self.db.scalar(
            f"SELECT COUNT(*) FROM tickets WHERE assignee_id IS NULL AND status IN ({marks})", OPEN_STATUSES)
        return report

    def overdue(self, actor_id: int) -> list[Ticket]:
        """Открытые заявки, у которых целевой срок решения уже прошёл."""
        self.hd.accounts.require(actor_id, *REPORT_ROLES)
        return self.hd.sla.overdue_tickets()

    def workload(self, actor_id: int) -> list[tuple[User, int]]:
        """Нагрузка исполнителей (по убыванию)."""
        self.hd.accounts.require(actor_id, *REPORT_ROLES)
        staff = self.db.query(User, "SELECT * FROM users WHERE role IN (?, ?) AND active = 1", (Role.SPECIALIST, Role.MANAGER))
        result = [(user, self.hd.routing.workload(user.id)) for user in staff]
        return sorted(result, key=lambda pair: (-pair[1], pair[0].id))

    def quality(self, actor_id: int) -> QualityReport:
        self.hd.accounts.require(actor_id, *REPORT_ROLES)
        report = QualityReport()
        done = self.hd.store.find("status IN (?, ?)", (Status.RESOLVED, Status.CLOSED))
        report.closed_or_resolved = len(done)
        if done:
            on_time = sum(1 for t in done if t.resolved_at is None or t.resolved_at <= t.resolve_due_at)
            report.in_time_percent = round(100 * on_time / len(done), 1)
            hours = [(t.resolved_at - t.created_at).total_seconds() / 3600 for t in done if t.resolved_at]
            if hours:
                report.average_resolution_hours = round(sum(hours) / len(hours), 1)
            report.reopened_percent = round(100 * sum(1 for t in done if t.reopen_count) / len(done), 1)

        report.rated = self.db.scalar("SELECT COUNT(*) FROM feedback")
        average = self.db.scalar("SELECT AVG(rating) FROM feedback")
        report.average_rating = None if average is None else round(average, 2)
        rows = self.db.execute(
            "SELECT COALESCE(g.name, 'Без группы') AS name, AVG(f.rating) AS avg FROM feedback f "
            "JOIN tickets t ON t.id = f.ticket_id LEFT JOIN groups g ON g.id = t.group_id GROUP BY name ORDER BY name"
        ).fetchall()
        report.average_rating_by_group = {row["name"]: round(row["avg"], 2) for row in rows}
        return report
