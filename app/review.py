"""Performance-based spaced repetition: the better the quiz score, the longer until the next revision."""
from datetime import date, timedelta

FIRST_INTERVAL = 1  # a finished lecture is first revised the next day
MAX_INTERVAL = 60


def next_interval(previous: float | None, score: float) -> float:
    """Days until the next revision after a quiz with `score` (0..1)."""
    previous = previous or FIRST_INTERVAL
    if score >= 0.9:
        interval = max(3.0, previous * 2.5)
    elif score >= 0.7:
        interval = max(2.0, previous * 1.8)
    elif score >= 0.5:
        interval = max(1.0, previous)
    else:
        interval = 1.0  # mostly wrong: revise again tomorrow
    return min(MAX_INTERVAL, interval)


def due_after(interval: float, today: date | None = None) -> date:
    return (today or date.today()) + timedelta(days=max(1, round(interval)))
