#!/usr/bin/env python3
"""Free agency market processor.

Cadence:
- Day 1 can be advanced by hour, which lets the busy opening wave feel active.
- Once the hourly window is over, the period switches to daily advancement.

This is intentionally a processor, not a polished UI. It writes a durable market
state that a future UI can render and control.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import roster_actions
import contract_negotiations
import cpu_depth_chart
import league_calendar
import player_personalities
import pro_player_fog
from setup_contract_years import rebuild_contract_year, sync_team_cap_space
from setup_transactions_cap_ledger import insert_transaction, snapshot_cap_ledger

try:
    import jersey_numbers
except ImportError:  # pragma: no cover - supports package-style imports.
    from tools import jersey_numbers


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
SOURCE = "free_agency_processor"
PHASE = "Free Agency"

POSITION_GROUP_BY_POS = {
    "QB": "QB",
    "RB": "RB",
    "FB": "RB",
    "WR": "WR",
    "SWR": "WR",
    "TE": "TE",
    "LT": "OT",
    "RT": "OT",
    "OT": "OT",
    "LG": "IOL",
    "RG": "IOL",
    "C": "IOL",
    "OG": "IOL",
    "IOL": "IOL",
    "EDGE": "EDGE",
    "DE": "EDGE",
    "OLB": "EDGE",
    "IDL": "IDL",
    "DT": "IDL",
    "NT": "IDL",
    "LB": "LB",
    "ILB": "LB",
    "MLB": "LB",
    "CB": "CB",
    "NB": "CB",
    "FS": "S",
    "SS": "S",
    "S": "S",
    "K": "K",
    "P": "P",
    "LS": "LS",
}

STARTER_SLOTS_BY_GROUP = {
    "QB": 1,
    "RB": 1,
    "WR": 3,
    "TE": 1,
    "OT": 2,
    "IOL": 3,
    "EDGE": 2,
    "IDL": 2,
    "LB": 2,
    "CB": 3,
    "S": 2,
    "K": 1,
    "P": 1,
    "LS": 1,
}

ROOM_IDEAL_BY_GROUP = {
    "QB": 3,
    "RB": 4,
    "WR": 6,
    "TE": 3,
    "OT": 4,
    "IOL": 5,
    "EDGE": 5,
    "IDL": 5,
    "LB": 5,
    "CB": 6,
    "S": 5,
    "K": 1,
    "P": 1,
    "LS": 1,
}

STARTER_FLOOR_BY_GROUP = {
    "QB": 77,
    "RB": 72,
    "WR": 73,
    "TE": 70,
    "OT": 71,
    "IOL": 71,
    "EDGE": 73,
    "IDL": 72,
    "LB": 70,
    "CB": 72,
    "S": 71,
    "K": 68,
    "P": 68,
    "LS": 60,
}

MARKET_TIER_MULTIPLIERS = {
    "Premium": 1.16,
    "Starter": 1.18,
    "Rotation": 1.10,
    "Depth": 1.04,
    "Camp": 1.00,
}

MARKET_GROUP_MULTIPLIERS = {
    "QB": 1.08,
    "RB": 1.08,
    "WR": 1.14,
    "TE": 1.10,
    "OT": 1.12,
    "IOL": 1.02,
    "EDGE": 1.06,
    "IDL": 1.02,
    "LB": 1.05,
    "CB": 1.00,
    "S": 1.06,
    "K": 1.02,
    "P": 1.02,
    "LS": 1.00,
    "ST": 1.02,
}

MARKET_TIER_FLOORS = {
    "Premium": 9_000_000,
    "Starter": 6_000_000,
    "Rotation": 2_800_000,
    "Depth": 1_300_000,
    "Camp": 915_000,
}

MARKET_GROUP_TIER_FLOORS = {
    ("Premium", "QB"): 12_000_000,
    ("Premium", "RB"): 11_500_000,
    ("Premium", "WR"): 20_000_000,
    ("Premium", "TE"): 15_000_000,
    ("Premium", "OT"): 18_000_000,
    ("Premium", "IOL"): 13_000_000,
    ("Premium", "EDGE"): 15_000_000,
    ("Premium", "IDL"): 16_000_000,
    ("Premium", "LB"): 10_000_000,
    ("Premium", "CB"): 16_000_000,
    ("Premium", "S"): 12_000_000,
    ("Premium", "ST"): 3_200_000,
    ("Starter", "QB"): 7_500_000,
    ("Starter", "RB"): 7_500_000,
    ("Starter", "WR"): 10_500_000,
    ("Starter", "TE"): 7_500_000,
    ("Starter", "OT"): 14_500_000,
    ("Starter", "IOL"): 8_500_000,
    ("Starter", "EDGE"): 10_500_000,
    ("Starter", "IDL"): 9_500_000,
    ("Starter", "LB"): 7_000_000,
    ("Starter", "CB"): 9_000_000,
    ("Starter", "S"): 8_500_000,
    ("Starter", "ST"): 2_500_000,
}

CPU_FA_CAP_RESERVE = 22_000_000
CPU_FA_LATE_MARKET_RESERVE = 8_000_000
CPU_FINAL_SIGNING_MIN_BUFFER = 4_000_000

CPU_GROUP_SPEND_LIMITS = {
    "QB": 30_000_000,
    "RB": 13_000_000,
    "WR": 38_000_000,
    "TE": 19_000_000,
    "OT": 39_000_000,
    "IOL": 24_000_000,
    "EDGE": 34_000_000,
    "IDL": 28_000_000,
    "LB": 19_000_000,
    "CB": 28_000_000,
    "S": 24_000_000,
    "ST": 6_000_000,
}

CPU_ACTIVE_ROOM_SPEND_MULTIPLIER = {
    "QB": 1.15,
    "RB": 1.35,
    "WR": 1.55,
    "TE": 1.45,
    "OT": 1.55,
    "IOL": 1.50,
    "EDGE": 1.55,
    "IDL": 1.45,
    "LB": 1.35,
    "CB": 1.55,
    "S": 1.45,
    "ST": 1.15,
}

CPU_GROUP_OFFER_COUNT_LIMITS = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 2,
    "OT": 2,
    "IOL": 3,
    "EDGE": 2,
    "IDL": 2,
    "LB": 2,
    "CB": 2,
    "S": 2,
    "ST": 1,
}

CPU_GROUP_DEPTH_COUNT_LIMITS = {
    "QB": 3,
    "RB": 5,
    "WR": 7,
    "TE": 4,
    "OT": 5,
    "IOL": 6,
    "EDGE": 6,
    "IDL": 6,
    "LB": 6,
    "CB": 7,
    "S": 6,
    "ST": 1,
}

POST_DRAFT_DEPTH_TEAM_ENTRY_RESERVE = 4_000_000
POST_DRAFT_DEPTH_CHEAP_DEAL_RESERVE = 4_000_000
POST_DRAFT_DEPTH_MODEST_DEAL_RESERVE = 6_000_000
POST_DRAFT_DEPTH_CHEAP_AAV = 2_500_000
POST_DRAFT_DEPTH_MODEST_AAV = 4_000_000

MINIMUM_AAV_RATIO_BY_TIER = {
    "Premium": 0.70,
    "Starter": 0.68,
    "Rotation": 0.62,
    "Depth": 0.58,
    "Camp": 0.55,
}

POST_DRAFT_ASK_DISCOUNT_BY_TIER = {
    "Premium": 0.84,
    "Starter": 0.80,
    "Rotation": 0.76,
    "Depth": 0.72,
    "Camp": 0.70,
}

POST_DRAFT_MINIMUM_RATIO_BY_TIER = {
    "Premium": 0.62,
    "Starter": 0.58,
    "Rotation": 0.52,
    "Depth": 0.48,
    "Camp": 0.46,
}

POST_DRAFT_STRATEGIES = {"soften", "normal", "firm_floor", "injury_wait"}

POSITION_OLD_AGE = {
    "QB": 36,
    "RB": 29,
    "TE": 31,
    "WR": 30,
    "CB": 30,
    "S": 31,
    "LB": 31,
    "EDGE": 31,
    "IDL": 32,
    "OT": 32,
    "IOL": 32,
    "ST": 34,
}


def round_to(value: float, increment: int = 50_000) -> int:
    return int(round(value / increment) * increment)


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def row_value(row: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def position_group_for(position: str | None) -> str:
    return POSITION_GROUP_BY_POS.get(str(position or "").upper(), str(position or "").upper() or "UNK")


def active_game_id_expr() -> str:
    return "(SELECT setting_value FROM game_settings WHERE setting_key = 'active_game_id')"


def active_game_start_year_expr() -> str:
    return (
        "COALESCE("
        "(SELECT start_league_year FROM game_saves WHERE game_id = "
        f"{active_game_id_expr()} LIMIT 1), "
        "(SELECT CAST(setting_value AS INTEGER) FROM game_settings WHERE setting_key = 'current_season')"
        ")"
    )


def ensure_schema(con: sqlite3.Connection) -> None:
    roster_actions.ensure_all_schema(con)
    player_personalities.ensure_schema(con)
    con.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS free_agent_profiles (
            player_id INTEGER PRIMARY KEY REFERENCES players(player_id) ON DELETE CASCADE,
            position_group TEXT NOT NULL,
            previous_team TEXT,
            market_tier TEXT NOT NULL,
            asking_aav INTEGER NOT NULL,
            minimum_aav INTEGER NOT NULL,
            preferred_years INTEGER NOT NULL DEFAULT 1,
            guarantee_pct INTEGER NOT NULL DEFAULT 0,
            contract_priority INTEGER NOT NULL DEFAULT 10,
            contender_priority INTEGER NOT NULL DEFAULT 10,
            role_priority INTEGER NOT NULL DEFAULT 10,
            hometown_priority INTEGER NOT NULL DEFAULT 5,
            patience INTEGER NOT NULL DEFAULT 10,
            preferred_teams TEXT,
            hometown_teams TEXT,
            motivation TEXT,
            signing_notes TEXT,
            source TEXT NOT NULL,
            source_url TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS free_agency_periods (
            league_year INTEGER PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'not_started',
            current_stage TEXT NOT NULL DEFAULT 'day_one_hourly',
            current_date TEXT NOT NULL,
            current_hour INTEGER NOT NULL DEFAULT 12,
            day_count INTEGER NOT NULL DEFAULT 1,
            first_day_start_hour INTEGER NOT NULL DEFAULT 12,
            first_day_end_hour INTEGER NOT NULL DEFAULT 20,
            started_at TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            notes TEXT
        );

        DROP TRIGGER IF EXISTS trg_free_agency_periods_no_backdate;
        CREATE TRIGGER trg_free_agency_periods_no_backdate
        BEFORE UPDATE OF "current_date" ON free_agency_periods
        FOR EACH ROW
        WHEN OLD."current_date" IS NOT NULL
          AND NEW."current_date" IS NOT NULL
          AND date(NEW."current_date") < date(OLD."current_date")
        BEGIN
            SELECT RAISE(IGNORE);
        END;

        CREATE TABLE IF NOT EXISTS free_agency_player_markets (
            league_year INTEGER NOT NULL,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            position_group TEXT NOT NULL,
            market_tier TEXT NOT NULL,
            asking_aav INTEGER NOT NULL,
            minimum_aav INTEGER NOT NULL,
            preferred_years INTEGER NOT NULL DEFAULT 1,
            guarantee_pct INTEGER NOT NULL DEFAULT 0,
            market_heat INTEGER NOT NULL DEFAULT 50,
            patience INTEGER NOT NULL DEFAULT 10,
            post_draft_strategy TEXT NOT NULL DEFAULT 'normal',
            holdout_until TEXT,
            holdout_reason TEXT,
            status TEXT NOT NULL DEFAULT 'available',
            signed_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            signed_offer_id INTEGER,
            last_offer_at TEXT,
            decision_notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (league_year, player_id)
        );

        CREATE TABLE IF NOT EXISTS free_agency_offers (
            offer_id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_year INTEGER NOT NULL,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            years INTEGER NOT NULL,
            aav INTEGER NOT NULL,
            total_value INTEGER NOT NULL,
            signing_bonus INTEGER NOT NULL DEFAULT 0,
            guarantee_pct INTEGER NOT NULL DEFAULT 0,
            contract_structure TEXT NOT NULL DEFAULT 'balanced',
            status TEXT NOT NULL DEFAULT 'pending',
            submitted_date TEXT NOT NULL,
            submitted_hour INTEGER,
            decided_date TEXT,
            decided_hour INTEGER,
            decision_score REAL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_free_agency_offers_player
            ON free_agency_offers(league_year, player_id, status);

        CREATE INDEX IF NOT EXISTS idx_free_agency_offers_team
            ON free_agency_offers(league_year, team_id, status);

        CREATE INDEX IF NOT EXISTS idx_players_free_agent_status
            ON players(status, team_id, player_id);

        CREATE INDEX IF NOT EXISTS idx_player_role_scores_market
            ON player_role_scores(scheme_key, season, player_id, role_score);

        CREATE INDEX IF NOT EXISTS idx_free_agency_markets_status
            ON free_agency_player_markets(league_year, status, market_heat, asking_aav);

        CREATE TABLE IF NOT EXISTS free_agency_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_year INTEGER NOT NULL,
            event_date TEXT NOT NULL,
            event_hour INTEGER,
            event_type TEXT NOT NULL,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            offer_id INTEGER REFERENCES free_agency_offers(offer_id) ON DELETE SET NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        DROP VIEW IF EXISTS free_agency_period_view;
        CREATE VIEW free_agency_period_view AS
        SELECT *
        FROM free_agency_periods;

        DROP VIEW IF EXISTS free_agency_board_view;
        CREATE VIEW free_agency_board_view AS
        SELECT
            m.league_year,
            m.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.years_exp,
            p.is_rookie,
            p.overall,
            p.potential,
            p.college,
            p.status AS player_status,
            m.position_group,
            m.market_tier,
            m.asking_aav,
            m.minimum_aav,
            m.preferred_years,
            m.guarantee_pct,
            m.market_heat,
            m.patience,
            COALESCE(m.post_draft_strategy, 'normal') AS post_draft_strategy,
            m.holdout_until,
            m.holdout_reason,
            m.status AS market_status,
            m.signed_team_id,
            signed_team.abbreviation AS signed_team,
            profile.previous_team,
            profile.source AS profile_source,
            profile.preferred_teams,
            profile.hometown_teams,
            profile.motivation,
            profile.signing_notes,
            COALESCE(pref.preference_archetype, 'balanced') AS preference_archetype,
            COALESCE(pref.money_priority, profile.contract_priority, 10) AS money_priority,
            COALESCE(pref.security_priority, 10) AS security_priority,
            COALESCE(pref.contender_priority, profile.contender_priority, 10) AS contender_priority,
            COALESCE(pref.role_priority, profile.role_priority, 10) AS role_priority,
            COALESCE(pref.loyalty_priority, 10) AS loyalty_priority,
            COALESCE(pref.location_priority, profile.hometown_priority, 8) AS location_priority,
            COALESCE(pref.contract_year_preference, m.preferred_years) AS contract_year_preference,
            COALESCE(pref.market_patience_modifier, 0) AS market_patience_modifier,
            COALESCE(pref.hometown_discount_pct, 0) AS hometown_discount_pct,
            COALESCE(pref.contender_discount_pct, 0) AS contender_discount_pct,
            COALESCE(pref.minimum_over_ask_pct, 0) AS minimum_over_ask_pct,
            COALESCE(score.role_score, p.overall) AS market_score,
            offer_counts.pending_offers,
            offer_counts.best_aav
        FROM free_agency_player_markets m
        JOIN players p ON p.player_id = m.player_id
        LEFT JOIN teams signed_team ON signed_team.team_id = m.signed_team_id
        LEFT JOIN free_agent_profiles profile ON profile.player_id = m.player_id
        LEFT JOIN player_free_agency_preferences pref
          ON pref.player_id = m.player_id
         AND pref.game_id = {active_game_id_expr()}
         AND pref.season = {active_game_start_year_expr()}
        LEFT JOIN (
            SELECT player_id, MAX(role_score) AS role_score
            FROM player_role_scores
            WHERE scheme_key = 'default'
              AND season = (SELECT CAST(setting_value AS INTEGER) FROM game_settings WHERE setting_key = 'current_season')
            GROUP BY player_id
        ) score ON score.player_id = m.player_id
        LEFT JOIN (
            SELECT
                league_year,
                player_id,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_offers,
                MAX(CASE WHEN status = 'pending' THEN aav ELSE NULL END) AS best_aav
            FROM free_agency_offers
            GROUP BY league_year, player_id
        ) offer_counts
          ON offer_counts.league_year = m.league_year
         AND offer_counts.player_id = m.player_id;

        DROP VIEW IF EXISTS free_agency_offers_view;
        CREATE VIEW free_agency_offers_view AS
        SELECT
            o.*,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position
        FROM free_agency_offers o
        JOIN teams t ON t.team_id = o.team_id
        JOIN players p ON p.player_id = o.player_id;
        """
    )
    for table, column, ddl in (
        ("free_agency_offers", "contract_structure", "TEXT NOT NULL DEFAULT 'balanced'"),
        ("contracts", "guarantee_pct", "INTEGER NOT NULL DEFAULT 0"),
        ("contracts", "salary_structure", "TEXT NOT NULL DEFAULT 'balanced'"),
        ("free_agency_player_markets", "post_draft_strategy", "TEXT NOT NULL DEFAULT 'normal'"),
        ("free_agency_player_markets", "holdout_until", "TEXT"),
        ("free_agency_player_markets", "holdout_reason", "TEXT"),
    ):
        try:
            existing = {str(row["name"]) for row in con.execute(f"PRAGMA table_info({table})")}
            if table_exists(con, table) and column not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        except sqlite3.Error:
            pass
    if table_exists(con, "game_saves"):
        con.executescript(
            """
            DROP TRIGGER IF EXISTS trg_game_saves_no_backdate;
            CREATE TRIGGER trg_game_saves_no_backdate
            BEFORE UPDATE OF "current_date" ON game_saves
            FOR EACH ROW
            WHEN OLD."current_date" IS NOT NULL
              AND NEW."current_date" IS NOT NULL
              AND date(NEW."current_date") < date(OLD."current_date")
            BEGIN
                SELECT RAISE(IGNORE);
            END;
            """
        )


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def date_text(value: date) -> str:
    return value.strftime("%Y-%m-%d")


def current_setting(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = ?",
        (key,),
    ).fetchone()
    if row:
        return str(row["setting_value"])
    if key in {"current_game_date", "current_league_year", "active_game_id"} and table_exists(con, "active_game_save_view"):
        column = {
            "current_game_date": '"current_date"',
            "current_league_year": "current_league_year",
            "active_game_id": "game_id",
        }[key]
        active = con.execute(f"SELECT {column} AS value FROM active_game_save_view LIMIT 1").fetchone()
        if active and active["value"] is not None:
            return str(active["value"])
    return None


def current_game_date_value(con: sqlite3.Connection) -> str | None:
    values: list[str] = []
    setting = current_setting(con, "current_game_date")
    if setting:
        values.append(setting)
    if table_exists(con, "active_game_save_view"):
        row = con.execute('SELECT "current_date" FROM active_game_save_view LIMIT 1').fetchone()
        if row and row["current_date"]:
            values.append(str(row["current_date"]))
    return max(values) if values else None


def default_league_year(con: sqlite3.Connection) -> int:
    contract_year = current_setting(con, "current_contract_year")
    if contract_year:
        return int(contract_year)
    sim_year = int(current_setting(con, "current_league_year") or current_setting(con, "current_season") or 2026)
    current_date = current_game_date_value(con)
    if current_date:
        parsed = parse_date(current_date)
        phase = current_calendar_phase(con)
        phase_code = str(row_value(phase, "phase_code", "") or "") if phase else ""
        if parsed.month <= 5 and phase_code not in {"REGULAR_SEASON", "POSTSEASON"}:
            return sim_year + 1
    return sim_year


def default_start_date(league_year: int) -> str:
    first = date(league_year, 3, 1)
    offset = (2 - first.weekday()) % 7
    return date_text(first + timedelta(days=offset + 7))


def money(value: int | None) -> str:
    if value is None:
        return "-"
    return f"${value / 1_000_000:.1f}M" if abs(value) >= 1_000_000 else f"${value:,}"


def event_time(period: sqlite3.Row) -> tuple[str, int | None]:
    return str(period["current_date"]), int(period["current_hour"]) if period["current_stage"] == "day_one_hourly" else None


