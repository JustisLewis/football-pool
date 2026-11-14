"""Compute the final-margin frequency table from ESPN scoreboards.

One-off tool, not part of the CLI. Prints a paste-ready literal for
`football_pool.margins.MARGIN_FREQUENCY`.

Only final scores are needed, so this reads the scoreboard endpoint once per
week -- no per-game odds calls, which is what keeps the run to minutes. The
payloads are large and `fetch_scoreboard` caches completed weeks permanently,
so anything downloaded here is deleted again on the way out.

    uv run python scripts/build_margins.py
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from football_pool import espn  # noqa: E402

SEASONS = range(2021, 2026)  # the transfer-portal / NIL era
WEEKS = range(0, 17)  # week 0 exists in some seasons; out-of-range is skipped
MAX_MARGIN = 28  # past here the tail is noise, and `.get` returns 0.0 anyway


def collect() -> tuple[collections.Counter, int]:
    """Tally absolute final margins across the seasons, week by week."""
    margins: collections.Counter[int] = collections.Counter()
    games = 0

    for season in SEASONS:
        season_games = 0
        for week in WEEKS:
            try:
                payload = espn.fetch_scoreboard(season, week)
            except espn.EspnError as exc:
                print(f"  {season} week {week}: {exc}", file=sys.stderr)
                continue

            # ESPN answers an out-of-range week with whatever it considers
            # current, so a payload that disagrees with the request is not
            # this week's data and must not be counted.
            got_season = payload.get("season", {}).get("year")
            got_week = payload.get("week", {}).get("number")
            if (got_season, got_week) != (season, week):
                continue

            for game in espn.parse_games(payload):
                if game["status"] != "STATUS_FINAL":
                    continue
                if game["home_score"] is None or game["away_score"] is None:
                    continue
                margins[abs(game["home_score"] - game["away_score"])] += 1
                season_games += 1

        print(f"  {season}: {season_games} games", file=sys.stderr)
        games += season_games

    return margins, games


def main() -> int:
    before = {p.name for p in espn.CACHE_DIR.glob("*.json")} if espn.CACHE_DIR.exists() else set()

    print(f"Fetching {SEASONS.start}-{SEASONS.stop - 1}...", file=sys.stderr)
    margins, games = collect()
    if not games:
        print("no games collected", file=sys.stderr)
        return 1

    # Delete only what this run downloaded; the pool's own weeks stay put.
    removed = 0
    for season in SEASONS:
        for week in WEEKS:
            path = espn._cache_path(season, week, all_divisions=False)
            if path.name not in before and path.exists():
                path.unlink()
                removed += 1
    print(f"pruned {removed} cached payloads", file=sys.stderr)

    print(f"\n# Computed from {games:,} FBS regular-season finals, "
          f"{SEASONS.start}-{SEASONS.stop - 1}.")
    print("MARGIN_FREQUENCY = {")
    for m in range(0, MAX_MARGIN + 1):
        share = margins[m] / games
        note = "  # no ties in college football" if m == 0 else ""
        print(f"    {m}: {share:.4f},{note}")
    print("}")

    tail = sum(c for m, c in margins.items() if m > MAX_MARGIN) / games
    print(f"\n# tail beyond {MAX_MARGIN}: {tail:.1%} of games", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
