"""End-to-end run over a completed week (2025 week 5).

Drives the whole path -- ESPN payload -> games -> slate -> picks -> grades ->
season report -> dashboard -- against real scores and real closing lines, with
results worked out by hand in the comments.
"""

import pytest

from football_pool import dashboard, db, report
from football_pool.espn import parse_games
from football_pool.grading import Result

SEASON, WEEK = 2025, 5

# Real games, with the arithmetic spelled out. `line` is home-relative.
#   ECU:   ECU won 28-6  (+22) at -3.5  -> covers by 18.5
#   ASU:   ASU won 27-24 (+3)  at -2.5  -> covers by 0.5
#   TA&M:  TA&M won 16-10 (+6) at -6.5  -> MISSES by 0.5, the hook
#   UGA:   UGA lost 21-24 (-3) at -2.5  -> Alabama covers by 5.5
#   PSU:   PSU lost 24-30 (-6) at -4.5  -> Oregon covers by 10.5
GAMES = [
    # espn_id,     line,  pick,   expected result, expected margin
    ("401762471", -3.5, "home", Result.WIN, 18.5),   # took ECU, the favorite
    ("401756902", -2.5, "home", Result.WIN, 0.5),    # took ASU, covered on the hook
    ("401752724", -6.5, "home", Result.LOSS, -0.5),  # took TA&M, lost on the hook
    ("401752718", -2.5, "away", Result.WIN, 5.5),    # took Alabama, the underdog
    ("401752854", -4.5, "home", Result.LOSS, -10.5), # took Penn State, buried
]


@pytest.fixture
def loaded(db_conn, week5_2025):
    """A database with the week's games, a slate, and picks recorded."""
    db.upsert_games(db_conn, parse_games(week5_2025))

    slate = [
        {
            "season": SEASON,
            "week": WEEK,
            "espn_id": espn_id,
            "slot": slot,
            "pool_line": line,
            "is_tiebreaker": 0,
            "raw_text": f"game {slot}",
            "source_image": None,
        }
        for slot, (espn_id, line, _, _, _) in enumerate(GAMES, start=1)
    ]
    db.replace_slate(db_conn, SEASON, WEEK, slate)

    for espn_id, line, side, _, _ in GAMES:
        game = db.get_game(db_conn, espn_id)
        abbr = game["home_abbr"] if side == "home" else game["away_abbr"]
        db.save_pick(db_conn, SEASON, WEEK, espn_id, side, abbr, line)
    return db_conn


class TestGradesRealGames:
    @pytest.mark.parametrize("espn_id,line,side,expected,margin", GAMES)
    def test_each_pick_grades_as_hand_calculated(
        self, loaded, espn_id, line, side, expected, margin
    ):
        picks = {p.espn_id: p for p in report.graded_picks(loaded, SEASON)}
        pick = picks[espn_id]
        assert pick.result is expected
        assert pick.margin == pytest.approx(margin)

    def test_season_record_adds_up(self, loaded):
        record = report.tally(report.graded_picks(loaded, SEASON))
        assert (record.wins, record.losses, record.pushes) == (3, 2, 0)
        assert record.pct == pytest.approx(0.6)

    def test_the_two_hook_games_land_on_opposite_sides(self, loaded):
        """Half a point is the whole difference between these two picks."""
        picks = {p.espn_id: p for p in report.graded_picks(loaded, SEASON)}
        assert picks["401756902"].result is Result.WIN   # covered by 0.5
        assert picks["401752724"].result is Result.LOSS  # missed by 0.5


class TestPoolLineGoverns:
    def test_a_different_pool_line_flips_the_result(self, loaded, week5_2025):
        """ESPN had ASU -2.5 and the pick covered by half a point. On a
        commissioner's line of -4.5 the same game is a loss -- the stored pool
        line is what settles it, not ESPN's."""
        db.save_pick(loaded, SEASON, WEEK, "401756902", "home", "ASU", -4.5)
        picks = {p.espn_id: p for p in report.graded_picks(loaded, SEASON)}
        assert picks["401756902"].result is Result.LOSS
        assert picks["401756902"].margin == pytest.approx(-1.5)

        # ESPN's own number is still on record for cross-checking.
        assert db.get_game(loaded, "401756902")["espn_spread"] == pytest.approx(-2.5)


