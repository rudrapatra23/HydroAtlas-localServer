from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StatisticsRequest:
    """Request body for district / state statistics.

    The fundamental time unit is ONE MONTH. Requests specify an inclusive
    ``[start_year/start_month, end_year/end_month]`` range. Individual
    days are never part of the API surface.
    """

    start_year: int
    start_month: int
    end_year: int
    end_month: int
    variable: str = "precipitation"

    def validate(self) -> None:
        """Validate the inclusive month range.

        Raises:
            ValueError: when the range is malformed, reversed, or out of
                bounds for the calendar.
        """
        if not (1 <= self.start_month <= 12):
            raise ValueError("start_month must be between 1 and 12")
        if not (1 <= self.end_month <= 12):
            raise ValueError("end_month must be between 1 and 12")
        if self.start_year <= 0 or self.end_year <= 0:
            raise ValueError("start_year and end_year must be positive")
        start_key = self.start_year * 12 + (self.start_month - 1)
        end_key = self.end_year * 12 + (self.end_month - 1)
        if start_key > end_key:
            raise ValueError("Start Month must be less than or equal to End Month")
