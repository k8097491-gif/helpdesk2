"""Настройки системы (хранятся в БД, значения по умолчанию — в rules.DEFAULT_SETTINGS)."""
from .base import Subsystem
from .errors import ValidationError
from .rules import DEFAULT_SETTINGS


class Settings(Subsystem):
    def get(self, key: str) -> str:
        if key not in DEFAULT_SETTINGS:
            raise ValidationError(f"Неизвестная настройка: {key}")
        value = self.db.scalar("SELECT value FROM settings WHERE key = ?", (key,))
        return DEFAULT_SETTINGS[key] if value is None else value

    def get_int(self, key: str) -> int:
        return int(self.get(key))

    def get_float(self, key: str) -> float:
        return float(self.get(key))

    def get_bool(self, key: str) -> bool:
        return self.get(key) in ("1", "true", "True")

    def set(self, key: str, value) -> None:
        if key not in DEFAULT_SETTINGS:
            raise ValidationError(f"Неизвестная настройка: {key}")
        if isinstance(value, bool):
            value = "1" if value else "0"
        value = str(value)
        try:
            number = float(value)
        except ValueError:
            raise ValidationError(f"Настройка {key} должна быть числом")
        if key == "auto_close_days" and number < 1:
            raise ValidationError("Срок автозакрытия — не менее 1 дня")
        if key == "risk_ratio" and not 0 < number < 1:
            raise ValidationError("risk_ratio должен быть между 0 и 1")
        with self.db.transaction():
            self.db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
