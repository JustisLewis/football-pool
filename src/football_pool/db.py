"""SQLite storage for slates, picks, and results."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "pool.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    espn_id      TEXT PRIMARY KEY,
    season       INTEGER NOT NULL,
    week         INTEGER NOT NULL,
    kickoff_utc  TEXT,
    home_id      TEXT,
    home_abbr    TEXT,
    home_name    TEXT,
    away_id      TEXT,
    away_abbr    TEXT,
    away_name    TEXT,
    neutral_site INTEGER DEFAULT 0,
    status       TEXT,
    home_score   INTEGER,
    away_score   INTEGER,
    espn_spread  REAL,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS games_week ON games (season, week);

-- The pool's slate: which games the commissioner posted, and on what number.
CREATE TABLE IF NOT EXISTS slate_games (
    season        INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    espn_id       TEXT NOT NULL,
    slot          INTEGER NOT NULL,   -- display order, 1-based
    pool_line     REAL,               -- home-relative; authoritative for grading
    is_tiebreaker INTEGER DEFAULT 0,
    raw_text      TEXT,
    source_image  TEXT,
    PRIMARY KEY (season, week, espn_id)
);

CREATE TABLE IF NOT EXISTS picks (
    season      INTEGER NOT NULL,
    week        INTEGER NOT NULL,
    espn_id     TEXT NOT NULL,
    picked_side TEXT NOT NULL CHECK (picked_side IN ('home', 'away')),
    picked_abbr TEXT,
    line        REAL NOT NULL,        -- copied from pool_line when the pick was made
    note        TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (season, week, espn_id)
);

CREATE TABLE IF NOT EXISTS tiebreakers (
    season          INTEGER NOT NULL,
    week            INTEGER NOT NULL,
    espn_id         TEXT NOT NULL,
    predicted_total INTEGER,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (season, week)
);

-- Commissioner shorthand -> ESPN team id. Learned once, reused forever.
CREATE TABLE IF NOT EXISTS aliases (
    alias        TEXT PRIMARY KEY,
    espn_team_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open the database, creating and migrating the schema as needed."""
    db_path = Path(path) if path else DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


# --- games ---------------------------------------------------------------


def upsert_games(conn: sqlite3.Connection, games: list[dict]) -> None:
    """Insert or update games from an ESPN fetch.

    Uses COALESCE on the odds column so a fetch that returns no line (ESPN drops
    odds once a game goes final) never erases a line we already stored.
    """
    conn.executemany(
        """
        INSERT INTO games (
            espn_id, season, week, kickoff_utc, home_id, home_abbr, home_name,
            away_id, away_abbr, away_name, neutral_site, status,
            home_score, away_score, espn_spread, updated_at
        ) VALUES (
            :espn_id, :season, :week, :kickoff_utc, :home_id, :home_abbr, :home_name,
            :away_id, :away_abbr, :away_name, :neutral_site, :status,
            :home_score, :away_score, :espn_spread, datetime('now')
        )
        ON CONFLICT (espn_id) DO UPDATE SET
            kickoff_utc = excluded.kickoff_utc,
            status      = excluded.status,
            home_score  = excluded.home_score,
            away_score  = excluded.away_score,
            espn_spread = COALESCE(excluded.espn_spread, games.espn_spread),
            updated_at  = datetime('now')
        """,
        games,
    )
    conn.commit()


