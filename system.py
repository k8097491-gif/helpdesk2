"""HelpdeskSystem — «шина», которая собирает все подсистемы вместе."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .accounts import Accounts
from .admin import Administration
from .analytics import Analytics
from .clock import SystemClock
from .db import Database
from .directory import InMemoryDirectory, UniversityDirectory
from .enums import Channel, Role
from .errors import NotFound
from .feedback import FeedbackService
from .intake import IntakeChannel
from .knowledge import KnowledgeBase
from .models import User
from .notifications import DeliveryChannel, MemoryChannel, Notifier
from .routing import Routing
from .rules import DEFAULT_SLA_MINUTES
from .settings import Settings
from .sla import SlaControl
from .store import TicketStore
from .workflow import TicketWorkflow


class HelpdeskSystem:
    """Точка входа. Подсистемы (см. раздел «Подсистемы» документа):

    intake     — канал приёма обращений
    workflow   — реестр и рабочий процесс
    routing    — классификация и маршрутизация
    sla        — сроки и эскалация
    notifier   — уведомления;  kb — база знаний
    feedback   — обратная связь;  analytics — аналитика
    admin      — администрирование
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        clock=None,
        directory: Optional[UniversityDirectory] = None,
        channels: Optional[dict[Channel, DeliveryChannel]] = None,
    ):
        self.clock = clock or SystemClock()
        self._last_now: Optional[datetime] = None
        self.db = Database(db_path)
        self.directory = directory or InMemoryDirectory()

        self.settings = Settings(self)
        self.accounts = Accounts(self)
        self.store = TicketStore(self)
        self.notifier = Notifier(self, channels or {c: MemoryChannel(c.value) for c in Channel})
        self.routing = Routing(self)
        self.sla = SlaControl(self)
        self.intake = IntakeChannel(self)
        self.workflow = TicketWorkflow(self)
        self.kb = KnowledgeBase(self)
        self.feedback = FeedbackService(self)
        self.analytics = Analytics(self)
        self.admin = Administration(self)

        self._seed_sla_policies()

    def close(self) -> None:
        """Закрыть соединение с базой данных."""
        self.db.close()

    def now(self) -> datetime:
        """Текущее время; каждое обращение даёт строго большее значение, чтобы события
        в истории всегда выстраивались в порядке их совершения."""
        current = self.clock.now()
        if self._last_now is not None and current <= self._last_now:
            current = self._last_now + timedelta(microseconds=1)
        self._last_now = current
        return current

    def _seed_sla_policies(self) -> None:
        with self.db.transaction():
            for priority, (response, resolve) in DEFAULT_SLA_MINUTES.items():
                self.db.execute(
                    "INSERT OR IGNORE INTO sla_policies (priority, response_minutes, resolve_minutes) VALUES (?, ?, ?)",
                    (priority, response, resolve),
                )

    # ------------------------------------------------------------ вход и первичная настройка
    def bootstrap_admin(self, login: str, full_name: str, email: str) -> User:
        """Создать первого администратора (пока в системе нет ни одного пользователя)."""
        if self.db.scalar("SELECT COUNT(*) FROM users"):
            raise RuntimeError("Пользователи уже есть: создавайте их через admin.create_user()")
        return self.accounts.create_user(login, full_name, email, Role.ADMIN)

    def sign_in(self, login: str) -> User:
        """Вход по учётным данным из университетского каталога.

        Новый пользователь автоматически регистрируется как «Заявитель»;
        имя и e-mail подтягиваются из каталога при каждом входе.
        """
        entry = self.directory.lookup(login)
        user = self.accounts.find_by_login(login)
        if user is None:
            if entry is None:
                raise NotFound(f"Пользователь «{login}» не найден в университетском каталоге")
            return self.accounts.create_user(entry.login, entry.full_name, entry.email, Role.REQUESTER)
        if entry and (entry.full_name, entry.email) != (user.full_name, user.email):
            with self.db.transaction():
                self.db.update("users", user.id, full_name=entry.full_name, email=entry.email)
            user = self.accounts.get_user(user.id)
        return user
