"""Turn stored picks into graded results and season summaries."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import db
from .grading import Result, Side, cover_margin, format_line, grade, pick_relative_line


@dataclass
class GradedPick:
    week: int
    espn_id: str
    away_abbr: str
    home_abbr: str
    picked_side: str
    picked_abbr: str
    line: float
    status: str
    home_score: int | None
    away_score: int | None
    result: Result

    @property
    def is_decided(self) -> bool:
        return self.result in (Result.WIN, Result.LOSS, Result.PUSH)

    @property
    def on_favorite(self) -> bool:
        """Was the pick on the favorite? (line is home-relative)"""
        if self.line == 0:
            return False
        return (self.picked_side == "home") == (self.line < 0)

    @property
    def margin(self) -> float | None:
        """Points by which the pick beat the number; None until final."""
        if self.home_score is None or self.away_score is None or not self.is_decided:
            return None
        return cover_margin(self.picked_side, self.line, self.home_score, self.away_score)

    @property
    def matchup(self) -> str:
        return f"{self.away_abbr} @ {self.home_abbr}"

    @property
    def line_text(self) -> str:
        return format_line(self.line, self.home_abbr, self.away_abbr)

    @property
    def taken_at(self) -> float:
        """The number from the side that was picked."""
        return pick_relative_line(self.picked_side, self.line)


def grade_row(row: sqlite3.Row) -> GradedPick:
    return GradedPick(
        week=row["week"],
        espn_id=row["espn_id"],
        away_abbr=row["away_abbr"],
        home_abbr=row["home_abbr"],
        picked_side=row["picked_side"],
        picked_abbr=row["picked_abbr"],
        line=row["line"],
        status=row["status"],
        home_score=row["home_score"],
        away_score=row["away_score"],
        result=grade(
            row["picked_side"],
            row["line"],
            row["status"],
            row["home_score"],
            row["away_score"],
        ),
    )


def graded_picks(conn: sqlite3.Connection, season: int) -> list[GradedPick]:
    return [grade_row(r) for r in db.all_picks(conn, season)]


@dataclass
class Record:
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    pending: int = 0
    void: int = 0

    def add(self, result: Result) -> None:
        setattr(self, _FIELD[result], getattr(self, _FIELD[result]) + 1)

    @property
    def decided(self) -> int:
        return self.wins + self.losses

    @property
    def pct(self) -> float | None:
        """ATS win percentage, pushes excluded (as pools score them)."""
        return self.wins / self.decided if self.decided else None

    def __str__(self) -> str:
        base = f"{self.wins}-{self.losses}"
        if self.pushes:
            base += f"-{self.pushes}"
        if self.pct is not None:
            base += f" ({self.pct:.1%})"
        return base


_FIELD = {
    Result.WIN: "wins",
    Result.LOSS: "losses",
    Result.PUSH: "pushes",
    Result.PENDING: "pending",
    Result.VOID: "void",
}


def tally(picks: list[GradedPick]) -> Record:
    record = Record()
    for pick in picks:
        record.add(pick.result)
    return record


def by_week(picks: list[GradedPick]) -> dict[int, Record]:
    weeks: dict[int, Record] = {}
    for pick in picks:
        weeks.setdefault(pick.week, Record()).add(pick.result)
    return dict(sorted(weeks.items()))


def splits(picks: list[GradedPick]) -> dict[str, Record]:
    """Favorite/underdog and home/away breakdowns, decided picks only."""
    out = {
        "favorites": Record(),
        "underdogs": Record(),
        "home picks": Record(),
        "away picks": Record(),
    }
    for pick in picks:
        if not pick.is_decided:
            continue
        out["favorites" if pick.on_favorite else "underdogs"].add(pick.result)
        out["home picks" if pick.picked_side == "home" else "away picks"].add(pick.result)
    return out


def streak(picks: list[GradedPick]) -> str:
    """Current run of wins or losses, most recent first. Pushes are skipped."""
    decided = [p for p in picks if p.result in (Result.WIN, Result.LOSS)]
    if not decided:
        return "--"
    latest = decided[-1].result
    count = 0
    for pick in reversed(decided):
        if pick.result != latest:
            break
        count += 1
    return f"{'W' if latest is Result.WIN else 'L'}{count}"


def tiebreaker_status(conn: sqlite3.Connection, season: int, week: int) -> dict | None:
    """The week's total-points prediction against the actual result."""
    row = db.get_tiebreaker(conn, season, week)
    if row is None:
        return None
    actual = None
    if row["home_score"] is not None and row["away_score"] is not None:
        actual = row["home_score"] + row["away_score"]
    return {
        "matchup": f"{row['away_abbr']} @ {row['home_abbr']}",
        "predicted": row["predicted_total"],
        "actual": actual,
        "diff": None if actual is None else abs(actual - row["predicted_total"]),
        "final": row["status"] == "STATUS_FINAL",
    }
