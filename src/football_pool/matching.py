"""Match the commissioner's shorthand team names to ESPN games.

The commissioner writes things like `Tenn St`, `Miami (OH)`, `Ole Miss` and
`Arkansas St`. These have to resolve to specific ESPN games, and some of them
are genuine traps: `Tenn St` (Tennessee State) sits one edit away from
`Tennessee`, and both can appear on the same slate.

Two things keep this safe. Matching is constrained to the games actually played
that week, which shrinks the candidate set enormously; and a game only matches
when *both* teams agree and the runner-up is clearly worse. Anything short of
that is reported as ambiguous so a human decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# Shorthand the commissioner uses that plain fuzzy matching handles badly.
EXPANSIONS = {
    "st": "state",
    "so": "southern",
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "cent": "central",
    "intl": "international",
    "univ": "university",
    "u": "university",
    "tech": "tech",
    "af": "air force",
    "miss": "mississippi",
}

# Names that must never be fuzzy-matched to each other, however close they look.
# Each frozenset is a group of mutually exclusive programs.
CONFUSABLE = [
    frozenset({"tennessee", "tennessee state"}),
    frozenset({"mississippi", "mississippi state"}),
    frozenset({"washington", "washington state"}),
    frozenset({"oregon", "oregon state"}),
    frozenset({"arizona", "arizona state"}),
    frozenset({"michigan", "michigan state"}),
    frozenset({"ohio", "ohio state"}),
    frozenset({"kansas", "kansas state"}),
    frozenset({"iowa", "iowa state"}),
    frozenset({"florida", "florida state"}),
    frozenset({"san jose state", "san diego state"}),
    frozenset({"louisiana", "louisiana tech"}),
    frozenset({"miami fl", "miami oh"}),
    frozenset({"texas", "texas state", "texas tech"}),
    frozenset({"colorado", "colorado state"}),
    frozenset({"utah", "utah state"}),
    frozenset({"boise state", "bowling green"}),
    frozenset({"alabama", "south alabama", "ualbany"}),
    frozenset({"arkansas", "arkansas state"}),
    frozenset({"georgia", "georgia state", "georgia tech", "georgia southern"}),
]

MIN_TEAM_SCORE = 0.72  # below this, a name is not considered a match at all
MIN_MARGIN = 0.08  # best candidate must beat the runner-up by this much


def normalize(text: str) -> str:
    """Fold a team name to a comparable form.

    Parentheticals are kept -- `Miami (OH)` and `Miami (FL)` are different
    schools and the parenthetical is the only thing telling them apart.
    """
    text = text.lower().strip()
    text = text.replace("&", " and ")
    text = re.sub(r"[().,'`\-/]", " ", text)
    tokens = [t for t in text.split() if t]
    tokens = [EXPANSIONS.get(t, t) for t in tokens]
    return " ".join(tokens)


def _confusable_conflict(a: str, b: str) -> bool:
    """True if these two normalized names are known-distinct programs."""
    if a == b:
        return False
    return any(a in group and b in group for group in CONFUSABLE)


@dataclass
class Team:
    id: str
    abbreviation: str | None
    display_name: str | None
    keys: set[str] = field(default_factory=set)

    @classmethod
    def from_espn(cls, raw: dict) -> "Team":
        keys = set()
        for field_name in ("displayName", "shortDisplayName", "location", "abbreviation"):
            value = raw.get(field_name)
            if value:
                keys.add(normalize(value))
        # "Georgia Tech Yellow Jackets" -> also index "georgia tech"
        location, nickname = raw.get("location"), raw.get("name")
        if location and nickname:
            keys.add(normalize(f"{location} {nickname}"))
        return cls(
            id=raw["id"],
            abbreviation=raw.get("abbreviation"),
            display_name=raw.get("displayName"),
            keys={k for k in keys if k},
        )

    def score(self, query: str) -> float:
        """Best similarity between a normalized query and any of this team's names."""
        best = 0.0
        for key in self.keys:
            if _confusable_conflict(query, key):
                continue
            if key == query:
                return 1.0
            ratio = SequenceMatcher(None, query, key).ratio()
            # A query that is a whole-word prefix of the name scores well:
            # "ole miss" vs "ole miss rebels".
            if key.startswith(query + " ") or query.startswith(key + " "):
                ratio = max(ratio, 0.93)
            best = max(best, ratio)
        return best


@dataclass
class GameCandidate:
    espn_id: str
    away: Team
    home: Team
    score: float


class WeekIndex:
    """Every team and game in one week, ready to match against."""

    def __init__(self, payload: dict, aliases: dict[str, str] | None = None):
        self.aliases = aliases or {}
        self.teams: dict[str, Team] = {}
        self.games: list[tuple[str, str, str]] = []  # (espn_id, away_id, home_id)

        for event in payload.get("events", []):
            comp = event["competitions"][0]
            sides = {c["homeAway"]: c["team"] for c in comp["competitors"]}
            if "home" not in sides or "away" not in sides:
                continue
            for raw in sides.values():
                if raw["id"] not in self.teams:
                    self.teams[raw["id"]] = Team.from_espn(raw)
            self.games.append((event["id"], sides["away"]["id"], sides["home"]["id"]))

    def _alias_id(self, name: str) -> str | None:
        return self.aliases.get(normalize(name))

    def score_team(self, name: str, team: Team) -> float:
        """How well a written name matches a specific team (1.0 for a saved alias)."""
        if self._alias_id(name) == team.id:
            return 1.0
        return team.score(normalize(name))

    def match_game(self, away_name: str, home_name: str) -> list[GameCandidate]:
        """Rank this week's games by how well they fit `away_name @ home_name`.

        Both teams must clear the floor for a game to be considered at all --
        that is what stops `Tenn St @ Georgia` from landing on a Tennessee game.
        """
        candidates: list[GameCandidate] = []
        for espn_id, away_id, home_id in self.games:
            away, home = self.teams[away_id], self.teams[home_id]
            away_score = self.score_team(away_name, away)
            home_score = self.score_team(home_name, home)
            if away_score < MIN_TEAM_SCORE or home_score < MIN_TEAM_SCORE:
                continue
            candidates.append(
                GameCandidate(espn_id, away, home, (away_score + home_score) / 2)
            )
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    def best_match(
        self, away_name: str, home_name: str
    ) -> tuple[GameCandidate | None, list[GameCandidate]]:
        """(confident match or None, all candidates).

        Returns None for the first element when nothing matched or when the top
        two candidates are too close to call -- the caller should ask a human.
        """
        candidates = self.match_game(away_name, home_name)
        if not candidates:
            return None, []
        if len(candidates) > 1 and (candidates[0].score - candidates[1].score) < MIN_MARGIN:
            return None, candidates
        return candidates[0], candidates
