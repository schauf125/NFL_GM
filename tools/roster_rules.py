#!/usr/bin/env python3
"""Roster rule tables and validation for NFL GM Sim.

This keeps league roster limits in data instead of hardcoding them into roster
actions. The validator is intentionally advisory: it reports rule failures and
warnings, but it does not cut, sign, or move players.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from setup_contract_years import ensure_schema as ensure_contract_schema
from setup_transactions_cap_ledger import ensure_schema as ensure_transaction_schema
from setup_transactions_cap_ledger import insert_transaction


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_SEASON = 2026
DEFAULT_PHASE = "Preseason"

NFL_OPS_CONTRACT_URL = (
    "https://operations.nfl.com/inside-football-ops/nfl-operations/"
    "nfl-free-agency/contract-language/"
)
NFL_OPS_IPP_URL = (
    "https://operations.nfl.com/updates/football-ops/"
    "nfl-to-expand-practice-squad-to-include-one-international-player-for-all-32-clubs-in-2024/"
)
TITANS_CAMP_PREVIEW_URL = (
    "https://static.clubs.nfl.com/image/upload/titans/pd5pu0hr5pjqndze1dr2"
)
SOURCE = "roster_rules"
DEFAULT_WAIVER_HOURS = 24
PRACTICE_SQUAD_STATUS = "Practice Squad"
WAIVED_STATUS = "Waived"
PRACTICE_SQUAD_DEVELOPMENTAL_LIMIT = 10
PRACTICE_SQUAD_VETERAN_EXCEPTION_LIMIT = 6
PRACTICE_SQUAD_ELEVATION_LIMIT = 3
PRACTICE_SQUAD_WEEKLY_ELEVATION_LIMIT = 2


@dataclass(frozen=True)
class RuleSetSeed:
    season: int
    phase: str
    rule_set_name: str
    active_roster_limit: int
    total_roster_limit: int
    practice_squad_limit: int
    practice_squad_international_exemption_limit: int
    practice_squad_developmental_limit: int
    practice_squad_veteran_exception_limit: int
    practice_squad_elevation_limit: int
    practice_squad_weekly_elevation_limit: int
    practice_squad_enabled: int
    game_day_active_limit: int
    game_day_active_limit_without_min_ol: int
    game_day_min_offensive_linemen: int
    top51_count: int
    salary_cap_mode: str
    source_name: str
    source_url: str
    notes: str


@dataclass(frozen=True)
class PositionRuleSeed:
    position_group: str
    group_label: str
    positions: tuple[str, ...]
    min_count: int
    recommended_min: int
    recommended_max: int
    max_count: int
    severity: str
    notes: str


RULE_SETS = [
    RuleSetSeed(
        season=2026,
        phase="Preseason",
        rule_set_name="2026 NFL Preseason / Training Camp",
        active_roster_limit=90,
        total_roster_limit=90,
        practice_squad_limit=0,
        practice_squad_international_exemption_limit=1,
        practice_squad_developmental_limit=0,
        practice_squad_veteran_exception_limit=0,
        practice_squad_elevation_limit=PRACTICE_SQUAD_ELEVATION_LIMIT,
        practice_squad_weekly_elevation_limit=PRACTICE_SQUAD_WEEKLY_ELEVATION_LIMIT,
        practice_squad_enabled=0,
        game_day_active_limit=90,
        game_day_active_limit_without_min_ol=90,
        game_day_min_offensive_linemen=0,
        top51_count=51,
        salary_cap_mode="TOP_51_ALWAYS",
        source_name="NFL club training camp roster rules",
        source_url=TITANS_CAMP_PREVIEW_URL,
        notes=(
            "Preseason/training camp phase. Clubs may carry up to 90 players, "
            "or 91 with an International Pathway exemption. Practice squads are "
            "not established until after final cutdown."
        ),
    ),
    RuleSetSeed(
        season=2026,
        phase="Regular Season",
        rule_set_name="2026 NFL Regular Season",
        active_roster_limit=53,
        total_roster_limit=90,
        practice_squad_limit=16,
        practice_squad_international_exemption_limit=1,
        practice_squad_developmental_limit=PRACTICE_SQUAD_DEVELOPMENTAL_LIMIT,
        practice_squad_veteran_exception_limit=PRACTICE_SQUAD_VETERAN_EXCEPTION_LIMIT,
        practice_squad_elevation_limit=PRACTICE_SQUAD_ELEVATION_LIMIT,
        practice_squad_weekly_elevation_limit=PRACTICE_SQUAD_WEEKLY_ELEVATION_LIMIT,
        practice_squad_enabled=1,
        game_day_active_limit=48,
        game_day_active_limit_without_min_ol=47,
        game_day_min_offensive_linemen=8,
        top51_count=51,
        salary_cap_mode="TOP_51_ALWAYS",
        source_name="NFL Football Operations roster sizes",
        source_url=NFL_OPS_CONTRACT_URL,
        notes=(
            "Regular-season active/inactive list is 53. Gameday active limit is "
            "48 with eight active offensive linemen, otherwise 47. Practice squad "
            "limit is 16, with up to 10 developmental players, up to six unlimited-experience "
            "veteran exceptions, plus one qualifying international player exemption."
        ),
    ),
]


PRESEASON_POSITION_RULES = [
    PositionRuleSeed("QB", "Quarterbacks", ("QB",), 2, 3, 5, 6, "WARNING", "Camp roster should have enough passers for reps."),
    PositionRuleSeed("RB", "Running Backs", ("RB", "FB"), 3, 5, 9, 10, "WARNING", "Includes fullbacks."),
    PositionRuleSeed("WR", "Wide Receivers", ("WR",), 5, 8, 14, 15, "WARNING", "Camp rosters commonly carry a large receiver group."),
    PositionRuleSeed("TE", "Tight Ends", ("TE",), 3, 4, 8, 9, "WARNING", "Includes inline and move tight ends."),
    PositionRuleSeed("OL", "Offensive Line", ("OT", "OG", "C"), 8, 13, 18, 22, "WARNING", "Includes tackles, guards, and centers."),
    PositionRuleSeed("EDGE", "Edge Rushers", ("EDGE", "DE"), 3, 5, 12, 13, "WARNING", "Outside pass-rush group."),
    PositionRuleSeed("IDL", "Interior Defensive Line", ("IDL", "DT", "NT"), 3, 5, 11, 12, "WARNING", "Includes nose tackles."),
    PositionRuleSeed("LB", "Linebackers", ("ILB", "OLB", "LB"), 4, 6, 11, 12, "WARNING", "Off-ball linebacker group."),
    PositionRuleSeed("CB", "Cornerbacks", ("CB", "NB"), 5, 8, 12, 13, "WARNING", "Includes nickel corners."),
    PositionRuleSeed("S", "Safeties", ("SS", "FS", "S"), 4, 6, 10, 11, "WARNING", "Includes strong/free/general safety labels."),
    PositionRuleSeed("K", "Kickers", ("K",), 1, 1, 2, 3, "ERROR", "At least one kicker is required."),
    PositionRuleSeed("P", "Punters", ("P",), 1, 1, 2, 3, "ERROR", "At least one punter is required."),
    PositionRuleSeed("LS", "Long Snappers", ("LS",), 1, 1, 2, 3, "ERROR", "At least one long snapper is required."),
]


REGULAR_SEASON_POSITION_RULES = [
    PositionRuleSeed("QB", "Quarterbacks", ("QB",), 2, 2, 3, 4, "WARNING", "Most teams carry two or three quarterbacks."),
    PositionRuleSeed("RB", "Running Backs", ("RB", "FB"), 3, 3, 5, 6, "WARNING", "Includes fullbacks."),
    PositionRuleSeed("WR", "Wide Receivers", ("WR",), 5, 5, 7, 8, "WARNING", "Typical active roster range."),
    PositionRuleSeed("TE", "Tight Ends", ("TE",), 2, 3, 4, 5, "WARNING", "Typical active roster range."),
    PositionRuleSeed("OL", "Offensive Line", ("OT", "OG", "C"), 8, 8, 10, 11, "WARNING", "Eight OL matters for the 48-player gameday limit."),
    PositionRuleSeed("EDGE", "Edge Rushers", ("EDGE", "DE"), 3, 4, 6, 7, "WARNING", "Outside pass-rush group."),
    PositionRuleSeed("IDL", "Interior Defensive Line", ("IDL", "DT", "NT"), 3, 4, 6, 7, "WARNING", "Includes nose tackles."),
    PositionRuleSeed("LB", "Linebackers", ("ILB", "OLB", "LB"), 4, 5, 7, 8, "WARNING", "Off-ball linebacker group."),
    PositionRuleSeed("CB", "Cornerbacks", ("CB", "NB"), 5, 5, 7, 8, "WARNING", "Includes nickel corners."),
    PositionRuleSeed("S", "Safeties", ("SS", "FS", "S"), 4, 4, 6, 7, "WARNING", "Includes strong/free/general safety labels."),
    PositionRuleSeed("K", "Kickers", ("K",), 1, 1, 1, 2, "ERROR", "Regular-season roster usually carries one kicker."),
    PositionRuleSeed("P", "Punters", ("P",), 1, 1, 1, 2, "ERROR", "Regular-season roster usually carries one punter."),
    PositionRuleSeed("LS", "Long Snappers", ("LS",), 1, 1, 1, 2, "ERROR", "Regular-season roster usually carries one long snapper."),
]


POSITION_RULES_BY_PHASE = {
    "Preseason": PRESEASON_POSITION_RULES,
    "Regular Season": REGULAR_SEASON_POSITION_RULES,
}


def format_money(amount: int | None) -> str:
    if amount is None:
        return "-"
    if amount < 0:
        return "-" + format_money(abs(amount))
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    return f"${amount}"


def current_season(con: sqlite3.Connection) -> int:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_season'"
    ).fetchone()
    return int(row["setting_value"]) if row else DEFAULT_SEASON


def normalize_phase(value: str) -> str:
    text = value.strip().lower().replace("_", " ").replace("-", " ")
    aliases = {
        "preseason": "Preseason",
        "training camp": "Preseason",
        "camp": "Preseason",
        "regular": "Regular Season",
        "regular season": "Regular Season",
        "season": "Regular Season",
    }
    if text not in aliases:
        raise ValueError(f"Unknown roster phase: {value}")
    return aliases[text]


def ensure_column(con: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row["name"] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    ).fetchone() is not None


def ensure_schema(con: sqlite3.Connection) -> None:
    ensure_contract_schema(con)
    ensure_transaction_schema(con)
    con.executescript(
        """
        PRAGMA foreign_keys = ON;

        INSERT INTO roster_status_types (
            status_code, display_name, counts_against_top51,
            counts_against_regular_cap, counts_against_roster_limit,
            counts_against_practice_squad_limit, description
        )
        VALUES
            ('Questionable', 'Questionable / Active Roster', 1, 1, 1, 0, 'Player is on the active roster but has questionable game availability.'),
            ('Doubtful', 'Doubtful / Active Roster', 1, 1, 1, 0, 'Player is on the active roster but is unlikely to play this week.'),
            ('Out', 'Out / Active Roster', 1, 1, 1, 0, 'Player is unavailable for the week but still occupies an active roster spot.'),
            ('Waived', 'Waived / Pending Claims', 0, 0, 0, 0, 'Player has been waived and is waiting for waiver claims to process.')
        ON CONFLICT(status_code) DO UPDATE SET
            display_name = excluded.display_name,
            counts_against_top51 = excluded.counts_against_top51,
            counts_against_regular_cap = excluded.counts_against_regular_cap,
            counts_against_roster_limit = excluded.counts_against_roster_limit,
            counts_against_practice_squad_limit = excluded.counts_against_practice_squad_limit,
            description = excluded.description;

        INSERT INTO transaction_types (transaction_type, category, description)
        VALUES
            ('Waiver', 'Roster', 'Player placed on waivers.'),
            ('Waiver Cleared', 'Roster', 'Player cleared waivers and became a free agent.'),
            ('Practice Squad Signing', 'Roster', 'Player signed to a practice squad.'),
            ('Practice Squad Release', 'Roster', 'Practice squad player released.'),
            ('Practice Squad Elevation', 'Roster', 'Practice squad player elevated to the active roster.'),
            ('Practice Squad Return', 'Roster', 'Elevated player returned to the practice squad.')
        ON CONFLICT(transaction_type) DO UPDATE SET
            category = excluded.category,
            description = excluded.description;

        CREATE TABLE IF NOT EXISTS roster_rule_sets (
            rule_set_id INTEGER PRIMARY KEY AUTOINCREMENT,
            season INTEGER NOT NULL,
            phase TEXT NOT NULL,
            rule_set_name TEXT NOT NULL,
            active_roster_limit INTEGER NOT NULL,
            total_roster_limit INTEGER NOT NULL,
            practice_squad_limit INTEGER NOT NULL,
            practice_squad_international_exemption_limit INTEGER NOT NULL DEFAULT 0,
            practice_squad_developmental_limit INTEGER NOT NULL DEFAULT 10,
            practice_squad_veteran_exception_limit INTEGER NOT NULL DEFAULT 6,
            practice_squad_elevation_limit INTEGER NOT NULL DEFAULT 3,
            practice_squad_weekly_elevation_limit INTEGER NOT NULL DEFAULT 2,
            practice_squad_enabled INTEGER NOT NULL DEFAULT 0,
            game_day_active_limit INTEGER NOT NULL,
            game_day_active_limit_without_min_ol INTEGER NOT NULL,
            game_day_min_offensive_linemen INTEGER NOT NULL DEFAULT 0,
            top51_count INTEGER NOT NULL DEFAULT 51,
            salary_cap_mode TEXT NOT NULL DEFAULT 'TOP_51_ALWAYS',
            source_name TEXT,
            source_url TEXT,
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(season, phase)
        );

        CREATE TABLE IF NOT EXISTS roster_position_rules (
            position_rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_set_id INTEGER NOT NULL REFERENCES roster_rule_sets(rule_set_id) ON DELETE CASCADE,
            position_group TEXT NOT NULL,
            group_label TEXT NOT NULL,
            min_count INTEGER NOT NULL DEFAULT 0,
            recommended_min INTEGER NOT NULL DEFAULT 0,
            recommended_max INTEGER NOT NULL DEFAULT 0,
            max_count INTEGER NOT NULL DEFAULT 0,
            severity TEXT NOT NULL DEFAULT 'WARNING',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(rule_set_id, position_group)
        );

        CREATE TABLE IF NOT EXISTS roster_position_group_members (
            position_member_id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_set_id INTEGER NOT NULL REFERENCES roster_rule_sets(rule_set_id) ON DELETE CASCADE,
            position_group TEXT NOT NULL,
            position TEXT NOT NULL,
            UNIQUE(rule_set_id, position_group, position)
        );

        CREATE TABLE IF NOT EXISTS roster_validation_runs (
            validation_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            season INTEGER NOT NULL,
            phase TEXT NOT NULL,
            rule_set_id INTEGER NOT NULL REFERENCES roster_rule_sets(rule_set_id) ON DELETE CASCADE,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            active_count INTEGER NOT NULL DEFAULT 0,
            practice_squad_count INTEGER NOT NULL DEFAULT 0,
            total_controlled_count INTEGER NOT NULL DEFAULT 0,
            salary_cap INTEGER NOT NULL DEFAULT 0,
            total_committed INTEGER NOT NULL DEFAULT 0,
            cap_space INTEGER NOT NULL DEFAULT 0,
            passed INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            warning_count INTEGER NOT NULL DEFAULT 0,
            info_count INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'roster_rules_validate',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS roster_validation_issues (
            validation_issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
            validation_run_id INTEGER NOT NULL REFERENCES roster_validation_runs(validation_run_id) ON DELETE CASCADE,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            severity TEXT NOT NULL,
            issue_code TEXT NOT NULL,
            position_group TEXT,
            actual_value INTEGER,
            expected_value INTEGER,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS waiver_priority (
            season INTEGER NOT NULL,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            priority INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'seeded',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(season, team_id),
            UNIQUE(season, priority)
        );

        CREATE TABLE IF NOT EXISTS waiver_wire (
            waiver_id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            original_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            waiver_date TEXT NOT NULL,
            claim_deadline TEXT NOT NULL,
            season INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'Open',
            reason TEXT,
            source TEXT NOT NULL DEFAULT 'roster_rules',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_waiver_wire_status
            ON waiver_wire(status, claim_deadline, season);

        CREATE TABLE IF NOT EXISTS waiver_claims (
            claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
            waiver_id INTEGER NOT NULL REFERENCES waiver_wire(waiver_id) ON DELETE CASCADE,
            claiming_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            claim_order INTEGER NOT NULL,
            claim_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(waiver_id, claiming_team_id)
        );

        CREATE INDEX IF NOT EXISTS idx_waiver_claims_wire_order
            ON waiver_claims(waiver_id, status, claim_order);

        CREATE TABLE IF NOT EXISTS practice_squad_moves (
            move_id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            move_date TEXT NOT NULL,
            week INTEGER,
            move_type TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_practice_squad_moves_player
            ON practice_squad_moves(player_id, season, move_type);

        CREATE INDEX IF NOT EXISTS idx_roster_validation_runs_team
            ON roster_validation_runs(team_id, season, phase, created_at);

        CREATE INDEX IF NOT EXISTS idx_roster_validation_issues_run
            ON roster_validation_issues(validation_run_id, severity);

        DROP VIEW IF EXISTS team_roster_counts_view;
        CREATE VIEW team_roster_counts_view AS
        SELECT
            t.team_id,
            t.abbreviation,
            t.city,
            t.nickname,
            COALESCE(SUM(
                CASE
                    WHEN p.player_id IS NULL THEN 0
                    ELSE COALESCE(
                        rst.counts_against_roster_limit,
                        CASE WHEN p.status <> 'Free Agent' THEN 1 ELSE 0 END
                    )
                END
            ), 0) AS active_roster_count,
            COALESCE(SUM(
                CASE
                    WHEN p.player_id IS NULL THEN 0
                    ELSE COALESCE(rst.counts_against_practice_squad_limit, 0)
                END
            ), 0) AS practice_squad_count,
            COALESCE(SUM(
                CASE
                    WHEN p.player_id IS NULL THEN 0
                    WHEN p.status IN ('Free Agent', 'Retired') THEN 0
                    ELSE 1
                END
            ), 0) AS total_controlled_count,
            COALESCE(SUM(CASE WHEN rst.status_code IS NULL AND p.player_id IS NOT NULL THEN 1 ELSE 0 END), 0)
                AS unknown_status_count
        FROM teams t
        LEFT JOIN players p ON p.team_id = t.team_id
        LEFT JOIN roster_status_types rst ON rst.status_code = p.status
        GROUP BY t.team_id, t.abbreviation, t.city, t.nickname;

        DROP VIEW IF EXISTS team_position_group_counts_view;
        CREATE VIEW team_position_group_counts_view AS
        SELECT
            rrs.rule_set_id,
            rrs.season,
            rrs.phase,
            t.team_id,
            t.abbreviation,
            rpr.position_group,
            rpr.group_label,
            rpr.min_count,
            rpr.recommended_min,
            rpr.recommended_max,
            rpr.max_count,
            rpr.severity,
            COUNT(p.player_id) AS player_count
        FROM roster_rule_sets rrs
        JOIN roster_position_rules rpr ON rpr.rule_set_id = rrs.rule_set_id
        CROSS JOIN teams t
        LEFT JOIN roster_position_group_members rpgm
            ON rpgm.rule_set_id = rpr.rule_set_id
           AND rpgm.position_group = rpr.position_group
        LEFT JOIN players p
            ON p.team_id = t.team_id
           AND p.position = rpgm.position
           AND COALESCE((
                SELECT rst.counts_against_roster_limit
                FROM roster_status_types rst
                WHERE rst.status_code = p.status
           ), CASE WHEN p.status <> 'Free Agent' THEN 1 ELSE 0 END) = 1
        GROUP BY
            rrs.rule_set_id, rrs.season, rrs.phase, t.team_id, t.abbreviation,
            rpr.position_group, rpr.group_label, rpr.min_count,
            rpr.recommended_min, rpr.recommended_max, rpr.max_count, rpr.severity;

        DROP VIEW IF EXISTS waiver_wire_view;
        CREATE VIEW waiver_wire_view AS
        SELECT
            ww.waiver_id,
            ww.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            ww.original_team_id,
            original.abbreviation AS original_team,
            ww.waiver_date,
            ww.claim_deadline,
            ww.season,
            ww.status,
            ww.reason,
            COUNT(wc.claim_id) AS claim_count,
            ww.created_at,
            ww.resolved_at
        FROM waiver_wire ww
        JOIN players p ON p.player_id = ww.player_id
        LEFT JOIN teams original ON original.team_id = ww.original_team_id
        LEFT JOIN waiver_claims wc ON wc.waiver_id = ww.waiver_id
        GROUP BY ww.waiver_id;

        DROP VIEW IF EXISTS waiver_claims_view;
        CREATE VIEW waiver_claims_view AS
        SELECT
            wc.claim_id,
            wc.waiver_id,
            wc.claiming_team_id,
            t.abbreviation AS claiming_team,
            wc.claim_order,
            wc.claim_date,
            wc.status,
            wc.notes,
            ww.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            ww.original_team_id,
            original.abbreviation AS original_team
        FROM waiver_claims wc
        JOIN waiver_wire ww ON ww.waiver_id = wc.waiver_id
        JOIN players p ON p.player_id = ww.player_id
        JOIN teams t ON t.team_id = wc.claiming_team_id
        LEFT JOIN teams original ON original.team_id = ww.original_team_id;

        DROP VIEW IF EXISTS practice_squad_moves_view;
        CREATE VIEW practice_squad_moves_view AS
        SELECT
            psm.move_id,
            psm.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            psm.team_id,
            t.abbreviation AS team,
            psm.season,
            psm.move_date,
            psm.week,
            psm.move_type,
            psm.from_status,
            psm.to_status,
            psm.notes,
            psm.created_at
        FROM practice_squad_moves psm
        JOIN players p ON p.player_id = psm.player_id
        JOIN teams t ON t.team_id = psm.team_id;
        """
    )
    ensure_column(con, "players", "is_international_pathway", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(con, "roster_rule_sets", "practice_squad_developmental_limit", "INTEGER NOT NULL DEFAULT 10")
    ensure_column(con, "roster_rule_sets", "practice_squad_veteran_exception_limit", "INTEGER NOT NULL DEFAULT 6")
    ensure_column(con, "roster_rule_sets", "practice_squad_elevation_limit", "INTEGER NOT NULL DEFAULT 3")
    ensure_column(con, "roster_rule_sets", "practice_squad_weekly_elevation_limit", "INTEGER NOT NULL DEFAULT 2")


def seeded_rule_seasons(con: sqlite3.Connection) -> list[int]:
    try:
        rows = con.execute(
            "SELECT league_year FROM league_years ORDER BY league_year"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    seasons = [int(row["league_year"] if isinstance(row, sqlite3.Row) else row[0]) for row in rows]
    return seasons or [DEFAULT_SEASON]


def seed_rules(con: sqlite3.Connection) -> None:
    for season in seeded_rule_seasons(con):
        for rule in RULE_SETS:
            con.execute(
                """
                INSERT INTO roster_rule_sets (
                    season, phase, rule_set_name, active_roster_limit, total_roster_limit,
                    practice_squad_limit, practice_squad_international_exemption_limit,
                    practice_squad_developmental_limit, practice_squad_veteran_exception_limit,
                    practice_squad_elevation_limit, practice_squad_weekly_elevation_limit,
                    practice_squad_enabled, game_day_active_limit,
                    game_day_active_limit_without_min_ol, game_day_min_offensive_linemen,
                    top51_count, salary_cap_mode, source_name, source_url, notes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(season, phase) DO UPDATE SET
                    rule_set_name = excluded.rule_set_name,
                    active_roster_limit = excluded.active_roster_limit,
                    total_roster_limit = excluded.total_roster_limit,
                    practice_squad_limit = excluded.practice_squad_limit,
                    practice_squad_international_exemption_limit = excluded.practice_squad_international_exemption_limit,
                    practice_squad_developmental_limit = excluded.practice_squad_developmental_limit,
                    practice_squad_veteran_exception_limit = excluded.practice_squad_veteran_exception_limit,
                    practice_squad_elevation_limit = excluded.practice_squad_elevation_limit,
                    practice_squad_weekly_elevation_limit = excluded.practice_squad_weekly_elevation_limit,
                    practice_squad_enabled = excluded.practice_squad_enabled,
                    game_day_active_limit = excluded.game_day_active_limit,
                    game_day_active_limit_without_min_ol = excluded.game_day_active_limit_without_min_ol,
                    game_day_min_offensive_linemen = excluded.game_day_min_offensive_linemen,
                    top51_count = excluded.top51_count,
                    salary_cap_mode = excluded.salary_cap_mode,
                    source_name = excluded.source_name,
                    source_url = excluded.source_url,
                    notes = excluded.notes,
                    updated_at = datetime('now')
                """,
                (
                    season,
                    rule.phase,
                    rule.rule_set_name.replace(str(rule.season), str(season)),
                    rule.active_roster_limit,
                    rule.total_roster_limit,
                    rule.practice_squad_limit,
                    rule.practice_squad_international_exemption_limit,
                    rule.practice_squad_developmental_limit,
                    rule.practice_squad_veteran_exception_limit,
                    rule.practice_squad_elevation_limit,
                    rule.practice_squad_weekly_elevation_limit,
                    rule.practice_squad_enabled,
                    rule.game_day_active_limit,
                    rule.game_day_active_limit_without_min_ol,
                    rule.game_day_min_offensive_linemen,
                    rule.top51_count,
                    rule.salary_cap_mode,
                    rule.source_name,
                    rule.source_url,
                    rule.notes,
                ),
            )
            rule_set_id = con.execute(
                "SELECT rule_set_id FROM roster_rule_sets WHERE season = ? AND phase = ?",
                (season, rule.phase),
            ).fetchone()["rule_set_id"]

            for position_rule in POSITION_RULES_BY_PHASE[rule.phase]:
                con.execute(
                    """
                    INSERT INTO roster_position_rules (
                        rule_set_id, position_group, group_label, min_count,
                        recommended_min, recommended_max, max_count, severity, notes,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(rule_set_id, position_group) DO UPDATE SET
                        group_label = excluded.group_label,
                        min_count = excluded.min_count,
                        recommended_min = excluded.recommended_min,
                        recommended_max = excluded.recommended_max,
                        max_count = excluded.max_count,
                        severity = excluded.severity,
                        notes = excluded.notes,
                        updated_at = datetime('now')
                    """,
                    (
                        rule_set_id,
                        position_rule.position_group,
                        position_rule.group_label,
                        position_rule.min_count,
                        position_rule.recommended_min,
                        position_rule.recommended_max,
                        position_rule.max_count,
                        position_rule.severity,
                        position_rule.notes,
                    ),
                )
                con.execute(
                    """
                    DELETE FROM roster_position_group_members
                    WHERE rule_set_id = ? AND position_group = ?
                    """,
                    (rule_set_id, position_rule.position_group),
                )
                for position in position_rule.positions:
                    con.execute(
                        """
                        INSERT INTO roster_position_group_members (
                            rule_set_id, position_group, position
                        )
                        VALUES (?, ?, ?)
                        """,
                        (rule_set_id, position_rule.position_group, position),
                    )


def get_rule_set(con: sqlite3.Connection, season: int, phase: str) -> sqlite3.Row:
    row = con.execute(
        """
        SELECT *
        FROM roster_rule_sets
        WHERE season = ? AND phase = ?
        """,
        (season, phase),
    ).fetchone()
    if not row:
        raise ValueError(f"No roster rule set for {season} {phase}. Run setup first.")
    return row


def get_team(con: sqlite3.Connection, abbreviation: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM teams WHERE abbreviation = ?",
        (abbreviation.upper(),),
    ).fetchone()
    if not row:
        raise ValueError(f"Team not found: {abbreviation}")
    return row


def normalize_name(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", value.lower()))


def today(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
    ).fetchone()
    if row:
        return row["setting_value"]
    return con.execute("SELECT date('now')").fetchone()[0]


def current_week(con: sqlite3.Connection) -> int | None:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_week'"
    ).fetchone()
    if not row:
        return None
    try:
        return int(row["setting_value"])
    except ValueError:
        return None


def phase_for_transactions(con: sqlite3.Connection) -> str:
    target_date = today(con)
    try:
        row = con.execute(
            """
            SELECT roster_rule_phase
            FROM league_phase_windows
            WHERE date(?) BETWEEN date(start_date) AND date(end_date)
              AND roster_rule_phase IS NOT NULL
            ORDER BY sort_order DESC
            LIMIT 1
            """,
            (target_date,),
        ).fetchone()
        if row and row["roster_rule_phase"]:
            return row["roster_rule_phase"]
    except sqlite3.OperationalError:
        pass
    return DEFAULT_PHASE


def find_player(
    con: sqlite3.Connection,
    name: str,
    *,
    team_id: int | None = None,
    statuses: set[str] | None = None,
    require_rostered: bool = False,
) -> sqlite3.Row:
    rows = con.execute(
        """
        SELECT p.*, t.abbreviation AS team
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        ORDER BY p.last_name, p.first_name
        """
    ).fetchall()
    needle = normalize_name(name)
    matches = []
    for row in rows:
        full_name = f"{row['first_name']} {row['last_name']}"
        if needle not in normalize_name(full_name):
            continue
        if team_id is not None and row["team_id"] != team_id:
            continue
        if statuses is not None and row["status"] not in statuses:
            continue
        if require_rostered and row["team_id"] is None:
            continue
        matches.append(row)

    if not matches:
        raise ValueError(f"Player not found: {name}")
    if len(matches) > 1:
        examples = ", ".join(
            f"{row['first_name']} {row['last_name']} ({row['position']}, {row['team'] or row['status']})"
            for row in matches[:8]
        )
        raise ValueError(f"Player search matched {len(matches)} players. Be more specific: {examples}")
    return matches[0]


def player_name(player: sqlite3.Row) -> str:
    return f"{player['first_name']} {player['last_name']}"


def active_contract(con: sqlite3.Connection, player_id: int) -> sqlite3.Row | None:
    try:
        return con.execute(
            """
            SELECT *
            FROM contracts
            WHERE player_id = ? AND COALESCE(is_active, 1) = 1
            ORDER BY contract_id DESC
            LIMIT 1
            """,
            (player_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def transfer_active_contract(con: sqlite3.Connection, player_id: int, team_id: int) -> int | None:
    contract = active_contract(con, player_id)
    if not contract:
        return None
    contract_id = int(contract["contract_id"])
    con.execute("UPDATE contracts SET team_id = ? WHERE contract_id = ?", (team_id, contract_id))
    try:
        con.execute("UPDATE contract_years SET team_id = ? WHERE contract_id = ?", (team_id, contract_id))
    except sqlite3.OperationalError:
        pass
    return contract_id


def deactivate_active_contract(con: sqlite3.Connection, player_id: int) -> int | None:
    contract = active_contract(con, player_id)
    if not contract:
        return None
    contract_id = int(contract["contract_id"])
    con.execute("UPDATE contracts SET is_active = 0 WHERE contract_id = ?", (contract_id,))
    try:
        con.execute("UPDATE contract_years SET is_active = 0 WHERE contract_id = ?", (contract_id,))
    except sqlite3.OperationalError:
        pass
    return contract_id


def set_player_status(
    con: sqlite3.Connection,
    *,
    player: sqlite3.Row,
    team_id: int | None,
    new_status: str,
    season: int,
    reason: str,
) -> None:
    old_status = player["status"]
    con.execute(
        "UPDATE players SET team_id = ?, status = ? WHERE player_id = ?",
        (team_id, new_status, player["player_id"]),
    )
    con.execute(
        """
        INSERT INTO player_roster_status_history (
            player_id, old_status, new_status, effective_date, season, reason
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (player["player_id"], old_status, new_status, today(con), season, reason),
    )


def clear_depth_chart(con: sqlite3.Connection, player_id: int) -> None:
    try:
        con.execute("DELETE FROM depth_charts WHERE player_id = ?", (player_id,))
    except sqlite3.OperationalError:
        pass


def log_transaction(
    con: sqlite3.Connection,
    *,
    transaction_type: str,
    season: int,
    player_id: int,
    team_id: int | None = None,
    secondary_team_id: int | None = None,
    from_team_id: int | None = None,
    to_team_id: int | None = None,
    old_status: str | None = None,
    new_status: str | None = None,
    contract_id: int | None = None,
    description: str | None = None,
) -> int:
    transaction_id, _inserted = insert_transaction(
        con,
        transaction_date=today(con),
        season=season,
        phase=phase_for_transactions(con),
        transaction_type=transaction_type,
        team_id=team_id,
        secondary_team_id=secondary_team_id,
        player_id=player_id,
        contract_id=contract_id,
        from_team_id=from_team_id,
        to_team_id=to_team_id,
        old_status=old_status,
        new_status=new_status,
        description=description,
        source=SOURCE,
    )
    return transaction_id


def record_practice_squad_move(
    con: sqlite3.Connection,
    *,
    player_id: int,
    team_id: int,
    season: int,
    move_type: str,
    from_status: str | None,
    to_status: str | None,
    notes: str | None,
) -> None:
    con.execute(
        """
        INSERT INTO practice_squad_moves (
            player_id, team_id, season, move_date, week, move_type,
            from_status, to_status, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            player_id,
            team_id,
            season,
            today(con),
            current_week(con),
            move_type,
            from_status,
            to_status,
            notes,
        ),
    )


def seed_waiver_priority(con: sqlite3.Connection, season: int, *, force: bool = False) -> int:
    if force:
        con.execute("DELETE FROM waiver_priority WHERE season = ?", (season,))
    existing = con.execute(
        "SELECT COUNT(*) FROM waiver_priority WHERE season = ?",
        (season,),
    ).fetchone()[0]
    if existing:
        return 0

    ordered_team_ids: list[int] = []
    seen: set[int] = set()

    source = "current_standings"
    if table_exists(con, "season_team_records"):
        standings_rows = con.execute(
            """
            SELECT str.team_id
            FROM season_team_records str
            JOIN teams t ON t.team_id = str.team_id
            WHERE str.season = ?
              AND (str.wins + str.losses + str.ties) > 0
            ORDER BY
                ((str.wins + str.ties * 0.5) * 1.0 / NULLIF(str.wins + str.losses + str.ties, 0)) ASC,
                str.wins ASC,
                (str.points_for - str.points_against) ASC,
                t.abbreviation ASC
            """,
            (season,),
        ).fetchall()
        for row in standings_rows:
            team_id = int(row["team_id"])
            if team_id not in seen:
                ordered_team_ids.append(team_id)
                seen.add(team_id)

    if len(ordered_team_ids) < 32:
        source = "draft_order_proxy"
        draft_rows = con.execute(
            """
            SELECT original_team_id
            FROM draft_picks
            WHERE draft_year = ? AND round = 1 AND original_team_id IS NOT NULL
            ORDER BY COALESCE(pick_number, pick_id), pick_id
            """,
            (season + 1,),
        ).fetchall()
        for row in draft_rows:
            team_id = int(row["original_team_id"])
            if team_id not in seen:
                ordered_team_ids.append(team_id)
                seen.add(team_id)

    if len(ordered_team_ids) < 32:
        source = "alphabetical_placeholder"
        ordered_team_ids = [
            int(row["team_id"])
            for row in con.execute("SELECT team_id FROM teams ORDER BY abbreviation").fetchall()
        ]

    for priority, team_id in enumerate(ordered_team_ids, start=1):
        con.execute(
            """
            INSERT INTO waiver_priority (season, team_id, priority, source, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(season, team_id) DO UPDATE SET
                priority = excluded.priority,
                source = excluded.source,
                notes = excluded.notes,
                updated_at = datetime('now')
            """,
            (
                season,
                team_id,
                priority,
                source,
                f"Waiver priority seeded from {source.replace('_', ' ')}.",
            ),
        )
    return len(ordered_team_ids)


def waiver_priority_for_team(con: sqlite3.Connection, season: int, team_id: int) -> int:
    seed_waiver_priority(con, season)
    row = con.execute(
        "SELECT priority FROM waiver_priority WHERE season = ? AND team_id = ?",
        (season, team_id),
    ).fetchone()
    if not row:
        raise ValueError(f"No waiver priority for team_id={team_id} in {season}.")
    return int(row["priority"])


def get_waiver(con: sqlite3.Connection, waiver_id: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM waiver_wire WHERE waiver_id = ?", (waiver_id,)).fetchone()
    if not row:
        raise ValueError(f"Waiver entry not found: {waiver_id}")
    return row


def find_open_waiver(con: sqlite3.Connection, player_search: str) -> sqlite3.Row:
    rows = con.execute(
        """
        SELECT ww.*, p.first_name, p.last_name, p.position
        FROM waiver_wire ww
        JOIN players p ON p.player_id = ww.player_id
        WHERE ww.status = 'Open'
        ORDER BY ww.claim_deadline, p.last_name, p.first_name
        """
    ).fetchall()
    needle = normalize_name(player_search)
    matches = [
        row
        for row in rows
        if needle in normalize_name(f"{row['first_name']} {row['last_name']}")
    ]
    if not matches:
        raise ValueError(f"No open waiver found for player search: {player_search}")
    if len(matches) > 1:
        examples = ", ".join(f"{row['first_name']} {row['last_name']}" for row in matches[:8])
        raise ValueError(f"Waiver search matched {len(matches)} entries. Be more specific: {examples}")
    return matches[0]


def default_claim_deadline(waiver_date: str) -> str:
    return (datetime.fromisoformat(waiver_date) + timedelta(hours=DEFAULT_WAIVER_HOURS)).date().isoformat()


def validation_issue(
    severity: str,
    issue_code: str,
    message: str,
    *,
    position_group: str | None = None,
    actual_value: int | None = None,
    expected_value: int | None = None,
) -> dict[str, object]:
    return {
        "severity": severity,
        "issue_code": issue_code,
        "message": message,
        "position_group": position_group,
        "actual_value": actual_value,
        "expected_value": expected_value,
    }


def team_cap_row(con: sqlite3.Connection, team_id: int) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM team_cap_view WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"No cap row available for team_id={team_id}")
    return row


def validate_team(
    con: sqlite3.Connection,
    team: sqlite3.Row,
    rule_set: sqlite3.Row,
    *,
    include_info: bool = False,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    counts = con.execute(
        "SELECT * FROM team_roster_counts_view WHERE team_id = ?",
        (team["team_id"],),
    ).fetchone()
    cap = team_cap_row(con, team["team_id"])
    issues: list[dict[str, object]] = []

    active_count = int(counts["active_roster_count"] or 0)
    practice_squad_count = int(counts["practice_squad_count"] or 0)
    total_controlled_count = int(counts["total_controlled_count"] or 0)
    unknown_status_count = int(counts["unknown_status_count"] or 0)

    if active_count > int(rule_set["active_roster_limit"]):
        issues.append(
            validation_issue(
                "ERROR",
                "ACTIVE_ROSTER_OVER_LIMIT",
                (
                    f"{team['abbreviation']} has {active_count} active roster players; "
                    f"limit is {rule_set['active_roster_limit']} for {rule_set['phase']}."
                ),
                actual_value=active_count,
                expected_value=int(rule_set["active_roster_limit"]),
            )
        )
    elif include_info and active_count < int(rule_set["active_roster_limit"]):
        issues.append(
            validation_issue(
                "INFO",
                "ACTIVE_ROSTER_UNDER_LIMIT",
                (
                    f"{team['abbreviation']} has {active_count} active roster players "
                    f"against a {rule_set['active_roster_limit']} player limit."
                ),
                actual_value=active_count,
                expected_value=int(rule_set["active_roster_limit"]),
            )
        )

    if total_controlled_count > int(rule_set["total_roster_limit"]):
        issues.append(
            validation_issue(
                "ERROR",
                "TOTAL_ROSTER_OVER_LIMIT",
                (
                    f"{team['abbreviation']} controls {total_controlled_count} players; "
                    f"overall phase limit is {rule_set['total_roster_limit']}."
                ),
                actual_value=total_controlled_count,
                expected_value=int(rule_set["total_roster_limit"]),
            )
        )

    ps_limit = int(rule_set["practice_squad_limit"])
    if int(rule_set["practice_squad_enabled"]) == 0 and practice_squad_count:
        issues.append(
            validation_issue(
                "WARNING",
                "PRACTICE_SQUAD_NOT_ENABLED",
                (
                    f"{team['abbreviation']} has {practice_squad_count} practice squad players, "
                    f"but practice squads are not enabled in {rule_set['phase']}."
                ),
                actual_value=practice_squad_count,
                expected_value=0,
            )
        )
    elif practice_squad_count > ps_limit + int(rule_set["practice_squad_international_exemption_limit"]):
        issues.append(
            validation_issue(
                "ERROR",
                "PRACTICE_SQUAD_OVER_LIMIT",
                (
                    f"{team['abbreviation']} has {practice_squad_count} practice squad players; "
                    f"base limit is {ps_limit} plus possible international exemption."
                ),
                actual_value=practice_squad_count,
                expected_value=ps_limit,
            )
        )
    elif practice_squad_count:
        ps_usage = practice_squad_usage(con, int(team["team_id"]), rule_set)
        ipp_limit = int(rule_set["practice_squad_international_exemption_limit"] or 0)
        base_limit = int(rule_set["practice_squad_limit"] or 0)
        dev_limit = int(rule_set["practice_squad_developmental_limit"] or PRACTICE_SQUAD_DEVELOPMENTAL_LIMIT)
        vet_limit = int(rule_set["practice_squad_veteran_exception_limit"] or PRACTICE_SQUAD_VETERAN_EXCEPTION_LIMIT)
        if ps_usage["base_count"] > base_limit:
            issues.append(
                validation_issue(
                    "ERROR",
                    "PRACTICE_SQUAD_BASE_OVER_LIMIT",
                    (
                        f"{team['abbreviation']} has {ps_usage['base_count']} non-exempt practice squad players; "
                        f"normal limit is {base_limit}."
                    ),
                    actual_value=ps_usage["base_count"],
                    expected_value=base_limit,
                )
            )
        if ps_usage["international_exemption_count"] > ipp_limit:
            issues.append(
                validation_issue(
                    "ERROR",
                    "PRACTICE_SQUAD_IPP_OVER_LIMIT",
                    (
                        f"{team['abbreviation']} has {ps_usage['international_exemption_count']} International Pathway "
                        f"practice squad exemptions; limit is {ipp_limit}."
                    ),
                    actual_value=ps_usage["international_exemption_count"],
                    expected_value=ipp_limit,
                )
            )
        if ps_usage["developmental_count"] > dev_limit:
            issues.append(
                validation_issue(
                    "ERROR",
                    "PRACTICE_SQUAD_DEVELOPMENTAL_OVER_LIMIT",
                    (
                        f"{team['abbreviation']} has {ps_usage['developmental_count']} developmental practice squad players; "
                        f"limit is {dev_limit}."
                    ),
                    actual_value=ps_usage["developmental_count"],
                    expected_value=dev_limit,
                )
            )
        if ps_usage["veteran_exception_count"] > vet_limit:
            issues.append(
                validation_issue(
                    "ERROR",
                    "PRACTICE_SQUAD_VETERAN_EXCEPTION_OVER_LIMIT",
                    (
                        f"{team['abbreviation']} has {ps_usage['veteran_exception_count']} veteran-exception practice squad players; "
                        f"limit is {vet_limit}."
                    ),
                    actual_value=ps_usage["veteran_exception_count"],
                    expected_value=vet_limit,
                )
            )

    if unknown_status_count:
        unknown_rows = con.execute(
            """
            SELECT p.first_name || ' ' || p.last_name AS player_name, p.status
            FROM players p
            LEFT JOIN roster_status_types rst ON rst.status_code = p.status
            WHERE p.team_id = ? AND rst.status_code IS NULL
            ORDER BY player_name
            LIMIT 5
            """,
            (team["team_id"],),
        ).fetchall()
        examples = ", ".join(f"{row['player_name']} ({row['status']})" for row in unknown_rows)
        issues.append(
            validation_issue(
                "WARNING",
                "UNKNOWN_ROSTER_STATUS",
                (
                    f"{team['abbreviation']} has {unknown_status_count} player(s) with statuses "
                    f"not in roster_status_types. Counted as roster players for now: {examples}."
                ),
                actual_value=unknown_status_count,
            )
        )

    if int(cap["cap_space"] or 0) < 0:
        issues.append(
            validation_issue(
                "WARNING",
                "TEAM_OVER_CAP",
                (
                    f"{team['abbreviation']} is over the {cap['cap_accounting_mode']} cap by "
                    f"{format_money(abs(int(cap['cap_space'] or 0)))}."
                ),
                actual_value=int(cap["cap_space"] or 0),
                expected_value=0,
            )
        )

    position_rows = con.execute(
        """
        SELECT *
        FROM team_position_group_counts_view
        WHERE rule_set_id = ? AND team_id = ?
        ORDER BY position_group
        """,
        (rule_set["rule_set_id"], team["team_id"]),
    ).fetchall()
    for row in position_rows:
        count = int(row["player_count"] or 0)
        min_count = int(row["min_count"] or 0)
        recommended_min = int(row["recommended_min"] or 0)
        recommended_max = int(row["recommended_max"] or 0)
        max_count = int(row["max_count"] or 0)
        severity = row["severity"] or "WARNING"
        label = row["group_label"]
        group = row["position_group"]

        if count < min_count:
            issues.append(
                validation_issue(
                    severity,
                    "POSITION_GROUP_BELOW_MIN",
                    f"{team['abbreviation']} has {count} {label}; minimum is {min_count}.",
                    position_group=group,
                    actual_value=count,
                    expected_value=min_count,
                )
            )
        elif recommended_min and count < recommended_min:
            issues.append(
                validation_issue(
                    "INFO" if rule_set["phase"] == "Preseason" else "WARNING",
                    "POSITION_GROUP_BELOW_RECOMMENDED",
                    (
                        f"{team['abbreviation']} has {count} {label}; "
                        f"recommended floor is {recommended_min}."
                    ),
                    position_group=group,
                    actual_value=count,
                    expected_value=recommended_min,
                )
            )

        if max_count and count > max_count:
            issues.append(
                validation_issue(
                    "WARNING",
                    "POSITION_GROUP_ABOVE_MAX",
                    f"{team['abbreviation']} has {count} {label}; max guideline is {max_count}.",
                    position_group=group,
                    actual_value=count,
                    expected_value=max_count,
                )
            )
        elif recommended_max and count > recommended_max:
            issues.append(
                validation_issue(
                    "INFO" if rule_set["phase"] == "Preseason" else "WARNING",
                    "POSITION_GROUP_ABOVE_RECOMMENDED",
                    (
                        f"{team['abbreviation']} has {count} {label}; "
                        f"recommended ceiling is {recommended_max}."
                    ),
                    position_group=group,
                    actual_value=count,
                    expected_value=recommended_max,
                )
            )

    if include_info:
        issues.append(
            validation_issue(
                "INFO",
                "CAP_ACCOUNTING_MODE",
                (
                    f"{team['abbreviation']} cap mode is {cap['cap_accounting_mode']}; "
                    f"{cap['contracts_counted']} contracts counted in Top 51."
                ),
                actual_value=int(cap["contracts_counted"] or 0),
                expected_value=int(rule_set["top51_count"] or 51),
            )
        )

    error_count = sum(1 for issue in issues if issue["severity"] == "ERROR")
    warning_count = sum(1 for issue in issues if issue["severity"] == "WARNING")
    info_count = sum(1 for issue in issues if issue["severity"] == "INFO")
    summary = {
        "team_id": team["team_id"],
        "team": team["abbreviation"],
        "active_count": active_count,
        "practice_squad_count": practice_squad_count,
        "total_controlled_count": total_controlled_count,
        "salary_cap": int(cap["salary_cap"] or 0),
        "total_committed": int(cap["total_committed"] or 0),
        "cap_space": int(cap["cap_space"] or 0),
        "passed": 1 if error_count == 0 else 0,
        "error_count": error_count,
        "warning_count": warning_count,
        "info_count": info_count,
    }
    return summary, issues


def save_validation_run(
    con: sqlite3.Connection,
    rule_set: sqlite3.Row,
    summary: dict[str, object],
    issues: list[dict[str, object]],
) -> int:
    cur = con.execute(
        """
        INSERT INTO roster_validation_runs (
            season, phase, rule_set_id, team_id, active_count,
            practice_squad_count, total_controlled_count, salary_cap,
            total_committed, cap_space, passed, error_count, warning_count,
            info_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(rule_set["season"]),
            rule_set["phase"],
            int(rule_set["rule_set_id"]),
            int(summary["team_id"]),
            int(summary["active_count"]),
            int(summary["practice_squad_count"]),
            int(summary["total_controlled_count"]),
            int(summary["salary_cap"]),
            int(summary["total_committed"]),
            int(summary["cap_space"]),
            int(summary["passed"]),
            int(summary["error_count"]),
            int(summary["warning_count"]),
            int(summary["info_count"]),
        ),
    )
    run_id = int(cur.lastrowid)
    for issue in issues:
        con.execute(
            """
            INSERT INTO roster_validation_issues (
                validation_run_id, team_id, severity, issue_code, position_group,
                actual_value, expected_value, message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                int(summary["team_id"]),
                issue["severity"],
                issue["issue_code"],
                issue["position_group"],
                issue["actual_value"],
                issue["expected_value"],
                issue["message"],
            ),
        )
    return run_id


def print_team_result(
    rule_set: sqlite3.Row,
    summary: dict[str, object],
    issues: list[dict[str, object]],
    *,
    detail: bool = True,
) -> None:
    status = "PASS" if summary["passed"] else "FAIL"
    print(
        f"{summary['team']} {status}: "
        f"active {summary['active_count']}/{rule_set['active_roster_limit']}, "
        f"PS {summary['practice_squad_count']}/{rule_set['practice_squad_limit']}, "
        f"controlled {summary['total_controlled_count']}/{rule_set['total_roster_limit']}, "
        f"cap {format_money(int(summary['cap_space']))} space, "
        f"errors {summary['error_count']}, warnings {summary['warning_count']}, infos {summary['info_count']}"
    )
    if detail:
        for issue in issues:
            print(f"  [{issue['severity']}] {issue['issue_code']}: {issue['message']}")


def selected_teams(con: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    if args.all:
        return con.execute("SELECT * FROM teams ORDER BY abbreviation").fetchall()
    if args.team:
        return [get_team(con, args.team)]
    raise ValueError("Use --team TEAM or --all.")


def action_setup(con: sqlite3.Connection) -> None:
    ensure_schema(con)
    seed_rules(con)
    con.commit()
    rule_sets = con.execute("SELECT COUNT(*) FROM roster_rule_sets").fetchone()[0]
    position_rules = con.execute("SELECT COUNT(*) FROM roster_position_rules").fetchone()[0]
    members = con.execute("SELECT COUNT(*) FROM roster_position_group_members").fetchone()[0]
    print(f"Roster rule sets available: {rule_sets}")
    print(f"Position-group rules available: {position_rules}")
    print(f"Position group members available: {members}")


def action_validate(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_rules(con)
    con.commit()
    phase = normalize_phase(args.phase)
    season = args.season if args.season is not None else current_season(con)
    rule_set = get_rule_set(con, season, phase)
    teams = selected_teams(con, args)
    saved_runs: list[int] = []
    totals = {"errors": 0, "warnings": 0, "infos": 0, "passed": 0}

    for team in teams:
        summary, issues = validate_team(con, team, rule_set, include_info=args.include_info)
        if not args.no_save:
            saved_runs.append(save_validation_run(con, rule_set, summary, issues))
        print_team_result(rule_set, summary, issues, detail=not args.summary_only)
        totals["errors"] += int(summary["error_count"])
        totals["warnings"] += int(summary["warning_count"])
        totals["infos"] += int(summary["info_count"])
        totals["passed"] += int(summary["passed"])

    if not args.no_save:
        con.commit()

    failed = len(teams) - totals["passed"]
    print(
        f"Validated {len(teams)} team(s) for {season} {phase}: "
        f"{totals['passed']} passed, {failed} failed, "
        f"{totals['errors']} errors, {totals['warnings']} warnings, {totals['infos']} infos."
    )
    if saved_runs:
        print(f"Saved validation run ids: {min(saved_runs)}-{max(saved_runs)}")


def action_show_rules(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_rules(con)
    phase = normalize_phase(args.phase)
    season = args.season if args.season is not None else current_season(con)
    rule_set = get_rule_set(con, season, phase)
    print(f"{rule_set['rule_set_name']} ({rule_set['season']} {rule_set['phase']})")
    print(
        f"Active {rule_set['active_roster_limit']}, total {rule_set['total_roster_limit']}, "
        f"practice squad {rule_set['practice_squad_limit']} "
        f"(dev {rule_set['practice_squad_developmental_limit']}, "
        f"vet exceptions {rule_set['practice_squad_veteran_exception_limit']}, "
        f"IPP +{rule_set['practice_squad_international_exemption_limit']})"
    )
    print(
        f"Gameday active {rule_set['game_day_active_limit']} "
        f"({rule_set['game_day_active_limit_without_min_ol']} without "
        f"{rule_set['game_day_min_offensive_linemen']} OL), cap mode {rule_set['salary_cap_mode']}"
    )
    print(
        f"Standard elevations: {rule_set['practice_squad_elevation_limit']} per player, "
        f"{rule_set['practice_squad_weekly_elevation_limit']} per team per week."
    )
    rows = con.execute(
        """
        SELECT rpr.position_group, rpr.group_label, rpr.min_count,
               rpr.recommended_min, rpr.recommended_max, rpr.max_count,
               GROUP_CONCAT(rpgm.position, ', ') AS positions
        FROM roster_position_rules rpr
        JOIN roster_position_group_members rpgm
          ON rpgm.rule_set_id = rpr.rule_set_id
         AND rpgm.position_group = rpr.position_group
        WHERE rpr.rule_set_id = ?
        GROUP BY rpr.position_rule_id
        ORDER BY rpr.position_group
        """,
        (rule_set["rule_set_id"],),
    ).fetchall()
    for row in rows:
        print(
            f"  {row['position_group']}: {row['positions']} | "
            f"min {row['min_count']}, recommended {row['recommended_min']}-{row['recommended_max']}, "
            f"max {row['max_count']}"
        )


def action_seed_waiver_priority(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_rules(con)
    season = args.season if args.season is not None else current_season(con)
    inserted = seed_waiver_priority(con, season, force=args.force)
    con.commit()
    total = con.execute(
        "SELECT COUNT(*) FROM waiver_priority WHERE season = ?",
        (season,),
    ).fetchone()[0]
    print(f"Waiver priority ready for {season}: {total} teams ({inserted} seeded).")


def action_waiver_wire(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_rules(con)
    params: list[object] = []
    where = ""
    if not args.all:
        where = "WHERE status = ?"
        params.append(args.status)
    rows = con.execute(
        f"""
        SELECT *
        FROM waiver_wire_view
        {where}
        ORDER BY claim_deadline, player_name
        LIMIT ?
        """,
        (*params, args.limit),
    ).fetchall()
    if not rows:
        print("No waiver entries found.")
        return
    for row in rows:
        print(
            f"{row['waiver_id']:>4} {row['status']:<8} "
            f"{row['player_name']} ({row['position']}) from {row['original_team'] or 'FA'} | "
            f"deadline {row['claim_deadline']} | claims {row['claim_count']}"
        )
        if args.claims:
            claims = con.execute(
                """
                SELECT *
                FROM waiver_claims_view
                WHERE waiver_id = ?
                ORDER BY claim_order, claim_date
                """,
                (row["waiver_id"],),
            ).fetchall()
            for claim in claims:
                print(
                    f"      #{claim['claim_order']:<2} {claim['claiming_team']} "
                    f"{claim['status']} on {claim['claim_date']}"
                )


def action_waive(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_rules(con)
    season = args.season if args.season is not None else current_season(con)
    team = get_team(con, args.team) if args.team else None
    player = find_player(
        con,
        args.player,
        team_id=team["team_id"] if team else None,
        require_rostered=True,
    )
    if player["status"] in {"Free Agent", "Retired", WAIVED_STATUS}:
        raise ValueError(f"{player_name(player)} cannot be waived from status {player['status']}.")
    open_existing = con.execute(
        """
        SELECT waiver_id
        FROM waiver_wire
        WHERE player_id = ? AND status = 'Open'
        """,
        (player["player_id"],),
    ).fetchone()
    if open_existing:
        raise ValueError(f"{player_name(player)} is already on waivers as entry {open_existing['waiver_id']}.")

    waiver_date = args.waiver_date or today(con)
    claim_deadline = args.deadline_date or default_claim_deadline(waiver_date)
    from_team_id = int(player["team_id"])
    contract = active_contract(con, player["player_id"])
    contract_id = int(contract["contract_id"]) if contract else None

    set_player_status(
        con,
        player=player,
        team_id=None,
        new_status=WAIVED_STATUS,
        season=season,
        reason=args.reason or "Placed on waivers.",
    )
    clear_depth_chart(con, player["player_id"])
    cur = con.execute(
        """
        INSERT INTO waiver_wire (
            player_id, original_team_id, waiver_date, claim_deadline,
            season, status, reason, source
        )
        VALUES (?, ?, ?, ?, ?, 'Open', ?, ?)
        """,
        (
            player["player_id"],
            from_team_id,
            waiver_date,
            claim_deadline,
            season,
            args.reason,
            SOURCE,
        ),
    )
    transaction_id = log_transaction(
        con,
        transaction_type="Waiver",
        season=season,
        team_id=from_team_id,
        player_id=player["player_id"],
        contract_id=contract_id,
        from_team_id=from_team_id,
        old_status=player["status"],
        new_status=WAIVED_STATUS,
        description=f"Placed {player_name(player)} on waivers. Claim deadline: {claim_deadline}.",
    )
    print(
        f"Waived {player_name(player)} from team_id {from_team_id}. "
        f"Waiver entry {cur.lastrowid}, transaction {transaction_id}, deadline {claim_deadline}."
    )


def action_claim_waiver(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_rules(con)
    season = args.season if args.season is not None else current_season(con)
    seed_waiver_priority(con, season)
    team = get_team(con, args.team)
    waiver = get_waiver(con, args.waiver_id) if args.waiver_id else find_open_waiver(con, args.player)
    if waiver["status"] != "Open":
        raise ValueError(f"Waiver entry {waiver['waiver_id']} is {waiver['status']}, not Open.")
    if waiver["original_team_id"] == team["team_id"]:
        raise ValueError("Original team cannot claim its own waiver entry.")
    claim_order = waiver_priority_for_team(con, season, int(team["team_id"]))
    con.execute(
        """
        INSERT INTO waiver_claims (
            waiver_id, claiming_team_id, claim_order, claim_date, status, notes
        )
        VALUES (?, ?, ?, ?, 'Pending', ?)
        ON CONFLICT(waiver_id, claiming_team_id) DO UPDATE SET
            claim_order = excluded.claim_order,
            claim_date = excluded.claim_date,
            status = 'Pending',
            notes = excluded.notes
        """,
        (
            waiver["waiver_id"],
            team["team_id"],
            claim_order,
            today(con),
            args.notes,
        ),
    )
    player = con.execute(
        "SELECT first_name, last_name FROM players WHERE player_id = ?",
        (waiver["player_id"],),
    ).fetchone()
    print(
        f"{team['abbreviation']} claimed {player['first_name']} {player['last_name']} "
        f"on waiver entry {waiver['waiver_id']} with priority {claim_order}."
    )


def action_process_waivers(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_rules(con)
    process_date = args.date or today(con)
    rows = con.execute(
        """
        SELECT *
        FROM waiver_wire
        WHERE status = 'Open'
          AND (? = 1 OR date(claim_deadline) <= date(?))
        ORDER BY claim_deadline, waiver_id
        """,
        (1 if args.all_open else 0, process_date),
    ).fetchall()
    if not rows:
        print("No waiver entries ready to process.")
        return

    processed = 0
    for waiver in rows:
        player = con.execute(
            "SELECT p.*, t.abbreviation AS team FROM players p LEFT JOIN teams t ON t.team_id = p.team_id WHERE p.player_id = ?",
            (waiver["player_id"],),
        ).fetchone()
        claims = con.execute(
            """
            SELECT wc.*, t.abbreviation AS claiming_team
            FROM waiver_claims wc
            JOIN teams t ON t.team_id = wc.claiming_team_id
            WHERE wc.waiver_id = ? AND wc.status = 'Pending'
            ORDER BY wc.claim_order, wc.claim_date, wc.claim_id
            """,
            (waiver["waiver_id"],),
        ).fetchall()

        if claims:
            winner = claims[0]
            contract_id = transfer_active_contract(con, waiver["player_id"], int(winner["claiming_team_id"]))
            set_player_status(
                con,
                player=player,
                team_id=int(winner["claiming_team_id"]),
                new_status="Active",
                season=int(waiver["season"]),
                reason=f"Claimed off waivers by {winner['claiming_team']}.",
            )
            con.execute(
                """
                UPDATE waiver_wire
                SET status = 'Claimed', resolved_at = datetime('now')
                WHERE waiver_id = ?
                """,
                (waiver["waiver_id"],),
            )
            con.execute(
                """
                UPDATE waiver_claims
                SET status = CASE WHEN claim_id = ? THEN 'Awarded' ELSE 'Denied' END
                WHERE waiver_id = ?
                """,
                (winner["claim_id"], waiver["waiver_id"]),
            )
            transaction_id = log_transaction(
                con,
                transaction_type="Waiver Claim",
                season=int(waiver["season"]),
                team_id=int(winner["claiming_team_id"]),
                secondary_team_id=waiver["original_team_id"],
                player_id=waiver["player_id"],
                contract_id=contract_id,
                from_team_id=waiver["original_team_id"],
                to_team_id=int(winner["claiming_team_id"]),
                old_status=player["status"],
                new_status="Active",
                description=f"{winner['claiming_team']} awarded waiver claim for {player_name(player)}.",
            )
            print(
                f"Awarded {player_name(player)} to {winner['claiming_team']} "
                f"(waiver {waiver['waiver_id']}, transaction {transaction_id})."
            )
        else:
            contract_id = deactivate_active_contract(con, waiver["player_id"])
            set_player_status(
                con,
                player=player,
                team_id=None,
                new_status="Free Agent",
                season=int(waiver["season"]),
                reason="Cleared waivers.",
            )
            con.execute(
                """
                UPDATE waiver_wire
                SET status = 'Cleared', resolved_at = datetime('now')
                WHERE waiver_id = ?
                """,
                (waiver["waiver_id"],),
            )
            transaction_id = log_transaction(
                con,
                transaction_type="Waiver Cleared",
                season=int(waiver["season"]),
                team_id=waiver["original_team_id"],
                player_id=waiver["player_id"],
                contract_id=contract_id,
                from_team_id=waiver["original_team_id"],
                old_status=player["status"],
                new_status="Free Agent",
                description=f"{player_name(player)} cleared waivers and became a free agent.",
            )
            print(
                f"{player_name(player)} cleared waivers "
                f"(waiver {waiver['waiver_id']}, transaction {transaction_id})."
            )
        processed += 1
    print(f"Processed {processed} waiver entr{'y' if processed == 1 else 'ies'}.")


def practice_squad_rule_set(con: sqlite3.Connection, season: int, phase: str | None) -> sqlite3.Row:
    selected_phase = normalize_phase(phase) if phase else phase_for_transactions(con)
    return get_rule_set(con, season, selected_phase)


def practice_squad_count(con: sqlite3.Connection, team_id: int) -> int:
    row = con.execute(
        "SELECT practice_squad_count FROM team_roster_counts_view WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    return int(row["practice_squad_count"] or 0) if row else 0


def active_roster_count(con: sqlite3.Connection, team_id: int) -> int:
    row = con.execute(
        "SELECT active_roster_count FROM team_roster_counts_view WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    return int(row["active_roster_count"] or 0) if row else 0


def row_int(row: sqlite3.Row | dict[str, object], key: str, default: int = 0) -> int:
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return int(value or default)


def player_is_international_pathway(player: sqlite3.Row | dict[str, object]) -> bool:
    try:
        return bool(int(player["is_international_pathway"] or 0))
    except (KeyError, IndexError, TypeError, ValueError):
        return False


def active_contract_aav(con: sqlite3.Connection, player_id: int) -> int:
    row = con.execute(
        """
        SELECT COALESCE(aav, 0) AS aav
        FROM contracts
        WHERE player_id = ?
          AND COALESCE(is_active, 1) = 1
        ORDER BY contract_id DESC
        LIMIT 1
        """,
        (player_id,),
    ).fetchone()
    return int(row["aav"] or 0) if row else 0


def practice_squad_bucket(player: sqlite3.Row | dict[str, object]) -> tuple[str, str]:
    years_exp = row_int(player, "years_exp", 0)
    is_rookie = row_int(player, "is_rookie", 0)
    if player_is_international_pathway(player):
        return (
            "international_exemption",
            "International Pathway player; can use the 17th exempt practice-squad slot if the normal 16 are full.",
        )
    if is_rookie or years_exp <= 0:
        return (
            "developmental",
            "Rookie/no accrued-season proxy; counts toward the 10-player developmental practice-squad bucket.",
        )
    if years_exp <= 2:
        return (
            "developmental",
            "One/two-year player; counts toward the 10-player developmental practice-squad bucket.",
        )
    return (
        "veteran_exception",
        "Three-plus accrued-season proxy; needs one of the six veteran-exception practice-squad slots.",
    )


def practice_squad_usage(
    con: sqlite3.Connection,
    team_id: int,
    rule_set: sqlite3.Row | None = None,
) -> dict[str, int]:
    players = con.execute(
        """
        SELECT p.*
        FROM players p
        WHERE p.team_id = ?
          AND p.status = ?
        """,
        (team_id, PRACTICE_SQUAD_STATUS),
    ).fetchall()
    ipp_limit = int(rule_set["practice_squad_international_exemption_limit"] or 0) if rule_set else 1
    usage = {
        "total": len(players),
        "base_count": 0,
        "developmental_count": 0,
        "veteran_exception_count": 0,
        "international_exemption_count": 0,
    }
    for player in players:
        bucket, _reason = practice_squad_bucket(player)
        if bucket == "international_exemption" and usage["international_exemption_count"] < ipp_limit:
            usage["international_exemption_count"] += 1
            continue
        usage["base_count"] += 1
        if bucket == "veteran_exception":
            usage["veteran_exception_count"] += 1
        else:
            usage["developmental_count"] += 1
    return usage


def elevation_count(con: sqlite3.Connection, player_id: int, season: int) -> int:
    return int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM practice_squad_moves
            WHERE player_id = ? AND season = ? AND move_type = 'Elevate'
            """,
            (player_id, season),
        ).fetchone()[0]
        or 0
    )


def weekly_elevation_count(con: sqlite3.Connection, team_id: int, season: int) -> int:
    week = current_week(con)
    if week is not None:
        row = con.execute(
            """
            SELECT COUNT(*)
            FROM practice_squad_moves
            WHERE team_id = ?
              AND season = ?
              AND move_type = 'Elevate'
              AND week = ?
            """,
            (team_id, season, week),
        ).fetchone()
    else:
        row = con.execute(
            """
            SELECT COUNT(*)
            FROM practice_squad_moves
            WHERE team_id = ?
              AND season = ?
              AND move_type = 'Elevate'
              AND move_date = ?
            """,
            (team_id, season, today(con)),
        ).fetchone()
    return int(row[0] or 0)


def practice_squad_eligibility(
    con: sqlite3.Connection,
    player: sqlite3.Row,
    team: sqlite3.Row,
    rule_set: sqlite3.Row,
    *,
    season: int,
) -> dict[str, object]:
    bucket, bucket_reason = practice_squad_bucket(player)
    usage = practice_squad_usage(con, int(team["team_id"]), rule_set)
    base_limit = int(rule_set["practice_squad_limit"] or 0)
    ipp_limit = int(rule_set["practice_squad_international_exemption_limit"] or 0)
    dev_limit = int(rule_set["practice_squad_developmental_limit"] or PRACTICE_SQUAD_DEVELOPMENTAL_LIMIT)
    vet_limit = int(rule_set["practice_squad_veteran_exception_limit"] or PRACTICE_SQUAD_VETERAN_EXCEPTION_LIMIT)
    player_elevations = elevation_count(con, int(player["player_id"]), season)
    blockers: list[str] = []
    reasons = [bucket_reason]
    already_current = player["status"] == PRACTICE_SQUAD_STATUS and int(player["team_id"] or 0) == int(team["team_id"])

    if int(rule_set["practice_squad_enabled"] or 0) == 0:
        blockers.append(f"Practice squads are not enabled during {rule_set['phase']}.")
    if player["status"] == "Retired":
        blockers.append("Retired players cannot be assigned.")
    if active_contract_aav(con, int(player["player_id"])) >= 2_500_000 and row_int(player, "years_exp", 0) >= 3:
        blockers.append("Veteran contract is too expensive for a realistic practice-squad stash.")
    if row_int(player, "overall", 0) >= 70:
        blockers.append("Current rating is too high for a realistic practice-squad stash.")
    if row_int(player, "age", 0) >= 30 and row_int(player, "overall", 0) >= 65:
        blockers.append("Established veteran should be kept active or released, not stashed on the practice squad.")
    if player["status"] == "Active":
        reasons.append("Would need to be waived/cut down first and could be claimed before signing to the practice squad.")
    if already_current:
        reasons.append("Already assigned to this practice squad.")
    elif player["status"] == PRACTICE_SQUAD_STATUS:
        blockers.append("Already on another team's practice squad.")

    can_use_ipp = bucket == "international_exemption" and usage["international_exemption_count"] < ipp_limit
    if bucket == "veteran_exception" and usage["veteran_exception_count"] >= vet_limit and not can_use_ipp and not already_current:
        blockers.append(f"Veteran-exception bucket is full ({usage['veteran_exception_count']}/{vet_limit}).")
    if bucket in {"developmental", "international_exemption"} and not can_use_ipp and usage["developmental_count"] >= dev_limit and not already_current:
        blockers.append(f"Developmental bucket is full ({usage['developmental_count']}/{dev_limit}).")
    if usage["base_count"] >= base_limit and not can_use_ipp and not already_current:
        blockers.append(f"Normal practice-squad slots are full ({usage['base_count']}/{base_limit}).")
    if usage["total"] >= base_limit + ipp_limit and not already_current:
        blockers.append(f"Practice squad is at the total limit ({usage['total']}/{base_limit + ipp_limit}).")

    if player_elevations >= int(rule_set["practice_squad_elevation_limit"] or PRACTICE_SQUAD_ELEVATION_LIMIT):
        reasons.append("Out of standard elevations; would need a 53-man signing to play on gameday.")
    elif player_elevations:
        reasons.append(
            f"{player_elevations}/{int(rule_set['practice_squad_elevation_limit'])} standard elevations used."
        )

    return {
        "player_id": int(player["player_id"]),
        "player_name": player_name(player),
        "position": player["position"],
        "team": team["abbreviation"],
        "current_status": player["status"],
        "age": row_int(player, "age", 0),
        "years_exp": row_int(player, "years_exp", 0),
        "overall": row_int(player, "overall", 0),
        "potential": row_int(player, "potential", 0),
        "is_rookie": row_int(player, "is_rookie", 0),
        "is_international_pathway": int(player_is_international_pathway(player)),
        "bucket": bucket,
        "eligible": not blockers,
        "reasons": reasons,
        "blockers": blockers,
        "usage": dict(usage),
        "elevations_used": player_elevations,
        "elevations_remaining": max(
            0,
            int(rule_set["practice_squad_elevation_limit"] or PRACTICE_SQUAD_ELEVATION_LIMIT) - player_elevations,
        ),
    }


def practice_squad_eligibility_rows(
    con: sqlite3.Connection,
    *,
    team: sqlite3.Row,
    season: int,
    rule_set: sqlite3.Row,
    include_active: bool = True,
    include_all_active: bool = False,
    include_current: bool = True,
    include_blocked: bool = True,
    limit: int = 80,
) -> list[dict[str, object]]:
    statuses = ["Free Agent", WAIVED_STATUS]
    own_statuses = ["Active"]
    if include_current:
        own_statuses.append(PRACTICE_SQUAD_STATUS)
    where = [
        "(p.team_id IS NULL AND COALESCE(p.status, 'Free Agent') IN ({fa}))".format(
            fa=",".join("?" for _ in statuses)
        )
    ]
    params: list[object] = list(statuses)
    if include_active or include_current:
        active_filter = ""
        if include_active and not include_all_active:
            active_filter = (
                " AND (COALESCE(p.status, 'Active') <> 'Active' "
                "OR COALESCE(p.overall, 50) <= 70 "
                "OR (COALESCE(p.is_rookie, 0) = 1 AND COALESCE(p.overall, 50) <= 72))"
            )
        where.append(
            "(p.team_id = ? AND COALESCE(p.status, 'Active') IN ({own}){active_filter})".format(
                own=",".join("?" for _ in own_statuses),
                active_filter=active_filter,
            )
        )
        params.append(int(team["team_id"]))
        params.extend(own_statuses)
    rows = con.execute(
        f"""
        SELECT p.*
        FROM players p
        WHERE COALESCE(p.status, 'Active') <> 'Retired'
          AND ({' OR '.join(where)})
        ORDER BY
            CASE WHEN p.status = ? THEN 0 WHEN p.team_id = ? THEN 1 ELSE 2 END,
            COALESCE(p.potential, p.overall, 50) DESC,
            COALESCE(p.overall, 50) DESC,
            p.age ASC,
            p.last_name,
            p.first_name
        LIMIT ?
        """,
        [*params, PRACTICE_SQUAD_STATUS, int(team["team_id"]), max(1, limit * 3)],
    ).fetchall()
    results = [
        practice_squad_eligibility(con, player, team, rule_set, season=season)
        for player in rows
    ]
    if not include_blocked:
        results = [row for row in results if row["eligible"]]
    results.sort(
        key=lambda row: (
            0 if row["current_status"] == PRACTICE_SQUAD_STATUS else 1,
            0 if row["eligible"] else 1,
            -int(row["potential"] or 0),
            -int(row["overall"] or 0),
            str(row["player_name"]),
        )
    )
    return results[:limit]


def action_sign_practice_squad(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_rules(con)
    season = args.season if args.season is not None else current_season(con)
    team = get_team(con, args.team)
    rule_set = practice_squad_rule_set(con, season, args.phase)
    if int(rule_set["practice_squad_enabled"]) == 0 and not args.force:
        raise ValueError(f"Practice squads are not enabled in {rule_set['season']} {rule_set['phase']}. Use --force to override.")
    player = find_player(con, args.player)
    if player["status"] == PRACTICE_SQUAD_STATUS:
        raise ValueError(f"{player_name(player)} is already on a practice squad.")
    if player["status"] == WAIVED_STATUS:
        open_waiver = con.execute(
            "SELECT waiver_id FROM waiver_wire WHERE player_id = ? AND status = 'Open'",
            (player["player_id"],),
        ).fetchone()
        if open_waiver and not args.force:
            raise ValueError(f"{player_name(player)} is still on open waivers. Process or force first.")
        if open_waiver:
            con.execute(
                "UPDATE waiver_wire SET status = 'Cancelled', resolved_at = datetime('now') WHERE waiver_id = ?",
                (open_waiver["waiver_id"],),
            )
    elif player["status"] not in {"Free Agent"} and not args.force:
        raise ValueError(f"{player_name(player)} has status {player['status']}; use --force to sign anyway.")
    eligibility = practice_squad_eligibility(con, player, team, rule_set, season=season)
    if not eligibility["eligible"] and not args.force:
        raise ValueError(
            f"{player_name(player)} is not practice-squad eligible for {team['abbreviation']}: "
            + " ".join(str(item) for item in eligibility["blockers"])
        )

    old_status = player["status"]
    set_player_status(
        con,
        player=player,
        team_id=int(team["team_id"]),
        new_status=PRACTICE_SQUAD_STATUS,
        season=season,
        reason=args.notes or f"Signed to {team['abbreviation']} practice squad.",
    )
    record_practice_squad_move(
        con,
        player_id=player["player_id"],
        team_id=int(team["team_id"]),
        season=season,
        move_type="Sign",
        from_status=old_status,
        to_status=PRACTICE_SQUAD_STATUS,
        notes=args.notes,
    )
    transaction_id = log_transaction(
        con,
        transaction_type="Practice Squad Signing",
        season=season,
        team_id=int(team["team_id"]),
        player_id=player["player_id"],
        to_team_id=int(team["team_id"]),
        old_status=old_status,
        new_status=PRACTICE_SQUAD_STATUS,
        description=args.notes or f"Signed {player_name(player)} to {team['abbreviation']} practice squad.",
    )
    print(f"Signed {player_name(player)} to {team['abbreviation']} practice squad (transaction {transaction_id}).")


def action_release_practice_squad(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_rules(con)
    season = args.season if args.season is not None else current_season(con)
    team = get_team(con, args.team) if args.team else None
    player = find_player(
        con,
        args.player,
        team_id=team["team_id"] if team else None,
        statuses={PRACTICE_SQUAD_STATUS},
    )
    from_team_id = int(player["team_id"])
    old_status = player["status"]
    set_player_status(
        con,
        player=player,
        team_id=None,
        new_status="Free Agent",
        season=season,
        reason=args.notes or "Released from practice squad.",
    )
    record_practice_squad_move(
        con,
        player_id=player["player_id"],
        team_id=from_team_id,
        season=season,
        move_type="Release",
        from_status=old_status,
        to_status="Free Agent",
        notes=args.notes,
    )
    transaction_id = log_transaction(
        con,
        transaction_type="Practice Squad Release",
        season=season,
        team_id=from_team_id,
        player_id=player["player_id"],
        from_team_id=from_team_id,
        old_status=old_status,
        new_status="Free Agent",
        description=args.notes or f"Released {player_name(player)} from practice squad.",
    )
    print(f"Released {player_name(player)} from practice squad (transaction {transaction_id}).")


def action_elevate_practice_squad(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_rules(con)
    season = args.season if args.season is not None else current_season(con)
    team = get_team(con, args.team)
    player = find_player(con, args.player, team_id=int(team["team_id"]), statuses={PRACTICE_SQUAD_STATUS})
    rule_set = practice_squad_rule_set(con, season, args.phase)
    player_elevation_count = elevation_count(con, int(player["player_id"]), season)
    elevation_limit = int(rule_set["practice_squad_elevation_limit"] or PRACTICE_SQUAD_ELEVATION_LIMIT)
    if player_elevation_count >= elevation_limit and not args.force:
        raise ValueError(f"{player_name(player)} already has {player_elevation_count} practice squad elevations this season.")
    team_weekly_count = weekly_elevation_count(con, int(team["team_id"]), season)
    weekly_limit = int(rule_set["practice_squad_weekly_elevation_limit"] or PRACTICE_SQUAD_WEEKLY_ELEVATION_LIMIT)
    if team_weekly_count >= weekly_limit and not args.force:
        raise ValueError(
            f"{team['abbreviation']} already used {team_weekly_count}/{weekly_limit} practice squad elevations for this week/date."
        )
    active_count = active_roster_count(con, int(team["team_id"]))
    elevation_roster_limit = int(rule_set["active_roster_limit"]) + weekly_limit
    if active_count >= elevation_roster_limit and not args.force:
        raise ValueError(f"{team['abbreviation']} active roster is at {active_count}/{elevation_roster_limit} with standard elevations.")

    old_status = player["status"]
    set_player_status(
        con,
        player=player,
        team_id=int(team["team_id"]),
        new_status="Active",
        season=season,
        reason=args.notes or "Practice squad standard elevation.",
    )
    record_practice_squad_move(
        con,
        player_id=player["player_id"],
        team_id=int(team["team_id"]),
        season=season,
        move_type="Elevate",
        from_status=old_status,
        to_status="Active",
        notes=args.notes,
    )
    transaction_id = log_transaction(
        con,
        transaction_type="Practice Squad Elevation",
        season=season,
        team_id=int(team["team_id"]),
        player_id=player["player_id"],
        old_status=old_status,
        new_status="Active",
        description=args.notes or f"Elevated {player_name(player)} from practice squad.",
    )
    print(
        f"Elevated {player_name(player)} to {team['abbreviation']} active roster "
        f"({player_elevation_count + 1}/{elevation_limit}, transaction {transaction_id})."
    )


def action_return_practice_squad(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_rules(con)
    season = args.season if args.season is not None else current_season(con)
    team = get_team(con, args.team)
    player = find_player(con, args.player, team_id=int(team["team_id"]), statuses={"Active"})
    old_status = player["status"]
    set_player_status(
        con,
        player=player,
        team_id=int(team["team_id"]),
        new_status=PRACTICE_SQUAD_STATUS,
        season=season,
        reason=args.notes or "Returned after practice squad elevation.",
    )
    record_practice_squad_move(
        con,
        player_id=player["player_id"],
        team_id=int(team["team_id"]),
        season=season,
        move_type="Return",
        from_status=old_status,
        to_status=PRACTICE_SQUAD_STATUS,
        notes=args.notes,
    )
    transaction_id = log_transaction(
        con,
        transaction_type="Practice Squad Return",
        season=season,
        team_id=int(team["team_id"]),
        player_id=player["player_id"],
        old_status=old_status,
        new_status=PRACTICE_SQUAD_STATUS,
        description=args.notes or f"Returned {player_name(player)} to practice squad.",
    )
    print(f"Returned {player_name(player)} to {team['abbreviation']} practice squad (transaction {transaction_id}).")


def action_practice_squad_eligibility(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_rules(con)
    season = args.season if args.season is not None else current_season(con)
    team = get_team(con, args.team)
    rule_set = practice_squad_rule_set(con, season, args.phase)
    usage = practice_squad_usage(con, int(team["team_id"]), rule_set)
    rows = practice_squad_eligibility_rows(
        con,
        team=team,
        season=season,
        rule_set=rule_set,
        include_active=args.include_active,
        include_all_active=args.include_all_active,
        include_current=not args.hide_current,
        include_blocked=args.include_blocked,
        limit=args.limit,
    )
    print(f"{team['abbreviation']} Practice Squad Eligibility ({season} {rule_set['phase']})")
    print(
        f"Usage: {usage['total']}/{int(rule_set['practice_squad_limit']) + int(rule_set['practice_squad_international_exemption_limit'])} total, "
        f"{usage['base_count']}/{rule_set['practice_squad_limit']} normal, "
        f"{usage['developmental_count']}/{rule_set['practice_squad_developmental_limit']} developmental, "
        f"{usage['veteran_exception_count']}/{rule_set['practice_squad_veteran_exception_limit']} veteran exceptions, "
        f"{usage['international_exemption_count']}/{rule_set['practice_squad_international_exemption_limit']} IPP."
    )
    if not rows:
        print("No matching candidates.")
        return
    for row in rows:
        marker = "YES" if row["eligible"] else "NO "
        why = "; ".join(row["reasons"] if row["eligible"] else row["blockers"])
        print(
            f"{marker} {row['player_name']:<24} {row['position']:<4} "
            f"{row['current_status']:<15} exp {row['years_exp']:<2} "
            f"{row['bucket']:<24} {why}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up and validate roster rules.")
    parser.add_argument("--db", default=str(DB_PATH), help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("setup", help="Create roster rule tables/views and seed default rules.")

    show_parser = subparsers.add_parser("show-rules", help="Print a seeded roster rule set.")
    show_parser.add_argument("--season", type=int)
    show_parser.add_argument("--phase", default=DEFAULT_PHASE)

    validate_parser = subparsers.add_parser("validate", help="Validate one team or all teams.")
    validate_parser.add_argument("--season", type=int)
    validate_parser.add_argument("--phase", default=DEFAULT_PHASE)
    target = validate_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--team", help="Team abbreviation.")
    target.add_argument("--all", action="store_true", help="Validate all teams.")
    validate_parser.add_argument("--summary-only", action="store_true", help="Hide per-issue detail.")
    validate_parser.add_argument("--include-info", action="store_true", help="Include informational messages.")
    validate_parser.add_argument("--no-save", action="store_true", help="Do not save validation run rows.")

    priority_parser = subparsers.add_parser("seed-waiver-priority", help="Seed waiver priority for a season.")
    priority_parser.add_argument("--season", type=int)
    priority_parser.add_argument("--force", action="store_true", help="Replace existing priority rows.")

    wire_parser = subparsers.add_parser("waiver-wire", help="Show waiver wire entries.")
    wire_parser.add_argument("--status", default="Open", choices=["Open", "Claimed", "Cleared", "Cancelled"])
    wire_parser.add_argument("--all", action="store_true", help="Show every status.")
    wire_parser.add_argument("--claims", action="store_true", help="Show claims under each waiver entry.")
    wire_parser.add_argument("--limit", type=int, default=50)

    waive_parser = subparsers.add_parser("waive", help="Place a rostered player on waivers.")
    waive_parser.add_argument("--player", required=True)
    waive_parser.add_argument("--team", help="Optional team abbreviation to disambiguate.")
    waive_parser.add_argument("--season", type=int)
    waive_parser.add_argument("--waiver-date")
    waive_parser.add_argument("--deadline-date")
    waive_parser.add_argument("--reason")
    waive_parser.add_argument("--dry-run", action="store_true")

    claim_parser = subparsers.add_parser("claim-waiver", help="File a waiver claim.")
    claim_target = claim_parser.add_mutually_exclusive_group(required=True)
    claim_target.add_argument("--waiver-id", type=int)
    claim_target.add_argument("--player")
    claim_parser.add_argument("--team", required=True)
    claim_parser.add_argument("--season", type=int)
    claim_parser.add_argument("--notes")
    claim_parser.add_argument("--dry-run", action="store_true")

    process_parser = subparsers.add_parser("process-waivers", help="Process waiver entries whose deadlines have passed.")
    process_parser.add_argument("--date")
    process_parser.add_argument("--all-open", action="store_true", help="Process all open waivers regardless of deadline.")
    process_parser.add_argument("--dry-run", action="store_true")

    sign_ps_parser = subparsers.add_parser("sign-ps", help="Sign a player to a practice squad.")
    sign_ps_parser.add_argument("--player", required=True)
    sign_ps_parser.add_argument("--team", required=True)
    sign_ps_parser.add_argument("--season", type=int)
    sign_ps_parser.add_argument("--phase", help="Roster rule phase. Defaults to current date phase.")
    sign_ps_parser.add_argument("--notes")
    sign_ps_parser.add_argument("--force", action="store_true")
    sign_ps_parser.add_argument("--dry-run", action="store_true")

    release_ps_parser = subparsers.add_parser("release-ps", help="Release a practice squad player.")
    release_ps_parser.add_argument("--player", required=True)
    release_ps_parser.add_argument("--team", help="Optional team abbreviation to disambiguate.")
    release_ps_parser.add_argument("--season", type=int)
    release_ps_parser.add_argument("--notes")
    release_ps_parser.add_argument("--dry-run", action="store_true")

    elevate_ps_parser = subparsers.add_parser("elevate-ps", help="Elevate a practice squad player to the active roster.")
    elevate_ps_parser.add_argument("--player", required=True)
    elevate_ps_parser.add_argument("--team", required=True)
    elevate_ps_parser.add_argument("--season", type=int)
    elevate_ps_parser.add_argument("--phase")
    elevate_ps_parser.add_argument("--notes")
    elevate_ps_parser.add_argument("--force", action="store_true")
    elevate_ps_parser.add_argument("--dry-run", action="store_true")

    return_ps_parser = subparsers.add_parser("return-ps", help="Return an elevated player to the practice squad.")
    return_ps_parser.add_argument("--player", required=True)
    return_ps_parser.add_argument("--team", required=True)
    return_ps_parser.add_argument("--season", type=int)
    return_ps_parser.add_argument("--notes")
    return_ps_parser.add_argument("--dry-run", action="store_true")

    ps_elig_parser = subparsers.add_parser("ps-eligibility", help="Show who can still be assigned to a team's practice squad and why.")
    ps_elig_parser.add_argument("--team", required=True)
    ps_elig_parser.add_argument("--season", type=int)
    ps_elig_parser.add_argument("--phase")
    ps_elig_parser.add_argument("--limit", type=int, default=40)
    ps_elig_parser.add_argument("--include-active", action=argparse.BooleanOptionalAction, default=True)
    ps_elig_parser.add_argument("--include-all-active", action="store_true", help="Include stars and clear 53-man players, not just fringe/developmental active players.")
    ps_elig_parser.add_argument("--hide-current", action="store_true", help="Hide players already on this practice squad.")
    ps_elig_parser.add_argument("--include-blocked", action=argparse.BooleanOptionalAction, default=True)

    args = parser.parse_args()
    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    should_commit = False
    dry_run = bool(getattr(args, "dry_run", False))
    try:
        if dry_run:
            con.execute("BEGIN")
        if args.command == "setup":
            action_setup(con)
        elif args.command == "show-rules":
            action_show_rules(con, args)
            con.commit()
        elif args.command == "validate":
            action_validate(con, args)
        elif args.command == "seed-waiver-priority":
            action_seed_waiver_priority(con, args)
        elif args.command == "waiver-wire":
            action_waiver_wire(con, args)
        elif args.command == "waive":
            action_waive(con, args)
            should_commit = True
        elif args.command == "claim-waiver":
            action_claim_waiver(con, args)
            should_commit = True
        elif args.command == "process-waivers":
            action_process_waivers(con, args)
            should_commit = True
        elif args.command == "sign-ps":
            action_sign_practice_squad(con, args)
            should_commit = True
        elif args.command == "release-ps":
            action_release_practice_squad(con, args)
            should_commit = True
        elif args.command == "elevate-ps":
            action_elevate_practice_squad(con, args)
            should_commit = True
        elif args.command == "return-ps":
            action_return_practice_squad(con, args)
            should_commit = True
        elif args.command == "ps-eligibility":
            action_practice_squad_eligibility(con, args)
        if should_commit:
            if dry_run:
                con.rollback()
                print("Dry run only. Rolled back changes.")
            else:
                con.commit()
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
