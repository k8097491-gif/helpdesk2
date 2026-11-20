"""Ошибки системы. Все наследуются от HelpdeskError."""


class HelpdeskError(Exception):
    """Базовая ошибка бизнес-логики."""


class NotFound(HelpdeskError):
    """Объект не найден."""


class PermissionDenied(HelpdeskError):
    """У пользователя нет прав на действие или на просмотр заявки."""


class ValidationError(HelpdeskError):
    """Некорректные входные данные или нарушено бизнес-правило."""


class InvalidTransition(HelpdeskError):
    """Недопустимый переход между статусами заявки."""
