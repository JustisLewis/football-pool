import pytest

from football_pool.matching import WeekIndex, normalize

# Exactly as the commissioner wrote them in the Week One screenshot.
WEEK1_SLATE = [
    ("Colorado", "Georgia Tech", "COLO", "GT"),
    ("Toledo", "Michigan State", "TOL", "MSU"),
    ("East Carolina", "Alabama", "ECU", "ALA"),
    ("Oregon State", "Houston", "ORST", "HOU"),
    ("Ohio", "Nebraska", "OHIO", "NEB"),
    ("Miami (OH)", "Pittsburgh", "M-OH", "PITT"),
    ("Tenn St", "Georgia", "TNST", "UGA"),
    ("Boston College", "Cincinnati", "BC", "CIN"),
    ("Tulane", "Duke", "TULN", "DUKE"),
    ("Baylor", "Auburn", "BAY", "AUB"),
    ("Arkansas St", "Memphis", "ARST", "MEM"),
    ("Clemson", "LSU", "CLEM", "LSU"),
    ("UNLV", "Hawaii", "UNLV", "HAW"),
    ("UCLA", "California", "UCLA", "CAL"),
    ("Louisville", "Ole Miss", "LOU", "MISS"),
    ("Furman", "Tennessee", "FUR", "TENN"),
]


class TestNormalize:
    def test_lowercases_and_strips_punctuation(self):
        assert normalize("Georgia Tech") == "georgia tech"

    def test_expands_st_to_state(self):
        assert normalize("Arkansas St") == "arkansas state"

    def test_keeps_parenthetical_disambiguator(self):
        # Miami (OH) and Miami (FL) are different schools -- the parenthetical
        # is the only thing separating them.
        assert normalize("Miami (OH)") != normalize("Miami (FL)")
        assert "oh" in normalize("Miami (OH)").split()


class TestWeekIndex:
    @pytest.mark.parametrize("away,home,away_abbr,home_abbr", WEEK1_SLATE)
    def test_every_slate_line_matches(self, week1_2026, away, home, away_abbr, home_abbr):
        index = WeekIndex(week1_2026)
        match, _ = index.best_match(away, home)
        assert match is not None, f"{away} @ {home} did not match"
        assert match.away.abbreviation == away_abbr
        assert match.home.abbreviation == home_abbr

    def test_tennessee_state_is_not_tennessee(self, week1_2026):
        """The headline trap: both programs are on this slate."""
        index = WeekIndex(week1_2026)
        tnst, _ = index.best_match("Tenn St", "Georgia")
        tenn, _ = index.best_match("Furman", "Tennessee")
        assert tnst.away.abbreviation == "TNST"
        assert tenn.home.abbreviation == "TENN"
        assert tnst.espn_id != tenn.espn_id

    def test_unknown_team_matches_nothing(self, week1_2026):
        index = WeekIndex(week1_2026)
        match, candidates = index.best_match("Hogwarts", "Narnia")
        assert match is None
        assert candidates == []

    def test_reversed_home_and_away_does_not_match(self, week1_2026):
        """Order matters: the pool writes Away @ Home."""
        index = WeekIndex(week1_2026)
        match, _ = index.best_match("Georgia Tech", "Colorado")
        assert match is None

    def test_saved_alias_resolves_a_name_fuzzy_matching_would_miss(self, week1_2026):
        index = WeekIndex(week1_2026)
        assert index.best_match("The Ramblin Wreck", "Colorado")[0] is None

        gt = next(t for t in index.teams.values() if t.abbreviation == "GT")
        aliased = WeekIndex(week1_2026, aliases={"the ramblin wreck": gt.id})
        # Alias is on the home side: Colorado @ Georgia Tech
        match, _ = aliased.best_match("Colorado", "The Ramblin Wreck")
        assert match is not None and match.home.abbreviation == "GT"
