"""Golden test for the Week One slate.

The fixture is the expected structured reading of the Week One screenshot. It
pins the three format rules that are easy to get wrong -- the parenthetical
attaching to the favored team by position, the unsigned line that must not be
guessed, and the total-points tiebreaker -- and checks that each converts to the
right home-relative line.

Run against the live API with:  uv run pytest --live
"""

import json
from pathlib import Path

import pytest

from football_pool.grading import to_home_relative
from football_pool.matching import WeekIndex
from football_pool.slate_import import ParsedSlate

FIXTURE = Path(__file__).parent / "fixtures" / "slate-2026-w01.json"


@pytest.fixture
def slate() -> ParsedSlate:
    return ParsedSlate.model_validate_json(FIXTURE.read_text())


def find(slate: ParsedSlate, away: str):
    return next(g for g in slate.games if g.away == away)


class TestSlateShape:
    def test_reads_the_week_heading(self, slate):
        assert slate.week_label == "WEEK ONE!!!!"

    def test_fifteen_spread_games_and_one_tiebreaker(self, slate):
        spread_games = [g for g in slate.games if not g.is_tiebreaker]
        tiebreakers = [g for g in slate.games if g.is_tiebreaker]
        assert len(spread_games) == 15
        assert len(tiebreakers) == 1

    def test_team_names_are_kept_verbatim(self, slate):
        """Shorthand must survive the parse; matching expands it later."""
        names = {g.away for g in slate.games}
        assert {"Tenn St", "Miami (OH)", "Arkansas St"} <= names


class TestPositionalRule:
    def test_paren_after_home_team_means_home_favored(self, slate):
        game = find(slate, "Colorado")  # Colorado @ Georgia Tech (-6.5)
        assert game.favored == "home"
        assert game.spread_magnitude == 6.5
        assert to_home_relative(game.favored, game.spread_magnitude) == -6.5

    def test_paren_after_away_team_means_away_favored(self, slate):
        game = find(slate, "UNLV")  # UNLV (-2.5) @ Hawaii
        assert game.favored == "away"
        assert game.spread_magnitude == 2.5
        # Away favored -> home-relative line is positive (home is the dog)
        assert to_home_relative(game.favored, game.spread_magnitude) == 2.5

    def test_second_away_favorite_on_the_same_slate(self, slate):
        game = find(slate, "UCLA")  # UCLA (-1.5) @ California
        assert game.favored == "away"
        assert to_home_relative(game.favored, game.spread_magnitude) == 1.5


class TestAmbiguity:
    def test_unsigned_line_is_flagged_not_guessed(self, slate):
        game = find(slate, "Arkansas St")  # Arkansas St @ Memphis (11.5)
        assert game.favored == "unknown"
        assert game.spread_magnitude == 11.5

    def test_unknown_cannot_be_converted_to_a_line(self, slate):
        """The type system refuses to invent a direction."""
        game = find(slate, "Arkansas St")
        with pytest.raises(ValueError):
            to_home_relative(game.favored, game.spread_magnitude)

    def test_tiebreaker_has_no_spread(self, slate):
        game = find(slate, "Furman")  # Total Points - Furman @ Tennessee
        assert game.is_tiebreaker
        assert game.spread_magnitude is None


class TestAgainstRealSchedule:
    def test_every_parsed_game_matches_an_espn_game(self, slate, week1_2026):
        """The parse is only useful if it lands on real games."""
        index = WeekIndex(week1_2026)
        unmatched = [
            g.raw_text for g in slate.games if index.best_match(g.away, g.home)[0] is None
        ]
        assert unmatched == []

    def test_pool_lines_agree_with_espn(self, slate, week1_2026):
        """A misread decimal would show up here as a large disagreement."""
        from football_pool.espn import parse_games

        index = WeekIndex(week1_2026)
        by_id = {g["espn_id"]: g for g in parse_games(week1_2026)}

        checked = 0
        for game in slate.games:
            if game.is_tiebreaker or game.favored == "unknown":
                continue
            match, _ = index.best_match(game.away, game.home)
            espn_spread = by_id[match.espn_id]["espn_spread"]
            if espn_spread is None:
                continue  # ESPN drops odds after a game goes final
            pool_line = to_home_relative(game.favored, game.spread_magnitude)
            assert abs(pool_line - espn_spread) <= 3.0, game.raw_text
            checked += 1
        # Guard against the check silently becoming vacuous if the fixture
        # loses its backfilled closing lines.
        assert checked >= 14


@pytest.mark.live
class TestLiveParse:
    def test_model_reads_the_real_screenshots(self, slate):
        """Re-parse the actual images and compare to the golden fixture.

        Skipped unless --live is passed and screenshots are present, because it
        costs an API call.
        """
        from football_pool import slate_import

        try:
            parsed, _, _ = slate_import.parse_slate(2026, 1, use_cache=False)
        except slate_import.SlateImportError as exc:
            pytest.skip(str(exc))

        expected = {(g.away, g.home, g.favored, g.spread_magnitude) for g in slate.games}
        actual = {(g.away, g.home, g.favored, g.spread_magnitude) for g in parsed.games}
        assert actual == expected
