"""Regenerate the trimmed ESPN fixtures used by the tests.

The real scoreboard payload is ~1.5MB per week; the tests only need the fields
the code actually reads. Run with: uv run python tests/make_fixture.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from football_pool import espn  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
TEAM_FIELDS = ("id", "abbreviation", "displayName", "shortDisplayName", "location", "name")


def trim(payload: dict) -> dict:
    events = []
    for event in payload.get("events", []):
        comp = event["competitions"][0]
        events.append(
            {
                "id": event["id"],
                "date": event.get("date"),
                "status": {"type": {"name": event["status"]["type"]["name"]}},
                "competitions": [
                    {
                        "neutralSite": comp.get("neutralSite", False),
                        "odds": comp.get("odds"),
                        "competitors": [
                            {
                                "homeAway": c["homeAway"],
                                "score": c.get("score"),
                                "team": {f: c["team"].get(f) for f in TEAM_FIELDS},
                            }
                            for c in comp["competitors"]
                        ],
                    }
                ],
            }
        )
    return {"season": payload["season"], "week": payload["week"], "events": events}


def backfill_odds(trimmed: dict) -> int:
    """Fill in closing lines the scoreboard omits once a game goes final.

    Makes the offline cross-check test meaningful: without this, every finished
    game has a null spread and the comparison is vacuous.
    """
    filled = 0
    for event in trimmed["events"]:
        comp = event["competitions"][0]
        if comp.get("odds"):
            continue
        spread = espn.fetch_closing_line(event["id"])
        if spread is None:
            continue
        comp["odds"] = [{"provider": {"name": "backfilled"}, "spread": spread}]
        filled += 1
    return filled


def main() -> None:
    FIXTURES.mkdir(exist_ok=True)
    for season, week in [(2026, 1), (2025, 5)]:
        payload = espn.fetch_scoreboard(season, week)
        trimmed = trim(payload)
        filled = backfill_odds(trimmed)
        out = FIXTURES / f"scoreboard-{season}-w{week:02d}.json"
        out.write_text(json.dumps(trimmed, indent=1))
        print(
            f"wrote {out.name} ({out.stat().st_size // 1024}KB, "
            f"{len(payload['events'])} events, {filled} lines backfilled)"
        )


if __name__ == "__main__":
    main()
