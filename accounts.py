"""Пользователи, группы и права доступа (кто что видит и может делать)."""
from __future__ import annotations

from typing import Optional

from .base import Subsystem
from .enums import Channel, Role
from .errors import NotFound, PermissionDenied, ValidationError
from .models import Group, Ticket, User


class Accounts(Subsystem):
    # ---------------------------------------------------------- чтение
    def get_user(self, user_id: int) -> User:
        user = self.db.one(User, "SELECT * FROM users WHERE id = ?", (user_id,))
        if user is None:
            raise NotFound(f"Пользователь {user_id} не найден")
        return user

    def find_by_login(self, login: str) -> Optional[User]:
        return self.db.one(User, "SELECT * FROM users WHERE login = ?", (login,))

    def get_group(self, group_id: int) -> Group:
        group = self.db.one(Group, "SELECT * FROM groups WHERE id = ?", (group_id,))
        if group is None:
            raise NotFound(f"Группа {group_id} не найдена")
        return group

    def name_of(self, user_id: Optional[int]) -> str:
        return "Система" if user_id is None else self.get_user(user_id).full_name

    def members(self, group_id: int, roles=(Role.SPECIALIST,)) -> list[User]:
        """Активные сотрудники группы с заданными ролями."""
        marks = ", ".join("?" for _ in roles)
        return self.db.query(
            User,
            f"SELECT * FROM users WHERE group_id = ? AND active = 1 AND role IN ({marks}) ORDER BY id",
            (group_id, *roles),
        )

    def managers_for(self, group_id: Optional[int]) -> list[User]:
        """Руководители группы; если у группы их нет (или группа не задана) — все руководители."""
        if group_id is not None:
            own = self.members(group_id, roles=(Role.MANAGER,))
            if own:
                return own
        return self.db.query(User, "SELECT * FROM users WHERE role = ? AND active = 1 ORDER BY id", (Role.MANAGER,))

    # ---------------------------------------------------------- права
    def require(self, actor_id: int, *roles: Role) -> User:
        """Пользователь существует, активен и (если указаны роли) имеет одну из них."""
        user = self.get_user(actor_id)
        if not user.active:
            raise PermissionDenied("Учётная запись отключена")
        if roles and user.role not in roles:
            allowed = ", ".join(r.label for r in roles)
            raise PermissionDenied(f"Действие доступно только ролям: {allowed}")
        return user

    @staticmethod
    def can_view(user: User, ticket: Ticket) -> bool:
        """Заявитель видит свои заявки, специалист — назначенные ему, диспетчер и руководитель — все."""
        if user.id == ticket.requester_id or user.id == ticket.assignee_id:
            return True
        return user.role in (Role.DISPATCHER, Role.MANAGER)

    def visible_ticket(self, actor_id: int, ticket_id: int) -> tuple[User, Ticket]:
        user = self.require(actor_id)
        ticket = self.hd.store.get(ticket_id)
        if not self.can_view(user, ticket):
            raise PermissionDenied("Нет доступа к этой заявке")
        return user, ticket

    # ---------------------------------------------------------- создание (внутреннее)
    def create_group(self, name: str) -> Group:
        name = (name or "").strip()
        if not name:
            raise ValidationError("Название группы не может быть пустым")
        with self.db.transaction():
            group_id = self.db.insert("groups", name=name, active=True)
        return self.get_group(group_id)

    def create_user(
        self,
        login: str,
        full_name: str,
        email: str,
        role: Role = Role.REQUESTER,
        group_id: Optional[int] = None,
        notify_channel: Channel = Channel.EMAIL,
    ) -> User:
        login, full_name, email = (login or "").strip(), (full_name or "").strip(), (email or "").strip()
        if not (login and full_name and "@" in email):
            raise ValidationError("Нужны логин, имя и корректный e-mail")
        if self.find_by_login(login):
            raise ValidationError(f"Логин «{login}» уже занят")
        if role is Role.SPECIALIST and group_id is None:
            raise ValidationError("Специалист должен входить в группу поддержки")
        if group_id is not None:
            self.get_group(group_id)
        with self.db.transaction():
            user_id = self.db.insert(
                "users", login=login, full_name=full_name, email=email, role=role,
                group_id=group_id, notify_channel=notify_channel, active=True,
            )
        return self.get_user(user_id)
