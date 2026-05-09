#!/usr/bin/env python3
"""Trade engine for NFL GM Sim.

Supports:
- Multiple draft-pick trade value charts (Jimmy Johnson, Rich Hill,
  Chase Stuart, and a balanced composite).
- Random chart assignment to AI GM profiles with deviation factors.
- Trade proposal lifecycle: propose, counter, accept, reject, execute.
- AI GM trade evaluation using chart values, team needs, personality,
  and cap/contract context.
- Trade execution that updates draft_picks, players, contracts, cap,
  and transaction log.
- CLI commands for proposing, reviewing, responding to, and executing
  trades through the save-aware play wrapper.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import roster_rules
from setup_transactions_cap_ledger import (
    ensure_schema as ensure_transaction_schema,
    insert_transaction,
)


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
SOURCE = "trade_engine"

CHART_JIMMY_JOHNSON = "jimmy_johnson"
CHART_RICH_HILL = "rich_hill"
CHART_CHASE_STUART = "chase_stuart"
CHART_BALANCED = "balanced"

ALL_CHARTS = [CHART_JIMMY_JOHNSON, CHART_RICH_HILL, CHART_CHASE_STUART, CHART_BALANCED]

CHART_DESCRIPTIONS = {
    CHART_JIMMY_JOHNSON: (
        "Jimmy Johnson Classic",
        "The original 1990s Dallas Cowboys draft trade chart. Heavily "
        "overvalues early first-round picks relative to modern NFL trade data.",
        "https://www.draftcountdown.com/trade-value-chart/",
    ),
    CHART_RICH_HILL: (
        "Rich Hill Modern",
        "A modern chart based on actual NFL trade data from 2012 onward. "
        "More balanced across rounds and reflects how contemporary GMs value picks.",
        "https://www.draftcountdown.com/rich-hill-trade-value-chart/",
    ),
    CHART_CHASE_STUART: (
        "Chase Stuart / Football Perspective",
        "An analytics-driven chart that values later picks significantly more "
        "than the Johnson chart, based on approximate WAR per draft slot.",
        "https://www.footballperspective.com/draft-value-chart/",
    ),
    CHART_BALANCED: (
        "Balanced Composite",
        "A composite chart blending the Rich Hill and Chase Stuart models. "
        "Designed to split the difference for moderate trade valuations.",
        None,
    ),
}

# ---------------------------------------------------------------------------
# Trade value chart point data (pick_number -> value)
# ---------------------------------------------------------------------------

JIMMY_JOHNSON_POINTS: dict[int, float] = {}
_JJ_ROUNDS: list[list[float]] = [
    # Round 1 (picks 1-32)
    [3000, 2600, 2200, 1800, 1700, 1600, 1500, 1400, 1300, 1250,
     1200, 1150, 1100, 1050, 1000, 950, 900, 850, 800, 780,
     760, 740, 720, 700, 680, 660, 640, 620, 600, 590,
     580, 570],
    # Round 2 (picks 33-64)
    [560, 550, 540, 530, 520, 510, 500, 490, 480, 470,
     460, 450, 440, 430, 420, 410, 400, 390, 380, 370,
     360, 350, 340, 330, 320, 310, 300, 295, 290, 285,
     280, 275],
    # Round 3 (picks 65-96)
    [270, 265, 260, 255, 250, 245, 240, 235, 230, 225,
     220, 215, 210, 205, 200, 195, 190, 185, 180, 175,
     170, 165, 160, 155, 150, 145, 140, 135, 130, 125,
     120, 115],
    # Round 4 (picks 97-128)
    [110, 107, 104, 101, 98, 95, 92, 89, 86, 83,
     80, 78, 76, 74, 72, 70, 68, 66, 64, 62,
     60, 58, 56, 54, 52, 50, 48, 46, 44, 42,
     40, 38],
    # Round 5 (picks 129-160)
    [36.0, 34.5, 33.0, 31.5, 30.0, 28.5, 27.0, 25.5, 24.0, 22.5,
     21.0, 19.5, 18.0, 17.0, 16.0, 15.0, 14.0, 13.0, 12.0, 11.5,
     11.0, 10.5, 10.0, 9.5, 9.0, 8.5, 8.0, 7.5, 7.0, 6.5,
     6.0, 5.5],
    # Round 6 (picks 161-192)
    [5.2, 5.0, 4.8, 4.6, 4.4, 4.2, 4.0, 3.8, 3.6, 3.4,
     3.2, 3.0, 2.9, 2.8, 2.7, 2.6, 2.5, 2.4, 2.3, 2.2,
     2.1, 2.0, 1.9, 1.8, 1.7, 1.6, 1.5, 1.4, 1.3, 1.2,
     1.1, 1.0],
    # Round 7 (picks 193-224)
    [0.90, 0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50, 0.48, 0.46,
     0.44, 0.42, 0.40, 0.38, 0.36, 0.34, 0.32, 0.30, 0.28, 0.26,
     0.24, 0.22, 0.20, 0.18, 0.17, 0.16, 0.15, 0.14, 0.13, 0.12,
     0.11, 0.10],
]
for _r, _vals in enumerate(_JJ_ROUNDS, start=1):
    for _i, _v in enumerate(_vals, start=(_r - 1) * 32 + 1):
        JIMMY_JOHNSON_POINTS[_i] = _v

RICH_HILL_POINTS: dict[int, float] = {}
_RH_ROUNDS: list[list[float]] = [
    # Round 1
    [92.6, 79.8, 67.4, 56.0, 46.8, 39.3, 33.3, 28.7, 25.2, 22.6,
     20.6, 18.9, 17.5, 16.3, 15.3, 14.5, 13.8, 13.2, 12.7, 12.3,
     11.9, 11.6, 11.3, 11.1, 10.9, 10.7, 10.5, 10.4, 10.3, 10.2,
     10.1, 10.0],
    # Round 2
    [9.5, 9.2, 8.9, 8.6, 8.3, 8.0, 7.7, 7.4, 7.1, 6.8,
     6.5, 6.3, 6.1, 5.9, 5.7, 5.5, 5.3, 5.1, 4.9, 4.7,
     4.5, 4.4, 4.3, 4.2, 4.1, 4.0, 3.9, 3.8, 3.7, 3.6,
     3.5, 3.4],
    # Round 3
    [3.3, 3.2, 3.1, 3.0, 2.9, 2.8, 2.7, 2.6, 2.5, 2.4,
     2.3, 2.2, 2.1, 2.0, 1.95, 1.90, 1.85, 1.80, 1.75, 1.70,
     1.65, 1.60, 1.55, 1.50, 1.45, 1.40, 1.35, 1.30, 1.25, 1.20,
     1.15, 1.10],
    # Round 4
    [1.05, 1.00, 0.96, 0.92, 0.88, 0.84, 0.80, 0.76, 0.72, 0.68,
     0.65, 0.62, 0.59, 0.56, 0.53, 0.50, 0.48, 0.46, 0.44, 0.42,
     0.40, 0.38, 0.36, 0.34, 0.32, 0.30, 0.28, 0.26, 0.24, 0.22,
     0.20, 0.18],
    # Round 5
    [0.17, 0.16, 0.15, 0.14, 0.13, 0.12, 0.115, 0.110, 0.105, 0.100,
     0.095, 0.090, 0.086, 0.082, 0.078, 0.074, 0.070, 0.066, 0.062, 0.058,
     0.055, 0.052, 0.049, 0.046, 0.043, 0.040, 0.038, 0.036, 0.034, 0.032,
     0.030, 0.028],
    # Round 6
    [0.027, 0.026, 0.025, 0.024, 0.023, 0.022, 0.021, 0.020, 0.019, 0.018,
     0.0175, 0.0170, 0.0165, 0.0160, 0.0155, 0.0150, 0.0145, 0.0140, 0.0135,
     0.0130, 0.0125, 0.0120, 0.0115, 0.0110, 0.0105, 0.0100, 0.0095, 0.0090,
     0.0085, 0.0080, 0.0075, 0.0070],
    # Round 7
    [0.0068, 0.0066, 0.0064, 0.0062, 0.0060, 0.0058, 0.0056, 0.0054,
     0.0052, 0.0050, 0.0048, 0.0046, 0.0044, 0.0042, 0.0040, 0.0038,
     0.0036, 0.0034, 0.0032, 0.0030, 0.0028, 0.0026, 0.0024, 0.0022,
     0.0020, 0.0018, 0.0016, 0.0014, 0.0012, 0.0010, 0.0008, 0.0006],
]
for _r, _vals in enumerate(_RH_ROUNDS, start=1):
    for _i, _v in enumerate(_vals, start=(_r - 1) * 32 + 1):
        RICH_HILL_POINTS[_i] = _v

CHASE_STUART_POINTS: dict[int, float] = {}
_CS_ROUNDS: list[list[float]] = [
    # Round 1 - roughly 1/pick_number * 3500 scale, much flatter
    [350.0, 175.0, 116.7, 87.5, 70.0, 58.3, 50.0, 43.8, 38.9, 35.0,
     31.8, 29.2, 26.9, 25.0, 23.3, 21.9, 20.6, 19.4, 18.4, 17.5,
     16.7, 15.9, 15.2, 14.6, 14.0, 13.5, 13.0, 12.5, 12.1, 11.7,
     11.3, 10.9],
    # Round 2
    [10.6, 10.3, 10.0, 9.7, 9.5, 9.2, 9.0, 8.7, 8.5, 8.3,
     8.1, 7.9, 7.7, 7.5, 7.3, 7.1, 7.0, 6.8, 6.6, 6.5,
     6.3, 6.2, 6.0, 5.9, 5.7, 5.6, 5.5, 5.3, 5.2, 5.1,
     5.0, 4.8],
    # Round 3
    [4.7, 4.6, 4.5, 4.4, 4.3, 4.2, 4.1, 4.0, 3.9, 3.8,
     3.7, 3.6, 3.5, 3.4, 3.3, 3.2, 3.1, 3.0, 2.9, 2.8,
     2.7, 2.6, 2.5, 2.4, 2.3, 2.2, 2.1, 2.0, 1.9, 1.8,
     1.7, 1.6],
    # Round 4
    [1.5, 1.45, 1.40, 1.35, 1.30, 1.25, 1.20, 1.15, 1.10, 1.05,
     1.00, 0.96, 0.92, 0.88, 0.84, 0.80, 0.76, 0.72, 0.68, 0.64,
     0.60, 0.56, 0.52, 0.48, 0.44, 0.40, 0.38, 0.36, 0.34, 0.32,
     0.30, 0.28],
    # Round 5
    [0.26, 0.24, 0.22, 0.20, 0.18, 0.16, 0.14, 0.12, 0.10, 0.09,
     0.08, 0.07, 0.065, 0.060, 0.055, 0.050, 0.045, 0.040, 0.035,
     0.030, 0.028, 0.026, 0.024, 0.022, 0.020, 0.018, 0.016, 0.014,
     0.012, 0.010, 0.009, 0.008],
    # Round 6
    [0.007, 0.0065, 0.0060, 0.0055, 0.0050, 0.0045, 0.0040, 0.0035,
     0.0030, 0.0028, 0.0026, 0.0024, 0.0022, 0.0020, 0.0018, 0.0016,
     0.0014, 0.0012, 0.0010, 0.0009, 0.0008, 0.0007, 0.0006, 0.0005,
     0.0004, 0.0003, 0.00025, 0.00020, 0.00015, 0.00010, 0.00008, 0.00006],
    # Round 7 - minimal value
    [0.00005] * 32,
]
for _r, _vals in enumerate(_CS_ROUNDS, start=1):
    for _i, _v in enumerate(_vals, start=(_r - 1) * 32 + 1):
        CHASE_STUART_POINTS[_i] = _v

BALANCED_POINTS: dict[int, float] = {}
for _pick in range(1, 225):
    _rh = RICH_HILL_POINTS.get(_pick, 0)
    _cs = CHASE_STUART_POINTS.get(_pick, 0)
    BALANCED_POINTS[_pick] = (_rh + _cs) / 2.0

ALL_CHART_DATA: dict[str, dict[int, float]] = {
    CHART_JIMMY_JOHNSON: JIMMY_JOHNSON_POINTS,
    CHART_RICH_HILL: RICH_HILL_POINTS,
    CHART_CHASE_STUART: CHASE_STUART_POINTS,
    CHART_BALANCED: BALANCED_POINTS,
}

# Position premium multipliers for player trade valuation
POSITION_PREMIUM: dict[str, float] = {
    "QB": 1.80,
    "EDGE": 1.35,
    "OT": 1.30,
    "CB": 1.25,
    "WR": 1.20,
    "DT": 1.10,
    "IDL": 1.10,
    "S": 1.05,
    "TE": 1.05,
    "LB": 1.00,
    "ILB": 1.00,
    "OLB": 1.00,
    "RB": 0.85,
    "FB": 0.60,
    "OG": 0.90,
    "C": 0.90,
    "P": 0.40,
    "K": 0.40,
    "LS": 0.20,
}

DEV_TRAIT_MULTIPLIER: dict[str, float] = {
    "X-Factor": 1.25,
    "Superstar": 1.15,
    "Star": 1.10,
    "Normal": 1.00,
    "Hidden": 0.95,
}

# Player values need to behave more like the real market than a linear OVR
# conversion. Replacement-level veterans should be cheap, while premium young
# starters can still return meaningful picks.
REPLACEMENT_OVERALL: dict[str, float] = {
    "QB": 63.0,
    "EDGE": 67.0,
    "OT": 67.0,
    "CB": 67.0,
    "WR": 66.0,
    "DT": 66.0,
    "IDL": 66.0,
    "S": 66.0,
    "TE": 65.0,
    "LB": 65.0,
    "ILB": 65.0,
    "OLB": 65.0,
    "RB": 66.0,
    "FB": 64.0,
    "OG": 66.0,
    "C": 66.0,
    "K": 72.0,
    "P": 72.0,
    "LS": 70.0,
}

PLAYER_VALUE_FACTOR = 4.6

MAX_PLAYER_TRADE_VALUE: dict[str, float] = {
    "QB": 5200.0,
    "EDGE": 3600.0,
    "OT": 3400.0,
    "CB": 3000.0,
    "WR": 3800.0,
    "DT": 2600.0,
    "IDL": 2600.0,
    "S": 1800.0,
    "TE": 1600.0,
    "LB": 1500.0,
    "ILB": 1500.0,
    "OLB": 1500.0,
    "RB": 1000.0,
    "FB": 120.0,
    "OG": 1700.0,
    "C": 1600.0,
    "K": 80.0,
    "P": 60.0,
    "LS": 20.0,
}

PROPOSAL_STATUSES = ("proposed", "countered", "accepted", "executed",
                     "rejected", "expired", "cancelled")
ASSET_TYPES = ("PlayerContract", "DraftPick", "ConditionalPick")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def money(value: int | float | None) -> str:
    if value is None:
        return "-"
    v = int(value)
    if v < 0:
        return "-" + money(abs(v))
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v}"


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def rows_as_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def current_season(con: sqlite3.Connection) -> int:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_season'"
    ).fetchone()
    return int(row["setting_value"]) if row else 2026


def today(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
    ).fetchone()
    return row["setting_value"] if row else con.execute("SELECT date('now')").fetchone()[0]


def current_phase(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_calendar_phase'"
    ).fetchone()
    return row["setting_value"] if row else "Preseason"


def trade_market_open(con: sqlite3.Connection, *, season: int, current_date: str | None = None) -> tuple[bool, str]:
    check_date = current_date or today(con)
    phase = None
    if table_exists(con, "league_phase_windows"):
        phase = con.execute(
            """
            SELECT *
            FROM league_phase_windows
            WHERE date(?) BETWEEN date(start_date) AND date(end_date)
            ORDER BY league_year
            LIMIT 1
            """,
            (check_date,),
        ).fetchone()
    if phase and not int(phase["transactions_open"] or 0):
        return False, f"transactions closed in {phase['phase_code']}"

    deadline = None
    if table_exists(con, "league_events"):
        row = con.execute(
            """
            SELECT event_date
            FROM league_events
            WHERE league_year = ?
              AND event_code = 'TRADE_DEADLINE'
            ORDER BY event_date
            LIMIT 1
            """,
            (season,),
        ).fetchone()
        if row:
            deadline = str(row["event_date"])
    if deadline is None:
        deadline = f"{season}-11-15"
    phase_code = str(phase["phase_code"] if phase else current_phase(con)).upper()
    if phase_code in {"REGULAR_SEASON", "POSTSEASON"} and deadline and check_date > deadline:
        return False, f"trade deadline passed on {deadline}"
    if phase_code == "POSTSEASON":
        return False, "postseason trade market is closed"
    return True, "open"


def get_team(con: sqlite3.Connection, abbreviation: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM teams WHERE abbreviation = ?",
        (abbreviation.upper(),),
    ).fetchone()
    if not row:
        raise ValueError(f"Team not found: {abbreviation}")
    return row


def table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def column_exists(con: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return any(
        row["name"] == column_name
        for row in con.execute(f'PRAGMA table_info("{table_name}")')
    )


def resolve_game_id(con: sqlite3.Connection, explicit_game_id: str | None = None) -> str:
    if explicit_game_id:
        return explicit_game_id
    if table_exists(con, "game_settings"):
        row = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = 'active_game_id'"
        ).fetchone()
        if row and row["setting_value"]:
            return str(row["setting_value"])
    if table_exists(con, "game_saves"):
        row = con.execute(
            """
            SELECT game_id
            FROM game_saves
            WHERE status = 'active'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row and row["game_id"]:
            return str(row["game_id"])
    return "master"


