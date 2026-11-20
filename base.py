"""Базовый класс подсистем: у каждой есть доступ к общей «шине» HelpdeskSystem."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .system import HelpdeskSystem


class Subsystem:
    def __init__(self, hd: "HelpdeskSystem"):
        self.hd = hd

    @property
    def db(self):
        return self.hd.db

    def now(self) -> datetime:
        return self.hd.now()
