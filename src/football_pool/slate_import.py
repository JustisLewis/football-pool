"""Turn the commissioner's slate screenshots into structured games and lines.

The slate arrives as one or more phone screenshots. They are sent to Claude in a
single request (so a multi-image week is read as one coherent slate) using
structured outputs, which guarantees schema-valid JSON rather than prose to
regex.

Nothing here is trusted blindly. The parse is cached, reviewed by a human before
it is written, and cross-checked against ESPN's own line.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# Structured extraction from a clean, high-contrast screenshot into a strict
# schema -- OCR with rules, not reasoning. Guarded by the review step and the
# ESPN cross-check. Change this one constant to move tiers.
MODEL = "claude-sonnet-5"

SLATES_DIR = Path(__file__).resolve().parents[2] / "slates"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

PROMPT = """\
These images are a college football pool's weekly pick sheet. Extract every game.

Format rules, which matter more than they look:

1. Games are written `Away @ Home`. The team before the `@` is the away team.

2. A spread in parentheses attaches to THE TEAM IT SITS NEXT TO, and that team
   is the favorite. Position is the only thing that determines the favorite --
   never infer it from which team seems better.
     `Colorado @ Georgia Tech (-6.5)`  -> home team Georgia Tech favored by 6.5
                                          -> favored="home", spread_magnitude=6.5
     `UNLV (-2.5) @ Hawaii`            -> away team UNLV favored by 2.5
                                          -> favored="away", spread_magnitude=2.5

3. If a number has NO visible minus sign, you cannot tell who is favored.
   Return favored="unknown" and put the number in spread_magnitude. Do NOT
   guess a direction. Example: `Arkansas St @ Memphis (11.5)` -> favored="unknown",
   spread_magnitude=11.5.

4. A line marked as a total-points or tiebreaker game (e.g.
   `Total Points - Furman @ Tennessee`) has no spread: set is_tiebreaker=true,
   favored="unknown", spread_magnitude=null.

5. Copy team names exactly as written, including shorthand and parentheticals
   (`Tenn St`, `Miami (OH)`, `Ole Miss`). Do not expand or correct them.

6. Put the game's full original text in raw_text, so a human can check your work.

Return the games in the order they appear. Ignore page furniture: headers,
"1 of 3" indicators, timestamps, status bars, and navigation buttons.
"""


class SlateGame(BaseModel):
    away: str = Field(description="Away team exactly as written")
    home: str = Field(description="Home team exactly as written")
    favored: Literal["home", "away", "unknown"]
    spread_magnitude: float | None = Field(
        default=None, description="Absolute value of the spread as printed"
    )
    is_tiebreaker: bool = False
    raw_text: str = Field(description="The original line, verbatim")


class ParsedSlate(BaseModel):
    week_label: str = Field(description="Week heading as written, e.g. 'WEEK ONE!!!!'")
    games: list[SlateGame]


class SlateImportError(RuntimeError):
    pass


def week_dir(season: int, week: int) -> Path:
    return SLATES_DIR / str(season) / f"week{week:02d}"


def find_images(season: int, week: int) -> list[Path]:
    """Every image in the week's slate folder, in filename order."""
    directory = week_dir(season, week)
    if not directory.is_dir():
        raise SlateImportError(
            f"no slate folder at {directory}\n"
            f"Create it and drop the week's screenshot(s) in:\n  mkdir -p {directory}"
        )
    images = sorted(p for p in directory.iterdir() if p.suffix.lower() in IMAGE_TYPES)
    if not images:
        unsupported = sorted(p.name for p in directory.iterdir() if p.is_file())
        hint = f" Found unsupported files: {', '.join(unsupported)}." if unsupported else ""
        raise SlateImportError(
            f"no images in {directory}.{hint}\n"
            f"Supported types: {', '.join(sorted(IMAGE_TYPES))}."
        )
    return images


def _cache_key(images: list[Path]) -> str:
    digest = hashlib.sha256()
    digest.update(MODEL.encode())
    digest.update(PROMPT.encode())
    for path in images:
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _cache_path(season: int, week: int, key: str) -> Path:
    return CACHE_DIR / f"slate-{season}-w{week:02d}-{key}.json"


def parse_slate(
    season: int, week: int, use_cache: bool = True
) -> tuple[ParsedSlate, list[Path], bool]:
    """Parse the week's screenshots into a structured slate.

    Returns (slate, images used, whether it came from cache). The cache is keyed
    on the image bytes, so re-running after a failed import -- or confirming a
    slate a second time -- never re-bills the API.
    """
    images = find_images(season, week)
    key = _cache_key(images)
    cache = _cache_path(season, week, key)

    if use_cache and cache.exists():
        return ParsedSlate.model_validate_json(cache.read_text()), images, True

    slate = _call_api(images)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(slate.model_dump_json(indent=1))
    return slate, images, False


def _call_api(images: list[Path]) -> ParsedSlate:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise SlateImportError("the `anthropic` package is not installed") from exc

    if not _has_credentials():
        raise SlateImportError(
            "no Anthropic credentials found.\n"
            "Set one of these up first:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...   # from console.anthropic.com\n"
            "  ant auth login                        # if you use the ant CLI"
        )

    blocks = []
    for path in images:
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": IMAGE_TYPES[path.suffix.lower()],
                    "data": base64.standard_b64encode(path.read_bytes()).decode(),
                },
            }
        )
    blocks.append({"type": "text", "text": PROMPT})

    client = anthropic.Anthropic()
    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=16000,
            messages=[{"role": "user", "content": blocks}],
            output_format=ParsedSlate,
        )
    except anthropic.APIError as exc:
        raise SlateImportError(f"Claude API request failed: {exc}") from exc

    slate = response.parsed_output
    if slate is None or not slate.games:
        raise SlateImportError(
            "Claude returned no games. Check that the screenshots show the pick sheet."
        )
    return slate


def _has_credentials() -> bool:
    """The SDK also reads an `ant auth login` profile, so an unset key is not fatal."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    profile_dir = Path.home() / ".config" / "anthropic"
    return profile_dir.is_dir() and any(profile_dir.iterdir())


def load_json(path: Path | str) -> ParsedSlate:
    """Load a slate from a JSON file instead of calling the API.

    Useful for correcting a parse by hand and re-importing it, and for running
    the pipeline without API credentials.
    """
    path = Path(path)
    if not path.is_file():
        raise SlateImportError(f"no such file: {path}")
    try:
        return ParsedSlate.model_validate_json(path.read_text())
    except Exception as exc:
        raise SlateImportError(f"{path} is not a valid slate file: {exc}") from exc


def load_cached(season: int, week: int) -> ParsedSlate | None:
    """Most recent cached parse for a week, if any (used by tests and --dry-run)."""
    matches = sorted(
        CACHE_DIR.glob(f"slate-{season}-w{week:02d}-*.json"),
        key=lambda p: p.stat().st_mtime,
    )
    if not matches:
        return None
    return ParsedSlate.model_validate_json(matches[-1].read_text())
