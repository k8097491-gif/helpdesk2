"""Helpdesk — сервис IT-поддержки вуза (практическая работа 7)."""
from .clock import ManualClock, SystemClock
from .directory import DirectoryEntry, InMemoryDirectory
from .enums import Channel, EscalationKind, Impact, Priority, Role, Status, Urgency
from .errors import HelpdeskError, InvalidTransition, NotFound, PermissionDenied, ValidationError
from .system import HelpdeskSystem

__all__ = [
    "HelpdeskSystem", "ManualClock", "SystemClock", "DirectoryEntry", "InMemoryDirectory",
    "Channel", "EscalationKind", "Impact", "Priority", "Role", "Status", "Urgency",
    "HelpdeskError", "InvalidTransition", "NotFound", "PermissionDenied", "ValidationError",
]
