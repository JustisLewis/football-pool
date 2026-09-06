"""Build the HTML dashboard.

The page is written without <!doctype>/<html>/<body> wrappers so the same file
can be published as an Artifact and opened locally in a browser.
"""

from __future__ import annotations

import html
import sqlite3
from pathlib import Path

from . import db, report
from .grading import Result, format_line

DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "dashboard.html"

# Minimum half-width of the margin scale, so a quiet week doesn't exaggerate a
# one-point cover into a full-width bar.
MIN_SCALE = 14.0

RESULT_LABEL = {
    Result.WIN: "Cover",
    Result.LOSS: "Miss",
    Result.PUSH: "Push",
    Result.PENDING: "Pending",
    Result.VOID: "Void",
}


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def write(
    conn: sqlite3.Connection,
    season: int,
    week: int,
    output: str | Path | None = None,
    sample: bool = False,
) -> Path:
    path = Path(output) if output else DEFAULT_OUTPUT
    path.write_text(render(conn, season, week, sample=sample), encoding="utf-8")
    return path


def render(
    conn: sqlite3.Connection, season: int, week: int, sample: bool = False
) -> str:
    picks = report.graded_picks(conn, season)
    overall = report.tally(picks)
    slate = db.slate_for_week(conn, season, week)
    tiebreak = report.tiebreaker_status(conn, season, week)
    week_picks = [p for p in picks if p.week == week]

    scale = max([MIN_SCALE] + [abs(p.margin) for p in picks if p.margin is not None])

    return "".join(
        [
            "<title>Cover Margin</title>",
            _fonts(),
            _styles(),
            '<div class="wrap">',
            _sample_banner() if sample else "",
            _header(season, week, overall, picks),
            _slate_section(slate, scale, week),
            _tiebreak_section(tiebreak),
            _splits_section(picks),
            _weeks_section(picks),
            _footer(season, week_picks),
            "</div>",
        ]
    )


def _fonts() -> str:
    return (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        "family=Barlow+Condensed:wght@500;600;700&family=Barlow:wght@400;500;600&"
        'family=IBM+Plex+Mono:wght@400;500;600&display=swap">'
    )


