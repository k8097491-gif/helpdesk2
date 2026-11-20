"""Концептуальная модель данных в виде dataclass-ов.

Центральная сущность — Ticket («Заявка»). Связи (см. документ):
  User 1:N Ticket                      (заявитель)
  SupportGroup 1:N Ticket, User        (группа включает специалистов)
  Ticket 1:N Comment, Attachment, StatusEvent
  Ticket 1:N Assignment                (история назначений; текущий — в Ticket)
  Ticket 1:N Escalation, 0..1 Feedback
"""
from __future__ import annotations

import dataclasses
import types
import typing
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from .enums import Channel, EscalationKind, Impact, Priority, Role, Status, Urgency


@dataclass
class Group:
    id: int
    name: str
    active: bool = True


@dataclass
class User:
    id: int
    login: str
    full_name: str
    email: str
    role: Role
    group_id: Optional[int] = None
    notify_channel: Channel = Channel.EMAIL
    active: bool = True


@dataclass
class Category:
    id: int
    name: str
    group_id: Optional[int] = None      # правило маршрутизации: категория -> группа
    active: bool = True


@dataclass
class SlaPolicy:
    priority: Priority
    response_minutes: int
    resolve_minutes: int


@dataclass
class Ticket:
    id: int
    number: str
    title: str
    description: str
    category_id: int
    impact: Impact
    urgency: Urgency
    priority: Priority
    status: Status
    requester_id: int
    created_at: datetime
    updated_at: datetime
    sla_start_at: datetime              # начало отсчёта сроков (создание или повторное открытие)
    response_due_at: datetime           # целевой срок первого отклика
    resolve_due_at: datetime            # целевой срок решения
    group_id: Optional[int] = None
    assignee_id: Optional[int] = None   # у заявки один текущий исполнитель
    first_response_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    waiting_since: Optional[datetime] = None
    waited_seconds: int = 0             # сколько секунд заявка «ожидала» заявителя (часы SLA стоят)
    resolution_text: Optional[str] = None
    reopen_count: int = 0
    risk_notified: bool = False
    breach_notified: bool = False


@dataclass
class Comment:
    id: int
    ticket_id: int
    author_id: int
    text: str
    created_at: datetime


@dataclass
class Attachment:
    id: int
    ticket_id: int
    author_id: int
    filename: str
    content_type: str
    size: int
    created_at: datetime
    data: bytes = b""


@dataclass
class StatusEvent:
    id: int
    ticket_id: int
    from_status: Optional[Status]
    to_status: Status
    author_id: Optional[int]            # None — действие выполнила система
    reason: str
    created_at: datetime


@dataclass
class Assignment:
    id: int
    ticket_id: int
    group_id: Optional[int]
    assignee_id: Optional[int]
    author_id: Optional[int]
    reason: str
    created_at: datetime


@dataclass
class Escalation:
    id: int
    ticket_id: int
    kind: EscalationKind
    author_id: int
    reason: str
    from_group_id: Optional[int]
    from_assignee_id: Optional[int]
    to_group_id: Optional[int]
    to_assignee_id: Optional[int]
    created_at: datetime


@dataclass
class FieldChange:
    id: int
    ticket_id: int
    field: str
    old_value: Optional[str]
    new_value: Optional[str]
    author_id: Optional[int]
    reason: str
    created_at: datetime


@dataclass
class Feedback:
    id: int
    ticket_id: int
    requester_id: int
    rating: int
    comment: Optional[str]
    created_at: datetime


@dataclass
class Notification:
    id: int
    user_id: int
    ticket_id: Optional[int]
    channel: Channel
    subject: str
    body: str
    created_at: datetime
    delivered: bool = False


@dataclass
class Article:
    id: int
    title: str
    body: str
    keywords: str
    category_id: Optional[int]
    author_id: int
    created_at: datetime


@dataclass
class TimelineEntry:
    """Строка единой истории заявки (для показа в карточке)."""
    at: datetime
    kind: str
    author: str
    text: str


# ---------------------------------------------------------------- преобразования

def to_db(value):
    """Python -> значение для SQLite."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    if isinstance(value, bool):
        return int(value)
    return value


def _unwrap_optional(hint):
    union_types = (typing.Union,)
    if hasattr(types, "UnionType"):
        union_types += (types.UnionType,)
    if typing.get_origin(hint) in union_types:
        args = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return hint


def from_row(cls, row):
    """Строка SQLite -> dataclass `cls` (enum, datetime и bool восстанавливаются по аннотациям)."""
    hints = typing.get_type_hints(cls)
    columns = set(row.keys())
    values = {}
    for f in dataclasses.fields(cls):
        if f.name not in columns:
            continue                      # например, Attachment.data не выбирается в списках
        raw = row[f.name]
        hint = _unwrap_optional(hints[f.name])
        if raw is None:
            values[f.name] = None
        elif isinstance(hint, type) and issubclass(hint, Enum):
            values[f.name] = hint(raw)
        elif hint is datetime:
            values[f.name] = datetime.fromisoformat(raw)
        elif hint is bool:
            values[f.name] = bool(raw)
        else:
            values[f.name] = raw
    return cls(**values)
