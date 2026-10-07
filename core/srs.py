"""Spaced repetition with the SM-2 algorithm (SuperMemo 2, the basis of Anki's scheduler).

Each card has an ease factor (how easy it is for you, starting at 2.5), an interval in days and a
count of successful reviews in a row. After a review graded 0-5:
  - grade < 3 (forgot): start over, see it again tomorrow
  - grade >= 3: interval 1 day, then 6 days, then previous interval x ease
  - the ease goes up for easy answers and down for hard ones (never below 1.3)
The four buttons map to grades: Again = 1, Hard = 3, Good = 4, Easy = 5.

Two changes borrowed from Anki, because plain SM-2 gives every new card "1 day" on all four buttons:
  - Again keeps the card due today (it comes back later in the same session)
  - Easy on a brand-new card jumps straight to 4 days
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

BUTTONS = {"Again": 1, "Hard": 3, "Good": 4, "Easy": 5}
MIN_EASE = 1.3
EASY_BONUS = 1.3   # an "Easy" answer stretches the interval a little further
NEW_CARD_EASY_DAYS = 4


@dataclass(frozen=True)
class CardState:
    ease: float = 2.5
    interval: int = 0
    reps: int = 0
    lapses: int = 0


def review(state: CardState, grade: int, today: date | None = None) -> tuple[CardState, date]:
    """Return the new state and the next due date for a review with this grade (0-5)."""
    if not 0 <= grade <= 5:
        raise ValueError("grade must be between 0 and 5")
    today = today or date.today()
    if grade < 3:
        new = CardState(state.ease, 0, 0, state.lapses + 1)   # relearn: due again today
    else:
        if state.reps == 0:
            interval = NEW_CARD_EASY_DAYS if grade == 5 else 1
        elif state.reps == 1:
            interval = 6
        else:
            interval = round(state.interval * state.ease)
        if grade == 5 and state.reps > 0:
            interval = round(interval * EASY_BONUS)
        new = CardState(state.ease, max(1, interval), state.reps + 1, state.lapses)
    # SM-2 ease update: +0.1 for grade 5, unchanged for 4, lower for 3 and below
    ease = state.ease + 0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02)
    new = CardState(max(MIN_EASE, round(ease, 3)), new.interval, new.reps, new.lapses)
    return new, today + timedelta(days=new.interval)


def preview(state: CardState) -> dict[str, str]:
    """Label for each button: when the card would come back ('1d', '6d', '2mo')."""
    out = {}
    for name, g in BUTTONS.items():
        days = review(state, g)[0].interval
        out[name] = ("again today" if days == 0 else f"{days}d" if days < 30 else
                     f"{days / 30:.0f}mo" if days < 365 else f"{days / 365:.1f}y")
    return out