def get_game(conn: sqlite3.Connection, espn_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM games WHERE espn_id = ?", (espn_id,)).fetchone()


def games_for_week(conn: sqlite3.Connection, season: int, week: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM games WHERE season = ? AND week = ? ORDER BY kickoff_utc",
        (season, week),
    ).fetchall()


# --- slate ---------------------------------------------------------------


def replace_slate(
    conn: sqlite3.Connection, season: int, week: int, rows: list[dict]
) -> None:
    """Replace the stored slate for a week. Picks are preserved by design --
    re-importing a corrected slate should not wipe picks already made."""
    conn.execute("DELETE FROM slate_games WHERE season = ? AND week = ?", (season, week))
    conn.executemany(
        """
        INSERT INTO slate_games
            (season, week, espn_id, slot, pool_line, is_tiebreaker, raw_text, source_image)
        VALUES
            (:season, :week, :espn_id, :slot, :pool_line, :is_tiebreaker, :raw_text, :source_image)
        """,
        rows,
    )
    conn.commit()


def slate_for_week(conn: sqlite3.Connection, season: int, week: int) -> list[sqlite3.Row]:
    """The week's slate joined to game details and any pick made."""
    return conn.execute(
        """
        SELECT s.*, g.home_abbr, g.home_name, g.away_abbr, g.away_name,
               g.kickoff_utc, g.status, g.home_score, g.away_score,
               g.espn_spread, g.neutral_site,
               p.picked_side, p.picked_abbr, p.line, p.note
        FROM slate_games s
        JOIN games g USING (espn_id)
        LEFT JOIN picks p
               ON p.season = s.season AND p.week = s.week AND p.espn_id = s.espn_id
        WHERE s.season = ? AND s.week = ?
        ORDER BY s.slot
        """,
        (season, week),
    ).fetchall()


def weeks_with_slates(conn: sqlite3.Connection, season: int) -> list[int]:
    rows = conn.execute(
        "SELECT DISTINCT week FROM slate_games WHERE season = ? ORDER BY week",
        (season,),
    ).fetchall()
    return [r["week"] for r in rows]


# --- picks ---------------------------------------------------------------


def save_pick(
    conn: sqlite3.Connection,
    season: int,
    week: int,
    espn_id: str,
    side: str,
    abbr: str,
    line: float,
    note: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO picks (season, week, espn_id, picked_side, picked_abbr, line, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (season, week, espn_id) DO UPDATE SET
            picked_side = excluded.picked_side,
            picked_abbr = excluded.picked_abbr,
            line        = excluded.line,
            note        = COALESCE(excluded.note, picks.note),
            created_at  = CURRENT_TIMESTAMP
        """,
        (season, week, espn_id, side, abbr, line, note),
    )
    conn.commit()


def delete_pick(conn: sqlite3.Connection, season: int, week: int, espn_id: str) -> int:
    cur = conn.execute(
        "DELETE FROM picks WHERE season = ? AND week = ? AND espn_id = ?",
        (season, week, espn_id),
    )
    conn.commit()
    return cur.rowcount


def all_picks(conn: sqlite3.Connection, season: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.*, g.home_abbr, g.away_abbr, g.status, g.home_score, g.away_score,
               g.kickoff_utc, g.neutral_site, s.is_tiebreaker
        FROM picks p
        JOIN games g USING (espn_id)
        LEFT JOIN slate_games s
               ON s.season = p.season AND s.week = p.week AND s.espn_id = p.espn_id
        WHERE p.season = ?
        ORDER BY p.week, g.kickoff_utc
        """,
        (season,),
    ).fetchall()


# --- tiebreaker ----------------------------------------------------------


def save_tiebreaker(
    conn: sqlite3.Connection, season: int, week: int, espn_id: str, total: int
) -> None:
    conn.execute(
        """
        INSERT INTO tiebreakers (season, week, espn_id, predicted_total)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (season, week) DO UPDATE SET
            espn_id = excluded.espn_id,
            predicted_total = excluded.predicted_total,
            created_at = CURRENT_TIMESTAMP
        """,
        (season, week, espn_id, total),
    )
    conn.commit()


def get_tiebreaker(conn: sqlite3.Connection, season: int, week: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT t.*, g.home_abbr, g.away_abbr, g.status, g.home_score, g.away_score
        FROM tiebreakers t
        LEFT JOIN games g USING (espn_id)
        WHERE t.season = ? AND t.week = ?
        """,
        (season, week),
    ).fetchone()


# --- aliases -------------------------------------------------------------


def load_aliases(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        r["alias"]: r["espn_team_id"]
        for r in conn.execute("SELECT alias, espn_team_id FROM aliases")
    }


def save_alias(conn: sqlite3.Connection, alias: str, espn_team_id: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO aliases (alias, espn_team_id) VALUES (?, ?)",
        (alias, espn_team_id),
    )
    conn.commit()


# --- meta ----------------------------------------------------------------


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