def _styles() -> str:
    return """<style>
:root {
  --paper:#f5f4f0; --surface:#fffefb; --sunken:#eceae3;
  --ink:#16213a; --muted:#666f7e; --rule:#e0ddd4;
  --accent:#1e3a5f; --accent-soft:#dfe6ef;
  --win:#15803d; --loss:#b4232b; --push:#8a8578; --live:#b45309;
  --shadow:0 1px 2px rgba(22,33,58,.07);
  --display:"Barlow Condensed", "Helvetica Neue", Arial, sans-serif;
  --body:"Barlow", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --mono:"IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper:#11141b; --surface:#1a1e27; --sunken:#232833;
    --ink:#e9e7e1; --muted:#98a1af; --rule:#2b313c;
    --accent:#7fa8d6; --accent-soft:#26313f;
    --win:#4ade80; --loss:#f4787f; --push:#9a958a; --live:#fbbf24;
    --shadow:0 1px 2px rgba(0,0,0,.4);
  }
}
:root[data-theme="dark"] {
  --paper:#11141b; --surface:#1a1e27; --sunken:#232833;
  --ink:#e9e7e1; --muted:#98a1af; --rule:#2b313c;
  --accent:#7fa8d6; --accent-soft:#26313f;
  --win:#4ade80; --loss:#f4787f; --push:#9a958a; --live:#fbbf24;
  --shadow:0 1px 2px rgba(0,0,0,.4);
}
* { box-sizing:border-box; }
body { margin:0; background:var(--paper); color:var(--ink);
  font-family:var(--body); font-size:15px; line-height:1.5;
  -webkit-font-smoothing:antialiased; }
.wrap { max-width:760px; margin:0 auto; padding:28px 18px 56px;
  display:flex; flex-direction:column; gap:30px; }
.mono { font-family:var(--mono); font-variant-numeric:tabular-nums; }

/* --- header --- */
.head { display:flex; flex-direction:column; gap:14px; }
.eyebrow { font-family:var(--display); text-transform:uppercase;
  letter-spacing:.14em; font-size:13px; font-weight:600; color:var(--muted); }
.recordline { display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; }
.record { font-family:var(--display); font-weight:700; font-size:58px;
  line-height:.9; letter-spacing:-.01em; font-variant-numeric:tabular-nums; }
.record-meta { display:flex; gap:16px; flex-wrap:wrap; align-items:baseline;
  color:var(--muted); font-size:14px; }
.record-meta b { color:var(--ink); font-weight:600; }
.streak { font-family:var(--mono); font-weight:600; padding:2px 8px;
  border-radius:4px; background:var(--accent-soft); color:var(--accent); }

/* --- section shell --- */
section { display:flex; flex-direction:column; gap:12px; }
h2 { font-family:var(--display); font-size:15px; font-weight:600; margin:0;
  text-transform:uppercase; letter-spacing:.12em; color:var(--muted); }
.hint { color:var(--muted); font-size:13px; margin:0; }

/* --- picks --- */
.picks { display:flex; flex-direction:column; gap:1px;
  background:var(--rule); border:1px solid var(--rule); border-radius:8px;
  overflow:hidden; }
.pick { background:var(--surface); display:grid; gap:2px 12px; padding:11px 14px;
  grid-template-columns:1fr auto;
  grid-template-areas:"game take" "bar bar"; border-left:3px solid var(--push); }
.pick[data-r="WIN"]  { border-left-color:var(--win); }
.pick[data-r="LOSS"] { border-left-color:var(--loss); }
.pick[data-r="PENDING"], .pick[data-r="VOID"] { border-left-color:var(--rule); }
.pick.tiebreak { border-left-color:var(--accent); }
.game { grid-area:game; font-weight:500; }
.game .kick { color:var(--muted); font-size:13px; margin-left:8px; }
.take { grid-area:take; text-align:right; white-space:nowrap; }
.take .team { font-weight:600; }
.take .num { color:var(--muted); font-size:13px; margin-left:5px; }

/* the zero line is the spread: left of it the pick missed, right of it it covered */
.bar { grid-area:bar; margin-top:5px; display:grid; gap:10px; align-items:center;
  grid-template-columns:1fr 56px 46px; }
.track { position:relative; height:16px; background:var(--sunken); border-radius:3px; }
.track::before { content:""; position:absolute; left:50%; top:-2px; bottom:-2px;
  width:1px; background:var(--accent); opacity:.55; }
.fill { position:absolute; top:3px; bottom:3px; border-radius:2px; }
.fill.win  { left:50%; background:var(--win); }
.fill.loss { right:50%; background:var(--loss); }
.fill.push { left:calc(50% - 2px); width:4px; background:var(--push); }
.margin { text-align:right; font-size:13px; font-weight:500; }
.margin.win  { color:var(--win); }
.margin.loss { color:var(--loss); }
.margin.push { color:var(--push); }
.score { font-size:13px; color:var(--muted); text-align:right; }
.pending { grid-column:1 / -1; font-size:13px; color:var(--live); font-weight:500; }

/* --- splits --- */
.splits { display:grid; gap:10px; grid-template-columns:1fr; }
.split { display:grid; grid-template-columns:88px 1fr 78px; gap:12px;
  align-items:center; }
.split .label { font-size:13px; color:var(--muted); }
.split .meter { height:8px; background:var(--sunken); border-radius:4px;
  overflow:hidden; }
.split .meter i { display:block; height:100%; background:var(--accent);
  border-radius:4px; }
.split .val { font-size:13px; text-align:right; }

/* --- weeks --- */
.weeks { display:flex; flex-wrap:wrap; gap:8px; }
.wk { background:var(--surface); border:1px solid var(--rule); border-radius:6px;
  padding:7px 11px; display:flex; flex-direction:column; gap:1px; min-width:66px;
  box-shadow:var(--shadow); }
.wk .n { font-family:var(--display); font-size:12px; text-transform:uppercase;
  letter-spacing:.1em; color:var(--muted); }
.wk .r { font-weight:600; font-size:15px; }
.wk.current { border-color:var(--accent); }

/* --- tiebreak + footer --- */
.tb { background:var(--surface); border:1px solid var(--rule); border-radius:8px;
  padding:12px 14px; display:flex; justify-content:space-between; gap:14px;
  flex-wrap:wrap; align-items:baseline; box-shadow:var(--shadow); }
.tb .who { font-weight:500; }
footer { color:var(--muted); font-size:12.5px; border-top:1px solid var(--rule);
  padding-top:14px; }
.banner { background:var(--accent-soft); color:var(--accent); border-radius:8px;
  padding:11px 14px; font-size:13.5px; line-height:1.45;
  border:1px solid color-mix(in srgb, var(--accent) 25%, transparent); }
.banner code { font-family:var(--mono); font-size:12.5px; }

@media (min-width:620px) {
  .pick { grid-template-columns:minmax(0,1fr) 190px 1fr;
    grid-template-areas:"game take bar"; align-items:center; gap:14px; }
  .bar { margin-top:0; grid-template-columns:1fr 52px 44px; }
  .splits { grid-template-columns:1fr 1fr; gap:12px 26px; }
}
</style>"""


def _sample_banner() -> str:
    return (
        '<div class="banner"><b>Sample picks.</b> These are placeholder '
        "selections used to demonstrate grading &mdash; not your picks. Run "
        "<code>./fp pick -i</code>, then <code>./fp dashboard</code>.</div>"
    )


def _header(season, week, overall, picks) -> str:
    pct = f"{overall.pct:.1%}" if overall.pct is not None else "--"
    pending = (
        f"<span><b>{overall.pending}</b> pending</span>" if overall.pending else ""
    )
    return f"""<header class="head">
<div class="eyebrow">{esc(season)} season &middot; against the spread</div>
<div class="recordline">
  <div class="record mono">{overall.wins}&ndash;{overall.losses}"""  + (
        f"&ndash;{overall.pushes}" if overall.pushes else ""
    ) + f"""</div>
  <div class="record-meta">
    <span><b>{pct}</b> ATS</span>
    <span>streak <span class="streak">{esc(report.streak(picks))}</span></span>
    {pending}
  </div>
</div>
</header>"""


