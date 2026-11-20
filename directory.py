"""Университетский каталог: источник учётных данных пользователей (LDAP/AD в реальной системе)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class DirectoryEntry:
    login: str
    full_name: str
    email: str


class UniversityDirectory(Protocol):
    def lookup(self, login: str) -> Optional[DirectoryEntry]: ...


class InMemoryDirectory:
    """Каталог-заглушка. Для реального вуза замените на класс, который обращается к LDAP/AD."""

    def __init__(self, entries: Optional[list[DirectoryEntry]] = None):
        self._entries = {e.login: e for e in entries or []}

    def add(self, entry: DirectoryEntry) -> None:
        self._entries[entry.login] = entry

    def lookup(self, login: str) -> Optional[DirectoryEntry]:
        return self._entries.get(login)
