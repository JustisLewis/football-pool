"""The key-number scoring that turns a line gap into a ranking."""

import pytest

from football_pool.margins import MARGIN_FREQUENCY, edge, format_edge, margin_frequency


class TestMarginFrequency:
    def test_is_two_sided(self):
        """The table is keyed on absolute margin, so a home win by 3 and an
        away win by 3 read the same."""
        assert margin_frequency(-3) == margin_frequency(3)

    def test_ties_are_worth_nothing(self):
        assert margin_frequency(0) == 0.0

    def test_past_the_table_is_zero_not_an_error(self):
        assert margin_frequency(70) == 0.0


class TestTableSanity:
    def test_no_negative_frequencies(self):
        assert all(v >= 0 for v in MARGIN_FREQUENCY.values())

    def test_frequencies_are_a_share_of_all_games(self):
        # Well under 1.0: the margins past the table's tail make up the rest.
        assert sum(MARGIN_FREQUENCY.values()) <= 1.0

    def test_keys_are_contiguous_from_zero(self):
        assert sorted(MARGIN_FREQUENCY) == list(range(len(MARGIN_FREQUENCY)))

    def test_the_key_numbers_are_the_peaks(self):
        """3 and 7 are where football scores land. If a rebuilt table ever
        stops showing that, it was built wrong."""
        assert MARGIN_FREQUENCY[3] == max(MARGIN_FREQUENCY.values())
        assert MARGIN_FREQUENCY[7] > MARGIN_FREQUENCY[10]
        assert MARGIN_FREQUENCY[7] > MARGIN_FREQUENCY[6]
        assert MARGIN_FREQUENCY[3] > MARGIN_FREQUENCY[2]


class TestEdge:
    def test_matching_lines_have_no_edge(self):
        assert edge(-7.5, -7.5) == 0.0

    def test_a_half_point_is_worth_its_key_number(self):
        assert edge(-7.5, -7.0) == pytest.approx(MARGIN_FREQUENCY[7])

    def test_buying_only_the_tie_is_worth_nothing(self):
        """A pickem shifted half a point crosses margin 0, which never happens."""
        assert edge(0.5, 0.0) == 0.0

    def test_half_a_point_on_3_beats_a_full_point_on_15(self):
        """The whole point of the feature. The bigger gap is the weaker edge:
        3 is the most common margin in football and 15 is a trough, so drift
        has to be scored by where it lands, not by how many points it is."""
        assert edge(-3.5, -3.0) > edge(-15.5, -14.5)

    def test_a_wider_gap_over_key_numbers_accumulates(self):
        assert edge(-7.0, -6.0) == pytest.approx(
            MARGIN_FREQUENCY[6] + MARGIN_FREQUENCY[7]
        )


class TestFormatEdge:
    def test_names_the_away_team_when_the_pool_line_favors_it(self):
        assert format_edge(-7.5, -7.0, "TROY", "SHSU").startswith("SHSU ")

    def test_names_the_home_team_when_the_pool_line_favors_it(self):
        assert format_edge(5.5, 5.0, "ODU", "LIB").startswith("ODU ")

    def test_renders_one_decimal_of_percent(self):
        assert format_edge(-7.5, -7.0, "TROY", "SHSU") == "SHSU 7.9%"

    def test_matching_lines_read_as_even(self):
        assert format_edge(-3.5, -3.5, "BUFF", "CONN") == "even"
