"""Turning what you type at the pick prompt into a side of a game.

Regression: `a`/`h` used to be checked only after a substring search of the
typed text against both team names, so a bare "h" matched the h in
"Sam Houston Bearkats" and picked the away team. Whether the shortcut worked
depended on which letters happened to be in the two teams' names.
"""

import pytest

from football_pool.cli import _find_slot, _side_for
from football_pool.espn import parse_games

from conftest import load_scoreboard


def every_game():
    """Every real matchup in both fixture weeks, as pick-prompt rows."""
    for season, week in [(2026, 1), (2025, 5)]:
        for game in parse_games(load_scoreboard(season, week)):
            yield pytest.param(game, id=f"{game['away_abbr']}@{game['home_abbr']}")


@pytest.mark.parametrize("game", list(every_game()))
def test_shortcuts_mean_the_same_side_in_every_game(game):
    """The property the bug broke, checked against ~150 real team-name pairs."""
    for key in ("a", "A", "away", " a "):
        assert _side_for(game, key) == "away", key
    for key in ("h", "H", "home", " h "):
        assert _side_for(game, key) == "home", key


def row(away_abbr, away_name, home_abbr, home_name, away_text=None, home_text=None, slot=1):
    return {
        "slot": slot,
        "away_abbr": away_abbr, "away_name": away_name, "away_text": away_text,
        "home_abbr": home_abbr, "home_name": home_name, "home_text": home_text,
    }


SHSU_KENN = row("SHSU", "Sam Houston Bearkats", "KENN", "Kennesaw State Owls",
                "Sam Houston", "Kennesaw State")


class TestPickByName:
    @pytest.mark.parametrize("typed,side", [
        ("KENN", "home"),
        ("kenn", "home"),
        ("Kennesaw State", "home"),
        ("kennesaw", "home"),        # whole-word prefix
        ("SHSU", "away"),
        ("Sam Houston", "away"),
        ("Bearkats", None),          # not a prefix of any name
        ("houston", None),           # mid-name fragment, not a prefix
        ("o", None),                 # single letters are not names
    ])
    def test_names_and_abbreviations(self, typed, side):
        assert _side_for(SHSU_KENN, typed) == side

    def test_equally_good_matches_are_refused(self):
        """'Louisiana' is a prefix of both teams' names; don't guess."""
        game = row("UL", "Louisiana Ragin' Cajuns", "LT", "Louisiana Tech Bulldogs")
        assert _side_for(game, "Louisiana") is None

    def test_the_sheets_exact_wording_breaks_the_tie(self):
        game = row("UL", "Louisiana Ragin' Cajuns", "LT", "Louisiana Tech Bulldogs",
                   away_text="Louisiana", home_text="Louisiana Tech")
        assert _side_for(game, "Louisiana") == "away"
        assert _side_for(game, "Louisiana Tech") == "home"


class TestFindSlot:
    ROWS = [
        row("NWST", "Northwestern State Demons", "LT", "Louisiana Tech Bulldogs",
            "Northwestern St", "Louisiana Tech", slot=1),
        row("LAM", "Lamar Cardinals", "UL", "Louisiana Ragin' Cajuns",
            "Lamar", "Louisiana", slot=2),
    ]

    def test_slot_number(self):
        assert _find_slot(self.ROWS, "2")["slot"] == 2

    def test_exact_name_beats_prefix(self):
        """'Louisiana' is Louisiana, not Louisiana Tech."""
        assert _find_slot(self.ROWS, "Louisiana")["slot"] == 2

    def test_unique_prefix_finds_the_game(self):
        assert _find_slot(self.ROWS, "Northwestern")["slot"] == 1

    def test_single_letter_finds_nothing(self):
        """Used to substring-match whichever game had an 'a' in it."""
        assert _find_slot(self.ROWS, "a") is None
