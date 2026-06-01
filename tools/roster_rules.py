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
from datetime import date, datetime, timedelta
from pathlib import Path

from setup_contract_years import ensure_schema as ensure_contract_schema
from setup_contract_years import rebuild_contract_years, sync_team_cap_space
from setup_transactions_cap_ledger import ensure_schema as ensure_transaction_schema
from setup_transactions_cap_ledger import insert_transaction

try:
    import jersey_numbers
except ImportError:  # pragma: no cover - supports package-style imports.
    from tools import jersey_numbers


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
IR_STATUS = "IR"
IR_MIN_RETURN_GAMES = 4
IR_REGULAR_SEASON_RETURN_LIMIT = 8
IR_POSTSEASON_EXTRA_RETURN_LIMIT = 2
IR_PRE_CUTDOWN_RETURN_LIMIT = 2
IR_PLAYER_RETURN_LIMIT = 2

POSITION_GROUPS: dict[str, tuple[str, ...]] = {
    "QB": ("QB",),
    "RB": ("RB", "FB"),
    "WR": ("WR",),
    "TE": ("TE",),
    "OL": ("OT", "OG", "C"),
    "EDGE": ("EDGE", "DE"),
    "IDL": ("IDL", "DT", "NT"),
    "LB": ("ILB", "OLB", "LB"),
    "CB": ("CB", "NB"),
    "S": ("FS", "SS", "S"),
    "ST": ("K", "P", "LS"),
}
POSITION_TO_GROUP = {
    position: group
    for group, positions in POSITION_GROUPS.items()
    for position in positions
}
WAIVER_CLAIM_GROUP_TARGETS = {
    "QB": 2,
    "RB": 3,
    "WR": 5,
    "TE": 3,
    "OL": 8,
    "EDGE": 4,
    "IDL": 4,
    "LB": 5,
    "CB": 5,
    "S": 4,
    "ST": 3,
}

