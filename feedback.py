"""Обратная связь: оценка закрытой заявки от 1 до 5 с необязательным комментарием."""
from __future__ import annotations

from typing import Optional

from .base import Subsystem
from .enums import Status
from .errors import PermissionDenied, ValidationError
from .models import Feedback, Ticket


class FeedbackService(Subsystem):
    def rate(self, actor_id: int, ticket_id: int, rating: int, comment: Optional[str] = None) -> Feedback:
        actor = self.hd.accounts.require(actor_id)
        ticket = self.hd.store.get(ticket_id)
        if ticket.requester_id != actor.id:
            raise PermissionDenied("Оценить заявку может только её заявитель")
        if ticket.status is not Status.CLOSED:
            raise ValidationError("Оценка доступна только после закрытия заявки")
        if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
            raise ValidationError("Оценка — целое число от 1 до 5")
        if self.get_for_ticket(ticket_id) is not None:
            raise ValidationError("Оценка по этой заявке уже сохранена")
        comment = (comment or "").strip() or None
        if comment and len(comment) > 1000:
            raise ValidationError("Комментарий — не более 1000 символов")
        with self.db.transaction():
            feedback_id = self.db.insert(
                "feedback", ticket_id=ticket_id, requester_id=actor.id, rating=rating,
                comment=comment, created_at=self.now(),
            )
        return self.db.one(Feedback, "SELECT * FROM feedback WHERE id = ?", (feedback_id,))

    def get_for_ticket(self, ticket_id: int) -> Optional[Feedback]:
        return self.db.one(Feedback, "SELECT * FROM feedback WHERE ticket_id = ?", (ticket_id,))

    def pending_surveys(self, actor_id: int) -> list[Ticket]:
        """Закрытые заявки пользователя, по которым он ещё не оставил оценку."""
        self.hd.accounts.require(actor_id)
        return self.hd.store.find(
            "requester_id = ? AND status = ? AND id NOT IN (SELECT ticket_id FROM feedback)",
            (actor_id, Status.CLOSED),
        )
