# Football Pool Tracker

Tracks college football pool picks **against the spread**. Drop the
commissioner's slate screenshots in a folder, confirm the parse, make picks —
scores and grades take care of themselves.

## Setup

Needs [uv](https://docs.astral.sh/uv/). Everything else installs itself.

```bash
uv sync
```

Importing a slate calls the Claude API, so set up credentials once:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

(An `ant auth login` profile works too — the SDK finds it automatically.)
Nothing else needs a key: ESPN's endpoints are public.

## Weekly workflow

```bash
mkdir -p slates/2026/week03          # drop the screenshot(s) in here
./fp import                          # parse, confirm, store the slate
./fp pick -i                         # walk the slate, one keystroke per game
./fp tiebreak 52                     # total-points prediction
./fp sync                            # refresh scores and grade
./fp record                          # season summary
./fp dashboard                       # rebuild dashboard.html
```

Every command defaults to the current season and week; pass `--season` /
`--week` to work on another one.

## How grading works

Lines are stored **home-relative**, matching ESPN's convention: a negative
number means the home team is favored. A pick is graded as

```
(home_score - away_score) + line   >0 home covered   <0 away covered   =0 push
```

Only integer lines can push; half-point lines never do. Canceled and postponed
games are `VOID`, not losses.

**The commissioner's line is what settles a pick.** ESPN's line is stored
alongside as a cross-check — it flags likely misreads and resolves ambiguous
lines — but it never overrides what the pool posted.

## Reading the slate

The pick sheet is written `Away @ Home (line)`, and the parenthetical attaches
to **the favored team by position**:

```
Colorado @ Georgia Tech (-6.5)      home favored
UNLV (-2.5) @ Hawaii                away favored
Arkansas St @ Memphis (11.5)        unsigned -> ambiguous
Total Points - Furman @ Tennessee   tiebreaker, no spread
```

An unsigned number is never guessed. The importer proposes ESPN's direction for
confirmation, and asks outright if ESPN has no line. Any game where the pool's
line differs from ESPN's by more than 3 points is flagged as a likely misread.

Team shorthand (`Tenn St`, `Miami (OH)`, `Ole Miss`) is matched against the
week's real games. A game only matches when *both* teams agree and no other game
is close, so `Tenn St` cannot quietly become Tennessee. Anything unresolved is
asked once and remembered.

If a parse needs hand-correcting, edit the cached JSON and re-import it:

```bash
./fp import --from-json path/to/slate.json
```

## Tests

```bash
uv run pytest              # offline; uses checked-in ESPN fixtures
uv run pytest --live       # also re-parses real screenshots (costs an API call)
```

`tests/test_end_to_end.py` grades real 2025 week 5 games against hand-calculated
results, including two games decided by the half-point hook in opposite
directions. Regenerate the ESPN fixtures with
`uv run python tests/make_fixture.py`.

## Notes

- ESPN drops a game's odds from the scoreboard once it goes final, but the core
  API keeps the closing line indefinitely — so lines never need capturing before
  kickoff.
- ESPN rejects requests carrying a custom or browser-spoofing `User-Agent`, so
  `espn.py` deliberately sends none.
- `fp` sets `PYTHONPATH` explicitly because hatchling writes its editable-install
  `.pth` without a trailing newline, which Python silently ignores.
