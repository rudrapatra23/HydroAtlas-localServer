from __future__ import annotations

from datetime import date


def validate_year_month(year: int, month: int) -> None:
    if year < 1900 or year > 2100:
        raise ValueError("year must be between 1900 and 2100")
    if month < 1 or month > 12:
        raise ValueError("month must be between 1 and 12")
    today = date.today()
    if (year, month) > (today.year, today.month):
        raise ValueError("requested month cannot be in the future")


def parse_int(value: object, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def previous_month(current: date | None = None) -> tuple[int, int]:
    current = current or date.today()
    if current.month == 1:
        return current.year - 1, 12
    return current.year, current.month - 1