def user_team_id_for_game(con: sqlite3.Connection, game_id: str | None) -> int | None:
    if not game_id or not table_exists(con, "game_saves"):
        return None
    row = con.execute(
        "SELECT user_team_id FROM game_saves WHERE game_id = ?",
        (game_id,),
    ).fetchone()
    if row and row["user_team_id"] is not None:
        return int(row["user_team_id"])
    return None


def active_trade_exists_for_player(
    con: sqlite3.Connection,
    *,
    game_id: str,
    player_id: int,
    other_team_id: int | None = None,
) -> bool:
    params: list[Any] = [game_id, player_id]
    team_filter = ""
    if other_team_id is not None:
        team_filter = "AND (tp.proposing_team_id = ? OR tp.receiving_team_id = ?)"
        params.extend([other_team_id, other_team_id])
    row = con.execute(
        f"""
        SELECT 1
        FROM trade_proposals tp
        JOIN trade_proposal_assets tpa ON tpa.proposal_id = tp.proposal_id
        WHERE tp.game_id = ?
          AND tp.status IN ('proposed', 'countered', 'accepted')
          AND tpa.asset_type = 'PlayerContract'
          AND tpa.player_id = ?
          {team_filter}
        LIMIT 1
        """,
        params,
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def ensure_schema(con: sqlite3.Connection) -> None:
    roster_rules.ensure_schema(con)
    roster_rules.seed_rules(con)
    ensure_transaction_schema(con)
    con.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS trade_value_charts (
            chart_name  TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            description TEXT,
            source_name TEXT,
            source_url  TEXT,
            scale_label TEXT NOT NULL DEFAULT 'points',
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS trade_value_chart_points (
            chart_name  TEXT NOT NULL REFERENCES trade_value_charts(chart_name) ON DELETE CASCADE,
            pick_number INTEGER NOT NULL,
            value       REAL NOT NULL,
            PRIMARY KEY (chart_name, pick_number)
        );

        CREATE TABLE IF NOT EXISTS trade_proposals (
            proposal_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id          TEXT NOT NULL,
            proposing_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            receiving_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            proposal_date    TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'proposed',
            proposing_chart  TEXT,
            receiving_chart  TEXT,
            proposing_value  REAL,
            receiving_value  REAL,
            value_delta      REAL,
            deadline_date    TEXT,
            counter_to_id    INTEGER REFERENCES trade_proposals(proposal_id) ON DELETE SET NULL,
            proposer_note    TEXT,
            responder_note   TEXT,
            evaluated_accept INTEGER DEFAULT NULL,
            evaluated_reason TEXT,
            source           TEXT NOT NULL DEFAULT 'trade_engine',
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(status IN ('proposed', 'countered', 'accepted', 'executed',
                             'rejected', 'expired', 'cancelled'))
        );

        CREATE INDEX IF NOT EXISTS idx_trade_proposals_game_status
            ON trade_proposals(game_id, status, proposal_date DESC);

        CREATE INDEX IF NOT EXISTS idx_trade_proposals_teams
            ON trade_proposals(proposing_team_id, receiving_team_id, status);

        CREATE TABLE IF NOT EXISTS trade_proposal_assets (
            asset_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id      INTEGER NOT NULL REFERENCES trade_proposals(proposal_id) ON DELETE CASCADE,
            side             TEXT NOT NULL,
            asset_type       TEXT NOT NULL,
            player_id        INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            pick_id          INTEGER REFERENCES draft_picks(pick_id) ON DELETE SET NULL,
            draft_year       INTEGER,
            round            INTEGER,
            pick_number      INTEGER,
            description      TEXT,
            chart_value      REAL,
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(side IN ('proposing', 'receiving')),
            CHECK(asset_type IN ('PlayerContract', 'DraftPick', 'ConditionalPick'))
        );

        CREATE INDEX IF NOT EXISTS idx_trade_proposal_assets_proposal
            ON trade_proposal_assets(proposal_id, side);

        CREATE TABLE IF NOT EXISTS trade_negotiation_log (
            log_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id      INTEGER NOT NULL REFERENCES trade_proposals(proposal_id) ON DELETE CASCADE,
            game_id          TEXT NOT NULL,
            team_id          INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            action           TEXT NOT NULL,
            message          TEXT,
            chart_used       TEXT,
            value_assessment TEXT,
            created_at       TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_trade_negotiation_log_proposal
            ON trade_negotiation_log(proposal_id, created_at);

        DROP VIEW IF EXISTS trade_proposals_view;
        CREATE VIEW trade_proposals_view AS
        SELECT
            tp.proposal_id,
            tp.game_id,
            tp.proposal_date,
            tp.status,
            prop.abbreviation AS proposing_team,
            recv.abbreviation AS receiving_team,
            tp.proposing_chart,
            tp.receiving_chart,
            tp.proposing_value,
            tp.receiving_value,
            tp.value_delta,
            tp.deadline_date,
            tp.counter_to_id,
            tp.proposer_note,
            tp.responder_note,
            tp.evaluated_accept,
            tp.evaluated_reason,
            tp.created_at
        FROM trade_proposals tp
        JOIN teams prop ON prop.team_id = tp.proposing_team_id
        JOIN teams recv ON recv.team_id = tp.receiving_team_id;

        DROP VIEW IF EXISTS trade_proposal_assets_view;
        CREATE VIEW trade_proposal_assets_view AS
        SELECT
            tpa.asset_id,
            tpa.proposal_id,
            tpa.side,
            tpa.asset_type,
            tpa.player_id,
            CASE WHEN tpa.player_id IS NOT NULL
                THEN p.first_name || ' ' || p.last_name
                ELSE NULL
            END AS player_name,
            CASE WHEN tpa.player_id IS NOT NULL THEN p.position ELSE NULL END AS player_position,
            tpa.pick_id,
            tpa.draft_year,
            tpa.round,
            tpa.pick_number,
            tpa.description,
            tpa.chart_value
        FROM trade_proposal_assets tpa
        LEFT JOIN players p ON p.player_id = tpa.player_id;
        """
    )
    # Add trade_value_chart column to ai_gm_profiles if missing
    if table_exists(con, "ai_gm_profiles"):
        if not column_exists(con, "ai_gm_profiles", "trade_value_chart"):
            con.execute(
                'ALTER TABLE ai_gm_profiles ADD COLUMN trade_value_chart TEXT'
            )
        if not column_exists(con, "ai_gm_profiles", "chart_deviation_factor"):
            con.execute(
                'ALTER TABLE ai_gm_profiles ADD COLUMN chart_deviation_factor REAL DEFAULT 0.15'
            )


# ---------------------------------------------------------------------------
# Chart seeding
# ---------------------------------------------------------------------------

def seed_charts(con: sqlite3.Connection) -> int:
    ensure_schema(con)
    seeded = 0
    for chart_name, (display_name, description, source_url) in CHART_DESCRIPTIONS.items():
        source_name = display_name
        con.execute(
            """
            INSERT INTO trade_value_charts (chart_name, display_name, description,
                                            source_name, source_url)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chart_name) DO UPDATE SET
                display_name = excluded.display_name,
                description = excluded.description,
                source_name = excluded.source_name,
                source_url = excluded.source_url
            """,
            (chart_name, display_name, description, source_name, source_url),
        )
        data = ALL_CHART_DATA.get(chart_name, {})
        rows = [(chart_name, pick, val) for pick, val in data.items()]
        if rows:
            con.executemany(
                """
                INSERT INTO trade_value_chart_points (chart_name, pick_number, value)
                VALUES (?, ?, ?)
                ON CONFLICT(chart_name, pick_number) DO UPDATE SET
                    value = excluded.value
                """,
                rows,
            )
        seeded += len(rows)
    return seeded


def assign_charts_to_gms(con: sqlite3.Connection, *, seed: int = 42) -> int:
    """Assign trade value charts randomly to AI GM profiles."""
    ensure_schema(con)
    rng = random.Random(seed)
    profiles = con.execute(
        "SELECT team_id FROM ai_gm_profiles ORDER BY team_id"
    ).fetchall()
    if not profiles:
        return 0
    updated = 0
    for profile in profiles:
        team_id = int(profile["team_id"])
        existing = con.execute(
            "SELECT trade_value_chart FROM ai_gm_profiles WHERE team_id = ?",
            (team_id,),
        ).fetchone()
        if existing and existing["trade_value_chart"]:
            continue
        chart = rng.choice(ALL_CHARTS)
        # Deviation factor: how much the GM can deviate from chart values
        # Range 0.05-0.30; more aggressive GMs get higher deviation
        deviation = round(rng.uniform(0.05, 0.30), 2)
        con.execute(
            """
            UPDATE ai_gm_profiles
            SET trade_value_chart = ?,
                chart_deviation_factor = ?,
                updated_at = datetime('now')
            WHERE team_id = ?
            """,
            (chart, deviation, team_id),
        )
        updated += 1
    return updated


def gm_chart(con: sqlite3.Connection, team_id: int) -> str:
    """Get the trade value chart name for a GM, defaulting to balanced."""
    row = con.execute(
        "SELECT trade_value_chart FROM ai_gm_profiles WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    if row and row["trade_value_chart"]:
        return row["trade_value_chart"]
    return CHART_BALANCED


def gm_deviation(con: sqlite3.Connection, team_id: int) -> float:
    """Get the chart deviation factor for a GM."""
    row = con.execute(
        "SELECT chart_deviation_factor FROM ai_gm_profiles WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    if row and row["chart_deviation_factor"] is not None:
        return float(row["chart_deviation_factor"])
    return 0.15


# ---------------------------------------------------------------------------
# Pick and player valuation
# ---------------------------------------------------------------------------

def pick_value(con: sqlite3.Connection, chart: str, pick_number: int) -> float:
    """Look up a draft pick's value on a given chart."""
    row = con.execute(
        "SELECT value FROM trade_value_chart_points WHERE chart_name = ? AND pick_number = ?",
        (chart, pick_number),
    ).fetchone()
    if row:
        return float(row["value"])
    # Fallback: use round-based estimate
    if pick_number <= 32:
        round_num = 1
    elif pick_number <= 64:
        round_num = 2
    elif pick_number <= 96:
        round_num = 3
    elif pick_number <= 128:
        round_num = 4
    elif pick_number <= 160:
        round_num = 5
    elif pick_number <= 192:
        round_num = 6
    else:
        round_num = 7
    # Get the last known value in that round
    last_row = con.execute(
        """
        SELECT value FROM trade_value_chart_points
        WHERE chart_name = ? AND pick_number <= ?
        ORDER BY pick_number DESC LIMIT 1
        """,
        (chart, round_num * 32),
    ).fetchone()
    base = float(last_row["value"]) if last_row else 1.0
    # Decay within round
    slot = pick_number - (round_num - 1) * 32
    decay = max(0.5, 1.0 - (slot - 1) * 0.015)
    return base * decay


def pick_value_for_round(
    con: sqlite3.Connection,
    chart: str,
    draft_year: int,
    round_num: int,
    team_id: int,
) -> float:
    """Estimate the value of a future pick by round (pick_number unknown)."""
    # For future picks without a known slot, use the midpoint of the round
    mid_pick = (round_num - 1) * 32 + 16
    return pick_value(con, chart, mid_pick)


def player_trade_value(
    con: sqlite3.Connection,
    player_id: int,
    season: int,
    chart: str = CHART_JIMMY_JOHNSON,
) -> float:
    """Estimate a player's trade value in chart-compatible points.

    Uses overall rating, age, contract control, position premium,
    dev trait, and role score to produce a value comparable to
    draft-pick chart points.
    """
    player = con.execute(
        """
        SELECT p.*, c.end_year, c.aav, c.total_years, c.is_active AS contract_active
        FROM players p
        LEFT JOIN contracts c ON c.player_id = p.player_id AND c.is_active = 1
        WHERE p.player_id = ?
        """,
        (player_id,),
    ).fetchone()
    if not player:
        return 0.0

    overall = float(player["overall"] or 50)
    age = float(player["age"] or 27)
    potential = float(player["potential"] or 50)
    dev_trait = player["dev_trait"] or "Normal"
    position = player["position"] or ""

    replacement = REPLACEMENT_OVERALL.get(position, 65.0)
    surplus = max(0.0, overall - replacement)

    # Nonlinear value curve: replacement-level players are close to free,
    # while true difference makers become draft-pick level assets.
    base = (surplus ** 2) * PLAYER_VALUE_FACTOR

    # Age factor: peak trade value is 23-26, declines after 29
    if age <= 22:
        age_factor = 1.10 + (22 - age) * 0.03
    elif age <= 26:
        age_factor = 1.10
    elif age <= 28:
        age_factor = 1.05
    elif age <= 30:
        age_factor = 0.90
    elif age <= 32:
        age_factor = 0.75
    else:
        age_factor = 0.50

    # Position premium
    pos_premium = POSITION_PREMIUM.get(position, 1.0)

    # Dev trait premium
    dev_mult = DEV_TRAIT_MULTIPLIER.get(dev_trait, 1.0)

    # Potential upside matters most when the player is young enough for a
    # buying team to plausibly capture the improvement.
    if age <= 25 and potential > overall:
        upside = ((potential - overall) ** 1.5) * 7.5
    elif age <= 28 and potential > overall:
        upside = ((potential - overall) ** 1.2) * 3.0
    else:
        upside = 0.0

    # Contract control: more years remaining = more valuable
    contract_years = 0
    if player["contract_active"]:
        end_year = int(player["end_year"] or season)
        contract_years = max(0, end_year - season)
    contract_factor = 1.0 + contract_years * 0.05

    # Role score bonus
    role_row = con.execute(
        """
        SELECT MAX(role_score) AS best_score
        FROM player_role_scores
        WHERE player_id = ? AND season = ? AND scheme_key = 'default'
        """,
        (player_id, season),
    ).fetchone()
    role_bonus = 0.0
    if role_row and role_row["best_score"] is not None:
        rs = float(role_row["best_score"])
        if rs >= 85:
            role_bonus = (rs - 85) * 8.0
        elif rs >= 75:
            role_bonus = (rs - 75) * 2.5

    value = (base + upside + role_bonus) * age_factor * pos_premium * dev_mult * contract_factor

    max_value = MAX_PLAYER_TRADE_VALUE.get(position, 1800.0)
    if position == "RB" and age >= 29:
        max_value = min(max_value, 90.0)
    elif position in ("K", "P", "LS"):
        max_value *= 0.5 if age >= 34 else 1.0
    elif age >= 33 and position != "QB":
        max_value *= 0.35
    elif age >= 31 and position != "QB":
        max_value *= 0.60
    value = min(value, max_value)

    # Normalize to chart scale
    # JJ chart: pick 1 = 3000, pick 32 = 570
    # So a 90+ OVR player should be worth a mid-late first
    # Our formula: 90 * 30 = 2700 * modifiers ~= first-round value
    # This works naturally for the JJ chart.
    # For other charts with different scales, we normalize.
    if chart != CHART_JIMMY_JOHNSON:
        jj_1 = JIMMY_JOHNSON_POINTS.get(1, 3000)
        c1v = chart_1_val(con, chart)
        if c1v > 0:
            scale = c1v / jj_1 if jj_1 > 0 else 1.0
            value = value * scale

    return max(0.0, round(value, 2))


def chart_1_val(con: sqlite3.Connection, chart: str) -> float:
    """Get the chart's pick-1 value for scaling."""
    row = con.execute(
        "SELECT value FROM trade_value_chart_points WHERE chart_name = ? AND pick_number = 1",
        (chart,),
    ).fetchone()
    return float(row["value"]) if row else 3000.0


def total_asset_value(
    con: sqlite3.Connection,
    chart: str,
    assets: list[dict[str, Any]],
    season: int,
) -> float:
    """Sum up the chart value of a list of trade assets."""
    total = 0.0
    for asset in assets:
        if asset["asset_type"] == "DraftPick":
            if asset.get("pick_number"):
                total += pick_value(con, chart, int(asset["pick_number"]))
            elif asset.get("pick_id"):
                pick_row = con.execute(
                    "SELECT pick_number FROM draft_picks WHERE pick_id = ?",
                    (int(asset["pick_id"]),),
                ).fetchone()
                if pick_row and pick_row["pick_number"]:
                    total += pick_value(con, chart, int(pick_row["pick_number"]))
                else:
                    # Future pick without slot: use round midpoint
                    total += pick_value_for_round(
                        con, chart,
                        int(asset.get("draft_year") or season + 1),
                        int(asset.get("round") or 1),
                        0,
                    )
            else:
                total += pick_value_for_round(
                    con, chart,
                    int(asset.get("draft_year") or season + 1),
                    int(asset.get("round") or 1),
                    0,
                )
        elif asset["asset_type"] == "PlayerContract":
            pid = asset.get("player_id")
            if pid:
                total += player_trade_value(con, int(pid), season, chart)
    return round(total, 2)


# ---------------------------------------------------------------------------
# Trade proposals
# ---------------------------------------------------------------------------

def validate_assets_owned_by_team(
    con: sqlite3.Connection,
    assets: list[dict[str, Any]],
    *,
    team_id: int,
    side_label: str,
    season: int,
) -> None:
    for asset in assets:
        asset_type = asset.get("asset_type")
        if asset_type == "PlayerContract":
            player_id = asset.get("player_id")
            if player_id is None:
                raise ValueError(f"{side_label} player asset is missing player_id.")
            row = con.execute(
                "SELECT team_id, status FROM players WHERE player_id = ?",
                (int(player_id),),
            ).fetchone()
            if not row:
                raise ValueError(f"{side_label} player {player_id} does not exist.")
            if int(row["team_id"] or 0) != team_id:
                raise ValueError(f"{side_label} player {player_id} is not owned by team_id={team_id}.")
        elif asset_type == "DraftPick":
            pick_id = asset.get("pick_id")
            if pick_id is not None:
                row = con.execute(
                    "SELECT current_team_id, is_used FROM draft_picks WHERE pick_id = ?",
                    (int(pick_id),),
                ).fetchone()
                if not row:
                    raise ValueError(f"{side_label} draft pick {pick_id} does not exist.")
                if int(row["current_team_id"] or 0) != team_id:
                    raise ValueError(f"{side_label} draft pick {pick_id} is not owned by team_id={team_id}.")
                if int(row["is_used"] or 0):
                    raise ValueError(f"{side_label} draft pick {pick_id} has already been used.")
            else:
                draft_year = int(asset.get("draft_year", season + 1))
                round_num = int(asset.get("round", 1))
                if not 1 <= round_num <= 7:
                    raise ValueError(f"{side_label} future pick round must be 1-7.")
                row = con.execute(
                    """
                    SELECT 1 FROM draft_picks
                    WHERE current_team_id = ? AND draft_year = ? AND round = ?
                      AND COALESCE(is_used, 0) = 0
                    LIMIT 1
                    """,
                    (team_id, draft_year, round_num),
                ).fetchone()
                if not row:
                    raise ValueError(
                        f"{side_label} team_id={team_id} does not own an unused {draft_year} round {round_num} pick."
                    )
        elif asset_type == "ConditionalPick":
            round_num = int(asset.get("round", 1))
            if not 1 <= round_num <= 7:
                raise ValueError(f"{side_label} conditional pick round must be 1-7.")
        else:
            raise ValueError(f"{side_label} asset type {asset_type!r} is not supported.")


def create_proposal(
    con: sqlite3.Connection,
    *,
    game_id: str,
    proposing_team_id: int,
    receiving_team_id: int,
    proposing_assets: list[dict[str, Any]],
    receiving_assets: list[dict[str, Any]],
    deadline_date: str | None = None,
    counter_to_id: int | None = None,
    proposer_note: str | None = None,
    proposal_date: str | None = None,
) -> int:
    """Create a trade proposal and return the proposal_id."""
    if not proposing_assets or not receiving_assets:
        raise ValueError("Trade proposals require at least one asset on each side.")
    season = current_season(con)
    validate_assets_owned_by_team(
        con, proposing_assets, team_id=proposing_team_id, side_label="proposing", season=season
    )
    validate_assets_owned_by_team(
        con, receiving_assets, team_id=receiving_team_id, side_label="receiving", season=season
    )
    prop_chart = gm_chart(con, proposing_team_id)
    recv_chart = gm_chart(con, receiving_team_id)
    prop_value = total_asset_value(con, prop_chart, proposing_assets, season)
    recv_value = total_asset_value(con, recv_chart, receiving_assets, season)

    cur = con.execute(
        """
        INSERT INTO trade_proposals (
            game_id, proposing_team_id, receiving_team_id, proposal_date,
            status, proposing_chart, receiving_chart, proposing_value,
            receiving_value, value_delta, deadline_date, counter_to_id,
            proposer_note
        )
        VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_id,
            proposing_team_id,
            receiving_team_id,
            proposal_date or today(con),
            prop_chart,
            recv_chart,
            prop_value,
            recv_value,
            round(recv_value - prop_value, 2) if prop_chart == recv_chart else None,
            deadline_date,
            counter_to_id,
            proposer_note,
        ),
    )
    proposal_id = int(cur.lastrowid)

    for side, assets in [("proposing", proposing_assets), ("receiving", receiving_assets)]:
        for asset in assets:
            chart = prop_chart if side == "proposing" else recv_chart
            asset_chart_value = None
            if asset["asset_type"] == "DraftPick" and asset.get("pick_number"):
                asset_chart_value = pick_value(con, chart, int(asset["pick_number"]))
            elif asset["asset_type"] == "PlayerContract" and asset.get("player_id"):
                asset_chart_value = player_trade_value(
                    con, int(asset["player_id"]), season, chart
                )
            con.execute(
                """
                INSERT INTO trade_proposal_assets (
                    proposal_id, side, asset_type, player_id, pick_id,
                    draft_year, round, pick_number, description, chart_value
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    side,
                    asset["asset_type"],
                    asset.get("player_id"),
                    asset.get("pick_id"),
                    asset.get("draft_year"),
                    asset.get("round"),
                    asset.get("pick_number"),
                    asset.get("description"),
                    asset_chart_value,
                ),
            )

    _log_negotiation(
        con, proposal_id, game_id, proposing_team_id, "propose",
        f"Proposed trade: {len(proposing_assets)} assets for {len(receiving_assets)} assets.",
    )
    return proposal_id


def _log_negotiation(
    con: sqlite3.Connection,
    proposal_id: int,
    game_id: str,
    team_id: int,
    action: str,
    message: str | None,
    chart: str | None = None,
    value_assessment: str | None = None,
) -> int:
    cur = con.execute(
        """
        INSERT INTO trade_negotiation_log (
            proposal_id, game_id, team_id, action, message,
            chart_used, value_assessment
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (proposal_id, game_id, team_id, action, message, chart, value_assessment),
    )
    return int(cur.lastrowid)


def update_proposal_status(
    con: sqlite3.Connection,
    proposal_id: int,
    status: str,
    *,
    responder_note: str | None = None,
    evaluated_accept: int | None = None,
    evaluated_reason: str | None = None,
) -> None:
    con.execute(
        """
        UPDATE trade_proposals
        SET status = ?,
            responder_note = COALESCE(?, responder_note),
            evaluated_accept = COALESCE(?, evaluated_accept),
            evaluated_reason = COALESCE(?, evaluated_reason),
            updated_at = datetime('now')
        WHERE proposal_id = ?
        """,
        (status, responder_note, evaluated_accept, evaluated_reason, proposal_id),
    )


# ---------------------------------------------------------------------------
# AI GM trade evaluation
# ---------------------------------------------------------------------------

def evaluate_trade_for_team(
    con: sqlite3.Connection,
    *,
    team_id: int,
    proposal_id: int,
    season: int,
) -> dict[str, Any]:
    """Evaluate whether a trade is acceptable for the given team.

    Returns a dict with:
      - accept: bool
      - reason: str
      - value_received: float (assets coming IN, on this team's chart)
      - value_given: float (assets going OUT, on this team's chart)
      - value_ratio: float (received / given, >1.0 = favorable)
      - need_boost: float (bonus for positional needs)
      - chart_deviation_allowed: float
    """
    chart = gm_chart(con, team_id)
    deviation = gm_deviation(con, team_id)

    # Get GM personality factors
    profile = con.execute(
        """
        SELECT trade_aggression, risk_profile, trade_policy
        FROM ai_gm_profiles WHERE team_id = ?
        """,
        (team_id,),
    ).fetchone()

    aggression_factor = 0.0
    if profile:
        ta = (profile["trade_aggression"] or "").lower()
        if "aggressive" in ta or "opportunistic" in ta:
            aggression_factor = 0.15
        elif "selective" in ta or "conservative" in ta:
            aggression_factor = -0.10
        elif "moderate" in ta:
            aggression_factor = 0.05

    # Determine which side this team is on
    proposal = con.execute(
        "SELECT * FROM trade_proposals WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()
    if not proposal:
        return {"accept": False, "reason": "Proposal not found.", "value_received": 0,
                "value_given": 0, "value_ratio": 0, "need_boost": 0,
                "chart_deviation_allowed": deviation}

    is_proposer = int(proposal["proposing_team_id"]) == team_id
    receiving_side = "receiving" if is_proposer else "proposing"
    giving_side = "proposing" if is_proposer else "receiving"

    # Get assets
    recv_assets = rows_as_dicts(con.execute(
        "SELECT * FROM trade_proposal_assets WHERE proposal_id = ? AND side = ?",
        (proposal_id, receiving_side),
    ).fetchall())
    give_assets = rows_as_dicts(con.execute(
        "SELECT * FROM trade_proposal_assets WHERE proposal_id = ? AND side = ?",
        (proposal_id, giving_side),
    ).fetchall())

    value_received = total_asset_value(con, chart, recv_assets, season)
    value_given = total_asset_value(con, chart, give_assets, season)
    value_ratio = value_received / value_given if value_given > 0 else 999.0

    # Positional need boost: receiving a player at a position of need
    need_boost = 0.0
    weak_positions = team_weak_positions(con, team_id, season)
    for asset in recv_assets:
        if asset["asset_type"] == "PlayerContract" and asset.get("player_id"):
            p = con.execute(
                "SELECT position FROM players WHERE player_id = ?",
                (int(asset["player_id"]),),
            ).fetchone()
            if p and p["position"] in weak_positions:
                need_boost += 0.10

    # Determine acceptance threshold
    # Base: value_ratio >= 0.85 (willing to take slight loss)
    # + aggression_factor (aggressive GMs accept worse ratios)
    # + need_boost (need-based flexibility)
    # + deviation (allow deviation from strict chart)
    threshold = 0.85 - aggression_factor - need_boost - deviation
    threshold = max(0.50, min(1.20, threshold))

    accept = value_ratio >= threshold

    # Special override: if the GM "really likes" the trade (high-value
    # player at a position of desperate need), they may accept even
    # below threshold
    if not accept and need_boost >= 0.20:
        accept = value_ratio >= (threshold - 0.15)

    if is_proposer:
        # Proposer already wanted this trade; no need to re-evaluate
        # unless the counter changed things
        pass

    reason_parts = []
    if accept:
        reason_parts.append("Trade favorable or acceptable.")
    else:
        reason_parts.append("Trade not favorable enough.")
    reason_parts.append(f"Ratio {value_ratio:.2f} vs threshold {threshold:.2f}")
    if need_boost > 0:
        reason_parts.append(f"Need boost: +{need_boost:.2f}")
    if aggression_factor != 0:
        reason_parts.append(f"Aggression: {aggression_factor:+.2f}")

    return {
        "accept": accept,
        "reason": "; ".join(reason_parts),
        "value_received": round(value_received, 2),
        "value_given": round(value_given, 2),
        "value_ratio": round(value_ratio, 2),
        "need_boost": round(need_boost, 2),
        "chart_deviation_allowed": deviation,
    }


def team_weak_positions(con: sqlite3.Connection, team_id: int, season: int) -> set[str]:
    """Identify position groups where a team is thin or low-rated."""
    weak: set[str] = set()
    rows = con.execute(
        """
        SELECT position, COUNT(*) AS players, ROUND(AVG(overall), 1) AS avg_overall
        FROM players
        WHERE team_id = ? AND position NOT IN ('K', 'P', 'LS')
        GROUP BY position
        HAVING COUNT(*) >= 1
        ORDER BY avg_overall ASC, players ASC
        LIMIT 4
        """,
        (team_id,),
    ).fetchall()
    for row in rows:
        if int(row["players"]) <= 2 or float(row["avg_overall"] or 70) < 68:
            weak.add(row["position"])
    return weak


# ---------------------------------------------------------------------------
# AI GM auto-respond
# ---------------------------------------------------------------------------

def ai_gm_respond(
    con: sqlite3.Connection,
    *,
    proposal_id: int,
    game_id: str,
) -> dict[str, Any]:
    """Have the receiving AI GM evaluate and respond to a trade proposal."""
    season = current_season(con)
    proposal = con.execute(
        "SELECT * FROM trade_proposals WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()
    if not proposal:
        raise ValueError(f"Proposal {proposal_id} not found.")
    if proposal["status"] not in ("proposed", "countered"):
        raise ValueError(f"Proposal {proposal_id} is {proposal['status']}, cannot respond.")

    receiving_team_id = int(proposal["receiving_team_id"])
    evaluation = evaluate_trade_for_team(
        con, team_id=receiving_team_id, proposal_id=proposal_id, season=season,
    )
    chart = gm_chart(con, receiving_team_id)

    if evaluation["accept"]:
        update_proposal_status(
            con, proposal_id, "accepted",
            responder_note="AI GM accepts the trade.",
            evaluated_accept=1,
            evaluated_reason=evaluation["reason"],
        )
        _log_negotiation(
            con, proposal_id, game_id, receiving_team_id, "accept",
            evaluation["reason"], chart,
            f"Received {evaluation['value_received']:.1f} for {evaluation['value_given']:.1f} "
            f"(ratio {evaluation['value_ratio']:.2f})",
        )
        return {"action": "accept", "evaluation": evaluation}
    else:
        # Determine counter-offer or outright rejection
        ratio = evaluation["value_ratio"]
        deviation = gm_deviation(con, receiving_team_id)

        if ratio >= 0.50:
            # Close enough to counter - request more value
            shortfall = evaluation["value_given"] - evaluation["value_received"]
            update_proposal_status(
                con, proposal_id, "countered",
                responder_note=f"AI GM counters: needs ~{shortfall:.0f} more value. {evaluation['reason']}",
                evaluated_accept=0,
                evaluated_reason=evaluation["reason"],
            )
            _log_negotiation(
                con, proposal_id, game_id, receiving_team_id, "counter_suggestion",
                f"Short by {shortfall:.0f} points. {evaluation['reason']}", chart,
            )
            return {"action": "counter_suggestion", "shortfall": round(shortfall, 2),
                    "evaluation": evaluation}
        else:
            # Way off - flat rejection
            update_proposal_status(
                con, proposal_id, "rejected",
                responder_note=f"AI GM rejects: too lopsided. {evaluation['reason']}",
                evaluated_accept=0,
                evaluated_reason=evaluation["reason"],
            )
            _log_negotiation(
                con, proposal_id, game_id, receiving_team_id, "reject",
                evaluation["reason"], chart,
            )
            return {"action": "reject", "evaluation": evaluation}


# ---------------------------------------------------------------------------
# AI GM proactive trade generation
# ---------------------------------------------------------------------------

def ai_gm_generate_trade_proposals(
    con: sqlite3.Connection,
    *,
    game_id: str,
    team_id: int,
    season: int,
    max_proposals: int = 3,
    exclude_target_team_ids: set[int] | None = None,
    proposal_date: str | None = None,
) -> list[int]:
    """Generate proactive trade proposals for an AI GM.

    Identifies surplus players and trade-block candidates, then proposes
    deals targeting needs.
    """
    chart = gm_chart(con, team_id)
    profile = con.execute(
        """
        SELECT trade_aggression, trade_policy FROM ai_gm_profiles WHERE team_id = ?
        """,
        (team_id,),
    ).fetchone()
    if not profile:
        return []

    is_aggressive = "aggressive" in (profile["trade_aggression"] or "").lower()
    is_selective = "selective" in (profile["trade_aggression"] or "").lower()
    if is_selective:
        max_proposals = max(1, max_proposals - 1)

    # Identify trade-block candidates (surplus, aging, expensive)
    trade_block = con.execute(
        """
        SELECT p.player_id, p.first_name || ' ' || p.last_name AS name,
               p.position, p.age, p.overall, p.potential,
               c.aav, c.end_year
        FROM players p
        LEFT JOIN contracts c ON c.player_id = p.player_id AND c.is_active = 1
        WHERE p.team_id = ?
          AND p.status IN ('Active', 'Questionable', 'Doubtful', 'Out')
          AND p.position NOT IN ('K', 'P', 'LS')
          AND p.overall >= 60
          AND p.overall < 78
          AND (p.age >= 29 OR p.overall < 70)
        ORDER BY p.age DESC, c.aav DESC
        LIMIT 10
        """,
        (team_id,),
    ).fetchall()

    if not trade_block:
        return []

    # Identify position needs
    weak_positions = team_weak_positions(con, team_id, season)

    # Find potential trade partners
    proposals_created: list[int] = []
    # Deterministic seed: avoid Python's randomized hash() (3.3+)
    seed_val = (team_id * 10000 + season * 100 + len(game_id)) & 0xFFFFFFFF
    rng = random.Random(seed_val)

    for candidate in trade_block:
        if len(proposals_created) >= max_proposals:
            break

        candidate_id = int(candidate["player_id"])
        if active_trade_exists_for_player(con, game_id=game_id, player_id=candidate_id):
            continue
        candidate_value = player_trade_value(con, candidate_id, season, chart)
        if candidate_value <= 0:
            continue

        # Find teams that might want this player (thin at his position)
        excluded = set(exclude_target_team_ids or set())
        excluded.add(team_id)
        placeholders = ",".join("?" for _ in excluded)
        target_teams = con.execute(
            f"""
            SELECT t.team_id, t.abbreviation,
                   COUNT(p2.player_id) AS pos_count,
                   AVG(p2.overall) AS pos_avg
            FROM teams t
            LEFT JOIN players p2 ON p2.team_id = t.team_id AND p2.position = ?
            WHERE t.team_id NOT IN ({placeholders})
            GROUP BY t.team_id
            ORDER BY pos_count ASC, pos_avg ASC
            LIMIT 5
            """,
            (candidate["position"], *sorted(excluded)),
        ).fetchall()

        for target in target_teams:
            if len(proposals_created) >= max_proposals:
                break

            target_id = int(target["team_id"])
            if active_trade_exists_for_player(
                con,
                game_id=game_id,
                player_id=candidate_id,
                other_team_id=target_id,
            ):
                continue
            target_chart = gm_chart(con, target_id)

            # What can the target offer? Look for picks in the right value range
            target_picks = con.execute(
                """
                SELECT dp.pick_id, dp.draft_year, dp.round, dp.pick_number
                FROM draft_picks dp
                WHERE dp.current_team_id = ?
                  AND dp.is_used = 0
                  AND dp.draft_year BETWEEN ? AND ? + 2
                ORDER BY dp.draft_year, dp.round, dp.pick_number
                """,
                (target_id, season + 1, season),
            ).fetchall()

            # Find picks whose value is close to the candidate's value
            for pick in target_picks:
                if pick["pick_number"]:
                    pick_val = pick_value(con, chart, int(pick["pick_number"]))
                else:
                    pick_val = pick_value_for_round(
                        con, chart, int(pick["draft_year"]), int(pick["round"]), team_id
                    )

                # Allow some flexibility based on aggression
                tolerance = 0.30 if is_aggressive else 0.15
                if abs(pick_val - candidate_value) / max(candidate_value, 1) <= tolerance:
                    proposing_assets = [{"asset_type": "PlayerContract", "player_id": candidate_id}]
                    receiving_assets = [{
                        "asset_type": "DraftPick",
                        "pick_id": int(pick["pick_id"]),
                        "draft_year": int(pick["draft_year"]),
                        "round": int(pick["round"]),
                        "pick_number": int(pick["pick_number"]) if pick["pick_number"] else None,
                        "description": f"{target['abbreviation']} {pick['draft_year']} R{pick['round']} pick",
                    }]
                    pid = create_proposal(
                        con,
                        game_id=game_id,
                        proposing_team_id=team_id,
                        receiving_team_id=target_id,
                        proposing_assets=proposing_assets,
                        receiving_assets=receiving_assets,
                        proposer_note=f"AI GM offers {candidate['name']} ({candidate['position']}, OVR {candidate['overall']}) for draft pick.",
                        proposal_date=proposal_date,
                    )
                    proposals_created.append(pid)
                    break

    return proposals_created


def ai_gm_process_trade_market(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    team_abbr: str | None = None,
    limit_teams: int = 8,
    max_proposals_per_team: int = 1,
    include_user_team_as_target: bool = True,
    execute_cpu_cpu: bool = True,
    ignore_trade_window: bool = False,
    current_date: str | None = None,
) -> dict[str, Any]:
    """Run a deterministic AI trade-market pass.

    CPU teams may propose to other CPU teams or, when enabled, to the user team.
    CPU receiving teams evaluate immediately. Accepted CPU-to-CPU deals can be
    executed automatically; offers involving the user are left pending.
    """
    ensure_schema(con)
    seed_charts(con)
    assign_charts_to_gms(con)

    window_open, window_reason = trade_market_open(con, season=season, current_date=current_date)
    if not window_open and not ignore_trade_window:
        return {
            "game_id": game_id,
            "season": season,
            "teams_scanned": 0,
            "generated": [],
            "responses": [],
            "executed": [],
            "user_pending": [],
            "errors": [],
            "skipped": True,
            "skip_reason": window_reason,
            "counts": {
                "generated": 0,
                "responded": 0,
                "accepted": 0,
                "countered": 0,
                "rejected": 0,
                "executed": 0,
                "user_pending": 0,
                "errors": 0,
            },
        }

    user_team_id = user_team_id_for_game(con, game_id)
    if team_abbr:
        teams = [get_team(con, team_abbr)]
    else:
        teams = con.execute(
            """
            SELECT t.*
            FROM teams t
            LEFT JOIN ai_gm_profiles p ON p.team_id = t.team_id
            WHERE (? IS NULL OR t.team_id != ?)
            ORDER BY
                CASE
                    WHEN lower(COALESCE(p.trade_aggression, '')) LIKE '%aggressive%' THEN 0
                    WHEN lower(COALESCE(p.trade_aggression, '')) LIKE '%opportunistic%' THEN 1
                    ELSE 2
                END,
                t.abbreviation
            LIMIT ?
            """,
            (user_team_id, user_team_id, max(1, int(limit_teams))),
        ).fetchall()

    excluded_targets: set[int] = set()
    if user_team_id is not None and not include_user_team_as_target:
        excluded_targets.add(user_team_id)

    generated: list[int] = []
    responses: list[dict[str, Any]] = []
    executed: list[int] = []
    user_pending: list[int] = []
    errors: list[str] = []

    for team in teams:
        team_id = int(team["team_id"])
        if user_team_id is not None and team_id == user_team_id:
            continue
        try:
            proposal_ids = ai_gm_generate_trade_proposals(
                con,
                game_id=game_id,
                team_id=team_id,
                season=season,
                max_proposals=max(1, int(max_proposals_per_team)),
                exclude_target_team_ids=excluded_targets,
                proposal_date=current_date,
            )
        except Exception as exc:
            errors.append(f"{team['abbreviation']}: {exc}")
            continue

        generated.extend(proposal_ids)
        for proposal_id in proposal_ids:
            proposal = con.execute(
                "SELECT * FROM trade_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            if not proposal:
                continue
            receiving_team_id = int(proposal["receiving_team_id"])
            involves_user = user_team_id is not None and (
                int(proposal["proposing_team_id"]) == user_team_id
                or receiving_team_id == user_team_id
            )
            if involves_user:
                user_pending.append(proposal_id)
                continue
            try:
                response = ai_gm_respond(con, proposal_id=proposal_id, game_id=game_id)
                responses.append({"proposal_id": proposal_id, **response})
                if response.get("action") == "accept" and execute_cpu_cpu:
                    execute_trade(con, proposal_id)
                    executed.append(proposal_id)
            except Exception as exc:
                errors.append(f"proposal {proposal_id}: {exc}")

    return {
        "game_id": game_id,
        "season": season,
        "teams_scanned": len(teams),
        "generated": generated,
        "responses": responses,
        "executed": executed,
        "user_pending": user_pending,
        "errors": errors,
        "skipped": False,
        "skip_reason": None,
        "counts": {
            "generated": len(generated),
            "responded": len(responses),
            "accepted": sum(1 for item in responses if item.get("action") == "accept"),
            "countered": sum(1 for item in responses if item.get("action") == "counter_suggestion"),
            "rejected": sum(1 for item in responses if item.get("action") == "reject"),
            "executed": len(executed),
            "user_pending": len(user_pending),
            "errors": len(errors),
        },
    }


# ---------------------------------------------------------------------------
# Trade execution
# ---------------------------------------------------------------------------

def execute_trade(con: sqlite3.Connection, proposal_id: int) -> dict[str, Any]:
    """Execute an accepted trade: move assets, update cap, log."""
    proposal = con.execute(
        "SELECT * FROM trade_proposals WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()
    if not proposal:
        raise ValueError(f"Proposal {proposal_id} not found.")
    if proposal["status"] not in ("accepted",):
        raise ValueError(f"Proposal {proposal_id} must be 'accepted' to execute, got '{proposal['status']}'.")

    season = current_season(con)
    game_id = proposal["game_id"]
    proposing_team_id = int(proposal["proposing_team_id"])
    receiving_team_id = int(proposal["receiving_team_id"])

    assets = rows_as_dicts(con.execute(
        "SELECT * FROM trade_proposal_assets WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchall())

    proposing_assets = [a for a in assets if a["side"] == "proposing"]
    receiving_assets = [a for a in assets if a["side"] == "receiving"]

    # Move proposing assets to receiving team
    for asset in proposing_assets:
        _transfer_asset(con, asset, from_team=proposing_team_id, to_team=receiving_team_id, season=season)

    # Move receiving assets to proposing team
    for asset in receiving_assets:
        _transfer_asset(con, asset, from_team=receiving_team_id, to_team=proposing_team_id, season=season)

    update_proposal_status(con, proposal_id, "executed")

    # Log the transaction in both trade_negotiation_log AND transaction_log
    prop_abbr = con.execute("SELECT abbreviation FROM teams WHERE team_id = ?", (proposing_team_id,)).fetchone()["abbreviation"]
    recv_abbr = con.execute("SELECT abbreviation FROM teams WHERE team_id = ?", (receiving_team_id,)).fetchone()["abbreviation"]

    asset_desc = []
    for a in proposing_assets:
        if a["asset_type"] == "PlayerContract" and a.get("player_id"):
            p = con.execute("SELECT first_name, last_name FROM players WHERE player_id = ?", (int(a["player_id"]),)).fetchone()
            if p:
                asset_desc.append(f"{p['first_name']} {p['last_name']}")
        elif a["asset_type"] in ("DraftPick", "ConditionalPick"):
            asset_desc.append(f"{a.get('draft_year','?')} R{a.get('round','?')} pick")
    for a in receiving_assets:
        if a["asset_type"] == "PlayerContract" and a.get("player_id"):
            p = con.execute("SELECT first_name, last_name FROM players WHERE player_id = ?", (int(a["player_id"]),)).fetchone()
            if p:
                asset_desc.append(f"{p['first_name']} {p['last_name']}")
        elif a["asset_type"] in ("DraftPick", "ConditionalPick"):
            asset_desc.append(f"{a.get('draft_year','?')} R{a.get('round','?')} pick")

    trade_desc = f"Trade: {prop_abbr} <-> {recv_abbr} - {', '.join(asset_desc)}"

    # Write to transaction_log for each player/pick moved so it appears
    # in team history (recent_transactions, view_team, etc.).
    trade_date = today(con)
    phase = current_phase(con)
    for a in proposing_assets:
        _log_trade_transaction(
            con,
            proposal_id=proposal_id,
            asset=a,
            transaction_date=trade_date,
            season=season,
            phase=phase,
            team_id=proposing_team_id,
            secondary_team_id=receiving_team_id,
            from_team_id=proposing_team_id,
            to_team_id=receiving_team_id,
            description=trade_desc,
            external_suffix="proposing_sent",
        )
        _log_trade_transaction(
            con,
            proposal_id=proposal_id,
            asset=a,
            transaction_date=trade_date,
            season=season,
            phase=phase,
            team_id=receiving_team_id,
            secondary_team_id=proposing_team_id,
            from_team_id=proposing_team_id,
            to_team_id=receiving_team_id,
            description=trade_desc,
            external_suffix="receiving_got",
        )

    for a in receiving_assets:
        _log_trade_transaction(
            con,
            proposal_id=proposal_id,
            asset=a,
            transaction_date=trade_date,
            season=season,
            phase=phase,
            team_id=receiving_team_id,
            secondary_team_id=proposing_team_id,
            from_team_id=receiving_team_id,
            to_team_id=proposing_team_id,
            description=trade_desc,
            external_suffix="receiving_sent",
        )
        _log_trade_transaction(
            con,
            proposal_id=proposal_id,
            asset=a,
            transaction_date=trade_date,
            season=season,
            phase=phase,
            team_id=proposing_team_id,
            secondary_team_id=receiving_team_id,
            from_team_id=receiving_team_id,
            to_team_id=proposing_team_id,
            description=trade_desc,
            external_suffix="proposing_got",
        )

    _log_negotiation(
        con, proposal_id, game_id, proposing_team_id, "execute",
        f"Trade executed: {prop_abbr} <-> {recv_abbr} - {', '.join(asset_desc)}",
    )

    # Rebuild cap after all moves
    try:
        from setup_contract_years import sync_team_cap_space
        sync_team_cap_space(con)
    except Exception as exc:
        import sys
        print(f"Warning: cap sync failed after trade execution: {exc}", file=sys.stderr)

    # NOTE: caller is responsible for con.commit() to maintain atomicity control
    return {"proposal_id": proposal_id, "status": "executed", "assets_moved": len(assets)}


def active_contract_id(con: sqlite3.Connection, player_id: int | None) -> int | None:
    if player_id is None:
        return None
    row = con.execute(
        """
        SELECT contract_id
        FROM contracts
        WHERE player_id = ? AND COALESCE(is_active, 1) = 1
        ORDER BY contract_id DESC
        LIMIT 1
        """,
        (player_id,),
    ).fetchone()
    return int(row["contract_id"]) if row else None


def _log_trade_transaction(
    con: sqlite3.Connection,
    *,
    proposal_id: int,
    asset: dict[str, Any],
    transaction_date: str,
    season: int,
    phase: str,
    team_id: int,
    secondary_team_id: int,
    from_team_id: int,
    to_team_id: int,
    description: str,
    external_suffix: str,
) -> None:
    player_id = int(asset["player_id"]) if asset.get("player_id") else None
    asset_id = asset.get("asset_id") or f"{asset.get('side', 'asset')}:{asset.get('asset_type', 'unknown')}"
    insert_transaction(
        con,
        transaction_date=transaction_date,
        season=season,
        phase=phase,
        transaction_type="Trade",
        team_id=team_id,
        secondary_team_id=secondary_team_id,
        player_id=player_id,
        contract_id=active_contract_id(con, player_id),
        from_team_id=from_team_id,
        to_team_id=to_team_id,
        description=description,
        source=SOURCE,
        external_ref=f"trade:{proposal_id}:{asset_id}:{external_suffix}",
    )


def _transfer_asset(
    con: sqlite3.Connection,
    asset: dict[str, Any],
    *,
    from_team: int,
    to_team: int,
    season: int,
) -> None:
    """Transfer a single asset from one team to another."""
    if asset["asset_type"] == "PlayerContract":
        player_id = int(asset["player_id"])
        # Verify ownership before moving
        row = con.execute(
            "SELECT team_id FROM players WHERE player_id = ?",
            (player_id,),
        ).fetchone()
        if not row or int(row["team_id"]) != from_team:
            raise ValueError(
                f"Player {player_id} does not belong to team {from_team}; cannot transfer."
            )
        # Move player
        con.execute(
            "UPDATE players SET team_id = ?, status = 'Active' WHERE player_id = ?",
            (to_team, player_id),
        )
        # Move contract
        con.execute(
            "UPDATE contracts SET team_id = ? WHERE player_id = ? AND is_active = 1",
            (to_team, player_id),
        )
        try:
            con.execute(
                "UPDATE contract_years SET team_id = ? WHERE contract_id IN "
                "(SELECT contract_id FROM contracts WHERE player_id = ? AND is_active = 1)",
                (to_team, player_id),
            )
        except sqlite3.OperationalError:
            pass
        # Clear depth chart entry
        try:
            con.execute("DELETE FROM depth_charts WHERE player_id = ?", (player_id,))
        except sqlite3.OperationalError:
            pass

    elif asset["asset_type"] == "DraftPick":
        pick_id = int(asset["pick_id"]) if asset.get("pick_id") else None
        if pick_id:
            # Verify ownership before moving
            row = con.execute(
                "SELECT current_team_id FROM draft_picks WHERE pick_id = ?",
                (pick_id,),
            ).fetchone()
            if not row or int(row["current_team_id"]) != from_team:
                raise ValueError(
                    f"Pick {pick_id} does not belong to team {from_team}; cannot transfer."
                )
            con.execute(
                "UPDATE draft_picks SET current_team_id = ?, is_traded = 1 WHERE pick_id = ?",
                (to_team, pick_id),
            )
        else:
            # Find a matching pick
            draft_year = int(asset.get("draft_year", season + 1))
            round_num = int(asset.get("round", 1))
            pick_row = con.execute(
                """
                SELECT pick_id FROM draft_picks
                WHERE current_team_id = ? AND draft_year = ? AND round = ?
                  AND is_used = 0
                ORDER BY pick_number, pick_id
                LIMIT 1
                """,
                (from_team, draft_year, round_num),
            ).fetchone()
            if pick_row:
                con.execute(
                    "UPDATE draft_picks SET current_team_id = ?, is_traded = 1 WHERE pick_id = ?",
                    (to_team, int(pick_row["pick_id"])),
                )

    elif asset["asset_type"] == "ConditionalPick":
        # Conditional picks are resolved after the season (e.g., "7th if player
        # makes roster, 6th otherwise"). For now, transfer the base pick.
        draft_year = int(asset.get("draft_year", season + 1))
        round_num = int(asset.get("round", 1))
        pick_row = con.execute(
            """
            SELECT pick_id FROM draft_picks
            WHERE current_team_id = ? AND draft_year = ? AND round = ?
              AND is_used = 0
            ORDER BY pick_number, pick_id
            LIMIT 1
            """,
            (from_team, draft_year, round_num),
        ).fetchone()
        if pick_row:
            con.execute(
                "UPDATE draft_picks SET current_team_id = ?, is_traded = 1 WHERE pick_id = ?",
                (to_team, int(pick_row["pick_id"])),
            )


# ---------------------------------------------------------------------------
# CLI actions
# ---------------------------------------------------------------------------

def action_setup(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    chart_points = seed_charts(con)
    con.commit()
    print("Trade engine schema is ready.")
    print(f"Trade value chart points seeded: {chart_points}")


def action_assign_charts(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_charts(con)
    updated = assign_charts_to_gms(con, seed=args.seed or 42)
    con.commit()
    print(f"Trade value charts assigned to {updated} AI GM profiles.")
    # Show assignments
    rows = con.execute(
        """
        SELECT t.abbreviation, p.trade_value_chart, p.chart_deviation_factor
        FROM ai_gm_profiles p
        JOIN teams t ON t.team_id = p.team_id
        ORDER BY t.abbreviation
        """
    ).fetchall()
    for row in rows:
        print(f"  {row['abbreviation']}: {row['trade_value_chart'] or 'none'} (deviation {row['chart_deviation_factor'] or 0.15})")


def action_show_chart(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    chart = args.chart
    rows = con.execute(
        "SELECT pick_number, value FROM trade_value_chart_points "
        "WHERE chart_name = ? ORDER BY pick_number LIMIT ?",
        (chart, args.limit),
    ).fetchall()
    if not rows:
        print(f"No data for chart: {chart}")
        return
    display_name = CHART_DESCRIPTIONS.get(chart, (chart, "", None))[0]
    print(f"Trade Value Chart: {display_name}")
    print(f"{'Pick':>5} {'Value':>12}")
    for row in rows:
        print(f"{row['pick_number']:>5} {row['value']:>12.2f}")


def action_propose(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    """Create a trade proposal from CLI args."""
    ensure_schema(con)
    seed_charts(con)
    assign_charts_to_gms(con)
    game_id = resolve_game_id(con, args.game_id)
    proposing_team = get_team(con, args.proposing_team)
    receiving_team = get_team(con, args.receiving_team)

    proposing_assets = []
    receiving_assets = []

    # Parse asset specs: "player:ID" or "pick:PICK_ID" or "pick:YEAR:ROUND"
    for spec in (args.offering or []):
        proposing_assets.append(_parse_asset_spec(con, spec))
    for spec in (args.requesting or []):
        receiving_assets.append(_parse_asset_spec(con, spec))

    if not proposing_assets or not receiving_assets:
        raise ValueError("Provide at least one asset on each side using --offering and --requesting.")

    proposal_id = create_proposal(
        con,
        game_id=game_id,
        proposing_team_id=int(proposing_team["team_id"]),
        receiving_team_id=int(receiving_team["team_id"]),
        proposing_assets=proposing_assets,
        receiving_assets=receiving_assets,
        deadline_date=args.deadline,
        proposer_note=args.note,
    )
    con.commit()

    prop_abbr = proposing_team["abbreviation"]
    recv_abbr = receiving_team["abbreviation"]
    print(f"Trade proposal {proposal_id}: {prop_abbr} -> {recv_abbr}")
    print(f"  Status: proposed")
    print(f"  Offering: {len(proposing_assets)} asset(s)")
    print(f"  Requesting: {len(receiving_assets)} asset(s)")
    print(f"  Use 'trade respond --proposal-id {proposal_id} --accept/--reject' to respond.")


def _parse_asset_spec(con: sqlite3.Connection, spec: str) -> dict[str, Any]:
    """Parse an asset specification string.

    Formats:
      player:PLAYER_ID
      pick:PICK_ID
      pick:YEAR:ROUND  (for future picks without a pick_id)
    """
    parts = spec.split(":")
    if parts[0].lower() == "player" and len(parts) >= 2:
        pid = int(parts[1])
        p = con.execute("SELECT * FROM players WHERE player_id = ?", (pid,)).fetchone()
        if not p:
            raise ValueError(f"Player {pid} not found.")
        return {
            "asset_type": "PlayerContract",
            "player_id": pid,
            "description": f"{p['first_name']} {p['last_name']} ({p['position']})",
        }
    elif parts[0].lower() == "pick" and len(parts) >= 2:
        if len(parts) == 2:
            # Direct pick_id
            pick_id = int(parts[1])
            dp = con.execute("SELECT * FROM draft_picks WHERE pick_id = ?", (pick_id,)).fetchone()
            if not dp:
                raise ValueError(f"Draft pick {pick_id} not found.")
            return {
                "asset_type": "DraftPick",
                "pick_id": pick_id,
                "draft_year": int(dp["draft_year"]),
                "round": int(dp["round"]),
                "pick_number": int(dp["pick_number"]) if dp["pick_number"] else None,
                "description": f"{dp['draft_year']} R{dp['round']} pick",
            }
        elif len(parts) == 3:
            # YEAR:ROUND
            return {
                "asset_type": "DraftPick",
                "pick_id": None,
                "draft_year": int(parts[1]),
                "round": int(parts[2]),
                "pick_number": None,
                "description": f"{parts[1]} R{parts[2]} pick",
            }
    raise ValueError(f"Unknown asset spec: {spec}. Use player:ID or pick:PICK_ID or pick:YEAR:ROUND")


def action_list_proposals(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    game_id = resolve_game_id(con, args.game_id)
    where_parts = ["tp.game_id = ?"]
    params: list[Any] = [game_id]
    if args.status:
        where_parts.append("tp.status = ?")
        params.append(args.status)
    if args.team:
        team = get_team(con, args.team)
        where_parts.append("(tp.proposing_team_id = ? OR tp.receiving_team_id = ?)")
        params.extend([int(team["team_id"]), int(team["team_id"])])

    where = " AND ".join(where_parts)
    rows = con.execute(
        f"""
        SELECT tp.*, prop.abbreviation AS prop_abbr, recv.abbreviation AS recv_abbr
        FROM trade_proposals tp
        JOIN teams prop ON prop.team_id = tp.proposing_team_id
        JOIN teams recv ON recv.team_id = tp.receiving_team_id
        WHERE {where}
        ORDER BY tp.proposal_date DESC, tp.proposal_id DESC
        LIMIT ?
        """,
        (*params, args.limit),
    ).fetchall()
    if not rows:
        print("No trade proposals found.")
        return
    for row in rows:
        print(
            f"  #{row['proposal_id']:<4} {row['prop_abbr']}->{row['recv_abbr']} "
            f"status={row['status']} date={row['proposal_date']}"
        )
        if row["proposer_note"]:
            print(f"         Note: {row['proposer_note'][:80]}")
        if row["responder_note"]:
            print(f"         Response: {row['responder_note'][:80]}")


def action_show_proposal(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    proposal = con.execute(
        "SELECT * FROM trade_proposals_view WHERE proposal_id = ?",
        (args.proposal_id,),
    ).fetchone()
    if not proposal:
        print(f"Proposal {args.proposal_id} not found.")
        return
    print(f"Trade Proposal #{proposal['proposal_id']}")
    print(f"  {proposal['proposing_team']} -> {proposal['receiving_team']}")
    print(f"  Status: {proposal['status']}")
    print(f"  Date: {proposal['proposal_date']}")
    if proposal["proposing_value"] is not None:
        print(f"  Proposing value: {proposal['proposing_value']:.1f}")
    if proposal["receiving_value"] is not None:
        print(f"  Receiving value: {proposal['receiving_value']:.1f}")
    if proposal["proposer_note"]:
        print(f"  Proposer note: {proposal['proposer_note']}")
    if proposal["responder_note"]:
        print(f"  Responder note: {proposal['responder_note']}")

    # Show assets
    assets = con.execute(
        "SELECT * FROM trade_proposal_assets_view WHERE proposal_id = ? ORDER BY side, asset_id",
        (args.proposal_id,),
    ).fetchall()
    print("  Offering:")
    for a in assets:
        if a["side"] == "proposing":
            _print_asset(a)
    print("  Requesting:")
    for a in assets:
        if a["side"] == "receiving":
            _print_asset(a)


def _print_asset(a: sqlite3.Row) -> None:
    if a["asset_type"] == "PlayerContract":
        name = a["player_name"] or f"player_id={a['player_id']}"
        pos = a["player_position"] or ""
        val = f" (chart: {a['chart_value']:.1f})" if a["chart_value"] else ""
        print(f"    🏈 {name} ({pos}){val}")
    elif a["asset_type"] == "DraftPick":
        yr = a["draft_year"] or "?"
        rnd = a["round"] or "?"
        pn = a["pick_number"] or "?"
        val = f" (chart: {a['chart_value']:.2f})" if a["chart_value"] else ""
        print(f"    📋 {yr} R{rnd} #{pn}{val}")


def action_respond(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_charts(con)
    assign_charts_to_gms(con)
    proposal = con.execute(
        "SELECT * FROM trade_proposals WHERE proposal_id = ?",
        (args.proposal_id,),
    ).fetchone()
    if not proposal:
        raise ValueError(f"Proposal {args.proposal_id} not found.")

    if args.accept:
        update_proposal_status(
            con, args.proposal_id, "accepted",
            responder_note=args.note or "Accepted by user.",
        )
        print(f"Proposal {args.proposal_id} accepted.")
        if args.execute:
            result = execute_trade(con, args.proposal_id)
            con.commit()
            print(f"Trade executed. {result['assets_moved']} assets moved.")
        else:
            con.commit()
            print(f"Use 'trade execute --proposal-id {args.proposal_id}' to finalize.")
    elif args.reject:
        update_proposal_status(
            con, args.proposal_id, "rejected",
            responder_note=args.note or "Rejected by user.",
        )
        con.commit()
        print(f"Proposal {args.proposal_id} rejected.")
    elif args.ai_respond:
        game_id = resolve_game_id(con, args.game_id)
        result = ai_gm_respond(con, proposal_id=args.proposal_id, game_id=game_id)
        con.commit()
        action = result["action"]
        eval_data = result.get("evaluation", {})
        if action == "accept":
            print(f"AI GM accepts proposal {args.proposal_id}.")
        elif action == "counter_suggestion":
            shortfall = result.get("shortfall", 0)
            print(f"AI GM suggests counter: needs ~{shortfall:.0f} more value.")
        elif action == "reject":
            print(f"AI GM rejects proposal {args.proposal_id}.")
        print(f"  Reason: {eval_data.get('reason', 'N/A')}")
        print(f"  Value ratio: {eval_data.get('value_ratio', 'N/A')}")
    else:
        raise ValueError("Use --accept, --reject, or --ai-respond.")


def action_execute(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    result = execute_trade(con, args.proposal_id)
    con.commit()
    print(f"Trade {result['proposal_id']} executed. {result['assets_moved']} assets moved.")


def action_cancel(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    proposal = con.execute(
        "SELECT * FROM trade_proposals WHERE proposal_id = ? AND status IN ('proposed', 'countered')",
        (args.proposal_id,),
    ).fetchone()
    if not proposal:
        raise ValueError(f"Proposal {args.proposal_id} not found or not cancellable.")
    update_proposal_status(con, args.proposal_id, "cancelled")
    con.commit()
    print(f"Proposal {args.proposal_id} cancelled.")


def action_ai_propose(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    """Have an AI GM generate proactive trade proposals."""
    ensure_schema(con)
    seed_charts(con)
    assign_charts_to_gms(con)
    game_id = resolve_game_id(con, args.game_id)
    team = get_team(con, args.team)
    season = current_season(con)
    proposal_ids = ai_gm_generate_trade_proposals(
        con, game_id=game_id, team_id=int(team["team_id"]),
        season=season, max_proposals=args.max_proposals,
    )
    con.commit()
    if not proposal_ids:
        print(f"No trade proposals generated for {team['abbreviation']}.")
    else:
        print(f"Generated {len(proposal_ids)} trade proposal(s) for {team['abbreviation']}:")
        for pid in proposal_ids:
            p = con.execute(
                "SELECT * FROM trade_proposals_view WHERE proposal_id = ?",
                (pid,),
            ).fetchone()
            if p:
                print(f"  #{pid}: {p['proposing_team']} -> {p['receiving_team']}")


def action_ai_market(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    """Run the league AI trade market pass."""
    game_id = resolve_game_id(con, args.game_id)
    season = args.season or current_season(con)
    result = ai_gm_process_trade_market(
        con,
        game_id=game_id,
        season=season,
        team_abbr=args.team,
        limit_teams=args.limit_teams,
        max_proposals_per_team=args.max_proposals_per_team,
        include_user_team_as_target=not args.no_user_offers,
        execute_cpu_cpu=not args.no_execute_cpu_cpu,
        ignore_trade_window=args.ignore_window,
        current_date=args.current_date,
    )
    if args.apply:
        con.commit()
        mode = "APPLY"
    else:
        con.rollback()
        mode = "DRY RUN"
    counts = result["counts"]
    print(f"AI trade market: {mode}")
    print(f"  Game: {result['game_id']} | season {result['season']}")
    if result.get("skipped"):
        print(f"  Skipped: {result.get('skip_reason')}")
        if not args.apply:
            print("Dry run only. Add --ignore-window to test generation outside the trade window.")
        return
    print(f"  Teams scanned: {result['teams_scanned']}")
    print(
        "  Proposals: "
        f"{counts['generated']} generated, {counts['user_pending']} user-facing, "
        f"{counts['accepted']} accepted, {counts['countered']} countered, "
        f"{counts['rejected']} rejected, {counts['executed']} executed"
    )
    if result["generated"]:
        print("  Generated IDs: " + ", ".join(str(pid) for pid in result["generated"][:20]))
    if result["user_pending"]:
        print("  User review offers: " + ", ".join(str(pid) for pid in result["user_pending"][:20]))
    if result["errors"]:
        print("  Errors:")
        for err in result["errors"][:10]:
            print(f"    - {err}")
    if not args.apply:
        print("Dry run only. Add --apply to persist proposals/responses/executions.")


def action_valuate(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    """Show the trade value of a player or draft pick."""
    ensure_schema(con)
    chart = args.chart or CHART_JIMMY_JOHNSON
    season = current_season(con)

    if args.player_id:
        val = player_trade_value(con, int(args.player_id), season, chart)
        p = con.execute(
            "SELECT first_name, last_name, position, overall, age FROM players WHERE player_id = ?",
            (int(args.player_id),),
        ).fetchone()
        if p:
            print(f"Player: {p['first_name']} {p['last_name']} ({p['position']}, OVR {p['overall']}, Age {p['age']})")
        print(f"Trade value on {chart} chart: {val:.1f}")
    elif args.pick_number:
        val = pick_value(con, chart, int(args.pick_number))
        print(f"Pick #{args.pick_number} on {chart} chart: {val:.2f}")
    else:
        raise ValueError("Provide --player-id or --pick-number.")


def action_negotiation_log(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    rows = con.execute(
        """
        SELECT tnl.*, t.abbreviation AS team
        FROM trade_negotiation_log tnl
        JOIN teams t ON t.team_id = tnl.team_id
        WHERE tnl.proposal_id = ?
        ORDER BY tnl.created_at
        """,
        (args.proposal_id,),
    ).fetchall()
    if not rows:
        print(f"No negotiation log for proposal {args.proposal_id}.")
        return
    for row in rows:
        print(f"  [{row['created_at']}] {row['team']} {row['action']}: {row['message'] or '-'}")
        if row["value_assessment"]:
            print(f"    Assessment: {row['value_assessment']}")


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NFL GM Sim trade engine.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("setup", help="Create trade engine tables and seed value charts.")

    assign_parser = subparsers.add_parser("assign-charts", help="Assign trade value charts to AI GMs.")
    assign_parser.add_argument("--seed", type=int, default=42, help="RNG seed for assignment.")

    chart_parser = subparsers.add_parser("show-chart", help="Show a trade value chart.")
    chart_parser.add_argument("--chart", choices=ALL_CHARTS, default=CHART_JIMMY_JOHNSON)
    chart_parser.add_argument("--limit", type=int, default=32)

    valuate_parser = subparsers.add_parser("valuate", help="Show trade value of a player or pick.")
    valuate_parser.add_argument("--player-id", type=int)
    valuate_parser.add_argument("--pick-number", type=int)
    valuate_parser.add_argument("--chart", choices=ALL_CHARTS, default=CHART_JIMMY_JOHNSON)

    propose_parser = subparsers.add_parser("propose", help="Create a trade proposal.")
    propose_parser.add_argument("--game-id")
    propose_parser.add_argument("--proposing-team", required=True)
    propose_parser.add_argument("--receiving-team", required=True)
    propose_parser.add_argument("--offering", nargs="+", help="Assets offered: player:ID or pick:PICK_ID or pick:YEAR:ROUND")
    propose_parser.add_argument("--requesting", nargs="+", help="Assets requested: player:ID or pick:PICK_ID or pick:YEAR:ROUND")
    propose_parser.add_argument("--deadline")
    propose_parser.add_argument("--note")

    list_parser = subparsers.add_parser("list", help="List trade proposals.")
    list_parser.add_argument("--game-id")
    list_parser.add_argument("--team")
    list_parser.add_argument("--status", choices=list(PROPOSAL_STATUSES))
    list_parser.add_argument("--limit", type=int, default=20)

    show_parser = subparsers.add_parser("show", help="Show trade proposal details.")
    show_parser.add_argument("--proposal-id", type=int, required=True)

    respond_parser = subparsers.add_parser("respond", help="Accept, reject, or have AI GM respond to a proposal.")
    respond_parser.add_argument("--game-id")
    respond_parser.add_argument("--proposal-id", type=int, required=True)
    respond_parser.add_argument("--accept", action="store_true")
    respond_parser.add_argument("--reject", action="store_true")
    respond_parser.add_argument("--ai-respond", action="store_true", help="Let the AI GM evaluate and respond.")
    respond_parser.add_argument("--execute", action="store_true", help="Execute immediately after accepting.")
    respond_parser.add_argument("--note")

    execute_parser = subparsers.add_parser("execute", help="Execute an accepted trade.")
    execute_parser.add_argument("--proposal-id", type=int, required=True)

    cancel_parser = subparsers.add_parser("cancel", help="Cancel a proposed trade.")
    cancel_parser.add_argument("--proposal-id", type=int, required=True)

    ai_propose_parser = subparsers.add_parser("ai-propose", help="Have an AI GM generate trade proposals.")
    ai_propose_parser.add_argument("--game-id")
    ai_propose_parser.add_argument("--team", required=True)
    ai_propose_parser.add_argument("--max-proposals", type=int, default=3)

    ai_market_parser = subparsers.add_parser("ai-market", help="Run CPU trade-market proposals, responses, and CPU-to-CPU execution.")
    ai_market_parser.add_argument("--game-id")
    ai_market_parser.add_argument("--season", type=int)
    ai_market_parser.add_argument("--team", help="Optional single CPU team to scan.")
    ai_market_parser.add_argument("--limit-teams", type=int, default=8)
    ai_market_parser.add_argument("--max-proposals-per-team", type=int, default=1)
    ai_market_parser.add_argument("--no-user-offers", action="store_true", help="Prevent CPU teams from proposing trades to the user team.")
    ai_market_parser.add_argument("--no-execute-cpu-cpu", action="store_true", help="Leave accepted CPU-to-CPU trades unexecuted.")
    ai_market_parser.add_argument("--ignore-window", action="store_true", help="Dry-test/generate even when the calendar trade window is closed.")
    ai_market_parser.add_argument("--current-date", help="Override the date used for trade-window checks.")
    ai_market_parser.add_argument("--apply", action="store_true", help="Persist generated trade activity.")

    neg_log_parser = subparsers.add_parser("neg-log", help="Show negotiation log for a proposal.")
    neg_log_parser.add_argument("--proposal-id", type=int, required=True)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    con = connect(args.db)
    try:
        if args.command == "setup":
            action_setup(con, args)
        elif args.command == "assign-charts":
            action_assign_charts(con, args)
        elif args.command == "show-chart":
            action_show_chart(con, args)
        elif args.command == "valuate":
            action_valuate(con, args)
        elif args.command == "propose":
            action_propose(con, args)
        elif args.command == "list":
            action_list_proposals(con, args)
        elif args.command == "show":
            action_show_proposal(con, args)
        elif args.command == "respond":
            action_respond(con, args)
        elif args.command == "execute":
            action_execute(con, args)
        elif args.command == "cancel":
            action_cancel(con, args)
        elif args.command == "ai-propose":
            action_ai_propose(con, args)
        elif args.command == "ai-market":
            action_ai_market(con, args)
        elif args.command == "neg-log":
            action_negotiation_log(con, args)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
