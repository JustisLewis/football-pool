"""How often college football games land on each final margin. Approximate.

Used to score the gap between the pool's line and the market's: the value of
that gap is not how many points it is, but how often the margins it spans
actually occur. Half a point across 3 is worth more than a full point across
15, because 3 is where football scores land and 15 is not.

Three caveats, none of which the table can fix:

* It is a sample, not a law. The counts come from the seasons named below and
  carry roughly half a point of error on the larger entries.
* It is unconditional. The real distribution depends on the spread -- a game
  lined at -3 puts far more mass on a 3-point margin than one lined at -24 --
  so a single table understates the edge on near-pickems and overstates it on
  blowout lines.
* It is two-sided while a pick is one-sided. These are frequencies of
  |margin|, lumping "favorite by k" together with "underdog by k", so they can
  overstate the chance of the specific result a given ticket needs.

So this ranks games against each other. It is not a price, and not a win
probability. Rebuild the table with `scripts/build_margins.py`.
"""

from __future__ import annotations

from .grading import Side, crossed_margins, drift_beneficiary

# Computed from 4,332 FBS regular-season finals, 2021-2025 (the transfer-portal
# era), via scripts/build_margins.py. Bowls and playoffs are excluded, since
# ESPN's scoreboard splits them into a separate season type.
#
# The peaks are the scoring combinations: 3 a field goal, 7 a touchdown, 10 and
# 14 and 17 and 21 their sums. The troughs -- 9, 12, 16 -- are the margins no
# tidy sequence of scores produces. Entries stop at 28; past there the tail is
# 22.5% of games spread thinly, and no plausible line drift reaches it.
MARGIN_FREQUENCY = {
    0: 0.0000,  # college football has no regulation ties
    1: 0.0298,
    2: 0.0295,
    3: 0.0976,
    4: 0.0325,
    5: 0.0212,
    6: 0.0302,
    7: 0.0792,
    8: 0.0270,
    9: 0.0118,
    10: 0.0432,
    11: 0.0203,
    12: 0.0152,
    13: 0.0169,
    14: 0.0386,
    15: 0.0159,
    16: 0.0108,
    17: 0.0390,
    18: 0.0226,
    19: 0.0145,
    20: 0.0173,
    21: 0.0365,
    22: 0.0139,
    23: 0.0122,
    24: 0.0268,
    25: 0.0169,
    26: 0.0099,
    27: 0.0166,
    28: 0.0289,
}


def margin_frequency(margin: int) -> float:
    """How often a game ends this many points apart, either direction."""
    return MARGIN_FREQUENCY.get(abs(margin), 0.0)


def edge(pool_line: float, espn_spread: float) -> float:
    """Share of final margins on which the pool's line beats the market's.

    In other words, how often the difference between the two numbers actually
    changes the result. The rest of the time both tickets land the same way.
    """
    return sum(margin_frequency(m) for m in crossed_margins(pool_line, espn_spread))


def format_edge(
    pool_line: float, espn_spread: float, home_abbr: str, away_abbr: str
) -> str:
    """Render the edge as the side it favors and how often it pays: 'SHSU 7.9%'."""
    side = drift_beneficiary(pool_line, espn_spread)
    if side is None:
        return "even"
    abbr = home_abbr if side is Side.HOME else away_abbr
    return f"{abbr} {edge(pool_line, espn_spread):.1%}"
