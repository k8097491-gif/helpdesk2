"""Уведомления: письмо или push, в зависимости от настройки пользователя.

Запись об уведомлении сохраняется в той же транзакции, что и действие, а сама
отправка выполняется ПОСЛЕ успешного коммита — при откате операции писем не будет.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional, Protocol

from .base import Subsystem
from .enums import Channel
from .models import Notification, Ticket, User

log = logging.getLogger(__name__)


class DeliveryChannel(Protocol):
    """Интерфейс канала доставки. Реальные SMTP/push-клиенты реализуют метод send()."""

    def send(self, user: User, subject: str, body: str) -> None: ...


class MemoryChannel:
    """Канал-заглушка: складывает «отправленные» сообщения в список (для демо и тестов)."""

    def __init__(self, name: str):
        self.name = name
        self.sent: list[tuple[str, str, str]] = []

    def send(self, user: User, subject: str, body: str) -> None:
        self.sent.append((user.email, subject, body))


class Notifier(Subsystem):
    def __init__(self, hd, channels: dict[Channel, DeliveryChannel]):
        super().__init__(hd)
        self.channels = channels

    def notify(self, user_id: Optional[int], ticket: Optional[Ticket], subject: str, body: str) -> None:
        if user_id is None:
            return
        user = self.hd.accounts.get_user(user_id)
        if not user.active:
            return
        if ticket is not None:
            subject = f"[{ticket.number}] {subject}"
        note_id = self.db.insert(
            "notifications",
            user_id=user.id,
            ticket_id=ticket.id if ticket else None,
            channel=user.notify_channel,
            subject=subject,
            body=body,
            created_at=self.now(),
            delivered=False,
        )
        self.db.after_commit(lambda: self._deliver(note_id))

    def notify_many(self, user_ids: Iterable[Optional[int]], ticket: Ticket, subject: str, body: str) -> None:
        for user_id in dict.fromkeys(uid for uid in user_ids if uid is not None):  # без дублей
            self.notify(user_id, ticket, subject, body)

    def _deliver(self, note_id: int) -> None:
        note = self.db.one(Notification, "SELECT * FROM notifications WHERE id = ?", (note_id,))
        user = self.hd.accounts.get_user(note.user_id)
        channel = self.channels.get(note.channel)
        try:
            channel.send(user, note.subject, note.body)
        except Exception:  # сбой доставки не должен ломать бизнес-операцию
            log.exception("Не удалось доставить уведомление %s", note_id)
            return
        with self.db.transaction():
            self.db.update("notifications", note_id, delivered=True)

    def retry_undelivered(self) -> int:
        """Повторная отправка недоставленных уведомлений (можно вызывать по расписанию)."""
        ids = [r["id"] for r in self.db.execute("SELECT id FROM notifications WHERE delivered = 0").fetchall()]
        for note_id in ids:
            self._deliver(note_id)
        return len(ids)

    def inbox(self, user_id: int) -> list[Notification]:
        return self.db.query(
            Notification, "SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC", (user_id,)
        )
