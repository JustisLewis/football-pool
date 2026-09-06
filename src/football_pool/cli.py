"""Command line interface."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone

from . import db, espn, report, slate_import
from .grading import Result, format_line, to_home_relative
from .matching import WeekIndex

# How far the pool's line may drift from ESPN's before we flag it at import.
LINE_DRIFT_THRESHOLD = 3.0

RESULT_MARK = {
    Result.WIN: "W",
    Result.LOSS: "L",
    Result.PUSH: "P",
    Result.PENDING: "-",
    Result.VOID: "x",
}


def out(text: str = "") -> None:
    print(text)


def warn(text: str) -> None:
    print(text, file=sys.stderr)


def die(text: str) -> None:
    warn(f"error: {text}")
    raise SystemExit(1)


def confirm(question: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def local_kickoff(kickoff_utc: str | None) -> str:
    if not kickoff_utc:
        return "TBD"
    try:
        stamp = datetime.strptime(kickoff_utc, "%Y-%m-%dT%H:%MZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return kickoff_utc
    return stamp.astimezone().strftime("%a %-m/%-d %-I:%M%p").replace("AM", "am").replace(
        "PM", "pm"
    )


def resolve_week(args) -> tuple[int, int]:
    """Season and week to operate on, defaulting to whatever ESPN says is current."""
    if args.season and args.week:
        return args.season, args.week
    try:
        season, week = espn.current_week()
    except espn.EspnError as exc:
        die(f"{exc}\nPass --season and --week to work offline.")
    return args.season or season, args.week or week


def score_text(row) -> str:
    if row["home_score"] is None or row["away_score"] is None:
        return ""
    return f"{row['away_score']}-{row['home_score']}"


# --- import ---------------------------------------------------------------


def cmd_import(args) -> int:
    conn = db.connect(args.db)
    season, week = resolve_week(args)

    try:
        if args.from_json:
            parsed = slate_import.load_json(args.from_json)
            out(f"Read slate from {args.from_json}")
        else:
            parsed, images, cached = slate_import.parse_slate(
                season, week, use_cache=not args.refresh
            )
            source = "cache" if cached else slate_import.MODEL
            out(
                f"Read {len(images)} image(s) from "
                f"slates/{season}/week{week:02d}/ via {source}"
            )
    except slate_import.SlateImportError as exc:
        die(str(exc))
    out(f"Slate heading: {parsed.week_label}")
    out()

    try:
        payload = espn.fetch_scoreboard(season, week, all_divisions=args.all_divisions)
    except espn.EspnError as exc:
        die(f"{exc}\nESPN is needed to match the slate to real games.")
    db.upsert_games(conn, espn.parse_games(payload))
    index = WeekIndex(payload, aliases=db.load_aliases(conn))

    rows, problems = [], []
    for slot, game in enumerate(parsed.games, start=1):
        resolved = _resolve_game(conn, index, game, slot, args, problems)
        if resolved:
            rows.append(resolved)

    if not rows:
        die("nothing could be matched to an ESPN game; slate not written")

    _print_import_table(conn, rows, season, week)
    if problems:
        out()
        for problem in problems:
            warn(f"  ! {problem}")

    if args.dry_run:
        out("\nDry run: nothing written.")
        return 0
    if not confirm(f"\nWrite {len(rows)} games as the week {week} slate?", args.yes):
        out("Aborted; nothing written.")
        return 1

    for row in rows:
        row.update(season=season, week=week)
    db.replace_slate(conn, season, week, rows)
    out(f"Wrote {len(rows)} games. Make picks with:  ./fp pick -i")
    return 0


def _resolve_game(conn, index, game, slot, args, problems) -> dict | None:
    """Match one parsed line to an ESPN game and settle its line."""
    match, candidates = index.best_match(game.away, game.home)

    if match is None:
        match = _ask_which_game(index, game, candidates, args)
        if match is None:
            problems.append(f"skipped (no ESPN match): {game.raw_text}")
            return None
        # Remember the shorthand so this is never asked again.
        db.save_alias(conn, _normalized(game.away), match.away.id)
        db.save_alias(conn, _normalized(game.home), match.home.id)

    pool_line = None
    if not game.is_tiebreaker:
        pool_line = _resolve_line(conn, game, match, args, problems)
        if pool_line is None:
            problems.append(f"skipped (unresolved line): {game.raw_text}")
            return None

    return {
        "espn_id": match.espn_id,
        "slot": slot,
        "pool_line": pool_line,
        "is_tiebreaker": int(game.is_tiebreaker),
        "raw_text": game.raw_text,
        "source_image": None,
    }


def _normalized(name: str) -> str:
    from .matching import normalize

    return normalize(name)


def _ask_which_game(index, game, candidates, args):
    if args.yes or not sys.stdin.isatty():
        return None
    out(f"\nCould not confidently match: {game.raw_text!r}")
    if not candidates:
        out("  No ESPN game this week looks like that matchup.")
        return None
    for i, candidate in enumerate(candidates[:5], start=1):
        out(
            f"  {i}) {candidate.away.display_name} @ {candidate.home.display_name}"
            f"  ({candidate.score:.2f})"
        )
    try:
        choice = input("  Which game? [number, or Enter to skip] ").strip()
    except EOFError:
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(candidates[:5]):
        return candidates[int(choice) - 1]
    return None


def _resolve_line(conn, game, match, args, problems) -> float | None:
    """Settle a game's line, including the unsigned-number case."""
    espn_spread = _espn_spread(conn, match.espn_id)

    if game.favored in ("home", "away") and game.spread_magnitude is not None:
        line = to_home_relative(game.favored, game.spread_magnitude)
        if espn_spread is not None and abs(line - espn_spread) > LINE_DRIFT_THRESHOLD:
            problems.append(
                f"{game.raw_text!r}: pool line "
                f"{format_line(line, match.home.abbreviation, match.away.abbreviation)} "
                f"but ESPN has "
                f"{format_line(espn_spread, match.home.abbreviation, match.away.abbreviation)}"
                " -- check for a misread"
            )
        return line

    if game.spread_magnitude is None:
        return None

    # Unsigned number: ambiguous. Use ESPN to propose a direction, never guess.
    magnitude = abs(game.spread_magnitude)
    if espn_spread is not None and abs(abs(espn_spread) - magnitude) <= LINE_DRIFT_THRESHOLD:
        proposed = -magnitude if espn_spread < 0 else magnitude
        text = format_line(proposed, match.home.abbreviation, match.away.abbreviation)
        if confirm(f"  {game.raw_text!r} has no minus sign. ESPN says {text}. Use it?", args.yes):
            return proposed

    if args.yes or not sys.stdin.isatty():
        return None
    out(f"\n  {game.raw_text!r}: who is favored by {magnitude:g}?")
    out(f"    h) {match.home.display_name} (home)")
    out(f"    a) {match.away.display_name} (away)")
    try:
        answer = input("  [h/a, or Enter to skip] ").strip().lower()
    except EOFError:
        return None
    if answer.startswith("h"):
        return -magnitude
    if answer.startswith("a"):
        return magnitude
    return None


