"""Классификация и маршрутизация: категория, приоритет, выбор группы и специалиста."""
from __future__ import annotations

from typing import Optional

from .base import Subsystem
from .enums import Impact, LOAD_STATUSES, OPEN_STATUSES, Priority, Role, Status, Urgency
from .errors import NotFound, ValidationError
from .models import Category, Ticket
from .rules import calculate_priority


class Routing(Subsystem):
    # ------------------------------------------------------------ правила
    def get_category(self, category_id: int) -> Category:
        category = self.db.one(Category, "SELECT * FROM categories WHERE id = ?", (category_id,))
        if category is None:
            raise NotFound(f"Категория {category_id} не найдена")
        return category

    def suggest_group(self, category_id: int) -> Optional[int]:
        """Правило маршрутизации: категория -> группа."""
        group_id = self.get_category(category_id).group_id
        if group_id is None or not self.hd.accounts.get_group(group_id).active:
            return None
        return group_id

    def workload(self, user_id: int) -> int:
        """Текущая нагрузка: сколько открытых заявок закреплено за исполнителем."""
        marks = ", ".join("?" for _ in LOAD_STATUSES)
        return self.db.scalar(
            f"SELECT COUNT(*) FROM tickets WHERE assignee_id = ? AND status IN ({marks})", (user_id, *LOAD_STATUSES)
        )

    def pick_assignee(self, group_id: int) -> Optional[int]:
        """Специалист группы с минимальной нагрузкой (при равенстве — с меньшим id)."""
        specialists = self.hd.accounts.members(group_id, roles=(Role.SPECIALIST,))
        if not specialists:
            return None
        return min(specialists, key=lambda u: (self.workload(u.id), u.id)).id

    # ------------------------------------------------------------ назначение
    def auto_assign(self, ticket_id: int) -> bool:
        """Назначение «по правилу»: группа по категории, специалист по нагрузке. True — назначено."""
        ticket = self.hd.store.get(ticket_id)
        if ticket.status is not Status.NEW:
            return False
        group_id = self.suggest_group(ticket.category_id)
        if group_id is None:
            return False                       # правила нет — заявку разберёт диспетчер
        assignee_id = self.pick_assignee(group_id)
        with self.db.transaction():
            ticket.group_id = group_id
            if assignee_id is None:            # группа определена, но свободных специалистов нет
                self.hd.store.save(ticket)
                self.hd.store.log_assignment(ticket, None, "Правило маршрутизации: нет доступных специалистов в группе")
                return False
            self._apply_assignment(ticket, group_id, assignee_id, None, "Автоназначение по правилу маршрутизации")
        return True

    def assign(self, actor_id: int, ticket_id: int, group_id: int,
               assignee_id: Optional[int] = None, reason: str = "") -> Ticket:
        """Диспетчер или руководитель назначает/переназначает группу и исполнителя.

        Если исполнитель не указан, он выбирается по нагрузке внутри группы.
        """
        actor = self.hd.accounts.require(actor_id, Role.DISPATCHER, Role.MANAGER)
        ticket = self.hd.store.get(ticket_id)
        if ticket.status not in OPEN_STATUSES:
            raise ValidationError("Нельзя назначать решённую или закрытую заявку")
        group = self.hd.accounts.get_group(group_id)
        if not group.active:
            raise ValidationError("Группа отключена")
        if assignee_id is None:
            assignee_id = self.pick_assignee(group_id)
            if assignee_id is None:
                raise ValidationError("В группе нет доступных специалистов")
        assignee = self.hd.accounts.get_user(assignee_id)
        if not assignee.active or assignee.role not in (Role.SPECIALIST, Role.MANAGER):
            raise ValidationError("Исполнителем может быть только действующий специалист или руководитель")
        if assignee.role is Role.SPECIALIST and assignee.group_id != group_id:
            raise ValidationError("Специалист не входит в выбранную группу")

        with self.db.transaction():
            self._apply_assignment(ticket, group_id, assignee_id, actor.id, reason or "Назначено вручную")
        return self.hd.store.get(ticket_id)

    def _apply_assignment(self, ticket: Ticket, group_id: int, assignee_id: int,
                          author_id: Optional[int], reason: str) -> None:
        """Общая часть ручного и автоматического назначения: поля, статус, история, уведомления."""
        previous_assignee = ticket.assignee_id
        first_assignment = ticket.status is Status.NEW
        ticket.group_id, ticket.assignee_id = group_id, assignee_id
        if ticket.status is not Status.ASSIGNED:
            self.hd.store.change_status(ticket, Status.ASSIGNED, author_id, reason)
        else:
            self.hd.store.save(ticket)
        self.hd.store.log_assignment(ticket, author_id, reason)
        self.hd.notifier.notify(
            assignee_id, ticket, "Вам назначена заявка",
            f"«{ticket.title}». Приоритет: {ticket.priority.label}. Срок решения: {ticket.resolve_due_at:%d.%m.%Y %H:%M} UTC.",
        )
        if first_assignment:
            self.hd.notifier.notify(
                ticket.requester_id, ticket, "Заявка передана специалисту",
                f"Ответственный: {self.hd.accounts.name_of(assignee_id)}.",
            )
        if previous_assignee and previous_assignee != assignee_id:
            self.hd.notifier.notify(
                previous_assignee, ticket, "Заявка переназначена",
                f"Новый исполнитель: {self.hd.accounts.name_of(assignee_id)}.",
            )

    # ------------------------------------------------------------ классификация
    def reclassify(self, actor_id: int, ticket_id: int, reason: str, *,
                   category_id: Optional[int] = None, impact: Optional[Impact] = None,
                   urgency: Optional[Urgency] = None, priority: Optional[Priority] = None) -> Ticket:
        """Диспетчер/руководитель проверяет и исправляет категорию и приоритет.

        Если заданы влияние/срочность — приоритет пересчитывается по матрице;
        явно указанный priority перекрывает расчёт. Каждое изменение пишется в историю.
        """
        actor = self.hd.accounts.require(actor_id, Role.DISPATCHER, Role.MANAGER)
        reason = (reason or "").strip()
        if not reason:
            raise ValidationError("Укажите причину изменения")
        ticket = self.hd.store.get(ticket_id)
        if ticket.status not in OPEN_STATUSES:
            raise ValidationError("Нельзя менять классификацию решённой или закрытой заявки")

        with self.db.transaction():
            log = lambda field, old, new: self.hd.store.log_field_change(ticket.id, field, old, new, actor.id, reason)
            if category_id is not None and category_id != ticket.category_id:
                new_category = self.get_category(category_id)
                if not new_category.active:
                    raise ValidationError("Категория отключена")
                log("Категория", self.get_category(ticket.category_id).name, new_category.name)
                ticket.category_id = category_id
            if impact is not None and impact != ticket.impact:
                log("Влияние", ticket.impact, impact)
                ticket.impact = impact
            if urgency is not None and urgency != ticket.urgency:
                log("Срочность", ticket.urgency, urgency)
                ticket.urgency = urgency
            new_priority = priority or calculate_priority(ticket.impact, ticket.urgency)
            if new_priority != ticket.priority:
                log("Приоритет", ticket.priority, new_priority)
                ticket.priority = new_priority
                self.hd.sla.recalculate(ticket)
            self.hd.store.save(ticket)
        return self.hd.store.get(ticket_id)
