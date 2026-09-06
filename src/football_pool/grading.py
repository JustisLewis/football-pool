"""Pure against-the-spread grading logic. No I/O, no dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# ESPN statuses that mean the game will never produce a result.
VOID_STATUSES = {"STATUS_CANCELED", "STATUS_POSTPONED"}
FINAL_STATUSES = {"STATUS_FINAL"}


class Result(str, Enum):
    WIN = "WIN"
    LOSS = "LOSS"
    PUSH = "PUSH"
    PENDING = "PENDING"
    VOID = "VOID"


class Side(str, Enum):
    HOME = "home"
    AWAY = "away"


@dataclass(frozen=True)
class Cover:
    """Which side covered, and by how much."""

    side: Side | None  # None on a push
    margin: float  # points by which the covering side beat the number


def cover(home_score: int, away_score: int, line: float) -> Cover:
    """Determine which side covered.

    `line` is home-relative, matching ESPN's convention: negative means the home
    team is favored (e.g. -6.5 == home favored by 6.5), positive means the home
    team is the underdog.
    """
    adjusted = (home_score - away_score) + line
    if adjusted > 0:
        return Cover(Side.HOME, adjusted)
    if adjusted < 0:
        return Cover(Side.AWAY, -adjusted)
    return Cover(None, 0.0)


def grade(
    picked_side: Side | str,
    line: float,
    status: str,
    home_score: int | None,
    away_score: int | None,
) -> Result:
    """Grade a single pick against the stored line.

    The line passed here is the pool's line, not ESPN's -- the commissioner's
    number is what the pool settles on.
    """
    if status in VOID_STATUSES:
        return Result.VOID
    if status not in FINAL_STATUSES or home_score is None or away_score is None:
        return Result.PENDING

    picked = Side(picked_side)
    result = cover(home_score, away_score, line)
    if result.side is None:
        return Result.PUSH
    return Result.WIN if result.side == picked else Result.LOSS


def cover_margin(
    picked_side: Side | str, line: float, home_score: int, away_score: int
) -> float:
    """Signed points by which the pick beat the number.

    Positive means the pick covered with room to spare; negative means it missed
    by that much. Zero is a push.
    """
    picked = Side(picked_side)
    adjusted = (home_score - away_score) + line
    return adjusted if picked == Side.HOME else -adjusted


def format_line(line: float, home_abbr: str, away_abbr: str) -> str:
    """Render a home-relative line the way people say it: 'GT -6.5'."""
    if line == 0:
        return "PK"
    favorite = home_abbr if line < 0 else away_abbr
    return f"{favorite} -{abs(line):g}"


def pick_relative_line(picked_side: Side | str, line: float) -> float:
    """The line from the picked team's side.

    Bettors quote the number they took, not the home team's: picking the
    favorite is a minus, picking the underdog is a plus.
    """
    return line if Side(picked_side) == Side.HOME else -line


def to_home_relative(favored: str, magnitude: float) -> float:
    """Convert 'which side is favored, by how much' into a home-relative line."""
    if magnitude < 0:
        raise ValueError(f"magnitude must be non-negative, got {magnitude}")
    if favored == "home":
        return -magnitude
    if favored == "away":
        return magnitude
    raise ValueError(f"cannot convert favored={favored!r} to a line")
