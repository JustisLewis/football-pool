"""The plain-text pick list sent to the commissioner.

The list has to be readable line-for-line against the sheet they sent out, so
it echoes their own wording rather than ESPN's -- ESPN writes "San Jose St" as
"San Jose State Spartans".
"""

import sqlite3

import pytest

from football_pool import db
from football_pool.cli import main
from football_pool.espn import parse_games
from football_pool.matching import WeekIndex, split_matchup

SEASON, WEEK = 2026, 1

SLATE = [
    # raw slate line,                      picked side, expected output line
    ("San Jose St @ USC (-37.5)", "away", "San Jose St"),
    ("Miami (FL) (-24.5) @ Stanford", "away", "Miami (FL)"),
    ("Wash St @ Washington (-24)", "home", "Washington"),
]


class TestSplitMatchup:
    def test_strips_the_spread_but_keeps_a_name_parenthetical(self):
        assert split_matchup("Miami (FL) @ Stanford (-16.5)") == (
            "Miami (FL)",
            "Stanford",
        )

    def test_handles_the_away_favorite_form(self):
        assert split_matchup("Wash St (-2.5) @ Washington") == (
            "Wash St",
            "Washington",
        )

    def test_drops_the_total_points_prefix(self):
        assert split_matchup("Total Points - Akron @ Wake Forest") == (
            "Akron",
            "Wake Forest",
        )

    def test_returns_none_when_there_is_no_matchup(self):
        assert split_matchup("SAMPLE WEEK") is None
        assert split_matchup("") is None


@pytest.fixture
def picked(tmp_path, week1_2026):
    """A week with three picks and a tiebreaker, stored the way import does."""
    conn = db.connect(tmp_path / "export.db")
    db.upsert_games(conn, parse_games(week1_2026))
    index = WeekIndex(week1_2026)

    rows, picks = [], []
    for slot, (raw, side, _) in enumerate(SLATE, start=1):
        away, home = split_matchup(raw)
        match, _ = index.best_match(away, home)
        rows.append(
            {
                "espn_id": match.espn_id,
                "slot": slot,
                "pool_line": -6.5,
                "is_tiebreaker": 0,
                "raw_text": raw,
                "away_text": away,
                "home_text": home,
            }
        )
        picks.append((match, side))

    tb_away, tb_home = split_matchup("Total Points - Akron @ Wake Forest")
    tb = index.best_match(tb_away, tb_home)[0]
    rows.append(
        {
            "espn_id": tb.espn_id,
            "slot": 99,
            "is_tiebreaker": 1,
            "raw_text": "Total Points - Akron @ Wake Forest",
            "away_text": tb_away,
            "home_text": tb_home,
        }
    )
    db.replace_slate(conn, SEASON, WEEK, rows)

    for match, side in picks:
        abbr = match.home.abbreviation if side == "home" else match.away.abbreviation
        db.save_pick(conn, SEASON, WEEK, match.espn_id, side, abbr, -6.5)
    db.save_tiebreaker(conn, SEASON, WEEK, tb.espn_id, 64)
    conn.close()
    return tmp_path / "export.db"


def run_export(db_path, capsys, *extra):
    code = main(["--db", str(db_path), "export", "--season", str(SEASON),
                 "--week", str(WEEK), *extra])
    return code, capsys.readouterr()


class TestExport:
    def test_one_line_per_pick_then_the_total(self, picked, capsys):
        code, captured = run_export(picked, capsys)
        assert code == 0
        assert captured.out.splitlines() == [
            "San Jose St",
            "Miami (FL)",
            "Washington",
            "64 points",
        ]

    def test_uses_the_sheets_wording_not_espns(self, picked, capsys):
        """ESPN calls this the San Jose State Spartans; the sheet does not."""
        _, captured = run_export(picked, capsys)
        assert "San Jose St" in captured.out
        assert "Spartans" not in captured.out

    def test_nothing_but_picks_on_stdout(self, picked, capsys):
        """The output is piped straight to a message, so no headers or totals."""
        _, captured = run_export(picked, capsys)
        assert "week" not in captured.out.lower()
        assert "@" not in captured.out

    def test_refuses_to_print_an_incomplete_list(self, picked, capsys):
        conn = db.connect(picked)
        rows = db.slate_for_week(conn, SEASON, WEEK)
        db.delete_pick(conn, SEASON, WEEK, rows[0]["espn_id"])
        conn.close()
        with pytest.raises(SystemExit) as exc:
            run_export(picked, capsys)
        assert exc.value.code == 1

    def test_allow_missing_prints_with_placeholders_and_warns(self, picked, capsys):
        conn = db.connect(picked)
        rows = db.slate_for_week(conn, SEASON, WEEK)
        db.delete_pick(conn, SEASON, WEEK, rows[0]["espn_id"])
        conn.close()
        code, captured = run_export(picked, capsys, "--allow-missing")
        assert code == 0
        assert captured.out.splitlines()[0] == "(no pick)"
        assert "warning: missing" in captured.err  # warnings stay off stdout


class TestMigration:
    def test_old_database_gains_the_columns_and_backfills_them(self, tmp_path):
        """Databases created before these columns existed must still export."""
        path = tmp_path / "old.db"
        old = sqlite3.connect(path)
        old.executescript(
            """
            CREATE TABLE slate_games (
                season INTEGER NOT NULL, week INTEGER NOT NULL,
                espn_id TEXT NOT NULL, slot INTEGER NOT NULL,
                pool_line REAL, is_tiebreaker INTEGER DEFAULT 0,
                raw_text TEXT, source_image TEXT,
                PRIMARY KEY (season, week, espn_id)
            );
            INSERT INTO slate_games VALUES
                (2026, 1, '999', 1, -2.5, 0, 'Wash St (-2.5) @ Washington', NULL);
            """
        )
        old.commit()
        old.close()

        conn = db.connect(path)  # migration runs here
        row = conn.execute(
            "SELECT away_text, home_text FROM slate_games WHERE espn_id = '999'"
        ).fetchone()
        assert (row["away_text"], row["home_text"]) == ("Wash St", "Washington")