def _espn_spread(conn, espn_id: str) -> float | None:
    """ESPN's line, from the scoreboard if present or the core API if not."""
    row = db.get_game(conn, espn_id)
    if row and row["espn_spread"] is not None:
        return row["espn_spread"]
    line = espn.fetch_closing_line(espn_id)
    if line is not None:
        conn.execute(
            "UPDATE games SET espn_spread = ? WHERE espn_id = ?", (line, espn_id)
        )
        conn.commit()
    return line


def _print_import_table(conn, rows, season, week) -> None:
    out(f"{'#':>3}  {'MATCHUP':<24} {'POOL LINE':<12} {'ESPN':<12} {'KICKOFF'}")
    for row in rows:
        game = db.get_game(conn, row["espn_id"])
        matchup = f"{game['away_abbr']} @ {game['home_abbr']}"
        if row["is_tiebreaker"]:
            pool = "TOTAL PTS"
            espn_text = ""
        else:
            pool = format_line(row["pool_line"], game["home_abbr"], game["away_abbr"])
            espn_text = (
                format_line(game["espn_spread"], game["home_abbr"], game["away_abbr"])
                if game["espn_spread"] is not None
                else "--"
            )
        out(
            f"{row['slot']:>3}  {matchup:<24} {pool:<12} {espn_text:<12} "
            f"{local_kickoff(game['kickoff_utc'])}"
        )


