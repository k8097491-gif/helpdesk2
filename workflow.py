"""Реестр и рабочий процесс: карточка заявки, статусы, комментарии, решение, закрытие, повторное открытие."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from .base import Subsystem
from .enums import OPEN_STATUSES, Role, Status
from .errors import PermissionDenied, ValidationError
from .models import Comment, Ticket, TimelineEntry
from .rules import MIN_RESOLUTION_LENGTH


class TicketWorkflow(Subsystem):
    # ------------------------------------------------------------ просмотр
    def get_ticket(self, actor_id: int, ticket_id: int) -> Ticket:
        """Карточка заявки (с проверкой прав доступа)."""
        return self.hd.accounts.visible_ticket(actor_id, ticket_id)[1]

    def timeline(self, actor_id: int, ticket_id: int) -> list[TimelineEntry]:
        self.hd.accounts.visible_ticket(actor_id, ticket_id)
        return self.hd.store.timeline(ticket_id)

    def my_queue(self, actor_id: int) -> list[Ticket]:
        """Очередь специалиста: открытые заявки, назначенные именно ему."""
        self.hd.accounts.require(actor_id, Role.SPECIALIST, Role.MANAGER)
        marks = ", ".join("?" for _ in OPEN_STATUSES)
        return self.hd.store.find(f"assignee_id = ? AND status IN ({marks})", (actor_id, *OPEN_STATUSES))

    def unassigned_queue(self, actor_id: int) -> list[Ticket]:
        """Очередь диспетчера: заявки без исполнителя."""
        self.hd.accounts.require(actor_id, Role.DISPATCHER, Role.MANAGER)
        marks = ", ".join("?" for _ in OPEN_STATUSES)
        return self.hd.store.find(f"assignee_id IS NULL AND status IN ({marks})", OPEN_STATUSES)

    def all_tickets(self, actor_id: int, status: Optional[Status] = None, group_id: Optional[int] = None) -> list[Ticket]:
        self.hd.accounts.require(actor_id, Role.DISPATCHER, Role.MANAGER)
        where, params = ["1 = 1"], []
        if status is not None:
            where.append("status = ?")
            params.append(status)
        if group_id is not None:
            where.append("group_id = ?")
            params.append(group_id)
        return self.hd.store.find(" AND ".join(where), params)

    # ------------------------------------------------------------ комментарии
    def add_comment(self, actor_id: int, ticket_id: int, text: str) -> Comment:
        """Комментарий заявителя или исполнителя. Ответ заявителя возвращает «Ожидает» в «В работе»."""
        user, ticket = self.hd.accounts.visible_ticket(actor_id, ticket_id)
        text = (text or "").strip()
        if not text:
            raise ValidationError("Комментарий не может быть пустым")
        if ticket.status is Status.CLOSED:
            raise ValidationError("Заявка закрыта: комментарии недоступны")

        with self.db.transaction():
            comment_id = self.db.insert(
                "comments", ticket_id=ticket.id, author_id=user.id, text=text, created_at=self.now())
            if user.id == ticket.requester_id:
                if ticket.status is Status.WAITING:
                    self.hd.store.change_status(ticket, Status.IN_PROGRESS, user.id, "Заявитель ответил на запрос сведений")
                self.hd.notifier.notify(ticket.assignee_id, ticket, "Комментарий заявителя", text)
            else:
                if ticket.first_response_at is None:
                    ticket.first_response_at = self.now()
                    self.hd.store.save(ticket)
                self.hd.notifier.notify(ticket.requester_id, ticket, "Новый комментарий по заявке", text)
        return self.db.one(Comment, "SELECT * FROM comments WHERE id = ?", (comment_id,))

    # ------------------------------------------------------------ работа исполнителя
    def _assignee_ticket(self, actor_id: int, ticket_id: int) -> tuple:
        actor = self.hd.accounts.require(actor_id, Role.SPECIALIST, Role.MANAGER)
        ticket = self.hd.store.get(ticket_id)
        if ticket.assignee_id != actor.id:
            raise PermissionDenied("Действие доступно только текущему исполнителю заявки")
        return actor, ticket

    def start_work(self, actor_id: int, ticket_id: int, note: str = "") -> Ticket:
        """Исполнитель берёт заявку в работу (из «Назначена», «Эскалирована», «Повторно открыта», «Ожидает»)."""
        actor, ticket = self._assignee_ticket(actor_id, ticket_id)
        with self.db.transaction():
            self.hd.store.change_status(ticket, Status.IN_PROGRESS, actor.id, note.strip() or "Исполнитель приступил к работе")
            self.hd.notifier.notify(ticket.requester_id, ticket, "Заявка в работе", f"Исполнитель: {actor.full_name}.")
        return self.hd.store.get(ticket_id)

    def request_info(self, actor_id: int, ticket_id: int, question: str) -> Ticket:
        """Нужны сведения от заявителя: статус «Ожидает» (срок решения на это время останавливается)."""
        actor, ticket = self._assignee_ticket(actor_id, ticket_id)
        question = (question or "").strip()
        if not question:
            raise ValidationError("Сформулируйте вопрос заявителю")
        with self.db.transaction():
            self.hd.store.change_status(ticket, Status.WAITING, actor.id, f"Запрошены сведения: {question}")
            self.db.insert("comments", ticket_id=ticket.id, author_id=actor.id, text=question, created_at=self.now())
            self.hd.notifier.notify(ticket.requester_id, ticket, "Нужны дополнительные сведения", question)
        return self.hd.store.get(ticket_id)

    def resolve(self, actor_id: int, ticket_id: int, resolution: str) -> Ticket:
        """Исполнитель фиксирует устранённую причину и итог; заявитель должен подтвердить решение."""
        actor, ticket = self._assignee_ticket(actor_id, ticket_id)
        resolution = (resolution or "").strip()
        if len(resolution) < MIN_RESOLUTION_LENGTH:
            raise ValidationError(f"Опишите решение подробнее (не менее {MIN_RESOLUTION_LENGTH} символов)")
        with self.db.transaction():
            ticket.resolution_text = resolution
            ticket.resolved_at = self.now()
            self.hd.store.change_status(ticket, Status.RESOLVED, actor.id, "Решение зафиксировано исполнителем")
            days = self.hd.settings.get_int("auto_close_days")
            self.hd.notifier.notify(
                ticket.requester_id, ticket, "Заявка решена — подтвердите результат",
                f"Решение: {resolution}\nЕсли проблема осталась, откройте заявку повторно. "
                f"Без ответа заявка закроется автоматически через {days} дн.",
            )
        return self.hd.store.get(ticket_id)

    # ------------------------------------------------------------ закрытие и повторное открытие
    def _requester_ticket(self, actor_id: int, ticket_id: int):
        actor = self.hd.accounts.require(actor_id)
        ticket = self.hd.store.get(ticket_id)
        if ticket.requester_id != actor.id:
            raise PermissionDenied("Решение подтверждает или отклоняет только заявитель")
        return actor, ticket

    def confirm_resolution(self, actor_id: int, ticket_id: int) -> Ticket:
        """Заявитель подтверждает решение -> «Закрыта»; заявитель получает опрос."""
        actor, ticket = self._requester_ticket(actor_id, ticket_id)
        with self.db.transaction():
            self.hd.store.change_status(ticket, Status.CLOSED, actor.id, "Решение подтверждено заявителем")
            self._invite_to_rate(ticket)
        return self.hd.store.get(ticket_id)

    def reopen(self, actor_id: int, ticket_id: int, reason: str) -> Ticket:
        """Заявитель сообщает, что проблема осталась -> «Повторно открыта», сроки отсчитываются заново."""
        actor, ticket = self._requester_ticket(actor_id, ticket_id)
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("Опишите, что именно не работает")
        with self.db.transaction():
            self.hd.store.change_status(ticket, Status.REOPENED, actor.id, f"Проблема осталась: {reason}")
            ticket.reopen_count += 1
            ticket.resolved_at = None
            ticket.sla_start_at = self.now()
            ticket.waited_seconds = 0
            ticket.risk_notified = ticket.breach_notified = False
            self.hd.sla.recalculate(ticket)
            self.hd.store.save(ticket)
            recipients = [ticket.assignee_id] + ([m.id for m in self.hd.accounts.managers_for(ticket.group_id)]
                                                 if ticket.reopen_count > 1 else [])
            self.hd.notifier.notify_many(recipients, ticket, "Заявка открыта повторно", f"Заявитель сообщает: {reason}")
        return self.hd.store.get(ticket_id)

    def close_expired(self) -> list[Ticket]:
        """Автозакрытие решённых заявок, на которые заявитель не ответил в настроенный срок.

        Вызывать периодически (планировщик/cron).
        """
        limit = timedelta(days=self.hd.settings.get_int("auto_close_days"))
        now = self.now()
        closed = []
        with self.db.transaction():
            for ticket in self.hd.store.find("status = ?", (Status.RESOLVED,)):
                if ticket.resolved_at and now - ticket.resolved_at >= limit:
                    self.hd.store.change_status(
                        ticket, Status.CLOSED, None,
                        f"Автозакрытие: заявитель не ответил в течение {limit.days} дн.")
                    self._invite_to_rate(ticket)
                    closed.append(ticket)
        return closed

    def _invite_to_rate(self, ticket: Ticket) -> None:
        self.hd.notifier.notify(
            ticket.requester_id, ticket, "Заявка закрыта — оцените поддержку",
            "Оцените качество обслуживания от 1 до 5 (комментарий — по желанию).",
        )
