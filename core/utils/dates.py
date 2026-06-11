"""Date series generation utilities.

Provides functions for generating period-end dates used in time-series
financial calculations (portfolio value, income, cost basis, etc.).
"""

from datetime import date, timedelta


def generate_date_series(
    start_date: date,
    end_date: date,
    freq: str = 'monthly',
) -> list[date]:
    """Generate period-end dates between start_date and end_date (inclusive).

    Args:
        freq: One of 'daily', 'weekly', 'monthly', 'annual'.

    Returns:
        List of date objects. For monthly: last day of each month.
        For weekly: each Friday (or last trading day of week).
        For daily: every calendar day in range.
        For annual: last day of each calendar year (Dec 31).
    """
    if freq == 'daily':
        dates = []
        current = start_date
        while current <= end_date:
            dates.append(current)
            current += timedelta(days=1)
        return dates

    elif freq == 'weekly':
        dates = []
        # Find the first Friday on or after start_date
        days_to_friday = (4 - start_date.weekday()) % 7
        current = start_date + timedelta(days=days_to_friday)
        while current <= end_date:
            dates.append(current)
            current += timedelta(days=7)
        return dates

    elif freq == 'annual':
        dates = []
        year = start_date.year
        while True:
            year_end = date(year, 12, 31)
            if year_end < start_date:
                year += 1
                continue
            if year_end > end_date:
                year_end = end_date
            dates.append(year_end)
            if year_end == end_date:
                break
            year += 1
        return dates

    else:
        # monthly (default)
        dates = []
        current = start_date.replace(day=1)
        while current <= end_date:
            # Move to next month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1, day=1)
            else:
                current = current.replace(month=current.month + 1, day=1)
            # Last day of the previous month (current is now the 1st of the next month)
            dates.append(current - timedelta(days=1))
        return dates