class TestSplits:
    def test_favorite_and_underdog_picks_are_classified(self, loaded):
        picks = report.graded_picks(loaded, SEASON)
        by_id = {p.espn_id: p for p in picks}
        assert by_id["401762471"].on_favorite is True   # took ECU -3.5
        assert by_id["401752718"].on_favorite is False  # took Alabama +2.5

        splits = report.splits(picks)
        assert splits["favorites"].decided + splits["underdogs"].decided == 5


class TestDashboardRenders:
    def test_page_contains_the_graded_numbers(self, loaded):
        html = dashboard.render(loaded, SEASON, WEEK)
        assert "<title>Cover Margin</title>" in html
        assert "3&ndash;2" in html  # season record
        assert "+18.5" in html  # ECU cover margin
        assert "-0.5" in html  # the hook loss
        # Both themes must define the palette, not just one.
        assert ":root {" in html and 'prefers-color-scheme: dark' in html

    def test_sample_banner_only_appears_when_asked(self, loaded):
        assert "Sample picks" not in dashboard.render(loaded, SEASON, WEEK)
        assert "Sample picks" in dashboard.render(loaded, SEASON, WEEK, sample=True)


class TestTiebreaker:
    """The total-points game is scored on the combined final, not a spread."""

    TIEBREAK_ID = "401752852"  # OSU 24 @ WASH 6 -> 30 points

    @pytest.fixture
    def with_tiebreaker(self, loaded):
        slate = [
            {
                "season": SEASON,
                "week": WEEK,
                "espn_id": self.TIEBREAK_ID,
                "slot": 99,
                "pool_line": None,
                "is_tiebreaker": 1,
                "raw_text": "Total Points - Ohio State @ Washington",
                "source_image": None,
            }
        ]
        existing = [dict(r) for r in db.slate_for_week(loaded, SEASON, WEEK)]
        rows = [
            {
                "season": SEASON,
                "week": WEEK,
                "espn_id": r["espn_id"],
                "slot": r["slot"],
                "pool_line": r["pool_line"],
                "is_tiebreaker": 0,
                "raw_text": r["raw_text"],
                "source_image": None,
            }
            for r in existing
        ]
        db.replace_slate(loaded, SEASON, WEEK, rows + slate)
        return loaded

    def test_status_is_none_before_a_prediction_is_made(self, with_tiebreaker):
        assert report.tiebreaker_status(with_tiebreaker, SEASON, WEEK) is None

    def test_prediction_is_scored_against_the_combined_total(self, with_tiebreaker):
        db.save_tiebreaker(with_tiebreaker, SEASON, WEEK, self.TIEBREAK_ID, 44)
        status = report.tiebreaker_status(with_tiebreaker, SEASON, WEEK)
        assert status["predicted"] == 44
        assert status["actual"] == 30  # 24 + 6
        assert status["diff"] == 14
        assert status["final"] is True

    def test_re_predicting_replaces_rather_than_duplicates(self, with_tiebreaker):
        db.save_tiebreaker(with_tiebreaker, SEASON, WEEK, self.TIEBREAK_ID, 44)
        db.save_tiebreaker(with_tiebreaker, SEASON, WEEK, self.TIEBREAK_ID, 31)
        assert report.tiebreaker_status(with_tiebreaker, SEASON, WEEK)["predicted"] == 31
        count = with_tiebreaker.execute(
            "SELECT COUNT(*) FROM tiebreakers WHERE season = ? AND week = ?",
            (SEASON, WEEK),
        ).fetchone()[0]
        assert count == 1

    def test_tiebreaker_is_not_counted_as_a_spread_pick(self, with_tiebreaker):
        db.save_tiebreaker(with_tiebreaker, SEASON, WEEK, self.TIEBREAK_ID, 44)
        record = report.tally(report.graded_picks(with_tiebreaker, SEASON))
        assert record.wins + record.losses == 5  # the five spread picks only

    def test_dashboard_shows_the_prediction(self, with_tiebreaker):
        db.save_tiebreaker(with_tiebreaker, SEASON, WEEK, self.TIEBREAK_ID, 44)
        html = dashboard.render(with_tiebreaker, SEASON, WEEK)
        assert "Tiebreaker" in html
        assert "off by <b>14</b>" in html