# --- slate / picks --------------------------------------------------------


def cmd_slate(args) -> int:
    conn = db.connect(args.db)
    season, week = resolve_week(args)
    rows = db.slate_for_week(conn, season, week)
    if not rows:
        die(f"no slate stored for {season} week {week}. Run:  ./fp import")
    _print_slate(rows, season, week)
    return 0


def _print_slate(rows, season, week) -> None:
    out(f"{season} week {week}")
    out(f"{'#':>3}  {'MATCHUP':<24} {'LINE':<12} {'PICK':<8} {'KICKOFF':<18} RESULT")
    picked = 0
    for row in rows:
        matchup = f"{row['away_abbr']} @ {row['home_abbr']}"
        if row["is_tiebreaker"]:
            line_text, pick_text, result = "TOTAL PTS", "", ""
        else:
            line_text = format_line(row["pool_line"], row["home_abbr"], row["away_abbr"])
            pick_text = row["picked_abbr"] or ""
            picked += 1 if row["picked_side"] else 0
            if row["picked_side"]:
                graded = report.grade_row(row)
                result = RESULT_MARK[graded.result]
                if graded.margin is not None:
                    result += f" {graded.margin:+g}"
            else:
                result = ""
        out(
            f"{row['slot']:>3}  {matchup:<24} {line_text:<12} {pick_text:<8} "
            f"{local_kickoff(row['kickoff_utc']):<18} {score_text(row):<8} {result}"
        )
    spread_games = sum(1 for r in rows if not r["is_tiebreaker"])
    out(f"\n{picked} of {spread_games} games picked")


def cmd_picks(args) -> int:
    conn = db.connect(args.db)
    season, week = resolve_week(args)
    rows = db.slate_for_week(conn, season, week)
    if not rows:
        die(f"no slate stored for {season} week {week}. Run:  ./fp import")
    _print_slate([r for r in rows if r["picked_side"] or r["is_tiebreaker"]], season, week)

    tb = report.tiebreaker_status(conn, season, week)
    if tb:
        line = f"Tiebreaker {tb['matchup']}: predicted {tb['predicted']}"
        if tb["actual"] is not None:
            line += f", actual {tb['actual']} (off by {tb['diff']})"
        out(line)
    return 0


def _find_slot(rows, token: str):
    """Locate a slate game by slot number or by either team's name."""
    if token.isdigit():
        for row in rows:
            if row["slot"] == int(token):
                return row
        return None
    needle = token.lower()
    hits = [
        row
        for row in rows
        if needle in (row["home_abbr"] or "").lower()
        or needle in (row["away_abbr"] or "").lower()
        or needle in (row["home_name"] or "").lower()
        or needle in (row["away_name"] or "").lower()
    ]
    return hits[0] if len(hits) == 1 else None


def _side_for(row, token: str) -> str | None:
    needle = token.lower()
    home = needle in (row["home_abbr"] or "").lower() or needle in (row["home_name"] or "").lower()
    away = needle in (row["away_abbr"] or "").lower() or needle in (row["away_name"] or "").lower()
    if home and not away:
        return "home"
    if away and not home:
        return "away"
    if needle in ("h", "home"):
        return "home"
    if needle in ("a", "away"):
        return "away"
    return None


