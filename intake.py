"""Канал приема обращений: форма заявки, вложения, просмотр заявителем своих обращений."""
from __future__ import annotations

import mimetypes
import os
from typing import Iterable, Optional

from .base import Subsystem
from .enums import Impact, Status, Urgency
from .errors import ValidationError
from .models import Attachment, Ticket
from .rules import ALLOWED_EXTENSIONS, MAX_ATTACHMENT_BYTES, calculate_priority


class IntakeChannel(Subsystem):
    def create_ticket(
        self,
        requester_id: int,
        title: str,
        description: str,
        category_id: int,
        impact: Impact,
        urgency: Urgency,
        attachments: Iterable[tuple[str, bytes]] = (),
    ) -> Ticket:
        """Создание заявки: номер, время, подтверждение приёма и (если есть правило) автоназначение."""
        requester = self.hd.accounts.require(requester_id)       # создавать может любой активный пользователь
        title, description = (title or "").strip(), (description or "").strip()
        if not 3 <= len(title) <= 200:
            raise ValidationError("Тема заявки: от 3 до 200 символов")
        if not description:
            raise ValidationError("Опишите проблему")
        category = self.hd.routing.get_category(category_id)
        if not category.active:
            raise ValidationError("Категория отключена")
        impact, urgency = Impact(impact), Urgency(urgency)
        priority = calculate_priority(impact, urgency)

        with self.db.transaction():
            created = self.now()
            response_due, resolve_due = self.hd.sla.deadlines(priority, created)
            ticket_id = self.db.insert(
                "tickets", title=title, description=description, category_id=category.id,
                impact=impact, urgency=urgency, priority=priority, status=Status.NEW,
                requester_id=requester.id, created_at=created, updated_at=created, sla_start_at=created,
                response_due_at=response_due, resolve_due_at=resolve_due,
            )
            number = f"HD-{ticket_id:06d}"
            self.db.update("tickets", ticket_id, number=number)
            self.db.insert(
                "status_events", ticket_id=ticket_id, from_status=None, to_status=Status.NEW,
                author_id=requester.id, reason="Заявка создана", created_at=self.now(),
            )
            for filename, data in attachments:
                self._store_attachment(ticket_id, requester.id, filename, data)

            ticket = self.hd.store.get(ticket_id)
            self.hd.notifier.notify(
                requester.id, ticket, "Заявка принята",
                f"Номер вашей заявки {number}. Приоритет: {priority.label}. "
                f"Плановый срок решения: {resolve_due:%d.%m.%Y %H:%M} UTC.",
            )
            if self.hd.settings.get_bool("auto_assign"):
                self.hd.routing.auto_assign(ticket_id)
        return self.hd.store.get(ticket_id)

    # ------------------------------------------------------------ вложения
    def add_attachment(self, actor_id: int, ticket_id: int, filename: str, data: bytes) -> Attachment:
        user, ticket = self.hd.accounts.visible_ticket(actor_id, ticket_id)
        if ticket.status is Status.CLOSED:
            raise ValidationError("К закрытой заявке нельзя добавлять файлы")
        with self.db.transaction():
            attachment = self._store_attachment(ticket.id, user.id, filename, data)
        return attachment

    def _store_attachment(self, ticket_id: int, author_id: int, filename: str, data: bytes) -> Attachment:
        filename = os.path.basename((filename or "").replace("\\", "/")).strip()
        extension = os.path.splitext(filename)[1].lower()
        if not filename or extension not in ALLOWED_EXTENSIONS:
            raise ValidationError(f"Недопустимый тип файла. Разрешены: {', '.join(sorted(ALLOWED_EXTENSIONS))}")
        if not data:
            raise ValidationError("Файл пустой")
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ValidationError(f"Файл больше {MAX_ATTACHMENT_BYTES // (1024 * 1024)} МБ")
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        attachment_id = self.db.insert(
            "attachments", ticket_id=ticket_id, author_id=author_id, filename=filename,
            content_type=content_type, size=len(data), data=data, created_at=self.now(),
        )
        return self.db.one(Attachment, "SELECT * FROM attachments WHERE id = ?", (attachment_id,))

    def get_attachment(self, actor_id: int, attachment_id: int) -> Attachment:
        attachment = self.db.one(Attachment, "SELECT * FROM attachments WHERE id = ?", (attachment_id,))
        if attachment is None:
            raise ValidationError("Файл не найден")
        self.hd.accounts.visible_ticket(actor_id, attachment.ticket_id)     # проверка доступа к заявке
        return attachment

    # ------------------------------------------------------------ мои обращения
    def my_tickets(self, requester_id: int, status: Optional[Status] = None) -> list[Ticket]:
        """Заявитель видит только свои заявки."""
        self.hd.accounts.require(requester_id)
        if status is None:
            return self.hd.store.find("requester_id = ?", (requester_id,))
        return self.hd.store.find("requester_id = ? AND status = ?", (requester_id, status))
