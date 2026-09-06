"""ESPN college football client.

Two endpoints are used:

* the public scoreboard, for the slate, scores and status;
* the core API odds endpoint, as a fallback for lines.

The distinction matters: the scoreboard drops the `odds` block once a game goes
final, while the core API keeps the closing line indefinitely. Scores come from
the first, lines are backfilled from the second.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
CORE_ODDS = (
    "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football"
    "/events/{eid}/competitions/{eid}/odds"
)
FBS_GROUP = "80"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
LIVE_TTL_SECONDS = 120


class EspnError(RuntimeError):
    """A fetch failed. Callers fall back to stored data rather than losing it."""


def _get(url: str, timeout: float = 20.0) -> dict:
    # Deliberately no User-Agent header. ESPN 403s a custom UA *and* a
    # browser-spoofing one, but serves urllib's default fine. Don't add one.
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        raise EspnError(f"could not reach ESPN ({url}): {exc}") from exc
    except json.JSONDecodeError as exc:
        raise EspnError(f"ESPN returned malformed JSON ({url}): {exc}") from exc


def _cache_path(season: int, week: int, all_divisions: bool) -> Path:
    suffix = "-all" if all_divisions else ""
    return CACHE_DIR / f"{season}-w{week:02d}{suffix}.json"


def _all_final(payload: dict) -> bool:
    events = payload.get("events") or []
    return bool(events) and all(
        e["status"]["type"]["name"] in {"STATUS_FINAL", "STATUS_CANCELED"} for e in events
    )


def fetch_scoreboard(
    season: int | None = None,
    week: int | None = None,
    all_divisions: bool = False,
    use_cache: bool = True,
) -> dict:
    """Fetch one week's scoreboard.

    With no season/week, ESPN returns the current week -- that is how the tool
    figures out what "this week" means. Completed weeks are cached permanently;
    in-flight weeks get a short TTL.
    """
    params = [f"limit=400"]
    if not all_divisions:
        params.append(f"groups={FBS_GROUP}")
    if season and week:
        params.extend([f"dates={season}", "seasontype=2", f"week={week}"])
        cache = _cache_path(season, week, all_divisions)
        if use_cache and cache.exists():
            cached = json.loads(cache.read_text())
            fresh_enough = (time.time() - cache.stat().st_mtime) < LIVE_TTL_SECONDS
            if _all_final(cached) or fresh_enough:
                return cached

    payload = _get(f"{SCOREBOARD}?{'&'.join(params)}")

    resolved_season = payload.get("season", {}).get("year", season)
    resolved_week = payload.get("week", {}).get("number", week)
    if resolved_season and resolved_week:
        cache = _cache_path(resolved_season, resolved_week, all_divisions)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload))
    return payload


def current_week(all_divisions: bool = False) -> tuple[int, int]:
    """(season, week) that ESPN considers current."""
    payload = fetch_scoreboard(all_divisions=all_divisions, use_cache=False)
    return payload["season"]["year"], payload["week"]["number"]


def _pick_odds(odds_items: list[dict]) -> dict | None:
    """Choose the most representative odds entry.

    Prefers a settled book line over an in-play "Live Odds" feed, which drifts
    during the game and is not what anyone's pool settles on.
    """
    usable = [o for o in odds_items or [] if o.get("spread") is not None]
    if not usable:
        return None
    settled = [o for o in usable if "live" not in _provider_name(o).lower()]
    return (settled or usable)[0]


def _provider_name(odds: dict) -> str:
    provider = odds.get("provider") or {}
    return provider.get("name") or ""


def parse_games(payload: dict) -> list[dict]:
    """Flatten an ESPN scoreboard payload into rows for the games table."""
    season = payload.get("season", {}).get("year")
    week = payload.get("week", {}).get("number")
    games: list[dict] = []

    for event in payload.get("events", []):
        comp = event["competitions"][0]
        sides = {c["homeAway"]: c for c in comp["competitors"]}
        home, away = sides.get("home"), sides.get("away")
        if not home or not away:
            continue  # malformed event; nothing useful to store

        odds = _pick_odds(comp.get("odds") or [])
        games.append(
            {
                "espn_id": event["id"],
                "season": season,
                "week": week,
                "kickoff_utc": event.get("date"),
                "home_id": home["team"]["id"],
                "home_abbr": home["team"].get("abbreviation"),
                "home_name": home["team"].get("displayName"),
                "away_id": away["team"]["id"],
                "away_abbr": away["team"].get("abbreviation"),
                "away_name": away["team"].get("displayName"),
                "neutral_site": int(bool(comp.get("neutralSite"))),
                "status": event["status"]["type"]["name"],
                "home_score": _int_or_none(home.get("score")),
                "away_score": _int_or_none(away.get("score")),
                # ESPN signs `spread` relative to the home team, which is the
                # same convention used throughout this project.
                "espn_spread": odds.get("spread") if odds else None,
            }
        )
    return games


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_closing_line(espn_id: str, pause: float = 0.3) -> float | None:
    """Home-relative closing line for one game, or None if ESPN has none.

    This survives the game going final, so it works as a late cross-check.
    """
    time.sleep(pause)  # be polite; this is called in a loop
    try:
        payload = _get(CORE_ODDS.format(eid=espn_id))
    except EspnError:
        return None
    odds = _pick_odds(payload.get("items") or [])
    return odds.get("spread") if odds else None


def teams_in_payload(payload: dict) -> list[dict]:
    """Every team appearing in a week, with the name fields used for matching."""
    teams: dict[str, dict] = {}
    for event in payload.get("events", []):
        for competitor in event["competitions"][0]["competitors"]:
            team = competitor["team"]
            teams[team["id"]] = {
                "id": team["id"],
                "abbreviation": team.get("abbreviation"),
                "displayName": team.get("displayName"),
                "shortDisplayName": team.get("shortDisplayName"),
                "location": team.get("location"),
                "name": team.get("name"),
            }
    return list(teams.values())
