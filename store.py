"""Реестр заявок: хранение, единая точка смены статуса и история изменений."""
from __future__ import annotations

import dataclasses
from typing import Optional

from .base import Subsystem
from .enums import OPEN_STATUSES, Status
from .errors import InvalidTransition, NotFound, ValidationError
from .models import (
    Assignment, Attachment, Comment, Escalation, FieldChange, StatusEvent, Ticket, TimelineEntry,
)
from .rules import TRANSITIONS


class TicketStore(Subsystem):
    # ------------------------------------------------------------ чтение
    def get(self, ticket_id: int) -> Ticket:
        ticket = self.db.one(Ticket, "SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        if ticket is None:
            raise NotFound(f"Заявка {ticket_id} не найдена")
        return ticket

    def find(self, where: str = "1 = 1", params=()) -> list[Ticket]:
        """Заявки по условию; сначала более важные, затем более старые."""
        tickets = self.db.query(Ticket, f"SELECT * FROM tickets WHERE {where} ORDER BY id", params)
        return sorted(tickets, key=lambda t: (t.priority.rank, t.created_at))

    def open_tickets(self) -> list[Ticket]:
        marks = ", ".join("?" for _ in OPEN_STATUSES)
        return self.find(f"status IN ({marks})", OPEN_STATUSES)

    def save(self, ticket: Ticket) -> None:
        ticket.updated_at = self.now()
        values = {f.name: getattr(ticket, f.name) for f in dataclasses.fields(ticket) if f.name != "id"}
        self.db.update("tickets", ticket.id, **values)

    # ------------------------------------------------------------ смена статуса
    def change_status(self, ticket: Ticket, new_status: Status, author_id: Optional[int], reason: str) -> None:
        """ЕДИНСТВЕННОЕ место, где меняется статус: проверяет переход, пишет событие с автором, временем и причиной."""
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("Укажите причину смены статуса")
        old_status = ticket.status
        if new_status not in TRANSITIONS[old_status]:
            raise InvalidTransition(f"Переход «{old_status.label}» → «{new_status.label}» недопустим")
        if new_status in (Status.RESOLVED, Status.CLOSED) and not (ticket.resolution_text or "").strip():
            raise ValidationError("Заявку нельзя решить или закрыть без описания решения")

        now = self.now()
        ticket.status = new_status
        if new_status is Status.IN_PROGRESS and ticket.first_response_at is None:
            ticket.first_response_at = now
        if new_status is Status.CLOSED:
            ticket.closed_at = now
        self.hd.sla.on_status_change(ticket, old_status, new_status, now)
        self.save(ticket)
        self.db.insert(
            "status_events", ticket_id=ticket.id, from_status=old_status, to_status=new_status,
            author_id=author_id, reason=reason, created_at=self.now(),
        )

    def log_assignment(self, ticket: Ticket, author_id: Optional[int], reason: str) -> None:
        """Запись в историю назначений (текущие группа и исполнитель берутся из заявки)."""
        self.db.insert(
            "assignments", ticket_id=ticket.id, group_id=ticket.group_id, assignee_id=ticket.assignee_id,
            author_id=author_id, reason=reason, created_at=self.now(),
        )

    def log_field_change(self, ticket_id: int, field: str, old, new, author_id: Optional[int], reason: str) -> None:
        self.db.insert(
            "field_changes", ticket_id=ticket_id, field=field,
            old_value=None if old is None else str(getattr(old, "label", old)),
            new_value=None if new is None else str(getattr(new, "label", new)),
            author_id=author_id, reason=reason, created_at=self.now(),
        )

    # ------------------------------------------------------------ история
    def comments(self, ticket_id: int) -> list[Comment]:
        return self.db.query(Comment, "SELECT * FROM comments WHERE ticket_id = ? ORDER BY id", (ticket_id,))

    def attachments(self, ticket_id: int) -> list[Attachment]:
        return self.db.query(
            Attachment,
            "SELECT id, ticket_id, author_id, filename, content_type, size, created_at "
            "FROM attachments WHERE ticket_id = ? ORDER BY id",
            (ticket_id,),
        )

    def status_events(self, ticket_id: int) -> list[StatusEvent]:
        return self.db.query(StatusEvent, "SELECT * FROM status_events WHERE ticket_id = ? ORDER BY id", (ticket_id,))

    def assignments(self, ticket_id: int) -> list[Assignment]:
        return self.db.query(Assignment, "SELECT * FROM assignments WHERE ticket_id = ? ORDER BY id", (ticket_id,))

    def escalations(self, ticket_id: int) -> list[Escalation]:
        return self.db.query(Escalation, "SELECT * FROM escalations WHERE ticket_id = ? ORDER BY id", (ticket_id,))

    def timeline(self, ticket_id: int) -> list[TimelineEntry]:
        """Единая хронология: статусы, назначения, эскалации, правки, комментарии и вложения."""
        acc = self.hd.accounts
        group_name = lambda gid: "—" if gid is None else acc.get_group(gid).name
        user_name = lambda uid: "—" if uid is None else acc.get_user(uid).full_name
        entries: list[TimelineEntry] = []

        for e in self.status_events(ticket_id):
            before = e.from_status.label if e.from_status else "—"
            entries.append(TimelineEntry(
                e.created_at, "Статус", acc.name_of(e.author_id), f"{before} → {e.to_status.label}. Причина: {e.reason}"))
        for a in self.assignments(ticket_id):
            entries.append(TimelineEntry(
                a.created_at, "Назначение", acc.name_of(a.author_id),
                f"Группа «{group_name(a.group_id)}», исполнитель: {user_name(a.assignee_id)}. Причина: {a.reason}"))
        for x in self.escalations(ticket_id):
            entries.append(TimelineEntry(
                x.created_at, "Эскалация", acc.name_of(x.author_id),
                f"{x.kind.label}: {user_name(x.from_assignee_id)} → {user_name(x.to_assignee_id)} "
                f"(группа «{group_name(x.to_group_id)}»). Причина: {x.reason}"))
        for c in self.db.query(FieldChange, "SELECT * FROM field_changes WHERE ticket_id = ?", (ticket_id,)):
            entries.append(TimelineEntry(
                c.created_at, "Изменение", acc.name_of(c.author_id),
                f"{c.field}: {c.old_value} → {c.new_value}. Причина: {c.reason}"))
        for c in self.comments(ticket_id):
            entries.append(TimelineEntry(c.created_at, "Комментарий", acc.name_of(c.author_id), c.text))
        for f in self.attachments(ticket_id):
            entries.append(TimelineEntry(f.created_at, "Вложение", acc.name_of(f.author_id), f"{f.filename} ({f.size} байт)"))
        return sorted(entries, key=lambda entry: entry.at)
