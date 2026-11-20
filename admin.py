"""Администрирование: категории, правила маршрутизации, роли, нормативы обслуживания, настройки."""
from __future__ import annotations

from typing import Optional

from .base import Subsystem
from .enums import Channel, Priority, Role
from .errors import ValidationError
from .models import Category, Group, SlaPolicy, User


class Administration(Subsystem):
    def create_group(self, actor_id: int, name: str) -> Group:
        self.hd.accounts.require(actor_id, Role.ADMIN)
        return self.hd.accounts.create_group(name)

    def create_user(self, actor_id: int, login: str, full_name: str, email: str,
                    role: Role = Role.REQUESTER, group_id: Optional[int] = None,
                    notify_channel: Channel = Channel.EMAIL) -> User:
        self.hd.accounts.require(actor_id, Role.ADMIN)
        return self.hd.accounts.create_user(login, full_name, email, role, group_id, notify_channel)

    def set_role(self, actor_id: int, user_id: int, role: Role, group_id: Optional[int] = None) -> User:
        """Роль доступа. Специалисту обязательно нужна группа."""
        self.hd.accounts.require(actor_id, Role.ADMIN)
        user = self.hd.accounts.get_user(user_id)
        if role is Role.SPECIALIST and group_id is None and user.group_id is None:
            raise ValidationError("Специалист должен входить в группу поддержки")
        if group_id is not None:
            self.hd.accounts.get_group(group_id)
        if user.role in (Role.SPECIALIST, Role.MANAGER) and role is not user.role:
            self._ensure_no_open_tickets(user)
        with self.db.transaction():
            self.db.update("users", user_id, role=role, group_id=group_id if group_id is not None else user.group_id)
        return self.hd.accounts.get_user(user_id)

    def deactivate_user(self, actor_id: int, user_id: int) -> User:
        self.hd.accounts.require(actor_id, Role.ADMIN)
        user = self.hd.accounts.get_user(user_id)
        self._ensure_no_open_tickets(user)
        with self.db.transaction():
            self.db.update("users", user_id, active=False)
        return self.hd.accounts.get_user(user_id)

    def _ensure_no_open_tickets(self, user: User) -> None:
        count = self.hd.routing.workload(user.id)
        if count:
            raise ValidationError(f"За пользователем закреплено заявок: {count}. Сначала переназначьте их")

    def create_category(self, actor_id: int, name: str, group_id: Optional[int] = None) -> Category:
        """Категория и (необязательно) правило маршрутизации: какая группа её обрабатывает."""
        self.hd.accounts.require(actor_id, Role.ADMIN)
        name = (name or "").strip()
        if not name:
            raise ValidationError("Название категории не может быть пустым")
        if group_id is not None:
            self.hd.accounts.get_group(group_id)
        with self.db.transaction():
            category_id = self.db.insert("categories", name=name, group_id=group_id, active=True)
        return self.hd.routing.get_category(category_id)

    def set_routing(self, actor_id: int, category_id: int, group_id: Optional[int]) -> Category:
        """Правило маршрутизации: категория -> группа (None — только вручную через диспетчера)."""
        self.hd.accounts.require(actor_id, Role.ADMIN)
        self.hd.routing.get_category(category_id)
        if group_id is not None:
            self.hd.accounts.get_group(group_id)
        with self.db.transaction():
            self.db.update("categories", category_id, group_id=group_id)
        return self.hd.routing.get_category(category_id)

    def set_sla(self, actor_id: int, priority: Priority, response_minutes: int, resolve_minutes: int) -> SlaPolicy:
        """Норматив обслуживания. Действует на новые заявки и при пересчёте сроков."""
        self.hd.accounts.require(actor_id, Role.ADMIN)
        if not (0 < response_minutes <= resolve_minutes):
            raise ValidationError("Нормативы: 0 < срок отклика ≤ срок решения (в минутах)")
        with self.db.transaction():
            self.db.execute(
                "UPDATE sla_policies SET response_minutes = ?, resolve_minutes = ? WHERE priority = ?",
                (response_minutes, resolve_minutes, priority),
            )
        return self.hd.sla.policy_for(priority)

    def set_setting(self, actor_id: int, key: str, value) -> None:
        self.hd.accounts.require(actor_id, Role.ADMIN)
        self.hd.settings.set(key, value)
