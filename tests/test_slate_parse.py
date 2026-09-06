"""Golden test for reading a pick sheet.

The fixture is a sample sheet in the format the pool uses, built from real
games. It deliberately does NOT contain a real pool's slate -- the sheet is the
commissioner's to share, not this repo's to publish.

It pins the three format rules that are easy to get wrong: the parenthetical
attaching to the favored team by position, the unsigned line that must not be
guessed, and the total-points tiebreaker.

Run against the live API with:  uv run pytest --live
"""

from pathlib import Path

import pytest

from football_pool.grading import to_home_relative
from football_pool.matching import WeekIndex
from football_pool.slate_import import ParsedSlate

FIXTURE = Path(__file__).parent / "fixtures" / "slate-sample.json"


@pytest.fixture
def slate() -> ParsedSlate:
    return ParsedSlate.model_validate_json(FIXTURE.read_text())


def find(slate: ParsedSlate, away: str):
    return next(g for g in slate.games if g.away == away)


class TestSlateShape:
    def test_reads_the_week_heading(self, slate):
        assert slate.week_label == "SAMPLE WEEK"

    def test_spread_games_and_one_tiebreaker(self, slate):
        spread_games = [g for g in slate.games if not g.is_tiebreaker]
        tiebreakers = [g for g in slate.games if g.is_tiebreaker]
        assert len(spread_games) == 11
        assert len(tiebreakers) == 1

    def test_team_names_are_kept_verbatim(self, slate):
        """Shorthand must survive the parse; matching expands it later."""
        names = {g.away for g in slate.games}
        assert {"Wash St", "Miami (FL)", "Oklahoma St"} <= names


class TestPositionalRule:
    def test_paren_after_home_team_means_home_favored(self, slate):
        game = find(slate, "North Carolina")  # North Carolina @ TCU (-7.5)
        assert game.favored == "home"
        assert game.spread_magnitude == 7.5
        assert to_home_relative(game.favored, game.spread_magnitude) == -7.5

    def test_paren_after_away_team_means_away_favored(self, slate):
        game = find(slate, "Miami (FL)")  # Miami (FL) (-24.5) @ Stanford
        assert game.favored == "away"
        assert game.spread_magnitude == 24.5
        # Away favored -> home-relative line is positive (home is the dog)
        assert to_home_relative(game.favored, game.spread_magnitude) == 24.5

    def test_second_away_favorite_on_the_same_slate(self, slate):
        game = find(slate, "Western Kentucky")  # Western Kentucky (-1.5) @ Nevada
        assert game.favored == "away"
        assert to_home_relative(game.favored, game.spread_magnitude) == 1.5

    def test_a_name_parenthetical_is_not_read_as_a_spread(self, slate):
        """`Miami (FL)` has two parentheticals; only one is the line."""
        game = find(slate, "Miami (FL)")
        assert game.home == "Stanford"
        assert game.spread_magnitude == 24.5


class TestAmbiguity:
    def test_unsigned_line_is_flagged_not_guessed(self, slate):
        game = find(slate, "Jacksonville St")  # ... @ North Dakota St (6.5)
        assert game.favored == "unknown"
        assert game.spread_magnitude == 6.5

    def test_unknown_cannot_be_converted_to_a_line(self, slate):
        """The type system refuses to invent a direction."""
        game = find(slate, "Jacksonville St")
        with pytest.raises(ValueError):
            to_home_relative(game.favored, game.spread_magnitude)

    def test_tiebreaker_has_no_spread(self, slate):
        game = find(slate, "Akron")  # Total Points - Akron @ Wake Forest
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

    def test_shorthand_resolves_to_the_right_school(self, slate, week1_2026):
        """`Wash St` is Washington State, and Washington is a different team
        playing in the same game."""
        index = WeekIndex(week1_2026)
        match, _ = index.best_match("Wash St", "Washington")
        assert match.away.abbreviation == "WSU"
        assert match.home.abbreviation == "WASH"

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
                continue
            pool_line = to_home_relative(game.favored, game.spread_magnitude)
            assert abs(pool_line - espn_spread) <= 3.0, game.raw_text
            checked += 1
        # Guard against the check silently becoming vacuous.
        assert checked >= 9


@pytest.mark.live
class TestLiveParse:
    def test_model_reads_real_screenshots(self):
        """Parse whatever screenshots are in slates/, and sanity-check the shape.

        Skipped unless --live is passed, because it costs an API call. This
        asserts structure rather than specific games, so it works with whatever
        week's sheet happens to be on disk.
        """
        from football_pool import slate_import

        try:
            parsed, _, _ = slate_import.parse_slate(2026, 1, use_cache=False)
        except slate_import.SlateImportError as exc:
            pytest.skip(str(exc))

        assert parsed.games, "no games parsed"
        for game in parsed.games:
            assert game.away and game.home
            assert game.favored in ("home", "away", "unknown")
            if game.favored in ("home", "away"):
                assert game.spread_magnitude is not None
                assert game.spread_magnitude > 0
            if game.is_tiebreaker:
                assert game.spread_magnitude is None
