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
import league_calendar
import player_personalities
from setup_contract_years import rebuild_contract_year, sync_team_cap_space
from setup_transactions_cap_ledger import insert_transaction, snapshot_cap_ledger


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
    "RB": 2,
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
    "RB": 1.02,
    "WR": 1.06,
    "TE": 1.06,
    "OT": 1.12,
    "IOL": 1.14,
    "EDGE": 1.12,
    "IDL": 1.02,
    "LB": 1.05,
    "CB": 1.05,
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
    ("Premium", "RB"): 8_000_000,
    ("Premium", "WR"): 20_000_000,
    ("Premium", "TE"): 13_000_000,
    ("Premium", "OT"): 18_000_000,
    ("Premium", "IOL"): 16_000_000,
    ("Premium", "EDGE"): 18_000_000,
    ("Premium", "IDL"): 16_000_000,
    ("Premium", "LB"): 10_000_000,
    ("Premium", "CB"): 16_000_000,
    ("Premium", "S"): 12_000_000,
    ("Premium", "ST"): 3_200_000,
    ("Starter", "QB"): 7_500_000,
    ("Starter", "RB"): 5_800_000,
    ("Starter", "WR"): 10_500_000,
    ("Starter", "TE"): 7_500_000,
    ("Starter", "OT"): 14_500_000,
    ("Starter", "IOL"): 10_500_000,
    ("Starter", "EDGE"): 12_500_000,
    ("Starter", "IDL"): 9_500_000,
    ("Starter", "LB"): 7_000_000,
    ("Starter", "CB"): 11_000_000,
    ("Starter", "S"): 8_500_000,
    ("Starter", "ST"): 2_500_000,
}

CPU_FA_CAP_RESERVE = 22_000_000
CPU_FA_LATE_MARKET_RESERVE = 8_000_000

CPU_GROUP_SPEND_LIMITS = {
    "QB": 30_000_000,
    "RB": 10_000_000,
    "WR": 38_000_000,
    "TE": 19_000_000,
    "OT": 39_000_000,
    "IOL": 34_000_000,
    "EDGE": 42_000_000,
    "IDL": 32_000_000,
    "LB": 19_000_000,
    "CB": 36_000_000,
    "S": 24_000_000,
    "ST": 6_000_000,
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
            m.status AS market_status,
            m.signed_team_id,
            signed_team.abbreviation AS signed_team,
            profile.previous_team,
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
            return 0.72
        if age >= 29:
            return 0.86
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


def normalized_tier(row: sqlite3.Row | dict[str, Any]) -> str:
    tier = str(row_value(row, "market_tier", "Depth") or "Depth").title()
    if tier in {"Core", "Franchise"}:
        tier = "Premium"
    if tier not in {"Premium", "Starter", "Rotation", "Depth", "Camp"}:
        tier = "Depth"

    group = normalized_group(row)
    score = int(row_value(row, "market_score", row_value(row, "overall", 60)) or 60)
    if group == "QB":
        score_tier = "Premium" if score >= 80 else "Starter" if score >= 72 else "Rotation" if score >= 65 else "Depth" if score >= 58 else "Camp"
    elif group in {"WR", "OT", "EDGE", "CB", "IDL", "IOL"}:
        score_tier = "Premium" if score >= 78 else "Starter" if score >= 72 else "Rotation" if score >= 66 else "Depth" if score >= 60 else "Camp"
    elif group in {"RB", "TE", "LB", "S"}:
        score_tier = "Premium" if score >= 76 else "Starter" if score >= 70 else "Rotation" if score >= 64 else "Depth" if score >= 58 else "Camp"
    elif group == "ST":
        score_tier = "Premium" if score >= 78 else "Starter" if score >= 70 else "Rotation" if score >= 62 else "Depth"
    else:
        score_tier = "Starter" if score >= 72 else "Rotation" if score >= 65 else "Depth" if score >= 58 else "Camp"

    order = {"Camp": 0, "Depth": 1, "Rotation": 2, "Starter": 3, "Premium": 4}
    return tier if order[tier] <= order[score_tier] else score_tier


def market_price_ceiling(row: sqlite3.Row | dict[str, Any], tier: str, *, post_draft: bool) -> int:
    group = normalized_group(row)
    score = int(row_value(row, "market_score", row_value(row, "overall", 60)) or 60)
    if group == "QB":
        if score < 64:
            base = 5_000_000
        elif score < 68:
            base = 8_000_000
        elif score < 72:
            base = 12_000_000
        elif score < 76:
            base = 18_000_000
        elif score < 80:
            base = 28_000_000
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
        "WR": 1.15,
        "OT": 1.18,
        "EDGE": 1.18,
        "CB": 1.06,
        "IDL": 1.03,
        "IOL": 1.05,
        "TE": 0.98,
        "S": 0.92,
        "LB": 0.88,
        "RB": 0.78,
        "ST": 0.42,
    }.get(group, 1.0)
    if group == "WR" and score >= 88:
        group_multiplier = 1.24
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