WAIVER_PLAYER_MOVE_COOLDOWN_DAYS = 28
WAIVER_PLAYER_CHURN_LOOKBACK_DAYS = 75
WAIVER_PLAYER_CHURN_LIMIT = 4
WAIVER_TEAM_WEEKLY_CLAIM_LIMIT = 2
WAIVER_TEAM_MONTHLY_CLAIM_LIMIT = 5


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
            ('IR', 'Injured Reserve', 0, 1, 0, 0, 'Reserve/Injured. Counts against the cap but not the active roster limit.'),
            ('PUP', 'Physically Unable To Perform', 0, 1, 0, 0, 'Reserve/PUP. Counts against the cap but not the active roster limit.'),
            ('NFI', 'Non-Football Injury', 0, 1, 0, 0, 'Reserve/NFI. Counts against the cap but not the active roster limit.'),
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
            ('Practice Squad Return', 'Roster', 'Elevated player returned to the practice squad.'),
            ('Injured Reserve', 'Roster', 'Player placed on Reserve/Injured.'),
            ('Activated From IR', 'Roster', 'Player activated from Reserve/Injured.')
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

        CREATE TABLE IF NOT EXISTS injured_reserve_designations (
            designation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            placed_date TEXT NOT NULL,
            placed_phase TEXT,
            active_injury_id INTEGER,
            injury_history_id INTEGER,
            designated_to_return INTEGER NOT NULL DEFAULT 1,
            pre_cutdown_return_slot INTEGER NOT NULL DEFAULT 0,
            return_games_required INTEGER NOT NULL DEFAULT 4,
            eligible_return_date TEXT,
            return_window_start TEXT,
            return_window_end TEXT,
            activation_date TEXT,
            status TEXT NOT NULL DEFAULT 'On IR',
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_ir_designations_player_status
            ON injured_reserve_designations(player_id, season, status);

        CREATE INDEX IF NOT EXISTS idx_ir_designations_team_status
            ON injured_reserve_designations(team_id, season, status);

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
    if row and row["setting_value"]:
        return row["setting_value"]
    try:
        row = con.execute(
            """
            SELECT "current_date"
            FROM active_game_save_view
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row and row["current_date"]:
            return str(row["current_date"])
    except sqlite3.OperationalError:
        pass
    try:
        row = con.execute(
            """
            SELECT "current_date"
            FROM game_saves
            WHERE status = 'active'
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row and row["current_date"]:
            return str(row["current_date"])
    except sqlite3.OperationalError:
        pass
    return f"{current_season(con)}-06-01"


def days_before(value: str | None, days: int) -> str:
    try:
        anchor = date.fromisoformat(str(value or ""))
    except ValueError:
        anchor = date(2026, 6, 1)
    return (anchor - timedelta(days=days)).isoformat()


def recent_transaction_count(
    con: sqlite3.Connection,
    *,
    player_id: int | None = None,
    team_id: int | None = None,
    days: int,
    before_date: str | None = None,
    transaction_types: tuple[str, ...] | None = None,
    source: str | None = None,
) -> int:
    if not table_exists(con, "transaction_log"):
        return 0
    anchor = before_date or today(con)
    since = days_before(anchor, days)
    where = [
        "date(transaction_date) >= date(?)",
        "date(transaction_date) <= date(?)",
    ]
    params: list[object] = [since, anchor]
    if player_id is not None:
        where.append("player_id = ?")
        params.append(player_id)
    if team_id is not None:
        where.append("(team_id = ? OR to_team_id = ?)")
        params.extend([team_id, team_id])
    if transaction_types:
        where.append(f"transaction_type IN ({','.join('?' for _ in transaction_types)})")
        params.extend(transaction_types)
    if source:
        where.append("source = ?")
        params.append(source)
    row = con.execute(
        f"SELECT COUNT(*) AS count FROM transaction_log WHERE {' AND '.join(where)}",
        params,
    ).fetchone()
    return int(row["count"] or 0) if row else 0


def player_recently_moved(
    con: sqlite3.Connection,
    *,
    player_id: int,
    before_date: str | None = None,
    days: int = WAIVER_PLAYER_MOVE_COOLDOWN_DAYS,
) -> bool:
    return recent_transaction_count(
        con,
        player_id=player_id,
        days=days,
        before_date=before_date,
        transaction_types=(
            "Waiver Claim",
            "Practice Squad Poaching",
            "Signing",
            "Roster Status Change",
        ),
    ) > 0


def player_has_waiver_churn(
    con: sqlite3.Connection,
    *,
    player_id: int,
    before_date: str | None = None,
) -> bool:
    return recent_transaction_count(
        con,
        player_id=player_id,
        days=WAIVER_PLAYER_CHURN_LOOKBACK_DAYS,
        before_date=before_date,
        transaction_types=(
            "Waiver",
            "Waiver Claim",
            "Practice Squad Poaching",
            "Practice Squad Signing",
            "Roster Status Change",
            "Release",
            "Signing",
        ),
    ) >= WAIVER_PLAYER_CHURN_LIMIT


def team_claim_cooldown_active(
    con: sqlite3.Connection,
    *,
    team_id: int,
    before_date: str | None = None,
) -> bool:
    return (
        recent_transaction_count(
            con,
            team_id=team_id,
            days=7,
            before_date=before_date,
            transaction_types=("Waiver Claim",),
        )
        >= WAIVER_TEAM_WEEKLY_CLAIM_LIMIT
        or recent_transaction_count(
            con,
            team_id=team_id,
            days=30,
            before_date=before_date,
            transaction_types=("Waiver Claim",),
        )
        >= WAIVER_TEAM_MONTHLY_CLAIM_LIMIT
    )


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


def phase_for_transactions(con: sqlite3.Connection, target_date: str | None = None) -> str:
    target_date = target_date or today(con)
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
    effective_date: str | None = None,
) -> None:
    old_status = player["status"]
    con.execute(
        "UPDATE players SET team_id = ?, status = ? WHERE player_id = ?",
        (team_id, new_status, player["player_id"]),
    )
    if team_id is not None and new_status not in {"Free Agent", "Retired", WAIVED_STATUS}:
        jersey_numbers.assign_player_number(
            con,
            int(player["player_id"]),
            team_id=int(team_id),
            source="roster_status_change",
        )
    con.execute(
        """
        INSERT INTO player_roster_status_history (
            player_id, old_status, new_status, effective_date, season, reason
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (player["player_id"], old_status, new_status, effective_date or today(con), season, reason),
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
    transaction_date: str | None = None,
) -> int:
    tx_date = transaction_date or today(con)
    transaction_id, _inserted = insert_transaction(
        con,
        transaction_date=tx_date,
        season=season,
        phase=phase_for_transactions(con, tx_date),
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
    expected_source, draft_order_year = waiver_priority_basis(con, season)
    if force:
        con.execute("DELETE FROM waiver_priority WHERE season = ?", (season,))
    existing = con.execute(
        "SELECT COUNT(*) AS count, MIN(source) AS source, MAX(source) AS max_source FROM waiver_priority WHERE season = ?",
        (season,),
    ).fetchone()
    existing_count = int(existing["count"] or 0) if existing else 0
    if existing_count and existing["source"] == existing["max_source"] == expected_source:
        return 0
    if existing_count:
        con.execute("DELETE FROM waiver_priority WHERE season = ?", (season,))

    ordered_team_ids: list[int] = []
    seen: set[int] = set()

    source = expected_source
    if source == "current_standings" and table_exists(con, "season_team_records"):
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
            (draft_order_year or season,),
        ).fetchall()
        if len(draft_rows) < 32 and (draft_order_year or season) != season + 1:
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


def league_trade_deadline(con: sqlite3.Connection, season: int) -> str | None:
    if not table_exists(con, "league_calendar_events"):
        return None
    row = con.execute(
        """
        SELECT event_start_date
        FROM league_calendar_events
        WHERE league_year = ?
          AND event_code = 'TRADE_DEADLINE'
        ORDER BY event_start_date
        LIMIT 1
        """,
        (season,),
    ).fetchone()
    return str(row["event_start_date"]) if row and row["event_start_date"] else None


def waiver_rule_season_for_date(waiver_date: str, fallback_season: int) -> int:
    """Map a transaction date to the season whose waiver deadline controls vets."""
    try:
        parsed = datetime.fromisoformat(str(waiver_date)).date()
    except ValueError:
        return fallback_season
    if parsed.month <= 2:
        return parsed.year - 1
    return parsed.year


def calendar_event_date(con: sqlite3.Connection, season: int, event_code: str) -> str | None:
    if not table_exists(con, "league_calendar_events"):
        return None
    row = con.execute(
        """
        SELECT event_start_date
        FROM league_calendar_events
        WHERE league_year = ?
          AND event_code = ?
        ORDER BY event_start_date
        LIMIT 1
        """,
        (season, event_code),
    ).fetchone()
    return str(row["event_start_date"]) if row and row["event_start_date"] else None


def final_roster_cutdown_date(con: sqlite3.Connection, season: int) -> str | None:
    return calendar_event_date(con, season, "FINAL_ROSTER_CUTDOWN_53")


def postseason_start_date(con: sqlite3.Connection, season: int) -> str | None:
    if not table_exists(con, "season_games"):
        return None
    row = con.execute(
        """
        SELECT MIN(game_date) AS postseason_start
        FROM season_games
        WHERE season = ?
          AND game_type = 'POST'
          AND game_date IS NOT NULL
        """,
        (season,),
    ).fetchone()
    return str(row["postseason_start"]) if row and row["postseason_start"] else None


def team_game_dates_after(
    con: sqlite3.Connection,
    *,
    team_id: int,
    season: int,
    placed_date: str,
    game_types: tuple[str, ...] = ("REG", "POST"),
) -> list[str]:
    if not table_exists(con, "season_games"):
        return []
    placeholders = ",".join("?" for _ in game_types)
    rows = con.execute(
        f"""
        SELECT game_date
        FROM season_games
        WHERE season = ?
          AND game_type IN ({placeholders})
          AND game_date IS NOT NULL
          AND date(game_date) > date(?)
          AND (home_team_id = ? OR away_team_id = ?)
        ORDER BY date(game_date), week, game_id
        """,
        (season, *game_types, placed_date, team_id, team_id),
    ).fetchall()
    return [str(row["game_date"]) for row in rows if row["game_date"]]


def ir_eligible_return_date(
    con: sqlite3.Connection,
    *,
    team_id: int,
    season: int,
    placed_date: str,
    required_games: int = IR_MIN_RETURN_GAMES,
) -> str:
    game_dates = team_game_dates_after(
        con,
        team_id=team_id,
        season=season,
        placed_date=placed_date,
    )
    if len(game_dates) >= required_games:
        return game_dates[required_games - 1]
    return (datetime.fromisoformat(placed_date) + timedelta(days=required_games * 7)).date().isoformat()


def ir_return_limit(con: sqlite3.Connection, season: int, as_of_date: str | None = None) -> int:
    target_date = as_of_date or today(con)
    postseason_start = postseason_start_date(con, season)
    if postseason_start and target_date >= postseason_start:
        return IR_REGULAR_SEASON_RETURN_LIMIT + IR_POSTSEASON_EXTRA_RETURN_LIMIT
    return IR_REGULAR_SEASON_RETURN_LIMIT


def ir_returns_used(con: sqlite3.Connection, team_id: int, season: int) -> int:
    if not table_exists(con, "injured_reserve_designations"):
        return 0
    row = con.execute(
        """
        SELECT COUNT(*) AS used
        FROM injured_reserve_designations
        WHERE team_id = ?
          AND season = ?
          AND status = 'Activated'
          AND designated_to_return = 1
        """,
        (team_id, season),
    ).fetchone()
    return int(row["used"] or 0) if row else 0


def player_ir_returns_used(con: sqlite3.Connection, player_id: int, season: int) -> int:
    if not table_exists(con, "injured_reserve_designations"):
        return 0
    row = con.execute(
        """
        SELECT COUNT(*) AS used
        FROM injured_reserve_designations
        WHERE player_id = ?
          AND season = ?
          AND status = 'Activated'
          AND designated_to_return = 1
        """,
        (player_id, season),
    ).fetchone()
    return int(row["used"] or 0) if row else 0


def pre_cutdown_ir_return_slots_used(con: sqlite3.Connection, team_id: int, season: int) -> int:
    if not table_exists(con, "injured_reserve_designations"):
        return 0
    row = con.execute(
        """
        SELECT COUNT(*) AS used
        FROM injured_reserve_designations
        WHERE team_id = ?
          AND season = ?
          AND pre_cutdown_return_slot = 1
        """,
        (team_id, season),
    ).fetchone()
    return int(row["used"] or 0) if row else 0


def active_ir_designation(con: sqlite3.Connection, player_id: int, season: int) -> sqlite3.Row | None:
    if not table_exists(con, "injured_reserve_designations"):
        return None
    return con.execute(
        """
        SELECT *
        FROM injured_reserve_designations
        WHERE player_id = ?
          AND season = ?
          AND status = 'On IR'
        ORDER BY designation_id DESC
        LIMIT 1
        """,
        (player_id, season),
    ).fetchone()


def latest_active_injury(con: sqlite3.Connection, player_id: int) -> sqlite3.Row | None:
    if not table_exists(con, "active_player_injuries"):
        return None
    return con.execute(
        """
        SELECT *
        FROM active_player_injuries
        WHERE player_id = ?
          AND resolved_at IS NULL
        ORDER BY expected_games DESC, return_earliest_date DESC, active_injury_id DESC
        LIMIT 1
        """,
        (player_id,),
    ).fetchone()


def ir_summary_for_player(con: sqlite3.Connection, player: sqlite3.Row | dict[str, object], season: int) -> dict[str, object]:
    player_id = row_int(player, "player_id")
    team_id = row_int(player, "team_id")
    designation = active_ir_designation(con, player_id, season)
    returns_used = ir_returns_used(con, team_id, season) if team_id else 0
    return_limit = ir_return_limit(con, season)
    player_returns = player_ir_returns_used(con, player_id, season)
    if not designation:
        return {
            "onIr": str(player["status"] if isinstance(player, sqlite3.Row) else player.get("status") or "") == IR_STATUS,
            "designatedToReturn": False,
            "eligible": False,
            "reason": "No active IR designation.",
            "returnsUsed": returns_used,
            "returnsLimit": return_limit,
            "playerReturnsUsed": player_returns,
            "playerReturnLimit": IR_PLAYER_RETURN_LIMIT,
        }
    current = today(con)
    designated = bool(int(designation["designated_to_return"] or 0))
    eligible_date = str(designation["eligible_return_date"] or "")
    eligible = bool(designated and eligible_date and current >= eligible_date)
    reason = ""
    if not designated:
        reason = "Season-ending IR; not designated to return."
    elif player_returns >= IR_PLAYER_RETURN_LIMIT:
        eligible = False
        reason = "Player has already used the season return limit."
    elif returns_used >= return_limit:
        eligible = False
        reason = "Team has used all IR return designations."
    elif eligible_date and current < eligible_date:
        reason = f"Eligible after {IR_MIN_RETURN_GAMES} team games ({eligible_date})."
    else:
        reason = "Eligible to activate."
    return {
        "designationId": int(designation["designation_id"]),
        "onIr": True,
        "designatedToReturn": designated,
        "preCutdownReturnSlot": bool(int(designation["pre_cutdown_return_slot"] or 0)),
        "placedDate": designation["placed_date"],
        "eligibleReturnDate": eligible_date,
        "eligible": eligible,
        "seasonEnding": not designated,
        "reason": reason,
        "returnsUsed": returns_used,
        "returnsLimit": return_limit,
        "playerReturnsUsed": player_returns,
        "playerReturnLimit": IR_PLAYER_RETURN_LIMIT,
    }


def ir_statuses_for_players(
    con: sqlite3.Connection,
    player_ids: list[int],
    *,
    season: int,
) -> dict[int, dict[str, object]]:
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = con.execute(
        f"""
        SELECT player_id, team_id, status
        FROM players
        WHERE player_id IN ({placeholders})
        """,
        player_ids,
    ).fetchall()
    return {
        int(row["player_id"]): ir_summary_for_player(con, row, season)
        for row in rows
    }


def place_player_on_ir(
    con: sqlite3.Connection,
    *,
    player_id: int,
    team_abbr: str,
    season: int,
    force: bool = False,
) -> str:
    ensure_schema(con)
    seed_rules(con)
    team = get_team(con, team_abbr)
    player = con.execute(
        """
        SELECT p.*, t.abbreviation AS team
        FROM players p
        JOIN teams t ON t.team_id = p.team_id
        WHERE p.player_id = ?
          AND p.team_id = ?
        """,
        (player_id, int(team["team_id"])),
    ).fetchone()
    if not player:
        raise ValueError(f"Player {player_id} is not on the {team_abbr.upper()} roster.")
    if player["status"] == IR_STATUS:
        return f"{player_name(player)} is already on injured reserve."
    if player["status"] not in {"Active", "Questionable", "Doubtful", "Out"} and not force:
        raise ValueError(f"{player_name(player)} has status {player['status']} and cannot be moved to IR.")
    injury = latest_active_injury(con, player_id)
    if not injury and not force:
        raise ValueError(f"{player_name(player)} does not have an active injury. Use force only for data repair.")

    placed_date = today(con)
    cutdown = final_roster_cutdown_date(con, season)
    before_cutdown = bool(cutdown and placed_date < cutdown)
    cutdown_day = bool(cutdown and placed_date == cutdown)
    pre_slots_used = pre_cutdown_ir_return_slots_used(con, int(team["team_id"]), season)
    designated_to_return = (not cutdown or placed_date > cutdown) or (
        cutdown_day and pre_slots_used < IR_PRE_CUTDOWN_RETURN_LIMIT
    )
    pre_slot = cutdown_day and designated_to_return
    eligible_date = (
        ir_eligible_return_date(
            con,
            team_id=int(team["team_id"]),
            season=season,
            placed_date=placed_date,
            required_games=IR_MIN_RETURN_GAMES,
        )
        if designated_to_return
        else None
    )
    note_parts = []
    if before_cutdown:
        note_parts.append("Placed before final cutdown; season-ending IR under preseason rules.")
    elif cutdown_day:
        if designated_to_return:
            note_parts.append(
                f"Preseason/cutdown IR return slot {pre_slots_used + 1}/{IR_PRE_CUTDOWN_RETURN_LIMIT} used."
            )
        else:
            note_parts.append("Placed on cutdown day without a return slot; season-ending IR.")
    if injury:
        note_parts.append(
            f"{injury['injury_label']} projected {int(injury['expected_games'] or 0)} game(s)."
        )
    notes = " ".join(note_parts)
    cur = con.execute(
        """
        INSERT INTO injured_reserve_designations (
            player_id, team_id, season, placed_date, placed_phase,
            active_injury_id, injury_history_id, designated_to_return,
            pre_cutdown_return_slot, return_games_required, eligible_return_date,
            status, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'On IR', ?)
        """,
        (
            player_id,
            int(team["team_id"]),
            season,
            placed_date,
            phase_for_transactions(con),
            int(injury["active_injury_id"]) if injury else None,
            int(injury["injury_history_id"]) if injury and injury["injury_history_id"] is not None else None,
            1 if designated_to_return else 0,
            1 if pre_slot else 0,
            IR_MIN_RETURN_GAMES,
            eligible_date,
            notes,
        ),
    )
    if injury:
        con.execute(
            """
            UPDATE active_player_injuries
            SET status = 'IR'
            WHERE player_id = ?
              AND resolved_at IS NULL
            """,
            (player_id,),
        )
    set_player_status(
        con,
        player=player,
        team_id=int(team["team_id"]),
        new_status=IR_STATUS,
        season=season,
        reason=notes or "Placed on Reserve/Injured.",
    )
    clear_depth_chart(con, player_id)
    transaction_id = log_transaction(
        con,
        transaction_type="Injured Reserve",
        season=season,
        team_id=int(team["team_id"]),
        player_id=player_id,
        from_team_id=int(team["team_id"]),
        to_team_id=int(team["team_id"]),
        old_status=player["status"],
        new_status=IR_STATUS,
        contract_id=transfer_active_contract(con, player_id, int(team["team_id"])),
        description=f"Placed {player_name(player)} on injured reserve. {notes}".strip(),
    )
    suffix = f" Eligible to return after {eligible_date}." if eligible_date else " This is season-ending IR."
    return f"Placed {player_name(player)} on IR (transaction {transaction_id}).{suffix}"


def activate_player_from_ir(
    con: sqlite3.Connection,
    *,
    player_id: int,
    team_abbr: str,
    season: int,
    force: bool = False,
) -> str:
    ensure_schema(con)
    seed_rules(con)
    team = get_team(con, team_abbr)
    player = con.execute(
        """
        SELECT p.*, t.abbreviation AS team
        FROM players p
        JOIN teams t ON t.team_id = p.team_id
        WHERE p.player_id = ?
          AND p.team_id = ?
        """,
        (player_id, int(team["team_id"])),
    ).fetchone()
    if not player:
        raise ValueError(f"Player {player_id} is not on the {team_abbr.upper()} roster.")
    designation = active_ir_designation(con, player_id, season)
    if not designation:
        raise ValueError(f"{player_name(player)} does not have an active IR designation.")
    summary = ir_summary_for_player(con, player, season)
    if not force and not summary.get("eligible"):
        raise ValueError(str(summary.get("reason") or "Player is not eligible to return from IR yet."))
    rule_set = practice_squad_rule_set(con, season, phase_for_transactions(con))
    active_limit = int(rule_set["active_roster_limit"] or 53)
    active_count = active_roster_count(con, int(team["team_id"]))
    if active_count >= active_limit and not force:
        raise ValueError(
            f"{team['abbreviation']} active roster is full ({active_count}/{active_limit}). "
            "Create a roster spot before activating from IR."
        )
    activation_date = today(con)
    con.execute(
        """
        UPDATE active_player_injuries
        SET resolved_at = ?, status = 'Cleared'
        WHERE player_id = ?
          AND resolved_at IS NULL
        """,
        (activation_date, player_id),
    )
    con.execute(
        """
        UPDATE player_injury_history
        SET resolved_date = ?
        WHERE player_id = ?
          AND resolved_date IS NULL
        """,
        (activation_date, player_id),
    )
    con.execute(
        """
        UPDATE injured_reserve_designations
        SET activation_date = ?,
            status = 'Activated',
            updated_at = datetime('now')
        WHERE designation_id = ?
        """,
        (activation_date, int(designation["designation_id"])),
    )
    set_player_status(
        con,
        player=player,
        team_id=int(team["team_id"]),
        new_status="Active",
        season=season,
        reason="Activated from Reserve/Injured.",
    )
    transaction_id = log_transaction(
        con,
        transaction_type="Activated From IR",
        season=season,
        team_id=int(team["team_id"]),
        player_id=player_id,
        from_team_id=int(team["team_id"]),
        to_team_id=int(team["team_id"]),
        old_status=IR_STATUS,
        new_status="Active",
        contract_id=transfer_active_contract(con, player_id, int(team["team_id"])),
        description=f"Activated {player_name(player)} from injured reserve.",
    )
    return f"Activated {player_name(player)} from IR (transaction {transaction_id})."


def sync_auto_ir_designations(
    con: sqlite3.Connection,
    *,
    season: int,
    player_ids: list[int] | None = None,
) -> int:
    """Create reserve-list records for injury events that auto-set player status to IR."""
    ensure_schema(con)
    seed_rules(con)
    if not table_exists(con, "active_player_injuries"):
        return 0
    filters = [
        "p.status = 'IR'",
        "api.resolved_at IS NULL",
        "api.status = 'IR'",
        "existing.designation_id IS NULL",
    ]
    params: list[object] = [season]
    if player_ids:
        placeholders = ",".join("?" for _ in player_ids)
        filters.append(f"p.player_id IN ({placeholders})")
        params.extend(int(player_id) for player_id in player_ids)
    rows = con.execute(
        f"""
        SELECT
            p.*,
            api.active_injury_id,
            api.injury_history_id,
            api.injury_label,
            api.start_date,
            api.expected_games
        FROM players p
        JOIN active_player_injuries api
          ON api.player_id = p.player_id
        LEFT JOIN injured_reserve_designations existing
          ON existing.player_id = p.player_id
         AND existing.season = ?
         AND existing.status = 'On IR'
        WHERE {' AND '.join(filters)}
        ORDER BY p.team_id, p.player_id, api.active_injury_id
        """,
        params,
    ).fetchall()
    created = 0
    seen_players: set[int] = set()
    for row in rows:
        player_id = int(row["player_id"])
        if player_id in seen_players or row["team_id"] is None:
            continue
        seen_players.add(player_id)
        team_id = int(row["team_id"])
        placed_date = str(row["start_date"] or today(con))
        cutdown = final_roster_cutdown_date(con, season)
        before_cutdown = bool(cutdown and placed_date < cutdown)
        cutdown_day = bool(cutdown and placed_date == cutdown)
        pre_slots_used = pre_cutdown_ir_return_slots_used(con, team_id, season)
        designated_to_return = (not cutdown or placed_date > cutdown) or (
            cutdown_day and pre_slots_used < IR_PRE_CUTDOWN_RETURN_LIMIT
        )
        eligible_date = (
            ir_eligible_return_date(
                con,
                team_id=team_id,
                season=season,
                placed_date=placed_date,
                required_games=IR_MIN_RETURN_GAMES,
            )
            if designated_to_return
            else None
        )
        notes = f"Automatic IR record from {row['injury_label'] or 'injury'}."
        if before_cutdown:
            notes += " Placed before final cutdown; season-ending IR under preseason rules."
        elif cutdown_day:
            notes += (
                f" Preseason/cutdown return slot {pre_slots_used + 1}/{IR_PRE_CUTDOWN_RETURN_LIMIT} used."
                if designated_to_return
                else " Cutdown-day IR without a return slot; season-ending."
            )
        con.execute(
            """
            INSERT INTO injured_reserve_designations (
                player_id, team_id, season, placed_date, placed_phase,
                active_injury_id, injury_history_id, designated_to_return,
                pre_cutdown_return_slot, return_games_required, eligible_return_date,
                status, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'On IR', ?)
            """,
            (
                player_id,
                team_id,
                season,
                placed_date,
                "Preseason" if before_cutdown or cutdown_day else phase_for_transactions(con),
                int(row["active_injury_id"]) if row["active_injury_id"] is not None else None,
                int(row["injury_history_id"]) if row["injury_history_id"] is not None else None,
                1 if designated_to_return else 0,
                1 if cutdown_day and designated_to_return else 0,
                IR_MIN_RETURN_GAMES,
                eligible_date,
                notes,
            ),
        )
        clear_depth_chart(con, player_id)
        contract = active_contract(con, player_id)
        log_transaction(
            con,
            transaction_type="Injured Reserve",
            season=season,
            team_id=team_id,
            player_id=player_id,
            from_team_id=team_id,
            to_team_id=team_id,
            old_status="Out",
            new_status=IR_STATUS,
            contract_id=int(contract["contract_id"]) if contract else None,
            description=f"Placed {player_name(row)} on injured reserve. {notes}",
        )
        created += 1
    return created


def week_four_start(con: sqlite3.Connection, season: int) -> str | None:
    if not table_exists(con, "season_games"):
        return None
    row = con.execute(
        """
        SELECT MIN(game_date) AS week_four
        FROM season_games
        WHERE season = ?
          AND game_type = 'REG'
          AND week = 4
        """,
        (season,),
    ).fetchone()
    return str(row["week_four"]) if row and row["week_four"] else None


def waiver_priority_basis(con: sqlite3.Connection, season: int, as_of_date: str | None = None) -> tuple[str, int | None]:
    """Return the NFL-style priority basis.

    Before Week 4, the league uses the prior draft order. After Week 3, it
    tracks reverse current standings. We use the most recent draft order as the
    proxy because the sim already stores pick order by draft year.
    """
    date_value = as_of_date or today(con)
    week4 = week_four_start(con, season)
    if week4 and date_value >= week4:
        return "current_standings", None
    return "draft_order_proxy", season


def waiver_required_for_player(
    con: sqlite3.Connection,
    player: sqlite3.Row | dict[str, object],
    *,
    season: int,
    waiver_date: str | None = None,
) -> bool:
    status = str(player["status"] if isinstance(player, sqlite3.Row) else player.get("status") or "")
    if status == PRACTICE_SQUAD_STATUS:
        return False
    years_exp = row_int(player, "years_exp", 0)
    if years_exp < 4:
        return True
    date_value = waiver_date or today(con)
    deadline = league_trade_deadline(con, waiver_rule_season_for_date(date_value, season))
    if deadline and date_value >= deadline:
        return True
    return False


def player_group(position: str | None) -> str:
    return POSITION_TO_GROUP.get(str(position or "").upper(), str(position or "OTHER").upper())


def active_group_count(con: sqlite3.Connection, team_id: int, group: str) -> int:
    positions = POSITION_GROUPS.get(group, (group,))
    placeholders = ",".join("?" for _ in positions)
    row = con.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM players
        WHERE team_id = ?
          AND status IN ('Active', 'Questionable', 'Doubtful', 'Out')
          AND position IN ({placeholders})
        """,
        (team_id, *positions),
    ).fetchone()
    return int(row["count"] or 0) if row else 0


def active_group_quality(con: sqlite3.Connection, team_id: int, group: str) -> float:
    positions = POSITION_GROUPS.get(group, (group,))
    placeholders = ",".join("?" for _ in positions)
    rows = con.execute(
        f"""
        SELECT COALESCE(overall, 50) AS overall
        FROM players
        WHERE team_id = ?
          AND status IN ('Active', 'Questionable', 'Doubtful', 'Out')
          AND position IN ({placeholders})
        ORDER BY COALESCE(overall, 50) DESC
        LIMIT 5
        """,
        (team_id, *positions),
    ).fetchall()
    if not rows:
        return 45.0
    return sum(float(row["overall"] or 50) for row in rows) / max(1, len(rows))


def active_contract_cap_hit(con: sqlite3.Connection, player_id: int, season: int) -> int:
    if table_exists(con, "contract_years"):
        row = con.execute(
            """
            SELECT COALESCE(cy.cap_hit, c.aav, 0) AS cap_hit
            FROM contracts c
            LEFT JOIN contract_years cy
              ON cy.contract_id = c.contract_id
             AND cy.season = ?
             AND COALESCE(cy.is_active, 1) = 1
            WHERE c.player_id = ?
              AND COALESCE(c.is_active, 1) = 1
            ORDER BY c.contract_id DESC
            LIMIT 1
            """,
            (season, player_id),
        ).fetchone()
    else:
        row = con.execute(
            """
            SELECT COALESCE(aav, 0) AS cap_hit
            FROM contracts
            WHERE player_id = ?
              AND COALESCE(is_active, 1) = 1
            ORDER BY contract_id DESC
            LIMIT 1
            """,
            (player_id,),
        ).fetchone()
    return int(row["cap_hit"] or 0) if row else 0


def team_cap_space(con: sqlite3.Connection, team_id: int) -> int:
    if not table_exists(con, "team_cap_view"):
        return 999_000_000
    row = con.execute(
        "SELECT COALESCE(cap_space, 0) AS cap_space FROM team_cap_view WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    return int(row["cap_space"] or 0) if row else 999_000_000


def place_player_on_waivers(
    con: sqlite3.Connection,
    *,
    player: sqlite3.Row,
    season: int,
    waiver_date: str | None = None,
    claim_deadline: str | None = None,
    reason: str | None = None,
    source: str = SOURCE,
) -> int:
    if player["status"] in {"Free Agent", "Retired", WAIVED_STATUS}:
        raise ValueError(f"{player_name(player)} cannot be waived from status {player['status']}.")
    waiver_date = waiver_date or today(con)
    if not waiver_required_for_player(con, player, season=season, waiver_date=waiver_date):
        raise ValueError(
            f"{player_name(player)} is a vested veteran before the trade deadline; release them instead of waiving."
        )
    open_existing = con.execute(
        """
        SELECT waiver_id
        FROM waiver_wire
        WHERE player_id = ? AND status = 'Open'
        """,
        (player["player_id"],),
    ).fetchone()
    if open_existing:
        return int(open_existing["waiver_id"])
    claim_deadline = claim_deadline or default_claim_deadline(waiver_date)
    from_team_id = int(player["team_id"]) if player["team_id"] is not None else None
    contract = active_contract(con, player["player_id"])
    contract_id = int(contract["contract_id"]) if contract else None
    set_player_status(
        con,
        player=player,
        team_id=None,
        new_status=WAIVED_STATUS,
        season=season,
        reason=reason or "Placed on waivers.",
        effective_date=waiver_date,
    )
    clear_depth_chart(con, int(player["player_id"]))
    cur = con.execute(
        """
        INSERT INTO waiver_wire (
            player_id, original_team_id, waiver_date, claim_deadline,
            season, status, reason, source
        )
        VALUES (?, ?, ?, ?, ?, 'Open', ?, ?)
        """,
        (
            int(player["player_id"]),
            from_team_id,
            waiver_date,
            claim_deadline,
            season,
            reason,
            source,
        ),
    )
    log_transaction(
        con,
        transaction_type="Waiver",
        season=season,
        team_id=from_team_id,
        player_id=int(player["player_id"]),
        contract_id=contract_id,
        from_team_id=from_team_id,
        old_status=player["status"],
        new_status=WAIVED_STATUS,
        description=f"Placed {player_name(player)} on waivers. Claim deadline: {claim_deadline}.",
        transaction_date=waiver_date,
    )
    return int(cur.lastrowid)


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
    waiver_date = args.waiver_date or today(con)
    claim_deadline = args.deadline_date or default_claim_deadline(waiver_date)
    waiver_id = place_player_on_waivers(
        con,
        player=player,
        season=season,
        waiver_date=waiver_date,
        claim_deadline=claim_deadline,
        reason=args.reason or "Placed on waivers.",
        source=SOURCE,
    )
    print(
        f"Waived {player_name(player)}. "
        f"Waiver entry {waiver_id}, deadline {claim_deadline}."
    )


def action_claim_waiver(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_rules(con)
    season = args.season if args.season is not None else current_season(con)
    seed_waiver_priority(con, season)
    team = get_team(con, args.team)
    waiver = get_waiver(con, args.waiver_id) if args.waiver_id else find_open_waiver(con, args.player)
    claim_order = submit_waiver_claim(
        con,
        waiver=waiver,
        team_id=int(team["team_id"]),
        season=season,
        notes=args.notes,
    )
    player = con.execute(
        "SELECT first_name, last_name FROM players WHERE player_id = ?",
        (waiver["player_id"],),
    ).fetchone()
    print(
        f"{team['abbreviation']} claimed {player['first_name']} {player['last_name']} "
        f"on waiver entry {waiver['waiver_id']} with priority {claim_order}."
    )


def submit_waiver_claim(
    con: sqlite3.Connection,
    *,
    waiver: sqlite3.Row,
    team_id: int,
    season: int,
    notes: str | None,
    claim_date: str | None = None,
) -> int:
    if waiver["status"] != "Open":
        raise ValueError(f"Waiver entry {waiver['waiver_id']} is {waiver['status']}, not Open.")
    if waiver["original_team_id"] == team_id:
        raise ValueError("Original team cannot claim its own waiver entry.")
    claim_order = waiver_priority_for_team(con, season, team_id)
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
            team_id,
            claim_order,
            claim_date or today(con),
            notes,
        ),
    )
    return claim_order


def active_roster_limit_for_claim(con: sqlite3.Connection, season: int) -> int:
    try:
        phase = phase_for_transactions(con)
        rule_set = get_rule_set(con, season, phase)
    except Exception:
        try:
            rule_set = get_rule_set(con, season, "Regular Season")
        except Exception:
            return 53
    return int(rule_set["active_roster_limit"] or 53)


def waiver_claim_cut_candidate_filter_sql(*, allow_specialists: bool = False) -> str:
    specialist_clause = "" if allow_specialists else "AND position NOT IN ('K', 'P', 'LS')"
    return f"""
          AND COALESCE(overall, 50) < 75
          AND COALESCE(potential, COALESCE(overall, 50)) < 82
          AND NOT (
                COALESCE(age, 26) <= 24
            AND COALESCE(overall, 50) >= 64
            AND COALESCE(potential, COALESCE(overall, 50)) >= 74
          )
          AND NOT (
                COALESCE(age, 26) <= 25
            AND COALESCE(potential, COALESCE(overall, 50)) >= 78
          )
          AND NOT (
                COALESCE(age, 26) <= 30
            AND COALESCE(overall, 50) >= 70
          )
          {specialist_clause}
    """


def team_has_claim_roster_path(con: sqlite3.Connection, team_id: int, group: str, season: int) -> bool:
    active_limit = active_roster_limit_for_claim(con, season)
    active_count = active_roster_count(con, team_id)
    if active_count < active_limit:
        return True
    if active_count > active_limit + 1:
        return False
    positions = POSITION_GROUPS.get(group, (group,))
    placeholders = ",".join("?" for _ in positions)
    target = WAIVER_CLAIM_GROUP_TARGETS.get(group, 4)
    if active_group_count(con, team_id, group) > target:
        replacement = con.execute(
            f"""
            SELECT player_id
            FROM players
            WHERE team_id = ?
              AND status = 'Active'
              AND position IN ({placeholders})
              AND COALESCE(overall, 50) < 67
              AND COALESCE(potential, overall, 50) < 74
              {waiver_claim_cut_candidate_filter_sql()}
            ORDER BY COALESCE(overall, 50), COALESCE(potential, overall, 50), age DESC
            LIMIT 1
            """,
            (team_id, *positions),
        ).fetchone()
        if replacement:
            return True
    if group not in {"QB", "ST"}:
        replacement = con.execute(
            f"""
            SELECT player_id
            FROM players
            WHERE team_id = ?
              AND status = 'Active'
              AND COALESCE(overall, 50) < 64
              AND COALESCE(potential, overall, 50) < 72
              {waiver_claim_cut_candidate_filter_sql()}
            ORDER BY COALESCE(overall, 50), COALESCE(potential, overall, 50), age DESC
            LIMIT 1
            """,
            (team_id,),
        ).fetchone()
        return replacement is not None
    return False


def waiver_claim_fit_score(
    con: sqlite3.Connection,
    *,
    waiver: sqlite3.Row,
    player: sqlite3.Row,
    team: sqlite3.Row,
    season: int,
    post_cutdown: bool,
) -> tuple[float, str]:
    team_id = int(team["team_id"])
    group = player_group(player["position"])
    overall = row_int(player, "overall", 50)
    potential = row_int(player, "potential", overall)
    age = row_int(player, "age", 26)
    years_exp = row_int(player, "years_exp", 0)
    group_count = active_group_count(con, team_id, group)
    active_count = active_roster_count(con, team_id)
    active_limit = active_roster_limit_for_claim(con, season)
    quality = active_group_quality(con, team_id, group)
    target = WAIVER_CLAIM_GROUP_TARGETS.get(group, 4)
    deficit = max(0, target - group_count)
    cap_hit = active_contract_cap_hit(con, int(player["player_id"]), season)
    cap_space = team_cap_space(con, team_id)
    waiver_date = str(waiver["claim_deadline"] or waiver["waiver_date"] or today(con))
    if player_recently_moved(con, player_id=int(player["player_id"]), before_date=waiver_date):
        return -95.0, "player recently moved teams"
    if player_has_waiver_churn(con, player_id=int(player["player_id"]), before_date=waiver_date):
        return -92.0, "player has already bounced around the waiver/practice-squad market"
    if str(waiver["source"] or "") != "roster_cutdown" and team_claim_cooldown_active(con, team_id=team_id, before_date=waiver_date):
        return -88.0, "team has already used recent waiver activity"
    if cap_hit > max(1_500_000, cap_space + 1_000_000):
        return -100.0, f"cap space cannot absorb {format_money(cap_hit)} claim"
    if active_count > active_limit + 1:
        return -90.0, f"active roster still over limit ({active_count}/{active_limit})"
    if not team_has_claim_roster_path(con, team_id, group, season):
        return -80.0, "no clear roster spot or expendable same-group player"

    youth_bonus = max(0, 26 - age) * 1.8 + (5.0 if years_exp <= 1 else 2.0 if years_exp <= 2 else 0.0)
    need_bonus = deficit * 13.0 + max(0.0, 70.0 - quality) * 0.8
    upside_bonus = max(0, potential - overall) * 1.6 + max(0, potential - 72) * 1.1
    starter_bonus = 7.5 if overall >= max(64, quality - 2) and group_count < target + 1 else 0.0
    post_cutdown_bonus = 8.0 if post_cutdown and (age <= 25 or potential >= 73) else 0.0
    contract_penalty = min(24.0, cap_hit / 1_000_000 * (1.4 if age >= 28 else 0.8))
    crowd_penalty = max(0, group_count - target - 1) * 8.0
    if group == "QB" and group_count >= 3 and overall < 68 and potential < 78:
        crowd_penalty += 18.0
    score = (
        overall * 0.66
        + potential * 0.38
        + youth_bonus
        + need_bonus
        + upside_bonus
        + starter_bonus
        + post_cutdown_bonus
        - contract_penalty
        - crowd_penalty
    )
    reason = (
        f"{group} need {group_count}/{target}, group quality {quality:.1f}, "
        f"{overall}/{potential} rating, cap hit {format_money(cap_hit)}"
    )
    return score, reason


def seed_cpu_waiver_claims(
    con: sqlite3.Connection,
    *,
    season: int,
    game_id: str | None = None,
    include_user_team: bool = False,
    max_claims_per_team: int = 3,
    max_claims_total: int = 48,
    post_cutdown: bool = False,
    claim_date: str | None = None,
    ready_on_or_before: str | None = None,
    include_corresponding_moves: bool = True,
) -> dict[str, int]:
    ensure_schema(con)
    seed_rules(con)
    seed_waiver_priority(con, season)
    user_team_id: int | None = None
    if game_id and table_exists(con, "game_saves"):
        row = con.execute("SELECT user_team_id, control_mode FROM game_saves WHERE game_id = ?", (game_id,)).fetchone()
        if row and str(row["control_mode"] or "team").lower() != "observe" and row["user_team_id"] is not None:
            user_team_id = int(row["user_team_id"])
    ready_clause = ""
    params: list[object] = []
    if ready_on_or_before:
        ready_clause = "AND date(ww.claim_deadline) <= date(?)"
        params.append(ready_on_or_before)
    source_clause = ""
    if not include_corresponding_moves:
        source_clause = "AND COALESCE(ww.source, '') != 'waiver_claim_corresponding_move'"
    waivers = con.execute(
        f"""
        SELECT ww.*, p.first_name, p.last_name, p.position, p.age, p.years_exp,
               p.is_rookie, p.overall, p.potential
        FROM waiver_wire ww
        JOIN players p ON p.player_id = ww.player_id
        WHERE ww.status = 'Open'
          {ready_clause}
          {source_clause}
        ORDER BY ww.claim_deadline, p.potential DESC, p.overall DESC, ww.waiver_id
        """,
        params,
    ).fetchall()
    teams = con.execute("SELECT * FROM teams ORDER BY abbreviation").fetchall()
    claims_by_team: dict[int, int] = {}
    submitted = 0
    evaluated = 0
    for waiver in waivers:
        if submitted >= max_claims_total:
            break
        player = con.execute("SELECT * FROM players WHERE player_id = ?", (waiver["player_id"],)).fetchone()
        if not player:
            continue
        waiver_reference_date = str(waiver["claim_deadline"] or waiver["waiver_date"] or claim_date or today(con))
        if str(waiver["source"] or "") != "roster_cutdown":
            if player_recently_moved(con, player_id=int(player["player_id"]), before_date=waiver_reference_date):
                continue
            if player_has_waiver_churn(con, player_id=int(player["player_id"]), before_date=waiver_reference_date):
                continue
        fits: list[tuple[float, str, sqlite3.Row]] = []
        for team in teams:
            team_id = int(team["team_id"])
            if team_id == int(waiver["original_team_id"] or 0):
                continue
            if not include_user_team and user_team_id is not None and team_id == user_team_id:
                continue
            if claims_by_team.get(team_id, 0) >= max_claims_per_team:
                continue
            if str(waiver["source"] or "") != "roster_cutdown" and team_claim_cooldown_active(
                con,
                team_id=team_id,
                before_date=waiver_reference_date,
            ):
                continue
            evaluated += 1
            score, reason = waiver_claim_fit_score(
                con,
                waiver=waiver,
                player=player,
                team=team,
                season=season,
                post_cutdown=post_cutdown,
            )
            if score >= (77.0 if post_cutdown else 84.0):
                fits.append((score, reason, team))
        for score, reason, team in sorted(fits, key=lambda item: item[0], reverse=True)[:5]:
            if submitted >= max_claims_total:
                break
            team_id = int(team["team_id"])
            if claims_by_team.get(team_id, 0) >= max_claims_per_team:
                continue
            note = f"CPU waiver claim score {score:.1f}: {reason}."
            submit_waiver_claim(
                con,
                waiver=waiver,
                team_id=team_id,
                season=season,
                notes=note,
                claim_date=claim_date,
            )
            claims_by_team[team_id] = claims_by_team.get(team_id, 0) + 1
            submitted += 1
    return {"open_waivers": len(waivers), "evaluated": evaluated, "claims": submitted, "teams": len(claims_by_team)}


def action_cpu_waiver_claims(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    seed_rules(con)
    season = args.season if args.season is not None else current_season(con)
    result = seed_cpu_waiver_claims(
        con,
        season=season,
        game_id=args.game_id,
        include_user_team=args.include_user_team,
        max_claims_per_team=args.max_claims_per_team,
        max_claims_total=args.max_claims_total,
        post_cutdown=args.post_cutdown,
    )
    print(
        "CPU waiver claims: "
        f"{result['claims']} claim(s) from {result['teams']} team(s), "
        f"{result['open_waivers']} open waiver(s) reviewed."
    )


def release_player_after_waiver_roster_move(
    con: sqlite3.Connection,
    *,
    player: sqlite3.Row,
    season: int,
    reason: str,
    transaction_date: str | None = None,
) -> None:
    contract_id = deactivate_active_contract(con, int(player["player_id"]))
    from_team_id = int(player["team_id"]) if player["team_id"] is not None else None
    apply_dead_cap_on_release(
        con,
        player_id=int(player["player_id"]),
        team_id=from_team_id,
        contract_id=contract_id,
        season=season,
        player_label=player_name(player),
        process_date=transaction_date,
    )
    set_player_status(
        con,
        player=player,
        team_id=None,
        new_status="Free Agent",
        season=season,
        reason=reason,
        effective_date=transaction_date,
    )
    clear_depth_chart(con, int(player["player_id"]))
    log_transaction(
        con,
        transaction_type="Release",
        season=season,
        team_id=from_team_id,
        player_id=int(player["player_id"]),
        contract_id=contract_id,
        from_team_id=from_team_id,
        old_status=player["status"],
        new_status="Free Agent",
        description=f"Released {player_name(player)} as the corresponding move after a waiver claim.",
        transaction_date=transaction_date,
    )


def apply_dead_cap_on_waiver_clear(
    con: sqlite3.Connection,
    *,
    player_id: int,
    team_id: int | None,
    contract_id: int | None,
    season: int,
    player_label: str,
    process_date: str | None = None,
) -> int:
    if not team_id or not contract_id or not table_exists(con, "team_cap_charges") or not table_exists(con, "contract_years"):
        return 0
    target_date = process_date or today(con)
    post_june1 = target_date >= f"{season}-06-02"
    row = con.execute(
        """
        SELECT
            COALESCE(dead_cap_if_cut_pre_june1, 0) AS pre_june_dead_cap,
            COALESCE(dead_cap_if_cut_post_june1_current, 0) AS post_june_current_dead_cap,
            COALESCE(dead_cap_if_cut_post_june1_next, 0) AS post_june_next_dead_cap
        FROM contract_years
        WHERE contract_id = ?
          AND season = ?
        ORDER BY contract_year_id DESC
        LIMIT 1
        """,
        (contract_id, season),
    ).fetchone()
    if not row:
        return 0

    current_dead_cap = int(
        row["post_june_current_dead_cap"] if post_june1 else row["pre_june_dead_cap"]
    )
    next_dead_cap = int(row["post_june_next_dead_cap"] or 0) if post_june1 else 0
    total_dead_cap = current_dead_cap + next_dead_cap
    if total_dead_cap <= 0:
        return 0

    if current_dead_cap > 0:
        con.execute(
            """
            INSERT INTO team_cap_charges (
                team_id, season, charge_type, description, amount, player_id, source
            )
            VALUES (?, ?, 'Dead Cap', ?, ?, ?, ?)
            """,
            (
                team_id,
                season,
                f"Dead cap from {player_label} clearing waivers.",
                current_dead_cap,
                player_id,
                SOURCE,
            ),
        )
    if next_dead_cap > 0:
        con.execute(
            """
            INSERT INTO team_cap_charges (
                team_id, season, charge_type, description, amount, player_id, source
            )
            VALUES (?, ?, 'Dead Cap', ?, ?, ?, ?)
            """,
            (
                team_id,
                season + 1,
                f"Post-June 1 dead cap from {player_label} clearing waivers.",
                next_dead_cap,
                player_id,
                SOURCE,
            ),
        )
    return total_dead_cap


def apply_dead_cap_on_release(
    con: sqlite3.Connection,
    *,
    player_id: int,
    team_id: int | None,
    contract_id: int | None,
    season: int,
    player_label: str,
    process_date: str | None = None,
) -> int:
    if not team_id or not contract_id or not table_exists(con, "team_cap_charges") or not table_exists(con, "contract_years"):
        return 0
    target_date = process_date or today(con)
    post_june1 = target_date >= f"{season}-06-02"
    row = con.execute(
        """
        SELECT
            COALESCE(dead_cap_if_cut_pre_june1, 0) AS pre_june_dead_cap,
            COALESCE(dead_cap_if_cut_post_june1_current, 0) AS post_june_current_dead_cap,
            COALESCE(dead_cap_if_cut_post_june1_next, 0) AS post_june_next_dead_cap
        FROM contract_years
        WHERE contract_id = ?
          AND season = ?
        ORDER BY contract_year_id DESC
        LIMIT 1
        """,
        (contract_id, season),
    ).fetchone()
    if not row:
        return 0
    current_dead_cap = int(
        row["post_june_current_dead_cap"] if post_june1 else row["pre_june_dead_cap"]
    )
    next_dead_cap = int(row["post_june_next_dead_cap"] or 0) if post_june1 else 0
    total_dead_cap = current_dead_cap + next_dead_cap
    if total_dead_cap <= 0:
        return 0
    if current_dead_cap > 0:
        con.execute(
            """
            INSERT INTO team_cap_charges (
                team_id, season, charge_type, description, amount, player_id, source
            )
            VALUES (?, ?, 'Dead Cap', ?, ?, ?, ?)
            """,
            (
                team_id,
                season,
                f"Dead cap from releasing {player_label}.",
                current_dead_cap,
                player_id,
                SOURCE,
            ),
        )
    if next_dead_cap > 0:
        con.execute(
            """
            INSERT INTO team_cap_charges (
                team_id, season, charge_type, description, amount, player_id, source
            )
            VALUES (?, ?, 'Dead Cap', ?, ?, ?, ?)
            """,
            (
                team_id,
                season + 1,
                f"Post-June 1 dead cap from releasing {player_label}.",
                next_dead_cap,
                player_id,
                SOURCE,
            ),
        )
    return total_dead_cap


def make_room_for_waiver_claim(
    con: sqlite3.Connection,
    *,
    team_id: int,
    claimed_player_id: int,
    season: int,
    max_moves: int | None = None,
    waiver_date: str | None = None,
) -> int:
    limit = active_roster_limit_for_claim(con, season)
    moved = 0
    while active_roster_count(con, team_id) > limit and (max_moves is None or moved < max_moves):
        claimed = con.execute("SELECT position FROM players WHERE player_id = ?", (claimed_player_id,)).fetchone()
        group = player_group(claimed["position"] if claimed else None)
        positions = POSITION_GROUPS.get(group, (group,))
        placeholders = ",".join("?" for _ in positions)
        target = WAIVER_CLAIM_GROUP_TARGETS.get(group, 4)
        candidate = None
        if active_group_count(con, team_id, group) > target:
            candidates = con.execute(
                f"""
                SELECT *
                FROM players
                WHERE team_id = ?
                  AND player_id != ?
                  AND status = 'Active'
                  AND position IN ({placeholders})
                  {waiver_claim_cut_candidate_filter_sql(allow_specialists=(group == "ST"))}
                ORDER BY
                  CASE WHEN COALESCE(overall, 50) >= 68 OR COALESCE(potential, overall, 50) >= 75 THEN 1 ELSE 0 END,
                  COALESCE(overall, 50),
                  COALESCE(potential, overall, 50),
                  age DESC
                LIMIT 8
                """,
                (team_id, claimed_player_id, *positions),
            ).fetchall()
            for row in candidates:
                if not player_recently_moved(con, player_id=int(row["player_id"]), before_date=waiver_date):
                    candidate = row
                    break
        if not candidate:
            candidates = con.execute(
                f"""
                SELECT *
                FROM players
                WHERE team_id = ?
                  AND player_id != ?
                  AND status = 'Active'
                  {waiver_claim_cut_candidate_filter_sql()}
                ORDER BY
                  CASE WHEN position IN ('QB','K','P','LS') THEN 1 ELSE 0 END,
                  CASE WHEN COALESCE(overall, 50) >= 70 OR COALESCE(potential, overall, 50) >= 76 THEN 1 ELSE 0 END,
                  COALESCE(overall, 50),
                  COALESCE(potential, overall, 50),
                  age DESC
                LIMIT 8
                """,
                (team_id, claimed_player_id),
            ).fetchall()
            for row in candidates:
                if not player_recently_moved(con, player_id=int(row["player_id"]), before_date=waiver_date):
                    candidate = row
                    break
        if not candidate:
            break
        reason = "Corresponding roster move after waiver claim."
        if waiver_required_for_player(con, candidate, season=season, waiver_date=waiver_date):
            place_player_on_waivers(
                con,
                player=candidate,
                season=season,
                waiver_date=waiver_date,
                reason=reason,
                source="waiver_claim_corresponding_move",
            )
        else:
            release_player_after_waiver_roster_move(
                con,
                player=candidate,
                season=season,
                reason=reason,
                transaction_date=waiver_date,
            )
        moved += 1
    return moved


def action_process_waivers(con: sqlite3.Connection, args: argparse.Namespace) -> dict[str, int]:
    ensure_schema(con)
    seed_rules(con)
    process_date = args.date or today(con)
    defer_cap_sync = bool(getattr(args, "defer_cap_sync", False))
    quiet = bool(getattr(args, "quiet", False))
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
        if not quiet:
            print("No waiver entries ready to process.")
        return {"processed": 0, "claimed": 0, "cleared": 0}

    processed = 0
    claimed_count = 0
    cleared_count = 0
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
            claiming_team_id = int(winner["claiming_team_id"])
            pre_claim_active_count = active_roster_count(con, claiming_team_id)
            contract_id = transfer_active_contract(con, waiver["player_id"], int(winner["claiming_team_id"]))
            set_player_status(
                con,
                player=player,
                team_id=claiming_team_id,
                new_status="Active",
                season=int(waiver["season"]),
                reason=f"Claimed off waivers by {winner['claiming_team']}.",
                effective_date=process_date,
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
            post_claim_active_count = active_roster_count(con, claiming_team_id)
            active_limit = active_roster_limit_for_claim(con, int(waiver["season"]))
            max_corresponding_moves = max(0, post_claim_active_count - max(active_limit, pre_claim_active_count))
            corresponding_moves = make_room_for_waiver_claim(
                con,
                team_id=claiming_team_id,
                claimed_player_id=int(waiver["player_id"]),
                season=int(waiver["season"]),
                max_moves=max_corresponding_moves,
                waiver_date=process_date,
            )
            transaction_id = log_transaction(
                con,
                transaction_type="Waiver Claim",
                season=int(waiver["season"]),
                team_id=claiming_team_id,
                secondary_team_id=waiver["original_team_id"],
                player_id=waiver["player_id"],
                contract_id=contract_id,
                from_team_id=waiver["original_team_id"],
                to_team_id=claiming_team_id,
                old_status=player["status"],
                new_status="Active",
                description=f"{winner['claiming_team']} awarded waiver claim for {player_name(player)}.",
                transaction_date=process_date,
            )
            if not quiet:
                print(
                    f"Awarded {player_name(player)} to {winner['claiming_team']} "
                    f"(waiver {waiver['waiver_id']}, transaction {transaction_id}, "
                    f"{corresponding_moves} corresponding move(s))."
                )
            claimed_count += 1
        else:
            contract_id = deactivate_active_contract(con, waiver["player_id"])
            apply_dead_cap_on_waiver_clear(
                con,
                player_id=int(waiver["player_id"]),
                team_id=int(waiver["original_team_id"]) if waiver["original_team_id"] is not None else None,
                contract_id=contract_id,
                season=int(waiver["season"]),
                player_label=player_name(player),
                process_date=process_date,
            )
            set_player_status(
                con,
                player=player,
                team_id=None,
                new_status="Free Agent",
                season=int(waiver["season"]),
                reason="Cleared waivers.",
                effective_date=process_date,
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
                transaction_date=process_date,
            )
            if not quiet:
                print(
                    f"{player_name(player)} cleared waivers "
                    f"(waiver {waiver['waiver_id']}, transaction {transaction_id})."
                )
            cleared_count += 1
        processed += 1
    if not defer_cap_sync:
        rebuild_contract_years(con)
        sync_team_cap_space(con)
    if not quiet:
        print(f"Processed {processed} waiver entr{'y' if processed == 1 else 'ies'}.")
    return {"processed": processed, "claimed": claimed_count, "cleared": cleared_count}


def settle_expired_waivers(
    con: sqlite3.Connection,
    *,
    season: int,
    target_date: str,
    game_id: str | None = None,
    include_user_team: bool = False,
    max_claims_per_team: int = 3,
    max_claims_total: int = 64,
    post_cutdown: bool = False,
    max_rounds: int = 14,
    include_corresponding_moves: bool = False,
) -> dict[str, int]:
    """Seed and process waiver entries whose deadlines are due by target_date.

    This intentionally walks deadline-by-deadline because awarded claims can
    create corresponding waiver moves with their own 24-hour clock.
    """
    ensure_schema(con)
    seed_rules(con)
    rounds = 0
    total_claims = 0
    total_processed = 0
    total_claimed = 0
    total_cleared = 0
    target = str(target_date)
    while rounds < max_rounds:
        row = con.execute(
            """
            SELECT MIN(claim_deadline) AS claim_deadline, COUNT(*) AS ready
            FROM waiver_wire
            WHERE status = 'Open'
              AND date(claim_deadline) <= date(?)
            """,
            (target,),
        ).fetchone()
        if not row or int(row["ready"] or 0) <= 0 or not row["claim_deadline"]:
            break
        process_date = str(row["claim_deadline"])
        claim_result = seed_cpu_waiver_claims(
            con,
            season=season,
            game_id=game_id,
            include_user_team=include_user_team,
            max_claims_per_team=max_claims_per_team,
            max_claims_total=max_claims_total,
            post_cutdown=post_cutdown,
            claim_date=process_date,
            ready_on_or_before=process_date,
            include_corresponding_moves=include_corresponding_moves,
        )
        process_result = action_process_waivers(
            con,
            argparse.Namespace(date=process_date, all_open=False, defer_cap_sync=True, quiet=True),
        )
        total_claims += int(claim_result.get("claims", 0))
        total_processed += int((process_result or {}).get("processed", 0))
        total_claimed += int((process_result or {}).get("claimed", 0))
        total_cleared += int((process_result or {}).get("cleared", 0))
        rounds += 1
        if int((process_result or {}).get("processed", 0)) <= 0:
            break
    if total_processed > 0:
        rebuild_contract_years(con)
        sync_team_cap_space(con)
    return {
        "rounds": rounds,
        "claims": total_claims,
        "processed": total_processed,
        "claimed": total_claimed,
        "cleared": total_cleared,
    }


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
    if player["status"] == WAIVED_STATUS:
        open_waiver = con.execute(
            "SELECT waiver_id, claim_deadline FROM waiver_wire WHERE player_id = ? AND status = 'Open'",
            (player["player_id"],),
        ).fetchone()
        if open_waiver:
            blockers.append(f"Must clear waivers before practice-squad signing; deadline {open_waiver['claim_deadline']}.")
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

    cpu_claim_parser = subparsers.add_parser("cpu-waiver-claims", help="Let CPU teams file realistic waiver claims.")
    cpu_claim_parser.add_argument("--season", type=int)
    cpu_claim_parser.add_argument("--game-id")
    cpu_claim_parser.add_argument("--include-user-team", action="store_true")
    cpu_claim_parser.add_argument("--post-cutdown", action="store_true")
    cpu_claim_parser.add_argument("--max-claims-per-team", type=int, default=3)
    cpu_claim_parser.add_argument("--max-claims-total", type=int, default=48)
    cpu_claim_parser.add_argument("--dry-run", action="store_true")

    process_parser = subparsers.add_parser("process-waivers", help="Process waiver entries whose deadlines have passed.")
    process_parser.add_argument("--date")
    process_parser.add_argument("--all-open", action="store_true", help="Process all open waivers regardless of deadline.")
    process_parser.add_argument("--quiet", action="store_true", help="Suppress per-player waiver output.")
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
        elif args.command == "cpu-waiver-claims":
            action_cpu_waiver_claims(con, args)
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
