"""Перечисления предметной области (роли, статусы, приоритеты и т.д.).

Каждое значение хранится в БД как строка (`.value`), а в интерфейсе
показывается по-русски (`.label`).
"""
from enum import Enum


class Labeled(str, Enum):
    """Строковый enum с русской подписью: Status("NEW").label == "Новая"."""

    def __new__(cls, value: str, label: str):
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.label = label
        return obj


class Role(Labeled):
    REQUESTER = ("REQUESTER", "Заявитель")
    DISPATCHER = ("DISPATCHER", "Диспетчер")
    SPECIALIST = ("SPECIALIST", "Специалист поддержки")
    MANAGER = ("MANAGER", "Руководитель поддержки")
    ADMIN = ("ADMIN", "Администратор")


class Status(Labeled):
    NEW = ("NEW", "Новая")
    ASSIGNED = ("ASSIGNED", "Назначена")
    IN_PROGRESS = ("IN_PROGRESS", "В работе")
    WAITING = ("WAITING", "Ожидает")
    ESCALATED = ("ESCALATED", "Эскалирована")
    RESOLVED = ("RESOLVED", "Решена")
    CLOSED = ("CLOSED", "Закрыта")
    REOPENED = ("REOPENED", "Повторно открыта")


class Impact(Labeled):
    """Влияние: сколько людей/сервисов затронуто."""
    LOW = ("LOW", "Затронут один пользователь")
    MEDIUM = ("MEDIUM", "Затронута группа или подразделение")
    HIGH = ("HIGH", "Затронут весь вуз или критичный сервис")


class Urgency(Labeled):
    """Срочность: насколько быстро нужно решение."""
    LOW = ("LOW", "Можно подождать")
    MEDIUM = ("MEDIUM", "Работа затруднена")
    HIGH = ("HIGH", "Работа остановлена")


class Priority(Labeled):
    CRITICAL = ("CRITICAL", "Критический")
    HIGH = ("HIGH", "Высокий")
    MEDIUM = ("MEDIUM", "Средний")
    LOW = ("LOW", "Низкий")

    @property
    def rank(self) -> int:
        """0 — самый важный (для сортировки очереди)."""
        return ("CRITICAL", "HIGH", "MEDIUM", "LOW").index(self.value)


class Channel(Labeled):
    EMAIL = ("EMAIL", "Электронная почта")
    PUSH = ("PUSH", "Push-уведомление")


class EscalationKind(Labeled):
    MANAGER = ("MANAGER", "Руководителю")
    GROUP = ("GROUP", "Другой группе")


# Заявка «в работе у поддержки» (идёт отсчёт срока, нагружает исполнителя).
OPEN_STATUSES = (
    Status.NEW,
    Status.ASSIGNED,
    Status.IN_PROGRESS,
    Status.WAITING,
    Status.ESCALATED,
    Status.REOPENED,
)
# Статусы, в которых заявка закреплена за исполнителем (считается нагрузкой).
LOAD_STATUSES = tuple(s for s in OPEN_STATUSES if s is not Status.NEW)
