import pytest

from football_pool.matching import WeekIndex, normalize

# Sample sheet lines in the shorthand a commissioner typically uses. These are
# deliberately not a real pool's slate.
WEEK1_SLATE = [
    ("North Carolina", "TCU", "UNC", "TCU"),
    ("NC State", "Virginia", "NCSU", "UVA"),
    ("San Jose St", "USC", "SJSU", "USC"),
    ("Wash St", "Washington", "WSU", "WASH"),
    ("Miami (FL)", "Stanford", "MIA", "STAN"),
    ("Oklahoma St", "Tulsa", "OKST", "TLSA"),
    ("Western Kentucky", "Nevada", "WKU", "NEV"),
    ("Jacksonville St", "North Dakota St", "JVST", "NDSU"),
    ("Wisconsin", "Notre Dame", "WIS", "ND"),
    ("UAB", "Illinois", "UAB", "ILL"),
    ("UMass", "Rutgers", "MASS", "RUTG"),
    ("Akron", "Wake Forest", "AKR", "WAKE"),
]


class TestNormalize:
    def test_lowercases_and_strips_punctuation(self):
        assert normalize("Notre Dame") == "notre dame"

    def test_expands_st_to_state(self):
        assert normalize("Oklahoma St") == "oklahoma state"

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

    def test_washington_state_is_not_washington(self, week1_2026):
        """The headline trap: both programs are in the same game."""
        index = WeekIndex(week1_2026)
        match, _ = index.best_match("Wash St", "Washington")
        assert match.away.abbreviation == "WSU"
        assert match.home.abbreviation == "WASH"

    def test_ambiguous_pairing_is_refused_rather_than_guessed(self, week1_2026):
        """`Idaho @ Utah` and `Idaho State @ Utah State` are both real games
        that week. Neither is clearly the better fit, so the matcher declines
        and leaves it to a human."""
        index = WeekIndex(week1_2026)
        match, candidates = index.best_match("Idaho", "Utah")
        assert match is None
        assert len(candidates) > 1

    def test_unknown_team_matches_nothing(self, week1_2026):
        index = WeekIndex(week1_2026)
        match, candidates = index.best_match("Hogwarts", "Narnia")
        assert match is None
        assert candidates == []

    def test_reversed_home_and_away_does_not_match(self, week1_2026):
        """Order matters: the pool writes Away @ Home."""
        index = WeekIndex(week1_2026)
        match, _ = index.best_match("TCU", "North Carolina")
        assert match is None

    def test_saved_alias_resolves_a_name_fuzzy_matching_would_miss(self, week1_2026):
        index = WeekIndex(week1_2026)
        assert index.best_match("North Carolina", "Fort Worth")[0] is None

        tcu = next(t for t in index.teams.values() if t.abbreviation == "TCU")
        aliased = WeekIndex(week1_2026, aliases={"fort worth": tcu.id})
        # Alias is on the home side: North Carolina @ TCU
        match, _ = aliased.best_match("North Carolina", "Fort Worth")
        assert match is not None and match.home.abbreviation == "TCU"