def cmd_pick(args) -> int:
    conn = db.connect(args.db)
    season, week = resolve_week(args)
    rows = [r for r in db.slate_for_week(conn, season, week) if not r["is_tiebreaker"]]
    if not rows:
        die(f"no slate stored for {season} week {week}. Run:  ./fp import")

    if args.interactive:
        return _pick_interactive(conn, rows, season, week)

    if not args.game or not args.team:
        die("give a game and a team (./fp pick 3 Alabama) or use -i to walk the slate")

    row = _find_slot(rows, args.game)
    if row is None:
        die(f"no single game matches {args.game!r}; use the slot number from ./fp slate")
    side = _side_for(row, args.team)
    if side is None:
        die(
            f"{args.team!r} is not clearly one of "
            f"{row['away_abbr']} / {row['home_abbr']}"
        )
    _save(conn, season, week, row, side, args.note)
    return 0


def _save(conn, season, week, row, side, note=None) -> None:
    abbr = row["home_abbr"] if side == "home" else row["away_abbr"]
    db.save_pick(conn, season, week, row["espn_id"], side, abbr, row["pool_line"], note)
    out(
        f"  {row['away_abbr']} @ {row['home_abbr']}: picked {abbr} "
        f"({format_line(row['pool_line'], row['home_abbr'], row['away_abbr'])})"
    )


def _pick_interactive(conn, rows, season, week) -> int:
    out(f"{season} week {week} -- Enter keeps an existing pick, 's' skips, 'q' quits.\n")
    for row in rows:
        line_text = format_line(row["pool_line"], row["home_abbr"], row["away_abbr"])
        current = f" [current: {row['picked_abbr']}]" if row["picked_side"] else ""
        prompt = (
            f"{row['slot']:>3}. {row['away_abbr']} @ {row['home_abbr']}  "
            f"({line_text}){current}\n"
            f"     a={row['away_abbr']}  h={row['home_abbr']} > "
        )
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            out("\nStopped.")
            break
        if answer in ("q", "quit"):
            break
        if answer in ("", "s", "skip"):
            continue
        side = _side_for(row, answer)
        if side is None:
            warn(f"     ? {answer!r} is not one of a/h/{row['away_abbr']}/{row['home_abbr']}")
            continue
        _save(conn, season, week, row, side)

    remaining = [
        r for r in db.slate_for_week(conn, season, week)
        if not r["is_tiebreaker"] and not r["picked_side"]
    ]
    out(f"\n{len(rows) - len(remaining)} of {len(rows)} games picked")
    return 0


def cmd_unpick(args) -> int:
    conn = db.connect(args.db)
    season, week = resolve_week(args)
    rows = db.slate_for_week(conn, season, week)
    row = _find_slot(rows, args.game)
    if row is None:
        die(f"no single game matches {args.game!r}")
    if db.delete_pick(conn, season, week, row["espn_id"]):
        out(f"Removed pick on {row['away_abbr']} @ {row['home_abbr']}")
    else:
        out("No pick to remove.")
    return 0


def cmd_tiebreak(args) -> int:
    conn = db.connect(args.db)
    season, week = resolve_week(args)
    rows = [r for r in db.slate_for_week(conn, season, week) if r["is_tiebreaker"]]
    if not rows:
        die(f"no tiebreaker game on the {season} week {week} slate")
    row = rows[0]
    db.save_tiebreaker(conn, season, week, row["espn_id"], args.points)
    out(f"Tiebreaker {row['away_abbr']} @ {row['home_abbr']}: predicted {args.points} points")
    return 0


# --- sync / record --------------------------------------------------------


def cmd_sync(args) -> int:
    conn = db.connect(args.db)
    season, week = resolve_week(args)
    weeks = db.weeks_with_slates(conn, season) if args.all else [week]
    if not weeks:
        weeks = [week]

    for target in weeks:
        try:
            payload = espn.fetch_scoreboard(
                season, target, all_divisions=args.all_divisions, use_cache=False
            )
        except espn.EspnError as exc:
            warn(f"week {target}: {exc} (keeping stored data)")
            continue
        games = espn.parse_games(payload)
        db.upsert_games(conn, games)
        finals = sum(1 for g in games if g["status"] == "STATUS_FINAL")
        out(f"week {target}: refreshed {len(games)} games ({finals} final)")

    picks = [p for p in report.graded_picks(conn, season) if p.week in weeks]
    if picks:
        out(f"\n{report.tally(picks)}")
        pending = sum(1 for p in picks if p.result is Result.PENDING)
        if pending:
            out(f"{pending} pick(s) still pending")
    return 0


