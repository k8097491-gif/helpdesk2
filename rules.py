"""Бизнес-правила в виде данных: матрица приоритетов, допустимые переходы, нормативы."""
from .enums import Impact, Priority, Status, Urgency

# Приоритет определяется влиянием и срочностью.
#            срочность:  LOW              MEDIUM            HIGH
PRIORITY_MATRIX = {
    (Impact.HIGH, Urgency.LOW): Priority.MEDIUM,
    (Impact.HIGH, Urgency.MEDIUM): Priority.HIGH,
    (Impact.HIGH, Urgency.HIGH): Priority.CRITICAL,
    (Impact.MEDIUM, Urgency.LOW): Priority.LOW,
    (Impact.MEDIUM, Urgency.MEDIUM): Priority.MEDIUM,
    (Impact.MEDIUM, Urgency.HIGH): Priority.HIGH,
    (Impact.LOW, Urgency.LOW): Priority.LOW,
    (Impact.LOW, Urgency.MEDIUM): Priority.LOW,
    (Impact.LOW, Urgency.HIGH): Priority.MEDIUM,
}


def calculate_priority(impact: Impact, urgency: Urgency) -> Priority:
    return PRIORITY_MATRIX[(impact, urgency)]


# Жизненный цикл заявки: из какого статуса в какие можно перейти.
TRANSITIONS = {
    Status.NEW: {Status.ASSIGNED},
    Status.ASSIGNED: {Status.IN_PROGRESS, Status.ESCALATED},
    Status.IN_PROGRESS: {Status.WAITING, Status.ESCALATED, Status.RESOLVED, Status.ASSIGNED},
    Status.WAITING: {Status.IN_PROGRESS, Status.ESCALATED, Status.ASSIGNED},
    Status.ESCALATED: {Status.ASSIGNED, Status.IN_PROGRESS},
    Status.RESOLVED: {Status.CLOSED, Status.REOPENED},
    Status.REOPENED: {Status.IN_PROGRESS, Status.ESCALATED, Status.ASSIGNED},
    Status.CLOSED: set(),
}

# Нормативы по умолчанию: приоритет -> (первый отклик, решение), в минутах.
# Администратор может изменить их через Administration.set_sla().
DEFAULT_SLA_MINUTES = {
    Priority.CRITICAL: (15, 4 * 60),
    Priority.HIGH: (60, 8 * 60),
    Priority.MEDIUM: (4 * 60, 2 * 24 * 60),
    Priority.LOW: (8 * 60, 5 * 24 * 60),
}

# Настройки по умолчанию (меняются администратором).
DEFAULT_SETTINGS = {
    "auto_close_days": "3",   # через сколько дней без ответа решённая заявка закрывается
    "risk_ratio": "0.8",      # доля срока, после которой предупреждаем о риске просрочки
    "auto_assign": "1",       # назначать ли заявку автоматически по правилам маршрутизации
}

MIN_RESOLUTION_LENGTH = 10          # «Закрытие требует описания решения»
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".txt", ".log", ".docx", ".xlsx", ".zip"}