def cpu_excluded_user_team(con: sqlite3.Connection, args: argparse.Namespace) -> str | None:
    if bool(getattr(args, "cpu_controls_user_team", False)):
        return None
    return contract_negotiations.active_user_team(con)


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


def cpu_true_quality_aav_cap(row: sqlite3.Row | dict[str, Any]) -> int:
    """Cap CPU bids by actual OVR so role-score outliers do not get star money."""
    group = normalized_group(row)
    tier = normalized_tier(row)
    overall = true_overall(row)
    potential = int(row_value(row, "potential", overall) or overall)
    upside_bonus = 1.0 + clamp((potential - overall) * 0.018, 0.0, 0.14)
    tables = {
        "QB": [(64, 5_000_000), (68, 8_500_000), (72, 13_000_000), (76, 20_000_000), (80, 32_000_000), (99, 58_000_000)],
        "RB": [(64, 2_200_000), (68, 3_800_000), (72, 5_800_000), (76, 8_500_000), (80, 12_500_000), (99, 16_000_000)],
        "CB": [(60, 3_500_000), (64, 5_500_000), (68, 8_500_000), (72, 12_000_000), (76, 16_000_000), (80, 22_000_000), (99, 29_000_000)],
        "S": [(60, 3_000_000), (64, 4_800_000), (68, 7_500_000), (72, 10_500_000), (76, 14_500_000), (80, 19_000_000), (99, 24_000_000)],
        "EDGE": [(64, 4_800_000), (68, 8_000_000), (72, 12_500_000), (76, 18_000_000), (80, 25_000_000), (99, 34_000_000)],
        "IDL": [(64, 4_200_000), (68, 7_000_000), (72, 10_500_000), (76, 14_000_000), (80, 20_000_000), (99, 28_000_000)],
        "WR": [(64, 4_000_000), (68, 7_000_000), (72, 10_500_000), (76, 15_500_000), (80, 24_000_000), (99, 36_000_000)],
        "TE": [(64, 3_500_000), (68, 6_000_000), (72, 9_000_000), (76, 13_000_000), (80, 18_000_000), (99, 23_000_000)],
        "OT": [(64, 4_500_000), (68, 8_000_000), (72, 13_500_000), (76, 18_500_000), (80, 25_000_000), (99, 32_000_000)],
        "IOL": [(64, 4_000_000), (68, 7_000_000), (72, 11_500_000), (76, 16_000_000), (80, 22_000_000), (99, 28_000_000)],
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
    score = int(row_value(row, "market_score", row_value(row, "overall", 60)) or 60)
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
        "RB": 0.72,
        "ST": 0.55,
    }.get(group, 0.90)
    score_multiplier = 1.0 + clamp((score - 70) * 0.035, -0.15, 0.55)
    age_penalty = 1.0 - max(0, age - POSITION_OLD_AGE.get(group, 31)) * 0.035
    floor = base * group_multiplier * score_multiplier * clamp(age_penalty, 0.70, 1.0)
    return max(840_000, round_to(floor, 100_000))