def _slate_section(slate, scale, week) -> str:
    if not slate:
        return (
            f'<section><h2>Week {week}</h2>'
            f'<p class="hint">No slate imported yet. Run <code>./fp import</code>.</p>'
            "</section>"
        )
    rows = "".join(_pick_row(r, scale) for r in slate)
    return f"""<section>
<h2>Week {week}</h2>
<p class="hint">Bars run from the spread: right of the line the pick covered, left of it it missed.</p>
<div class="picks">{rows}</div>
</section>"""


def _pick_row(row, scale) -> str:
    matchup = f"{esc(row['away_abbr'])} @ {esc(row['home_abbr'])}"

    if row["is_tiebreaker"]:
        total = ""
        if row["home_score"] is not None and row["away_score"] is not None:
            total = f'<span class="score mono">{row["away_score"] + row["home_score"]} pts</span>'
        return (
            f'<div class="pick tiebreak" data-r="VOID">'
            f'<div class="game">{matchup}<span class="kick">total points</span></div>'
            f'<div class="take">{total}</div></div>'
        )

    line_text = format_line(row["pool_line"], row["home_abbr"], row["away_abbr"])
    if not row["picked_side"]:
        return (
            f'<div class="pick" data-r="PENDING">'
            f'<div class="game">{matchup}</div>'
            f'<div class="take"><span class="num mono">{esc(line_text)}</span></div>'
            f'<div class="bar"><span class="pending">No pick</span></div></div>'
        )

    graded = report.grade_row(row)
    # Quote the number from the side that was taken, the way a bettor says it.
    take = (
        f'<div class="take"><span class="team">{esc(row["picked_abbr"])}</span>'
        f'<span class="num mono">{graded.taken_at:+g}</span></div>'
    )

    if graded.margin is None:
        body = f'<div class="bar"><span class="pending">{esc(RESULT_LABEL[graded.result])}</span></div>'
    else:
        pct = min(50.0, abs(graded.margin) / scale * 50.0)
        if graded.result is Result.PUSH:
            fill, cls = '<i class="fill push"></i>', "push"
        elif graded.margin > 0:
            fill, cls = f'<i class="fill win" style="width:{pct:.1f}%"></i>', "win"
        else:
            fill, cls = f'<i class="fill loss" style="width:{pct:.1f}%"></i>', "loss"
        body = (
            f'<div class="bar"><div class="track">{fill}</div>'
            f'<span class="score mono">{row["away_score"]}&ndash;{row["home_score"]}</span>'
            f'<span class="margin mono {cls}">{graded.margin:+g}</span></div>'
        )

    return (
        f'<div class="pick" data-r="{graded.result.value}">'
        f'<div class="game">{matchup}</div>{take}{body}</div>'
    )


def _tiebreak_section(tiebreak) -> str:
    if not tiebreak:
        return ""
    right = f"predicted <b>{tiebreak['predicted']}</b>"
    if tiebreak["actual"] is not None:
        right += (
            f' &middot; actual <b>{tiebreak["actual"]}</b>'
            f' &middot; off by <b>{tiebreak["diff"]}</b>'
        )
    return f"""<section>
<h2>Tiebreaker</h2>
<div class="tb"><span class="who">{esc(tiebreak['matchup'])}</span>
<span class="mono">{right}</span></div>
</section>"""


def _splits_section(picks) -> str:
    splits = report.splits(picks)
    if not any(r.decided for r in splits.values()):
        return ""
    bars = []
    for label, record in splits.items():
        if not record.decided:
            continue
        pct = record.pct or 0.0
        bars.append(
            f'<div class="split"><span class="label">{esc(label)}</span>'
            f'<span class="meter"><i style="width:{pct * 100:.0f}%"></i></span>'
            f'<span class="val mono">{record.wins}&ndash;{record.losses}'
            f' &middot; {pct:.0%}</span></div>'
        )
    return f'<section><h2>Splits</h2><div class="splits">{"".join(bars)}</div></section>'


def _weeks_section(picks) -> str:
    weeks = report.by_week(picks)
    if len(weeks) < 2:
        return ""
    cards = "".join(
        f'<div class="wk"><span class="n">wk {w}</span>'
        f'<span class="r mono">{r.wins}&ndash;{r.losses}'
        + (f"&ndash;{r.pushes}" if r.pushes else "")
        + "</span></div>"
        for w, r in weeks.items()
    )
    return f'<section><h2>By week</h2><div class="weeks">{cards}</div></section>'


def _footer(season, week_picks) -> str:
    decided = sum(1 for p in week_picks if p.is_decided)
    return (
        f"<footer>{decided} of {len(week_picks)} picks settled this week. "
        f"Lines are the pool's, not ESPN's. Refresh with <code>./fp sync</code>.</footer>"
    )
