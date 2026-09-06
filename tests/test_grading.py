import pytest

from football_pool.grading import (
    Result,
    Side,
    cover,
    cover_margin,
    format_line,
    grade,
    pick_relative_line,
    to_home_relative,
)

FINAL = "STATUS_FINAL"


class TestCover:
    def test_home_favorite_covers(self):
        # Home favored by 6.5, wins by 10 -> home covers by 3.5
        c = cover(31, 21, -6.5)
        assert c.side is Side.HOME
        assert c.margin == pytest.approx(3.5)

    def test_home_favorite_wins_but_fails_to_cover(self):
        # Home favored by 10, wins by only 3 -> away covers
        c = cover(24, 21, -10)
        assert c.side is Side.AWAY
        assert c.margin == pytest.approx(7)

    def test_away_favorite_covers(self):
        # Away favored by 3 (home +3), away wins by 14
        c = cover(10, 24, 3)
        assert c.side is Side.AWAY
        assert c.margin == pytest.approx(11)

    def test_home_underdog_covers_by_losing_close(self):
        # Home +7, loses by 3 -> home covers by 4
        c = cover(21, 24, 7)
        assert c.side is Side.HOME
        assert c.margin == pytest.approx(4)

    def test_exact_push_on_integer_line(self):
        # Home favored by 7, wins by exactly 7
        c = cover(28, 21, -7)
        assert c.side is None
        assert c.margin == 0

    def test_pickem_decided_by_winner(self):
        assert cover(21, 20, 0).side is Side.HOME
        assert cover(20, 21, 0).side is Side.AWAY


class TestGrade:
    def test_correct_pick_wins(self):
        assert grade(Side.HOME, -6.5, FINAL, 31, 21) is Result.WIN

    def test_wrong_pick_loses(self):
        assert grade(Side.AWAY, -6.5, FINAL, 31, 21) is Result.LOSS

    def test_push_regardless_of_side(self):
        assert grade(Side.HOME, -7, FINAL, 28, 21) is Result.PUSH
        assert grade(Side.AWAY, -7, FINAL, 28, 21) is Result.PUSH

    def test_half_point_line_never_pushes(self):
        # The .5 hook: 7-point win against -7.5 is a loss for the favorite
        assert grade(Side.HOME, -7.5, FINAL, 28, 21) is Result.LOSS
        assert grade(Side.AWAY, -7.5, FINAL, 28, 21) is Result.WIN

    @pytest.mark.parametrize("status", ["STATUS_CANCELED", "STATUS_POSTPONED"])
    def test_canceled_and_postponed_are_void_not_losses(self, status):
        assert grade(Side.HOME, -6.5, status, None, None) is Result.VOID

    @pytest.mark.parametrize(
        "status", ["STATUS_SCHEDULED", "STATUS_IN_PROGRESS", "STATUS_HALFTIME"]
    )
    def test_unfinished_games_are_pending(self, status):
        assert grade(Side.HOME, -6.5, status, None, None) is Result.PENDING

    def test_final_without_scores_is_pending_not_a_crash(self):
        assert grade(Side.HOME, -6.5, FINAL, None, None) is Result.PENDING

    def test_accepts_plain_strings_for_side(self):
        assert grade("home", -6.5, FINAL, 31, 21) is Result.WIN


class TestCoverMargin:
    def test_positive_when_pick_covers(self):
        assert cover_margin(Side.HOME, -6.5, 31, 21) == pytest.approx(3.5)

    def test_negative_when_pick_misses(self):
        assert cover_margin(Side.AWAY, -6.5, 31, 21) == pytest.approx(-3.5)

    def test_zero_on_push(self):
        assert cover_margin(Side.HOME, -7, 28, 21) == 0


class TestFormatting:
    def test_home_favorite_renders_with_home_abbr(self):
        assert format_line(-6.5, "TCU", "UNC") == "TCU -6.5"

    def test_away_favorite_renders_with_away_abbr(self):
        assert format_line(2.5, "STAN", "MIA") == "MIA -2.5"

    def test_pickem(self):
        assert format_line(0, "TCU", "UNC") == "PK"

    def test_trims_trailing_zeros(self):
        assert format_line(-7.0, "TCU", "UNC") == "TCU -7"


class TestToHomeRelative:
    def test_home_favored_is_negative(self):
        assert to_home_relative("home", 6.5) == -6.5

    def test_away_favored_is_positive(self):
        assert to_home_relative("away", 2.5) == 2.5

    def test_unknown_is_rejected(self):
        with pytest.raises(ValueError):
            to_home_relative("unknown", 11.5)

    def test_negative_magnitude_is_rejected(self):
        with pytest.raises(ValueError):
            to_home_relative("home", -6.5)


class TestPickRelativeLine:
    def test_taking_the_favorite_is_a_minus(self):
        # Home favored by 6.5, and we took the home team
        assert pick_relative_line(Side.HOME, -6.5) == -6.5

    def test_taking_the_underdog_is_a_plus(self):
        # Home favored by 9.5, and we took the away underdog
        assert pick_relative_line(Side.AWAY, -9.5) == 9.5

    def test_away_favorite_taken_is_a_minus(self):
        # Away favored by 2.5 (home-relative +2.5), and we took the away team
        assert pick_relative_line(Side.AWAY, 2.5) == -2.5
