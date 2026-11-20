"""Сроки и эскалация: целевые сроки, предупреждения о просрочке, передача руководителю/в другую группу."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .base import Subsystem
from .enums import EscalationKind, OPEN_STATUSES, Priority, Role, Status
from .errors import PermissionDenied, ValidationError
from .models import SlaPolicy, Ticket, User


class SlaControl(Subsystem):
    # ------------------------------------------------------------ нормативы
    def policy_for(self, priority: Priority) -> SlaPolicy:
        return self.db.one(SlaPolicy, "SELECT * FROM sla_policies WHERE priority = ?", (priority,))

    def deadlines(self, priority: Priority, start: datetime, waited_seconds: int = 0) -> tuple[datetime, datetime]:
        """(срок отклика, срок решения). Время «ожидания заявителя» в срок решения не входит."""
        policy = self.policy_for(priority)
        return (
            start + timedelta(minutes=policy.response_minutes),
            start + timedelta(minutes=policy.resolve_minutes, seconds=waited_seconds),
        )

    def recalculate(self, ticket: Ticket) -> None:
        """Пересчитать сроки (при смене приоритета или повторном открытии)."""
        ticket.response_due_at, ticket.resolve_due_at = self.deadlines(
            ticket.priority, ticket.sla_start_at, ticket.waited_seconds
        )

    def on_status_change(self, ticket: Ticket, old: Status, new: Status, now: datetime) -> None:
        """Пока заявка «Ожидает» ответа заявителя, срок решения не идёт — сдвигаем его на время ожидания."""
        if new is Status.WAITING:
            ticket.waiting_since = now
        elif old is Status.WAITING and ticket.waiting_since is not None:
            waited = round((now - ticket.waiting_since).total_seconds())
            ticket.waited_seconds += waited
            ticket.resolve_due_at += timedelta(seconds=waited)
            ticket.waiting_since = None

    # ------------------------------------------------------------ контроль
    def is_overdue(self, ticket: Ticket) -> bool:
        """Просрочена ли заявка: открытая — срок прошёл; решённая — решена позже срока."""
        if ticket.status in OPEN_STATUSES:
            return ticket.waiting_since is None and self.now() > ticket.resolve_due_at
        reference = ticket.resolved_at or ticket.closed_at
        return reference is not None and reference > ticket.resolve_due_at

    def overdue_tickets(self) -> list[Ticket]:
        return [t for t in self.hd.store.open_tickets() if self.is_overdue(t)]

    def check_deadlines(self) -> list[tuple[Ticket, str]]:
        """Периодическая проверка (вызывать по расписанию).

        Предупреждает о риске просрочки ("risk") и о самой просрочке ("breach"),
        каждое — один раз за цикл. Возвращает список сработавших предупреждений.
        """
        now = self.now()
        risk_ratio = self.hd.settings.get_float("risk_ratio")
        alerts: list[tuple[Ticket, str]] = []
        with self.db.transaction():
            for ticket in self.hd.store.open_tickets():
                if ticket.waiting_since is not None:       # часы стоят
                    continue
                window = (ticket.resolve_due_at - ticket.sla_start_at).total_seconds() - ticket.waited_seconds
                elapsed = (now - ticket.sla_start_at).total_seconds() - ticket.waited_seconds
                if now > ticket.resolve_due_at and not ticket.breach_notified:
                    ticket.breach_notified = ticket.risk_notified = True
                    self._alert(ticket, "Срок решения заявки нарушен", "Целевой срок решения истёк.")
                    alerts.append((ticket, "breach"))
                elif window > 0 and elapsed / window >= risk_ratio and not ticket.risk_notified and not ticket.breach_notified:
                    ticket.risk_notified = True
                    self._alert(
                        ticket, "Риск просрочки",
                        f"Израсходовано {int(elapsed / window * 100)}% срока. Срок решения: {ticket.resolve_due_at:%d.%m.%Y %H:%M} UTC.",
                    )
                    alerts.append((ticket, "risk"))
                else:
                    continue
                self.hd.store.save(ticket)
        return alerts

    def _alert(self, ticket: Ticket, subject: str, body: str) -> None:
        recipients = [ticket.assignee_id] + [m.id for m in self.hd.accounts.managers_for(ticket.group_id)]
        if ticket.assignee_id is None:             # исполнителя ещё нет — предупреждаем и диспетчеров
            dispatchers = self.db.query(User, "SELECT * FROM users WHERE role = ? AND active = 1", (Role.DISPATCHER,))
            recipients += [d.id for d in dispatchers]
        self.hd.notifier.notify_many(recipients, ticket, subject, body)

    # ------------------------------------------------------------ эскалация
    def escalate(self, actor_id: int, ticket_id: int, reason: str, to_group_id: Optional[int] = None) -> Ticket:
        """Исполнитель передаёт заявку руководителю (по умолчанию) либо в другую группу.

        Причина обязательна; прежние назначения сохраняются в истории.
        """
        actor = self.hd.accounts.require(actor_id, Role.SPECIALIST, Role.MANAGER)
        ticket = self.hd.store.get(ticket_id)
        if ticket.assignee_id != actor.id:
            raise PermissionDenied("Эскалировать может только текущий исполнитель заявки")
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("Укажите причину эскалации")

        with self.db.transaction():
            if to_group_id is None:
                kind = EscalationKind.MANAGER
                managers = [m for m in self.hd.accounts.managers_for(ticket.group_id) if m.id != actor.id]
                if not managers:
                    raise ValidationError("Нет доступного руководителя для эскалации")
                new_group_id = ticket.group_id
                new_assignee_id = min(managers, key=lambda m: (self.hd.routing.workload(m.id), m.id)).id
            else:
                kind = EscalationKind.GROUP
                target = self.hd.accounts.get_group(to_group_id)
                if not target.active or to_group_id == ticket.group_id:
                    raise ValidationError("Выберите другую действующую группу")
                new_group_id = to_group_id
                new_assignee_id = self.hd.routing.pick_assignee(to_group_id)   # может быть None

            self.hd.store.change_status(ticket, Status.ESCALATED, actor.id, f"Эскалация: {reason}")
            self.db.insert(
                "escalations",
                ticket_id=ticket.id, kind=kind, author_id=actor.id, reason=reason,
                from_group_id=ticket.group_id, from_assignee_id=ticket.assignee_id,
                to_group_id=new_group_id, to_assignee_id=new_assignee_id, created_at=self.now(),
            )
            ticket.group_id, ticket.assignee_id = new_group_id, new_assignee_id
            self.hd.store.save(ticket)
            self.hd.store.log_assignment(ticket, actor.id, f"Эскалация: {reason}")

            self.hd.notifier.notify(
                new_assignee_id, ticket, "Вам передана эскалированная заявка",
                f"«{ticket.title}». Причина эскалации: {reason}",
            )
            if new_assignee_id is None:
                self.hd.notifier.notify_many(
                    [m.id for m in self.hd.accounts.managers_for(new_group_id)], ticket,
                    "Эскалированная заявка без исполнителя", f"Назначьте исполнителя. Причина: {reason}",
                )
            self.hd.notifier.notify(
                ticket.requester_id, ticket, "Заявка передана на следующий уровень поддержки",
                "Ваша заявка эскалирована и находится под контролем руководителя.",
            )
        return self.hd.store.get(ticket_id)