def log_event(
    con: sqlite3.Connection,
    *,
    league_year: int,
    event_date: str,
    event_hour: int | None,
    event_type: str,
    message: str,
    team_id: int | None = None,
    player_id: int | None = None,
    offer_id: int | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO free_agency_events (
            league_year, event_date, event_hour, event_type,
            team_id, player_id, offer_id, message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (league_year, event_date, event_hour, event_type, team_id, player_id, offer_id, message),
    )


def team_by_abbr(con: sqlite3.Connection, abbreviation: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM teams WHERE abbreviation = ?",
        (abbreviation.upper(),),
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown team abbreviation: {abbreviation}")
    return row


def player_by_id_or_name(con: sqlite3.Connection, player: str) -> sqlite3.Row:
    if player.isdigit():
        row = con.execute(
            "SELECT * FROM players WHERE player_id = ?",
            (int(player),),
        ).fetchone()
        if row:
            return row
    return roster_actions.find_player(con, player, require_free_agent=True)


def market_heat_for(tier: str, asking_aav: int, score: int, age: int | None) -> int:
    base = {
        "Premium": 88,
        "Starter": 74,
        "Rotation": 56,
        "Depth": 38,
        "Camp": 20,
    }.get(tier, 45)
    base += max(-10, min(12, score - 68))
    if asking_aav >= 20_000_000:
        base += 6
    elif asking_aav >= 12_000_000:
        base += 3
    if age and age >= 32:
        base -= 8
    return max(5, min(99, base))


def market_age_factor(group: str, tier: str, age: int | None) -> float:
    if age is None:
        return 1.0
    if group == "QB":
        if age >= 39:
            return 0.72
        if age >= 36:
            return 0.86
        if age <= 25 and tier in {"Premium", "Starter"}:
            return 1.04
        return 1.0
    if group == "RB":
        if age >= 31:
            return 0.82
        if age >= 29:
            return 0.92
        if age <= 25 and tier in {"Premium", "Starter"}:
            return 1.06
        return 1.0
    if group == "TE":
        if age >= 33:
            return 0.74
        if age >= 31:
            return 0.82
        if age >= 29:
            return 0.90
        if age <= 25 and tier in {"Premium", "Starter"}:
            return 1.04
        return 1.0
    if group == "WR":
        if age >= 34:
            return 0.66
        if age >= 32:
            return 0.76
        if age >= 30:
            return 0.88
        if age <= 25 and tier in {"Premium", "Starter"}:
            return 1.05
        return 1.0
    if group in {"CB", "S", "LB"}:
        if age >= 34:
            return 0.68
        if age >= 32:
            return 0.78
        if age >= 30:
            return 0.87
        if age <= 25 and tier in {"Premium", "Starter"}:
            return 1.04
        return 1.0
    if group == "EDGE":
        if age >= 35:
            return 0.70
        if age >= 33:
            return 0.78
        if age >= 31:
            return 0.90
        if age <= 25 and tier in {"Premium", "Starter"}:
            return 1.04
        return 1.0
    if group == "IDL":
        if age >= 35:
            return 0.70
        if age >= 33:
            return 0.78
        if age >= 31:
            return 0.89
        if age <= 25 and tier in {"Premium", "Starter"}:
            return 1.04
        return 1.0
    if group in {"OT", "IOL"}:
        if age >= 35:
            return 0.78
        if age >= 33:
            return 0.86
        if age >= 31:
            return 0.94
        if age <= 25 and tier in {"Premium", "Starter"}:
            return 1.03
        return 1.0
    if age >= 35:
        return 0.76
    if age >= 33:
        return 0.84
    if age >= 31:
        return 0.92
    if age <= 25 and tier in {"Premium", "Starter"}:
        return 1.05
    return 1.0


def normalized_group(row: sqlite3.Row | dict[str, Any]) -> str:
    group = str(row_value(row, "position_group", row_value(row, "position", "UNK")) or "UNK").upper()
    if group in {"K", "P", "LS"}:
        return "ST"
    return group


def market_tier_score(row: sqlite3.Row | dict[str, Any]) -> int:
    """Anchor market tier to true OVR, with only a small role/potential bump."""
    overall = true_overall(row)
    role_score = float(row_value(row, "market_score", overall) or overall)
    potential = int(row_value(row, "potential", overall) or overall)
    age_value = row_value(row, "age")
    age = int(age_value) if age_value is not None else 28
    bonus = 0
    if role_score >= overall + 8:
        bonus += 2 if overall >= 72 else 1
    elif role_score >= overall + 5 and overall >= 70:
        bonus += 1
    if age <= 26 and potential >= overall + 8:
        bonus += 2
    elif age <= 27 and potential >= overall + 5:
        bonus += 1
    return int(max(overall, min(role_score, overall + min(4, bonus))))


def normalized_tier(row: sqlite3.Row | dict[str, Any]) -> str:
    tier = str(row_value(row, "market_tier", "Depth") or "Depth").title()
    if tier in {"Core", "Franchise"}:
        tier = "Premium"
    if tier not in {"Premium", "Starter", "Rotation", "Depth", "Camp"}:
        tier = "Depth"

    group = normalized_group(row)
    score = market_tier_score(row)
    if group == "QB":
        score_tier = "Premium" if score >= 80 else "Starter" if score >= 72 else "Rotation" if score >= 65 else "Depth" if score >= 58 else "Camp"
    elif group in {"WR", "OT", "EDGE", "CB", "IDL", "IOL"}:
        score_tier = "Premium" if score >= 78 else "Starter" if score >= 72 else "Rotation" if score >= 66 else "Depth" if score >= 60 else "Camp"
    elif group in {"RB", "TE", "LB", "S"}:
        score_tier = "Premium" if score >= 78 else "Starter" if score >= 70 else "Rotation" if score >= 64 else "Depth" if score >= 58 else "Camp"
    elif group == "ST":
        score_tier = "Premium" if score >= 78 else "Starter" if score >= 70 else "Rotation" if score >= 62 else "Depth"
    else:
        score_tier = "Starter" if score >= 72 else "Rotation" if score >= 65 else "Depth" if score >= 58 else "Camp"

    order = {"Camp": 0, "Depth": 1, "Rotation": 2, "Starter": 3, "Premium": 4}
    return tier if order[tier] <= order[score_tier] else score_tier


def deactivate_prior_player_contracts(con: sqlite3.Connection, player_id: int, *, new_contract_id: int, new_start_year: int) -> int:
    """Retire older active deals once a replacement/future deal is in place."""
    cur = con.execute(
        """
        UPDATE contracts
        SET is_active = 0
        WHERE player_id = ?
          AND contract_id <> ?
          AND is_active = 1
          AND COALESCE(end_year, ?) < ?
        """,
        (int(player_id), int(new_contract_id), int(new_start_year), int(new_start_year)),
    )
    changed = int(cur.rowcount or 0)
    if changed and table_exists(con, "contract_years"):
        con.execute(
            """
            UPDATE contract_years
            SET is_active = 0
            WHERE player_id = ?
              AND season < ?
              AND contract_id <> ?
            """,
            (int(player_id), int(new_start_year), int(new_contract_id)),
        )
    return changed


def deactivate_elapsed_active_contracts(con: sqlite3.Connection, league_year: int) -> int:
    """Clean old active flags that should no longer participate in active joins."""
    if not table_exists(con, "contracts"):
        return 0
    rows = con.execute(
        """
        SELECT old.contract_id, old.player_id
        FROM contracts old
        WHERE old.is_active = 1
          AND COALESCE(old.end_year, ?) < ?
          AND EXISTS (
              SELECT 1
              FROM contracts newer
              WHERE newer.player_id = old.player_id
                AND newer.contract_id <> old.contract_id
                AND newer.is_active = 1
                AND COALESCE(newer.start_year, ?) >= ?
          )
        """,
        (int(league_year), int(league_year), int(league_year), int(league_year)),
    ).fetchall()
    if not rows:
        return 0
    contract_ids = [int(row["contract_id"]) for row in rows]
    con.executemany(
        "UPDATE contracts SET is_active = 0 WHERE contract_id = ?",
        [(contract_id,) for contract_id in contract_ids],
    )
    if table_exists(con, "contract_years"):
        con.executemany(
            "UPDATE contract_years SET is_active = 0 WHERE contract_id = ?",
            [(contract_id,) for contract_id in contract_ids],
        )
    return len(contract_ids)


def market_price_ceiling(row: sqlite3.Row | dict[str, Any], tier: str, *, post_draft: bool) -> int:
    group = normalized_group(row)
    score = market_tier_score(row)
    if group == "QB":
        if score < 64:
            base = 5_000_000
        elif score < 68:
            base = 8_000_000
        elif score < 72:
            base = 12_000_000
        elif score < 74:
            base = 14_000_000
        elif score < 76:
            base = 18_000_000
        elif score < 78:
            base = 20_000_000
        elif score < 80:
            base = 26_000_000
        elif score < 82:
            base = 36_000_000
        else:
            base = 55_000_000
    else:
        if score < 64:
            base = 3_500_000
        elif score < 68:
            base = 6_000_000
        elif score < 72:
            base = 9_500_000
        elif score < 76:
            base = 15_000_000
        elif score < 80:
            base = 23_000_000
        else:
            base = 38_000_000
    group_multiplier = {
        "WR": 1.18,
        "OT": 1.18,
        "EDGE": 1.18,
        "CB": 1.06,
        "IDL": 1.03,
        "IOL": 0.94,
        "TE": 1.08,
        "S": 0.92,
        "LB": 0.88,
        "RB": 0.78,
        "ST": 0.42,
    }.get(group, 1.0)
    if group == "WR" and score >= 88:
        group_multiplier = 1.32
    if group == "TE" and score >= 86:
        group_multiplier = 1.18
    tier_multiplier = {"Premium": 1.08, "Starter": 1.0, "Rotation": 0.88, "Depth": 0.72, "Camp": 0.60}.get(tier, 0.85)
    ceiling = base * group_multiplier * tier_multiplier
    if post_draft:
        ceiling *= 0.90
    return max(915_000, round_to(ceiling, 100_000))


def true_overall(row: sqlite3.Row | dict[str, Any]) -> int:
    return int(row_value(row, "overall", row_value(row, "market_score", 60)) or 60)


def cpu_cap_reserve_for_period(period: sqlite3.Row | dict[str, Any]) -> int:
    stage = str(row_value(period, "current_stage", "") or "")
    day_count = int(row_value(period, "day_count", 1) or 1)
    if stage == "day_one_hourly":
        return CPU_FA_CAP_RESERVE
    if day_count <= 10:
        return 18_000_000
    if day_count <= 24:
        return 12_000_000
    return max(CPU_FA_LATE_MARKET_RESERVE, 10_000_000)


def cpu_post_draft_depth_team_entry_reserve(period: sqlite3.Row | dict[str, Any]) -> int:
    return min(cpu_cap_reserve_for_period(period), POST_DRAFT_DEPTH_TEAM_ENTRY_RESERVE)


def cpu_post_draft_depth_offer_reserve(
    period: sqlite3.Row | dict[str, Any],
    *,
    group: str,
    aav: int,
    years: int,
    starter_hole: bool,
    guarantee_pct: int = 0,
) -> int:
    base = cpu_cap_reserve_for_period(period)
    if group == "QB" or years > 1 or guarantee_pct > 35:
        return base
    if starter_hole and aav > POST_DRAFT_DEPTH_MODEST_AAV:
        return base
    if aav <= POST_DRAFT_DEPTH_CHEAP_AAV:
        return min(base, POST_DRAFT_DEPTH_CHEAP_DEAL_RESERVE)
    if aav <= POST_DRAFT_DEPTH_MODEST_AAV:
        return min(base, POST_DRAFT_DEPTH_MODEST_DEAL_RESERVE)
    return base


def cpu_excluded_user_team(con: sqlite3.Connection, args: argparse.Namespace) -> str | None:
    if bool(getattr(args, "cpu_controls_user_team", False)):
        return None
    return contract_negotiations.active_user_team(con)


def cpu_controlled_user_team(con: sqlite3.Connection, args: argparse.Namespace) -> str | None:
    if not bool(getattr(args, "cpu_controls_user_team", False)):
        return None
    team = contract_negotiations.active_user_team(con)
    return team.upper() if team else None


def general_market_excluded_user_team(con: sqlite3.Connection, args: argparse.Namespace) -> str | None:
    """Exclude the user team from loose market offers when a formal AI plan is running."""
    controlled_user = cpu_controlled_user_team(con, args)
    if controlled_user:
        return controlled_user
    return cpu_excluded_user_team(con, args)


def active_game_id_value(con: sqlite3.Connection) -> str:
    return current_setting(con, "active_game_id") or "master"


def apply_cpu_controlled_user_free_agent_plan(
    con: sqlite3.Connection,
    period: sqlite3.Row | dict[str, Any],
    args: argparse.Namespace,
    *,
    max_offers: int,
) -> dict[str, int]:
    """Use the same needs-aware planner for the user team when FA is skipped.

    The generic CPU offer loop is intentionally broad. When a player fast-forwards
    through the whole market, the user team should instead get a persisted AI GM
    plan that targets its roster holes and then submits a small number of offers.
    """
    team_abbr = cpu_controlled_user_team(con, args)
    if not team_abbr or max_offers <= 0:
        return {"plans": 0, "offers": 0}
    league_year = int(row_value(period, "league_year", getattr(args, "league_year", 0)) or 0)
    plan_date = str(row_value(period, "current_date", current_game_date_value(con) or default_start_date(league_year)))
    team = team_by_abbr(con, team_abbr)
    game_id = active_game_id_value(con)
    existing = None
    if table_exists(con, "ai_gm_free_agent_plans"):
        existing = con.execute(
            """
            SELECT plan_id, apply_status
            FROM ai_gm_free_agent_plans
            WHERE game_id = ?
              AND team_id = ?
              AND league_year = ?
              AND plan_date = ?
              AND apply_status = 'applied'
            ORDER BY plan_id DESC
            LIMIT 1
            """,
            (game_id, int(team["team_id"]), league_year, plan_date),
        ).fetchone()
    if existing:
        return {"plans": 0, "offers": 0}

    try:
        import ai_gm_free_agent_planner as free_agent_planner

        free_agent_planner.ensure_schema(con)
        free_agent_planner.build_free_agent_plan(
            con,
            team_abbr=team_abbr,
            league_year=league_year,
            season=league_year,
            game_id=game_id,
            plan_date=plan_date,
            persist=True,
            refresh_market=True,
            market_limit=160,
            strict_need_plan=True,
        )
        plan_row = con.execute(
            """
            SELECT plan_id
            FROM ai_gm_free_agent_plans
            WHERE game_id = ?
              AND team_id = ?
              AND league_year = ?
              AND plan_date = ?
            ORDER BY plan_id DESC
            LIMIT 1
            """,
            (game_id, int(team["team_id"]), league_year, plan_date),
        ).fetchone()
        if not plan_row:
            return {"plans": 0, "offers": 0}
        result = free_agent_planner.apply_free_agent_plan(
            con,
            plan_id=int(plan_row["plan_id"]),
            allow_stale=True,
            max_offers=max_offers,
        )
        offers = len(result.get("operations") or []) if result.get("applied") else 0
        log_event(
            con,
            league_year=league_year,
            event_date=plan_date,
            event_hour=int(row_value(period, "current_hour", 12) or 12)
            if str(row_value(period, "current_stage", "")) == "day_one_hourly"
            else None,
            event_type="user_auto_fa_plan",
            team_id=int(team["team_id"]),
            message=(
                f"{team_abbr} used an AI GM free-agent plan while the user skipped the market; "
                f"{offers} offer(s) submitted."
            ),
        )
        return {"plans": 1, "offers": offers}
    except Exception as exc:
        log_event(
            con,
            league_year=league_year,
            event_date=plan_date,
            event_hour=None,
            event_type="user_auto_fa_plan_failed",
            team_id=int(team["team_id"]),
            message=f"{team_abbr} AI GM free-agent plan could not be applied: {exc}",
        )
        return {"plans": 0, "offers": 0}


def ensure_missing_specialists_for_fa(
    con: sqlite3.Connection,
    period: sqlite3.Row | dict[str, Any],
    *,
    user_team: str | None = None,
    max_total: int = 96,
) -> int:
    """Fill unavoidable K/P/LS holes before roster/cap cleanup gets too far along."""
    try:
        import roster_cutdown

        roster_cutdown.ensure_cutdown_schema(con)
    except Exception:
        return 0

    league_year = int(row_value(period, "league_year", default_league_year(con)) or default_league_year(con))
    event_date = str(row_value(period, "current_date", current_game_date_value(con) or default_start_date(league_year)))
    teams = con.execute(
        """
        SELECT team_id, abbreviation
        FROM teams
        WHERE (? IS NULL OR abbreviation <> ?)
        ORDER BY abbreviation
        """,
        (user_team, user_team),
    ).fetchall()
    signed = 0
    for team in teams:
        if signed >= max_total:
            break
        for position in ("K", "P", "LS"):
            if signed >= max_total:
                break
            try:
                did_sign = roster_cutdown.sign_missing_specialist(
                    con,
                    team=team,
                    position=position,
                    season=league_year,
                )
            except Exception:
                did_sign = False
            if not did_sign:
                continue
            signed += 1
            log_event(
                con,
                league_year=league_year,
                event_date=event_date,
                event_hour=None,
                event_type="cpu_specialist_signing",
                team_id=int(team["team_id"]),
                message=f"{team['abbreviation']} filled a missing {position} spot before roster finalization.",
            )
    if signed:
        sync_team_cap_space(con)
    return signed


def cpu_late_market(period: sqlite3.Row | dict[str, Any]) -> bool:
    return str(row_value(period, "current_stage", "") or "") == "daily" and int(row_value(period, "day_count", 1) or 1) >= 8


def cpu_top_remaining_free_agent(row: sqlite3.Row | dict[str, Any]) -> bool:
    group = normalized_group(row)
    score = true_overall(row)
    potential = int(row_value(row, "potential", score) or score)
    heat = int(row_value(row, "market_heat", 0) or 0)
    tier = normalized_tier(row)
    if tier == "Premium" and score >= 74:
        return True
    if group == "QB":
        return score >= 72 or potential >= 82
    return score >= 78 or potential >= 84 or heat >= 88


def cpu_elite_free_agent(row: sqlite3.Row | dict[str, Any]) -> bool:
    group = normalized_group(row)
    score = true_overall(row)
    potential = int(row_value(row, "potential", score) or score)
    if group == "QB":
        return score >= 82 or potential >= 88
    if group in {"WR", "TE", "OT", "EDGE", "CB", "IDL"}:
        return score >= 86 or (score >= 83 and potential >= 88)
    if group == "IOL":
        return score >= 88 or (score >= 84 and potential >= 89)
    return score >= 87 or (score >= 84 and potential >= 88)


def cpu_tag_age_fit(group: str, age: int, score: float, overall: int, potential: int) -> bool:
    if group == "QB":
        return age <= 34 and overall >= 84 and potential >= 86
    if group == "RB":
        return age <= 27 and score >= 88
    if group in {"WR", "TE", "EDGE", "CB", "S", "LB"}:
        return age <= 30 and (overall >= 84 or potential >= 88 or score >= 86)
    if group in {"OT", "IOL", "IDL"}:
        return age <= 31 and (overall >= 84 or potential >= 88 or score >= 86)
    return False


def cpu_should_block_cap_casualty_reunion(
    player: sqlite3.Row | dict[str, Any],
    team_abbr: str,
    *,
    late_market: bool,
) -> bool:
    """Prevent CPU teams from immediately undoing their own cap-casualty cuts."""
    source = str(row_value(player, "profile_source", "") or "").lower()
    previous_team = str(row_value(player, "previous_team", "") or "").upper()
    if source != "released_player_market" or not previous_team or previous_team != team_abbr.upper():
        return False
    if not late_market:
        return True
    score = true_overall(player)
    asking = int(row_value(player, "asking_aav", 0) or 0)
    minimum = int(row_value(player, "minimum_aav", 0) or 0)
    # Late-market reunions can happen, but only when the player has clearly
    # come back at a discount and is still a credible contributor.
    return not (score >= 74 and asking <= max(3_500_000, int(minimum * 1.12)))


def cpu_true_quality_aav_cap(row: sqlite3.Row | dict[str, Any]) -> int:
    """Cap CPU bids by actual OVR so role-score outliers do not get star money."""
    group = normalized_group(row)
    tier = normalized_tier(row)
    overall = true_overall(row)
    potential = int(row_value(row, "potential", overall) or overall)
    upside_bonus = 1.0 + clamp((potential - overall) * 0.018, 0.0, 0.14)
    tables = {
        "QB": [(64, 5_000_000), (68, 8_000_000), (72, 10_500_000), (74, 13_000_000), (77, 16_500_000), (79, 21_000_000), (80, 26_000_000), (82, 36_000_000), (99, 58_000_000)],
        "RB": [(64, 2_600_000), (68, 4_400_000), (72, 6_800_000), (76, 9_800_000), (80, 13_800_000), (99, 17_500_000)],
        "CB": [(60, 3_000_000), (64, 4_800_000), (68, 7_000_000), (72, 9_500_000), (76, 11_500_000), (78, 14_500_000), (80, 20_000_000), (99, 29_000_000)],
        "S": [(60, 3_000_000), (64, 4_800_000), (68, 7_500_000), (72, 10_500_000), (74, 12_000_000), (76, 14_500_000), (80, 19_000_000), (99, 24_000_000)],
        "EDGE": [(64, 4_800_000), (68, 7_800_000), (72, 10_800_000), (74, 12_000_000), (76, 15_000_000), (78, 17_500_000), (80, 22_000_000), (99, 34_000_000)],
        "IDL": [(64, 4_200_000), (68, 7_000_000), (72, 10_500_000), (76, 14_000_000), (78, 16_000_000), (80, 20_000_000), (99, 28_000_000)],
        "WR": [(64, 4_000_000), (68, 6_800_000), (72, 10_000_000), (76, 12_000_000), (78, 14_800_000), (80, 25_500_000), (84, 31_000_000), (88, 37_000_000), (99, 43_000_000)],
        "TE": [(64, 3_500_000), (68, 6_000_000), (72, 8_500_000), (76, 10_800_000), (78, 12_500_000), (80, 16_500_000), (86, 22_000_000), (99, 27_000_000)],
        "OT": [(64, 4_500_000), (68, 8_000_000), (72, 11_500_000), (74, 12_000_000), (76, 17_500_000), (80, 25_000_000), (99, 32_000_000)],
        "IOL": [(64, 3_500_000), (68, 6_000_000), (72, 9_000_000), (74, 11_000_000), (76, 13_000_000), (78, 15_500_000), (82, 20_000_000), (99, 25_000_000)],
        "LB": [(64, 3_500_000), (68, 6_000_000), (72, 9_000_000), (76, 13_000_000), (80, 18_000_000), (99, 22_000_000)],
        "ST": [(64, 1_500_000), (70, 2_800_000), (76, 4_000_000), (99, 5_500_000)],
    }
    table = tables.get(group, [(64, 3_000_000), (68, 5_500_000), (72, 9_000_000), (76, 14_000_000), (80, 20_000_000), (99, 26_000_000)])
    cap = table[-1][1]
    for threshold, value in table:
        if overall < threshold:
            cap = value
            break
    # Younger, high-upside players can command a little more, but never enough
    # to turn a 58 OVR corner into a $20M player.
    if overall < 72:
        upside_bonus = min(upside_bonus, 1.08)
    if group in {"TE", "OT"} and overall <= 73 and potential < 80:
        upside_bonus = min(upside_bonus, 1.03)
    if group == "QB":
        if overall < 78 and potential < 84:
            upside_bonus = 1.0
        elif overall <= 72 and potential < 82:
            upside_bonus = min(upside_bonus, 1.02)
    if group in {"WR", "OT", "EDGE", "IDL", "CB", "S", "IOL", "TE", "LB"} and potential <= overall + 3:
        if overall <= 72:
            cap = min(cap, 8_500_000 if group not in {"OT", "EDGE"} else 10_500_000)
        elif overall <= 74:
            cap = min(cap, 10_500_000 if group not in {"OT", "EDGE"} else 12_500_000)
        elif overall <= 76:
            cap = min(cap, 12_500_000 if group not in {"OT", "EDGE"} else 14_000_000)
        elif overall <= 78:
            cap = min(cap, 15_500_000 if group not in {"OT", "EDGE"} else 16_500_000)
    age = int(row_value(row, "age", 28) or 28)
    if group not in {"QB", "ST"}:
        old_age = POSITION_OLD_AGE.get(group, 31)
        years_old = max(0, age - old_age)
        if years_old >= 6:
            cap *= 0.68
        elif years_old >= 4:
            cap *= 0.78
        elif years_old >= 2:
            cap *= 0.88
        if overall < 68:
            cap = min(cap, 6_500_000)
        elif overall < 70:
            cap = min(cap, 8_500_000)
        elif overall < 72 and tier != "Premium":
            cap = min(cap, 10_500_000)
        if overall < 72 and potential <= overall + 2:
            cap *= 0.90
    return round_to(cap * upside_bonus, 50_000)


def is_post_draft_market_context(con: sqlite3.Connection, league_year: int) -> bool:
    phase = current_calendar_phase(con)
    phase_code = str(row_value(phase, "phase_code", "") or "") if phase else ""
    current_date = current_game_date_value(con)
    if phase_code in {"OFFSEASON_OPEN", "CAMP_REPORTING", "TRAINING_CAMP", "FINAL_CUTDOWN", "REGULAR_SEASON"}:
        return True
    if table_exists(con, "draft_room_state"):
        row = con.execute(
            """
            SELECT 1
            FROM draft_room_state
            WHERE draft_year = ?
              AND status = 'complete'
            LIMIT 1
            """,
            (league_year,),
        ).fetchone()
        if row:
            return True
    if current_date and current_date >= f"{league_year}-05-01":
        return True
    return False


def veteran_floor_aav(row: sqlite3.Row | dict[str, Any], *, post_draft: bool) -> int:
    if not post_draft:
        return 840_000
    group = normalized_group(row)
    tier = normalized_tier(row)
    age_value = row_value(row, "age")
    age = int(age_value) if age_value is not None else None
    score = market_tier_score(row)
    if age is None or age < POSITION_OLD_AGE.get(group, 31) or score < 64:
        return 840_000
    if tier == "Premium":
        base = 5_000_000
    elif tier == "Starter":
        base = 3_200_000
    elif tier == "Rotation":
        base = 1_600_000
    else:
        base = 1_000_000
    group_multiplier = {
        "QB": 1.60,
        "OT": 1.25,
        "EDGE": 1.20,
        "IDL": 1.10,
        "WR": 1.05,
        "CB": 1.05,
        "IOL": 1.00,
        "TE": 0.95,
        "S": 0.90,
        "LB": 0.88,
        "RB": 0.95,
        "ST": 0.55,
    }.get(group, 0.90)
    score_multiplier = 1.0 + clamp((score - 70) * 0.035, -0.15, 0.55)
    age_penalty = 1.0 - max(0, age - POSITION_OLD_AGE.get(group, 31)) * 0.035
    floor = base * group_multiplier * score_multiplier * clamp(age_penalty, 0.70, 1.0)
    return max(840_000, round_to(floor, 100_000))


def calendar_event_date(
    con: sqlite3.Connection,
    league_year: int,
    event_code: str,
    fallback_month: int,
    fallback_day: int,
) -> str:
    if table_exists(con, "league_calendar_events"):
        row = con.execute(
            """
            SELECT event_start_date
            FROM league_calendar_events
            WHERE league_year = ?
              AND event_code = ?
            LIMIT 1
            """,
            (league_year, event_code),
        ).fetchone()
        if row and row["event_start_date"]:
            return str(row["event_start_date"])
    return date_text(date(league_year, fallback_month, fallback_day))


def post_draft_holdout_date(con: sqlite3.Connection, league_year: int, player_id: int, rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.54:
        return calendar_event_date(con, league_year, "VETERAN_TRAINING_CAMP_REPORTING", 7, 22)
    if roll < 0.88:
        return calendar_event_date(con, league_year, "PRESEASON_WEEK_2", 8, 15)
    return calendar_event_date(con, league_year, "FINAL_ROSTER_CUTDOWN_53", 8, 26)


def post_draft_market_strategy(
    con: sqlite3.Connection,
    row: sqlite3.Row | dict[str, Any],
    league_year: int,
    *,
    post_draft: bool,
) -> tuple[str, str | None, str | None]:
    if not post_draft:
        return "normal", None, None
    player_id = int(row_value(row, "player_id", 0) or 0)
    rng = random.Random(f"fa-post-draft-strategy:{league_year}:{player_id}")
    tier = normalized_tier(row)
    group = normalized_group(row)
    score = market_tier_score(row)
    age = int(row_value(row, "age", 28) or 28)
    patience = int(row_value(row, "patience", 8) or 8)
    money_priority = int(row_value(row, "money_priority", row_value(row, "contract_priority", 10)) or 10)
    role_priority = int(row_value(row, "role_priority", 10) or 10)
    old_age = POSITION_OLD_AGE.get(group, 31)
    established = tier in {"Premium", "Starter"} and score >= 70
    credible_veteran = tier == "Rotation" and score >= 65 and age >= old_age - 1

    injury_wait_probability = 0.0
    if established and group in {"QB", "WR", "TE", "OT", "IOL", "EDGE", "IDL", "CB", "S"}:
        injury_wait_probability = 0.080
        injury_wait_probability += max(0, patience - 10) * 0.010
        injury_wait_probability += max(0, money_priority - 11) * 0.010
        if score >= 78:
            injury_wait_probability += 0.040
        if age >= old_age:
            injury_wait_probability += 0.055
        if group in {"QB", "OT", "EDGE", "CB", "WR"}:
            injury_wait_probability += 0.025
        if role_priority >= 14:
            injury_wait_probability += 0.020
        injury_wait_probability = clamp(injury_wait_probability, 0.04, 0.36)
    elif credible_veteran and group in {"QB", "WR", "TE", "OT", "IOL", "EDGE", "IDL", "CB", "S"}:
        injury_wait_probability = 0.045 + max(0, patience - 10) * 0.008
        if group in {"OT", "EDGE", "CB", "QB"}:
            injury_wait_probability += 0.025
        injury_wait_probability = clamp(injury_wait_probability, 0.025, 0.16)
    elif established and group == "RB" and score >= 76 and age <= 28:
        injury_wait_probability = 0.035

    if injury_wait_probability and rng.random() < injury_wait_probability:
        return (
            "injury_wait",
            post_draft_holdout_date(con, league_year, player_id, rng),
            "Holding firm for possible training-camp or injury leverage.",
        )

    firm_probability = 0.0
    if established:
        firm_probability = 0.28
        if tier == "Premium":
            firm_probability += 0.12
        if age >= old_age:
            firm_probability += 0.08
        if patience >= 12:
            firm_probability += 0.08
        if money_priority >= 13:
            firm_probability += 0.06
        firm_probability = clamp(firm_probability, 0.12, 0.54)
    elif credible_veteran:
        firm_probability = 0.20
    if firm_probability and rng.random() < firm_probability:
        return "firm_floor", None, "Keeping a veteran floor rather than chasing the first cheap deal."

    if tier in {"Depth", "Camp"} or score < 66 or patience <= 7:
        soften_probability = 0.52
        if group == "RB" and age >= 27:
            soften_probability += 0.18
        if age >= old_age + 2:
            soften_probability += 0.14
        if rng.random() < clamp(soften_probability, 0.34, 0.82):
            return "soften", None, "Post-draft depth market softened the asking price."

    return "normal", None, None


def apply_post_draft_strategy_prices(
    row: sqlite3.Row | dict[str, Any],
    asking: int,
    minimum: int,
    *,
    strategy: str,
    post_draft: bool,
) -> tuple[int, int]:
    if not post_draft:
        return asking, minimum
    strategy = strategy if strategy in POST_DRAFT_STRATEGIES else "normal"
    ceiling = market_price_ceiling(row, normalized_tier(row), post_draft=True)
    ceiling = min(ceiling, cpu_true_quality_aav_cap(row))
    veteran_floor = veteran_floor_aav(row, post_draft=True)
    if strategy == "injury_wait":
        floor = max(minimum, veteran_floor, round_to(asking * 0.70, 100_000))
        asking = min(ceiling, max(asking, round_to(asking * 1.10, 100_000), round_to(floor * 1.30, 100_000)))
        minimum = max(minimum, floor, round_to(asking * 0.68, 100_000))
    elif strategy == "firm_floor":
        floor = max(minimum, veteran_floor, round_to(asking * 0.58, 100_000))
        asking = min(ceiling, max(asking, round_to(asking * 1.03, 100_000), floor))
        minimum = max(minimum, floor, round_to(asking * 0.60, 100_000))
    elif strategy == "soften":
        floor = max(840_000, int(veteran_floor * 0.78))
        asking = max(floor, round_to(asking * 0.90, 100_000))
        minimum = max(floor, round_to(minimum * 0.90, 100_000), round_to(asking * 0.44, 100_000))
    return int(max(840_000, asking)), int(min(max(840_000, minimum), max(840_000, asking)))


def active_fa_injury_pressure_groups(con: sqlite3.Connection) -> set[tuple[int, str]]:
    if not table_exists(con, "active_player_injuries"):
        return set()
    rows = con.execute(
        """
        SELECT
            p.team_id,
            p.position,
            api.status,
            COALESCE(api.expected_games, 0) AS expected_games
        FROM active_player_injuries api
        JOIN players p ON p.player_id = api.player_id
        WHERE p.team_id IS NOT NULL
          AND api.resolved_at IS NULL
          AND api.status IN ('Doubtful', 'Out', 'IR', 'PUP', 'NFI')
          AND COALESCE(p.status, 'Active') NOT IN ('Free Agent', 'Retired')
        """
    ).fetchall()
    pressure: set[tuple[int, str]] = set()
    for row in rows:
        expected_games = int(row["expected_games"] or 0)
        if expected_games < 1 and str(row["status"] or "") not in {"IR", "PUP", "NFI"}:
            continue
        pressure.add((int(row["team_id"]), position_group_for(str(row["position"] or ""))))
    return pressure


def post_draft_strategy_allows_offer(
    con: sqlite3.Connection,
    player: sqlite3.Row | dict[str, Any],
    *,
    team_id: int | None = None,
    pressure_groups: set[tuple[int, str]] | None = None,
) -> bool:
    if str(row_value(player, "post_draft_strategy", "normal") or "normal") != "injury_wait":
        return True
    current_date = current_game_date_value(con)
    holdout_until = str(row_value(player, "holdout_until", "") or "")
    if current_date and holdout_until and current_date >= holdout_until:
        return True
    group = normalized_group(player)
    pressure_groups = pressure_groups if pressure_groups is not None else active_fa_injury_pressure_groups(con)
    if team_id is None:
        return any(pressure_group == group for _team_id, pressure_group in pressure_groups)
    return (team_id, group) in pressure_groups


def adjusted_market_prices(row: sqlite3.Row, league_year: int, *, post_draft: bool = False) -> tuple[int, int]:
    """Return current open-market asking/minimum AAV for an offseason FA.

    The seeded profile is the baseline personality/market expectation, but the
    actual offseason market should run hotter than a summer street-FA list.
    """
    player_id = int(row["player_id"])
    tier = normalized_tier(row)
    group = normalized_group(row)
    age = int(row["age"]) if row["age"] is not None else None
    score = market_tier_score(row)
    ceiling = market_price_ceiling(row, tier, post_draft=post_draft)
    ceiling = min(ceiling, cpu_true_quality_aav_cap(row))
    profile_ask = min(ceiling, max(915_000, int(row_value(row, "asking_aav", 1_500_000) or 1_500_000)))
    profile_min = min(int(ceiling * 0.72), max(840_000, int(row_value(row, "minimum_aav", 915_000) or 915_000)))

    tier_multiplier = MARKET_TIER_MULTIPLIERS.get(tier, 1.05)
    group_multiplier = MARKET_GROUP_MULTIPLIERS.get(group, 1.03)
    score_multiplier = 1.0 + clamp((score - 72) * 0.012, -0.08, 0.18)
    age_factor = market_age_factor(group, tier, age)
    jitter = clamp(random.Random(f"fa-market:{league_year}:{player_id}:{'post' if post_draft else 'open'}").gauss(1.0, 0.035), 0.92, 1.10)

    floor = MARKET_GROUP_TIER_FLOORS.get(
        (tier, group),
        MARKET_TIER_FLOORS.get(tier, 1_500_000),
    )
    floor = int(floor * age_factor)
    floor = min(floor, ceiling)
    adjusted_ask = profile_ask * tier_multiplier * group_multiplier * score_multiplier * age_factor * jitter
    if post_draft:
        discount = POST_DRAFT_ASK_DISCOUNT_BY_TIER.get(tier, 0.74)
        discount *= clamp(random.Random(f"fa-street:{league_year}:{player_id}").gauss(1.0, 0.045), 0.90, 1.08)
        floor = round_to(max(veteran_floor_aav(row, post_draft=True), int(floor * 0.82)), 100_000)
        floor = min(floor, ceiling)
        asking = min(ceiling, round_to(max(floor, adjusted_ask * discount), 100_000))
        minimum_ratio = POST_DRAFT_MINIMUM_RATIO_BY_TIER.get(tier, 0.50)
        minimum_floor = max(840_000, veteran_floor_aav(row, post_draft=True), int(profile_min * 0.72))
        minimum = max(minimum_floor, round_to(asking * minimum_ratio, 100_000))
    else:
        asking = min(ceiling, max(profile_ask, floor, round_to(adjusted_ask, 100_000)))
        minimum_ratio = MINIMUM_AAV_RATIO_BY_TIER.get(tier, 0.60)
        minimum = max(profile_min, round_to(asking * minimum_ratio, 100_000), 840_000)
    return int(asking), int(min(minimum, asking))


def ensure_market(con: sqlite3.Connection, league_year: int) -> int:
    expire_stale_market_years(con, league_year)
    free_agents = con.execute(
        """
        SELECT p.player_id
        FROM players p
        LEFT JOIN free_agent_profiles fap ON fap.player_id = p.player_id
        WHERE p.team_id IS NULL
          AND p.status = 'Free Agent'
          AND fap.player_id IS NULL
        ORDER BY p.player_id
        """
    ).fetchall()
    for row in free_agents:
        roster_actions.upsert_basic_free_agent_profile(
            con,
            int(row["player_id"]),
            ensure_ratings=False,
        )

    rows = con.execute(
        f"""
        SELECT
            p.player_id,
            p.position,
            p.age,
            p.overall,
            p.potential,
            COALESCE(fap.position_group, p.position) AS position_group,
            COALESCE(fap.market_tier, 'Depth') AS market_tier,
            COALESCE(fap.asking_aav, 1500000) AS asking_aav,
            COALESCE(fap.minimum_aav, 915000) AS minimum_aav,
            COALESCE(pref.contract_year_preference, fap.preferred_years, 1) AS preferred_years,
            COALESCE(fap.guarantee_pct, 0) AS guarantee_pct,
            COALESCE(pref.money_priority, fap.contract_priority, 10) AS money_priority,
            COALESCE(pref.role_priority, fap.role_priority, 10) AS role_priority,
            MAX(1, MIN(20, COALESCE(fap.patience, 8) + COALESCE(pref.market_patience_modifier, 0))) AS patience,
            COALESCE(score.role_score, p.overall, 60) AS market_score
        FROM players p
        LEFT JOIN free_agent_profiles fap ON fap.player_id = p.player_id
        LEFT JOIN player_free_agency_preferences pref
          ON pref.player_id = p.player_id
         AND pref.game_id = {active_game_id_expr()}
         AND pref.season = {active_game_start_year_expr()}
        LEFT JOIN (
            SELECT player_id, MAX(role_score) AS role_score
            FROM player_role_scores
            WHERE scheme_key = 'default'
              AND season = (SELECT CAST(setting_value AS INTEGER) FROM game_settings WHERE setting_key = 'current_season')
            GROUP BY player_id
        ) score ON score.player_id = p.player_id
        WHERE p.team_id IS NULL
          AND p.status = 'Free Agent'
        """
    ).fetchall()

    post_draft_market = is_post_draft_market_context(con, league_year)
    inserted = 0
    for row in rows:
        tier = normalized_tier(row)
        asking_aav, minimum_aav = adjusted_market_prices(row, league_year, post_draft=post_draft_market)
        strategy, holdout_until, holdout_reason = post_draft_market_strategy(
            con,
            row,
            league_year,
            post_draft=post_draft_market,
        )
        asking_aav, minimum_aav = apply_post_draft_strategy_prices(
            row,
            asking_aav,
            minimum_aav,
            strategy=strategy,
            post_draft=post_draft_market,
        )
        heat = market_heat_for(
            tier,
            asking_aav,
            market_tier_score(row),
            int(row["age"]) if row["age"] is not None else None,
        )
        cur = con.execute(
            """
            INSERT INTO free_agency_player_markets (
                league_year, player_id, position_group, market_tier,
                asking_aav, minimum_aav, preferred_years, guarantee_pct,
                market_heat, patience, post_draft_strategy, holdout_until,
                holdout_reason, status, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', datetime('now'))
            ON CONFLICT(league_year, player_id) DO UPDATE SET
                position_group = excluded.position_group,
                market_tier = excluded.market_tier,
                asking_aav = excluded.asking_aav,
                minimum_aav = excluded.minimum_aav,
                preferred_years = excluded.preferred_years,
                guarantee_pct = excluded.guarantee_pct,
                market_heat = excluded.market_heat,
                patience = excluded.patience,
                post_draft_strategy = excluded.post_draft_strategy,
                holdout_until = excluded.holdout_until,
                holdout_reason = excluded.holdout_reason,
                status = CASE
                    WHEN free_agency_player_markets.status = 'signed' THEN free_agency_player_markets.status
                    ELSE 'available'
                END,
                updated_at = datetime('now')
            """,
            (
                league_year,
                row["player_id"],
                row["position_group"],
                tier,
                asking_aav,
                minimum_aav,
                int(row["preferred_years"]),
                int(row["guarantee_pct"]),
                heat,
                int(row["patience"]),
                strategy,
                holdout_until,
                holdout_reason,
            ),
        )
        if cur.rowcount:
            inserted += 1
    return inserted


def expire_stale_market_years(con: sqlite3.Connection, league_year: int) -> None:
    if not table_exists(con, "free_agency_player_markets"):
        return
    con.execute(
        """
        UPDATE free_agency_player_markets
        SET status = 'expired',
            decision_notes = COALESCE(decision_notes, 'Expired when a newer free-agency year opened.'),
            updated_at = datetime('now')
        WHERE league_year < ?
          AND status = 'available'
        """,
        (league_year,),
    )
    if table_exists(con, "free_agency_offers"):
        con.execute(
            """
            UPDATE free_agency_offers
            SET status = 'expired',
                notes = COALESCE(notes || ' | ', '') || 'Expired when a newer free-agency year opened.',
                updated_at = datetime('now')
            WHERE league_year < ?
              AND status = 'pending'
            """,
            (league_year,),
        )
    if table_exists(con, "free_agency_periods"):
        con.execute(
            """
            UPDATE free_agency_periods
            SET status = 'completed',
                completed_at = COALESCE(completed_at, datetime('now')),
                updated_at = datetime('now')
            WHERE league_year < ?
              AND status = 'active'
            """,
            (league_year,),
        )


def current_period(con: sqlite3.Connection, league_year: int) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM free_agency_periods WHERE league_year = ?",
        (league_year,),
    ).fetchone()


def sync_period_to_game_date(con: sqlite3.Connection, league_year: int) -> bool:
    """Keep an active FA period aligned with the playable save date.

    Street free agency stays open through the post-draft/regular-season
    transaction windows. Multiple UI and sim paths touch the FA period, so this
    guard makes the period date monotonic: it may catch up to the active game
    date, but it cannot be pulled backward by a stale setting or host date.
    """
    current_game_date = current_game_date_value(con)
    if not current_game_date:
        return False
    period = current_period(con, league_year)
    if not period or period["status"] != "active":
        return False
    try:
        period_date = parse_date(str(period["current_date"]))
        game_date = parse_date(str(current_game_date))
    except ValueError:
        return False
    if game_date <= period_date:
        return False
    con.execute(
        """
        UPDATE free_agency_periods
        SET current_stage = 'daily',
            "current_date" = ?,
            current_hour = 12,
            updated_at = datetime('now')
        WHERE league_year = ?
          AND status = 'active'
        """,
        (date_text(game_date), league_year),
    )
    return True


def league_year_start_date(league_year: int) -> date:
    return parse_date(default_start_date(league_year))


def close_elapsed_period_if_needed(con: sqlite3.Connection, league_year: int) -> bool:
    """Close an old FA period once the next league year's market has opened."""
    current_date = current_game_date_value(con)
    if not current_date:
        return False
    try:
        parsed = parse_date(current_date)
    except ValueError:
        return False
    if parsed < league_year_start_date(league_year + 1):
        return False
    period = current_period(con, league_year)
    if not period or period["status"] != "active":
        return False
    con.execute(
        """
        UPDATE free_agency_periods
        SET "current_date" = ?,
            current_hour = 12,
            current_stage = 'daily',
            updated_at = datetime('now')
        WHERE league_year = ?
        """,
        (current_date, league_year),
    )
    closing_period = current_period(con, league_year)
    if closing_period:
        resolve_pending_offers(con, closing_period, write_cap_snapshot=False)
    con.execute(
        """
        UPDATE free_agency_periods
        SET status = 'completed',
            current_stage = 'closed',
            "current_date" = ?,
            current_hour = 12,
            completed_at = COALESCE(completed_at, datetime('now')),
            updated_at = datetime('now'),
            notes = COALESCE(notes || ' ', '') || ?
        WHERE league_year = ?
        """,
        (
            current_date,
            "Closed automatically because the next league year has begun.",
            league_year,
        ),
    )
    con.execute(
        """
        UPDATE free_agency_offers
        SET status = 'expired',
            decided_date = ?,
            decided_hour = NULL,
            notes = COALESCE(notes || ' | ', '') || 'Expired when FA period closed.',
            updated_at = datetime('now')
        WHERE league_year = ?
          AND status = 'pending'
        """,
        (current_date, league_year),
    )
    log_event(
        con,
        league_year=league_year,
        event_date=current_date,
        event_hour=None,
        event_type="period_auto_closed",
        message="Free agency period closed automatically because the next league year has begun.",
    )
    return True


def reconcile_market_state(con: sqlite3.Connection, league_year: int, *, stale_offer_days: int = 14) -> dict[str, int]:
    current_date = current_game_date_value(con)
    result = {
        "rostered_markets": 0,
        "released_signed_markets": 0,
        "stale_offers": 0,
        "closed_period": 0,
        "resolved_before_stale": 0,
    }
    if close_elapsed_period_if_needed(con, league_year):
        result["closed_period"] = 1
    sync_period_to_game_date(con, league_year)

    cur = con.execute(
        """
        UPDATE free_agency_player_markets
        SET status = 'rostered',
            signed_team_id = COALESCE(signed_team_id, (SELECT team_id FROM players WHERE players.player_id = free_agency_player_markets.player_id)),
            decision_notes = COALESCE(decision_notes, 'Removed from market because player is already rostered.'),
            updated_at = datetime('now')
        WHERE league_year = ?
          AND status = 'available'
          AND EXISTS (
              SELECT 1
              FROM players p
              WHERE p.player_id = free_agency_player_markets.player_id
                AND p.team_id IS NOT NULL
                AND COALESCE(p.status, '') <> 'Retired'
          )
        """,
        (league_year,),
    )
    result["rostered_markets"] = int(cur.rowcount or 0)

    cur = con.execute(
        """
        UPDATE free_agency_player_markets
        SET status = 'available',
            signed_team_id = NULL,
            signed_offer_id = NULL,
            last_offer_at = NULL,
            decision_notes = TRIM(COALESCE(decision_notes || ' ', '') || 'Returned to market after release.'),
            updated_at = datetime('now')
        WHERE league_year = ?
          AND status = 'signed'
          AND EXISTS (
              SELECT 1
              FROM players p
              WHERE p.player_id = free_agency_player_markets.player_id
                AND p.team_id IS NULL
                AND COALESCE(p.status, '') = 'Free Agent'
          )
        """,
        (league_year,),
    )
    result["released_signed_markets"] = int(cur.rowcount or 0)

    period = current_period(con, league_year)
    if period and period["status"] == "active":
        current_date = str(period["current_date"])
    if current_date:
        result["stale_offers"] = expire_stale_pending_offers(
            con,
            league_year=league_year,
            current_date=current_date,
            stale_offer_days=stale_offer_days,
            note="Expired after sitting pending too long.",
        )
        if period and period["status"] == "active":
            result["resolved_before_stale"] = resolve_pending_offers(
                con,
                period,
                write_cap_snapshot=False,
            )
    return result


def current_calendar_phase(con: sqlite3.Connection) -> sqlite3.Row | None:
    current_date = current_game_date_value(con)
    if not current_date or not table_exists(con, "league_phase_windows"):
        return None
    return con.execute(
        """
        SELECT *
        FROM league_phase_windows
        WHERE ? BETWEEN start_date AND end_date
        ORDER BY league_year DESC, sort_order DESC
        LIMIT 1
        """,
        (current_date,),
    ).fetchone()


def can_auto_open_street_market(con: sqlite3.Connection, league_year: int) -> bool:
    if league_year != default_league_year(con):
        return False
    phase = current_calendar_phase(con)
    if not phase:
        return False
    if int(row_value(phase, "transactions_open", 0) or 0) != 1:
        return False
    return str(row_value(phase, "phase_code", "") or "") != "POST_SUPER_BOWL_OFFSEASON"


def auto_open_street_market(con: sqlite3.Connection, league_year: int) -> sqlite3.Row | None:
    if not can_auto_open_street_market(con, league_year):
        return None
    current_date = current_game_date_value(con) or date_text(date(league_year, 6, 1))
    ensure_market(con, league_year)
    con.execute(
        """
        INSERT INTO free_agency_periods (
            league_year, status, current_stage, "current_date", current_hour,
            day_count, first_day_start_hour, first_day_end_hour,
            started_at, updated_at, notes
        )
        VALUES (?, 'active', 'daily', ?, 12, 1, 12, 20, datetime('now'), datetime('now'), ?)
        ON CONFLICT(league_year) DO UPDATE SET
            status = 'active',
            current_stage = CASE
                WHEN free_agency_periods.current_stage IS NULL THEN 'daily'
                ELSE free_agency_periods.current_stage
            END,
            "current_date" = CASE
                WHEN date(excluded."current_date") > date(free_agency_periods."current_date")
                    THEN excluded."current_date"
                ELSE free_agency_periods."current_date"
            END,
            completed_at = NULL,
            updated_at = datetime('now'),
            notes = COALESCE(free_agency_periods.notes, excluded.notes)
        """,
        (
            league_year,
            current_date,
            "Auto-opened street free agency during the open transaction calendar window.",
            current_date,
        ),
    )
    log_event(
        con,
        league_year=league_year,
        event_date=current_date,
        event_hour=None,
        event_type="period_auto_opened",
        message="Street free agency auto-opened from the current league calendar window.",
    )
    return current_period(con, league_year)


def active_period(con: sqlite3.Connection, league_year: int) -> sqlite3.Row:
    reconcile_market_state(con, league_year)
    sync_period_to_game_date(con, league_year)
    row = current_period(con, league_year)
    if not row or row["status"] != "active":
        row = auto_open_street_market(con, league_year)
    if not row or row["status"] != "active":
        raise ValueError(f"Free agency is not active for {league_year}. Run start first.")
    current_game_date = current_game_date_value(con)
    if current_game_date and can_auto_open_street_market(con, league_year):
        if sync_period_to_game_date(con, league_year):
            row = current_period(con, league_year)
    return row


def upsert_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    if key == "current_game_date":
        existing = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = ?",
            (key,),
        ).fetchone()
        if existing and str(existing["setting_value"] or "") > str(value):
            value = str(existing["setting_value"])
    con.execute(
        """
        INSERT INTO game_settings (setting_key, setting_value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(setting_key) DO UPDATE SET
            setting_value = excluded.setting_value,
            updated_at = datetime('now')
        """,
        (key, value),
    )


def sync_active_game_to_date(con: sqlite3.Connection, target_date: str) -> None:
    if not table_exists(con, "game_saves"):
        return
    row = con.execute(
        """
        SELECT *
        FROM game_saves
        WHERE status = 'active'
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return
    if str(row["current_date"]) >= target_date:
        phase = league_calendar.phase_for_date(con, str(row["current_date"]))
        if phase:
            upsert_setting(con, "current_game_date", str(row["current_date"]))
            upsert_setting(con, "current_league_year", str(int(phase["league_year"])))
            upsert_setting(con, "current_season", str(int(phase["league_year"])))
            upsert_setting(con, "current_calendar_phase", str(phase["phase_code"]))
        return
    phase = league_calendar.phase_for_date(con, target_date)
    if not phase:
        raise ValueError(f"No league calendar phase found for {target_date}.")
    con.execute(
        """
        UPDATE game_saves
        SET "current_date" = ?,
            current_league_year = ?,
            current_phase_code = ?,
            updated_at = datetime('now')
        WHERE game_id = ?
        """,
        (target_date, int(phase["league_year"]), phase["phase_code"], row["game_id"]),
    )
    upsert_setting(con, "current_game_date", target_date)
    upsert_setting(con, "current_league_year", str(int(phase["league_year"])))
    upsert_setting(con, "current_season", str(int(phase["league_year"])))
    upsert_setting(con, "current_calendar_phase", phase["phase_code"])
    if table_exists(con, "game_flow_log"):
        con.execute(
            """
            INSERT INTO game_flow_log (
                game_id, game_date, log_type, event_code, title, details
            )
            VALUES (?, ?, 'DATE_ADVANCE', 'FREE_AGENCY_START', ?, ?)
            """,
            (
                row["game_id"],
                target_date,
                "Advanced to free agency",
                f"Active save advanced to {target_date} for the {target_date[:4]} NFL league year.",
            ),
        )


def cpu_re_sign_probability(player: dict[str, Any] | sqlite3.Row) -> float:
    priority = str(player["priority"] if "priority" in player.keys() else "").lower()
    tier = str(player["market_tier"] if "market_tier" in player.keys() else "").lower()
    group = str(player["position_group"] if "position_group" in player.keys() else "").upper()
    score = float(player["market_score"] or 60)
    overall = float(row_value(player, "overall", score) or score)
    potential = float(row_value(player, "potential", overall) or overall)
    age = int(player["age"] or 28) if "age" in player.keys() else 28
    if group == "WR" and overall >= 83 and potential >= 86 and age <= 29:
        base = 0.98
    elif group == "TE" and overall >= 88 and potential >= 90 and age <= 29:
        base = 0.96
    elif score >= 90:
        base = 0.96
    elif score >= 86 and group in {"QB", "WR", "OT", "IOL", "EDGE", "IDL", "CB", "TE"}:
        base = 0.91
    elif priority == "priority" or tier in {"franchise", "core", "premium"} or score >= 82:
        base = 0.82
    elif priority == "negotiable" or tier in {"starter"} or score >= 74:
        base = 0.42
    elif group == "ST" and score >= 68:
        base = 0.36
    elif score >= 68:
        base = 0.18
    else:
        base = 0.06
    if age >= 32 and group not in {"QB", "OT", "IOL", "ST"}:
        base -= 0.16
    if group == "RB" and age >= 28:
        base -= 0.18
    return max(0.03, min(0.97, base))


def cpu_must_protect_expiring_player(player: dict[str, Any] | sqlite3.Row) -> bool:
    group = str(row_value(player, "position_group", "") or "").upper()
    score = float(row_value(player, "market_score", row_value(player, "overall", 60)) or 60)
    overall = int(row_value(player, "overall", score) or score)
    potential = int(row_value(player, "potential", overall) or overall)
    age = int(row_value(player, "age", 28) or 28)
    if group == "QB":
        return overall >= 81 and potential >= 84 and age <= 33
    if group == "WR":
        return age <= 29 and (overall >= 84 or (overall >= 82 and potential >= 88) or score >= 86)
    if group in {"OT", "EDGE", "CB", "IDL"}:
        return age <= 30 and (overall >= 84 or (overall >= 81 and potential >= 87) or score >= 86)
    if group == "IOL":
        return age <= 30 and (overall >= 86 or (overall >= 82 and potential >= 88))
    if group == "TE":
        return age <= 30 and (overall >= 86 or (overall >= 82 and potential >= 89))
    if group in {"S", "LB"}:
        return age <= 30 and (overall >= 85 or (overall >= 82 and potential >= 88))
    return score >= 90


def cpu_offer_year_cap(player: sqlite3.Row | dict[str, Any], *, own_retention: bool = False) -> int:
    group = normalized_group(player)
    score = true_overall(player)
    potential = int(row_value(player, "potential", score) or score)
    age = int(row_value(player, "age", 28) or 28)
    if group == "QB":
        if age >= 38:
            return 1
        if age >= 36:
            return 2
        if score <= 76 and potential < 82:
            return 2 if own_retention else 1
        if score < 78 and potential < 84:
            return 2
        if score < 80 and potential < 84:
            return 3
        return 5
    old_age = POSITION_OLD_AGE.get(group, 31)
    if age >= old_age + 2 and score < 84:
        return 1
    if age >= old_age and score < 82:
        return 2
    if group == "RB":
        return 2 if score < 78 or age >= 28 else 3
    return 5


def preferred_years_for_offer(player: sqlite3.Row, rng: random.Random, *, max_years: int = 5) -> int:
    base = int(row_value(player, "contract_year_preference", row_value(player, "preferred_years", 1)) or 1)
    security = int(row_value(player, "security_priority", 10) or 10)
    role = int(row_value(player, "role_priority", 10) or 10)
    jitter_choices = [-1, 0, 0, 1]
    if security >= 16:
        jitter_choices.append(1)
    if role >= 16:
        jitter_choices.append(-1)
    return max(1, min(max_years, base + rng.choice(jitter_choices)))


def preference_adjusted_aav(player: sqlite3.Row, aav: int, rng: random.Random) -> int:
    money_priority = int(row_value(player, "money_priority", 10) or 10)
    contender_priority = int(row_value(player, "contender_priority", 10) or 10)
    role_priority = int(row_value(player, "role_priority", 10) or 10)
    modifier = 1.0 + max(-0.07, min(0.18, (money_priority - 10) * 0.012))
    if contender_priority >= 16:
        modifier -= 0.025
    if role_priority >= 16:
        modifier -= 0.015
    modifier += clamp(rng.gauss(0.0, 0.018), -0.035, 0.045)
    return round_to(max(int(row_value(player, "minimum_aav", 915000) or 915000), int(aav * modifier)))


def cpu_aav_bounds(
    player: sqlite3.Row,
    *,
    best_aav: int = 0,
    response_offer: bool = False,
    market_pressure: float = 0.0,
) -> tuple[int, int]:
    group = normalized_group(player)
    tier = str(row_value(player, "market_tier", "Depth") or "Depth").title()
    if tier in {"Core", "Franchise"}:
        tier = "Premium"
    asking = max(1, int(row_value(player, "asking_aav", row_value(player, "minimum_aav", 0)) or 0))
    minimum = max(1, int(row_value(player, "minimum_aav", 0) or 0))
    score = true_overall(player)
    potential = int(row_value(player, "potential", score) or score)
    if tier == "Premium":
        low_pct, high_pct = 1.00, 1.30
    elif tier == "Starter":
        low_pct, high_pct = 0.98, 1.24
    elif tier == "Rotation":
        low_pct, high_pct = 0.92, 1.15
    elif tier == "Depth":
        low_pct, high_pct = 0.84, 1.08
    else:
        low_pct, high_pct = 0.70, 1.02
    if response_offer:
        low_pct += 0.02
        high_pct += 0.08
    if market_pressure > 0:
        pressure = clamp(market_pressure, 0.0, 1.0)
        if tier == "Premium" or cpu_elite_free_agent(player):
            low_pct += 0.03 * pressure
            high_pct += 0.16 * pressure
        elif tier == "Starter":
            low_pct += 0.02 * pressure
            high_pct += 0.08 * pressure
    low = max(minimum, int(asking * low_pct), int(best_aav * 0.99))
    high = max(
        low,
        int(asking * high_pct),
        int(best_aav * (1.16 if response_offer or market_pressure > 0 else 1.08)),
    )
    cap = max(minimum, cpu_true_quality_aav_cap(player))
    if group == "WR":
        if score >= 88:
            star_floor = 29_000_000
        elif score >= 86 and potential >= 88:
            star_floor = 27_000_000
        elif score >= 84 and potential >= 87:
            star_floor = 25_000_000
        elif score >= 82 and potential >= 86:
            star_floor = 22_000_000
        else:
            star_floor = 0
        if star_floor:
            low = max(low, min(star_floor, cap))
            high = max(high, min(int(star_floor * 1.24), cap))
    elif group == "TE" and score >= 88:
        star_floor = 20_000_000 if potential >= 90 else 18_000_000
        low = max(low, min(star_floor, cap))
        high = max(high, min(int(star_floor * 1.15), cap))
    if low > cap:
        low = cap
    high = min(high, max(low, cap))
    return low, high


def no_interest_decay_rate(row: sqlite3.Row, *, opening_phase_ended: bool, days: int) -> float:
    tier = normalized_tier(row)
    group = normalized_group(row)
    age = int(row["age"]) if row["age"] is not None else None

    rate = 0.055 if opening_phase_ended else 0.024
    rate += max(0, days - 1) * 0.010
    stage = str(row_value(row, "period_stage", "") or "")
    if stage == "daily":
        rate += 0.012
    if tier == "Premium":
        rate *= 0.80
    elif tier == "Starter":
        rate *= 0.95
    elif tier in {"Depth", "Camp"}:
        rate *= 1.15

    if age is not None:
        if group == "QB":
            if age >= 38:
                rate *= 1.25
        elif group == "RB":
            if age >= 29:
                rate *= 1.45
        elif group == "TE":
            if age >= 29:
                rate *= 1.30
        elif group in {"WR", "CB", "S", "LB"}:
            if age >= 30:
                rate *= 1.30
        elif group in {"EDGE", "IDL", "OT", "IOL"}:
            if age >= 31:
                rate *= 1.18
    patience = int(row_value(row, "patience", 8) or 8)
    rate *= 1.0 + clamp((10 - patience) * 0.025, -0.18, 0.18)
    player_id = int(row_value(row, "player_id", 0) or 0)
    league_year = int(row_value(row, "league_year", 0) or 0)
    jitter = random.Random(f"fa-decay:{league_year}:{player_id}:{days}:{stage}").uniform(0.82, 1.22)
    return clamp(rate * jitter, 0.010, 0.170)


def retirement_probability_for_no_interest(row: sqlite3.Row, *, days: int) -> float:
    tier = normalized_tier(row)
    group = normalized_group(row)
    age_value = row_value(row, "age")
    if age_value is None:
        return 0.0
    age = int(age_value)
    old_age = POSITION_OLD_AGE.get(group, 31)
    if age < old_age + 1:
        return 0.0
    score = int(row_value(row, "market_score", 60) or 60)
    total_offers = int(row_value(row, "total_offers", 0) or 0)
    if total_offers > 0:
        return 0.0
    weeks_on_market = max(0.0, (int(row_value(row, "day_count", 1) or 1) + max(0, days - 1)) / 7.0)
    base = 0.010 + max(0, age - old_age) * 0.010 + weeks_on_market * 0.010
    if tier == "Premium":
        base *= 0.45
    elif tier == "Starter":
        base *= 0.75
    elif tier in {"Depth", "Camp"}:
        base *= 1.45
    if group == "RB":
        base *= 1.45
    elif group in {"QB", "OT", "K", "P", "ST"}:
        base *= 0.60
    if score >= 76:
        base *= 0.55
    elif score < 63:
        base *= 1.35
    patience = int(row_value(row, "patience", 8) or 8)
    base *= 1.0 + clamp((8 - patience) * 0.045, -0.20, 0.30)
    return clamp(base, 0.0, 0.18)


def retire_no_interest_free_agents(
    con: sqlite3.Connection,
    period: sqlite3.Row,
    rows: list[sqlite3.Row],
    *,
    days: int,
) -> int:
    retired = 0
    event_date, event_hour = event_time(period)
    pressure_groups = active_fa_injury_pressure_groups(con)
    for row in rows:
        if str(row_value(row, "post_draft_strategy", "normal") or "normal") == "injury_wait":
            if not post_draft_strategy_allows_offer(con, row, pressure_groups=pressure_groups):
                continue
        probability = retirement_probability_for_no_interest(row, days=max(1, days))
        if probability <= 0:
            continue
        player_id = int(row["player_id"])
        rng = random.Random(f"fa-retire:{period['league_year']}:{player_id}:{period['current_date']}:{row_value(row, 'day_count', 1)}")
        if rng.random() >= probability:
            continue
        con.execute(
            """
            UPDATE free_agency_player_markets
            SET status = 'retired',
                decision_notes = COALESCE(decision_notes || ' | ', '') || ?,
                updated_at = datetime('now')
            WHERE league_year = ?
              AND player_id = ?
              AND status = 'available'
            """,
            (
                f"Retired after a quiet post-draft market; retirement probability {probability:.0%}.",
                int(period["league_year"]),
                player_id,
            ),
        )
        con.execute(
            "UPDATE players SET status = 'Retired' WHERE player_id = ? AND team_id IS NULL",
            (player_id,),
        )
        log_event(
            con,
            league_year=int(period["league_year"]),
            event_date=event_date,
            event_hour=event_hour,
            event_type="player_retired_market",
            player_id=player_id,
            message=f"{row['player_name']} retired after drawing little post-draft interest.",
        )
        retired += 1
    return retired


def apply_no_interest_demand_decay(
    con: sqlite3.Connection,
    period: sqlite3.Row,
    *,
    hours: int = 0,
    days: int = 0,
) -> tuple[int, int]:
    opening_phase_ended = False
    if period["current_stage"] == "day_one_hourly":
        if hours and int(period["current_hour"]) + hours > int(period["first_day_end_hour"]):
            opening_phase_ended = True
        elif days:
            opening_phase_ended = True
    if not opening_phase_ended and days <= 0:
        return 0, 0

    rows = con.execute(
        """
        SELECT
            m.league_year,
            m.player_id,
            m.position_group,
            m.market_tier,
            m.asking_aav,
            m.minimum_aav,
            m.market_heat,
            m.patience,
            COALESCE(m.post_draft_strategy, 'normal') AS post_draft_strategy,
            m.holdout_until,
            m.holdout_reason,
            ? AS period_stage,
            ? AS day_count,
            p.position,
            p.age,
            p.first_name || ' ' || p.last_name AS player_name,
            COALESCE(score.role_score, p.overall, 60) AS market_score,
            COALESCE(profile.minimum_aav, 840000) AS profile_minimum_aav,
            COALESCE(offers.total_offers, 0) AS total_offers,
            COALESCE(offers.pending_offers, 0) AS pending_offers
        FROM free_agency_player_markets m
        JOIN players p ON p.player_id = m.player_id
        LEFT JOIN free_agent_profiles profile ON profile.player_id = m.player_id
        LEFT JOIN (
            SELECT player_id, MAX(role_score) AS role_score
            FROM player_role_scores
            WHERE scheme_key = 'default'
              AND season = (SELECT CAST(setting_value AS INTEGER) FROM game_settings WHERE setting_key = 'current_season')
            GROUP BY player_id
        ) score ON score.player_id = m.player_id
        LEFT JOIN (
            SELECT
                league_year,
                player_id,
                COUNT(*) AS total_offers,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_offers
            FROM free_agency_offers
            GROUP BY league_year, player_id
        ) offers ON offers.league_year = m.league_year
                AND offers.player_id = m.player_id
        WHERE m.league_year = ?
          AND m.status = 'available'
          AND COALESCE(offers.total_offers, 0) = 0
        """,
        (period["current_stage"], int(period["day_count"] or 1), period["league_year"]),
    ).fetchall()

    changed = 0
    post_draft_market = is_post_draft_market_context(con, int(period["league_year"]))
    pressure_groups = active_fa_injury_pressure_groups(con) if post_draft_market else set()
    for row in rows:
        old_ask = int(row["asking_aav"] or 0)
        old_min = int(row["minimum_aav"] or 0)
        if old_ask <= 0:
            continue
        strategy = str(row_value(row, "post_draft_strategy", "normal") or "normal")
        if strategy == "injury_wait" and not post_draft_strategy_allows_offer(
            con,
            row,
            pressure_groups=pressure_groups,
        ):
            continue
        rate = no_interest_decay_rate(row, opening_phase_ended=opening_phase_ended, days=max(1, days))
        if post_draft_market:
            if strategy == "injury_wait":
                rate *= 0.28
            elif strategy == "firm_floor":
                rate *= 0.58
            elif strategy == "soften":
                rate *= 1.32
        profile_minimum = max(840_000, int(row["profile_minimum_aav"] or 840_000))
        floor = max(veteran_floor_aav(row, post_draft=post_draft_market), int(profile_minimum * (0.72 if post_draft_market else 1.0)))
        if post_draft_market:
            if strategy == "injury_wait":
                floor = max(floor, round_to(old_ask * 0.64, 100_000))
            elif strategy == "firm_floor":
                floor = max(floor, round_to(old_ask * 0.54, 100_000))
            elif strategy == "soften":
                floor = max(840_000, int(floor * 0.82))
        new_ask = max(floor, round_to(old_ask * (1.0 - rate), 100_000))
        new_minimum = max(floor, round_to(old_min * (1.0 - rate * 0.90), 100_000))
        new_minimum = min(new_minimum, new_ask)
        if new_ask >= old_ask and new_minimum >= old_min:
            continue
        heat = market_heat_for(
            str(row["market_tier"]),
            new_ask,
            int(row["market_score"] or 60),
            int(row["age"]) if row["age"] is not None else None,
        )
        con.execute(
            """
            UPDATE free_agency_player_markets
            SET asking_aav = ?,
                minimum_aav = ?,
                market_heat = ?,
                decision_notes = COALESCE(decision_notes || ' | ', '') || ?,
                updated_at = datetime('now')
            WHERE league_year = ?
              AND player_id = ?
            """,
            (
                new_ask,
                new_minimum,
                heat,
                f"No-offer demand adjustment: ask {money(old_ask)} to {money(new_ask)}.",
                int(row["league_year"]),
                int(row["player_id"]),
            ),
        )
        changed += 1
    retired = retire_no_interest_free_agents(con, period, rows, days=max(1, days))
    return changed, retired


def guarantee_for_preference(player: sqlite3.Row, base_guarantee: int, rng: random.Random) -> int:
    security_priority = int(row_value(player, "security_priority", 10) or 10)
    money_priority = int(row_value(player, "money_priority", 10) or 10)
    return max(
        0,
        min(
            85,
            base_guarantee
            + rng.randint(-4, 10)
            + max(0, security_priority - 10)
            + max(0, money_priority - 15),
        ),
    )


def cpu_signing_bonus_for_offer(
    player: sqlite3.Row | dict[str, Any],
    *,
    aav: int,
    years: int,
    guarantee_pct: int,
    rng: random.Random,
    response_offer: bool = False,
    own_retention: bool = False,
) -> int:
    """Create enough upfront money that newly signed deals are not easy cap-casualty cuts."""
    if years <= 0 or aav <= 0:
        return 0
    tier = normalized_tier(player)
    group = normalized_group(player)
    score = true_overall(player)
    potential = int(row_value(player, "potential", score) or score)
    total = int(aav) * int(years)
    if years == 1:
        low, high = 0.10, 0.22
    elif tier == "Premium" or cpu_elite_free_agent(player):
        low, high = 0.30, 0.44
    elif tier == "Starter":
        low, high = 0.24, 0.36
    elif tier == "Rotation":
        low, high = 0.18, 0.30
    else:
        low, high = 0.12, 0.24
    if group in {"QB", "WR", "OT", "EDGE", "CB"} and (score >= 78 or potential >= 84):
        low += 0.03
        high += 0.04
    if group == "RB" and score < 82:
        high -= 0.04
    if response_offer:
        low += 0.02
        high += 0.03
    if own_retention:
        low -= 0.03
        high -= 0.02
    guarantee_bump = clamp((int(guarantee_pct or 0) - 35) / 100.0, -0.04, 0.10)
    low = clamp(low + guarantee_bump * 0.45, 0.06, 0.50)
    high = clamp(max(low + 0.03, high + guarantee_bump * 0.60), low, 0.55)
    return round_to(total * rng.uniform(low, high), 50_000)


def normalized_contract_structure(value: Any) -> str:
    label = str(value or "balanced").strip().lower().replace("_", "-")
    if label in {"backload", "backloaded", "back-loaded"}:
        return "backloaded"
    if label in {"frontload", "frontloaded", "front-loaded"}:
        return "frontloaded"
    if label in {"bonus-heavy", "bonus", "low-year-one", "low-year-1"}:
        return "bonus-heavy"
    return "balanced"


def first_year_cap_estimate(*, aav: int, years: int, signing_bonus: int, structure: str) -> int:
    years = max(1, int(years or 1))
    total_value = int(aav) * years
    proration = int(int(signing_bonus or 0) / max(1, min(5, years)))
    base_pool = max(0, total_value - int(signing_bonus or 0))
    label = normalized_contract_structure(structure)
    if years <= 1 or label == "balanced":
        base = max(0, int(aav) - proration)
    else:
        if label == "frontloaded":
            weights = [1.34, 1.18, 1.02, 0.90, 0.80, 0.72]
        elif label == "bonus-heavy":
            weights = [0.62, 0.92, 1.12, 1.28, 1.42, 1.56]
        else:
            weights = [0.72, 0.90, 1.08, 1.24, 1.38, 1.52]
        used = [weights[index] if index < len(weights) else weights[-1] for index in range(years)]
        base = int(base_pool * (used[0] / sum(used)))
    return max(0, base + proration)


def cpu_contract_structure_for_offer(
    player: sqlite3.Row | dict[str, Any],
    *,
    team_cap_space: int | None,
    aav: int,
    years: int,
    guarantee_pct: int,
    rng: random.Random,
    own_retention: bool = False,
) -> str:
    if years <= 1:
        return "balanced"
    group = normalized_group(player)
    tier = normalized_tier(player)
    score = true_overall(player)
    age = int(row_value(player, "age", 27) or 27)
    cap_space = int(team_cap_space or 0)
    first_year_flat = aav
    if group == "RB" or age >= POSITION_OLD_AGE.get(group, 31) + 1:
        return "frontloaded" if guarantee_pct >= 28 or own_retention else "balanced"
    if tier == "Premium" or score >= 78:
        if cap_space and cap_space < int(first_year_flat * 1.35):
            return "backloaded"
        if group in {"QB", "WR", "OT", "EDGE", "CB"} and years >= 3 and rng.random() < 0.45:
            return "backloaded"
    if guarantee_pct >= 45 and years >= 3 and rng.random() < 0.25:
        return "bonus-heavy"
    if cap_space and cap_space > int(first_year_flat * 2.5) and rng.random() < 0.18:
        return "frontloaded"
    return "balanced"


def need_threshold_for_market_pressure(player: sqlite3.Row | dict[str, Any]) -> float:
    group = normalized_group(player)
    if group == "QB":
        return 68.0
    if cpu_elite_free_agent(player):
        return 36.0
    if normalized_tier(player) == "Premium":
        return 44.0
    return 52.0


def market_pressure_for_need(
    player: sqlite3.Row | dict[str, Any],
    *,
    base_pressure: float,
    team_need: float,
) -> float:
    threshold = need_threshold_for_market_pressure(player)
    if team_need < threshold:
        return 0.0
    return clamp(base_pressure * clamp((team_need - threshold + 18.0) / 46.0, 0.20, 1.0), 0.0, 1.0)


def recent_team_release_for_player(
    con: sqlite3.Connection,
    *,
    player_id: int,
    team_id: int,
    since_date: str,
) -> bool:
    if not table_exists(con, "transaction_log"):
        return False
    row = con.execute(
        """
        SELECT 1
        FROM transaction_log
        WHERE player_id = ?
          AND team_id = ?
          AND transaction_date >= ?
          AND transaction_type IN ('Release', 'Waiver', 'Contract Termination')
        LIMIT 1
        """,
        (int(player_id), int(team_id), str(since_date)),
    ).fetchone()
    return row is not None


def recent_rejected_offer_for_player(
    con: sqlite3.Connection,
    *,
    league_year: int,
    team_id: int,
    player_id: int,
    current_date: str,
    cooldown_days: int = 21,
) -> bool:
    if not table_exists(con, "free_agency_offers") or not current_date:
        return False
    row = con.execute(
        """
        SELECT 1
        FROM free_agency_offers
        WHERE league_year = ?
          AND team_id = ?
          AND player_id = ?
          AND status = 'rejected'
          AND julianday(?) - julianday(COALESCE(decided_date, submitted_date)) < ?
        LIMIT 1
        """,
        (int(league_year), int(team_id), int(player_id), str(current_date), int(cooldown_days)),
    ).fetchone()
    return row is not None


def recent_rejected_offer_count_for_group(
    con: sqlite3.Connection,
    *,
    league_year: int,
    team_id: int,
    group: str,
    current_date: str,
    cooldown_days: int = 21,
) -> int:
    if not table_exists(con, "free_agency_offers") or not current_date:
        return 0
    row = con.execute(
        """
        SELECT COUNT(*) AS count
        FROM free_agency_offers o
        JOIN free_agency_player_markets m
          ON m.league_year = o.league_year
         AND m.player_id = o.player_id
        WHERE o.league_year = ?
          AND o.team_id = ?
          AND m.position_group = ?
          AND o.status = 'rejected'
          AND julianday(?) - julianday(COALESCE(o.decided_date, o.submitted_date)) < ?
        """,
        (int(league_year), int(team_id), str(group), str(current_date), int(cooldown_days)),
    ).fetchone()
    return int(row["count"] or 0) if row else 0


def expire_stale_pending_offers(
    con: sqlite3.Connection,
    *,
    league_year: int,
    current_date: str,
    stale_offer_days: int,
    note: str = "Expired after sitting pending too long.",
) -> int:
    if not table_exists(con, "free_agency_offers") or not current_date:
        return 0
    cur = con.execute(
        """
        UPDATE free_agency_offers
        SET status = 'expired',
            decided_date = ?,
            decided_hour = NULL,
            notes = COALESCE(notes || ' | ', '') || ?,
            updated_at = datetime('now')
        WHERE league_year = ?
          AND status = 'pending'
          AND julianday(?) - julianday(submitted_date) >= ?
        """,
        (str(current_date), str(note), int(league_year), str(current_date), int(stale_offer_days)),
    )
    return int(cur.rowcount or 0)


def stale_offer_days_for_period(con: sqlite3.Connection, period: sqlite3.Row | dict[str, Any]) -> int:
    league_year = int(row_value(period, "league_year", default_league_year(con)) or default_league_year(con))
    if is_post_draft_market_context(con, league_year):
        return 7
    if cpu_late_market(period):
        return 10
    return 21


def post_draft_depth_signing_counts_by_group(
    con: sqlite3.Connection,
    *,
    league_year: int,
) -> dict[tuple[int, str], int]:
    if not table_exists(con, "free_agency_offers"):
        return {}
    rows = con.execute(
        """
        SELECT o.team_id, m.position_group, COUNT(*) AS signed_count
        FROM free_agency_offers o
        JOIN free_agency_player_markets m
          ON m.league_year = o.league_year
         AND m.player_id = o.player_id
        WHERE o.league_year = ?
          AND o.status = 'accepted'
          AND o.decided_date >= ?
          AND COALESCE(o.notes, '') LIKE '%post-draft depth%'
        GROUP BY o.team_id, m.position_group
        """,
        (int(league_year), f"{int(league_year)}-04-01"),
    ).fetchall()
    return {
        (int(row["team_id"]), str(row["position_group"])): int(row["signed_count"] or 0)
        for row in rows
    }


def current_year_fa_contract(
    con: sqlite3.Connection,
    *,
    contract_id: int,
    player_id: int,
    team_id: int,
    league_year: int,
) -> bool:
    if not table_exists(con, "free_agency_offers"):
        return False
    row = con.execute(
        """
        SELECT 1
        FROM free_agency_offers o
        WHERE o.league_year = ?
          AND o.player_id = ?
          AND o.team_id = ?
          AND o.status = 'accepted'
          AND EXISTS (
              SELECT 1
              FROM contracts c
              WHERE c.contract_id = ?
                AND c.player_id = o.player_id
                AND c.team_id = o.team_id
                AND c.start_year = o.league_year
          )
        LIMIT 1
        """,
        (int(league_year), int(player_id), int(team_id), int(contract_id)),
    ).fetchone()
    return row is not None


def cpu_extend_expiring_players(
    con: sqlite3.Connection,
    *,
    expiring_season: int,
    league_year: int,
    user_team: str | None,
    per_team: int,
    seed: int | None = None,
    write_cap_snapshot: bool = True,
) -> int:
    if per_team <= 0:
        return 0
    rng = random.Random(seed or f"cpu-extensions:{league_year}")
    teams = con.execute("SELECT abbreviation FROM teams ORDER BY abbreviation").fetchall()
    retained = 0
    total_limit = max(0, per_team * max(1, len(teams)))
    for team in teams:
        if retained >= total_limit:
            break
        abbr = str(team["abbreviation"])
        if user_team and abbr == user_team:
            continue
        try:
            rows = contract_negotiations.expiring_players(con, abbr, expiring_season)
        except Exception:
            continue
        rows.sort(
            key=lambda player: (
                1 if cpu_must_protect_expiring_player(player) else 0,
                float(row_value(player, "market_score", 60) or 60),
                int(row_value(player, "potential", row_value(player, "overall", 60)) or 60),
                -int(row_value(player, "age", 28) or 28),
            ),
            reverse=True,
        )
        kept_for_team = 0
        for player in rows:
            must_protect = cpu_must_protect_expiring_player(player)
            if kept_for_team >= per_team and not must_protect:
                continue
            team_id = int(row_value(player, "team_id", 0) or 0)
            if team_id <= 0:
                team_row = con.execute("SELECT team_id FROM teams WHERE abbreviation = ?", (abbr,)).fetchone()
                team_id = int(team_row["team_id"] or 0) if team_row else 0
            score = float(row_value(player, "market_score", 60) or 60)
            group = str(row_value(player, "position_group", "") or "")
            must_protect = must_protect or score >= 88 or (score >= 84 and group in {"QB", "OT", "EDGE", "WR", "CB"})
            if not must_protect and rng.random() > cpu_re_sign_probability(player):
                continue
            low, high = cpu_aav_bounds(player)
            if team_id > 0 and group == "QB":
                room = team_group_room_context(
                    con,
                    team_id,
                    "QB",
                    exclude_player_id=int(row_value(player, "player_id", 0) or 0),
                )
                qb_limits = qb_room_offer_limits(
                    con,
                    player,
                    team_qb_scores=[float(value) for value in room["scores"]],
                    team_need=load_team_need_scores(con).get((team_id, "QB"), 0.0),
                    roster_qb_count=int(room["count"]),
                )
                if not qb_limits["allowed"]:
                    continue
                if qb_limits["backup_cap"]:
                    high = min(high, int(qb_limits["backup_cap"]))
                    low = min(low, high)
            aav = round_to(rng.randint(low, high), 100_000)
            years = min(max(1, int(player["suggested_years"] or 1)), cpu_offer_year_cap(player, own_retention=True))
            if group == "QB" and team_id > 0:
                room = team_group_room_context(
                    con,
                    team_id,
                    "QB",
                    exclude_player_id=int(row_value(player, "player_id", 0) or 0),
                )
                if int(room["best"]) >= 82 and score <= float(room["best"]) - 4:
                    years = 1
            guard_period = {"league_year": league_year, "current_date": f"{league_year}-03-10", "current_hour": 9}
            guard_offer = {
                "team_id": team_id,
                "player_id": int(row_value(player, "player_id", 0) or 0),
                "aav": aav,
                "years": years,
                "signing_bonus": 0,
            }
            allowed, _reason = cpu_final_signing_guardrails(
                con,
                guard_period,
                guard_offer,
                dict(player),
                own_retention=True,
            )
            if not allowed:
                continue
            try:
                contract_negotiations.extend_player(
                    con,
                    team=abbr,
                    season=expiring_season,
                    player_id=int(player["player_id"]),
                    years=years,
                    aav=aav,
                    signing_bonus=0,
                    apply=True,
                    force=False,
                    quiet=True,
                    rebuild_all_contracts=False,
                    sync_cap=False,
                    write_cap_snapshot=False,
                )
            except Exception:
                continue
            kept_for_team += 1
            retained += 1
            if retained >= total_limit:
                break
    if retained:
        sync_team_cap_space(con)
        if write_cap_snapshot:
            snapshot_cap_ledger(
                con,
                label=f"free_agency_{league_year}_cpu_extensions",
                phase=PHASE,
                source=SOURCE,
                replace=True,
            )
    return retained


def cpu_apply_pre_fa_tags(
    con: sqlite3.Connection,
    *,
    expiring_season: int,
    league_year: int,
    user_team: str | None,
    seed: int | None = None,
    write_cap_snapshot: bool = True,
) -> int:
    rng = random.Random(seed or f"cpu-tags:{league_year}")
    tagged = 0
    teams = con.execute("SELECT team_id, abbreviation FROM teams ORDER BY team_id").fetchall()
    for team in teams:
        abbr = str(team["abbreviation"])
        if user_team and abbr == user_team:
            continue
        if contract_negotiations.existing_team_tag(con, int(team["team_id"]), league_year):
            continue
        try:
            players = contract_negotiations.expiring_players(con, int(team["team_id"]), expiring_season)
        except Exception:
            continue
        candidates: list[tuple[float, str, dict[str, Any]]] = []
        projected = contract_negotiations.projected_cap_summary(con, int(team["team_id"]), league_year) or {}
        cap_space = int(projected.get("cap_space") or 0)
        for player in players:
            if str(player.get("rights_type") or "UFA").upper() != "UFA":
                continue
            score = float(player.get("market_score") or 60)
            group = str(player.get("position_group") or "")
            age = int(player.get("age") or 28)
            overall = int(player.get("overall") or score)
            potential = int(player.get("potential") or overall)
            franchise = int(player.get("franchise_tag_aav") or 0)
            transition = int(player.get("transition_tag_aav") or 0)
            age_fit = cpu_tag_age_fit(group, age, score, overall, potential)
            if not age_fit:
                continue
            if group == "RB":
                eligible = score >= 88 and age <= 27 and franchise <= cap_space - 5_000_000
            elif group == "QB":
                current_aav = int(player.get("aav") or 0)
                eligible = (
                    score >= 88
                    and overall >= 84
                    and potential >= 86
                    and franchise <= cap_space - 10_000_000
                    and franchise <= max(int(current_aav * 2.25), int(player.get("asking_aav") or 0) + 12_000_000)
                )
            elif group == "WR" and int(player.get("overall") or score) >= 84 and int(player.get("potential") or score) >= 87 and age <= 29:
                eligible = franchise <= cap_space - 5_000_000
            elif group in {"WR", "OT", "EDGE", "CB", "IDL"}:
                eligible = score >= 84 and franchise <= cap_space - 7_500_000
            elif group == "IOL":
                eligible = score >= 87 and franchise <= cap_space - 7_500_000
            elif group in {"TE", "S", "LB"}:
                eligible = score >= 86 and franchise <= cap_space - 7_500_000
            else:
                eligible = score >= 88 and franchise <= cap_space - 7_500_000
            transition_threshold = {
                "QB": 88,
                "WR": 84,
                "OT": 83,
                "EDGE": 83,
                "CB": 82,
                "IDL": 83,
                "IOL": 86,
                "TE": 84,
                "S": 84,
                "LB": 85,
            }.get(group, 86)
            transition_ok = (
                age_fit
                and score >= transition_threshold
                and group not in {"RB", "ST"}
                and transition <= cap_space - 6_000_000
            )
            if group == "QB":
                current_aav = int(player.get("aav") or 0)
                transition_ok = (
                    transition_ok
                    and overall >= 84
                    and potential >= 86
                    and transition <= max(int(current_aav * 2.25), int(player.get("asking_aav") or 0) + 12_000_000)
                )
            if eligible:
                surplus = max(0, int(player.get("asking_aav") or 0) - franchise)
                if score >= 90:
                    probability = 0.98
                elif score >= 86 and group in {"QB", "WR", "OT", "EDGE", "CB", "IDL"}:
                    probability = 0.90
                else:
                    probability = 0.52 + clamp((score - 82) * 0.04, 0.0, 0.28) + clamp(surplus / 12_000_000, 0.0, 0.18)
                if rng.random() <= min(0.98, probability):
                    upside = max(0, potential - overall)
                    age_bonus = max(0, 31 - age) * 0.35
                    candidates.append((score + upside * 0.45 + age_bonus + surplus / 1_000_000, "franchise", player))
            elif transition_ok:
                probability = 0.36 + clamp((score - 78) * 0.04, 0.0, 0.26)
                if rng.random() <= min(0.68, probability):
                    upside = max(0, potential - overall)
                    age_bonus = max(0, 31 - age) * 0.20
                    candidates.append((score + upside * 0.25 + age_bonus, "transition", player))
        if not candidates:
            continue
        candidates.sort(key=lambda item: item[0], reverse=True)
        _value, tag_type, player = candidates[0]
        try:
            contract_negotiations.apply_tag(
                con,
                team=abbr,
                season=expiring_season,
                player_id=int(player["player_id"]),
                tag_type=tag_type,
                apply=True,
                force=False,
                target_player=player,
                skip_cap_check=True,
                quiet=True,
                rebuild_all_contracts=False,
                sync_cap=False,
                write_cap_snapshot=False,
            )
        except Exception:
            continue
        tagged += 1
    if tagged:
        sync_team_cap_space(con)
        if write_cap_snapshot:
            snapshot_cap_ledger(
                con,
                label=f"free_agency_{league_year}_cpu_tags",
                phase=PHASE,
                source=SOURCE,
                replace=True,
            )
    return tagged


def cpu_choose_rights_tender(player: dict[str, Any] | sqlite3.Row, rng: random.Random) -> str | None:
    rights_type = str(row_value(player, "rights_type", "") or "").upper()
    score = float(row_value(player, "market_score", 60) or 60)
    overall = int(row_value(player, "overall", score) or score)
    potential = int(row_value(player, "potential", overall) or overall)
    group = str(row_value(player, "position_group", "") or "")
    age = int(row_value(player, "age", 24) or 24)
    status = str(row_value(player, "status", "") or "")
    if rights_type == "ERFA":
        if score >= 55 or status == "Active" or rng.random() < 0.62:
            return "erfa"
        return None
    if rights_type != "RFA":
        return None
    premium_group = group in {"QB", "OT", "EDGE", "CB", "WR", "IDL"}
    if overall >= 78 or (premium_group and overall >= 75 and potential >= 82):
        return "rfa_first" if overall >= 82 and rng.random() < 0.22 else "rfa_second"
    if overall >= 73 or (premium_group and overall >= 70 and potential >= 80):
        return "rfa_second" if rng.random() < 0.38 else "rfa_original"
    if overall >= 68 or (age <= 25 and potential >= 76 and overall >= 64):
        return "rfa_original" if rng.random() < 0.42 else "rfa_rofr"
    if overall >= 62 or (age <= 25 and potential >= 72 and overall >= 58):
        return "rfa_rofr"
    return None


def cpu_apply_pre_fa_tenders(
    con: sqlite3.Connection,
    *,
    expiring_season: int,
    league_year: int,
    user_team: str | None,
    seed: int | None = None,
    write_cap_snapshot: bool = True,
) -> int:
    rng = random.Random(seed or f"cpu-rights-tenders:{league_year}")
    tendered = 0
    teams = con.execute("SELECT team_id, abbreviation FROM teams ORDER BY team_id").fetchall()
    for team in teams:
        abbr = str(team["abbreviation"])
        if user_team and abbr == user_team:
            continue
        try:
            players = contract_negotiations.expiring_players(con, int(team["team_id"]), expiring_season)
        except Exception:
            continue
        projected = contract_negotiations.projected_cap_summary(con, int(team["team_id"]), league_year) or {}
        cap_space = int(projected.get("cap_space") or 0)
        players.sort(
            key=lambda player: (
                str(player.get("rights_type") or "") == "RFA",
                float(player.get("market_score") or 60),
            ),
            reverse=True,
        )
        for player in players:
            tender_type = cpu_choose_rights_tender(player, rng)
            if not tender_type:
                continue
            group = str(player.get("position_group") or "")
            tender_aav = contract_negotiations.tag_tender_aav(group, int(player.get("aav") or 0), tender_type)
            # Rights tenders should be routine, but avoid burying teams that are already tight.
            if tender_aav > max(0, cap_space + 2_500_000):
                continue
            try:
                contract_negotiations.apply_tag(
                    con,
                    team=abbr,
                    season=expiring_season,
                    player_id=int(player["player_id"]),
                    tag_type=tender_type,
                    apply=True,
                    force=False,
                    target_player=player,
                    skip_cap_check=True,
                    quiet=True,
                    rebuild_all_contracts=False,
                    sync_cap=False,
                    write_cap_snapshot=False,
                )
            except Exception:
                continue
            cap_space -= tender_aav
            tendered += 1
    if tendered:
        sync_team_cap_space(con)
        if write_cap_snapshot:
            snapshot_cap_ledger(
                con,
                label=f"free_agency_{league_year}_cpu_rights_tenders",
                phase=PHASE,
                source=SOURCE,
                replace=True,
            )
    return tendered


def cpu_apply_fifth_year_options(
    con: sqlite3.Connection,
    *,
    league_year: int,
    user_team: str | None,
    write_cap_snapshot: bool = True,
) -> int:
    exercised = 0
    teams = con.execute("SELECT team_id, abbreviation FROM teams ORDER BY team_id").fetchall()
    for team in teams:
        abbr = str(team["abbreviation"])
        if user_team and abbr == user_team:
            continue
        try:
            candidates = contract_negotiations.fifth_year_option_candidates(con, int(team["team_id"]), league_year)
        except Exception:
            continue
        for player in candidates:
            score = float(player.get("market_score") or 60)
            group = str(player.get("position_group") or "")
            recommendation = str(player.get("recommendation") or "")
            keep = recommendation == "Exercise" or (group == "QB" and score >= 68)
            if not keep:
                try:
                    contract_negotiations.decline_fifth_year_option(
                        con,
                        team=abbr,
                        league_year=league_year,
                        player_id=int(player["player_id"]),
                        apply=True,
                        quiet=True,
                    )
                except Exception:
                    pass
                continue
            try:
                contract_negotiations.exercise_fifth_year_option(
                    con,
                    team=abbr,
                    league_year=league_year,
                    player_id=int(player["player_id"]),
                    apply=True,
                    force=False,
                    quiet=True,
                    rebuild_all_contracts=False,
                    sync_cap=False,
                    write_cap_snapshot=False,
                )
            except Exception:
                continue
            exercised += 1
    if exercised:
        sync_team_cap_space(con)
        if write_cap_snapshot:
            snapshot_cap_ledger(
                con,
                label=f"free_agency_{league_year}_cpu_fifth_year_options",
                phase=PHASE,
                source=SOURCE,
                replace=True,
            )
    return exercised


def start_period(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    sync_active_game_to_date(con, str(args.start_date))
    user_team = cpu_excluded_user_team(con, args)
    market_user_team = general_market_excluded_user_team(con, args)
    write_cap_snapshot = not getattr(args, "no_cap_snapshot", False)
    deactivate_elapsed_active_contracts(con, int(args.league_year))
    cpu_extensions = 0
    expiration_result = {"processed": 0}
    if not args.skip_expirations:
        rollover_result = contract_negotiations.process_cap_rollover(
            con,
            from_season=int(args.league_year) - 1,
            to_season=int(args.league_year),
            apply=True,
            quiet=True,
        )
        cpu_options = cpu_apply_fifth_year_options(
            con,
            league_year=int(args.league_year),
            user_team=user_team,
            write_cap_snapshot=write_cap_snapshot,
        )
        cpu_extensions = cpu_extend_expiring_players(
            con,
            expiring_season=int(args.league_year) - 1,
            league_year=int(args.league_year),
            user_team=user_team,
            per_team=args.cpu_resign_per_team,
            seed=args.seed,
            write_cap_snapshot=write_cap_snapshot,
        )
        cpu_tenders = cpu_apply_pre_fa_tenders(
            con,
            expiring_season=int(args.league_year) - 1,
            league_year=int(args.league_year),
            user_team=user_team,
            seed=args.seed,
            write_cap_snapshot=write_cap_snapshot,
        )
        cpu_tags = cpu_apply_pre_fa_tags(
            con,
            expiring_season=int(args.league_year) - 1,
            league_year=int(args.league_year),
            user_team=user_team,
            seed=args.seed,
            write_cap_snapshot=write_cap_snapshot,
        )
        expiration_result = contract_negotiations.process_expired_contracts(
            con,
            expiring_season=int(args.league_year) - 1,
            contract_league_year=int(args.league_year),
            transaction_date=str(args.start_date),
            write_cap_snapshot=write_cap_snapshot,
        )
        deactivated_elapsed_after_rights = deactivate_elapsed_active_contracts(con, int(args.league_year))
    else:
        rollover_result = {"teams": []}
        cpu_options = 0
        cpu_tags = 0
        cpu_tenders = 0
        deactivated_elapsed_after_rights = deactivate_elapsed_active_contracts(con, int(args.league_year))
        contract_negotiations.set_current_contract_year(con, int(args.league_year))
    ensure_market(con, args.league_year)
    con.execute(
        """
        INSERT INTO free_agency_periods (
            league_year, status, current_stage, "current_date", current_hour,
            day_count, first_day_start_hour, first_day_end_hour,
            started_at, updated_at, notes
        )
        VALUES (?, 'active', 'day_one_hourly', ?, ?, 1, ?, ?, datetime('now'), datetime('now'), ?)
        ON CONFLICT(league_year) DO UPDATE SET
            status = 'active',
            current_stage = 'day_one_hourly',
            "current_date" = excluded."current_date",
            current_hour = excluded.current_hour,
            day_count = 1,
            first_day_start_hour = excluded.first_day_start_hour,
            first_day_end_hour = excluded.first_day_end_hour,
            completed_at = NULL,
            updated_at = datetime('now'),
            notes = excluded.notes
        """,
        (
            args.league_year,
            args.start_date,
            args.start_hour,
            args.start_hour,
            args.end_hour,
            args.notes,
        ),
    )
    period = active_period(con, args.league_year)
    cpu_restructures = cpu_restructure_core_contracts_for_fa(con, period, user_team=user_team)
    cpu_cap_releases = cpu_release_bad_contracts_for_fa(con, period, user_team=user_team, max_total=24)
    cpu_strategic_releases = cpu_release_strategic_cap_casualties_for_fa(
        con,
        period,
        user_team=user_team,
        max_total=8,
        max_per_team=1,
    )
    cpu_retained = cpu_retain_own_free_agents(
        con,
        period,
        user_team=user_team,
        per_team=args.cpu_retention_per_team,
        seed=args.seed,
        write_cap_snapshot=write_cap_snapshot,
    )
    user_plan = apply_cpu_controlled_user_free_agent_plan(
        con,
        period,
        args,
        max_offers=max(2, min(5, int(args.opening_cpu_offers or 0) // 8 if int(args.opening_cpu_offers or 0) else 4)),
    )
    opening_cpu_offers = create_cpu_offers(
        con,
        period,
        args.opening_cpu_offers,
        args.seed,
        user_team=market_user_team,
    )
    cap_cleanup = cpu_cap_compliance_sweep(
        con,
        int(args.league_year),
        user_team=user_team,
        min_space=8_000_000,
        max_moves_per_team=5,
        max_teams=32,
        time_budget_seconds=45.0,
        write_snapshot=write_cap_snapshot,
    )
    if int(cap_cleanup.get("still_over") or 0) > 0:
        followup_cleanup = cpu_cap_compliance_sweep(
            con,
            int(args.league_year),
            user_team=user_team,
            min_space=4_000_000,
            max_moves_per_team=3,
            max_teams=32,
            time_budget_seconds=30.0,
            write_snapshot=write_cap_snapshot,
        )
        for key, value in followup_cleanup.items():
            if key == "still_over":
                cap_cleanup[key] = value
            else:
                cap_cleanup[key] = int(cap_cleanup.get(key) or 0) + int(value or 0)
    log_event(
        con,
        league_year=args.league_year,
        event_date=args.start_date,
        event_hour=args.start_hour,
        event_type="period_started",
        message=(
            f"{args.league_year} free agency opened. "
            f"Expired contracts processed: {expiration_result['processed']}. "
            f"Cap rollovers: {sum(1 for row in rollover_result.get('teams', []) if row.get('rollover_amount'))}. "
            f"CPU fifth-year options: {cpu_options}. "
            f"CPU tags: {cpu_tags}. "
            f"CPU rights tenders: {cpu_tenders}. "
            f"CPU extensions: {cpu_extensions}. "
            f"CPU restructures: {cpu_restructures}. "
            f"CPU cap releases: {cpu_cap_releases}. "
            f"CPU strategic releases: {cpu_strategic_releases}. "
            f"CPU own-player FA re-signings: {cpu_retained}. "
            f"User auto-FA plan offers: {user_plan.get('offers', 0)}. "
            f"Opening CPU offers: {opening_cpu_offers}. "
            f"Elapsed contract cleanup: {deactivated_elapsed_after_rights}. "
            f"Cap compliance: {cap_cleanup.get('teams', 0)} team(s), "
            f"{cap_cleanup.get('restructures', 0)} restructure(s), "
            f"{cap_cleanup.get('releases', 0)} release(s), "
            f"{cap_cleanup.get('still_over', 0)} still short. "
            f"Day 1 is hourly until {args.end_hour}:00."
        ),
    )


def submit_offer(
    con: sqlite3.Connection,
    *,
    league_year: int,
    team_id: int,
    player_id: int,
    years: int,
    aav: int,
    signing_bonus: int,
    guarantee_pct: int,
    submitted_date: str,
    submitted_hour: int | None,
    contract_structure: str = "balanced",
    notes: str | None = None,
) -> int:
    total_value = int(aav) * int(years)
    structure = normalized_contract_structure(contract_structure)
    cur = con.execute(
        """
        INSERT INTO free_agency_offers (
            league_year, player_id, team_id, years, aav, total_value,
            signing_bonus, guarantee_pct, contract_structure, status, submitted_date,
            submitted_hour, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            league_year,
            player_id,
            team_id,
            years,
            int(aav),
            total_value,
            int(signing_bonus),
            int(guarantee_pct),
            structure,
            submitted_date,
            submitted_hour,
            notes,
        ),
    )
    con.execute(
        """
        UPDATE free_agency_player_markets
        SET last_offer_at = ?, updated_at = datetime('now')
        WHERE league_year = ? AND player_id = ?
        """,
        (
            f"{submitted_date} {submitted_hour:02d}:00" if submitted_hour is not None else submitted_date,
            league_year,
            player_id,
        ),
    )
    return int(cur.lastrowid)


def action_offer_inner(con: sqlite3.Connection, args: argparse.Namespace) -> dict[str, int]:
    ensure_schema(con)
    period = active_period(con, args.league_year)
    team = team_by_abbr(con, args.team)
    player = player_by_id_or_name(con, args.player)
    market = con.execute(
        """
        SELECT *
        FROM free_agency_player_markets
        WHERE league_year = ? AND player_id = ?
        """,
        (args.league_year, player["player_id"]),
    ).fetchone()
    if not market or market["status"] != "available":
        raise ValueError(f"{player['first_name']} {player['last_name']} is not available in this market.")
    offer_date, offer_hour = event_time(period)
    aav = roster_actions.parse_money(args.aav)
    bonus = roster_actions.parse_money(args.bonus or 0)
    contract_structure = normalized_contract_structure(getattr(args, "structure", None))
    if aav < int(market["minimum_aav"]) and not args.force_market:
        raise ValueError(
            f"Offer is below minimum AAV ({money(market['minimum_aav'])}). Use --force-market to override."
        )
    offer_id = submit_offer(
        con,
        league_year=args.league_year,
        team_id=int(team["team_id"]),
        player_id=int(player["player_id"]),
        years=args.years,
        aav=aav,
        signing_bonus=bonus,
        guarantee_pct=args.guarantee_pct,
        contract_structure=contract_structure,
        submitted_date=offer_date,
        submitted_hour=offer_hour,
        notes=args.notes,
    )
    log_event(
        con,
        league_year=args.league_year,
        event_date=offer_date,
        event_hour=offer_hour,
        event_type="offer_submitted",
        team_id=int(team["team_id"]),
        player_id=int(player["player_id"]),
        offer_id=offer_id,
        message=(
            f"{team['abbreviation']} offered {player['first_name']} {player['last_name']} "
            f"{args.years} year(s), {money(aav)} AAV ({contract_structure})."
        ),
    )
    cpu_offers = create_cpu_competing_offers(
        con,
        period,
        player_id=int(player["player_id"]),
        user_team=str(args.team).upper(),
        count=args.cpu_response_offers,
        seed=args.seed,
    )
    return {"offer_id": offer_id, "cpu_offers": cpu_offers}


def load_team_context(con: sqlite3.Connection, season: int) -> dict[int, dict[str, float]]:
    if not table_exists(con, "season_standings_view"):
        return {}
    rows = con.execute(
        """
        SELECT team_id, COALESCE(win_pct, 0.5) AS win_pct, COALESCE(point_diff, 0) AS point_diff
        FROM season_standings_view
        WHERE season = ?
        """,
        (season,),
    ).fetchall()
    return {
        int(row["team_id"]): {
            "win_pct": float(row["win_pct"] or 0.5),
            "point_diff": float(row["point_diff"] or 0),
        }
        for row in rows
    }


def load_playing_time_competition(
    con: sqlite3.Connection,
    season: int,
) -> dict[tuple[int, str], list[float]]:
    rows = con.execute(
        """
        SELECT
            p.player_id,
            p.team_id,
            p.position,
            COALESCE(p.overall, 50) AS overall,
            COALESCE(p.potential, p.overall, 50) AS potential,
            COALESCE(role.role_score, p.overall, 50) AS player_score
        FROM players p
        LEFT JOIN (
            SELECT player_id, MAX(role_score) AS role_score
            FROM player_role_scores
            WHERE scheme_key = 'default'
              AND season = ?
            GROUP BY player_id
        ) role ON role.player_id = p.player_id
        WHERE p.team_id IS NOT NULL
          AND p.status = 'Active'
        """,
        (season,),
    ).fetchall()
    by_team: dict[int, list[int]] = {}
    for row in rows:
        by_team.setdefault(int(row["team_id"]), []).append(int(row["player_id"]))
    game_id = pro_player_fog.active_game_id(con)
    reads_by_team: dict[int, dict[int, dict[str, Any]]] = {}
    for team_id, player_ids in by_team.items():
        reads, created = pro_player_fog.evaluations_for_team(
            con,
            game_id=game_id,
            season=season,
            evaluator_team_id=team_id,
            player_ids=player_ids,
            create_missing=True,
        )
        if created:
            con.commit()
        reads_by_team[team_id] = reads
    competition: dict[tuple[int, str], list[float]] = {}
    for row in rows:
        team_id = int(row["team_id"])
        key = (team_id, position_group_for(str(row["position"])))
        read = reads_by_team.get(team_id, {}).get(int(row["player_id"]))
        score = float(read.get("overall") if read else row["player_score"] or 50)
        competition.setdefault(key, []).append(score)
    for scores in competition.values():
        scores.sort(reverse=True)
    return competition


def player_has_backup_qb_history(con: sqlite3.Connection, player_id: int) -> bool:
    row = None
    if table_exists(con, "player_career_stats"):
        row = con.execute(
            "SELECT career_games, passing_attempts FROM player_career_stats WHERE player_id = ?",
            (int(player_id),),
        ).fetchone()
    player = con.execute("SELECT years_exp FROM players WHERE player_id = ?", (int(player_id),)).fetchone()
    years_exp = int(player["years_exp"] or 0) if player else 0
    if not row:
        return years_exp >= 7
    games = int(row["career_games"] or 0)
    attempts = int(row["passing_attempts"] or 0)
    return years_exp >= 5 and (attempts < 1200 or (games >= 40 and attempts / max(1, games) < 20))


def qb_backup_reluctance(
    con: sqlite3.Connection,
    player: sqlite3.Row,
    *,
    team_id: int,
    team_qb_scores: list[float],
    team_need: float,
    rng: random.Random,
    incoming_score_override: float | None = None,
) -> float | None:
    """Return an AAV multiplier for backup-QB offers, or None to skip the fit."""
    if not team_qb_scores:
        return 1.0
    incoming_score = (
        float(incoming_score_override)
        if incoming_score_override is not None
        else float(row_value(player, "market_score", row_value(player, "overall", 60)) or 60)
    )
    incumbent = max(team_qb_scores)
    if incoming_score >= incumbent - 2 or team_need >= 72:
        return 1.0
    role_priority = int(row_value(player, "role_priority", 10) or 10)
    security_priority = int(row_value(player, "security_priority", 10) or 10)
    contender_priority = int(row_value(player, "contender_priority", 10) or 10)
    money_priority = int(row_value(player, "money_priority", 10) or 10)
    backup_history = player_has_backup_qb_history(con, int(row_value(player, "player_id", 0) or 0))
    franchise_blocked = incumbent >= 82 and incoming_score <= incumbent - 5
    if not franchise_blocked:
        return 0.88 if incoming_score <= incumbent - 4 else 0.95

    willingness = 0.18
    if backup_history:
        willingness += 0.38
    willingness += max(0, security_priority - 10) * 0.025
    willingness += max(0, contender_priority - 10) * 0.020
    willingness -= max(0, role_priority - 10) * 0.045
    willingness -= max(0, money_priority - 14) * 0.020
    if incoming_score >= 72:
        willingness -= 0.10
    if team_qb_scores and len(team_qb_scores) <= 1:
        willingness += 0.10
    if rng.random() > clamp(willingness, 0.05, 0.82):
        return None
    return 0.62 if not backup_history else 0.74


def load_team_need_scores(con: sqlite3.Connection) -> dict[tuple[int, str], float]:
    game_id = pro_player_fog.active_game_id(con)
    season = default_league_year(con)
    rows = con.execute(
        """
        SELECT
            player_id,
            team_id,
            position,
            COALESCE(overall, 50) AS overall,
            COALESCE(potential, overall, 50) AS potential
        FROM players
        WHERE team_id IS NOT NULL
          AND status IN ('Active', 'Reserve/Future', 'PUP', 'IR')
        """
    ).fetchall()
    by_team: dict[int, list[int]] = {}
    for row in rows:
        by_team.setdefault(int(row["team_id"]), []).append(int(row["player_id"]))
    reads_by_team: dict[int, dict[int, dict[str, Any]]] = {}
    for team_id, player_ids in by_team.items():
        reads, created = pro_player_fog.evaluations_for_team(
            con,
            game_id=game_id,
            season=season,
            evaluator_team_id=team_id,
            player_ids=player_ids,
            create_missing=True,
        )
        if created:
            con.commit()
        reads_by_team[team_id] = reads
    rooms: dict[tuple[int, str], list[float]] = {}
    for row in rows:
        team_id = int(row["team_id"])
        group = position_group_for(str(row["position"]))
        read = reads_by_team.get(team_id, {}).get(int(row["player_id"]))
        score = float(read.get("overall") if read else row["overall"] or 50)
        rooms.setdefault((team_id, group), []).append(score)
    for scores in rooms.values():
        scores.sort(reverse=True)

    team_ids = [int(row["team_id"]) for row in con.execute("SELECT team_id FROM teams").fetchall()]
    groups = list(ROOM_IDEAL_BY_GROUP.keys())
    need_scores: dict[tuple[int, str], float] = {}
    for team_id in team_ids:
        for group in groups:
            scores = rooms.get((team_id, group), [])
            starters = STARTER_SLOTS_BY_GROUP.get(group, 1)
            ideal = ROOM_IDEAL_BY_GROUP.get(group, starters + 2)
            floor = STARTER_FLOOR_BY_GROUP.get(group, 68)
            starter_scores = scores[:starters]
            count_gap = max(0, ideal - len(scores))
            starter_gap = sum(max(0.0, floor - score) for score in starter_scores)
            if len(starter_scores) < starters:
                starter_gap += (starters - len(starter_scores)) * 12.0
            depth_scores = scores[starters:ideal]
            depth_gap = max(0, ideal - starters - len(depth_scores)) * 4.0
            thin_depth = sum(max(0.0, (floor - 8) - score) * 0.22 for score in depth_scores)
            need = min(100.0, count_gap * 7.0 + starter_gap * 1.7 + depth_gap + thin_depth)
            need_scores[(team_id, group)] = round(need, 2)
    return need_scores


def load_team_group_counts(con: sqlite3.Connection) -> dict[tuple[int, str], int]:
    rows = con.execute(
        """
        SELECT team_id, position
        FROM players
        WHERE team_id IS NOT NULL
          AND status IN ('Active', 'Reserve/Future', 'PUP', 'IR')
        """
    ).fetchall()
    counts: dict[tuple[int, str], int] = {}
    for row in rows:
        group = position_group_for(str(row["position"]))
        key = (int(row["team_id"]), group)
        counts[key] = counts.get(key, 0) + 1
    return counts


def load_team_active_group_spend(con: sqlite3.Connection) -> dict[tuple[int, str], int]:
    rows = con.execute(
        """
        SELECT
            p.team_id,
            p.position,
            SUM(COALESCE(c.aav, 0)) AS active_aav
        FROM players p
        LEFT JOIN contracts c
          ON c.player_id = p.player_id
         AND c.is_active = 1
        WHERE p.team_id IS NOT NULL
          AND p.status IN ('Active', 'Reserve/Future', 'PUP', 'IR')
        GROUP BY p.team_id, p.position
        """
    ).fetchall()
    spend: dict[tuple[int, str], int] = {}
    for row in rows:
        group = position_group_for(str(row["position"]))
        key = (int(row["team_id"]), group)
        spend[key] = spend.get(key, 0) + int(row["active_aav"] or 0)
    return spend


def room_spend_soft_limit(group: str) -> int:
    base = CPU_GROUP_SPEND_LIMITS.get(group, 24_000_000)
    multiplier = CPU_ACTIVE_ROOM_SPEND_MULTIPLIER.get(group, 1.45)
    return int(base * multiplier)


def expensive_room_offer_allowed(
    *,
    group: str,
    team_need: float,
    active_spend: int,
    pending_spend: int,
    offer_aav: int,
    player_score: float,
    player_potential: int,
    elite_target: bool,
) -> bool:
    projected = int(active_spend) + int(pending_spend) + int(offer_aav)
    soft_limit = room_spend_soft_limit(group)
    if projected <= soft_limit:
        return True
    if group == "QB":
        return team_need >= 82 and player_score >= 78
    if elite_target and team_need >= 42 and projected <= int(soft_limit * 1.28):
        return True
    if team_need >= 72 and (player_score >= 76 or player_potential >= 84) and projected <= int(soft_limit * 1.18):
        return True
    return False


def qb_room_offer_limits(
    con: sqlite3.Connection,
    player: sqlite3.Row | dict[str, Any],
    *,
    team_qb_scores: list[float],
    team_need: float,
    roster_qb_count: int,
    incoming_score_override: float | None = None,
    incoming_potential_override: int | None = None,
) -> dict[str, Any]:
    """Guard against teams with franchise QBs repeatedly chasing starter-priced backups."""
    incoming_score = float(incoming_score_override) if incoming_score_override is not None else float(true_overall(player))
    incoming_potential = (
        int(incoming_potential_override)
        if incoming_potential_override is not None
        else int(row_value(player, "potential", incoming_score) or incoming_score)
    )
    if not team_qb_scores:
        return {"allowed": True, "backup_cap": None}

    incumbent = max(float(score) for score in team_qb_scores)
    backup_history = player_has_backup_qb_history(con, int(row_value(player, "player_id", 0) or 0))
    starter_path = incoming_score >= incumbent - 2 or team_need >= 82
    if starter_path:
        if (
            incumbent >= 72
            and incoming_score <= incumbent + 2
            and roster_qb_count >= 1
            and team_need < 88
        ):
            if incoming_score >= 74:
                cap = 8_500_000 if backup_history else 7_000_000
            elif incoming_score >= 70:
                cap = 7_000_000 if backup_history else 5_800_000
            else:
                cap = 4_800_000
            return {"allowed": True, "backup_cap": cap}
        return {"allowed": True, "backup_cap": None}

    if incumbent >= 86 and incoming_score >= 70 and incoming_score <= incumbent - 3:
        if incoming_score >= 74 or incoming_potential >= 80 or not backup_history:
            return {"allowed": False, "backup_cap": 0}

    if incumbent >= 82 and incoming_score <= incumbent - 3:
        if roster_qb_count >= 2 and incoming_score >= 72 and not backup_history:
            return {"allowed": False, "backup_cap": 0}
        if incoming_score >= 72:
            cap = 8_000_000 if backup_history else 6_500_000
        elif incoming_score >= 68:
            cap = 6_500_000 if backup_history else 5_500_000
        else:
            cap = 4_500_000
        return {"allowed": True, "backup_cap": cap}

    if incumbent >= 78 and roster_qb_count >= 2 and incoming_score < incumbent - 5 and team_need < 62:
        return {"allowed": True, "backup_cap": 5_500_000 if incoming_score < 70 else 7_000_000}

    return {"allowed": True, "backup_cap": None}


def team_group_room_context(
    con: sqlite3.Connection,
    team_id: int,
    group: str,
    *,
    season: int | None = None,
    exclude_player_id: int | None = None,
) -> dict[str, Any]:
    rows = con.execute(
        """
        SELECT
            p.player_id,
            p.position,
            p.age,
            COALESCE(p.overall, 50) AS overall,
            COALESCE(p.potential, p.overall, 50) AS potential,
            p.status
        FROM players p
        WHERE p.team_id = ?
          AND p.status IN ('Active', 'IR', 'PUP', 'Reserve/Future')
          AND (? IS NULL OR p.player_id <> ?)
        """,
        (int(team_id), exclude_player_id, exclude_player_id),
    ).fetchall()
    season = int(season or default_league_year(con))
    reads, created = pro_player_fog.evaluations_for_team(
        con,
        game_id=pro_player_fog.active_game_id(con),
        season=season,
        evaluator_team_id=team_id,
        player_ids=[int(row["player_id"]) for row in rows],
        create_missing=True,
    )
    if created:
        con.commit()
    scores: list[int] = []
    young_upside = 0
    for row in rows:
        if position_group_for(str(row["position"])) != group:
            continue
        read = reads.get(int(row["player_id"]))
        overall = int(read.get("overall") if read else row["overall"] or 50)
        potential = int(read.get("potential") if read else row["potential"] or overall)
        age = int(row["age"] or 28)
        scores.append(overall)
        if age <= 26 and potential >= overall + 6 and potential >= 75:
            young_upside += 1
    scores.sort(reverse=True)
    starters = STARTER_SLOTS_BY_GROUP.get(group, 1)
    ideal = ROOM_IDEAL_BY_GROUP.get(group, starters + 2)
    return {
        "scores": scores,
        "count": len(scores),
        "starter_slots": starters,
        "ideal": ideal,
        "best": scores[0] if scores else 0,
        "starter_floor": scores[starters - 1] if len(scores) >= starters else 0,
        "young_upside": young_upside,
    }


def rb_starter_stack_block_reason(
    player: sqlite3.Row | dict[str, Any],
    *,
    room: dict[str, Any],
    team_need: float,
    offer_aav: int,
    player_score_override: float | None = None,
    player_potential_override: int | None = None,
) -> str | None:
    """Prevent CPU teams from hoarding multiple lead-back expectation signings.

    Depth and committee backs are still fine. This only pushes back when a team
    already has a credible lead back and the incoming RB expects starter money
    or starter usage without being a clear upgrade.
    """
    if normalized_group(player) != "RB":
        return None
    score = float(player_score_override) if player_score_override is not None else float(true_overall(player))
    potential = (
        int(player_potential_override)
        if player_potential_override is not None
        else int(row_value(player, "potential", score) or score)
    )
    tier = normalized_tier(player)
    count = int(room.get("count", 0) or 0)
    best = int(room.get("best", 0) or 0)
    starter_floor = int(room.get("starter_floor", 0) or 0)
    starter_expectation = score >= 72 or offer_aav >= 5_500_000 or (tier in {"Premium", "Starter"} and score >= 70)
    if not starter_expectation:
        return None
    clear_upgrade = score >= best + 4 or (score >= 82 and best < 78) or (potential >= 86 and score >= best + 2)
    if count >= 1 and best >= 74 and not clear_upgrade and team_need < 74:
        return "lead RB already in place; incoming back lacks a clear starter path."
    if count >= 2 and starter_floor >= 68 and score <= best + 2 and team_need < 82:
        return "RB room already has enough starter-level options."
    return None


def team_group_active_spend(
    con: sqlite3.Connection,
    team_id: int,
    group: str,
    *,
    exclude_player_id: int | None = None,
) -> dict[str, int]:
    rows = con.execute(
        """
        SELECT p.player_id, p.position, COALESCE(c.aav, 0) AS aav
        FROM players p
        LEFT JOIN contracts c
          ON c.player_id = p.player_id
         AND c.is_active = 1
        WHERE p.team_id = ?
          AND p.status IN ('Active', 'IR', 'PUP', 'Reserve/Future')
          AND (? IS NULL OR p.player_id <> ?)
        """,
        (int(team_id), exclude_player_id, exclude_player_id),
    ).fetchall()
    total = 0
    premium_count = 0
    starter_money_count = 0
    premium_floor = {
        "QB": 14_000_000,
        "RB": 8_000_000,
        "WR": 13_000_000,
        "TE": 10_000_000,
        "OT": 14_000_000,
        "IOL": 10_000_000,
        "EDGE": 13_000_000,
        "IDL": 13_000_000,
        "LB": 10_000_000,
        "CB": 12_000_000,
        "S": 10_000_000,
        "ST": 3_000_000,
    }.get(group, 10_000_000)
    starter_floor = max(5_000_000, int(premium_floor * 0.62))
    for row in rows:
        if position_group_for(str(row["position"])) != group:
            continue
        aav = int(row["aav"] or 0)
        total += aav
        if aav >= premium_floor:
            premium_count += 1
        if aav >= starter_floor:
            starter_money_count += 1
    return {
        "total": total,
        "premium_count": premium_count,
        "starter_money_count": starter_money_count,
    }


def cpu_offer_notes(notes: Any) -> bool:
    text = str(notes or "").lower()
    return text.startswith("cpu") or "cpu " in text or "post-draft depth" in text


def cpu_final_signing_guardrails(
    con: sqlite3.Connection,
    period: sqlite3.Row | dict[str, Any],
    offer: sqlite3.Row | dict[str, Any],
    market: sqlite3.Row | dict[str, Any],
    *,
    own_retention: bool = False,
) -> tuple[bool, str]:
    """Last line of defense before a CPU FA contract is written.

    Offer creation has its own heuristics, but multiple code paths can create
    offers or sign own players directly. This guard is intentionally repeated
    at signing time using the current roster/cap state so earlier moves in the
    same FA wave cannot make a later signing nonsensical.
    """
    team_id = int(row_value(offer, "team_id", 0) or 0)
    player_id = int(row_value(offer, "player_id", row_value(market, "player_id", 0)) or 0)
    aav = int(row_value(offer, "aav", 0) or 0)
    years = int(row_value(offer, "years", 1) or 1)
    signing_bonus = int(row_value(offer, "signing_bonus", 0) or 0)
    guarantee_pct = int(row_value(offer, "guarantee_pct", 0) or 0)
    notes_text = str(row_value(offer, "notes", "") or "").lower()
    post_draft_depth_offer = "post-draft depth" in notes_text
    group = position_group_for(str(row_value(market, "position_group", row_value(market, "position", ""))))
    actual_overall = true_overall(market)
    score = float(actual_overall) if post_draft_depth_offer else float(row_value(market, "market_score", row_value(market, "overall", 60)) or 60)
    potential = int(row_value(market, "potential", score) or score)
    age = int(row_value(market, "age", 28) or 28)
    team_need = load_team_need_scores(con).get((team_id, group), 0.0)
    room = team_group_room_context(con, team_id, group, exclude_player_id=player_id)
    room_spend = team_group_active_spend(con, team_id, group, exclude_player_id=player_id)
    active_spend = int(room_spend["total"])
    premium_count = int(room_spend["premium_count"])
    starter_money_count = int(room_spend["starter_money_count"])
    starter_slots = int(room["starter_slots"])
    ideal = int(room["ideal"])
    count = int(room["count"])
    best = int(room["best"])
    starter_floor = int(room["starter_floor"])
    target_floor = STARTER_FLOOR_BY_GROUP.get(group, 68)
    starter_hole = count < starter_slots or starter_floor < target_floor - 4
    reserve = max(CPU_FINAL_SIGNING_MIN_BUFFER, cpu_cap_reserve_for_period(period))
    if post_draft_depth_offer:
        reserve = max(
            CPU_FINAL_SIGNING_MIN_BUFFER,
            cpu_post_draft_depth_offer_reserve(
                period,
                group=group,
                aav=aav,
                years=years,
                starter_hole=starter_hole,
                guarantee_pct=guarantee_pct,
            ),
        )

    cap = roster_actions.cap_row(con, team_id)
    cap_space = int(cap["cap_space"] or 0) if cap else 0
    projected_first_year_cost = first_year_cap_estimate(
        aav=aav,
        years=years,
        signing_bonus=signing_bonus,
        structure=str(row_value(offer, "contract_structure", "balanced")),
    )
    if cap_space - projected_first_year_cost < reserve:
        return False, (
            f"CPU signing blocked: cap buffer would fall below {money(reserve)} "
            f"after {money(projected_first_year_cost)} first-year cost."
        )

    quality_cap = cpu_true_quality_aav_cap(market)
    if own_retention and (score >= 82 or potential >= 88):
        quality_cap = int(quality_cap * 1.10)
    if aav > int(quality_cap * 1.08):
        return False, (
            f"CPU signing blocked: {money(aav)} AAV exceeds quality cap "
            f"{money(quality_cap)} for {int(score)} OVR/{potential} POT."
        )
    if age >= POSITION_OLD_AGE.get(group, 31) + 2 and score < 76 and aav > int(quality_cap * 0.96):
        return False, "CPU signing blocked: aging low-70s veteran priced too close to ceiling."
    if (
        group in {"WR", "OT", "EDGE", "CB", "IDL", "TE", "LB", "S", "IOL"}
        and actual_overall <= 73
        and potential < 78
        and aav >= 12_000_000
    ):
        return False, "CPU signing blocked: low-ceiling role player above starter price."
    if (
        group in {"CB", "S", "IOL"}
        and actual_overall <= 73
        and potential <= 78
        and aav >= 10_000_000
    ):
        return False, "CPU signing blocked: low-70s secondary/interior player above starter price."
    if (
        group in {"CB", "S", "IOL", "IDL", "LB"}
        and actual_overall <= 73
        and potential <= 80
        and aav >= 9_750_000
    ):
        return False, "CPU signing blocked: low-70s role player priced above his ceiling."

    room_scores = [float(value) for value in room.get("scores", [])]
    high_upside_candidate = potential >= max(84, actual_overall + 7) and age <= 26
    if post_draft_depth_offer and group != "QB":
        recent_group_signings = post_draft_depth_signing_counts_by_group(
            con,
            league_year=int(row_value(period, "league_year", default_league_year(con)) or default_league_year(con)),
        ).get((team_id, group), 0)
        meaningful_depth = (
            actual_overall >= max(58, min(target_floor - 6, starter_floor - 2))
            or high_upside_candidate
        )
        if count >= ideal and not starter_hole and not meaningful_depth:
            return False, "CPU signing blocked: post-draft depth target would not improve a full room."
        if count >= ideal and recent_group_signings >= 1 and actual_overall <= starter_floor and team_need < 82:
            return False, "CPU signing blocked: post-draft room already added depth at this position."
        if count >= ideal + 1 and actual_overall < max(starter_floor, target_floor - 4) and not starter_hole:
            return False, "CPU signing blocked: post-draft signing would overfill a full room with replacement depth."
    premium_room_groups = {"WR", "EDGE", "CB", "OT", "IDL", "TE", "IOL", "S", "LB"}
    if group in premium_room_groups and aav >= 12_000_000 and not high_upside_candidate:
        comparable = sum(1 for value in room_scores if value >= actual_overall - 2)
        much_better = sum(1 for value in room_scores if value >= actual_overall + 5)
        if actual_overall < 80 and comparable >= starter_slots and team_need < 82:
            return False, "CPU signing blocked: expensive addition lacks a starter path in a playable room."
        if group == "WR" and actual_overall < 80 and comparable >= 2 and aav >= 14_000_000:
            return False, "CPU signing blocked: expensive WR3/WR4 target in an already playable room."
        if actual_overall < 80 and much_better >= 1 and count >= starter_slots and aav >= 14_000_000 and team_need < 86:
            return False, "CPU signing blocked: expensive non-upgrade behind a clearly better starter."

    if group == "RB":
        rb_block = rb_starter_stack_block_reason(
            market,
            room=room,
            team_need=team_need,
            offer_aav=aav,
        )
        if rb_block:
            return False, f"CPU signing blocked: {rb_block}"

    if group == "QB":
        qb_limits = qb_room_offer_limits(
            con,
            market,
            team_qb_scores=[float(value) for value in room["scores"]],
            team_need=team_need,
            roster_qb_count=count,
        )
        if not qb_limits["allowed"]:
            return False, "CPU signing blocked: no realistic QB starter path."
        backup_cap = qb_limits["backup_cap"]
        incumbent = float(best)
        if (
            int(room.get("young_upside", 0) or 0) > 0
            and incumbent >= 70
            and age >= 27
            and actual_overall <= incumbent + 3
            and aav >= 12_000_000
            and team_need < 84
        ):
            return False, "CPU signing blocked: veteran QB would block a young high-upside starter without being a clear upgrade."
        if backup_cap and aav > int(backup_cap):
            return False, f"CPU signing blocked: backup QB cap is {money(int(backup_cap))}."
        if incumbent >= 84 and score <= incumbent - 4 and aav > 8_500_000:
            return False, "CPU signing blocked: starter-priced QB behind established franchise QB."
        if count >= 1 and actual_overall <= incumbent + 2 and aav >= 10_000_000 and team_need < 88:
            return False, "CPU signing blocked: parallel bridge QB money without a clear upgrade."
        if count >= 1 and actual_overall <= incumbent + 1 and years > 1 and not own_retention and team_need < 86:
            return False, "CPU signing blocked: multi-year QB deal lacks a clear path over the current starter."
        if incumbent >= 82 and actual_overall <= incumbent - 3 and aav >= 8_500_000:
            return False, "CPU signing blocked: actual QB grade is too far behind the established starter."
        if actual_overall <= 72 and aav > 10_000_000:
            if not (count == 0 and team_need >= 84 and potential >= 82):
                return False, "CPU signing blocked: low-70s QB above bridge-starter price."
        if count >= 1 and actual_overall <= 72 and aav >= 8_500_000:
            return False, "CPU signing blocked: backup QB price exceeds the room need."
        if count >= 2 and team_need < 75 and aav > 8_000_000:
            return False, "CPU signing blocked: expensive third QB without a major QB need."
        if actual_overall <= 76 and potential < 82 and aav > 16_500_000:
            return False, "CPU signing blocked: bridge QB above mid-tier starter price."
        if actual_overall < 78 and potential < 84 and years > 2:
            return False, "CPU signing blocked: bridge QB should not receive a long-term starter deal."
        if actual_overall <= 76 and potential < 82 and years > 1 and not own_retention:
            return False, "CPU signing blocked: low-upside bridge QB should be a short-term deal."
        return True, "ok"

    clear_upgrade = score >= best + 3 or (score >= starter_floor + 5 and count < ideal)
    high_upside = potential >= max(82, int(score) + 7) and age <= 26
    elite_target = cpu_elite_free_agent(market)
    if group == "TE" and premium_count >= 1 and aav >= 10_000_000:
        major_upgrade = actual_overall >= best + 4 or (potential >= best + 7 and age <= 25)
        if not (team_need >= 82 and major_upgrade):
            return False, "CPU signing blocked: already has TE1 money or young TE upside in the room."
    if (
        group in {"OT", "TE", "S", "RB", "IDL", "IOL", "LB"}
        and starter_money_count >= starter_slots
        and aav >= 9_000_000
        and actual_overall <= starter_floor + 2
        and not (clear_upgrade or elite_target or team_need >= 82)
    ):
        return False, "CPU signing blocked: paid depth would crowd an already covered position group."
    if count >= ideal and score <= starter_floor + 2 and aav >= 8_000_000 and team_need < 66:
        return False, "CPU signing blocked: crowded room and no clear role upgrade."
    if count >= starter_slots and score <= starter_floor + 1 and aav >= 10_000_000 and team_need < 58:
        return False, "CPU signing blocked: starter-money offer without starter path."
    if premium_count >= starter_slots and aav >= 12_000_000 and not (clear_upgrade or elite_target or team_need >= 74):
        return False, "CPU signing blocked: already has premium money committed at position."
    if starter_money_count >= ideal and aav >= 7_000_000 and not (high_upside and team_need >= 45):
        return False, "CPU signing blocked: position room already has enough paid depth."
    if not expensive_room_offer_allowed(
        group=group,
        team_need=team_need,
        active_spend=active_spend,
        pending_spend=0,
        offer_aav=aav,
        player_score=score,
        player_potential=potential,
        elite_target=elite_target,
    ):
        return False, "CPU signing blocked: projected room spend exceeds need/value guardrail."
    return True, "ok"


def strategic_cap_casualty_grade(
    con: sqlite3.Connection,
    candidate: dict[str, Any],
    *,
    team_id: int,
    cap_space: int | None = None,
) -> tuple[bool, str]:
    if current_year_fa_contract(
        con,
        contract_id=int(candidate.get("contract_id") or 0),
        player_id=int(candidate.get("player_id") or 0),
        team_id=team_id,
        league_year=int(candidate.get("season") or candidate.get("league_year") or default_league_year(con)),
    ):
        return False, "fresh FA signing protected"
    group = position_group_for(str(candidate.get("position") or ""))
    if group in {"QB", "K", "P", "LS", "ST"}:
        return False, "protected position"
    overall = int(candidate.get("overall") or candidate.get("market_score") or 60)
    potential = int(candidate.get("potential") or overall)
    age = int(candidate.get("age") or 28)
    savings = int(candidate.get("net_savings_pre_june1") or 0)
    cap_hit = int(candidate.get("cap_hit") or 0)
    cap_space = int(cap_space or 0)
    if cap_space < 0:
        pressure = "over_cap"
        savings_floor = 4_000_000
        threshold_delta = -1_000_000
    elif cap_space < 5_000_000:
        pressure = "tight_cap"
        savings_floor = 4_500_000
        threshold_delta = -500_000
    elif cap_space < 12_000_000:
        pressure = "thin_cap"
        savings_floor = 5_000_000
        threshold_delta = 0
    elif cap_space < 25_000_000:
        pressure = "comfortable_cap"
        savings_floor = 6_500_000
        threshold_delta = 1_500_000
    else:
        pressure = "flush_cap"
        savings_floor = 8_000_000
        threshold_delta = 3_000_000

    if savings < savings_floor or cap_hit < 6_000_000:
        return False, "not enough savings"
    if overall >= 76:
        return False, "too good for strategic low-tier cut"
    if age <= 26 and potential >= 80 and savings < 14_000_000:
        return False, "young upside protected"
    if age <= 25 and potential >= 78 and savings < 12_000_000:
        return False, "developing depth protected"

    room = team_group_room_context(
        con,
        team_id,
        group,
        exclude_player_id=int(candidate["player_id"]),
    )
    count = int(room["count"])
    starters = int(room["starter_slots"])
    ideal = int(room["ideal"])
    best = int(room["best"])
    starter_floor = int(room["starter_floor"])
    buried = count >= starters and best >= overall + 2
    deep_room = count >= max(starters + 1, ideal - 1)
    comparable_cover = count >= starters and starter_floor >= overall - 4
    pressure_note = {
        "over_cap": "cap emergency",
        "tight_cap": "tight cap",
        "thin_cap": "thin cap",
        "comfortable_cap": "comfortable cap",
        "flush_cap": "flush cap",
    }[pressure]

    if overall <= 66 and savings >= 5_000_000 + threshold_delta and (comparable_cover or deep_room):
        return True, f"low-60s/backup money with survivable room, {pressure_note}"
    if overall <= 69 and savings >= 7_000_000 + threshold_delta and (comparable_cover or deep_room):
        return True, f"sub-70 expensive depth, {pressure_note}"
    if overall <= 72 and savings >= 8_000_000 + threshold_delta and (buried or deep_room):
        return True, f"low-70s overpaid and covered, {pressure_note}"
    if overall <= 75 and savings >= 10_000_000 + threshold_delta and (buried or (deep_room and age >= 28)):
        return True, f"mid-70s buried expensive veteran, {pressure_note}"
    if age >= 30 and overall <= 74 and savings >= 7_500_000 + threshold_delta and comparable_cover:
        return True, f"aging replaceable veteran, {pressure_note}"
    return False, "room or value not strong enough"


def team_list_contains(raw: Any, team: str | None) -> bool:
    if not raw or not team:
        return False
    values = {item.strip().upper() for item in str(raw).replace(";", ",").split(",") if item.strip()}
    return team.upper() in values


def playing_time_offer_delta(
    offer: sqlite3.Row,
    market: sqlite3.Row,
    competition: dict[tuple[int, str], list[float]] | None,
) -> float:
    if not competition:
        return 0.0
    role_priority = int(row_value(market, "role_priority", 10))
    if role_priority <= 4:
        return 0.0
    group = position_group_for(str(row_value(market, "position_group", row_value(market, "position", ""))))
    team_id = int(row_value(offer, "team_id", 0) or 0)
    incoming_score = float(row_value(market, "market_score", 60) or 60)
    scores = competition.get((team_id, group), [])
    slots = STARTER_SLOTS_BY_GROUP.get(group, 2)
    clearly_ahead = sum(1 for score in scores if score >= incoming_score + 3)
    comparable_or_better = sum(1 for score in scores if score >= incoming_score - 3)
    open_path = max(0, slots - clearly_ahead) / max(1, slots)
    crowding = max(0, comparable_or_better - slots) / max(1, slots + 2)
    return ((open_path - 0.45) * role_priority * 1.25) - (crowding * role_priority * 0.85)


def offer_score(
    offer: sqlite3.Row,
    market: sqlite3.Row,
    team_cap_space: int | None,
    *,
    team_context: dict[int, dict[str, float]] | None = None,
    competition: dict[tuple[int, str], list[float]] | None = None,
) -> float:
    asking = max(1, int(market["asking_aav"]))
    minimum = max(1, int(market["minimum_aav"]))
    aav = int(offer["aav"])
    years = int(offer["years"])
    signing_bonus = int(offer["signing_bonus"] or 0)
    guarantee_pct = int(offer["guarantee_pct"] or 0)
    structure = normalized_contract_structure(row_value(offer, "contract_structure", "balanced"))

    money_priority = int(row_value(market, "money_priority", 10))
    security_priority = int(row_value(market, "security_priority", 10))
    contender_priority = int(row_value(market, "contender_priority", 10))
    role_priority = int(row_value(market, "role_priority", 10))
    loyalty_priority = int(row_value(market, "loyalty_priority", 10))
    location_priority = int(row_value(market, "location_priority", 8))
    minimum_over_ask_pct = float(row_value(market, "minimum_over_ask_pct", 0) or 0)

    effective_asking = max(1, int(asking * (1.0 + minimum_over_ask_pct)))
    money_weight = 50 + (money_priority - 10) * 2.2
    minimum_weight = 13 + money_priority * 0.55
    bonus_weight = 5 + security_priority * 0.65
    guarantee_weight = 5 + security_priority * 0.72

    score = 0.0
    score += min(1.35, aav / effective_asking) * money_weight
    score += min(1.40, aav / minimum) * minimum_weight
    score += min(18, signing_bonus / max(1, aav) * bonus_weight)
    score += min(16, guarantee_pct / 10 * (guarantee_weight / 10))

    preferred_years = int(row_value(market, "contract_year_preference", row_value(market, "preferred_years", 1)) or 1)
    year_gap = abs(years - preferred_years)
    score -= year_gap * (1.2 + security_priority * 0.22)
    if years >= preferred_years and security_priority >= 14:
        score += min(5, (years - preferred_years + 1) * 0.9)
    if structure == "backloaded":
        score -= 1.5 if security_priority >= 13 else 0.5
    elif structure == "frontloaded":
        score += 1.2 if security_priority >= 10 else 0.4
    elif structure == "bonus-heavy":
        score += min(3.0, security_priority * 0.18)

    offer_team = str(row_value(offer, "team", "") or "").upper()
    previous_team = str(row_value(market, "previous_team", "") or "").upper()
    if previous_team and offer_team == previous_team:
        score += max(-4, (loyalty_priority - 10) * 0.55) + min(5, loyalty_priority * 0.18)
    if team_list_contains(row_value(market, "preferred_teams"), offer_team):
        score += location_priority * 0.45
    if team_list_contains(row_value(market, "hometown_teams"), offer_team):
        score += location_priority * 0.55

    if team_context:
        context = team_context.get(int(row_value(offer, "team_id", 0) or 0), {})
        win_pct = float(context.get("win_pct", 0.5))
        point_diff = float(context.get("point_diff", 0))
        contender_delta = ((win_pct - 0.5) * 4.0 + clamp(point_diff / 250.0, -0.35, 0.35)) * contender_priority
        if contender_delta > 0:
            contender_delta *= 1.0 + float(row_value(market, "contender_discount_pct", 0) or 0) * 2.0
        score += contender_delta

    score += playing_time_offer_delta(offer, market, competition)

    if team_cap_space is not None and team_cap_space < aav:
        score -= 30
    return round(score, 2)


def should_accept_best(period: sqlite3.Row, market: sqlite3.Row, best_offer: sqlite3.Row, score: float) -> bool:
    asking = int(market["asking_aav"])
    minimum = int(market["minimum_aav"])
    aav = int(best_offer["aav"])
    tier = str(market["market_tier"])
    patience = int(market["patience"] or 8)
    strategy = str(row_value(market, "post_draft_strategy", "normal") or "normal")
    holdout_until = str(row_value(market, "holdout_until", "") or "")
    current_date = str(row_value(period, "current_date", "") or "")
    injury_leverage_offer = "injury" in str(row_value(best_offer, "notes", "") or "").lower()

    if aav < minimum:
        return False
    if strategy == "injury_wait" and holdout_until and current_date and current_date < holdout_until:
        if injury_leverage_offer and aav >= int(asking * 0.96) and score >= 67:
            return True
        return aav >= int(asking * 1.14) and score >= 76
    if aav >= int(asking * 1.08):
        return True
    if period["current_stage"] == "day_one_hourly":
        hour = int(period["current_hour"])
        if tier in {"Premium", "Starter"} and hour < 17 and aav < asking:
            return False
        if patience >= 12 and hour < int(period["first_day_end_hour"]):
            return aav >= asking
        return hour >= 16 and score >= 70
    return score >= 66 or aav >= int(asking * 0.92)


def sign_offer(con: sqlite3.Connection, period: sqlite3.Row, offer: sqlite3.Row, market: sqlite3.Row) -> int:
    player = con.execute("SELECT * FROM players WHERE player_id = ?", (offer["player_id"],)).fetchone()
    team = con.execute("SELECT * FROM teams WHERE team_id = ?", (offer["team_id"],)).fetchone()
    if not player or not team:
        raise ValueError("Offer references a missing player or team.")
    if player["team_id"] is not None or player["status"] != "Free Agent":
        raise ValueError(f"{player['first_name']} {player['last_name']} is no longer a free agent.")
    if cpu_offer_notes(offer["notes"]):
        allowed, reason = cpu_final_signing_guardrails(con, period, offer, market)
        if not allowed:
            raise ValueError(reason)

    season = int(period["league_year"])
    signed_date = str(period["current_date"])
    before = roster_actions.cap_row(con, int(team["team_id"]))
    offer_structure = normalized_contract_structure(row_value(offer, "contract_structure", "balanced"))
    projected_first_year_cost = first_year_cap_estimate(
        aav=int(offer["aav"]),
        years=int(offer["years"] or 1),
        signing_bonus=int(offer["signing_bonus"] or 0),
        structure=offer_structure,
    )
    if int(before["cap_space"] or 0) < projected_first_year_cost:
        raise ValueError(f"{team['abbreviation']} does not have enough practical cap room for this offer.")
    if str(offer["notes"] or "").startswith("CPU"):
        reserve = cpu_cap_reserve_for_period(period)
        if "post-draft depth" in str(offer["notes"] or "").lower():
            group = position_group_for(str(row_value(market, "position_group", row_value(market, "position", ""))))
            room = team_group_room_context(con, int(team["team_id"]), group, exclude_player_id=int(offer["player_id"]))
            starter_floor = int(room["starter_floor"] or 0)
            starter_slots = int(room["starter_slots"] or 1)
            count = int(room["count"] or 0)
            target_floor = STARTER_FLOOR_BY_GROUP.get(group, 68)
            starter_hole = count < starter_slots or starter_floor < target_floor - 4
            reserve = max(
                CPU_FINAL_SIGNING_MIN_BUFFER,
                cpu_post_draft_depth_offer_reserve(
                    period,
                    group=group,
                    aav=int(offer["aav"] or 0),
                    years=int(offer["years"] or 1),
                    starter_hole=starter_hole,
                    guarantee_pct=int(offer["guarantee_pct"] or 0),
                ),
            )
        if int(before["cap_space"] or 0) - reserve < projected_first_year_cost:
            raise ValueError(f"{team['abbreviation']} does not have enough CPU reserve cap room for this offer.")

    cur = con.execute(
        """
        INSERT INTO contracts (
            player_id, team_id, signed_date, start_year, end_year, total_value,
            total_years, aav, signing_bonus, roster_bonus, workout_bonus,
            is_guaranteed, guarantee_pct, dead_cap_current, dead_cap_next,
            contract_type, salary_structure, is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, 0, 0, 'FreeAgent', ?, 1)
        """,
        (
            offer["player_id"],
            offer["team_id"],
            signed_date,
            season,
            season + int(offer["years"]) - 1,
            int(offer["total_value"]),
            int(offer["years"]),
            int(offer["aav"]),
            int(offer["signing_bonus"]),
            1 if int(offer["guarantee_pct"] or 0) >= 50 else 0,
            int(offer["guarantee_pct"] or 0),
            offer_structure,
        ),
    )
    contract_id = int(cur.lastrowid)
    deactivate_prior_player_contracts(
        con,
        int(offer["player_id"]),
        new_contract_id=contract_id,
        new_start_year=season,
    )
    con.execute(
        "UPDATE players SET team_id = ?, status = 'Active' WHERE player_id = ?",
        (offer["team_id"], offer["player_id"]),
    )
    jersey_numbers.assign_player_number(
        con,
        int(offer["player_id"]),
        team_id=int(offer["team_id"]),
        source="free_agency_signing",
    )
    cpu_depth_chart.mark_depth_chart_stale(
        con,
        str(team["abbreviation"]),
        reason="Free-agent signing changed roster composition.",
    )
    roster_actions.ensure_player_normalized_ratings(
        con,
        int(offer["player_id"]),
        source="free_agency_processor",
        schema_ready=True,
    )
    rebuild_contract_year(con, contract_id)
    sync_team_cap_space(con)
    after = roster_actions.cap_row(con, int(team["team_id"]))
    cap_delta = int(after["total_committed"] or 0) - int(before["total_committed"] or 0)
    transaction_id, _created = insert_transaction(
        con,
        transaction_date=signed_date,
        season=season,
        phase=PHASE,
        transaction_type="Signing",
        team_id=int(team["team_id"]),
        player_id=int(player["player_id"]),
        contract_id=contract_id,
        to_team_id=int(team["team_id"]),
        old_status="Free Agent",
        new_status="Active",
        cap_delta_current=cap_delta,
        cash_delta=int(offer["aav"]) + int(offer["signing_bonus"]),
        description=(
            f"{team['abbreviation']} signed {player['first_name']} {player['last_name']} "
            f"from free agency for {offer['years']} year(s), {money(offer['aav'])} AAV."
        ),
        source=SOURCE,
        external_ref=f"fa:{season}:offer:{offer['offer_id']}",
    )
    con.execute(
        """
        INSERT INTO transaction_assets (
            transaction_id, asset_type, player_id, contract_id, to_team_id,
            amount, season, asset_description
        )
        VALUES (?, 'PlayerContract', ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_id,
            offer["player_id"],
            contract_id,
            offer["team_id"],
            int(offer["aav"]),
            season,
            "Free-agency processor signing.",
        ),
    )
    con.execute(
        """
        UPDATE free_agency_player_markets
        SET status = 'signed',
            signed_team_id = ?,
            signed_offer_id = ?,
            decision_notes = ?,
            updated_at = datetime('now')
        WHERE league_year = ? AND player_id = ?
        """,
        (
            offer["team_id"],
            offer["offer_id"],
            f"Accepted {team['abbreviation']} offer at {money(offer['aav'])} AAV.",
            period["league_year"],
            offer["player_id"],
        ),
    )
    con.execute(
        """
        UPDATE free_agency_offers
        SET status = CASE WHEN offer_id = ? THEN 'accepted' ELSE 'rejected' END,
            decided_date = ?,
            decided_hour = ?,
            updated_at = datetime('now')
        WHERE league_year = ? AND player_id = ? AND status = 'pending'
        """,
        (
            offer["offer_id"],
            period["current_date"],
            period["current_hour"] if period["current_stage"] == "day_one_hourly" else None,
            period["league_year"],
            offer["player_id"],
        ),
    )
    log_event(
        con,
        league_year=int(period["league_year"]),
        event_date=str(period["current_date"]),
        event_hour=int(period["current_hour"]) if period["current_stage"] == "day_one_hourly" else None,
        event_type="player_signed",
        team_id=int(team["team_id"]),
        player_id=int(player["player_id"]),
        offer_id=int(offer["offer_id"]),
        message=(
            f"{team['abbreviation']} signed {player['first_name']} {player['last_name']} "
            f"for {offer['years']} year(s), {money(offer['aav'])} AAV."
        ),
    )
    return transaction_id


def resolve_pending_offers(
    con: sqlite3.Connection,
    period: sqlite3.Row,
    limit: int | None = None,
    *,
    write_cap_snapshot: bool = True,
) -> int:
    current_date = str(row_value(period, "current_date", "") or "")
    if current_date:
        expire_stale_pending_offers(
            con,
            league_year=int(period["league_year"]),
            current_date=current_date,
            stale_offer_days=stale_offer_days_for_period(con, period),
            note="Expired before resolution because the market moved on.",
        )
    prior_season = int(period["league_year"]) - 1
    team_context = load_team_context(con, prior_season)
    competition = load_playing_time_competition(con, prior_season)
    player_rows = con.execute(
        """
        SELECT DISTINCT m.*
        FROM free_agency_board_view m
        JOIN free_agency_offers o
          ON o.league_year = m.league_year
         AND o.player_id = m.player_id
         AND o.status = 'pending'
        WHERE m.league_year = ?
          AND m.market_status = 'available'
        ORDER BY m.market_heat DESC, m.asking_aav DESC
        """,
        (period["league_year"],),
    ).fetchall()
    signed = 0
    for market in player_rows:
        offers = con.execute(
            """
            SELECT o.*, t.abbreviation AS team, cap.cap_space
            FROM free_agency_offers o
            JOIN teams t ON t.team_id = o.team_id
            LEFT JOIN team_cap_view cap ON cap.team_id = o.team_id
            WHERE o.league_year = ?
              AND o.player_id = ?
              AND o.status = 'pending'
            """,
            (period["league_year"], market["player_id"]),
        ).fetchall()
        if not offers:
            continue
        scored = [
            (
                offer_score(
                    offer,
                    market,
                    int(offer["cap_space"] or 0),
                    team_context=team_context,
                    competition=competition,
                ),
                offer,
            )
            for offer in offers
        ]
        scored.sort(key=lambda item: (item[0], int(item[1]["aav"])), reverse=True)
        best_score, best_offer = scored[0]
        con.execute(
            "UPDATE free_agency_offers SET decision_score = ? WHERE offer_id = ?",
            (best_score, best_offer["offer_id"]),
        )
        if should_accept_best(period, market, best_offer, best_score):
            try:
                sign_offer(con, period, best_offer, market)
            except ValueError as exc:
                con.execute(
                    """
                    UPDATE free_agency_offers
                    SET status = 'rejected',
                        decided_date = ?,
                        decided_hour = ?,
                        notes = COALESCE(notes || ' | ', '') || ?,
                        updated_at = datetime('now')
                    WHERE offer_id = ?
                    """,
                    (
                        period["current_date"],
                        period["current_hour"] if period["current_stage"] == "day_one_hourly" else None,
                        str(exc),
                        best_offer["offer_id"],
                    ),
                )
                continue
            signed += 1
            if limit is not None and signed >= limit:
                break
    if signed and write_cap_snapshot:
        snapshot_cap_ledger(
            con,
            label=f"free_agency_{period['league_year']}_{period['current_date']}",
            phase=PHASE,
            source=SOURCE,
            replace=True,
        )
    return signed


def cpu_retain_own_free_agents(
    con: sqlite3.Connection,
    period: sqlite3.Row,
    *,
    user_team: str | None,
    per_team: int,
    seed: int | None = None,
    write_cap_snapshot: bool = True,
) -> int:
    if per_team <= 0:
        return 0
    rng = random.Random(seed or f"cpu-retain-fa:{period['league_year']}:{period['current_date']}")
    rows = con.execute(
        """
        SELECT b.*, t.team_id AS previous_team_id
        FROM free_agency_board_view b
        JOIN teams t ON t.abbreviation = b.previous_team
        WHERE b.league_year = ?
          AND b.market_status = 'available'
          AND b.previous_team IS NOT NULL
          AND (? IS NULL OR b.previous_team <> ?)
        ORDER BY b.previous_team, b.market_heat DESC, b.market_score DESC, b.asking_aav DESC
        """,
        (period["league_year"], user_team, user_team),
    ).fetchall()
    kept_by_team: dict[str, int] = {}
    team_spend: dict[int, int] = {}
    contract_ids: list[int] = []
    signed = 0
    total_limit = max(0, per_team * 32)
    event_date, event_hour = event_time(period)
    for player in rows:
        if signed >= total_limit:
            break
        previous_team = str(player["previous_team"])
        if kept_by_team.get(previous_team, 0) >= per_team:
            continue
        if rng.random() > cpu_re_sign_probability(player):
            continue
        cap = con.execute(
            """
            SELECT COALESCE(cap.cap_space, t.salary_cap) AS cap_space
            FROM teams t
            LEFT JOIN team_cap_view cap ON cap.team_id = t.team_id
            WHERE t.team_id = ?
            """,
            (int(player["previous_team_id"]),),
        ).fetchone()
        low, high = cpu_aav_bounds(player)
        aav = round_to(rng.randint(low, high))
        aav = preference_adjusted_aav(player, aav, rng)
        if cap and int(cap["cap_space"] or 0) - team_spend.get(int(player["previous_team_id"]), 0) < aav:
            continue
        years = preferred_years_for_offer(
            player,
            rng,
            max_years=cpu_offer_year_cap(player, own_retention=True),
        )
        guarantee = guarantee_for_preference(player, int(player["guarantee_pct"] or 0), rng)
        bonus = cpu_signing_bonus_for_offer(
            player,
            aav=aav,
            years=years,
            guarantee_pct=guarantee,
            rng=rng,
            own_retention=True,
        )
        structure = cpu_contract_structure_for_offer(
            player,
            team_cap_space=int(cap["cap_space"] or 0) if cap else None,
            aav=aav,
            years=years,
            guarantee_pct=guarantee,
            rng=rng,
            own_retention=True,
        )
        synthetic_offer = {
            "team_id": int(player["previous_team_id"]),
            "player_id": int(player["player_id"]),
            "years": years,
            "aav": aav,
            "signing_bonus": bonus,
            "contract_structure": structure,
            "notes": "CPU own-player retention offer",
        }
        allowed, reason = cpu_final_signing_guardrails(
            con,
            period,
            synthetic_offer,
            player,
            own_retention=True,
        )
        if not allowed:
            log_event(
                con,
                league_year=int(period["league_year"]),
                event_date=event_date,
                event_hour=event_hour,
                event_type="cpu_re_signing_skipped",
                team_id=int(player["previous_team_id"]),
                player_id=int(player["player_id"]),
                message=f"{previous_team} passed on retaining {player['player_name']}: {reason}",
            )
            continue
        offer_id = submit_offer(
            con,
            league_year=int(period["league_year"]),
            team_id=int(player["previous_team_id"]),
            player_id=int(player["player_id"]),
            years=years,
            aav=aav,
            signing_bonus=bonus,
            guarantee_pct=guarantee,
            submitted_date=event_date,
            submitted_hour=event_hour,
            contract_structure=structure,
            notes="CPU own-player retention offer",
        )
        contract_id = con.execute(
            """
            INSERT INTO contracts (
                player_id, team_id, signed_date, start_year, end_year, total_value,
                total_years, aav, signing_bonus, roster_bonus, workout_bonus,
                is_guaranteed, guarantee_pct, dead_cap_current, dead_cap_next,
                contract_type, salary_structure, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, 0, 0, 'Standard', ?, 1)
            """,
            (
                int(player["player_id"]),
                int(player["previous_team_id"]),
                event_date,
                int(period["league_year"]),
                int(period["league_year"]) + years - 1,
                aav * years,
                years,
                aav,
                bonus,
                1 if guarantee >= 50 else 0,
                guarantee,
                structure,
            ),
        ).lastrowid
        deactivate_prior_player_contracts(
            con,
            int(player["player_id"]),
            new_contract_id=int(contract_id),
            new_start_year=int(period["league_year"]),
        )
        contract_ids.append(int(contract_id))
        con.execute(
            "UPDATE players SET team_id = ?, status = 'Active' WHERE player_id = ?",
            (int(player["previous_team_id"]), int(player["player_id"])),
        )
        jersey_numbers.assign_player_number(
            con,
            int(player["player_id"]),
            team_id=int(player["previous_team_id"]),
            source="free_agency_retention",
        )
        roster_actions.ensure_player_normalized_ratings(
            con,
            int(player["player_id"]),
            source="free_agency_processor",
            schema_ready=True,
        )
        transaction_id, _created = insert_transaction(
            con,
            transaction_date=event_date,
            season=int(period["league_year"]),
            phase=PHASE,
            transaction_type="Signing",
            team_id=int(player["previous_team_id"]),
            player_id=int(player["player_id"]),
            contract_id=int(contract_id),
            to_team_id=int(player["previous_team_id"]),
            old_status="Free Agent",
            new_status="Active",
            cap_delta_current=aav,
            cash_delta=aav + bonus,
            description=(
                f"{previous_team} re-signed {player['player_name']} from free agency "
                f"for {years} year(s), {money(aav)} AAV."
            ),
            source=SOURCE,
            external_ref=f"fa:{period['league_year']}:own_retention:{offer_id}",
        )
        con.execute(
            """
            INSERT INTO transaction_assets (
                transaction_id, asset_type, player_id, contract_id, to_team_id,
                amount, season, asset_description
            )
            VALUES (?, 'PlayerContract', ?, ?, ?, ?, ?, ?)
            """,
            (
                transaction_id,
                int(player["player_id"]),
                int(contract_id),
                int(player["previous_team_id"]),
                aav,
                int(period["league_year"]),
                "CPU own-player free-agency retention signing.",
            ),
        )
        con.execute(
            """
            UPDATE free_agency_player_markets
            SET status = 'signed',
                signed_team_id = ?,
                signed_offer_id = ?,
                decision_notes = ?,
                updated_at = datetime('now')
            WHERE league_year = ? AND player_id = ?
            """,
            (
                int(player["previous_team_id"]),
                offer_id,
                f"Re-signed by {previous_team} at {money(aav)} AAV.",
                int(period["league_year"]),
                int(player["player_id"]),
            ),
        )
        con.execute(
            """
            UPDATE free_agency_offers
            SET status = CASE WHEN offer_id = ? THEN 'accepted' ELSE 'rejected' END,
                decided_date = ?,
                decided_hour = ?,
                updated_at = datetime('now')
            WHERE league_year = ? AND player_id = ? AND status = 'pending'
            """,
            (
                offer_id,
                event_date,
                event_hour,
                int(period["league_year"]),
                int(player["player_id"]),
            ),
        )
        team_spend[int(player["previous_team_id"])] = team_spend.get(int(player["previous_team_id"]), 0) + aav
        kept_by_team[previous_team] = kept_by_team.get(previous_team, 0) + 1
        signed += 1
        log_event(
            con,
            league_year=int(period["league_year"]),
            event_date=event_date,
            event_hour=event_hour,
            event_type="cpu_re_signing",
            team_id=int(player["previous_team_id"]),
            player_id=int(player["player_id"]),
            offer_id=offer_id,
            message=f"{previous_team} re-signed {player['player_name']} before he fully tested the market.",
        )
    if signed:
        for contract_id in contract_ids:
            rebuild_contract_year(con, contract_id)
        sync_team_cap_space(con)
        if write_cap_snapshot:
            snapshot_cap_ledger(
                con,
                label=f"free_agency_{period['league_year']}_cpu_retention",
                phase=PHASE,
                source=SOURCE,
                replace=True,
            )
    return signed


def create_cpu_competing_offers(
    con: sqlite3.Connection,
    period: sqlite3.Row,
    *,
    player_id: int,
    user_team: str | None,
    count: int,
    seed: int | None = None,
) -> int:
    if count <= 0:
        return 0
    player = con.execute(
        """
        SELECT *
        FROM free_agency_board_view
        WHERE league_year = ?
          AND player_id = ?
          AND market_status = 'available'
        """,
        (period["league_year"], player_id),
    ).fetchone()
    if not player:
        return 0
    rng = random.Random(seed or f"cpu-counter:{period['league_year']}:{player_id}:{period['current_hour']}")
    teams = con.execute(
        """
        SELECT t.team_id, t.abbreviation, cap.cap_space
        FROM teams t
        LEFT JOIN team_cap_view cap ON cap.team_id = t.team_id
        WHERE COALESCE(cap.cap_space, t.salary_cap) > ?
          AND (? IS NULL OR t.abbreviation <> ?)
        ORDER BY t.abbreviation
        """,
        (int(player["minimum_aav"] or 0) + 1_000_000, user_team, user_team),
    ).fetchall()
    rng.shuffle(teams)
    need_scores = load_team_need_scores(con)
    competition = load_playing_time_competition(con, int(period["league_year"]) - 1)
    group_counts = load_team_group_counts(con)
    active_group_spend = load_team_active_group_spend(con)
    player_group = position_group_for(str(row_value(player, "position_group", row_value(player, "position", ""))))
    player_score = float(row_value(player, "market_score", row_value(player, "overall", 60)) or 60)
    player_potential = int(row_value(player, "potential", player_score) or player_score)
    best_aav = int(player["best_aav"] or 0)
    ask = int(player["asking_aav"] or player["minimum_aav"] or 0)
    minimum = int(player["minimum_aav"] or 0)
    cap_reserve = cpu_cap_reserve_for_period(period)
    created = 0
    event_date, event_hour = event_time(period)
    for team in teams:
        duplicate = con.execute(
            """
            SELECT 1
            FROM free_agency_offers
            WHERE league_year = ?
              AND player_id = ?
              AND team_id = ?
              AND status = 'pending'
            """,
            (period["league_year"], player_id, team["team_id"]),
        ).fetchone()
        if duplicate:
            continue
        team_need = need_scores.get((int(team["team_id"]), player_group), 0.0)
        team_id = int(team["team_id"])
        group_key = (team_id, player_group)
        if player_group == "QB":
            qb_fit = qb_backup_reluctance(
                con,
                player,
                team_id=team_id,
                team_qb_scores=competition.get((team_id, "QB"), []),
                team_need=team_need,
                rng=rng,
            )
            if qb_fit is None:
                continue
        roster_group_count = group_counts.get(group_key, 0)
        group_depth_limit = CPU_GROUP_DEPTH_COUNT_LIMITS.get(player_group, ROOM_IDEAL_BY_GROUP.get(player_group, 5) + 1)
        if roster_group_count >= group_depth_limit and team_need < 52:
            continue
        response_pressure = market_pressure_for_need(
            player,
            base_pressure=0.72 if best_aav >= ask else 0.45,
            team_need=team_need,
        )
        effective_best = best_aav if response_pressure > 0 else 0
        low, high = cpu_aav_bounds(
            player,
            best_aav=effective_best,
            response_offer=True,
            market_pressure=response_pressure,
        )
        max_room = max(0, int(team["cap_space"] or 0) - cap_reserve)
        if low > max_room:
            continue
        high = min(high, max_room)
        quality_cap = cpu_true_quality_aav_cap(player)
        if team_need < 18:
            if not cpu_elite_free_agent(player):
                continue
            quality_cap = int(quality_cap * 0.86)
        elif team_need < 32:
            quality_cap = int(quality_cap * 0.86)
        if player_group == "RB" and team_need < 45:
            quality_cap = int(quality_cap * 0.82)
        quality_cap = round_to(max(minimum, quality_cap), 50_000)
        if low > quality_cap:
            continue
        high = min(high, max(low, quality_cap))
        aav = preference_adjusted_aav(player, round_to(rng.randint(low, high)), rng)
        aav = min(aav, max(low, quality_cap))
        if player_group == "RB":
            rb_block = rb_starter_stack_block_reason(
                player,
                room=team_group_room_context(con, team_id, player_group),
                team_need=team_need,
                offer_aav=aav,
            )
            if rb_block:
                continue
        if not expensive_room_offer_allowed(
            group=player_group,
            team_need=team_need,
            active_spend=active_group_spend.get(group_key, 0),
            pending_spend=0,
            offer_aav=aav,
            player_score=player_score,
            player_potential=player_potential,
            elite_target=cpu_elite_free_agent(player),
        ):
            continue
        years = preferred_years_for_offer(player, rng, max_years=5)
        guarantee = guarantee_for_preference(player, int(player["guarantee_pct"] or 0) + 8, rng)
        bonus = cpu_signing_bonus_for_offer(
            player,
            aav=aav,
            years=years,
            guarantee_pct=guarantee,
            rng=rng,
            response_offer=True,
        )
        cap_row = roster_actions.cap_row(con, int(team["team_id"]))
        structure = cpu_contract_structure_for_offer(
            player,
            team_cap_space=int(cap_row["cap_space"] or 0) if cap_row else None,
            aav=aav,
            years=years,
            guarantee_pct=guarantee,
            rng=rng,
        )
        offer_id = submit_offer(
            con,
            league_year=int(period["league_year"]),
            team_id=int(team["team_id"]),
            player_id=player_id,
            years=years,
            aav=aav,
            signing_bonus=bonus,
            guarantee_pct=guarantee,
            submitted_date=event_date,
            submitted_hour=event_hour,
            contract_structure=structure,
            notes="CPU response offer after user bid",
        )
        log_event(
            con,
            league_year=int(period["league_year"]),
            event_date=event_date,
            event_hour=event_hour,
            event_type="cpu_counter_offer",
            team_id=int(team["team_id"]),
            player_id=player_id,
            offer_id=offer_id,
            message=f"{team['abbreviation']} responded with a competing offer for {player['player_name']} at {money(aav)} AAV.",
        )
        created += 1
        if created >= count:
            break
    return created


def cpu_offer_candidates(con: sqlite3.Connection, league_year: int, count: int) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT *
        FROM free_agency_board_view
        WHERE league_year = ?
          AND market_status = 'available'
          AND COALESCE(pending_offers, 0) < 4
        ORDER BY market_heat DESC, asking_aav DESC, player_id
        LIMIT ?
        """,
        (league_year, max(count * 4, count)),
    ).fetchall()


def cpu_offer_slots_for_player(player: sqlite3.Row, rng: random.Random) -> int:
    heat = int(row_value(player, "market_heat", 0) or 0)
    tier = normalized_tier(player)
    pending = int(row_value(player, "pending_offers", 0) or 0)
    elite = cpu_elite_free_agent(player)
    if elite and heat >= 78:
        target = 4 if rng.random() < 0.58 else 3
    elif tier in {"Premium", "Starter"} and heat >= 82:
        target = 3 if rng.random() < 0.72 else 2
    elif tier in {"Premium", "Starter"} or heat >= 72:
        target = 2 if rng.random() < 0.72 else 1
    elif heat >= 60:
        target = 2 if rng.random() < 0.28 else 1
    else:
        target = 1
    return max(0, min(4 - pending, target))


def cpu_release_bad_contracts_for_fa(
    con: sqlite3.Connection,
    period: sqlite3.Row,
    *,
    user_team: str | None = None,
    max_total: int = 10,
) -> int:
    """Let CPU teams create practical cap room before the opening FA wave."""
    day_count = int(period["day_count"] or 1)
    if day_count > 1 and day_count % 7 != 0:
        return 0
    reserve = cpu_cap_reserve_for_period(period)
    league_year = int(period["league_year"])
    teams = con.execute(
        """
        SELECT t.team_id, t.abbreviation, COALESCE(cap.cap_space, t.salary_cap) AS cap_space
        FROM teams t
        LEFT JOIN team_cap_view cap ON cap.team_id = t.team_id
        WHERE (? IS NULL OR t.abbreviation <> ?)
        ORDER BY cap_space ASC
        """,
        (user_team, user_team),
    ).fetchall()
    released = 0
    for team in teams:
        if released >= max_total:
            break
        cap_space = int(team["cap_space"] or 0)
        if cap_space >= reserve:
            continue
        try:
            candidates = contract_negotiations.cap_casualty_candidates(
                con,
                int(team["team_id"]),
                league_year,
                limit=20,
            )
        except Exception:
            continue
        for candidate in candidates:
            group = position_group_for(str(candidate.get("position") or ""))
            overall = int(candidate.get("overall") or candidate.get("market_score") or 60)
            savings = int(candidate.get("net_savings_pre_june1") or 0)
            cap_hit = int(candidate.get("cap_hit") or 0)
            if current_year_fa_contract(
                con,
                contract_id=int(candidate.get("contract_id") or 0),
                player_id=int(candidate.get("player_id") or 0),
                team_id=int(team["team_id"]),
                league_year=league_year,
            ):
                continue
            if savings < 2_000_000:
                continue
            if group == "QB" and overall >= 68:
                continue
            if overall >= 76 and savings < 8_000_000:
                continue
            if overall >= 72 and savings < 4_500_000:
                continue
            if cap_hit < 4_000_000:
                continue
            try:
                contract_negotiations.release_player(
                    con,
                    team=int(team["team_id"]),
                    season=league_year - 1,
                    player_id=int(candidate["player_id"]),
                    post_june1=False,
                    apply=True,
                    force=True,
                    rebuild_all_contracts=False,
                    sync_cap=False,
                    write_cap_snapshot=False,
                    quiet=True,
                )
                cpu_depth_chart.mark_depth_chart_stale(
                    con,
                    str(team["abbreviation"]),
                    reason="Free-agency cap release changed roster composition.",
                )
                released += 1
                log_event(
                    con,
                    league_year=league_year,
                    event_date=str(period["current_date"]),
                    event_hour=int(period["current_hour"]) if period["current_stage"] == "day_one_hourly" else None,
                    event_type="cpu_cap_release",
                    team_id=int(team["team_id"]),
                    player_id=int(candidate["player_id"]),
                    message=(
                        f"{team['abbreviation']} released {candidate['player_name']} "
                        f"to create {money(savings)} in practical FA cap room."
                    ),
                )
            except Exception:
                continue
            break
    if released:
        sync_team_cap_space(con)
    return released


def cpu_release_strategic_cap_casualties_for_fa(
    con: sqlite3.Connection,
    period: sqlite3.Row,
    *,
    user_team: str | None = None,
    max_total: int = 8,
    max_per_team: int = 1,
) -> int:
    """Create NFL-style surprise cuts for bad low-tier contracts, not core stars."""
    day_count = int(period["day_count"] or 1)
    if day_count > 1 and day_count % 14 != 0:
        return 0
    league_year = int(period["league_year"])
    teams = con.execute(
        """
        SELECT t.team_id, t.abbreviation, COALESCE(cap.cap_space, t.salary_cap) AS cap_space
        FROM teams t
        LEFT JOIN team_cap_view cap ON cap.team_id = t.team_id
        WHERE (? IS NULL OR t.abbreviation <> ?)
        ORDER BY cap_space ASC
        """,
        (user_team, user_team),
    ).fetchall()
    released = 0
    released_by_team: dict[int, int] = {}
    for team in teams:
        if released >= max_total:
            break
        team_id = int(team["team_id"])
        if released_by_team.get(team_id, 0) >= max_per_team:
            continue
        try:
            candidates = contract_negotiations.cap_casualty_candidates(
                con,
                team_id,
                league_year,
                limit=80,
            )
        except Exception:
            continue
        candidates.sort(
            key=lambda candidate: (
                int(candidate.get("net_savings_pre_june1") or 0),
                -int(candidate.get("overall") or 99),
                int(candidate.get("cap_hit") or 0),
            ),
            reverse=True,
        )
        for candidate in candidates:
            should_cut, reason = strategic_cap_casualty_grade(
                con,
                candidate,
                team_id=team_id,
                cap_space=int(team["cap_space"] or 0),
            )
            if not should_cut:
                continue
            savings = int(candidate.get("net_savings_pre_june1") or 0)
            try:
                contract_negotiations.release_player(
                    con,
                    team=team_id,
                    season=league_year - 1,
                    player_id=int(candidate["player_id"]),
                    post_june1=False,
                    apply=True,
                    force=True,
                    rebuild_all_contracts=False,
                    sync_cap=False,
                    write_cap_snapshot=False,
                    quiet=True,
                )
            except Exception:
                continue
            cpu_depth_chart.mark_depth_chart_stale(
                con,
                str(team["abbreviation"]),
                reason="Strategic cap release changed roster composition.",
            )
            released += 1
            released_by_team[team_id] = released_by_team.get(team_id, 0) + 1
            log_event(
                con,
                league_year=league_year,
                event_date=str(period["current_date"]),
                event_hour=int(period["current_hour"]) if period["current_stage"] == "day_one_hourly" else None,
                event_type="cpu_strategic_cap_release",
                team_id=team_id,
                player_id=int(candidate["player_id"]),
                message=(
                    f"{team['abbreviation']} released {candidate['player_name']} "
                    f"for {money(savings)} in cap flexibility ({reason})."
                ),
            )
            break
    if released:
        sync_team_cap_space(con)
    return released


def cpu_restructure_core_contracts_for_fa(
    con: sqlite3.Connection,
    period: sqlite3.Row,
    *,
    user_team: str | None = None,
    max_total: int = 12,
) -> int:
    """Create FA room by restructuring players the CPU should expect to keep."""
    day_count = int(period["day_count"] or 1)
    if day_count > 1 and day_count % 7 != 0:
        return 0
    reserve = cpu_cap_reserve_for_period(period)
    league_year = int(period["league_year"])
    teams = con.execute(
        """
        SELECT t.team_id, t.abbreviation, COALESCE(cap.cap_space, t.salary_cap) AS cap_space
        FROM teams t
        LEFT JOIN team_cap_view cap ON cap.team_id = t.team_id
        WHERE (? IS NULL OR t.abbreviation <> ?)
        ORDER BY cap_space ASC
        """,
        (user_team, user_team),
    ).fetchall()
    restructured = 0
    for team in teams:
        if restructured >= max_total:
            break
        cap_space = int(team["cap_space"] or 0)
        if cap_space >= reserve:
            continue
        try:
            candidates = contract_negotiations.restructure_candidates(
                con,
                int(team["team_id"]),
                league_year,
                limit=20,
            )
        except Exception:
            continue
        for candidate in candidates:
            group = position_group_for(str(candidate.get("position") or ""))
            overall = int(candidate.get("overall") or candidate.get("market_score") or 60)
            age = int(candidate.get("age") or 28)
            remaining_years = int(candidate.get("remaining_contract_years") or 1)
            savings = int(candidate.get("estimated_current_savings") or 0)
            if savings < 2_500_000 or remaining_years < 2:
                continue
            core_player = (
                (group == "QB" and overall >= 78 and age <= 35)
                or (group in {"WR", "TE", "OT", "IOL", "EDGE", "IDL", "CB"} and overall >= 80 and age <= 30)
                or (group in {"LB", "S"} and overall >= 81 and age <= 29)
                or (group == "RB" and overall >= 83 and age <= 27)
            )
            if not core_player:
                continue
            try:
                contract_negotiations.restructure_player(
                    con,
                    team=int(team["team_id"]),
                    season=league_year - 1,
                    player_id=int(candidate["player_id"]),
                    amount=int(candidate["suggested_convert"] or 0),
                    apply=True,
                    force=False,
                )
                restructured += 1
                log_event(
                    con,
                    league_year=league_year,
                    event_date=str(period["current_date"]),
                    event_hour=int(period["current_hour"]) if period["current_stage"] == "day_one_hourly" else None,
                    event_type="cpu_restructure",
                    team_id=int(team["team_id"]),
                    player_id=int(candidate["player_id"]),
                    message=(
                        f"{team['abbreviation']} restructured {candidate['player_name']} "
                        f"for about {money(savings)} in current-year FA cap room."
                    ),
                )
            except Exception:
                continue
            break
    if restructured:
        sync_team_cap_space(con)
    return restructured


def quick_restructure_candidates(
    con: sqlite3.Connection,
    team_id: int,
    league_year: int,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
            cy.contract_id,
            cy.player_id,
            cy.base_salary,
            cy.cap_hit,
            c.end_year,
            c.aav,
            c.contract_type,
            c.salary_structure,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.overall,
            p.potential,
            EXISTS (
                SELECT 1
                FROM free_agency_offers o
                WHERE o.league_year = ?
                  AND o.player_id = cy.player_id
                  AND o.team_id = cy.team_id
                  AND o.status = 'accepted'
            ) AS recent_fa_signing
        FROM contract_years cy
        JOIN contracts c ON c.contract_id = cy.contract_id
        JOIN players p ON p.player_id = cy.player_id
        LEFT JOIN roster_status_types rst ON rst.status_code = p.status
        WHERE cy.team_id = ?
          AND cy.season = ?
          AND cy.is_active = 1
          AND c.is_active = 1
          AND p.team_id = cy.team_id
          AND COALESCE(rst.counts_against_top51, 1) = 1
          AND COALESCE(c.end_year, ?) > ?
          AND cy.base_salary > ?
          AND NOT EXISTS (
              SELECT 1
              FROM contract_restructures rr
              WHERE rr.contract_id = cy.contract_id
                AND rr.restructure_season = ?
                AND rr.is_active = 1
          )
        ORDER BY cy.base_salary DESC, cy.cap_hit DESC
        LIMIT ?
        """,
        (
            league_year,
            team_id,
            league_year,
            league_year,
            league_year,
            contract_negotiations.MIN_RESTRUCTURE_BASE_FLOOR + contract_negotiations.MIN_RESTRUCTURE_SAVINGS,
            league_year,
            max(limit * 3, limit),
        ),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if int(row["recent_fa_signing"] or 0):
            continue
        remaining_years = max(1, int(row["end_year"] or league_year) - league_year + 1)
        proration_years = min(5, remaining_years)
        max_convert = max(0, int(row["base_salary"] or 0) - contract_negotiations.MIN_RESTRUCTURE_BASE_FLOOR)
        suggested_convert = min(max_convert, round_to(int(row["base_salary"] or 0) * 0.60, 50_000))
        current_savings = suggested_convert - int(suggested_convert / proration_years) if suggested_convert else 0
        if current_savings < contract_negotiations.MIN_RESTRUCTURE_SAVINGS:
            continue
        item = dict(row)
        item.update(
            {
                "remaining_contract_years": remaining_years,
                "proration_years": proration_years,
                "suggested_convert": suggested_convert,
                "estimated_current_savings": current_savings,
            }
        )
        candidates.append(item)
    candidates.sort(key=lambda item: int(item.get("estimated_current_savings") or 0), reverse=True)
    return candidates[:limit]


def quick_cap_release_candidates(
    con: sqlite3.Connection,
    team_id: int,
    league_year: int,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
            cy.contract_id,
            cy.player_id,
            cy.cap_hit,
            cy.dead_cap_if_cut_pre_june1,
            c.end_year,
            c.aav,
            c.contract_type,
            c.salary_structure,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.overall,
            p.potential,
            EXISTS (
                SELECT 1
                FROM free_agency_offers o
                WHERE o.league_year = ?
                  AND o.player_id = cy.player_id
                  AND o.team_id = cy.team_id
                  AND o.status = 'accepted'
            ) AS recent_fa_signing
        FROM contract_years cy
        JOIN contracts c ON c.contract_id = cy.contract_id
        JOIN players p ON p.player_id = cy.player_id
        LEFT JOIN roster_status_types rst ON rst.status_code = p.status
        WHERE cy.team_id = ?
          AND cy.season = ?
          AND cy.is_active = 1
          AND c.is_active = 1
          AND p.team_id = cy.team_id
          AND COALESCE(rst.counts_against_top51, 1) = 1
          AND cy.cap_hit >= 2500000
        ORDER BY (cy.cap_hit - cy.dead_cap_if_cut_pre_june1) DESC, cy.cap_hit DESC
        LIMIT ?
        """,
        (league_year, team_id, league_year, max(limit * 3, limit)),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        savings = int(row["cap_hit"] or 0) - int(row["dead_cap_if_cut_pre_june1"] or 0)
        if savings <= 0:
            continue
        item = dict(row)
        item.update(
            {
                "net_savings_pre_june1": savings,
                "gross_savings_pre_june1": savings,
                "market_score": int(row["overall"] or 60),
            }
        )
        candidates.append(item)
    candidates.sort(key=lambda item: int(item.get("net_savings_pre_june1") or 0), reverse=True)
    return candidates[:limit]


def quick_extension_restructure_candidates(
    con: sqlite3.Connection,
    team_id: int,
    league_year: int,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
            cy.contract_id,
            cy.player_id,
            cy.base_salary,
            cy.cap_hit,
            c.end_year,
            c.start_year,
            c.total_years,
            c.total_value,
            c.aav,
            c.contract_type,
            c.salary_structure,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.overall,
            p.potential
        FROM contract_years cy
        JOIN contracts c ON c.contract_id = cy.contract_id
        JOIN players p ON p.player_id = cy.player_id
        LEFT JOIN roster_status_types rst ON rst.status_code = p.status
        WHERE cy.team_id = ?
          AND cy.season = ?
          AND cy.is_active = 1
          AND c.is_active = 1
          AND p.team_id = cy.team_id
          AND COALESCE(rst.counts_against_top51, 1) = 1
          AND COALESCE(c.end_year, ?) <= ?
          AND cy.base_salary > ?
          AND lower(COALESCE(c.contract_type, '')) NOT LIKE '%rookie%'
          AND COALESCE(c.contract_type, '') NOT IN ('FifthYearOption', 'FranchiseTag', 'TransitionTag', 'RFAROFRTender', 'RFAOriginalRoundTender', 'RFASecondRoundTender', 'RFAFirstRoundTender', 'ERFATender')
          AND NOT EXISTS (
              SELECT 1
              FROM contract_restructures rr
              WHERE rr.contract_id = cy.contract_id
                AND rr.restructure_season = ?
                AND rr.is_active = 1
          )
        ORDER BY cy.base_salary DESC, cy.cap_hit DESC
        LIMIT ?
        """,
        (
            team_id,
            league_year,
            league_year,
            league_year,
            contract_negotiations.MIN_RESTRUCTURE_BASE_FLOOR + contract_negotiations.MIN_RESTRUCTURE_SAVINGS,
            league_year,
            max(limit * 3, limit),
        ),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        group = position_group_for(str(item.get("position") or ""))
        age = int(item.get("age") or 28)
        extension_years = 2
        if group == "QB" and age <= 34:
            extension_years = 3
        elif group in {"WR", "TE", "OT", "IOL", "EDGE", "IDL", "CB", "S"} and age <= 30:
            extension_years = 3
        proration_years = min(5, 1 + extension_years)
        max_convert = max(0, int(item.get("base_salary") or 0) - contract_negotiations.MIN_RESTRUCTURE_BASE_FLOOR)
        suggested_convert = min(max_convert, round_to(int(item.get("base_salary") or 0) * 0.60, 50_000))
        current_savings = suggested_convert - int(suggested_convert / proration_years) if suggested_convert else 0
        if current_savings < contract_negotiations.MIN_RESTRUCTURE_SAVINGS:
            continue
        item.update(
            {
                "extension_years": extension_years,
                "remaining_contract_years": 1 + extension_years,
                "proration_years": proration_years,
                "suggested_convert": suggested_convert,
                "estimated_current_savings": current_savings,
            }
        )
        candidates.append(item)
    candidates.sort(key=lambda item: int(item.get("estimated_current_savings") or 0), reverse=True)
    return candidates[:limit]


def apply_quick_cap_restructure(
    con: sqlite3.Connection,
    *,
    team_id: int,
    league_year: int,
    candidate: dict[str, Any],
) -> int:
    converted = int(candidate.get("suggested_convert") or 0)
    if converted <= 0:
        raise ValueError("No restructure amount available.")
    proration_years = max(1, min(5, int(candidate.get("proration_years") or 1)))
    current_proration = int(converted / proration_years)
    con.execute(
        """
        INSERT INTO contract_restructures (
            contract_id, player_id, team_id, restructure_season, converted_salary,
            proration_years, current_year_proration, source, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(candidate["contract_id"]),
            int(candidate["player_id"]),
            team_id,
            league_year,
            converted,
            proration_years,
            current_proration,
            SOURCE,
            "CPU post-draft cap compliance restructure.",
        ),
    )
    contract_negotiations.apply_restructure_to_contract_years(
        con,
        contract_id=int(candidate["contract_id"]),
        restructure_season=league_year,
        converted_salary=converted,
        proration_years=proration_years,
    )
    sync_team_cap_space(con)
    return converted - current_proration


def apply_quick_cap_extension_restructure(
    con: sqlite3.Connection,
    *,
    team_id: int,
    league_year: int,
    candidate: dict[str, Any],
) -> int:
    converted = int(candidate.get("suggested_convert") or 0)
    extension_years = max(1, int(candidate.get("extension_years") or 2))
    old_end = int(candidate.get("end_year") or league_year)
    new_end = old_end + extension_years
    proration_years = max(1, min(5, int(candidate.get("proration_years") or (1 + extension_years))))
    if converted <= 0:
        raise ValueError("No extension restructure amount available.")
    current_proration = int(converted / proration_years)
    aav = int(candidate.get("aav") or 0)
    added_value = max(0, aav * extension_years)
    con.execute(
        """
        UPDATE contracts
        SET end_year = ?,
            total_years = COALESCE(total_years, 0) + ?,
            total_value = COALESCE(total_value, 0) + ?,
            salary_structure = COALESCE(NULLIF(salary_structure, ''), 'balanced')
        WHERE contract_id = ?
        """,
        (new_end, extension_years, added_value, int(candidate["contract_id"])),
    )
    con.execute(
        """
        INSERT INTO contract_restructures (
            contract_id, player_id, team_id, restructure_season, converted_salary,
            proration_years, current_year_proration, source, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(candidate["contract_id"]),
            int(candidate["player_id"]),
            team_id,
            league_year,
            converted,
            proration_years,
            current_proration,
            SOURCE,
            f"CPU cap compliance extension restructure through {new_end}.",
        ),
    )
    rebuild_contract_year(con, int(candidate["contract_id"]))
    sync_team_cap_space(con)
    return converted - current_proration


def apply_quick_cap_release(
    con: sqlite3.Connection,
    *,
    team_id: int,
    league_year: int,
    candidate: dict[str, Any],
    event_date: str,
) -> int:
    player_id = int(candidate["player_id"])
    contract_id = int(candidate["contract_id"])
    dead_current = int(candidate.get("dead_cap_if_cut_pre_june1") or 0)
    savings = int(candidate.get("net_savings_pre_june1") or 0)
    con.execute("UPDATE contracts SET is_active = 0 WHERE contract_id = ?", (contract_id,))
    con.execute(
        """
        UPDATE contract_years
        SET is_active = 0,
            notes = COALESCE(notes || ' ', '') || 'CPU post-draft cap compliance release.',
            updated_at = datetime('now')
        WHERE contract_id = ?
          AND season >= ?
        """,
        (contract_id, league_year),
    )
    if dead_current:
        con.execute(
            """
            INSERT INTO team_cap_charges (
                team_id, season, charge_type, description, amount, player_id, source
            )
            VALUES (?, ?, 'Dead Cap', ?, ?, ?, ?)
            """,
            (
                team_id,
                league_year,
                f"Dead cap from releasing {candidate['player_name']}.",
                dead_current,
                player_id,
                SOURCE,
            ),
        )
    con.execute(
        "UPDATE players SET team_id = NULL, status = 'Free Agent' WHERE player_id = ?",
        (player_id,),
    )
    if table_exists(con, "free_agency_player_markets"):
        con.execute(
            """
            UPDATE free_agency_player_markets
            SET status = 'available',
                signed_team_id = NULL,
                signed_offer_id = NULL,
                last_offer_at = NULL,
                decision_notes = TRIM(COALESCE(decision_notes || ' ', '') || 'Returned to market after cap-compliance release.'),
                updated_at = datetime('now')
            WHERE league_year = ?
              AND player_id = ?
              AND status = 'signed'
            """,
            (league_year, player_id),
        )
    cpu_depth_chart.mark_depth_chart_stale(
        con,
        team_id=team_id,
        reason="Cap-compliance release changed roster composition.",
    )
    if table_exists(con, "depth_charts"):
        con.execute("DELETE FROM depth_charts WHERE player_id = ?", (player_id,))
    try:
        roster_actions.upsert_basic_free_agent_profile(con, player_id)
    except Exception:
        pass
    try:
        transaction_id, _ = insert_transaction(
            con,
            transaction_date=event_date,
            season=league_year,
            phase=PHASE,
            transaction_type="Release",
            team_id=team_id,
            player_id=player_id,
            contract_id=contract_id,
            from_team_id=team_id,
            old_status="Active",
            new_status="Free Agent",
            cap_delta_current=-savings,
            cash_delta=0,
            description=f"CPU cap compliance release of {candidate['player_name']}.",
            source=SOURCE,
            external_ref=f"cpu_cap_compliance_release:{league_year}:{player_id}:{contract_id}",
        )
        con.execute(
            """
            INSERT INTO transaction_assets (
                transaction_id, asset_type, player_id, contract_id,
                from_team_id, amount, season, asset_description
            )
            VALUES (?, 'ReleasedPlayer', ?, ?, ?, ?, ?, ?)
            """,
            (
                transaction_id,
                player_id,
                contract_id,
                team_id,
                dead_current,
                league_year,
                "CPU post-draft cap compliance release.",
            ),
        )
    except Exception:
        pass
    sync_team_cap_space(con)
    return savings


def cap_restructure_keeper_score(candidate: dict[str, Any]) -> int:
    group = position_group_for(str(candidate.get("position") or ""))
    overall = int(candidate.get("overall") or candidate.get("market_score") or 60)
    potential = int(candidate.get("potential") or overall)
    age = int(candidate.get("age") or 28)
    score = overall
    if group == "QB":
        score += 10
    elif group in {"WR", "TE", "OT", "EDGE", "IDL", "CB"}:
        score += 4
    elif group in {"IOL", "LB", "S"}:
        score += 2
    elif group == "RB":
        score -= 2
    if age <= 26 and potential >= overall + 5:
        score += min(9, potential - overall)
    if age <= 25 and potential >= 86:
        score += 4
    old_age = POSITION_OLD_AGE.get(group, 31)
    if age > old_age:
        score -= (age - old_age) * 2
    if group == "RB" and age >= 28:
        score -= 3
    if group in {"K", "P", "LS", "ST"}:
        score -= 30
    return score


def cap_compliance_restructure_grade(
    candidate: dict[str, Any],
    *,
    cap_space: int,
    min_space: int,
) -> tuple[bool, str]:
    group = position_group_for(str(candidate.get("position") or ""))
    if group in {"K", "P", "LS", "ST"}:
        return False, "specialist restructure avoided"
    if int(candidate.get("recent_fa_signing") or 0):
        return False, "fresh FA signing restructure avoided"

    savings = int(candidate.get("estimated_current_savings") or 0)
    remaining_years = int(candidate.get("remaining_contract_years") or 1)
    if remaining_years < 2:
        return False, "not enough years left"

    savings_floor = 2_000_000
    required_score = 80
    if cap_space < 0:
        savings_floor = 1_250_000
        required_score = 75
    elif cap_space < min_space:
        savings_floor = 1_500_000
        required_score = 77
    if savings < savings_floor:
        return False, "not enough current-year savings"

    overall = int(candidate.get("overall") or candidate.get("market_score") or 60)
    potential = int(candidate.get("potential") or overall)
    contract_type = str(candidate.get("contract_type") or "").lower()
    rookie_scale = "rookie" in contract_type or "draft" in contract_type
    keeper_score = cap_restructure_keeper_score(candidate)
    if rookie_scale and not (
        cap_space < 0
        and savings >= 1_250_000
        and (potential >= 84 or overall >= 74)
    ):
        return False, "rookie-scale deal protected from routine restructure"
    if keeper_score < required_score:
        return False, f"keeper score {keeper_score} below {required_score}"
    return True, f"keeper score {keeper_score}"


def cap_compliance_release_grade(
    con: sqlite3.Connection,
    candidate: dict[str, Any],
    *,
    team_id: int,
    cap_space: int,
    min_space: int,
) -> tuple[bool, str]:
    group = position_group_for(str(candidate.get("position") or ""))
    overall = int(candidate.get("overall") or candidate.get("market_score") or 60)
    potential = int(candidate.get("potential") or overall)
    age = int(candidate.get("age") or 28)
    savings = int(candidate.get("net_savings_pre_june1") or 0)
    cap_hit = int(candidate.get("cap_hit") or 0)
    needed_savings = max(1_500_000, min_space - cap_space)
    cap_pressure = cap_space < min_space
    cap_emergency = cap_space < 0
    recent_fa = bool(int(candidate.get("recent_fa_signing") or 0))

    if savings < 1_250_000 or cap_hit < 2_500_000:
        return False, "not enough cap impact"
    if group in {"K", "P", "LS", "ST"}:
        return False, "specialist protected"
    if group == "QB" and (overall >= 66 or potential >= 75):
        return False, "QB room protected"
    if overall >= 82:
        return False, "core starter protected"
    if age <= 25 and potential >= 84:
        return False, "young high-upside player protected"
    if group in {"WR", "TE", "OT", "EDGE", "IDL", "CB"} and overall >= 79 and age <= 30:
        return False, "premium-position starter protected"
    if group == "RB" and overall >= 80 and age <= 27 and savings < 14_000_000:
        return False, "prime feature back protected"

    room = team_group_room_context(
        con,
        team_id,
        group,
        exclude_player_id=int(candidate["player_id"]),
    )
    count = int(room["count"])
    starters = int(room["starter_slots"])
    ideal = int(room["ideal"])
    starter_floor = int(room["starter_floor"])
    best = int(room["best"])
    target_floor = STARTER_FLOOR_BY_GROUP.get(group, 68)
    comparable_cover = count >= starters and starter_floor >= min(overall - 4, target_floor - 2)
    deep_room = count >= max(starters + 1, ideal - 1)
    buried = count >= starters and best >= overall + 3

    if recent_fa:
        recent_floor = 4_000_000 if cap_emergency else 6_000_000
        if not cap_pressure:
            return False, "fresh FA signing protected without cap pressure"
        if savings < recent_floor and not (cap_emergency and overall <= 69 and savings >= 2_500_000):
            return False, "fresh FA signing savings not worth reversal"

    capped_needed = min(needed_savings, 8_000_000)
    if overall <= 69 and savings >= max(1_500_000, int(capped_needed * 0.35)) and (comparable_cover or deep_room or cap_emergency):
        return True, "expensive sub-70 depth"
    if overall <= 72 and savings >= max(2_500_000, int(capped_needed * 0.45)) and (comparable_cover or deep_room or cap_emergency):
        return True, "replaceable low-70s contract"
    if overall <= 75 and savings >= max(4_000_000, int(capped_needed * 0.60)) and (
        comparable_cover or deep_room or buried or (cap_emergency and (age >= 30 or group in {"RB", "LB", "S", "IOL", "IDL"}))
    ):
        return True, "mid-tier veteran cap casualty"
    if group in {"RB", "LB", "S"} and overall <= 78 and age >= 28 and savings >= 4_000_000 and (comparable_cover or cap_emergency):
        return True, "replaceable veteran at devalued position"
    if age >= POSITION_OLD_AGE.get(group, 31) + 1 and overall <= 76 and savings >= 6_000_000 and (comparable_cover or deep_room):
        return True, "aging expensive veteran with cover"
    return False, "room/value does not justify release"


def cpu_cap_compliance_sweep(
    con: sqlite3.Connection,
    league_year: int,
    *,
    user_team: str | None = None,
    min_space: int = 1_000_000,
    max_moves_per_team: int = 6,
    max_teams: int = 32,
    time_budget_seconds: float = 35.0,
    write_snapshot: bool = False,
) -> dict[str, int]:
    """Bring CPU teams back toward cap compliance after FA/draft commitments.

    This is intentionally conservative: restructure real core players first,
    then release non-core contracts with meaningful savings. It exists as a
    final guardrail for fast-forwarded offseasons where many signings and
    rookie contracts land without the user manually managing every team.
    """
    sync_team_cap_space(con)
    totals = {"teams": 0, "restructures": 0, "releases": 0, "still_over": 0}
    started_at = time.monotonic()
    teams = con.execute(
        """
        SELECT t.team_id, t.abbreviation, COALESCE(cap.cap_space, t.salary_cap) AS cap_space
        FROM teams t
        LEFT JOIN team_cap_view cap ON cap.team_id = t.team_id
        WHERE COALESCE(cap.cap_space, t.salary_cap) < ?
          AND (? IS NULL OR t.abbreviation <> ?)
        ORDER BY cap_space ASC
        LIMIT ?
        """,
        (min_space, user_team, user_team, max_teams),
    ).fetchall()
    for team in teams:
        if time.monotonic() - started_at > time_budget_seconds:
            totals["timed_out"] = 1
            break
        team_id = int(team["team_id"])
        moves = 0
        touched = False
        period = current_period(con, league_year)
        event_date = period["current_date"] if period else f"{league_year}-04-22"
        while moves < max_moves_per_team:
            if time.monotonic() - started_at > time_budget_seconds:
                totals["timed_out"] = 1
                break
            cap_row = con.execute("SELECT cap_space FROM team_cap_view WHERE team_id = ?", (team_id,)).fetchone()
            cap_space = int(cap_row["cap_space"] or 0) if cap_row else int(team["cap_space"] or 0)
            if cap_space >= min_space:
                break

            moved = False
            try:
                restructures = quick_restructure_candidates(con, team_id, league_year, limit=12)
            except Exception:
                restructures = []
            for candidate in restructures:
                restructure_ok, _reason = cap_compliance_restructure_grade(
                    candidate,
                    cap_space=cap_space,
                    min_space=min_space,
                )
                if not restructure_ok:
                    continue
                try:
                    applied_savings = apply_quick_cap_restructure(
                        con,
                        team_id=team_id,
                        league_year=league_year,
                        candidate=candidate,
                    )
                except Exception:
                    continue
                if applied_savings <= 0:
                    continue
                totals["restructures"] += 1
                moves += 1
                touched = True
                moved = True
                log_event(
                    con,
                    league_year=league_year,
                    event_date=event_date,
                    event_hour=None,
                    event_type="cpu_cap_compliance_restructure",
                    team_id=team_id,
                    player_id=int(candidate["player_id"]),
                    message=(
                        f"{team['abbreviation']} restructured {candidate['player_name']} "
                        f"during the post-draft cap compliance sweep."
                    ),
                )
                break
            if moved:
                sync_team_cap_space(con)
                continue

            try:
                extension_restructures = quick_extension_restructure_candidates(con, team_id, league_year, limit=10)
            except Exception:
                extension_restructures = []
            for candidate in extension_restructures:
                restructure_ok, _reason = cap_compliance_restructure_grade(
                    candidate,
                    cap_space=cap_space,
                    min_space=min_space,
                )
                if not restructure_ok:
                    continue
                try:
                    applied_savings = apply_quick_cap_extension_restructure(
                        con,
                        team_id=team_id,
                        league_year=league_year,
                        candidate=candidate,
                    )
                except Exception:
                    continue
                if applied_savings <= 0:
                    continue
                totals["restructures"] += 1
                moves += 1
                touched = True
                moved = True
                log_event(
                    con,
                    league_year=league_year,
                    event_date=event_date,
                    event_hour=None,
                    event_type="cpu_cap_compliance_restructure",
                    team_id=team_id,
                    player_id=int(candidate["player_id"]),
                    message=(
                        f"{team['abbreviation']} extended and restructured {candidate['player_name']} "
                        f"during the post-draft cap compliance sweep."
                    ),
                )
                break
            if moved:
                sync_team_cap_space(con)
                continue

            try:
                releases = quick_cap_release_candidates(con, team_id, league_year, limit=16)
            except Exception:
                releases = []
            for candidate in releases:
                release_ok, _reason = cap_compliance_release_grade(
                    con,
                    candidate,
                    team_id=team_id,
                    cap_space=cap_space,
                    min_space=min_space,
                )
                if not release_ok:
                    continue
                try:
                    applied_savings = apply_quick_cap_release(
                        con,
                        team_id=team_id,
                        league_year=league_year,
                        candidate=candidate,
                        event_date=event_date,
                    )
                except Exception:
                    continue
                if applied_savings <= 0:
                    continue
                totals["releases"] += 1
                moves += 1
                touched = True
                moved = True
                log_event(
                    con,
                    league_year=league_year,
                    event_date=event_date,
                    event_hour=None,
                    event_type="cpu_cap_compliance_release",
                    team_id=team_id,
                    player_id=int(candidate["player_id"]),
                    message=(
                        f"{team['abbreviation']} released {candidate['player_name']} "
                        f"during the post-draft cap compliance sweep."
                    ),
                )
                break
            if moved:
                sync_team_cap_space(con)
                continue
            break

        cap_row = con.execute("SELECT cap_space FROM team_cap_view WHERE team_id = ?", (team_id,)).fetchone()
        final_space = int(cap_row["cap_space"] or 0) if cap_row else 0
        if touched:
            totals["teams"] += 1
        if final_space < min_space:
            totals["still_over"] += 1
    if totals["teams"] and write_snapshot:
        snapshot_cap_ledger(
            con,
            label=f"post_draft_cap_compliance_{league_year}",
            phase=PHASE,
            source=SOURCE,
            replace=True,
        )
    return totals


def create_cpu_offers(
    con: sqlite3.Connection,
    period: sqlite3.Row,
    count: int,
    seed: int | None = None,
    *,
    user_team: str | None = None,
) -> int:
    if count <= 0:
        return 0
    rng = random.Random(seed or f"{period['league_year']}:{period['current_date']}:{period['current_hour']}")
    cap_reserve = cpu_cap_reserve_for_period(period)
    teams = con.execute(
        """
        SELECT t.team_id, t.abbreviation, cap.cap_space
        FROM teams t
        LEFT JOIN team_cap_view cap ON cap.team_id = t.team_id
        WHERE COALESCE(cap.cap_space, t.salary_cap) > ?
          AND (? IS NULL OR t.abbreviation <> ?)
        ORDER BY t.abbreviation
        """
        ,
        (cap_reserve + 2_000_000, user_team, user_team),
    ).fetchall()
    if not teams:
        return 0

    created = 0
    offers_by_team: dict[int, int] = {}
    team_spend: dict[int, int] = {
        int(row["team_id"]): int(row["pending_aav"] or 0)
        for row in con.execute(
            """
            SELECT team_id, SUM(aav) AS pending_aav
            FROM free_agency_offers
            WHERE league_year = ?
              AND status = 'pending'
            GROUP BY team_id
            """,
            (period["league_year"],),
        ).fetchall()
    }
    team_group_spend: dict[tuple[int, str], int] = {
        (int(row["team_id"]), str(row["position_group"])): int(row["pending_aav"] or 0)
        for row in con.execute(
            """
            SELECT o.team_id, m.position_group, SUM(o.aav) AS pending_aav
            FROM free_agency_offers o
            JOIN free_agency_player_markets m
              ON m.league_year = o.league_year
             AND m.player_id = o.player_id
            WHERE o.league_year = ?
              AND o.status = 'pending'
            GROUP BY o.team_id, m.position_group
            """,
            (period["league_year"],),
        ).fetchall()
    }
    team_group_offers: dict[tuple[int, str], int] = {
        (int(row["team_id"]), str(row["position_group"])): int(row["pending_count"] or 0)
        for row in con.execute(
            """
            SELECT o.team_id, m.position_group, COUNT(*) AS pending_count
            FROM free_agency_offers o
            JOIN free_agency_player_markets m
              ON m.league_year = o.league_year
             AND m.player_id = o.player_id
            WHERE o.league_year = ?
              AND o.status = 'pending'
            GROUP BY o.team_id, m.position_group
            """,
            (period["league_year"],),
        ).fetchall()
    }
    max_offers_per_team = max(1, min(3, int(count / 28) + 1))
    event_date, event_hour = event_time(period)
    candidates = cpu_offer_candidates(con, int(period["league_year"]), count)
    need_scores = load_team_need_scores(con)
    competition = load_playing_time_competition(con, int(period["league_year"]) - 1)
    group_counts = load_team_group_counts(con)
    active_group_spend = load_team_active_group_spend(con)
    pressure_groups = active_fa_injury_pressure_groups(con)
    early_wave = str(period["current_stage"] or "") == "day_one_hourly" and int(period["day_count"] or 1) <= 1
    late_market = cpu_late_market(period)
    post_draft_context = is_post_draft_market_context(con, int(period["league_year"]))
    fog_game_id = pro_player_fog.active_game_id(con)
    fog_season = int(period["league_year"])
    if late_market:
        candidates.sort(
            key=lambda player: (
                0 if cpu_top_remaining_free_agent(player) and int(row_value(player, "pending_offers", 0) or 0) == 0 else 1,
                -int(row_value(player, "market_heat", 0) or 0),
                -true_overall(player),
                -int(row_value(player, "potential", true_overall(player)) or true_overall(player)),
                -int(row_value(player, "asking_aav", 0) or 0),
                int(row_value(player, "player_id", 0) or 0),
            )
        )
    else:
        candidates.sort(
            key=lambda player: (
                0 if cpu_elite_free_agent(player) else 1,
                0 if cpu_top_remaining_free_agent(player) else 1,
                -int(row_value(player, "market_heat", 0) or 0),
                -true_overall(player),
                -int(row_value(player, "potential", true_overall(player)) or true_overall(player)),
                rng.random(),
            )
        )
    for player in candidates:
        player_group = position_group_for(str(row_value(player, "position_group", row_value(player, "position", ""))))
        market_score = float(row_value(player, "market_score", row_value(player, "overall", 60)) or 60)
        base_player_score = float(true_overall(player)) if post_draft_context else market_score
        true_player_score = float(true_overall(player))
        true_player_potential = int(row_value(player, "potential", true_player_score) or true_player_score)
        if not post_draft_strategy_allows_offer(
            con,
            player,
            pressure_groups=pressure_groups,
        ):
            continue
        top_remaining = cpu_top_remaining_free_agent(player)
        floor_ratio = 0.72
        if late_market and top_remaining:
            floor_ratio = 0.56
        elif late_market:
            floor_ratio = 0.64
        practical_floor = max(
            int(player["minimum_aav"] or 0),
            int((player["asking_aav"] or 0) * floor_ratio),
        )
        affordable_teams = [
            team for team in teams
            if int(team["cap_space"] or 0) - cap_reserve - team_spend.get(int(team["team_id"]), 0) > practical_floor
            and offers_by_team.get(int(team["team_id"]), 0) < max_offers_per_team
        ]
        if not affordable_teams:
            continue
        rng.shuffle(affordable_teams)
        if early_wave:
            affordable_teams.sort(
                key=lambda team: (
                    -need_scores.get((int(team["team_id"]), player_group), 0.0),
                    offers_by_team.get(int(team["team_id"]), 0),
                    -(int(team["cap_space"] or 0) - team_spend.get(int(team["team_id"]), 0)),
                    rng.random(),
                )
            )
        slots = cpu_offer_slots_for_player(player, rng)
        pressure_threshold = need_threshold_for_market_pressure(player)
        serious_bidders = [
            team for team in affordable_teams
            if need_scores.get((int(team["team_id"]), player_group), 0.0) >= pressure_threshold
        ]
        competing_team_count = len(serious_bidders)
        base_market_pressure = 0.0
        if normalized_tier(player) == "Premium" or cpu_elite_free_agent(player):
            base_market_pressure = clamp((competing_team_count - 1) / 4.0, 0.0, 1.0)
        elif normalized_tier(player) == "Starter":
            base_market_pressure = clamp((competing_team_count - 2) / 5.0, 0.0, 0.65)
        current_best_aav = max(
            int(row_value(player, "best_aav", 0) or 0),
            max(
                (
                    int(row["best_aav"] or 0)
                    for row in con.execute(
                        """
                        SELECT MAX(aav) AS best_aav
                        FROM free_agency_offers
                        WHERE league_year = ?
                          AND player_id = ?
                          AND status = 'pending'
                        """,
                        (period["league_year"], player["player_id"]),
                    ).fetchall()
                ),
                default=0,
            ),
        )
        for team in affordable_teams[:slots]:
            team_id = int(team["team_id"])
            perceived_overall, perceived_potential, _staff_read = pro_player_fog.perceived_overall_potential(
                con,
                game_id=fog_game_id,
                season=fog_season,
                evaluator_team_id=team_id,
                player_id=int(player["player_id"]),
                true_overall=true_player_score,
                true_potential=true_player_potential,
                create_missing=True,
            )
            if post_draft_context:
                player_score = float(perceived_overall)
            else:
                player_score = max(45.0, float(base_player_score) + (float(perceived_overall) - true_player_score) * 0.70)
            player_potential = int(round(float(perceived_potential)))
            if not post_draft_strategy_allows_offer(
                con,
                player,
                team_id=team_id,
                pressure_groups=pressure_groups,
            ):
                continue
            injury_leverage_offer = (
                str(row_value(player, "post_draft_strategy", "normal") or "normal") == "injury_wait"
                and (team_id, player_group) in pressure_groups
            )
            if cpu_should_block_cap_casualty_reunion(
                player,
                str(team["abbreviation"]),
                late_market=late_market,
            ):
                continue
            team_need = need_scores.get((int(team["team_id"]), player_group), 0.0)
            if recent_team_release_for_player(
                con,
                player_id=int(player["player_id"]),
                team_id=team_id,
                since_date=f"{int(period['league_year'])}-03-01",
            ):
                continue
            if recent_rejected_offer_for_player(
                con,
                league_year=int(period["league_year"]),
                team_id=team_id,
                player_id=int(player["player_id"]),
                current_date=str(event_date),
                cooldown_days=14 if late_market or post_draft_context else 21,
            ):
                continue
            group_key = (team_id, player_group)
            group_spend = team_group_spend.get(group_key, 0)
            active_spend = active_group_spend.get(group_key, 0)
            group_offer_count = team_group_offers.get(group_key, 0)
            group_spend_limit = CPU_GROUP_SPEND_LIMITS.get(player_group, 30_000_000)
            group_count_limit = CPU_GROUP_OFFER_COUNT_LIMITS.get(player_group, 2)
            roster_group_count = group_counts.get(group_key, 0)
            group_depth_limit = CPU_GROUP_DEPTH_COUNT_LIMITS.get(player_group, ROOM_IDEAL_BY_GROUP.get(player_group, 5) + 1)
            room_context = team_group_room_context(con, team_id, player_group)
            room_ideal = int(room_context["ideal"])
            room_starters = int(room_context["starter_slots"])
            room_starter_floor = int(room_context["starter_floor"])
            late_need_exception = late_market and top_remaining and team_need >= 28
            if player_group == "RB" and group_offer_count >= 1 and player_score >= 70 and team_need < 82 and not late_need_exception:
                continue
            qb_backup_cap: int | None = None
            if player_group == "QB":
                if roster_group_count >= group_depth_limit and team_need < 70:
                    continue
                if group_offer_count >= 1 and team_need < 70:
                    continue
                if group_spend > 0 and team_need < 75:
                    continue
                if (
                    int(room_context.get("young_upside", 0) or 0) > 0
                    and int(room_context.get("best", 0) or 0) >= 70
                    and int(row_value(player, "age", 28) or 28) >= 27
                    and player_score <= int(room_context.get("best", 0) or 0) + 3
                    and team_need < 84
                ):
                    continue
                qb_limits = qb_room_offer_limits(
                    con,
                    player,
                    team_qb_scores=competition.get((team_id, "QB"), []),
                    team_need=team_need,
                    roster_qb_count=roster_group_count,
                    incoming_score_override=player_score,
                    incoming_potential_override=player_potential,
                )
                if not qb_limits["allowed"]:
                    continue
                qb_backup_cap = qb_limits["backup_cap"]
            elif roster_group_count >= group_depth_limit and group_offer_count >= 1 and team_need < 40 and not late_need_exception:
                continue
            elif roster_group_count >= room_ideal and group_offer_count >= 1 and team_need < 64 and not late_need_exception:
                continue
            elif (
                roster_group_count >= room_ideal + 1
                and player_score <= room_starter_floor + 2
                and team_need < 72
                and not late_need_exception
            ):
                continue
            elif roster_group_count >= room_starters and group_offer_count >= 2:
                continue
            if group_offer_count >= group_count_limit and team_need < 48 and not late_need_exception:
                continue
            if group_spend >= group_spend_limit and team_need < 62 and not late_need_exception:
                continue
            if early_wave:
                is_top_market = normalized_tier(player) in {"Premium", "Starter"} or int(row_value(player, "market_heat", 0) or 0) >= 72
                elite_target = cpu_elite_free_agent(player)
                if is_top_market and team_need < 24 and player_score >= 68 and not elite_target:
                    continue
                if player_score >= 74 and team_need < 16 and not elite_target:
                    continue
                if player_group not in {"QB", "OT", "IOL", "EDGE", "CB", "WR", "TE"} and team_need < 30 and player_score >= 70:
                    continue
            qb_backup_multiplier = 1.0
            if player_group == "QB":
                qb_fit = qb_backup_reluctance(
                    con,
                    player,
                    team_id=team_id,
                    team_qb_scores=competition.get((team_id, "QB"), []),
                    team_need=team_need,
                    rng=rng,
                    incoming_score_override=player_score,
                )
                if qb_fit is None:
                    continue
                qb_backup_multiplier = qb_fit
            duplicate = con.execute(
                """
                SELECT 1
                FROM free_agency_offers
                WHERE league_year = ?
                  AND player_id = ?
                  AND team_id = ?
                  AND status = 'pending'
                """,
                (period["league_year"], player["player_id"], team["team_id"]),
            ).fetchone()
            if duplicate:
                continue

            team_market_pressure = market_pressure_for_need(
                player,
                base_pressure=base_market_pressure,
                team_need=team_need,
            )
            effective_best_aav = current_best_aav if team_market_pressure > 0 else 0
            low, high = cpu_aav_bounds(
                player,
                best_aav=effective_best_aav,
                market_pressure=team_market_pressure,
            )
            max_room = max(0, int(team["cap_space"] or 0) - cap_reserve - team_spend.get(int(team["team_id"]), 0))
            max_room = int(max_room * 0.88)
            if low > max_room:
                continue
            high = min(high, max_room)
            quality_cap = cpu_true_quality_aav_cap(player)
            if late_market and top_remaining:
                if team_need < 12:
                    quality_cap = int(quality_cap * 0.82)
                elif team_need < 24:
                    quality_cap = int(quality_cap * 0.92)
            elif cpu_elite_free_agent(player):
                if team_need < 10:
                    quality_cap = int(quality_cap * 0.86)
                elif team_need < 24:
                    quality_cap = int(quality_cap * 0.96)
            else:
                if team_need < 18:
                    quality_cap = int(quality_cap * 0.72)
                elif team_need < 32:
                    quality_cap = int(quality_cap * 0.86)
            if player_group == "RB" and team_need < 45:
                quality_cap = int(quality_cap * 0.82)
            if player_group == "QB" and qb_backup_multiplier < 1.0:
                quality_cap = int(quality_cap * qb_backup_multiplier)
            if player_group == "QB" and qb_backup_cap:
                if int(player["minimum_aav"] or 0) > int(qb_backup_cap):
                    continue
                quality_cap = min(quality_cap, int(qb_backup_cap))
            if group_spend:
                remaining_group_room = max(0, group_spend_limit - group_spend)
                if team_need < 62 and not late_need_exception:
                    quality_cap = min(quality_cap, remaining_group_room)
            quality_cap = round_to(max(int(player["minimum_aav"] or 0), quality_cap), 50_000)
            if low > quality_cap:
                if team_need < 55 and not (late_market and top_remaining and team_need >= 28):
                    continue
                low = quality_cap
            high = min(high, max(low, quality_cap))
            aav = preference_adjusted_aav(player, int(round(rng.randint(low, high) / 50_000) * 50_000), rng)
            aav = min(aav, max(low, quality_cap))
            rb_block = rb_starter_stack_block_reason(
                player,
                room=room_context,
                team_need=team_need,
                offer_aav=aav,
                player_score_override=player_score,
                player_potential_override=player_potential,
            )
            if rb_block and not late_need_exception:
                continue
            if not expensive_room_offer_allowed(
                group=player_group,
                team_need=team_need,
                active_spend=active_spend,
                pending_spend=group_spend,
                offer_aav=aav,
                player_score=player_score,
                player_potential=player_potential,
                elite_target=cpu_elite_free_agent(player),
            ):
                continue
            years = preferred_years_for_offer(
                player,
                rng,
                max_years=cpu_offer_year_cap(player),
            )
            if player_group == "QB" and qb_backup_multiplier < 1.0:
                years = min(years, 1 if qb_backup_multiplier < 0.70 else 2)
            if player_group == "QB" and player_score < 72:
                years = min(years, 2)
            if player_group == "QB" and player_score <= 76 and player_potential < 82:
                years = min(years, 1)
            if player_group == "QB" and player_score < 75 and team_need < 75:
                years = min(years, 1)
                aav = min(aav, max(low, round_to(quality_cap * 0.92, 50_000)))
            if post_draft_context:
                starter_path = (
                    team_need >= 82
                    or player_score >= max(75, room_starter_floor + 4)
                    or (cpu_elite_free_agent(player) and team_need >= 42)
                )
                if not starter_path:
                    years = 1
                elif player_score < 76 or int(row_value(player, "age", 29) or 29) >= POSITION_OLD_AGE.get(player_group, 31):
                    years = min(years, 1)
                elif player_score < 78 and player_potential < 82:
                    years = min(years, 2)
            guarantee = guarantee_for_preference(player, int(player["guarantee_pct"] or 0) + 5, rng)
            bonus = cpu_signing_bonus_for_offer(
                player,
                aav=aav,
                years=years,
                guarantee_pct=guarantee,
                rng=rng,
            )
            structure = cpu_contract_structure_for_offer(
                player,
                team_cap_space=max_room,
                aav=aav,
                years=years,
                guarantee_pct=guarantee,
                rng=rng,
            )
            offer_preview = {
                "league_year": int(period["league_year"]),
                "team_id": team_id,
                "player_id": int(player["player_id"]),
                "years": years,
                "aav": aav,
                "total_value": aav * years,
                "signing_bonus": bonus,
                "guarantee_pct": guarantee,
                "contract_structure": structure,
                "notes": "CPU injury-leverage market offer" if injury_leverage_offer else "CPU market offer",
            }
            allowed, _reason = cpu_final_signing_guardrails(con, period, offer_preview, player)
            if not allowed:
                continue
            offer_id = submit_offer(
                con,
                league_year=int(period["league_year"]),
                team_id=team_id,
                player_id=int(player["player_id"]),
                years=years,
                aav=aav,
                signing_bonus=bonus,
                guarantee_pct=guarantee,
                submitted_date=event_date,
                submitted_hour=event_hour,
                contract_structure=structure,
                notes="CPU injury-leverage market offer" if injury_leverage_offer else "CPU market offer",
            )
            offers_by_team[team_id] = offers_by_team.get(team_id, 0) + 1
            team_spend[team_id] = team_spend.get(team_id, 0) + aav
            team_group_spend[group_key] = team_group_spend.get(group_key, 0) + aav
            team_group_offers[group_key] = team_group_offers.get(group_key, 0) + 1
            group_counts[group_key] = group_counts.get(group_key, 0) + 1
            current_best_aav = max(current_best_aav, aav)
            if team_market_pressure > 0:
                base_market_pressure = min(1.0, base_market_pressure + 0.08)
            log_event(
                con,
                league_year=int(period["league_year"]),
                event_date=event_date,
                event_hour=event_hour,
                event_type="cpu_offer",
                team_id=int(team["team_id"]),
                player_id=int(player["player_id"]),
                offer_id=offer_id,
                message=f"{team['abbreviation']} entered the market for {player['player_name']} at {money(aav)} AAV.",
            )
            created += 1
            if created >= count:
                break
        if created >= count:
            break
    return created


def post_draft_depth_offer_aav(
    player: sqlite3.Row | dict[str, Any],
    *,
    starter_hole: bool,
    rng: random.Random,
) -> int:
    tier = normalized_tier(player)
    group = normalized_group(player)
    asking = int(row_value(player, "asking_aav", 1_500_000) or 1_500_000)
    minimum = int(row_value(player, "minimum_aav", 915_000) or 915_000)
    score = true_overall(player)
    strategy = str(row_value(player, "post_draft_strategy", "normal") or "normal")
    discount = {
        "Premium": 0.78,
        "Starter": 0.70,
        "Rotation": 0.60,
        "Depth": 0.54,
        "Camp": 0.50,
    }.get(tier, 0.56)
    if starter_hole:
        discount += 0.06
    if group == "QB":
        discount -= 0.06
    if score < 70:
        discount -= 0.04
    if strategy == "injury_wait":
        discount = max(discount, 0.92 if starter_hole else 0.86)
    elif strategy == "firm_floor":
        discount = max(discount, 0.76 if starter_hole else 0.70)
    elif strategy == "soften":
        discount -= 0.06
    discount = clamp(discount + rng.gauss(0.0, 0.035), 0.48, 0.86)
    if strategy == "injury_wait":
        discount = max(discount, 0.86)
    cap_multiplier = 0.82 if starter_hole else 0.68
    if group == "QB":
        cap_multiplier = 0.62 if not starter_hole else 0.72
    quality_cap = int(cpu_true_quality_aav_cap(player) * cap_multiplier)
    return round_to(max(minimum, min(int(asking * discount), quality_cap)), 50_000)


def post_draft_depth_market_period(con: sqlite3.Connection, league_year: int) -> sqlite3.Row | dict[str, Any]:
    sync_period_to_game_date(con, league_year)
    period = current_period(con, league_year)
    if period and period["status"] == "active":
        return period
    current_date = current_game_date_value(con) or f"{league_year}-04-30"
    return {
        "league_year": league_year,
        "current_date": current_date,
        "current_hour": 12,
        "current_stage": "daily",
        "day_count": 45,
        "first_day_end_hour": 18,
        "status": "active",
    }


def cpu_post_draft_depth_signings(
    con: sqlite3.Connection,
    league_year: int,
    *,
    user_team: str | None = None,
    max_per_team: int = 3,
    max_total: int = 56,
    seed: int | None = None,
    fill_specialists: bool = True,
    event_date_override: str | None = None,
) -> dict[str, int]:
    """Small post-draft wave where CPU teams fill weak depth with discounted veterans."""
    ensure_schema(con)
    rng = random.Random(seed or f"post-draft-depth:{league_year}")
    reconcile_market_state(con, league_year, stale_offer_days=7)
    ensure_market(con, league_year)
    period = post_draft_depth_market_period(con, league_year)
    if event_date_override:
        period = dict(period)
        period["current_date"] = event_date_override
        period["current_hour"] = 12
        period["current_stage"] = "daily"
    specialist_signings = (
        ensure_missing_specialists_for_fa(
            con,
            period,
            user_team=user_team,
            max_total=96,
        )
        if fill_specialists
        else 0
    )
    event_date, event_hour = event_time(period)
    cap_reserve = cpu_cap_reserve_for_period(period)
    entry_reserve = cpu_post_draft_depth_team_entry_reserve(period)
    teams = con.execute(
        """
        SELECT t.team_id, t.abbreviation, COALESCE(cap.cap_space, t.salary_cap) AS cap_space
        FROM teams t
        LEFT JOIN team_cap_view cap ON cap.team_id = t.team_id
        WHERE COALESCE(cap.cap_space, t.salary_cap) > ?
          AND (? IS NULL OR t.abbreviation <> ?)
        ORDER BY t.abbreviation
        """,
        (entry_reserve + 950_000, user_team, user_team),
    ).fetchall()
    if not teams:
        return {"signings": 0, "teams": 0, "specialist_signings": specialist_signings}

    candidates = con.execute(
        """
        SELECT *
        FROM free_agency_board_view
        WHERE league_year = ?
          AND market_status = 'available'
          AND COALESCE(pending_offers, 0) = 0
        ORDER BY market_score DESC, potential DESC, asking_aav ASC, player_id
        LIMIT 500
        """,
        (league_year,),
    ).fetchall()
    signed_players: set[int] = set()
    signed_teams: set[int] = set()
    total_signed = 0
    need_scores = load_team_need_scores(con)
    post_draft_group_signings = post_draft_depth_signing_counts_by_group(con, league_year=league_year)
    competition = load_playing_time_competition(con, league_year - 1)
    pressure_groups = active_fa_injury_pressure_groups(con)
    fog_game_id = pro_player_fog.active_game_id(con)

    for team in teams:
        if total_signed >= max_total:
            break
        team_id = int(team["team_id"])
        team_signed = 0
        group_order = sorted(
            ROOM_IDEAL_BY_GROUP.keys(),
            key=lambda group: (
                -need_scores.get((team_id, group), 0.0),
                0 if group in {"QB", "OT", "EDGE", "CB", "WR"} else 1,
                group,
            ),
        )
        for group in group_order:
            if team_signed >= max_per_team or total_signed >= max_total:
                break
            if group in {"LS", "ST"}:
                continue
            context = team_group_room_context(con, team_id, group)
            count = int(context["count"])
            ideal = int(context["ideal"])
            starters = int(context["starter_slots"])
            best = int(context["best"])
            starter_floor = int(context["starter_floor"])
            target_floor = STARTER_FLOOR_BY_GROUP.get(group, 68)
            need = need_scores.get((team_id, group), 0.0)
            recent_group_signings = post_draft_group_signings.get((team_id, group), 0)
            starter_hole = count < starters or starter_floor < target_floor - 4
            depth_hole = count < ideal or (count < ideal + 1 and need >= 42)
            if group == "QB":
                qb_scores = competition.get((team_id, "QB"), [])
                best_qb = max(qb_scores) if qb_scores else float(best)
                depth_hole = count < 2 or (count < 3 and need >= 62)
                starter_hole = count == 0 or best_qb < 70
                if not starter_hole and not depth_hole:
                    continue
            elif not starter_hole and not depth_hole:
                continue
            elif (
                count >= ideal
                and need < 70
                and starter_floor >= max(62, target_floor - 2)
            ):
                continue
            elif (
                count >= ideal
                and recent_group_signings >= 1
                and not starter_hole
                and need < 82
            ):
                continue
            elif count >= ideal + 1 and not starter_hole:
                continue

            group_recent_rejections = recent_rejected_offer_count_for_group(
                con,
                league_year=league_year,
                team_id=team_id,
                group=group,
                current_date=str(event_date),
                cooldown_days=21,
            )
            group_candidates = [
                player for player in candidates
                if int(row_value(player, "player_id", 0) or 0) not in signed_players
                and position_group_for(str(row_value(player, "position_group", row_value(player, "position", "")))) == group
                and not recent_rejected_offer_for_player(
                    con,
                    league_year=league_year,
                    team_id=team_id,
                    player_id=int(row_value(player, "player_id", 0) or 0),
                    current_date=str(event_date),
                    cooldown_days=21,
                )
            ]
            if not group_candidates:
                continue
            candidate_player_ids = [
                int(row_value(player, "player_id", 0) or 0)
                for player in group_candidates
            ]
            staff_reads, _created_staff_reads = pro_player_fog.evaluations_for_team(
                con,
                game_id=fog_game_id,
                season=league_year,
                evaluator_team_id=team_id,
                player_ids=candidate_player_ids,
                create_missing=True,
            )
            perceived_candidates: dict[int, tuple[float, int]] = {}
            for candidate in group_candidates:
                candidate_id = int(row_value(candidate, "player_id", 0) or 0)
                true_score = float(true_overall(candidate))
                true_potential = int(row_value(candidate, "potential", true_score) or true_score)
                read = staff_reads.get(candidate_id)
                if read:
                    perceived_candidates[candidate_id] = (
                        float(read.get("overall") or true_score),
                        int(read.get("potential") or true_potential),
                    )
                else:
                    perceived_candidates[candidate_id] = (true_score, true_potential)
            group_candidates.sort(
                key=lambda player: (
                    0 if starter_hole and perceived_candidates.get(int(row_value(player, "player_id", 0) or 0), (true_overall(player), 0))[0] >= max(64, starter_floor + 2) else 1,
                    0 if group_recent_rejections < 3 or perceived_candidates.get(int(row_value(player, "player_id", 0) or 0), (true_overall(player), 0))[0] >= max(63, starter_floor + 1) else 1,
                    -min(8, max(0, perceived_candidates.get(int(row_value(player, "player_id", 0) or 0), (true_overall(player), 0))[0] - max(starter_floor, 58))),
                    int(row_value(player, "asking_aav", 0) or 0),
                    rng.random(),
                )
            )
            for player in group_candidates[:28]:
                player_id = int(row_value(player, "player_id", 0) or 0)
                player_score, player_potential = perceived_candidates.get(
                    player_id,
                    (
                        float(true_overall(player)),
                        int(row_value(player, "potential", true_overall(player)) or true_overall(player)),
                    ),
                )
                player_age = int(row_value(player, "age", 29) or 29)
                young_upside_depth = (
                    player_age <= 25
                    and player_potential >= max(target_floor + 2, int(player_score) + 6)
                )
                improves_starter_floor = player_score > starter_floor
                meaningful_depth = (
                    player_score >= max(58, min(target_floor - 6, starter_floor - 2))
                    or young_upside_depth
                )
                if not post_draft_strategy_allows_offer(
                    con,
                    player,
                    team_id=team_id,
                    pressure_groups=pressure_groups,
                ):
                    continue
                candidate_starter_hole = bool(starter_hole)
                if starter_hole:
                    starter_upgrade_floor = max(62, min(target_floor + 2, starter_floor + 5))
                    if group != "QB" and player_score < starter_upgrade_floor:
                        if not (count < ideal and meaningful_depth):
                            continue
                        candidate_starter_hole = False
                elif player_score < 58:
                    continue
                elif count >= ideal and not meaningful_depth:
                    continue
                elif (
                    count >= ideal
                    and recent_group_signings >= 1
                    and not improves_starter_floor
                    and not (young_upside_depth and need >= 70)
                ):
                    continue
                elif count >= ideal and need < 70:
                    continue
                elif count >= starters and starter_floor >= target_floor - 2 and player_score <= starter_floor - 3 and need < 74:
                    continue
                elif count >= ideal - 1 and best >= target_floor + 8 and player_score < max(60, starter_floor) and need < 80:
                    continue
                qb_backup_cap: int | None = None
                if group == "QB":
                    qb_limits = qb_room_offer_limits(
                        con,
                        player,
                        team_qb_scores=competition.get((team_id, "QB"), []),
                        team_need=need,
                        roster_qb_count=count,
                        incoming_score_override=player_score,
                        incoming_potential_override=player_potential,
                    )
                    if not qb_limits["allowed"]:
                        continue
                    qb_backup_cap = qb_limits["backup_cap"]
                cap_row = roster_actions.cap_row(con, team_id)
                cap_space = int(cap_row["cap_space"] or 0)
                aav = post_draft_depth_offer_aav(player, starter_hole=candidate_starter_hole, rng=rng)
                if qb_backup_cap:
                    aav = min(aav, int(qb_backup_cap))
                if group == "QB" and not starter_hole:
                    aav = min(aav, 8_000_000 if player_score >= 70 else 6_000_000)
                years = 1
                if candidate_starter_hole and player_score >= 75 and player_age <= 29:
                    years = 2
                guarantee = min(35, int(row_value(player, "guarantee_pct", 0) or 0))
                effective_reserve = cpu_post_draft_depth_offer_reserve(
                    period,
                    group=group,
                    aav=aav,
                    years=years,
                    starter_hole=candidate_starter_hole,
                    guarantee_pct=guarantee,
                )
                cap_room = cap_space - effective_reserve
                if cap_room <= 950_000:
                    break
                if aav > int(cap_room * 0.86):
                    continue
                bonus = cpu_signing_bonus_for_offer(
                    player,
                    aav=aav,
                    years=years,
                    guarantee_pct=guarantee,
                    rng=rng,
                )
                offer_preview = {
                    "league_year": league_year,
                    "team_id": team_id,
                    "player_id": player_id,
                    "years": years,
                    "aav": aav,
                    "total_value": aav * years,
                    "signing_bonus": bonus,
                    "guarantee_pct": guarantee,
                    "contract_structure": "balanced",
                    "notes": (
                        "CPU injury-leverage post-draft depth signing"
                        if (team_id, group) in pressure_groups
                        and str(row_value(player, "post_draft_strategy", "normal") or "normal") == "injury_wait"
                        else "CPU post-draft depth signing"
                    ),
                }
                allowed, _reason = cpu_final_signing_guardrails(con, period, offer_preview, player)
                if not allowed:
                    continue
                offer_id = submit_offer(
                    con,
                    league_year=league_year,
                    team_id=team_id,
                    player_id=player_id,
                    years=years,
                    aav=aav,
                    signing_bonus=bonus,
                    guarantee_pct=guarantee,
                    submitted_date=event_date,
                    submitted_hour=event_hour,
                    contract_structure="balanced",
                    notes=str(offer_preview["notes"]),
                )
                offer = con.execute("SELECT * FROM free_agency_offers WHERE offer_id = ?", (offer_id,)).fetchone()
                market = con.execute(
                    "SELECT * FROM free_agency_board_view WHERE league_year = ? AND player_id = ?",
                    (league_year, player_id),
                ).fetchone()
                if not offer or not market:
                    continue
                try:
                    sign_offer(con, period, offer, market)
                except Exception:
                    con.execute(
                        """
                        UPDATE free_agency_offers
                        SET status = 'rejected',
                            decided_date = ?,
                            decided_hour = ?,
                            updated_at = datetime('now')
                        WHERE offer_id = ?
                        """,
                        (event_date, event_hour, offer_id),
                    )
                    continue
                signed_players.add(player_id)
                signed_teams.add(team_id)
                post_draft_group_signings[(team_id, group)] = post_draft_group_signings.get((team_id, group), 0) + 1
                total_signed += 1
                team_signed += 1
                log_event(
                    con,
                    league_year=league_year,
                    event_date=event_date,
                    event_hour=event_hour,
                    event_type="post_draft_depth_signing",
                    team_id=team_id,
                    player_id=player_id,
                    offer_id=offer_id,
                    message=(
                        f"{team['abbreviation']} filled a post-draft {group} need with "
                        f"{player['player_name']} at {money(aav)} AAV."
                    ),
                )
                break
    if total_signed:
        sync_team_cap_space(con)
    return {"signings": total_signed, "teams": len(signed_teams), "specialist_signings": specialist_signings}


def advance_period_clock(con: sqlite3.Connection, period: sqlite3.Row, *, days: int = 0, hours: int = 0) -> None:
    if hours:
        next_hour = int(period["current_hour"]) + hours
        if next_hour > int(period["first_day_end_hour"]):
            next_date = parse_date(str(period["current_date"])) + timedelta(days=1)
            con.execute(
                """
                UPDATE free_agency_periods
                SET current_stage = 'daily',
                    "current_date" = ?,
                    current_hour = first_day_start_hour,
                    day_count = day_count + 1,
                    updated_at = datetime('now')
                WHERE league_year = ?
                """,
                (date_text(next_date), period["league_year"]),
            )
        else:
            con.execute(
                """
                UPDATE free_agency_periods
                SET current_hour = ?,
                    updated_at = datetime('now')
                WHERE league_year = ?
                """,
                (next_hour, period["league_year"]),
            )
    elif days:
        next_date = parse_date(str(period["current_date"])) + timedelta(days=days)
        con.execute(
            """
            UPDATE free_agency_periods
            SET current_stage = 'daily',
                "current_date" = ?,
                day_count = day_count + ?,
                updated_at = datetime('now')
            WHERE league_year = ?
            """,
            (date_text(next_date), days, period["league_year"]),
        )


def process_tick(con: sqlite3.Connection, args: argparse.Namespace, *, hours: int = 0, days: int = 0) -> dict[str, int]:
    ensure_schema(con)
    deactivated_contracts = deactivate_elapsed_active_contracts(con, int(args.league_year))
    period = active_period(con, args.league_year)
    if days and period["current_stage"] == "day_one_hourly" and not args.force:
        raise ValueError("Day 1 is still in hourly mode. Use advance-hour, or pass --force to jump to daily.")

    user_team = cpu_excluded_user_team(con, args)
    market_user_team = general_market_excluded_user_team(con, args)
    skip_cap_cleanup = bool(getattr(args, "skip_cap_cleanup", False))
    if skip_cap_cleanup:
        cpu_restructures = 0
        cpu_cap_releases = 0
        cpu_strategic_releases = 0
    else:
        cpu_restructures = cpu_restructure_core_contracts_for_fa(con, period, user_team=user_team, max_total=8)
        cpu_cap_releases = cpu_release_bad_contracts_for_fa(con, period, user_team=user_team, max_total=16)
        cpu_strategic_releases = cpu_release_strategic_cap_casualties_for_fa(
            con,
            period,
            user_team=user_team,
            max_total=4,
            max_per_team=1,
        )
    user_plan = apply_cpu_controlled_user_free_agent_plan(
        con,
        period,
        args,
        max_offers=max(1, min(3, int(args.cpu_offers or 0) // 12 if int(args.cpu_offers or 0) else 2)),
    )
    created = create_cpu_offers(con, period, args.cpu_offers, args.seed, user_team=market_user_team)
    signed = resolve_pending_offers(
        con,
        period,
        limit=args.signing_limit,
        write_cap_snapshot=not getattr(args, "no_cap_snapshot", False),
    )
    demand_drops, retirements = apply_no_interest_demand_decay(con, period, days=days, hours=hours)
    advance_period_clock(con, period, days=days, hours=hours)
    fresh_period_for_resolution = current_period(con, args.league_year)
    signed_after_advance = 0
    specialist_signings = 0
    if fresh_period_for_resolution and fresh_period_for_resolution["status"] == "active":
        signed_after_advance = resolve_pending_offers(
            con,
            fresh_period_for_resolution,
            limit=args.signing_limit,
            write_cap_snapshot=False,
        )
        signed += signed_after_advance
        if cpu_late_market(fresh_period_for_resolution) or is_post_draft_market_context(con, int(args.league_year)):
            specialist_signings = ensure_missing_specialists_for_fa(
                con,
                fresh_period_for_resolution,
                user_team=user_team,
            )
    cleanup = reconcile_market_state(con, args.league_year)
    cap_cleanup = {"teams": 0, "restructures": 0, "releases": 0, "still_over": 0}
    if not skip_cap_cleanup:
        cap_cleanup = cpu_cap_compliance_sweep(
            con,
            int(args.league_year),
            user_team=user_team,
            min_space=8_000_000,
            max_moves_per_team=5,
            max_teams=32,
            time_budget_seconds=45.0,
            write_snapshot=not getattr(args, "no_cap_snapshot", False),
        )
    if not skip_cap_cleanup and int(cap_cleanup.get("still_over") or 0) > 0:
        followup_cleanup = cpu_cap_compliance_sweep(
            con,
            int(args.league_year),
            user_team=user_team,
            min_space=4_000_000,
            max_moves_per_team=3,
            max_teams=32,
            time_budget_seconds=30.0,
            write_snapshot=not getattr(args, "no_cap_snapshot", False),
        )
        for key, value in followup_cleanup.items():
            if key == "still_over":
                cap_cleanup[key] = value
            else:
                cap_cleanup[key] = int(cap_cleanup.get(key) or 0) + int(value or 0)
    fresh_period = active_period(con, args.league_year)
    log_event(
        con,
        league_year=args.league_year,
        event_date=str(fresh_period["current_date"]),
        event_hour=int(fresh_period["current_hour"]) if fresh_period["current_stage"] == "day_one_hourly" else None,
        event_type="market_advanced",
        message=(
            f"Free agency advanced. CPU offers: {created}. User auto-plan offers: {user_plan.get('offers', 0)}. "
            f"Signings: {signed}. Specialist signings: {specialist_signings}. "
            f"Demand drops: {demand_drops}. Retirements: {retirements}. "
            f"Strategic releases: {cpu_strategic_releases}."
        ),
    )
    if demand_drops:
        log_event(
            con,
            league_year=args.league_year,
            event_date=str(fresh_period["current_date"]),
            event_hour=int(fresh_period["current_hour"]) if fresh_period["current_stage"] == "day_one_hourly" else None,
            event_type="market_demands_softened",
            message=f"{demand_drops} unsigned free agent(s) lowered asking prices after receiving no offers.",
        )
    return {
        "cpu_offers": created,
        "user_auto_plan_offers": user_plan.get("offers", 0),
        "signings": signed,
        "specialist_signings": specialist_signings,
        "demand_drops": demand_drops,
        "retirements": retirements,
        "restructures": cpu_restructures,
        "cap_releases": cpu_cap_releases,
        "strategic_releases": cpu_strategic_releases,
        "post_advance_signings": signed_after_advance,
        "deactivated_contracts": deactivated_contracts,
        "cap_cleanup_teams": cap_cleanup.get("teams", 0),
        "cap_cleanup_restructures": cap_cleanup.get("restructures", 0),
        "cap_cleanup_releases": cap_cleanup.get("releases", 0),
        "cap_cleanup_still_over": cap_cleanup.get("still_over", 0),
        **cleanup,
    }


def board_rows(con: sqlite3.Connection, league_year: int, limit: int, position: str | None = None) -> list[sqlite3.Row]:
    filters = ["league_year = ?", "market_status = 'available'"]
    params: list[Any] = [league_year]
    if position:
        filters.append("position = ?")
        params.append(position.upper())
    return con.execute(
        f"""
        SELECT *
        FROM free_agency_board_view
        WHERE {' AND '.join(filters)}
        ORDER BY market_heat DESC, asking_aav DESC, market_score DESC, player_id
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()


def export_ui_data(con: sqlite3.Connection, league_year: int) -> dict[str, Any]:
    ensure_schema(con)
    reconcile_market_state(con, league_year)
    period = current_period(con, league_year)
    offers = con.execute(
        """
        SELECT *
        FROM free_agency_offers_view
        WHERE league_year = ?
        ORDER BY
            CASE status WHEN 'pending' THEN 0 WHEN 'accepted' THEN 1 ELSE 2 END,
            offer_id DESC
        LIMIT 80
        """,
        (league_year,),
    ).fetchall()
    events = con.execute(
        """
        SELECT *
        FROM free_agency_events
        WHERE league_year = ?
        ORDER BY event_id DESC
        LIMIT 40
        """,
        (league_year,),
    ).fetchall()
    return {
        "league_year": league_year,
        "period": dict(period) if period else None,
        "board": [dict(row) for row in board_rows(con, league_year, limit=100)],
        "offers": [dict(row) for row in offers],
        "recent_events": [dict(row) for row in events],
    }


def run_mutation(con: sqlite3.Connection, args: argparse.Namespace, func):
    con.execute("BEGIN")
    try:
        result = func(con, args)
        if args.apply:
            con.commit()
        else:
            con.rollback()
        return result
    except Exception:
        con.rollback()
        raise


def action_setup(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_schema(con)
        con.commit()
    print("Free agency processor schema ready.")


def action_start(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_schema(con)
        con.commit()
        run_mutation(con, args, start_period)
    print(f"Free agency {'started' if args.apply else 'start dry run'} for {args.league_year}.")
    if not args.apply:
        print("Dry run only. Add --apply to save the market state.")


def action_status(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_schema(con)
        period = current_period(con, args.league_year)
        if not period:
            print(f"No free agency period exists for {args.league_year}.")
            return
        board_count = con.execute(
            """
            SELECT COUNT(*) FROM free_agency_player_markets
            WHERE league_year = ? AND status = 'available'
            """,
            (args.league_year,),
        ).fetchone()[0]
        pending = con.execute(
            """
            SELECT COUNT(*) FROM free_agency_offers
            WHERE league_year = ? AND status = 'pending'
            """,
            (args.league_year,),
        ).fetchone()[0]
        signed = con.execute(
            """
            SELECT COUNT(*) FROM free_agency_player_markets
            WHERE league_year = ? AND status = 'signed'
            """,
            (args.league_year,),
        ).fetchone()[0]
        print(f"{args.league_year} Free Agency")
        print(
            f"Status: {period['status']} | Stage: {period['current_stage']} | "
            f"Date: {period['current_date']} "
            f"{str(period['current_hour']).zfill(2) + ':00' if period['current_stage'] == 'day_one_hourly' else ''}"
        )
        print(f"Available: {board_count} | Pending offers: {pending} | Signed: {signed}")


def action_board(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_schema(con)
        for row in board_rows(con, args.league_year, args.limit, args.position):
            print(
                f"{row['player_id']:>4} {row['player_name']:<24} {row['position']:<4} "
                f"{row['market_tier']:<8} heat {row['market_heat']:>2} "
                f"ask {money(row['asking_aav']):>7} min {money(row['minimum_aav']):>7} "
                f"offers {row['pending_offers'] or 0}"
            )


def action_offer(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = run_mutation(con, args, action_offer_inner)
    print(
        f"Offer {'submitted' if args.apply else 'dry run'}: "
        f"#{result['offer_id']} | CPU response offers: {result['cpu_offers']}"
    )
    if not args.apply:
        print("Dry run only. Add --apply to save the offer.")


def action_advance_hour(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = run_mutation(con, args, lambda c, a: process_tick(c, a, hours=1))
    print(
        f"Advanced one free-agency hour ({'saved' if args.apply else 'dry run'}): "
        f"{result['cpu_offers']} CPU offer(s), {result.get('user_auto_plan_offers', 0)} user-plan offer(s), "
        f"{result['signings']} signing(s), {result.get('specialist_signings', 0)} specialist signing(s), "
        f"{result['demand_drops']} demand drop(s), {result.get('retirements', 0)} retirement(s)."
    )


def action_advance_day(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = run_mutation(con, args, lambda c, a: process_tick(c, a, days=args.days))
    print(
        f"Advanced {args.days} free-agency day(s) ({'saved' if args.apply else 'dry run'}): "
        f"{result['cpu_offers']} CPU offer(s), {result.get('user_auto_plan_offers', 0)} user-plan offer(s), "
        f"{result['signings']} signing(s), {result.get('specialist_signings', 0)} specialist signing(s), "
        f"{result['demand_drops']} demand drop(s), {result.get('retirements', 0)} retirement(s)."
    )


def action_resolve(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = run_mutation(
            con,
            args,
            lambda c, a: {
                "cpu_offers": 0,
                "signings": resolve_pending_offers(
                    c,
                    active_period(c, a.league_year),
                    a.signing_limit,
                    write_cap_snapshot=not getattr(a, "no_cap_snapshot", False),
                ),
            },
        )
    print(f"Resolved offers ({'saved' if args.apply else 'dry run'}): {result['signings']} signing(s).")


def action_cpu_seed(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        def run_seed(c: sqlite3.Connection, a: argparse.Namespace) -> dict[str, int]:
            ensure_schema(c)
            period = active_period(c, a.league_year)
            user_team = cpu_excluded_user_team(c, a)
            market_user_team = general_market_excluded_user_team(c, a)
            retained = cpu_retain_own_free_agents(
                c,
                period,
                user_team=user_team,
                per_team=a.cpu_retention_per_team,
                seed=a.seed,
                write_cap_snapshot=not getattr(a, "no_cap_snapshot", False),
            )
            user_plan = apply_cpu_controlled_user_free_agent_plan(
                c,
                period,
                a,
                max_offers=max(1, min(3, int(a.cpu_offers or 0) // 12 if int(a.cpu_offers or 0) else 2)),
            )
            offers = create_cpu_offers(c, period, a.cpu_offers, a.seed, user_team=market_user_team)
            log_event(
                c,
                league_year=a.league_year,
                event_date=str(period["current_date"]),
                event_hour=int(period["current_hour"]) if period["current_stage"] == "day_one_hourly" else None,
                event_type="cpu_market_seed",
                message=(
                    "Seeded CPU market activity. "
                    f"Own-player re-signings: {retained}. "
                    f"User auto-plan offers: {user_plan.get('offers', 0)}. "
                    f"Open offers: {offers}."
                ),
            )
            return {"retained": retained, "cpu_offers": offers, "user_auto_plan_offers": user_plan.get("offers", 0)}

        result = run_mutation(con, args, run_seed)
    print(
        f"CPU market seed ({'saved' if args.apply else 'dry run'}): "
        f"{result['retained']} own-player re-signing(s), "
        f"{result.get('user_auto_plan_offers', 0)} user-plan offer(s), "
        f"{result['cpu_offers']} open offer(s)."
    )


def action_ui_data(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        payload = export_ui_data(con, args.league_year)
    text = json.dumps(payload, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Create free agency tables/views.")
    setup.set_defaults(func=action_setup)

    start = subparsers.add_parser("start", help="Start a free agency period.")
    start.add_argument("--league-year", type=int)
    start.add_argument("--start-date")
    start.add_argument("--start-hour", type=int, default=12)
    start.add_argument("--end-hour", type=int, default=20)
    start.add_argument("--notes")
    start.add_argument("--skip-expirations", action="store_true", help="Do not move expired contracts into free agency before opening the market.")
    start.add_argument("--cpu-resign-per-team", type=int, default=2, help="CPU own-team extensions before expirations.")
    start.add_argument("--cpu-retention-per-team", type=int, default=0, help="CPU own-player FA re-signings after the market opens.")
    start.add_argument("--opening-cpu-offers", type=int, default=64, help="CPU market offers created immediately when FA opens.")
    start.add_argument("--cpu-controls-user-team", action="store_true", help="Allow CPU FA automation for the active user team when fast-forwarding past free agency.")
    start.add_argument("--seed", type=int)
    start.add_argument("--no-cap-snapshot", action="store_true", help="Skip cap-ledger snapshots for faster UI/background runs.")
    start.add_argument("--apply", action="store_true")
    start.set_defaults(func=action_start)

    status = subparsers.add_parser("status", help="Show free agency status.")
    status.add_argument("--league-year", type=int)
    status.set_defaults(func=action_status)

    board = subparsers.add_parser("board", help="Show the free agency board.")
    board.add_argument("--league-year", type=int)
    board.add_argument("--position")
    board.add_argument("--limit", type=int, default=40)
    board.set_defaults(func=action_board)

    offer = subparsers.add_parser("offer", help="Submit a manual offer.")
    offer.add_argument("--league-year", type=int)
    offer.add_argument("--team", required=True)
    offer.add_argument("--player", required=True, help="Player id or name search.")
    offer.add_argument("--years", type=int, required=True)
    offer.add_argument("--aav", required=True)
    offer.add_argument("--bonus", default="0")
    offer.add_argument("--guarantee-pct", type=int, default=0)
    offer.add_argument(
        "--structure",
        choices=["balanced", "backloaded", "frontloaded", "bonus-heavy"],
        default="balanced",
        help="Year-by-year contract shape for cap/cash accounting.",
    )
    offer.add_argument("--notes")
    offer.add_argument("--force-market", action="store_true")
    offer.add_argument("--cpu-response-offers", type=int, default=2)
    offer.add_argument("--seed", type=int)
    offer.add_argument("--apply", action="store_true")
    offer.set_defaults(func=action_offer)

    advance_hour = subparsers.add_parser("advance-hour", help="Process one first-day free agency hour.")
    advance_hour.add_argument("--league-year", type=int)
    advance_hour.add_argument("--cpu-offers", type=int, default=28)
    advance_hour.add_argument("--signing-limit", type=int)
    advance_hour.add_argument("--seed", type=int)
    advance_hour.add_argument("--force", action="store_true")
    advance_hour.add_argument("--cpu-controls-user-team", action="store_true", help="Allow CPU FA automation for the active user team when fast-forwarding past free agency.")
    advance_hour.add_argument("--no-cap-snapshot", action="store_true", help="Skip cap-ledger snapshots for faster UI/background runs.")
    advance_hour.add_argument("--apply", action="store_true")
    advance_hour.set_defaults(func=action_advance_hour)

    advance_day = subparsers.add_parser("advance-day", help="Process one or more free agency days.")
    advance_day.add_argument("--league-year", type=int)
    advance_day.add_argument("--days", type=int, default=1)
    advance_day.add_argument("--cpu-offers", type=int, default=40)
    advance_day.add_argument("--signing-limit", type=int)
    advance_day.add_argument("--seed", type=int)
    advance_day.add_argument("--force", action="store_true")
    advance_day.add_argument("--cpu-controls-user-team", action="store_true", help="Allow CPU FA automation for the active user team when fast-forwarding past free agency.")
    advance_day.add_argument("--no-cap-snapshot", action="store_true", help="Skip cap-ledger snapshots for faster UI/background runs.")
    advance_day.add_argument("--apply", action="store_true")
    advance_day.set_defaults(func=action_advance_day)

    resolve = subparsers.add_parser("resolve", help="Resolve currently pending offers without advancing time.")
    resolve.add_argument("--league-year", type=int)
    resolve.add_argument("--signing-limit", type=int)
    resolve.add_argument("--no-cap-snapshot", action="store_true", help="Skip cap-ledger snapshots for faster UI/background runs.")
    resolve.add_argument("--apply", action="store_true")
    resolve.set_defaults(func=action_resolve)

    cpu_seed = subparsers.add_parser("cpu-seed", help="Seed CPU free-agency retention and open market offers.")
    cpu_seed.add_argument("--league-year", type=int)
    cpu_seed.add_argument("--cpu-retention-per-team", type=int, default=1)
    cpu_seed.add_argument("--cpu-offers", type=int, default=64)
    cpu_seed.add_argument("--seed", type=int)
    cpu_seed.add_argument("--cpu-controls-user-team", action="store_true", help="Allow CPU FA automation for the active user team when fast-forwarding past free agency.")
    cpu_seed.add_argument("--no-cap-snapshot", action="store_true", help="Skip cap-ledger snapshots for faster UI/background runs.")
    cpu_seed.add_argument("--apply", action="store_true")
    cpu_seed.set_defaults(func=action_cpu_seed)

    ui_data = subparsers.add_parser("ui-data", help="Export JSON for a free agency UI.")
    ui_data.add_argument("--league-year", type=int)
    ui_data.add_argument("--output", type=Path)
    ui_data.set_defaults(func=action_ui_data)
    return parser


def hydrate_defaults(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    if hasattr(args, "league_year") and args.league_year is None:
        args.league_year = default_league_year(con)
    if hasattr(args, "start_date") and args.start_date is None:
        args.start_date = default_start_date(int(args.league_year))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command != "setup":
            with connect(args.db) as con:
                hydrate_defaults(con, args)
        args.func(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