def adjusted_market_prices(row: sqlite3.Row, league_year: int, *, post_draft: bool = False) -> tuple[int, int]:
    """Return current open-market asking/minimum AAV for an offseason FA.

    The seeded profile is the baseline personality/market expectation, but the
    actual offseason market should run hotter than a summer street-FA list.
    """
    player_id = int(row["player_id"])
    tier = normalized_tier(row)
    group = normalized_group(row)
    age = int(row["age"]) if row["age"] is not None else None
    score = int(row_value(row, "market_score", 60) or 60)
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
        heat = market_heat_for(
            tier,
            asking_aav,
            int(row["market_score"] or 60),
            int(row["age"]) if row["age"] is not None else None,
        )
        cur = con.execute(
            """
            INSERT INTO free_agency_player_markets (
                league_year, player_id, position_group, market_tier,
                asking_aav, minimum_aav, preferred_years, guarantee_pct,
                market_heat, patience, status, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', datetime('now'))
            ON CONFLICT(league_year, player_id) DO UPDATE SET
                position_group = excluded.position_group,
                market_tier = excluded.market_tier,
                asking_aav = excluded.asking_aav,
                minimum_aav = excluded.minimum_aav,
                preferred_years = excluded.preferred_years,
                guarantee_pct = excluded.guarantee_pct,
                market_heat = excluded.market_heat,
                patience = excluded.patience,
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
    result = {"rostered_markets": 0, "stale_offers": 0, "closed_period": 0, "resolved_before_stale": 0}
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

    period = current_period(con, league_year)
    if period and period["status"] == "active":
        current_date = str(period["current_date"])
    if current_date:
        if period and period["status"] == "active":
            result["resolved_before_stale"] = resolve_pending_offers(
                con,
                period,
                write_cap_snapshot=False,
            )
        cur = con.execute(
            """
            UPDATE free_agency_offers
            SET status = 'expired',
                decided_date = ?,
                decided_hour = NULL,
                notes = COALESCE(notes || ' | ', '') || 'Expired after sitting pending too long.',
                updated_at = datetime('now')
            WHERE league_year = ?
              AND status = 'pending'
              AND julianday(?) - julianday(submitted_date) >= ?
            """,
            (current_date, league_year, current_date, stale_offer_days),
        )
        result["stale_offers"] = int(cur.rowcount or 0)
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
    age = int(player["age"] or 28) if "age" in player.keys() else 28
    if score >= 90:
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
) -> tuple[int, int]:
    tier = str(row_value(player, "market_tier", "Depth") or "Depth").title()
    if tier in {"Core", "Franchise"}:
        tier = "Premium"
    asking = max(1, int(row_value(player, "asking_aav", row_value(player, "minimum_aav", 0)) or 0))
    minimum = max(1, int(row_value(player, "minimum_aav", 0) or 0))
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
    low = max(minimum, int(asking * low_pct), int(best_aav * 0.99))
    high = max(low, int(asking * high_pct), int(best_aav * (1.16 if response_offer else 1.08)))
    cap = max(minimum, cpu_true_quality_aav_cap(player))
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
    for row in rows:
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
    for row in rows:
        old_ask = int(row["asking_aav"] or 0)
        old_min = int(row["minimum_aav"] or 0)
        if old_ask <= 0:
            continue
        rate = no_interest_decay_rate(row, opening_phase_ended=opening_phase_ended, days=max(1, days))
        profile_minimum = max(840_000, int(row["profile_minimum_aav"] or 840_000))
        floor = max(veteran_floor_aav(row, post_draft=post_draft_market), int(profile_minimum * (0.72 if post_draft_market else 1.0)))
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
        kept_for_team = 0
        for player in rows:
            if kept_for_team >= per_team:
                break
            score = float(row_value(player, "market_score", 60) or 60)
            group = str(row_value(player, "position_group", "") or "")
            must_protect = score >= 88 or (score >= 84 and group in {"QB", "OT", "EDGE", "WR", "CB"})
            if not must_protect and rng.random() > cpu_re_sign_probability(player):
                continue
            low, high = cpu_aav_bounds(player)
            aav = round_to(rng.randint(low, high), 100_000)
            years = max(1, int(player["suggested_years"] or 1))
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
            franchise = int(player.get("franchise_tag_aav") or 0)
            transition = int(player.get("transition_tag_aav") or 0)
            if group == "RB":
                eligible = score >= 87 and age <= 27 and franchise <= cap_space - 5_000_000
            elif group == "QB":
                eligible = score >= 78 and franchise <= cap_space - 8_000_000
            elif group in {"WR", "OT", "EDGE", "CB", "IDL", "IOL"}:
                eligible = score >= 81 and franchise <= cap_space - 6_000_000
            else:
                eligible = score >= 84 and franchise <= cap_space - 6_000_000
            transition_ok = score >= 77 and group not in {"RB", "ST"} and transition <= cap_space - 5_000_000
            if eligible:
                surplus = max(0, int(player.get("asking_aav") or 0) - franchise)
                if score >= 90:
                    probability = 0.98
                elif score >= 86 and group in {"QB", "WR", "OT", "EDGE", "CB", "IDL"}:
                    probability = 0.90
                else:
                    probability = 0.52 + clamp((score - 82) * 0.04, 0.0, 0.28) + clamp(surplus / 12_000_000, 0.0, 0.18)
                if rng.random() <= min(0.98, probability):
                    candidates.append((score + surplus / 1_000_000, "franchise", player))
            elif transition_ok:
                probability = 0.36 + clamp((score - 78) * 0.04, 0.0, 0.26)
                if rng.random() <= min(0.68, probability):
                    candidates.append((score, "transition", player))
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
    group = str(row_value(player, "position_group", "") or "")
    age = int(row_value(player, "age", 24) or 24)
    status = str(row_value(player, "status", "") or "")
    if rights_type == "ERFA":
        if score >= 55 or status == "Active" or rng.random() < 0.62:
            return "erfa"
        return None
    if rights_type != "RFA":
        return None
    if score >= 78 or (group in {"QB", "OT", "EDGE", "CB", "WR"} and score >= 74):
        return "rfa_first" if rng.random() < 0.55 else "rfa_second"
    if score >= 70:
        return "rfa_second" if rng.random() < 0.72 else "rfa_original"
    if score >= 63:
        return "rfa_original" if rng.random() < 0.62 else "rfa_rofr"
    if score >= 58 and age <= 25:
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
    write_cap_snapshot = not getattr(args, "no_cap_snapshot", False)
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
    else:
        rollover_result = {"teams": []}
        cpu_options = 0
        cpu_tags = 0
        cpu_tenders = 0
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
    cpu_cap_releases = cpu_release_bad_contracts_for_fa(con, period, user_team=user_team)
    cpu_retained = cpu_retain_own_free_agents(
        con,
        period,
        user_team=user_team,
        per_team=args.cpu_retention_per_team,
        seed=args.seed,
        write_cap_snapshot=write_cap_snapshot,
    )
    opening_cpu_offers = create_cpu_offers(con, period, args.opening_cpu_offers, args.seed, user_team=user_team)
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
            f"CPU own-player FA re-signings: {cpu_retained}. "
            f"Opening CPU offers: {opening_cpu_offers}. "
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
    notes: str | None = None,
) -> int:
    total_value = int(aav) * int(years)
    cur = con.execute(
        """
        INSERT INTO free_agency_offers (
            league_year, player_id, team_id, years, aav, total_value,
            signing_bonus, guarantee_pct, status, submitted_date,
            submitted_hour, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
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
            f"{args.years} year(s), {money(aav)} AAV."
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
            p.team_id,
            p.position,
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
    competition: dict[tuple[int, str], list[float]] = {}
    for row in rows:
        key = (int(row["team_id"]), position_group_for(str(row["position"])))
        competition.setdefault(key, []).append(float(row["player_score"] or 50))
    for scores in competition.values():
        scores.sort(reverse=True)
    return competition


def load_team_need_scores(con: sqlite3.Connection) -> dict[tuple[int, str], float]:
    rows = con.execute(
        """
        SELECT
            team_id,
            position,
            COALESCE(overall, 50) AS overall
        FROM players
        WHERE team_id IS NOT NULL
          AND status IN ('Active', 'Reserve/Future', 'PUP', 'IR')
        """
    ).fetchall()
    rooms: dict[tuple[int, str], list[float]] = {}
    for row in rows:
        group = position_group_for(str(row["position"]))
        rooms.setdefault((int(row["team_id"]), group), []).append(float(row["overall"] or 50))
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

    if aav >= int(asking * 1.08):
        return True
    if aav < minimum:
        return False
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

    season = int(period["league_year"])
    signed_date = str(period["current_date"])
    before = roster_actions.cap_row(con, int(team["team_id"]))
    projected_first_year_cost = int(offer["aav"]) + int(int(offer["signing_bonus"] or 0) / max(1, min(5, int(offer["years"] or 1))))
    if int(before["cap_space"] or 0) < projected_first_year_cost:
        raise ValueError(f"{team['abbreviation']} does not have enough practical cap room for this offer.")
    if str(offer["notes"] or "").startswith("CPU"):
        reserve = cpu_cap_reserve_for_period(period)
        if int(before["cap_space"] or 0) - reserve < projected_first_year_cost:
            raise ValueError(f"{team['abbreviation']} does not have enough CPU reserve cap room for this offer.")

    cur = con.execute(
        """
        INSERT INTO contracts (
            player_id, team_id, signed_date, start_year, end_year, total_value,
            total_years, aav, signing_bonus, roster_bonus, workout_bonus,
            is_guaranteed, dead_cap_current, dead_cap_next, contract_type, is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 0, 0, 'Standard', 1)
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
        ),
    )
    contract_id = int(cur.lastrowid)
    con.execute(
        "UPDATE players SET team_id = ?, status = 'Active' WHERE player_id = ?",
        (offer["team_id"], offer["player_id"]),
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
        years = preferred_years_for_offer(player, rng, max_years=5)
        bonus = round_to(aav * years * rng.uniform(0.03, 0.12))
        guarantee = guarantee_for_preference(player, int(player["guarantee_pct"] or 0), rng)
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
            notes="CPU own-player retention offer",
        )
        contract_id = con.execute(
            """
            INSERT INTO contracts (
                player_id, team_id, signed_date, start_year, end_year, total_value,
                total_years, aav, signing_bonus, roster_bonus, workout_bonus,
                is_guaranteed, dead_cap_current, dead_cap_next, contract_type, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 0, 0, 'Standard', 1)
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
            ),
        ).lastrowid
        contract_ids.append(int(contract_id))
        con.execute(
            "UPDATE players SET team_id = ?, status = 'Active' WHERE player_id = ?",
            (int(player["previous_team_id"]), int(player["player_id"])),
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
    player_group = position_group_for(str(row_value(player, "position_group", row_value(player, "position", ""))))
    best_aav = int(player["best_aav"] or 0)
    ask = int(player["asking_aav"] or player["minimum_aav"] or 0)
    minimum = int(player["minimum_aav"] or 0)
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
        low, high = cpu_aav_bounds(player, best_aav=best_aav, response_offer=True)
        max_room = max(0, int(team["cap_space"] or 0) - 1_000_000)
        if low > max_room:
            continue
        high = min(high, max_room)
        team_need = need_scores.get((int(team["team_id"]), player_group), 0.0)
        quality_cap = cpu_true_quality_aav_cap(player)
        if team_need < 18:
            continue
        if team_need < 32:
            quality_cap = int(quality_cap * 0.86)
        if player_group == "RB" and team_need < 45:
            quality_cap = int(quality_cap * 0.82)
        quality_cap = round_to(max(minimum, quality_cap), 50_000)
        if low > quality_cap:
            continue
        high = min(high, max(low, quality_cap))
        aav = preference_adjusted_aav(player, round_to(rng.randint(low, high)), rng)
        aav = min(aav, max(low, quality_cap))
        years = preferred_years_for_offer(player, rng, max_years=5)
        bonus = round_to(aav * years * rng.uniform(0.04, 0.16))
        guarantee = guarantee_for_preference(player, int(player["guarantee_pct"] or 0) + 8, rng)
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
    if tier in {"Premium", "Starter"} and heat >= 82:
        target = 3 if rng.random() < 0.62 else 2
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
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.overall
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
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.overall
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
        (team_id, league_year, max(limit * 3, limit)),
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


def cpu_cap_compliance_sweep(
    con: sqlite3.Connection,
    league_year: int,
    *,
    user_team: str | None = None,
    min_space: int = 1_000_000,
    max_moves_per_team: int = 3,
    max_teams: int = 10,
    time_budget_seconds: float = 25.0,
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
                restructures = quick_restructure_candidates(con, team_id, league_year, limit=8)
            except Exception:
                restructures = []
            for candidate in restructures:
                group = position_group_for(str(candidate.get("position") or ""))
                overall = int(candidate.get("overall") or candidate.get("market_score") or 60)
                age = int(candidate.get("age") or 28)
                savings = int(candidate.get("estimated_current_savings") or 0)
                remaining_years = int(candidate.get("remaining_contract_years") or 1)
                core_player = (
                    (group == "QB" and overall >= 76 and age <= 35)
                    or (group in {"WR", "TE", "OT", "IOL", "EDGE", "IDL", "CB"} and overall >= 78 and age <= 31)
                    or (group in {"LB", "S"} and overall >= 79 and age <= 30)
                    or (group == "RB" and overall >= 82 and age <= 27)
                )
                if not core_player or savings < 2_000_000 or remaining_years < 2:
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
                releases = quick_cap_release_candidates(con, team_id, league_year, limit=10)
            except Exception:
                releases = []
            for candidate in releases:
                group = position_group_for(str(candidate.get("position") or ""))
                overall = int(candidate.get("overall") or candidate.get("market_score") or 60)
                savings = int(candidate.get("net_savings_pre_june1") or 0)
                cap_hit = int(candidate.get("cap_hit") or 0)
                age = int(candidate.get("age") or 28)
                if savings < 1_500_000 or cap_hit < 2_500_000:
                    continue
                if group == "QB" and overall >= 66:
                    continue
                if overall >= 80:
                    continue
                if overall >= 76 and (savings < 7_000_000 or age <= 29):
                    continue
                if overall >= 72 and savings < 4_000_000:
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
    group_counts = load_team_group_counts(con)
    early_wave = str(period["current_stage"] or "") == "day_one_hourly" and int(period["day_count"] or 1) <= 1
    late_market = cpu_late_market(period)
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
        rng.shuffle(candidates)
    for player in candidates:
        player_group = position_group_for(str(row_value(player, "position_group", row_value(player, "position", ""))))
        player_score = float(row_value(player, "market_score", row_value(player, "overall", 60)) or 60)
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
        for team in affordable_teams[:slots]:
            team_id = int(team["team_id"])
            team_need = need_scores.get((int(team["team_id"]), player_group), 0.0)
            group_key = (team_id, player_group)
            group_spend = team_group_spend.get(group_key, 0)
            group_offer_count = team_group_offers.get(group_key, 0)
            group_spend_limit = CPU_GROUP_SPEND_LIMITS.get(player_group, 30_000_000)
            group_count_limit = CPU_GROUP_OFFER_COUNT_LIMITS.get(player_group, 2)
            roster_group_count = group_counts.get(group_key, 0)
            group_depth_limit = CPU_GROUP_DEPTH_COUNT_LIMITS.get(player_group, ROOM_IDEAL_BY_GROUP.get(player_group, 5) + 1)
            late_need_exception = late_market and top_remaining and team_need >= 28
            if player_group == "QB":
                if roster_group_count >= group_depth_limit and team_need < 70:
                    continue
                if group_offer_count >= 1 and team_need < 70:
                    continue
                if group_spend > 0 and team_need < 75:
                    continue
            elif roster_group_count >= group_depth_limit and group_offer_count >= 1 and team_need < 40 and not late_need_exception:
                continue
            if group_offer_count >= group_count_limit and team_need < 48 and not late_need_exception:
                continue
            if group_spend >= group_spend_limit and team_need < 62 and not late_need_exception:
                continue
            if early_wave:
                is_top_market = normalized_tier(player) in {"Premium", "Starter"} or int(row_value(player, "market_heat", 0) or 0) >= 72
                if is_top_market and team_need < 24 and player_score >= 68:
                    continue
                if player_score >= 74 and team_need < 16:
                    continue
                if player_group not in {"QB", "OT", "IOL", "EDGE", "CB", "WR"} and team_need < 30 and player_score >= 70:
                    continue
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

            low, high = cpu_aav_bounds(player)
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
            else:
                if team_need < 18:
                    quality_cap = int(quality_cap * 0.72)
                elif team_need < 32:
                    quality_cap = int(quality_cap * 0.86)
            if player_group == "RB" and team_need < 45:
                quality_cap = int(quality_cap * 0.82)
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
            years = preferred_years_for_offer(player, rng, max_years=5)
            if player_group == "QB" and player_score < 72:
                years = min(years, 2)
            if player_group == "QB" and player_score < 75 and team_need < 75:
                years = min(years, 1)
                aav = min(aav, max(low, round_to(quality_cap * 0.92, 50_000)))
            if player_group == "RB":
                years = min(years, 2 if player_score < 78 else 3)
            bonus = int(round((aav * years * rng.uniform(0.03, 0.18)) / 50_000) * 50_000)
            guarantee = guarantee_for_preference(player, int(player["guarantee_pct"] or 0) + 5, rng)
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
                notes="CPU market offer",
            )
            offers_by_team[team_id] = offers_by_team.get(team_id, 0) + 1
            team_spend[team_id] = team_spend.get(team_id, 0) + aav
            team_group_spend[group_key] = team_group_spend.get(group_key, 0) + aav
            team_group_offers[group_key] = team_group_offers.get(group_key, 0) + 1
            group_counts[group_key] = group_counts.get(group_key, 0) + 1
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
    period = active_period(con, args.league_year)
    if days and period["current_stage"] == "day_one_hourly" and not args.force:
        raise ValueError("Day 1 is still in hourly mode. Use advance-hour, or pass --force to jump to daily.")

    user_team = cpu_excluded_user_team(con, args)
    cpu_restructures = cpu_restructure_core_contracts_for_fa(con, period, user_team=user_team, max_total=8)
    cpu_cap_releases = cpu_release_bad_contracts_for_fa(con, period, user_team=user_team, max_total=8)
    created = create_cpu_offers(con, period, args.cpu_offers, args.seed, user_team=user_team)
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
    if fresh_period_for_resolution and fresh_period_for_resolution["status"] == "active":
        signed_after_advance = resolve_pending_offers(
            con,
            fresh_period_for_resolution,
            limit=args.signing_limit,
            write_cap_snapshot=False,
        )
        signed += signed_after_advance
    cleanup = reconcile_market_state(con, args.league_year)
    fresh_period = active_period(con, args.league_year)
    log_event(
        con,
        league_year=args.league_year,
        event_date=str(fresh_period["current_date"]),
        event_hour=int(fresh_period["current_hour"]) if fresh_period["current_stage"] == "day_one_hourly" else None,
        event_type="market_advanced",
        message=f"Free agency advanced. CPU offers: {created}. Signings: {signed}. Demand drops: {demand_drops}. Retirements: {retirements}.",
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
        "signings": signed,
        "demand_drops": demand_drops,
        "retirements": retirements,
        "restructures": cpu_restructures,
        "cap_releases": cpu_cap_releases,
        "post_advance_signings": signed_after_advance,
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
        f"{result['cpu_offers']} CPU offer(s), {result['signings']} signing(s), "
        f"{result['demand_drops']} demand drop(s), {result.get('retirements', 0)} retirement(s)."
    )


def action_advance_day(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = run_mutation(con, args, lambda c, a: process_tick(c, a, days=args.days))
    print(
        f"Advanced {args.days} free-agency day(s) ({'saved' if args.apply else 'dry run'}): "
        f"{result['cpu_offers']} CPU offer(s), {result['signings']} signing(s), "
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
            retained = cpu_retain_own_free_agents(
                c,
                period,
                user_team=user_team,
                per_team=a.cpu_retention_per_team,
                seed=a.seed,
                write_cap_snapshot=not getattr(a, "no_cap_snapshot", False),
            )
            offers = create_cpu_offers(c, period, a.cpu_offers, a.seed, user_team=user_team)
            log_event(
                c,
                league_year=a.league_year,
                event_date=str(period["current_date"]),
                event_hour=int(period["current_hour"]) if period["current_stage"] == "day_one_hourly" else None,
                event_type="cpu_market_seed",
                message=f"Seeded CPU market activity. Own-player re-signings: {retained}. Open offers: {offers}.",
            )
            return {"retained": retained, "cpu_offers": offers}

        result = run_mutation(con, args, run_seed)
    print(
        f"CPU market seed ({'saved' if args.apply else 'dry run'}): "
        f"{result['retained']} own-player re-signing(s), {result['cpu_offers']} open offer(s)."
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