def cmd_record(args) -> int:
    conn = db.connect(args.db)
    season = args.season or espn.current_week()[0]
    picks = report.graded_picks(conn, season)
    if not picks:
        die(f"no picks stored for {season}")

    overall = report.tally(picks)
    out(f"{season} season: {overall}")
    if overall.pending:
        out(f"  {overall.pending} pending, {overall.void} void")
    out(f"  current streak: {report.streak(picks)}")

    out("\nBy week")
    for week, record in report.by_week(picks).items():
        out(f"  week {week:>2}: {record}")

    out("\nSplits")
    for label, record in report.splits(picks).items():
        if record.decided or record.pushes:
            out(f"  {label:<12} {record}")

    worst = [p for p in picks if p.result is Result.LOSS and p.margin is not None]
    if worst:
        closest = max(worst, key=lambda p: p.margin)
        out(f"\nClosest miss: {closest.picked_abbr} {closest.line_text} "
            f"({closest.matchup}) by {abs(closest.margin):g}")
    return 0


def cmd_dashboard(args) -> int:
    from . import dashboard

    conn = db.connect(args.db)
    season, week = resolve_week(args)
    path = dashboard.write(conn, season, week, args.output, sample=args.sample)
    out(f"Wrote {path}")
    return 0


# --- argument parsing -----------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fp", description="Track college football pool picks against the spread."
    )
    parser.add_argument("--db", help="database path (default data/pool.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_week_args(sp):
        sp.add_argument("--season", type=int, help="season year (default: current)")
        sp.add_argument("--week", type=int, help="week number (default: current)")
        sp.add_argument(
            "--all-divisions",
            action="store_true",
            help="include non-FBS games when looking up the schedule",
        )
        return sp

    p = add_week_args(sub.add_parser("import", help="read the week's slate screenshots"))
    p.add_argument("--refresh", action="store_true", help="re-read images, ignoring cache")
    p.add_argument("--dry-run", action="store_true", help="show the parse, write nothing")
    p.add_argument(
        "--from-json",
        metavar="FILE",
        help="import an already-parsed slate instead of calling the API",
    )
    p.add_argument("-y", "--yes", action="store_true", help="skip prompts")
    p.set_defaults(func=cmd_import)

    p = add_week_args(sub.add_parser("slate", help="show the stored slate"))
    p.set_defaults(func=cmd_slate)

    p = add_week_args(sub.add_parser("pick", help="record a pick"))
    p.add_argument("game", nargs="?", help="slot number or team name")
    p.add_argument("team", nargs="?", help="team to pick (or a/h)")
    p.add_argument("-i", "--interactive", action="store_true", help="walk the whole slate")
    p.add_argument("--note")
    p.set_defaults(func=cmd_pick)

    p = add_week_args(sub.add_parser("unpick", help="remove a pick"))
    p.add_argument("game", help="slot number or team name")
    p.set_defaults(func=cmd_unpick)

    p = add_week_args(sub.add_parser("tiebreak", help="record the total-points prediction"))
    p.add_argument("points", type=int)
    p.set_defaults(func=cmd_tiebreak)

    p = add_week_args(sub.add_parser("picks", help="show this week's picks"))
    p.set_defaults(func=cmd_picks)

    p = add_week_args(sub.add_parser("sync", help="refresh scores and grade picks"))
    p.add_argument("--all", action="store_true", help="every week with a stored slate")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("record", help="season summary")
    p.add_argument("--season", type=int)
    p.set_defaults(func=cmd_record)

    p = add_week_args(sub.add_parser("dashboard", help="build the HTML dashboard"))
    p.add_argument("-o", "--output", help="output path (default dashboard.html)")
    p.add_argument(
        "--sample",
        action="store_true",
        help="mark the page as showing placeholder picks, not real ones",
    )
    p.set_defaults(func=cmd_dashboard)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        warn("\nInterrupted.")
        return 130
    except sqlite3.Error as exc:
        die(f"database error: {exc}")
