#!/usr/bin/env python3
"""User scouting and inbox tools for generated draft classes."""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.draft.schema import ensure_schema as ensure_draft_schema
from engine.draft.senior_bowl import senior_bowl_status
import ai_gm_team_evaluator as team_eval
import scouting_perception

CONFIDENCE_BANDS = [
    (80, "Very High"),
    (60, "High"),
    (38, "Medium"),
    (0, "Low"),
]

CONFIDENCE_ORDER = ["Low", "Medium", "High", "Very High"]
CONFIDENCE_TARGET_LEVELS = {
    "Low": 15,
    "Medium": 40,
    "High": 65,
    "Very High": 85,
}
DISCOVERY_START_CONFIDENCE = "Medium"
DISCOVERY_START_LEVEL = CONFIDENCE_TARGET_LEVELS[DISCOVERY_START_CONFIDENCE]

SIMPLE_ACTION_LABELS = {
    "auto_assign": "Auto Assign 6 Scouts",
    "specific": "Scout 4 Specific Players",
    "random_two": "Scout 8 Random Players",
    "discover_four": "Scout 4 Random + 8 Random Discoveries",
}

FOCUS_LABELS = {
    "film": "Film Study",
    "game": "Game Exposure",
    "personality": "Background",
    "medical": "Medical",
    "workout": "Workout",
}

DRAFT_PROSPECT_SCOUTING_COLUMNS = {
    "college_class": "TEXT",
    "senior_bowl_eligible": "INTEGER NOT NULL DEFAULT 0",
    "senior_bowl_invited": "INTEGER NOT NULL DEFAULT 0",
    "senior_bowl_accepted": "INTEGER NOT NULL DEFAULT 0",
    "senior_bowl_result": "TEXT",
    "senior_bowl_notes": "TEXT",
}

CPU_SCOUTING_PROGRESS_COLUMNS = {
    "personality_known": "INTEGER NOT NULL DEFAULT 0",
    "top30_full_info_known": "INTEGER NOT NULL DEFAULT 0",
}

CPU_POSITION_TARGETS = {
    "QB": 2,
    "RB": 4,
    "FB": 1,
    "WR": 6,
    "TE": 3,
    "OT": 4,
    "OG": 4,
    "C": 2,
    "EDGE": 5,
    "IDL": 5,
    "DT": 5,
    "NT": 3,
    "LB": 6,
    "ILB": 4,
    "OLB": 5,
    "CB": 6,
    "S": 4,
    "FS": 3,
    "SS": 3,
    "K": 1,
    "P": 1,
    "LS": 1,
}

WEEKLY_SCOUTING_START_WEEK = 2
WEEKLY_SCOUTING_END_WEEK = 18
AUTO_ASSIGN_COUNT = 6
SPECIFIC_SCOUTING_COUNT = 4
RANDOM_CROSSCHECK_COUNT = 8
DISCOVER_RANDOM_CROSSCHECK_COUNT = 4
DISCOVER_NON_PUBLIC_COUNT = 8
USER_AUTO_DUE_DILIGENCE_MIN = 3
USER_AUTO_NEED_MIN = 2
USER_AUTO_NON_NEED_REPEAT_PENALTY = 18.0
USER_AUTO_FIRST_ROUND_VERY_HIGH_CAP = 8
CPU_WEEKLY_SCOUTING_COUNT = 5
CPU_EXTRA_HIDDEN_DISCOVERY_CHANCE = 0.25
USER_EXTRA_HIDDEN_DISCOVERY_CHANCE = 0.25
HIDDEN_DISCOVERY_SHARED_TEAM_CAP = 10
FIRST_ROUND_SCOUTING_NEED_BONUS = 36.0
LATER_ROUND_SCOUTING_NEED_BONUS = 10.0
CPU_FIRST_ROUND_DUE_DILIGENCE_BONUS = 22.0
CPU_QB_SCOUTING_NEED_FLOOR = 72.0
CPU_QB_SCOUTING_STRONG_NEED = 78.0
CPU_QB_DUE_DILIGENCE_BONUS = 44.0
CPU_QB_CONTRACT_YEAR_NEED = 84.0
CPU_QB_NEXT_CONTRACT_NEED = 74.0
CPU_SCOUTING_BUCKETS = {
    "early": (1, 48),
    "day2": (33, 112),
    "day3": (97, 240),
}
PRE_DRAFT_PUBLIC_EARLY_COUNT = 10
PRE_DRAFT_PUBLIC_LATE_COUNT = 15
PREMIUM_POSITION_SCOUTING_BONUS = 4.0
USER_AUTO_TOP30_FIRST_ROUND_CAP = 8
USER_AUTO_TOP30_LATE_EARLY_CAP = 6
USER_AUTO_TOP30_DAY2_CAP = 8
USER_AUTO_TOP30_DAY3_CAP = 8


def specific_scouting_cost(position: str | None) -> int:
    """A weekly QB deep dive consumes the whole specific-player scouting package."""
    return SPECIFIC_SCOUTING_COUNT if str(position or "").upper() == "QB" else 1
PREMIUM_POSITION_GROUPS = {"QB", "WR", "OL", "EDGE", "IDL", "CB"}
LOW_COST_POSITION_GROUPS = {"K", "P", "LS"}


@dataclass(frozen=True)
class ScoutingPeriod:
    season: int
    week: int
    label: str
    date: str


def row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


def hidden_discovery_weight(row: sqlite3.Row) -> float:
    """Weight off-board discovery from prospect traits, not who else found him."""
    tier_multiplier = {
        "Small": 1.18,
        "Regular": 1.00,
        "International": 0.88,
        "Power": 0.78,
    }.get(str(row_value(row, "college_tier", "") or ""), 0.95)
    variance = max(0.0, min(100.0, float(row_value(row, "scouting_variance", 50) or 50)))
    scout_grade = float(row_value(row, "scout_grade", 55) or 55)
    scout_ceiling = float(row_value(row, "scout_ceiling", scout_grade) or scout_grade)
    variance_multiplier = 0.55 + (variance / 85.0)
    grade_multiplier = 1.0 + max(0.0, scout_grade - 58.0) / 120.0
    ceiling_multiplier = 1.0 + max(0.0, scout_ceiling - 68.0) / 90.0
    return max(
        0.01,
        tier_multiplier
        * variance_multiplier
        * grade_multiplier
        * ceiling_multiplier,
    )


def choose_hidden_discovery_candidates(
    candidates: list[sqlite3.Row],
    *,
    count: int,
    rng: random.Random,
) -> list[sqlite3.Row]:
    pool = list(candidates)
    selected: list[sqlite3.Row] = []
    for _ in range(min(max(0, count), len(pool))):
        eligible_pool = [
            row
            for row in pool
            if int(row_value(row, "discovered_elsewhere_count", 0) or 0) < HIDDEN_DISCOVERY_SHARED_TEAM_CAP
        ]
        weighted_pool = eligible_pool or pool
        weights = [hidden_discovery_weight(row) for row in weighted_pool]
        selected_row = weighted_pool[rng.choices(range(len(weighted_pool)), weights=weights, k=1)[0]]
        selected.append(selected_row)
        pool.remove(selected_row)
    return selected


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    ).fetchone() is not None


def ensure_schema(con: sqlite3.Connection) -> None:
    if table_exists(con, "draft_classes") or table_exists(con, "draft_prospects"):
        ensure_draft_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_inbox_messages (
            message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            message_date TEXT NOT NULL,
            category TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'normal',
            source TEXT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            related_table TEXT,
            related_id INTEGER,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_user_inbox_messages_game_read
            ON user_inbox_messages(game_id, is_read, message_date DESC, message_id DESC);

        CREATE TABLE IF NOT EXISTS scouting_prospect_progress (
            game_id TEXT NOT NULL,
            draft_year INTEGER NOT NULL,
            prospect_id INTEGER NOT NULL REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
            visibility_status TEXT NOT NULL DEFAULT 'known',
            scouting_level INTEGER NOT NULL DEFAULT 15,
            scouting_confidence TEXT NOT NULL DEFAULT 'Low',
            times_scouted INTEGER NOT NULL DEFAULT 0,
            personality_known INTEGER NOT NULL DEFAULT 0,
            last_scouted_season INTEGER,
            last_scouted_week INTEGER,
            last_scouted_date TEXT,
            last_report TEXT,
            user_notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (game_id, draft_year, prospect_id)
        );

        CREATE INDEX IF NOT EXISTS idx_scouting_progress_game_year
            ON scouting_prospect_progress(game_id, draft_year, visibility_status, scouting_level DESC);

        CREATE TABLE IF NOT EXISTS scouting_assignments (
            assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            draft_year INTEGER NOT NULL,
            season INTEGER NOT NULL,
            week INTEGER NOT NULL,
            prospect_id INTEGER NOT NULL REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
            focus TEXT NOT NULL DEFAULT 'film',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            processed_at TEXT,
            UNIQUE(game_id, draft_year, season, week, prospect_id)
        );

        CREATE INDEX IF NOT EXISTS idx_scouting_assignments_period
            ON scouting_assignments(game_id, draft_year, season, week, status);

        CREATE TABLE IF NOT EXISTS scouting_weekly_actions (
            game_id TEXT NOT NULL,
            draft_year INTEGER NOT NULL,
            season INTEGER NOT NULL,
            week INTEGER NOT NULL,
            action_key TEXT NOT NULL,
            uses INTEGER NOT NULL DEFAULT 1,
            description TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (game_id, draft_year, season, week, action_key)
        );

        CREATE INDEX IF NOT EXISTS idx_scouting_weekly_actions_period
            ON scouting_weekly_actions(game_id, draft_year, season, week);

        CREATE TABLE IF NOT EXISTS scouting_top30_visits (
            visit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            draft_year INTEGER NOT NULL,
            team_abbr TEXT NOT NULL,
            prospect_id INTEGER NOT NULL REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
            visit_date TEXT NOT NULL,
            result_type TEXT NOT NULL,
            personality_revealed INTEGER NOT NULL DEFAULT 0,
            full_info_revealed INTEGER NOT NULL DEFAULT 0,
            revealed_traits_json TEXT,
            revealed_hidden_info_json TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(game_id, draft_year, team_abbr, prospect_id)
        );

        CREATE INDEX IF NOT EXISTS idx_scouting_top30_visits_game_year
            ON scouting_top30_visits(game_id, draft_year, team_abbr, visit_id DESC);

        CREATE TABLE IF NOT EXISTS scouting_senior_bowl_runs (
            game_id TEXT NOT NULL,
            draft_year INTEGER NOT NULL,
            event_date TEXT NOT NULL,
            seed TEXT,
            eligible_count INTEGER NOT NULL DEFAULT 0,
            invited_count INTEGER NOT NULL DEFAULT 0,
            accepted_count INTEGER NOT NULL DEFAULT 0,
            team_report_count INTEGER NOT NULL DEFAULT 0,
            user_report_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (game_id, draft_year)
        );

        CREATE TABLE IF NOT EXISTS scouting_senior_bowl_reports (
            report_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            draft_year INTEGER NOT NULL,
            team_abbr TEXT NOT NULL,
            prospect_id INTEGER NOT NULL REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
            event_date TEXT NOT NULL,
            result_type TEXT NOT NULL,
            trait_revealed INTEGER NOT NULL DEFAULT 0,
            confidence_up INTEGER NOT NULL DEFAULT 0,
            revealed_traits_json TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(game_id, draft_year, team_abbr, prospect_id)
        );

        CREATE INDEX IF NOT EXISTS idx_scouting_senior_bowl_reports_game_year
            ON scouting_senior_bowl_reports(game_id, draft_year, team_abbr, report_id DESC);

        CREATE TABLE IF NOT EXISTS cpu_scouting_prospect_progress (
            game_id TEXT NOT NULL,
            draft_year INTEGER NOT NULL,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            prospect_id INTEGER NOT NULL REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
            visibility_status TEXT NOT NULL DEFAULT 'known',
            scouting_level INTEGER NOT NULL DEFAULT 15,
            scouting_confidence TEXT NOT NULL DEFAULT 'Low',
            times_scouted INTEGER NOT NULL DEFAULT 0,
            last_scouted_season INTEGER,
            last_scouted_week INTEGER,
            last_scouted_date TEXT,
            last_report TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (game_id, draft_year, team_id, prospect_id)
        );

        CREATE INDEX IF NOT EXISTS idx_cpu_scouting_progress_team
            ON cpu_scouting_prospect_progress(game_id, draft_year, team_id, scouting_level DESC);

        CREATE INDEX IF NOT EXISTS idx_cpu_scouting_progress_prospect
            ON cpu_scouting_prospect_progress(game_id, draft_year, prospect_id);

        CREATE TABLE IF NOT EXISTS scouting_pre_draft_sweeps (
            game_id TEXT NOT NULL,
            draft_year INTEGER NOT NULL,
            user_team_id INTEGER,
            early_per_team INTEGER NOT NULL DEFAULT 10,
            late_per_team INTEGER NOT NULL DEFAULT 15,
            user_updates INTEGER NOT NULL DEFAULT 0,
            cpu_updates INTEGER NOT NULL DEFAULT 0,
            seed TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (game_id, draft_year)
        );
        """
    )
    ensure_draft_prospect_scouting_columns(con)
    ensure_cpu_scouting_columns(con)


def ensure_draft_prospect_scouting_columns(con: sqlite3.Connection) -> None:
    if not table_exists(con, "draft_prospects"):
        return
    existing = {row[1] for row in con.execute("PRAGMA table_info(draft_prospects)").fetchall()}
    for column, definition in DRAFT_PROSPECT_SCOUTING_COLUMNS.items():
        if column not in existing:
            con.execute(f"ALTER TABLE draft_prospects ADD COLUMN {column} {definition}")


def ensure_cpu_scouting_columns(con: sqlite3.Connection) -> None:
    if not table_exists(con, "cpu_scouting_prospect_progress"):
        return
    existing = {row[1] for row in con.execute("PRAGMA table_info(cpu_scouting_prospect_progress)").fetchall()}
    for column, definition in CPU_SCOUTING_PROGRESS_COLUMNS.items():
        if column not in existing:
            con.execute(f"ALTER TABLE cpu_scouting_prospect_progress ADD COLUMN {column} {definition}")


def setting(con: sqlite3.Connection, key: str, fallback: str | None = None) -> str | None:
    if not table_exists(con, "game_settings"):
        return fallback
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = ?",
        (key,),
    ).fetchone()
    return str(row["setting_value"]) if row else fallback


def active_game(con: sqlite3.Connection) -> sqlite3.Row | None:
    if table_exists(con, "active_game_save_view"):
        return con.execute("SELECT * FROM active_game_save_view LIMIT 1").fetchone()
    if table_exists(con, "game_saves"):
        return con.execute(
            """
            SELECT gs.*, t.abbreviation AS user_team
            FROM game_saves gs
            LEFT JOIN teams t ON t.team_id = gs.user_team_id
            WHERE gs.status = 'active'
            ORDER BY gs.updated_at DESC, gs.created_at DESC
            LIMIT 1
            """
        ).fetchone()
    return None


def active_game_id(con: sqlite3.Connection, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    row = active_game(con)
    if row and row["game_id"]:
        return str(row["game_id"])
    return "default"


def user_team_abbr(con: sqlite3.Connection) -> str:
    row = active_game(con)
    if row and "user_team" in row.keys() and row["user_team"]:
        return str(row["user_team"])
    return setting(con, "user_team", "MIN") or "MIN"


def user_team_id(con: sqlite3.Connection) -> int:
    row = active_game(con)
    if row and "user_team_id" in row.keys() and row["user_team_id"] is not None:
        return int(row["user_team_id"])
    abbr = user_team_abbr(con)
    if table_exists(con, "teams"):
        team = con.execute("SELECT team_id FROM teams WHERE abbreviation = ? LIMIT 1", (abbr,)).fetchone()
        if team:
            return int(team["team_id"])
    return 0


def current_date(con: sqlite3.Connection) -> str:
    row = active_game(con)
    if row and row["current_date"]:
        return str(row["current_date"])
    return setting(con, "current_game_date", "2026-06-01") or "2026-06-01"


def current_season(con: sqlite3.Connection) -> int:
    value = setting(con, "current_season")
    if value:
        return int(value)
    row = active_game(con)
    if row and row["current_league_year"]:
        return int(row["current_league_year"])
    return 2026


def current_phase_code(con: sqlite3.Connection) -> str:
    row = active_game(con)
    if row and "current_phase_code" in row.keys() and row["current_phase_code"]:
        return str(row["current_phase_code"])
    if row and "phase_name" in row.keys() and row["phase_name"]:
        return str(row["phase_name"])
    return setting(con, "current_calendar_phase", "") or ""


def draft_class_row(con: sqlite3.Connection, draft_year: int | None = None) -> sqlite3.Row | None:
    if not table_exists(con, "draft_classes"):
        return None
    if draft_year is not None:
        return con.execute(
            "SELECT * FROM draft_classes WHERE draft_year = ? ORDER BY draft_class_id DESC LIMIT 1",
            (draft_year,),
        ).fetchone()
    season = current_season(con)
    row = con.execute(
        """
        SELECT *
        FROM draft_classes
        WHERE draft_year >= ?
        ORDER BY draft_year, draft_class_id
        LIMIT 1
        """,
        (season + 1,),
    ).fetchone()
    if row:
        return row
    return con.execute(
        "SELECT * FROM draft_classes ORDER BY draft_year DESC, draft_class_id DESC LIMIT 1"
    ).fetchone()


def current_scouting_period(con: sqlite3.Connection) -> ScoutingPeriod:
    season = current_season(con)
    game_date = current_date(con)
    if not table_exists(con, "season_games"):
        return ScoutingPeriod(season=season, week=0, label="Preseason Board Build", date=game_date)
    rows = con.execute(
        """
        SELECT week,
               MIN(game_date) AS first_date,
               MAX(game_date) AS last_date,
               SUM(CASE WHEN played = 1 THEN 1 ELSE 0 END) AS played_count,
               COUNT(*) AS game_count
        FROM season_games
        WHERE season = ?
          AND game_type = 'REG'
        GROUP BY week
        ORDER BY week
        """,
        (season,),
    ).fetchall()
    if not rows:
        return ScoutingPeriod(season=season, week=0, label="Preseason Board Build", date=game_date)
    today = date.fromisoformat(game_date)
    for index, row in enumerate(rows):
        first = date.fromisoformat(str(row["first_date"]))
        last = date.fromisoformat(str(row["last_date"]))
        if today >= last and index + 1 < len(rows):
            next_week = int(rows[index + 1]["week"])
            next_first = date.fromisoformat(str(rows[index + 1]["first_date"]))
            if today < next_first:
                return ScoutingPeriod(season=season, week=next_week, label=f"Week {next_week} Scouting", date=game_date)
            continue
        if today <= last:
            week = int(row["week"])
            if today < first:
                return ScoutingPeriod(season=season, week=max(0, week - 1), label="Preseason Board Build" if week == 1 else f"Week {week - 1} Scouting", date=game_date)
            return ScoutingPeriod(season=season, week=week, label=f"Week {week} Scouting", date=game_date)
    last_week = int(rows[-1]["week"])
    return ScoutingPeriod(season=season, week=last_week, label=f"Week {last_week} Scouting", date=game_date)


def weekly_scouting_window_status(con: sqlite3.Connection, period: ScoutingPeriod | None = None) -> dict[str, Any]:
    target = period or current_scouting_period(con)
    phase = current_phase_code(con).upper().replace(" ", "_")
    in_regular_season = "REGULAR" in phase
    week_open = WEEKLY_SCOUTING_START_WEEK <= int(target.week) <= WEEKLY_SCOUTING_END_WEEK
    open_now = in_regular_season and week_open
    reason = None
    if not in_regular_season:
        reason = "Weekly tape scouting is only open during the regular season. Use Senior Bowl and Top 30 visits in the offseason."
    elif int(target.week) < WEEKLY_SCOUTING_START_WEEK:
        reason = f"Weekly scouting opens in Week {WEEKLY_SCOUTING_START_WEEK}, once the college season has enough usable tape."
    elif int(target.week) > WEEKLY_SCOUTING_END_WEEK:
        reason = f"Weekly scouting closes after Week {WEEKLY_SCOUTING_END_WEEK}. Use offseason scouting events from here."
    return {
        "open": open_now,
        "startWeek": WEEKLY_SCOUTING_START_WEEK,
        "endWeek": WEEKLY_SCOUTING_END_WEEK,
        "label": f"Weeks {WEEKLY_SCOUTING_START_WEEK}-{WEEKLY_SCOUTING_END_WEEK}",
        "autoAssignCount": AUTO_ASSIGN_COUNT,
        "specificCount": SPECIFIC_SCOUTING_COUNT,
        "randomCount": RANDOM_CROSSCHECK_COUNT,
        "discoverRandomCount": DISCOVER_RANDOM_CROSSCHECK_COUNT,
        "discoverCount": DISCOVER_NON_PUBLIC_COUNT,
        "ruleSummary": (
            f"Weekly scouting runs during regular season Weeks {WEEKLY_SCOUTING_START_WEEK}-"
            f"{WEEKLY_SCOUTING_END_WEEK}. If you skip a week in that window, your staff auto-assigns "
            f"{AUTO_ASSIGN_COUNT} priority prospect reports."
        ),
        "reason": reason,
    }


def senior_bowl_event_date(draft_year: int) -> str:
    anchor = date(int(draft_year), 2, 1)
    candidates = [anchor + timedelta(days=offset) for offset in range(-6, 7)]
    event_day = min(
        (candidate for candidate in candidates if candidate.weekday() == 5),
        key=lambda candidate: abs((candidate - anchor).days),
    )
    return event_day.isoformat()


def senior_bowl_window(draft_year: int) -> tuple[str, str, str]:
    event_day = date.fromisoformat(senior_bowl_event_date(draft_year))
    return (
        (event_day - timedelta(days=5)).isoformat(),
        event_day.isoformat(),
        (event_day + timedelta(days=3)).isoformat(),
    )


def calendar_event_dates(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    event_code: str,
) -> tuple[str | None, str | None]:
    if not table_exists(con, "league_calendar_events"):
        return None, None
    row = con.execute(
        """
        SELECT event_start_date, event_end_date
        FROM league_calendar_events
        WHERE event_code = ?
          AND (league_year = ? OR event_name = ? OR event_name LIKE ?)
        ORDER BY event_start_date
        LIMIT 1
        """,
        (event_code, draft_year - 1, f"{draft_year} NFL Draft", f"{draft_year}%"),
    ).fetchone()
    if not row:
        return None, None
    start = str(row["event_start_date"]) if row["event_start_date"] else None
    end = str(row["event_end_date"]) if row["event_end_date"] else start
    return start, end


def top30_visit_window(con: sqlite3.Connection, draft_year: int) -> tuple[str, str]:
    combine_start, combine_end = calendar_event_dates(
        con,
        draft_year=draft_year,
        event_code="SCOUTING_COMBINE",
    )
    deadline_start, _deadline_end = calendar_event_dates(
        con,
        draft_year=draft_year,
        event_code="DRAFT_FACILITY_VISIT_DEADLINE",
    )
    draft_start, _draft_end = calendar_event_dates(
        con,
        draft_year=draft_year,
        event_code="NFL_DRAFT",
    )
    start = combine_end or combine_start or date(draft_year, 3, 1).isoformat()
    end = deadline_start
    if not end and draft_start:
        end = (date.fromisoformat(draft_start) - timedelta(days=1)).isoformat()
    if not end:
        end = date(draft_year, 4, 20).isoformat()
    return start, end


def workout_visibility(con: sqlite3.Connection, draft_year: int, target_date: str | None = None) -> dict[str, Any]:
    today = target_date or current_date(con)
    combine_start, combine_end = calendar_event_dates(
        con,
        draft_year=draft_year,
        event_code="SCOUTING_COMBINE",
    )
    draft_start, _draft_end = calendar_event_dates(
        con,
        draft_year=draft_year,
        event_code="NFL_DRAFT",
    )
    combine_gate = combine_end or combine_start or date(draft_year, 3, 1).isoformat()
    pro_day_gate = combine_gate
    if combine_gate:
        pro_day_gate = (date.fromisoformat(combine_gate) + timedelta(days=14)).isoformat()
    if draft_start:
        pro_day_gate = min(pro_day_gate, (date.fromisoformat(draft_start) - timedelta(days=1)).isoformat())
    return {
        "currentDate": today,
        "combineDate": combine_start,
        "combineEndDate": combine_end or combine_start,
        "proDayDate": pro_day_gate,
        "combineAvailable": bool(today >= combine_gate),
        "proDayAvailable": bool(today >= pro_day_gate),
    }


COMBINE_UI_FIELDS = {
    "combine_status",
    "combine_grade",
    "athletic_score",
    "drills_completed",
    "forty_yard_dash",
    "ten_yard_split",
    "bench_press_reps",
    "vertical_jump_in",
    "broad_jump_in",
    "three_cone_sec",
    "twenty_yard_shuttle_sec",
    "sixty_yard_shuttle_sec",
    "combine_injured",
    "combine_top_skip",
}

PRO_DAY_UI_FIELDS = {
    "pro_day_status",
    "pro_day_grade",
    "pro_day_athletic_score",
    "pro_day_forty_yard_dash",
    "pro_day_vertical_jump_in",
    "pro_day_broad_jump_in",
    "pro_day_improved_from_combine",
    "pro_day_medical_recheck",
}


def mask_unavailable_workouts(payload: dict[str, Any]) -> dict[str, Any]:
    visibility = payload.get("workoutVisibility") or {}
    combine_available = bool(visibility.get("combineAvailable"))
    pro_day_available = bool(visibility.get("proDayAvailable"))
    for prospect in payload.get("board") or []:
        if not combine_available:
            for field in COMBINE_UI_FIELDS:
                if field in prospect:
                    prospect[field] = None
            prospect["combine_status"] = "Pending"
            prospect["workout_pending"] = True
        if not pro_day_available:
            for field in PRO_DAY_UI_FIELDS:
                if field in prospect:
                    prospect[field] = None
            prospect["pro_day_status"] = "Pending"
            prospect["pro_day_pending"] = True
    return payload


def top30_window_status(con: sqlite3.Connection, draft_year: int) -> dict[str, Any]:
    return top30_window_status_for_date(con, draft_year, current_date(con))


def top30_window_status_for_date(con: sqlite3.Connection, draft_year: int, target_date: str) -> dict[str, Any]:
    start, end = top30_visit_window(con, draft_year)
    today = target_date
    if today < start:
        return {
            "open": False,
            "start": start,
            "end": end,
            "reason": f"Top 30 visits open during the pre-draft visit window ({start} to {end}).",
        }
    if today > end:
        return {
            "open": False,
            "start": start,
            "end": end,
            "reason": f"Top 30 visits closed at the draft prospect facility visit deadline ({end}).",
        }
    return {"open": True, "start": start, "end": end, "reason": None}


def senior_bowl_window_status(con: sqlite3.Connection, draft_year: int) -> dict[str, Any]:
    start, event_day, end = senior_bowl_window(draft_year)
    today = current_date(con)
    if today < start:
        return {
            "open": False,
            "start": start,
            "eventDate": event_day,
            "end": end,
            "reason": f"Senior Bowl processing opens during Senior Bowl week ({start} to {end}).",
        }
    if today > end:
        return {
            "open": False,
            "start": start,
            "eventDate": event_day,
            "end": end,
            "reason": f"Senior Bowl processing closed after Senior Bowl week ({end}).",
        }
    return {"open": True, "start": start, "eventDate": event_day, "end": end, "reason": None}


def backfill_senior_bowl_fields(
    con: sqlite3.Connection,
    *,
    draft_year: int | None = None,
    force: bool = False,
) -> dict[str, int]:
    ensure_draft_prospect_scouting_columns(con)
    class_row = draft_class_row(con, draft_year)
    if not class_row:
        return {"draft_year": draft_year or current_season(con) + 1, "updated": 0, "eligible": 0, "invited": 0, "accepted": 0}
    draft_class_id = int(class_row["draft_class_id"])
    combine_join = ""
    combine_select = "0 AS combine_injured, 0 AS combine_top_skip"
    if table_exists(con, "draft_prospect_combine_results"):
        combine_join = "LEFT JOIN draft_prospect_combine_results dpc ON dpc.prospect_id = dp.prospect_id"
        combine_select = "COALESCE(dpc.is_injured, 0) AS combine_injured, COALESCE(dpc.is_top_skip, 0) AS combine_top_skip"
    rows = con.execute(
        f"""
        SELECT
            dp.prospect_id,
            dp.first_name,
            dp.last_name,
            dp.college,
            dp.position,
            dp.age,
            dp.college_tier,
            dp.public_board_rank,
            dp.public_board_status,
            dp.projected_round,
            dp.scout_grade,
            dp.college_class,
            dp.senior_bowl_result,
            {combine_select}
        FROM draft_prospects dp
        {combine_join}
        WHERE dp.draft_class_id = ?
        """,
        (draft_class_id,),
    ).fetchall()
    updated = 0
    for row in rows:
        has_existing_status = bool(row["college_class"]) and bool(row["senior_bowl_result"])
        if has_existing_status and not force:
            continue
        prospect_key = f"{row['prospect_id']}:{row['first_name']}:{row['last_name']}:{row['college']}"
        class_label = None if force else (str(row["college_class"]) if row["college_class"] else None)
        status = senior_bowl_status(
            age=row["age"],
            prospect_key=prospect_key,
            college_class=class_label,
            public_board_rank=row["public_board_rank"],
            public_board_status=row["public_board_status"],
            projected_round=row["projected_round"],
            college_tier=row["college_tier"],
            position=row["position"],
            combine_injured=bool(row["combine_injured"]),
            combine_top_skip=bool(row["combine_top_skip"]),
            scout_grade=row["scout_grade"],
        )
        con.execute(
            """
            UPDATE draft_prospects
            SET college_class = ?,
                senior_bowl_eligible = ?,
                senior_bowl_invited = ?,
                senior_bowl_accepted = ?,
                senior_bowl_result = ?,
                senior_bowl_notes = ?,
                updated_at = datetime('now')
            WHERE prospect_id = ?
            """,
            (
                status.college_class,
                int(status.eligible),
                int(status.invited),
                int(status.accepted),
                status.result,
                status.notes,
                int(row["prospect_id"]),
            ),
        )
        updated += 1
    counts = senior_bowl_counts(con, draft_class_id=draft_class_id)
    return {"draft_year": int(class_row["draft_year"]), "updated": updated, **counts}


def senior_bowl_counts(con: sqlite3.Connection, *, draft_class_id: int) -> dict[str, int]:
    ensure_draft_prospect_scouting_columns(con)
    row = con.execute(
        """
        SELECT
            SUM(CASE WHEN COALESCE(senior_bowl_eligible, 0) = 1 THEN 1 ELSE 0 END) AS eligible,
            SUM(CASE WHEN COALESCE(senior_bowl_invited, 0) = 1 THEN 1 ELSE 0 END) AS invited,
            SUM(CASE WHEN COALESCE(senior_bowl_accepted, 0) = 1 THEN 1 ELSE 0 END) AS accepted
        FROM draft_prospects
        WHERE draft_class_id = ?
        """,
        (draft_class_id,),
    ).fetchone()
    return {
        "eligible": int(row["eligible"] or 0),
        "invited": int(row["invited"] or 0),
        "accepted": int(row["accepted"] or 0),
    }


def confidence_for_level(level: int) -> str:
    for threshold, label in CONFIDENCE_BANDS:
        if level >= threshold:
            return label
    return "Low"


def normalize_confidence(label: str | None) -> str:
    if not label:
        return "Low"
    text = str(label).strip()
    return text if text in CONFIDENCE_TARGET_LEVELS else confidence_for_level(int(text) if text.isdigit() else 15)


def next_confidence(label: str | None) -> str:
    return advance_confidence(label, 1)


def advance_confidence(label: str | None, steps: int = 1) -> str:
    current = normalize_confidence(label)
    index = CONFIDENCE_ORDER.index(current)
    return CONFIDENCE_ORDER[min(index + max(0, int(steps)), len(CONFIDENCE_ORDER) - 1)]


def add_inbox_message(
    con: sqlite3.Connection,
    *,
    game_id: str,
    title: str,
    body: str,
    category: str = "Scouting",
    priority: str = "normal",
    source: str = "Scouting Department",
    message_date: str | None = None,
    related_table: str | None = None,
    related_id: int | None = None,
) -> None:
    ensure_schema(con)
    con.execute(
        """
        INSERT INTO user_inbox_messages (
            game_id, message_date, category, priority, source, title, body,
            related_table, related_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_id,
            message_date or current_date(con),
            category,
            priority,
            source,
            title,
            body,
            related_table,
            related_id,
        ),
    )


def public_pre_draft_candidate_rows(
    con: sqlite3.Connection,
    *,
    draft_class_id: int,
    early: bool,
    game_id: str,
    team_id: int | None = None,
) -> list[sqlite3.Row]:
    progress_filter = "COALESCE(progress.scouting_confidence, 'Low') = 'Low'"
    params: list[Any] = []
    if team_id is None:
        progress_join = """
            LEFT JOIN scouting_prospect_progress progress
              ON progress.game_id = ?
             AND progress.draft_year = dc.draft_year
             AND progress.prospect_id = dp.prospect_id
        """
        params.append(game_id)
    else:
        progress_join = """
            LEFT JOIN cpu_scouting_prospect_progress progress
              ON progress.game_id = ?
             AND progress.draft_year = dc.draft_year
             AND progress.team_id = ?
             AND progress.prospect_id = dp.prospect_id
        """
        params.extend([game_id, team_id])
    board_filter = "dp.public_board_rank BETWEEN 1 AND 64" if early else "dp.public_board_rank > 64"
    params.append(draft_class_id)
    return con.execute(
        f"""
        SELECT dp.*, dc.draft_year
        FROM draft_prospects dp
        JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
        {progress_join}
        WHERE dp.draft_class_id = ?
          AND dp.public_board_rank IS NOT NULL
          AND COALESCE(dp.public_board_status, 'public_board') <> 'off_public_board'
          AND {board_filter}
          AND {progress_filter}
        ORDER BY dp.public_board_rank, dp.prospect_id
        """,
        tuple(params),
    ).fetchall()


def choose_pre_draft_public_rows(
    rows: list[sqlite3.Row],
    *,
    count: int,
    rng: random.Random,
) -> list[sqlite3.Row]:
    if count <= 0 or not rows:
        return []
    pool = list(rows)
    rng.shuffle(pool)
    return pool[: min(count, len(pool))]


def upsert_user_pre_draft_medium(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    prospect: sqlite3.Row,
    sweep_date: str,
) -> None:
    report = "Pre-draft meetings and late-cycle film work moved this public-board file from Low to Medium confidence."
    con.execute(
        """
        INSERT INTO scouting_prospect_progress (
            game_id, draft_year, prospect_id, visibility_status, scouting_level,
            scouting_confidence, times_scouted, last_scouted_date, last_report, updated_at
        )
        VALUES (?, ?, ?, 'known', ?, 'Medium', 1, ?, ?, datetime('now'))
        ON CONFLICT(game_id, draft_year, prospect_id) DO UPDATE SET
            visibility_status = CASE
                WHEN scouting_prospect_progress.visibility_status = 'hidden' THEN 'known'
                ELSE scouting_prospect_progress.visibility_status
            END,
            scouting_level = MAX(scouting_prospect_progress.scouting_level, excluded.scouting_level),
            scouting_confidence = CASE
                WHEN scouting_prospect_progress.scouting_confidence = 'Low' THEN 'Medium'
                ELSE scouting_prospect_progress.scouting_confidence
            END,
            times_scouted = scouting_prospect_progress.times_scouted + 1,
            last_scouted_date = excluded.last_scouted_date,
            last_report = excluded.last_report,
            updated_at = datetime('now')
        """,
        (
            game_id,
            draft_year,
            int(prospect["prospect_id"]),
            CONFIDENCE_TARGET_LEVELS["Medium"],
            sweep_date,
            report,
        ),
    )
    con.execute(
        """
        UPDATE draft_prospects
        SET scout_confidence = CASE
                WHEN COALESCE(scout_confidence, 'Low') = 'Low' THEN 'Medium'
                ELSE scout_confidence
            END,
            scout_grade = CASE
                WHEN COALESCE(scout_confidence, 'Low') = 'Low' THEN ?
                ELSE scout_grade
            END,
            scout_ceiling = CASE
                WHEN COALESCE(scout_confidence, 'Low') = 'Low' THEN ?
                ELSE scout_ceiling
            END,
            updated_at = datetime('now')
        WHERE prospect_id = ?
        """,
        (
            tighten_displayed_scout_read(
                prospect["scout_grade"],
                prospect["true_grade"],
                "Medium",
                public_board_rank=prospect["public_board_rank"],
                prospect_id=prospect["prospect_id"],
                draft_year=draft_year,
            ),
            tighten_displayed_scout_read(
                prospect["scout_ceiling"],
                prospect["ceiling_grade"],
                "Medium",
                ceiling=True,
                public_board_rank=prospect["public_board_rank"],
                prospect_id=prospect["prospect_id"],
                draft_year=draft_year,
            ),
            int(prospect["prospect_id"]),
        ),
    )


def upsert_cpu_pre_draft_medium(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    team_id: int,
    prospect: sqlite3.Row,
    sweep_date: str,
) -> None:
    report = "Late pre-draft cross-check moved this public-board file from Low to Medium confidence."
    con.execute(
        """
        INSERT INTO cpu_scouting_prospect_progress (
            game_id, draft_year, team_id, prospect_id, visibility_status,
            scouting_level, scouting_confidence, times_scouted, last_scouted_date,
            last_report, updated_at
        )
        VALUES (?, ?, ?, ?, 'known', ?, 'Medium', 1, ?, ?, datetime('now'))
        ON CONFLICT(game_id, draft_year, team_id, prospect_id) DO UPDATE SET
            visibility_status = CASE
                WHEN cpu_scouting_prospect_progress.visibility_status = 'hidden' THEN 'known'
                ELSE cpu_scouting_prospect_progress.visibility_status
            END,
            scouting_level = MAX(cpu_scouting_prospect_progress.scouting_level, excluded.scouting_level),
            scouting_confidence = CASE
                WHEN cpu_scouting_prospect_progress.scouting_confidence = 'Low' THEN 'Medium'
                ELSE cpu_scouting_prospect_progress.scouting_confidence
            END,
            times_scouted = cpu_scouting_prospect_progress.times_scouted + 1,
            last_scouted_date = excluded.last_scouted_date,
            last_report = excluded.last_report,
            updated_at = datetime('now')
        """,
        (
            game_id,
            draft_year,
            team_id,
            int(prospect["prospect_id"]),
            CONFIDENCE_TARGET_LEVELS["Medium"],
            sweep_date,
            report,
        ),
    )


def run_pre_draft_public_scouting_sweep(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    draft_year: int | None = None,
    seed: str | None = None,
) -> dict[str, int]:
    ensure_schema(con)
    target_game_id = active_game_id(con, game_id)
    class_row = draft_class_row(con, draft_year)
    if not class_row:
        return {"user_updates": 0, "cpu_updates": 0, "teams": 0, "already_run": 0}
    target_year = int(class_row["draft_year"])
    draft_class_id = int(class_row["draft_class_id"])
    existing = con.execute(
        """
        SELECT user_updates, cpu_updates
        FROM scouting_pre_draft_sweeps
        WHERE game_id = ? AND draft_year = ?
        """,
        (target_game_id, target_year),
    ).fetchone()
    if existing:
        return {
            "user_updates": int(existing["user_updates"] or 0),
            "cpu_updates": int(existing["cpu_updates"] or 0),
            "teams": 0,
            "already_run": 1,
        }

    sweep_date = current_date(con)
    active_user_team_id = user_team_id(con)
    base_seed = seed or f"{target_game_id}:{target_year}:pre-draft-public-sweep"
    user_rng = random.Random(f"{base_seed}:user")

    user_updates = 0
    user_rows = [
        *choose_pre_draft_public_rows(
            public_pre_draft_candidate_rows(con, draft_class_id=draft_class_id, early=True, game_id=target_game_id),
            count=PRE_DRAFT_PUBLIC_EARLY_COUNT,
            rng=user_rng,
        ),
        *choose_pre_draft_public_rows(
            public_pre_draft_candidate_rows(con, draft_class_id=draft_class_id, early=False, game_id=target_game_id),
            count=PRE_DRAFT_PUBLIC_LATE_COUNT,
            rng=user_rng,
        ),
    ]
    for prospect in user_rows:
        upsert_user_pre_draft_medium(
            con,
            game_id=target_game_id,
            draft_year=target_year,
            prospect=prospect,
            sweep_date=sweep_date,
        )
        user_updates += 1

    cpu_updates = 0
    teams = con.execute("SELECT team_id FROM teams ORDER BY team_id").fetchall()
    for team in teams:
        team_id_value = int(team["team_id"])
        team_rng = random.Random(f"{base_seed}:team:{team_id_value}")
        team_rows = [
            *choose_pre_draft_public_rows(
                public_pre_draft_candidate_rows(
                    con,
                    draft_class_id=draft_class_id,
                    early=True,
                    game_id=target_game_id,
                    team_id=team_id_value,
                ),
                count=PRE_DRAFT_PUBLIC_EARLY_COUNT,
                rng=team_rng,
            ),
            *choose_pre_draft_public_rows(
                public_pre_draft_candidate_rows(
                    con,
                    draft_class_id=draft_class_id,
                    early=False,
                    game_id=target_game_id,
                    team_id=team_id_value,
                ),
                count=PRE_DRAFT_PUBLIC_LATE_COUNT,
                rng=team_rng,
            ),
        ]
        for prospect in team_rows:
            upsert_cpu_pre_draft_medium(
                con,
                game_id=target_game_id,
                draft_year=target_year,
                team_id=team_id_value,
                prospect=prospect,
                sweep_date=sweep_date,
            )
            cpu_updates += 1

    con.execute(
        """
        INSERT INTO scouting_pre_draft_sweeps (
            game_id, draft_year, user_team_id, early_per_team, late_per_team,
            user_updates, cpu_updates, seed
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            target_game_id,
            target_year,
            active_user_team_id,
            PRE_DRAFT_PUBLIC_EARLY_COUNT,
            PRE_DRAFT_PUBLIC_LATE_COUNT,
            user_updates,
            cpu_updates,
            base_seed,
        ),
    )
    return {
        "user_updates": user_updates,
        "cpu_updates": cpu_updates,
        "teams": len(teams),
        "already_run": 0,
    }


def initialize_for_game(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    draft_year: int | None = None,
    reset: bool = False,
    welcome_message: bool = True,
) -> dict[str, int]:
    ensure_schema(con)
    target_game_id = active_game_id(con, game_id)
    class_row = draft_class_row(con, draft_year)
    if not class_row:
        return {"public": 0, "hidden": 0, "draft_year": draft_year or current_season(con) + 1}
    target_year = int(class_row["draft_year"])
    draft_class_id = int(class_row["draft_class_id"])
    senior_bowl_setup = backfill_senior_bowl_fields(con, draft_year=target_year)
    existing_progress_count = con.execute(
        """
        SELECT COUNT(*) AS c
        FROM scouting_prospect_progress
        WHERE game_id = ?
          AND draft_year = ?
        """,
        (target_game_id, target_year),
    ).fetchone()["c"]
    if reset:
        con.execute(
            "DELETE FROM scouting_assignments WHERE game_id = ? AND draft_year = ?",
            (target_game_id, target_year),
        )
        con.execute(
            "DELETE FROM scouting_prospect_progress WHERE game_id = ? AND draft_year = ?",
            (target_game_id, target_year),
        )
        existing_progress_count = 0
    public_rows = con.execute(
        """
        SELECT prospect_id
        FROM draft_prospects
        WHERE draft_class_id = ?
          AND COALESCE(public_board_status, 'public_board') <> 'off_public_board'
        """,
        (draft_class_id,),
    ).fetchall()
    for row in public_rows:
        con.execute(
            """
            INSERT INTO scouting_prospect_progress (
                game_id, draft_year, prospect_id, visibility_status, scouting_level, scouting_confidence
            )
            VALUES (?, ?, ?, 'known', 15, 'Low')
            ON CONFLICT(game_id, draft_year, prospect_id) DO NOTHING
            """,
            (target_game_id, target_year, int(row["prospect_id"])),
        )
    if existing_progress_count == 0:
        con.execute(
            """
            UPDATE draft_prospects
            SET scout_confidence = 'Low'
            WHERE draft_class_id = ?
              AND COALESCE(public_board_status, 'public_board') <> 'off_public_board'
            """,
            (draft_class_id,),
        )
    hidden_count = con.execute(
        """
        SELECT COUNT(*) AS c
        FROM draft_prospects
        WHERE draft_class_id = ?
          AND COALESCE(public_board_status, '') = 'off_public_board'
        """,
        (draft_class_id,),
    ).fetchone()["c"]
    if welcome_message:
        existing = con.execute(
            """
            SELECT 1
            FROM user_inbox_messages
            WHERE game_id = ?
              AND category = 'Scouting'
              AND title = ?
            LIMIT 1
            """,
            (target_game_id, f"{target_year} Draft Scouting Opened"),
        ).fetchone()
        if not existing:
            add_inbox_message(
                con,
                game_id=target_game_id,
                title=f"{target_year} Draft Scouting Opened",
                body=(
                    f"The scouting department loaded {len(public_rows)} public-board prospects. "
                    f"{hidden_count} off-board names are not visible yet and can be discovered during the season. "
                    f"{senior_bowl_setup['accepted']} prospects are currently listed as Senior Bowl participants."
                ),
                category="Scouting",
                priority="normal",
                related_table="draft_classes",
                related_id=draft_class_id,
            )
    return {
        "public": len(public_rows),
        "hidden": int(hidden_count or 0),
        "draft_year": target_year,
        "senior_bowl_accepted": int(senior_bowl_setup["accepted"]),
    }


def prospect_name(row: sqlite3.Row) -> str:
    return f"{row['first_name']} {row['last_name']}"


def assignment_period(con: sqlite3.Connection, season: int | None = None, week: int | None = None) -> ScoutingPeriod:
    current = current_scouting_period(con)
    return ScoutingPeriod(
        season=season if season is not None else current.season,
        week=week if week is not None else current.week,
        label=current.label if week is None else f"Week {week} Scouting",
        date=current.date,
    )


def assign_prospect(
    con: sqlite3.Connection,
    *,
    prospect_id: int,
    game_id: str | None = None,
    draft_year: int | None = None,
    season: int | None = None,
    week: int | None = None,
    focus: str = "film",
) -> sqlite3.Row:
    ensure_schema(con)
    target_game_id = active_game_id(con, game_id)
    class_row = draft_class_row(con, draft_year)
    if not class_row:
        raise ValueError("No draft class found for scouting.")
    target_year = int(class_row["draft_year"])
    initialize_for_game(con, game_id=target_game_id, draft_year=target_year, welcome_message=False)
    prospect = con.execute(
        """
        SELECT *
        FROM draft_prospects
        WHERE prospect_id = ?
          AND draft_class_id = ?
        """,
        (prospect_id, int(class_row["draft_class_id"])),
    ).fetchone()
    if not prospect:
        raise ValueError(f"Prospect {prospect_id} is not in the active draft class.")
    if str(prospect["public_board_status"] or "") == "off_public_board":
        visible = con.execute(
            """
            SELECT 1
            FROM scouting_prospect_progress
            WHERE game_id = ?
              AND draft_year = ?
              AND prospect_id = ?
              AND visibility_status <> 'hidden'
            """,
            (target_game_id, target_year, prospect_id),
        ).fetchone()
        if not visible:
            raise ValueError("That prospect has not been discovered yet.")
    period = assignment_period(con, season, week)
    window = weekly_scouting_window_status(con, period)
    if not window["open"]:
        raise ValueError(str(window["reason"] or "Weekly scouting is not open."))
    non_specific_action = con.execute(
        """
        SELECT action_key
        FROM scouting_weekly_actions
        WHERE game_id = ?
          AND draft_year = ?
          AND season = ?
          AND week = ?
          AND action_key <> 'specific'
        LIMIT 1
        """,
        (target_game_id, target_year, period.season, period.week),
    ).fetchone()
    if non_specific_action:
        label = SIMPLE_ACTION_LABELS.get(str(non_specific_action["action_key"]), str(non_specific_action["action_key"]))
        raise ValueError(f"The weekly scouting choice has already been used for {period.label}: {label}.")
    existing_assignment = con.execute(
        """
        SELECT 1
        FROM scouting_assignments
        WHERE game_id = ?
          AND draft_year = ?
          AND season = ?
          AND week = ?
          AND prospect_id = ?
          AND status = 'pending'
        """,
        (target_game_id, target_year, period.season, period.week, prospect_id),
    ).fetchone()
    pending_row = con.execute(
        """
        SELECT
            COUNT(*) AS pending_count,
            COALESCE(SUM(CASE WHEN UPPER(COALESCE(dp.position, '')) = 'QB' THEN ? ELSE 1 END), 0) AS pending_cost
        FROM scouting_assignments
        JOIN draft_prospects dp
          ON dp.prospect_id = scouting_assignments.prospect_id
        WHERE scouting_assignments.game_id = ?
          AND scouting_assignments.draft_year = ?
          AND scouting_assignments.season = ?
          AND scouting_assignments.week = ?
          AND scouting_assignments.status = 'pending'
        """,
        (SPECIFIC_SCOUTING_COUNT, target_game_id, target_year, period.season, period.week),
    ).fetchone()
    processed_specific_uses = con.execute(
        """
        SELECT COALESCE(SUM(uses), 0) AS c
        FROM scouting_weekly_actions
        WHERE game_id = ?
          AND draft_year = ?
          AND season = ?
          AND week = ?
          AND action_key = 'specific'
        """,
        (target_game_id, target_year, period.season, period.week),
    ).fetchone()["c"]
    pending_cost = int(pending_row["pending_cost"] or 0)
    new_cost = specific_scouting_cost(prospect["position"])
    if not existing_assignment and pending_cost + int(processed_specific_uses or 0) + new_cost > SPECIFIC_SCOUTING_COUNT:
        raise ValueError(f"You already selected {SPECIFIC_SCOUTING_COUNT} specific players for {period.label}. Unselect one to change your mind.")
    normalized_focus = focus if focus in FOCUS_LABELS else "film"
    con.execute(
        """
        INSERT INTO scouting_assignments (
            game_id, draft_year, season, week, prospect_id, focus, status
        )
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
        ON CONFLICT(game_id, draft_year, season, week, prospect_id) DO UPDATE SET
            focus = excluded.focus,
            status = CASE
                WHEN scouting_assignments.status = 'processed' THEN scouting_assignments.status
                ELSE 'pending'
            END
        """,
        (target_game_id, target_year, period.season, period.week, prospect_id, normalized_focus),
    )
    add_inbox_message(
        con,
        game_id=target_game_id,
        title=f"Scouting Queued: {prospect_name(prospect)}",
        body=(
            f"{prospect_name(prospect)} ({prospect['position']}, {prospect['college']}) "
            f"was added to {period.label} with a {FOCUS_LABELS[normalized_focus]} focus."
        ),
        category="Scouting",
        priority="normal",
        related_table="draft_prospects",
        related_id=prospect_id,
    )
    return prospect


def unassign_prospect(
    con: sqlite3.Connection,
    *,
    prospect_id: int,
    game_id: str | None = None,
    draft_year: int | None = None,
    season: int | None = None,
    week: int | None = None,
) -> dict[str, Any]:
    ensure_schema(con)
    target_game_id = active_game_id(con, game_id)
    class_row = draft_class_row(con, draft_year)
    if not class_row:
        raise ValueError("No draft class found for scouting.")
    target_year = int(class_row["draft_year"])
    period = assignment_period(con, season, week)
    prospect = con.execute(
        """
        SELECT *
        FROM draft_prospects
        WHERE prospect_id = ?
          AND draft_class_id = ?
        """,
        (prospect_id, int(class_row["draft_class_id"])),
    ).fetchone()
    if not prospect:
        raise ValueError(f"Prospect {prospect_id} is not in the active draft class.")
    deleted = con.execute(
        """
        DELETE FROM scouting_assignments
        WHERE game_id = ?
          AND draft_year = ?
          AND season = ?
          AND week = ?
          AND prospect_id = ?
          AND status = 'pending'
        """,
        (target_game_id, target_year, period.season, period.week, prospect_id),
    ).rowcount
    return {
        "removed": int(deleted or 0),
        "name": prospect_name(prospect),
        "position": prospect["position"],
        "college": prospect["college"],
        "period": period.label,
    }


def trait_display(con: sqlite3.Connection, trait_key: str) -> str:
    if table_exists(con, "personality_trait_definitions"):
        row = con.execute(
            "SELECT display_name FROM personality_trait_definitions WHERE trait_key = ?",
            (trait_key,),
        ).fetchone()
        if row and row["display_name"]:
            return str(row["display_name"])
    return trait_key.replace("_", " ").title()


def scouting_note(prospect: sqlite3.Row, focus: str, level_gain: int, personality_text: str | None) -> str:
    name = prospect_name(prospect)
    strengths = str(prospect["scouting_strengths"] or "the early flashes are still being sorted")
    concerns = str(prospect["scouting_concerns"] or "the staff wants a cleaner second look")
    if focus == "personality":
        base = f"Area scouts dug into {name}'s background. The current read is still incomplete, but the file is more useful than last week."
    elif focus == "medical":
        base = f"The medical cross-check on {name} added availability context and helped separate normal risk from real red flags."
    elif focus == "workout":
        base = f"The workout review on {name} gave the staff more confidence in which athletic traits should translate."
    elif focus == "game":
        base = f"Live-game exposure on {name} clarified how the traits showed up when the script got messy."
    else:
        base = f"Additional tape study on {name} sharpened the role projection and cleaned up some first-pass uncertainty."
    detail = f" Level +{level_gain}. Strengths noted: {strengths}. Concerns noted: {concerns}."
    if personality_text:
        detail += f" Background note: {personality_text}."
    return base + detail


def require_weekly_action_available(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    period: ScoutingPeriod,
    action_key: str,
) -> None:
    ensure_schema(con)
    window = weekly_scouting_window_status(con, period)
    if not window["open"]:
        raise ValueError(str(window["reason"] or "Weekly scouting is not open."))
    existing = con.execute(
        """
        SELECT action_key, uses
        FROM scouting_weekly_actions
        WHERE game_id = ?
          AND draft_year = ?
          AND season = ?
          AND week = ?
        """,
        (game_id, draft_year, period.season, period.week),
    ).fetchall()
    uses_by_action = {str(row["action_key"]): int(row["uses"] or 0) for row in existing}
    if action_key == "specific":
        if not existing:
            return
        non_specific = [key for key in uses_by_action if key != "specific"]
        if non_specific:
            label = SIMPLE_ACTION_LABELS.get(non_specific[0], non_specific[0])
            raise ValueError(f"The weekly scouting choice has already been used for {period.label}: {label}. Advance to the next week to scout again.")
        if uses_by_action.get("specific", 0) >= SPECIFIC_SCOUTING_COUNT:
            raise ValueError(f"The weekly specific-player scouts have already been used for {period.label}. Advance to the next week to scout again.")
        return
    pending_specific = con.execute(
        """
        SELECT COUNT(*) AS c
        FROM scouting_assignments
        WHERE game_id = ?
          AND draft_year = ?
          AND season = ?
          AND week = ?
          AND status = 'pending'
        """,
        (game_id, draft_year, period.season, period.week),
    ).fetchone()["c"]
    if int(pending_specific or 0) > 0:
        raise ValueError(f"You already selected specific players for {period.label}. Unselect them to choose another scouting package.")
    if not existing:
        return
    label = SIMPLE_ACTION_LABELS.get(next(iter(uses_by_action)), next(iter(uses_by_action)))
    raise ValueError(f"The weekly scouting choice has already been used for {period.label}: {label}. Advance to the next week to scout again.")


def record_weekly_action(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    period: ScoutingPeriod,
    action_key: str,
    description: str,
    uses: int = 1,
) -> None:
    ensure_schema(con)
    con.execute(
        """
        INSERT INTO scouting_weekly_actions (
            game_id, draft_year, season, week, action_key, uses, description
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_id, draft_year, season, week, action_key) DO UPDATE SET
            uses = uses + excluded.uses,
            description = excluded.description,
            created_at = datetime('now')
        """,
        (game_id, draft_year, period.season, period.week, action_key, max(1, int(uses)), description),
    )


def visible_scouting_candidates(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    draft_class_id: int,
    include_very_high: bool = False,
) -> list[sqlite3.Row]:
    confidence_filter = "" if include_very_high else "AND COALESCE(spp.scouting_confidence, 'Low') <> 'Very High'"
    return con.execute(
        f"""
        SELECT dp.*, spp.scouting_level, spp.scouting_confidence, spp.visibility_status, spp.times_scouted
        FROM draft_prospects dp
        JOIN scouting_prospect_progress spp
          ON spp.prospect_id = dp.prospect_id
         AND spp.game_id = ?
         AND spp.draft_year = ?
        WHERE dp.draft_class_id = ?
          AND (
                COALESCE(dp.public_board_status, 'public_board') <> 'off_public_board'
                OR spp.visibility_status = 'discovered'
              )
          {confidence_filter}
        ORDER BY
          CASE COALESCE(spp.scouting_confidence, 'Low')
            WHEN 'Low' THEN 0
            WHEN 'Medium' THEN 1
            WHEN 'High' THEN 2
            ELSE 3
          END,
          CASE WHEN dp.public_board_rank IS NULL THEN 999 ELSE dp.public_board_rank END,
          dp.scout_grade DESC,
          dp.scouting_variance DESC
        """,
        (game_id, draft_year, draft_class_id),
    ).fetchall()


def board_rank(row: sqlite3.Row, default: int = 9999) -> int:
    return int(row_value(row, "public_board_rank", row_value(row, "scouting_rank", default)) or default)


def prospect_position_group(prospect: sqlite3.Row) -> str:
    return team_eval.position_group(str(row_value(prospect, "position", "") or ""))


def user_team_need_scores(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    evaluation_date: str,
) -> dict[str, float]:
    return cpu_team_need_scores(
        con,
        team_abbr=user_team_abbr(con),
        season=season,
        game_id=game_id,
        evaluation_date=evaluation_date,
    )


def user_auto_scouting_score(
    prospect: sqlite3.Row,
    *,
    need_scores: dict[str, float],
    pick_profile: dict[str, Any],
) -> float:
    rank = board_rank(prospect, 260)
    confidence = normalize_confidence(row_value(prospect, "scouting_confidence", "Low"))
    need = need_scores.get(prospect_position_group(prospect), 0.0)
    grade = float(row_value(prospect, "scout_grade", row_value(prospect, "true_grade", 55)) or 55)
    ceiling = float(row_value(prospect, "scout_ceiling", row_value(prospect, "ceiling_grade", grade)) or grade)
    variance = float(row_value(prospect, "scouting_variance", 0) or 0)

    score = 0.0
    score += max(0.0, 120.0 - min(rank, 180)) * 0.33
    score += (need / 100.0) * (34.0 if rank <= 64 else 22.0 if rank <= 128 else 12.0)
    score += max(0.0, ceiling - grade) * 0.28
    score += variance * 0.08

    if pick_profile.get("has_first_round_pick") and rank <= 48:
        score += 22.0
    elif rank <= 96:
        score += 10.0

    if confidence == "Low":
        score += 4.0 if rank <= 96 else 9.0
    elif confidence == "Medium":
        score += 24.0 if rank <= 64 else 14.0
    elif confidence == "High":
        score += 18.0 if rank <= 64 else 7.0
    else:
        score -= 100.0

    if need < 34.0 and confidence in {"Medium", "High"}:
        score -= USER_AUTO_NON_NEED_REPEAT_PENALTY
    if need < 20.0 and rank > 96:
        score -= 12.0

    if row_value(prospect, "public_board_status", "") == "off_public_board":
        score += 4.0
    return score


def select_user_auto_assign_candidates(
    candidates: list[sqlite3.Row],
    *,
    need_scores: dict[str, float],
    pick_profile: dict[str, Any],
    count: int,
    first_round_very_high_remaining: int | None = None,
) -> list[sqlite3.Row]:
    selected: list[sqlite3.Row] = []
    selected_ids: set[int] = set()
    first_round_vh_slots = (
        9999
        if first_round_very_high_remaining is None
        else max(0, int(first_round_very_high_remaining))
    )

    def add_from(pool: list[sqlite3.Row], limit: int) -> None:
        nonlocal first_round_vh_slots
        for prospect in sorted(
            pool,
            key=lambda row: (
                -user_auto_scouting_score(row, need_scores=need_scores, pick_profile=pick_profile),
                board_rank(row),
            ),
        ):
            if len(selected) >= count or limit <= 0:
                return
            prospect_id = int(prospect["prospect_id"])
            if prospect_id in selected_ids:
                continue
            rank = board_rank(prospect)
            confidence = normalize_confidence(row_value(prospect, "scouting_confidence", "Low"))
            would_create_first_round_vh = rank <= 32 and confidence == "High"
            if would_create_first_round_vh and first_round_vh_slots <= 0:
                continue
            selected.append(prospect)
            selected_ids.add(prospect_id)
            if would_create_first_round_vh:
                first_round_vh_slots -= 1
            limit -= 1

    due_diligence = [
        row
        for row in candidates
        if board_rank(row) <= 64
        and normalize_confidence(row_value(row, "scouting_confidence", "Low")) in {"Medium", "High"}
        and (
            need_scores.get(prospect_position_group(row), 0.0) >= 34.0
            or user_auto_scouting_score(row, need_scores=need_scores, pick_profile=pick_profile) >= 42.0
        )
    ]
    add_from(due_diligence, min(count, USER_AUTO_DUE_DILIGENCE_MIN))

    need_pool = [
        row
        for row in candidates
        if board_rank(row) <= 180
        and need_scores.get(prospect_position_group(row), 0.0) >= 48.0
    ]
    add_from(need_pool, min(count - len(selected), USER_AUTO_NEED_MIN))

    depth_pool = [row for row in candidates if 65 <= board_rank(row) <= 220]
    add_from(depth_pool, max(0, count - len(selected)))
    add_from(candidates, max(0, count - len(selected)))
    return selected[:count]


def simple_scouting_report(prospect: sqlite3.Row, old_confidence: str, new_confidence: str, reason: str) -> str:
    name = prospect_name(prospect)
    strengths = str(prospect["scouting_strengths"] or "the staff found usable traits worth another look")
    concerns = str(prospect["scouting_concerns"] or "the projection still needs more evidence")
    return (
        f"{reason} {name}'s file moved from {old_confidence} to {new_confidence} confidence. "
        f"Current read: {prospect['position']} from {prospect['college']}. "
        f"Strengths: {strengths}. Concerns: {concerns}."
    )


def tighten_displayed_scout_read(
    current_value: Any,
    true_value: Any,
    confidence: str,
    *,
    ceiling: bool = False,
    public_board_rank: Any = None,
    prospect_id: Any = None,
    draft_year: Any = None,
) -> int:
    """Move the shared board read toward true value as confidence rises."""
    current = float(current_value or true_value or 50)
    true = float(true_value or current)
    normalized = normalize_confidence(confidence)
    try:
        rank = int(public_board_rank or 9999)
    except (TypeError, ValueError):
        rank = 9999
    early_public_board = 1 <= rank <= 50
    weight = {
        "Low": 0.18,
        "Medium": 0.42,
        "High": 0.74,
        "Very High": 0.90,
    }.get(normalized, 0.18)
    if early_public_board and normalized == "High":
        weight = min(weight, 0.62)
    elif early_public_board and normalized == "Very High":
        weight = min(weight, 0.78)
    tightened = current + ((true - current) * weight)
    max_gap = {
        "Low": 18.0 if ceiling else 12.0,
        "Medium": 10.0 if ceiling else 7.0,
        "High": 5.0 if ceiling else 4.0,
        "Very High": 3.0 if ceiling else 2.0,
    }.get(normalized, 12.0)
    if early_public_board and normalized in {"High", "Very High"}:
        max_gap += 2.0 if ceiling else 1.5
    if abs(tightened - true) > max_gap:
        tightened = true + (max_gap if tightened > true else -max_gap)
    if early_public_board and normalized in {"High", "Very High"} and prospect_id is not None:
        jitter_sigma = {
            ("High", False): 1.15,
            ("High", True): 1.65,
            ("Very High", False): 0.80,
            ("Very High", True): 1.15,
        }[(normalized, ceiling)]
        jitter_limit = 3.5 if ceiling else 2.5
        seed = f"early-public-scout-read:{draft_year or ''}:{prospect_id}:{normalized}:{'ceiling' if ceiling else 'grade'}"
        jitter = random.Random(seed).gauss(0.0, jitter_sigma)
        tightened += max(-jitter_limit, min(jitter_limit, jitter))
    return int(round(max(20.0, min(99.0, tightened))))


def advance_prospect_one_tier(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    period: ScoutingPeriod,
    prospect: sqlite3.Row,
    reason: str,
    source: str = "College Scouting",
) -> dict[str, Any]:
    current = con.execute(
        """
        SELECT *
        FROM scouting_prospect_progress
        WHERE game_id = ?
          AND draft_year = ?
          AND prospect_id = ?
        """,
        (game_id, draft_year, int(prospect["prospect_id"])),
    ).fetchone()
    old_confidence = normalize_confidence(current["scouting_confidence"] if current else "Low")
    new_confidence = next_confidence(old_confidence)
    old_level = int(current["scouting_level"] or CONFIDENCE_TARGET_LEVELS[old_confidence]) if current else 15
    new_level = max(old_level, CONFIDENCE_TARGET_LEVELS[new_confidence])
    report = simple_scouting_report(prospect, old_confidence, new_confidence, reason)
    visibility = "discovered" if str(prospect["public_board_status"] or "") == "off_public_board" else "known"
    if current and str(current["visibility_status"] or "") not in {"hidden", ""}:
        visibility = str(current["visibility_status"])

    con.execute(
        """
        INSERT INTO scouting_prospect_progress (
            game_id, draft_year, prospect_id, visibility_status, scouting_level,
            scouting_confidence, times_scouted, last_scouted_season,
            last_scouted_week, last_scouted_date, last_report, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(game_id, draft_year, prospect_id) DO UPDATE SET
            visibility_status = CASE
                WHEN scouting_prospect_progress.visibility_status = 'hidden' THEN excluded.visibility_status
                ELSE scouting_prospect_progress.visibility_status
            END,
            scouting_level = excluded.scouting_level,
            scouting_confidence = excluded.scouting_confidence,
            times_scouted = scouting_prospect_progress.times_scouted + 1,
            last_scouted_season = excluded.last_scouted_season,
            last_scouted_week = excluded.last_scouted_week,
            last_scouted_date = excluded.last_scouted_date,
            last_report = excluded.last_report,
            updated_at = datetime('now')
        """,
        (
            game_id,
            draft_year,
            int(prospect["prospect_id"]),
            visibility,
            new_level,
            new_confidence,
            period.season,
            period.week,
            period.date,
            report,
        ),
    )
    con.execute(
        """
        UPDATE draft_prospects
        SET scout_confidence = ?,
            scout_grade = ?,
            scout_ceiling = ?,
            discovery_status = CASE
                WHEN COALESCE(public_board_status, '') = 'off_public_board'
                     AND COALESCE(discovery_status, 'undiscovered') = 'undiscovered'
                THEN 'discovered'
                ELSE discovery_status
            END,
            updated_at = datetime('now')
        WHERE prospect_id = ?
        """,
        (
            new_confidence,
            tighten_displayed_scout_read(
                prospect["scout_grade"],
                prospect["true_grade"],
                new_confidence,
                public_board_rank=prospect["public_board_rank"],
                prospect_id=prospect["prospect_id"],
                draft_year=draft_year,
            ),
            tighten_displayed_scout_read(
                prospect["scout_ceiling"],
                prospect["ceiling_grade"],
                new_confidence,
                ceiling=True,
                public_board_rank=prospect["public_board_rank"],
                prospect_id=prospect["prospect_id"],
                draft_year=draft_year,
            ),
            int(prospect["prospect_id"]),
        ),
    )
    add_inbox_message(
        con,
        game_id=game_id,
        title=f"Scouting Update: {prospect_name(prospect)}",
        body=report,
        category="Scouting",
        priority="normal" if new_confidence in {"Low", "Medium"} else "high",
        source=source,
        message_date=period.date,
        related_table="draft_prospects",
        related_id=int(prospect["prospect_id"]),
    )
    return {
        "prospect_id": int(prospect["prospect_id"]),
        "name": prospect_name(prospect),
        "position": prospect["position"],
        "college": prospect["college"],
        "old_confidence": old_confidence,
        "new_confidence": new_confidence,
        "scouting_level": new_level,
    }


def simple_action_context(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    draft_year: int | None = None,
    season: int | None = None,
    week: int | None = None,
) -> tuple[str, sqlite3.Row, int, ScoutingPeriod]:
    ensure_schema(con)
    target_game_id = active_game_id(con, game_id)
    class_row = draft_class_row(con, draft_year)
    if not class_row:
        raise ValueError("No draft class found for scouting.")
    target_year = int(class_row["draft_year"])
    initialize_for_game(con, game_id=target_game_id, draft_year=target_year, welcome_message=False)
    return target_game_id, class_row, target_year, assignment_period(con, season, week)


def auto_assign_scouts(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    draft_year: int | None = None,
    season: int | None = None,
    week: int | None = None,
    count: int = AUTO_ASSIGN_COUNT,
) -> dict[str, Any]:
    target_game_id, class_row, target_year, period = simple_action_context(
        con,
        game_id=game_id,
        draft_year=draft_year,
        season=season,
        week=week,
    )
    require_weekly_action_available(con, game_id=target_game_id, draft_year=target_year, period=period, action_key="auto_assign")
    candidates = visible_scouting_candidates(
        con,
        game_id=target_game_id,
        draft_year=target_year,
        draft_class_id=int(class_row["draft_class_id"]),
    )
    team_id = user_team_id(con)
    pick_profile = cpu_draft_pick_profile(con, team_id, target_year) if team_id else {}
    need_scores = user_team_need_scores(
        con,
        game_id=target_game_id,
        season=period.season,
        evaluation_date=period.date,
    )
    first_round_very_high = int(
        con.execute(
            """
            SELECT COUNT(*) AS count
            FROM draft_prospects dp
            JOIN scouting_prospect_progress spp
              ON spp.prospect_id = dp.prospect_id
             AND spp.game_id = ?
             AND spp.draft_year = ?
            WHERE dp.draft_class_id = ?
              AND dp.public_board_rank BETWEEN 1 AND 32
              AND spp.scouting_confidence = 'Very High'
            """,
            (target_game_id, target_year, int(class_row["draft_class_id"])),
        ).fetchone()["count"]
        or 0
    )
    selected = select_user_auto_assign_candidates(
        candidates,
        need_scores=need_scores,
        pick_profile=pick_profile,
        count=max(1, count),
        first_round_very_high_remaining=USER_AUTO_FIRST_ROUND_VERY_HIGH_CAP - first_round_very_high,
    )
    if not selected:
        raise ValueError("No visible prospects need more scouting right now.")
    results = [
        advance_prospect_one_tier(
            con,
            game_id=target_game_id,
            draft_year=target_year,
            period=period,
            prospect=prospect,
            reason="The scouting director auto-assigned the weekly staff and sharpened",
        )
        for prospect in selected
    ]
    record_weekly_action(
        con,
        game_id=target_game_id,
        draft_year=target_year,
        period=period,
        action_key="auto_assign",
        description=f"Auto-scouted {len(results)} prospects.",
    )
    background = discover_user_extra_hidden_prospect(
        con,
        game_id=target_game_id,
        draft_year=target_year,
        draft_class_id=int(class_row["draft_class_id"]),
        period=period,
        rng=random.Random(f"{target_game_id}:{target_year}:{period.season}:{period.week}:user-background-discovery"),
    )
    return {"action": "auto_assign", "period": period.label, "advanced": results, "background_discoveries": background}


def scout_specific_player(
    con: sqlite3.Connection,
    *,
    prospect_id: int,
    game_id: str | None = None,
    draft_year: int | None = None,
) -> dict[str, Any]:
    target_game_id, class_row, target_year, period = simple_action_context(con, game_id=game_id, draft_year=draft_year)
    require_weekly_action_available(con, game_id=target_game_id, draft_year=target_year, period=period, action_key="specific")
    prospect = con.execute(
        """
        SELECT dp.*, spp.visibility_status, spp.scouting_confidence, spp.scouting_level
        FROM draft_prospects dp
        JOIN scouting_prospect_progress spp
          ON spp.prospect_id = dp.prospect_id
         AND spp.game_id = ?
         AND spp.draft_year = ?
        WHERE dp.draft_class_id = ?
          AND dp.prospect_id = ?
          AND (
                COALESCE(dp.public_board_status, 'public_board') <> 'off_public_board'
                OR spp.visibility_status = 'discovered'
              )
        """,
        (target_game_id, target_year, int(class_row["draft_class_id"]), prospect_id),
    ).fetchone()
    if not prospect:
        raise ValueError("That prospect is not visible on the scouting board yet.")
    if normalize_confidence(prospect["scouting_confidence"]) == "Very High":
        raise ValueError(f"{prospect_name(prospect)} is already at Very High confidence.")
    result = advance_prospect_one_tier(
        con,
        game_id=target_game_id,
        draft_year=target_year,
        period=period,
        prospect=prospect,
        reason="The area scout focused the weekly report on",
    )
    record_weekly_action(
        con,
        game_id=target_game_id,
        draft_year=target_year,
        period=period,
        action_key="specific",
        description=f"Scouted {result['name']}.",
    )
    background = discover_user_extra_hidden_prospect(
        con,
        game_id=target_game_id,
        draft_year=target_year,
        draft_class_id=int(class_row["draft_class_id"]),
        period=period,
        rng=random.Random(f"{target_game_id}:{target_year}:{period.season}:{period.week}:user-background-discovery"),
    )
    return {"action": "specific", "period": period.label, "advanced": [result], "background_discoveries": background}


def scout_random_players(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    draft_year: int | None = None,
    count: int = RANDOM_CROSSCHECK_COUNT,
    seed: str | None = None,
) -> dict[str, Any]:
    target_game_id, class_row, target_year, period = simple_action_context(con, game_id=game_id, draft_year=draft_year)
    require_weekly_action_available(con, game_id=target_game_id, draft_year=target_year, period=period, action_key="random_two")
    rng = random.Random(seed or f"{target_game_id}:{target_year}:{period.season}:{period.week}:random-two")
    candidates = visible_scouting_candidates(
        con,
        game_id=target_game_id,
        draft_year=target_year,
        draft_class_id=int(class_row["draft_class_id"]),
    )
    if not candidates:
        raise ValueError("No visible prospects need more scouting right now.")
    selected = rng.sample(candidates, k=min(count, len(candidates)))
    results = [
        advance_prospect_one_tier(
            con,
            game_id=target_game_id,
            draft_year=target_year,
            period=period,
            prospect=prospect,
            reason="A regional scout used the week on a fresh cross-check and advanced",
            source="Regional Scout",
        )
        for prospect in selected
    ]
    record_weekly_action(
        con,
        game_id=target_game_id,
        draft_year=target_year,
        period=period,
        action_key="random_two",
        description=f"Randomly scouted {len(results)} prospects.",
    )
    background = discover_user_extra_hidden_prospect(
        con,
        game_id=target_game_id,
        draft_year=target_year,
        draft_class_id=int(class_row["draft_class_id"]),
        period=period,
        rng=random.Random(f"{target_game_id}:{target_year}:{period.season}:{period.week}:user-background-discovery"),
    )
    return {"action": "random_two", "period": period.label, "advanced": results, "background_discoveries": background}


def discover_non_public_players(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    draft_year: int | None = None,
    count: int = DISCOVER_NON_PUBLIC_COUNT,
    seed: str | None = None,
) -> dict[str, Any]:
    target_game_id, class_row, target_year, period = simple_action_context(con, game_id=game_id, draft_year=draft_year)
    require_weekly_action_available(con, game_id=target_game_id, draft_year=target_year, period=period, action_key="discover_four")
    rng = random.Random(seed or f"{target_game_id}:{target_year}:{period.season}:{period.week}:discover-four")
    candidates = con.execute(
        """
        SELECT
            dp.*,
            (
                SELECT COUNT(DISTINCT csp2.team_id)
                FROM cpu_scouting_prospect_progress csp2
                WHERE csp2.game_id = ?
                  AND csp2.draft_year = ?
                  AND csp2.prospect_id = dp.prospect_id
                  AND csp2.visibility_status = 'discovered'
            )
            +
            (
                SELECT COUNT(*)
                FROM scouting_prospect_progress spp2
                WHERE spp2.game_id = ?
                  AND spp2.draft_year = ?
                  AND spp2.prospect_id = dp.prospect_id
                  AND spp2.visibility_status = 'discovered'
            ) AS discovered_elsewhere_count
        FROM draft_prospects dp
        LEFT JOIN scouting_prospect_progress spp
          ON spp.prospect_id = dp.prospect_id
         AND spp.game_id = ?
         AND spp.draft_year = ?
        WHERE dp.draft_class_id = ?
          AND COALESCE(dp.public_board_status, '') = 'off_public_board'
          AND COALESCE(dp.discovery_status, 'undiscovered') = 'undiscovered'
          AND spp.prospect_id IS NULL
        """,
        (
            target_game_id,
            target_year,
            target_game_id,
            target_year,
            target_game_id,
            target_year,
            int(class_row["draft_class_id"]),
        ),
    ).fetchall()
    if not candidates:
        raise ValueError("No undiscovered off-public-board prospects remain.")
    random_candidates = visible_scouting_candidates(
        con,
        game_id=target_game_id,
        draft_year=target_year,
        draft_class_id=int(class_row["draft_class_id"]),
    )
    random_selected = rng.sample(random_candidates, k=min(DISCOVER_RANDOM_CROSSCHECK_COUNT, len(random_candidates)))
    random_results = [
        advance_prospect_one_tier(
            con,
            game_id=target_game_id,
            draft_year=target_year,
            period=period,
            prospect=prospect,
            reason="A regional scout paired the hidden-board search with a quick visible-board cross-check and advanced",
            source="Regional Scout",
        )
        for prospect in random_selected
    ]
    selected = choose_hidden_discovery_candidates(candidates, count=count, rng=rng)
    results = []
    for prospect in selected:
        level = DISCOVERY_START_LEVEL
        report = (
            f"Area scouts found {prospect_name(prospect)} ({prospect['position']}, {prospect['college']}). "
            "He was not on the public board. The early file starts at medium confidence, but still needs follow-up work."
        )
        con.execute(
            """
            INSERT INTO scouting_prospect_progress (
                game_id, draft_year, prospect_id, visibility_status, scouting_level,
                scouting_confidence, times_scouted, last_scouted_season,
                last_scouted_week, last_scouted_date, last_report, updated_at
            )
            VALUES (?, ?, ?, 'discovered', ?, ?, 0, ?, ?, ?, ?, datetime('now'))
            """,
            (
                target_game_id,
                target_year,
                int(prospect["prospect_id"]),
                level,
                DISCOVERY_START_CONFIDENCE,
                period.season,
                period.week,
                period.date,
                report,
            ),
        )
        con.execute(
            """
            UPDATE draft_prospects
            SET discovery_status = 'discovered',
                scout_confidence = ?,
                updated_at = datetime('now')
            WHERE prospect_id = ?
            """,
            (DISCOVERY_START_CONFIDENCE, int(prospect["prospect_id"])),
        )
        add_inbox_message(
            con,
            game_id=target_game_id,
            title=f"New Prospect Found: {prospect_name(prospect)}",
            body=report,
            category="Scouting",
            priority="normal",
            source="Area Scout",
            message_date=period.date,
            related_table="draft_prospects",
            related_id=int(prospect["prospect_id"]),
        )
        results.append(
            {
                "prospect_id": int(prospect["prospect_id"]),
                "name": prospect_name(prospect),
                "position": prospect["position"],
                "college": prospect["college"],
                "old_confidence": "Hidden",
                "new_confidence": DISCOVERY_START_CONFIDENCE,
                "scouting_level": level,
            }
        )
    record_weekly_action(
        con,
        game_id=target_game_id,
        draft_year=target_year,
        period=period,
        action_key="discover_four",
        description=f"Scouted {len(random_results)} random prospects and discovered {len(results)} off-public-board prospects.",
    )
    background = discover_user_extra_hidden_prospect(
        con,
        game_id=target_game_id,
        draft_year=target_year,
        draft_class_id=int(class_row["draft_class_id"]),
        period=period,
        rng=random.Random(f"{target_game_id}:{target_year}:{period.season}:{period.week}:user-background-discovery"),
    )
    return {
        "action": "discover_four",
        "period": period.label,
        "advanced": random_results + results,
        "random_advanced": random_results,
        "discovered": results,
        "background_discoveries": background,
    }


def cpu_team_rows(con: sqlite3.Connection, *, exclude_user: bool = True) -> list[sqlite3.Row]:
    user_team = user_team_abbr(con).upper() if exclude_user else None
    rows = con.execute(
        """
        SELECT team_id, abbreviation
        FROM teams
        ORDER BY abbreviation
        """
    ).fetchall()
    return [row for row in rows if not user_team or str(row["abbreviation"]).upper() != user_team]


def cpu_position_need_bonus(con: sqlite3.Connection, team_id: int, position: str) -> int:
    target = CPU_POSITION_TARGETS.get(position.upper(), 3)
    row = con.execute(
        """
        SELECT COUNT(*) AS count
        FROM players
        WHERE team_id = ?
          AND status IN ('Active', 'Reserve/Future', 'Practice Squad', 'PUP', 'IR')
          AND position = ?
        """,
        (team_id, position.upper()),
    ).fetchone()
    count = int(row["count"] or 0) if row else 0
    if count <= max(0, target - 2):
        return 18
    if count < target:
        return 8
    return 0


def cpu_qb_scouting_need_score(con: sqlite3.Connection, team_id: int) -> float:
    """QB scouting should anticipate both bad rooms and looming succession needs."""
    rows = con.execute(
        """
        SELECT player_id, age, overall, potential, years_exp, status
        FROM players
        WHERE team_id = ?
          AND position = 'QB'
          AND status IN ('Active', 'Reserve/Future', 'Practice Squad', 'PUP', 'IR')
        ORDER BY overall DESC, potential DESC
        """,
        (team_id,),
    ).fetchall()
    if not rows:
        return 100.0
    starter = rows[0]
    top_overall = float(starter["overall"] or 0)
    top_potential = float(starter["potential"] or top_overall)
    age = int(starter["age"] or 0)
    starter_id = int(starter["player_id"] or 0)
    target_draft_year = current_season(con) + 1
    score = 0.0
    if top_overall < 66:
        score = max(score, 96.0)
    elif top_overall < 70:
        score = max(score, 88.0)
    elif top_overall < 74:
        score = max(score, 84.0)
    elif top_overall < 76 and top_potential < 82:
        score = max(score, 78.0)
    elif top_overall < 78 and top_potential < 82:
        score = max(score, 62.0)
    if age >= 36:
        score = max(score, 86.0)
    elif age >= 34:
        score = max(score, 72.0)
    elif age >= 32 and top_overall < 82:
        score = max(score, 58.0)
    if len(rows) < 2:
        score = max(score, 48.0)
    elif max(float(row["potential"] or row["overall"] or 0) for row in rows[1:]) < 70 and age >= 30:
        score = max(score, 52.0)
    if starter_id and table_exists(con, "contracts"):
        contract = con.execute(
            """
            SELECT end_year, aav, contract_type, franchise_tag
            FROM contracts
            WHERE player_id = ?
              AND is_active = 1
            ORDER BY end_year DESC, contract_id DESC
            LIMIT 1
            """,
            (starter_id,),
        ).fetchone()
        if contract:
            end_year = int(contract["end_year"] or 0)
            aav = float(contract["aav"] or 0)
            tagged = bool(contract["franchise_tag"] or str(contract["contract_type"] or "").lower().endswith("tag"))
            if end_year <= target_draft_year - 1:
                score = max(score, CPU_QB_CONTRACT_YEAR_NEED)
            elif end_year <= target_draft_year:
                score = max(score, CPU_QB_NEXT_CONTRACT_NEED)
            if tagged and age >= 30:
                score = max(score, CPU_QB_CONTRACT_YEAR_NEED)
            if age >= 32 and end_year <= target_draft_year + 1 and aav >= 20_000_000:
                score = max(score, 72.0)
    return score


def cpu_draft_pick_profile(con: sqlite3.Connection, team_id: int, draft_year: int) -> dict[str, Any]:
    if not table_exists(con, "draft_picks"):
        return {"earliest_round": None, "has_first_round_pick": False, "first_round_picks": 0}
    row = con.execute(
        """
        SELECT
            MIN(round) AS earliest_round,
            SUM(CASE WHEN round = 1 THEN 1 ELSE 0 END) AS first_round_picks
        FROM draft_picks
        WHERE draft_year = ?
          AND current_team_id = ?
          AND COALESCE(is_used, 0) = 0
        """,
        (draft_year, team_id),
    ).fetchone()
    earliest = int(row["earliest_round"]) if row and row["earliest_round"] is not None else None
    first_round_picks = int(row["first_round_picks"] or 0) if row else 0
    return {
        "earliest_round": earliest,
        "has_first_round_pick": first_round_picks > 0,
        "first_round_picks": first_round_picks,
    }


def cpu_first_round_scouting_multiplier(pick_profile: dict[str, Any]) -> float:
    earliest = pick_profile.get("earliest_round")
    if pick_profile.get("has_first_round_pick"):
        return 1.0
    if earliest == 2:
        return 0.45
    if earliest == 3:
        return 0.25
    return 0.10


def cpu_team_need_scores(
    con: sqlite3.Connection,
    *,
    team_abbr: str,
    season: int,
    game_id: str,
    evaluation_date: str,
) -> dict[str, float]:
    try:
        team_eval.ensure_schema(con)
        evaluation = team_eval.evaluate_team(
            con,
            team_abbr=team_abbr,
            season=season,
            game_id=game_id,
            evaluation_date=evaluation_date,
            persist=False,
        )
    except Exception:
        return {}

    scores: dict[str, float] = {}
    team_row = con.execute("SELECT team_id FROM teams WHERE abbreviation = ? LIMIT 1", (team_abbr,)).fetchone()
    team_id = int(team_row["team_id"]) if team_row else 0
    for index, need in enumerate(evaluation.get("roster_needs") or []):
        group = str(need.get("position_group") or "").upper()
        if not group:
            continue
        score = float(need.get("need_score") or 0.0) + max(0, 8 - index) * 1.4
        if group in PREMIUM_POSITION_GROUPS:
            score += PREMIUM_POSITION_SCOUTING_BONUS
        if group in LOW_COST_POSITION_GROUPS:
            score *= 0.45
        scores[group] = max(scores.get(group, 0.0), score)

    for player in evaluation.get("contract_pressure") or []:
        group = str(player.get("position_group") or "").upper()
        if not group:
            continue
        pressure = 7.0
        if int(player.get("years_until_expiry") or 9) <= 1:
            pressure += 5.0
        if group in PREMIUM_POSITION_GROUPS:
            pressure += 2.0
        scores[group] = scores.get(group, 0.0) + pressure
    if team_id:
        qb_score = cpu_qb_scouting_need_score(con, team_id)
        if qb_score >= CPU_QB_SCOUTING_NEED_FLOOR:
            scores["QB"] = max(scores.get("QB", 0.0), qb_score)
    return scores


def cpu_scouting_need_bonus(
    prospect: sqlite3.Row,
    *,
    need_scores: dict[str, float],
    pick_profile: dict[str, Any],
) -> float:
    group = team_eval.position_group(str(prospect["position"]))
    need_score = need_scores.get(group, 0.0)
    if need_score <= 0:
        return 0.0
    multiplier = cpu_first_round_scouting_multiplier(pick_profile)
    base = FIRST_ROUND_SCOUTING_NEED_BONUS if multiplier >= 1.0 else LATER_ROUND_SCOUTING_NEED_BONUS
    return (need_score / 100.0) * base * multiplier


def cpu_first_round_due_diligence_bonus(
    prospect: sqlite3.Row,
    *,
    need_scores: dict[str, float],
    pick_profile: dict[str, Any],
) -> float:
    if not pick_profile.get("has_first_round_pick"):
        return 0.0
    rank = int(prospect["public_board_rank"] or prospect["scouting_rank"] or 9999)
    if rank > CPU_SCOUTING_BUCKETS["early"][1]:
        return 0.0
    group = team_eval.position_group(str(prospect["position"]))
    need_score = need_scores.get(group, 0.0)
    if need_score <= 0:
        return 0.0
    confidence = normalize_confidence(prospect["cpu_scouting_confidence"])
    confidence_multiplier = {"Low": 1.0, "Medium": 0.72, "High": 0.08, "Very High": 0.0}.get(confidence, 0.55)
    rank_multiplier = max(0.30, (CPU_SCOUTING_BUCKETS["early"][1] + 1 - rank) / CPU_SCOUTING_BUCKETS["early"][1])
    return CPU_FIRST_ROUND_DUE_DILIGENCE_BONUS * (need_score / 100.0) * confidence_multiplier * rank_multiplier


def cpu_qb_scouting_priority_bonus(
    prospect: sqlite3.Row,
    *,
    need_scores: dict[str, float],
    pick_profile: dict[str, Any],
) -> float:
    if str(prospect["position"] or "").upper() != "QB":
        return 0.0
    need_score = need_scores.get("QB", 0.0)
    if need_score < CPU_QB_SCOUTING_NEED_FLOOR:
        return 0.0
    rank = int(prospect["public_board_rank"] or prospect["scouting_rank"] or 9999)
    earliest = int(pick_profile.get("earliest_round") or 7)
    if rank > 96 and need_score < CPU_QB_SCOUTING_STRONG_NEED:
        return 0.0
    confidence = normalize_confidence(prospect["cpu_scouting_confidence"])
    confidence_multiplier = {"Low": 1.0, "Medium": 0.78, "High": 0.28, "Very High": 0.0}.get(confidence, 0.7)
    rank_window = 96 if earliest <= 2 else 160
    rank_multiplier = max(0.25, (rank_window + 1 - min(rank, rank_window)) / rank_window)
    pick_multiplier = 1.35 if pick_profile.get("has_first_round_pick") else 0.90 if earliest <= 3 else 0.55
    return CPU_QB_DUE_DILIGENCE_BONUS * (need_score / 100.0) * confidence_multiplier * rank_multiplier * pick_multiplier


def cpu_visible_scouting_candidates(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    draft_class_id: int,
    team_id: int,
    limit: int = 24,
    board_rank_min: int | None = None,
    board_rank_max: int | None = None,
) -> list[sqlite3.Row]:
    rank_filters = []
    params: list[Any] = [game_id, draft_year, team_id, draft_class_id]
    if board_rank_min is not None:
        rank_filters.append("COALESCE(dp.public_board_rank, dp.scouting_rank, 9999) >= ?")
        params.append(int(board_rank_min))
    if board_rank_max is not None:
        rank_filters.append("COALESCE(dp.public_board_rank, dp.scouting_rank, 9999) <= ?")
        params.append(int(board_rank_max))
    rank_clause = f"AND {' AND '.join(rank_filters)}" if rank_filters else ""
    params.append(limit)
    return con.execute(
        f"""
        SELECT
            dp.*,
            COALESCE(csp.scouting_level, 15) AS cpu_scouting_level,
            COALESCE(csp.scouting_confidence, 'Low') AS cpu_scouting_confidence,
            COALESCE(csp.times_scouted, 0) AS cpu_times_scouted,
            CASE
              WHEN COALESCE(csp.visibility_status, '') = 'discovered'
                   OR COALESCE(dp.discovery_status, '') = 'discovered'
              THEN 'discovered'
              ELSE csp.visibility_status
            END AS cpu_visibility_status
        FROM draft_prospects dp
        LEFT JOIN cpu_scouting_prospect_progress csp
          ON csp.prospect_id = dp.prospect_id
         AND csp.game_id = ?
         AND csp.draft_year = ?
         AND csp.team_id = ?
        WHERE dp.draft_class_id = ?
          AND dp.status = 'Available'
          AND (
                COALESCE(dp.public_board_status, 'public_board') <> 'off_public_board'
                OR csp.visibility_status = 'discovered'
                OR COALESCE(dp.discovery_status, '') = 'discovered'
              )
          AND COALESCE(csp.scouting_confidence, 'Low') <> 'Very High'
          {rank_clause}
        ORDER BY
          CASE COALESCE(csp.scouting_confidence, 'Low')
            WHEN 'Low' THEN 0
            WHEN 'Medium' THEN 1
            WHEN 'High' THEN 2
            ELSE 3
          END,
          CASE WHEN COALESCE(dp.public_board_rank, dp.scouting_rank) IS NULL THEN 999 ELSE COALESCE(dp.public_board_rank, dp.scouting_rank) END
        LIMIT ?
        """,
        params,
    ).fetchall()


def cpu_weekly_scouting_bucket_plan(
    *,
    pick_profile: dict[str, Any],
    count: int,
    rng: random.Random,
) -> list[str]:
    count = max(1, int(count))
    earliest = pick_profile.get("earliest_round")
    has_first = bool(pick_profile.get("has_first_round_pick"))
    if has_first:
        early_count = min(count - 1, max(1, round(count * 0.60))) if count > 1 else 1
        plan = ["early"] * early_count
        while len(plan) < count:
            plan.append("day2" if len(plan) % 2 else "day3")
    elif earliest == 2:
        plan = ["day2", "early", "day3"]
    elif earliest == 3:
        plan = ["day2", "day3", "day3"]
    else:
        plan = ["day3", "day2", "day3"]
    while len(plan) < count:
        plan.append(rng.choice(["day2", "day3"]))
    return plan[:count]


def cpu_scouting_candidate_score(
    prospect: sqlite3.Row,
    *,
    con: sqlite3.Connection,
    team_id: int,
    need_scores: dict[str, float],
    pick_profile: dict[str, Any],
    game_id: str,
    draft_year: int,
    rng: random.Random,
) -> tuple[float, int, int]:
    rank = int(prospect["public_board_rank"] or prospect["scouting_rank"] or 9999)
    confidence = normalize_confidence(prospect["cpu_scouting_confidence"])
    confidence_bonus = {"Low": 18.0, "Medium": 10.0, "High": 3.5, "Very High": 0.0}.get(confidence, 8.0)
    grade = scouting_perception.perceived_grade(
        prospect,
        game_id=game_id,
        draft_year=draft_year,
        team_id=team_id,
    )
    ceiling = scouting_perception.perceived_ceiling(
        prospect,
        game_id=game_id,
        draft_year=draft_year,
        team_id=team_id,
    )
    need_bonus = cpu_scouting_need_bonus(prospect, need_scores=need_scores, pick_profile=pick_profile)
    due_diligence_bonus = cpu_first_round_due_diligence_bonus(
        prospect,
        need_scores=need_scores,
        pick_profile=pick_profile,
    )
    qb_priority_bonus = cpu_qb_scouting_priority_bonus(
        prospect,
        need_scores=need_scores,
        pick_profile=pick_profile,
    )
    position_bonus = cpu_position_need_bonus(con, team_id, str(prospect["position"]))
    upside_bonus = max(0.0, ceiling - grade) / 3.0
    variance_bonus = float(prospect["scouting_variance"] or 0.0) / 6.0
    rank_bonus = max(0.0, 12.0 - (rank / 28.0))
    jitter = rng.random() * 1.5
    score = confidence_bonus + need_bonus + due_diligence_bonus + qb_priority_bonus + position_bonus + ((grade - 55.0) / 2.0) + upside_bonus + variance_bonus + rank_bonus + jitter
    return (-score, rank, int(prospect["prospect_id"]))


def select_cpu_weekly_scouting_prospects(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    draft_class_id: int,
    team_id: int,
    need_scores: dict[str, float],
    pick_profile: dict[str, Any],
    count: int,
    rng: random.Random,
) -> list[sqlite3.Row]:
    selected: list[sqlite3.Row] = []
    selected_ids: set[int] = set()
    qb_need = need_scores.get("QB", 0.0)
    if qb_need >= CPU_QB_SCOUTING_NEED_FLOOR and count > 0:
        qb_limit = 96 if pick_profile.get("has_first_round_pick") or qb_need >= CPU_QB_SCOUTING_STRONG_NEED else 160
        qb_candidates = [
            row
            for row in cpu_visible_scouting_candidates(
                con,
                game_id=game_id,
                draft_year=draft_year,
                draft_class_id=draft_class_id,
                team_id=team_id,
                limit=40,
                board_rank_min=1,
                board_rank_max=qb_limit,
            )
            if str(row["position"] or "").upper() == "QB"
        ]
        qb_candidates.sort(
            key=lambda row: cpu_scouting_candidate_score(
                row,
                con=con,
                team_id=team_id,
                need_scores=need_scores,
                pick_profile=pick_profile,
                game_id=game_id,
                draft_year=draft_year,
                rng=rng,
            )
        )
        qb_slots = 2 if qb_need >= CPU_QB_SCOUTING_STRONG_NEED and count >= 4 else 1
        for prospect in qb_candidates[:qb_slots]:
            selected.append(prospect)
            selected_ids.add(int(prospect["prospect_id"]))
    plan = cpu_weekly_scouting_bucket_plan(pick_profile=pick_profile, count=count, rng=rng)
    for bucket in plan:
        board_min, board_max = CPU_SCOUTING_BUCKETS[bucket]
        candidates = cpu_visible_scouting_candidates(
            con,
            game_id=game_id,
            draft_year=draft_year,
            draft_class_id=draft_class_id,
            team_id=team_id,
            limit=36,
            board_rank_min=board_min,
            board_rank_max=board_max,
        )
        candidates = [row for row in candidates if int(row["prospect_id"]) not in selected_ids]
        if not candidates:
            continue
        candidates.sort(
            key=lambda row: cpu_scouting_candidate_score(
                row,
                con=con,
                team_id=team_id,
                need_scores=need_scores,
                pick_profile=pick_profile,
                game_id=game_id,
                draft_year=draft_year,
                rng=rng,
            )
        )
        selected.append(candidates[0])
        selected_ids.add(int(candidates[0]["prospect_id"]))
    if len(selected) >= count:
        return selected[:count]

    fallback = cpu_visible_scouting_candidates(
        con,
        game_id=game_id,
        draft_year=draft_year,
        draft_class_id=draft_class_id,
        team_id=team_id,
        limit=max(48, count * 16),
    )
    fallback = [row for row in fallback if int(row["prospect_id"]) not in selected_ids]
    fallback.sort(
        key=lambda row: cpu_scouting_candidate_score(
            row,
            con=con,
            team_id=team_id,
            need_scores=need_scores,
            pick_profile=pick_profile,
            game_id=game_id,
            draft_year=draft_year,
            rng=rng,
        )
    )
    for prospect in fallback:
        if len(selected) >= count:
            break
        selected.append(prospect)
        selected_ids.add(int(prospect["prospect_id"]))
    return selected


def advance_cpu_prospect_one_tier(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    team_id: int,
    period: ScoutingPeriod,
    prospect: sqlite3.Row,
    reason: str,
) -> dict[str, Any]:
    current = con.execute(
        """
        SELECT *
        FROM cpu_scouting_prospect_progress
        WHERE game_id = ?
          AND draft_year = ?
          AND team_id = ?
          AND prospect_id = ?
        """,
        (game_id, draft_year, team_id, int(prospect["prospect_id"])),
    ).fetchone()
    old_confidence = normalize_confidence(current["scouting_confidence"] if current else "Low")
    new_confidence = next_confidence(old_confidence)
    old_level = int(current["scouting_level"] or CONFIDENCE_TARGET_LEVELS[old_confidence]) if current else 15
    new_level = max(old_level, CONFIDENCE_TARGET_LEVELS[new_confidence])
    visibility = "discovered" if str(prospect["public_board_status"] or "") == "off_public_board" else "known"
    if current and str(current["visibility_status"] or "") not in {"", "hidden"}:
        visibility = str(current["visibility_status"])
    report = simple_scouting_report(prospect, old_confidence, new_confidence, reason)
    con.execute(
        """
        INSERT INTO cpu_scouting_prospect_progress (
            game_id, draft_year, team_id, prospect_id, visibility_status,
            scouting_level, scouting_confidence, times_scouted,
            last_scouted_season, last_scouted_week, last_scouted_date,
            last_report, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(game_id, draft_year, team_id, prospect_id) DO UPDATE SET
            visibility_status = CASE
                WHEN cpu_scouting_prospect_progress.visibility_status = 'hidden' THEN excluded.visibility_status
                ELSE cpu_scouting_prospect_progress.visibility_status
            END,
            scouting_level = excluded.scouting_level,
            scouting_confidence = excluded.scouting_confidence,
            times_scouted = cpu_scouting_prospect_progress.times_scouted + 1,
            last_scouted_season = excluded.last_scouted_season,
            last_scouted_week = excluded.last_scouted_week,
            last_scouted_date = excluded.last_scouted_date,
            last_report = excluded.last_report,
            updated_at = datetime('now')
        """,
        (
            game_id,
            draft_year,
            team_id,
            int(prospect["prospect_id"]),
            visibility,
            new_level,
            new_confidence,
            period.season,
            period.week,
            period.date,
            report,
        ),
    )
    return {
        "prospect_id": int(prospect["prospect_id"]),
        "old_confidence": old_confidence,
        "new_confidence": new_confidence,
        "scouting_level": new_level,
    }


def mark_cpu_prospect_personality_known(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    team_id: int,
    period: ScoutingPeriod,
    prospect: sqlite3.Row,
    notes: str,
) -> None:
    current = con.execute(
        """
        SELECT *
        FROM cpu_scouting_prospect_progress
        WHERE game_id = ?
          AND draft_year = ?
          AND team_id = ?
          AND prospect_id = ?
        """,
        (game_id, draft_year, team_id, int(prospect["prospect_id"])),
    ).fetchone()
    old_confidence = normalize_confidence(current["scouting_confidence"] if current else "Low")
    old_level = int(current["scouting_level"] or CONFIDENCE_TARGET_LEVELS[old_confidence]) if current else 15
    visibility = "discovered" if str(prospect["public_board_status"] or "") == "off_public_board" else "known"
    if current and str(current["visibility_status"] or "") not in {"", "hidden"}:
        visibility = str(current["visibility_status"])
    con.execute(
        """
        INSERT INTO cpu_scouting_prospect_progress (
            game_id, draft_year, team_id, prospect_id, visibility_status,
            scouting_level, scouting_confidence, times_scouted,
            last_scouted_season, last_scouted_week, last_scouted_date,
            last_report, personality_known, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 1, datetime('now'))
        ON CONFLICT(game_id, draft_year, team_id, prospect_id) DO UPDATE SET
            visibility_status = CASE
                WHEN cpu_scouting_prospect_progress.visibility_status = 'hidden' THEN excluded.visibility_status
                ELSE cpu_scouting_prospect_progress.visibility_status
            END,
            times_scouted = cpu_scouting_prospect_progress.times_scouted + 1,
            personality_known = 1,
            last_scouted_season = excluded.last_scouted_season,
            last_scouted_week = excluded.last_scouted_week,
            last_scouted_date = excluded.last_scouted_date,
            last_report = excluded.last_report,
            updated_at = datetime('now')
        """,
        (
            game_id,
            draft_year,
            team_id,
            int(prospect["prospect_id"]),
            visibility,
            old_level,
            old_confidence,
            period.season,
            period.week,
            period.date,
            notes,
        ),
    )


def discover_cpu_hidden_prospects(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    draft_class_id: int,
    team_id: int,
    period: ScoutingPeriod,
    rng: random.Random,
) -> int:
    chance = 0.08 if period.week <= 10 else 0.12 if period.week <= 15 else 0.18
    discovery_count = 0
    if rng.random() <= chance:
        discovery_count += 1
    if rng.random() <= CPU_EXTRA_HIDDEN_DISCOVERY_CHANCE:
        discovery_count += 1
    if discovery_count <= 0:
        return 0
    candidates = con.execute(
        """
        SELECT
            dp.*,
            (
                SELECT COUNT(DISTINCT csp2.team_id)
                FROM cpu_scouting_prospect_progress csp2
                WHERE csp2.game_id = ?
                  AND csp2.draft_year = ?
                  AND csp2.prospect_id = dp.prospect_id
                  AND csp2.visibility_status = 'discovered'
            )
            +
            (
                SELECT COUNT(*)
                FROM scouting_prospect_progress spp2
                WHERE spp2.game_id = ?
                  AND spp2.draft_year = ?
                  AND spp2.prospect_id = dp.prospect_id
                  AND spp2.visibility_status = 'discovered'
            ) AS discovered_elsewhere_count
        FROM draft_prospects dp
        LEFT JOIN cpu_scouting_prospect_progress csp
          ON csp.prospect_id = dp.prospect_id
         AND csp.game_id = ?
         AND csp.draft_year = ?
         AND csp.team_id = ?
        WHERE dp.draft_class_id = ?
          AND dp.status = 'Available'
          AND COALESCE(dp.public_board_status, '') = 'off_public_board'
          AND csp.prospect_id IS NULL
        """,
        (
            game_id,
            draft_year,
            game_id,
            draft_year,
            game_id,
            draft_year,
            team_id,
            draft_class_id,
        ),
    ).fetchall()
    if not candidates:
        return 0
    created = 0
    for candidate in choose_hidden_discovery_candidates(candidates, count=discovery_count, rng=rng):
        level = DISCOVERY_START_LEVEL
        confidence = DISCOVERY_START_CONFIDENCE
        con.execute(
            """
            INSERT INTO cpu_scouting_prospect_progress (
                game_id, draft_year, team_id, prospect_id, visibility_status,
                scouting_level, scouting_confidence, times_scouted,
                last_scouted_season, last_scouted_week, last_scouted_date,
                last_report, updated_at
            )
            VALUES (?, ?, ?, ?, 'discovered', ?, ?, 0, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(game_id, draft_year, team_id, prospect_id) DO NOTHING
            """,
            (
                game_id,
                draft_year,
                team_id,
                int(candidate["prospect_id"]),
                level,
                confidence,
                period.season,
                period.week,
                period.date,
                "CPU area scout discovery. Hidden from the user board unless the user also discovers him.",
            ),
        )
        created += int(con.execute("SELECT changes()").fetchone()[0] or 0)
    return created


def run_cpu_weekly_scouting(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    draft_year: int | None = None,
    season: int | None = None,
    week: int | None = None,
    count_per_team: int = CPU_WEEKLY_SCOUTING_COUNT,
    seed: str | None = None,
) -> dict[str, Any]:
    ensure_schema(con)
    target_game_id = active_game_id(con, game_id)
    class_row = draft_class_row(con, draft_year)
    if not class_row:
        return {"status": "skipped", "teams": 0, "advanced": 0, "discoveries": 0, "reason": "No draft class found."}
    target_year = int(class_row["draft_year"])
    draft_class_id = int(class_row["draft_class_id"])
    period = assignment_period(con, season, week)
    window = weekly_scouting_window_status(con, period)
    if not window["open"]:
        return {
            "status": "skipped",
            "teams": 0,
            "advanced": 0,
            "discoveries": 0,
            "reason": window["reason"] or "CPU weekly scouting is outside the active window.",
        }
    teams = cpu_team_rows(con, exclude_user=True)
    total_advanced = 0
    total_discoveries = 0
    for team in teams:
        team_id = int(team["team_id"])
        team_abbr = str(team["abbreviation"])
        pick_profile = cpu_draft_pick_profile(con, team_id, target_year)
        need_scores = cpu_team_need_scores(
            con,
            team_abbr=team_abbr,
            season=period.season,
            game_id=target_game_id,
            evaluation_date=period.date,
        )
        rng = random.Random(seed or f"{target_game_id}:{target_year}:{period.season}:{period.week}:{team_id}:cpu-scouting")
        selected = select_cpu_weekly_scouting_prospects(
            con,
            game_id=target_game_id,
            draft_year=target_year,
            draft_class_id=draft_class_id,
            team_id=team_id,
            need_scores=need_scores,
            pick_profile=pick_profile,
            count=max(1, count_per_team),
            rng=rng,
        )
        for prospect in selected:
            advance_cpu_prospect_one_tier(
                con,
                game_id=target_game_id,
                draft_year=target_year,
                team_id=team_id,
                period=period,
                prospect=prospect,
                reason=f"{team_abbr} scouts sharpened",
            )
        total_advanced += len(selected)
        total_discoveries += discover_cpu_hidden_prospects(
            con,
            game_id=target_game_id,
            draft_year=target_year,
            draft_class_id=draft_class_id,
            team_id=team_id,
            period=period,
            rng=rng,
        )
    return {
        "status": "processed",
        "teams": len(teams),
        "advanced": total_advanced,
        "discoveries": total_discoveries,
        "period": period.label,
    }


def process_assignments(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    draft_year: int | None = None,
    season: int | None = None,
    week: int | None = None,
    slots: int = 8,
    seed: str | None = None,
) -> dict[str, int]:
    ensure_schema(con)
    target_game_id = active_game_id(con, game_id)
    class_row = draft_class_row(con, draft_year)
    if not class_row:
        return {"processed": 0, "discovered": 0, "pending_remaining": 0}
    target_year = int(class_row["draft_year"])
    initialize_for_game(con, game_id=target_game_id, draft_year=target_year, welcome_message=False)
    period = assignment_period(con, season, week)
    rng = random.Random(seed or f"{target_game_id}:{target_year}:{period.season}:{period.week}:scouting")
    pending = con.execute(
        """
        SELECT sa.*, dp.*
        FROM scouting_assignments sa
        JOIN draft_prospects dp ON dp.prospect_id = sa.prospect_id
        WHERE sa.game_id = ?
          AND sa.draft_year = ?
          AND sa.season = ?
          AND sa.week = ?
          AND sa.status = 'pending'
        ORDER BY sa.assignment_id
        LIMIT ?
        """,
        (target_game_id, target_year, period.season, period.week, max(0, slots)),
    ).fetchall()
    processed = 0
    processed_uses = 0
    for assignment in pending:
        focus = str(assignment["focus"] or "film")
        base_gain = {
            "film": (8, 14),
            "game": (10, 16),
            "personality": (6, 11),
            "medical": (5, 10),
            "workout": (7, 13),
        }.get(focus, (8, 14))
        gain = rng.randint(*base_gain)
        current = con.execute(
            """
            SELECT *
            FROM scouting_prospect_progress
            WHERE game_id = ?
              AND draft_year = ?
              AND prospect_id = ?
            """,
            (target_game_id, target_year, int(assignment["prospect_id"])),
        ).fetchone()
        old_level = int(current["scouting_level"] or 15) if current else 15
        new_level = max(old_level, min(95, old_level + gain))
        confidence = confidence_for_level(new_level)

        personality_text = None
        personality_known = int(current["personality_known"] or 0) if current else 0
        if (focus == "personality" or rng.random() < 0.18) and not personality_known:
            trait = con.execute(
                """
                SELECT trait_key, intensity
                FROM draft_prospect_personalities
                WHERE prospect_id = ?
                ORDER BY intensity DESC
                LIMIT 1
                """,
                (int(assignment["prospect_id"]),),
            ).fetchone()
            if trait:
                personality_known = 1
                personality_text = f"{trait_display(con, str(trait['trait_key']))} tendency ({int(trait['intensity'])}/100)"

        note = scouting_note(assignment, focus, gain, personality_text)
        con.execute(
            """
            INSERT INTO scouting_prospect_progress (
                game_id, draft_year, prospect_id, visibility_status, scouting_level,
                scouting_confidence, times_scouted, personality_known,
                last_scouted_season, last_scouted_week, last_scouted_date,
                last_report, updated_at
            )
            VALUES (?, ?, ?, 'known', ?, ?, 1, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(game_id, draft_year, prospect_id) DO UPDATE SET
                visibility_status = CASE
                    WHEN visibility_status = 'hidden' THEN 'discovered'
                    ELSE visibility_status
                END,
                scouting_level = excluded.scouting_level,
                scouting_confidence = excluded.scouting_confidence,
                times_scouted = scouting_prospect_progress.times_scouted + 1,
                personality_known = MAX(scouting_prospect_progress.personality_known, excluded.personality_known),
                last_scouted_season = excluded.last_scouted_season,
                last_scouted_week = excluded.last_scouted_week,
                last_scouted_date = excluded.last_scouted_date,
                last_report = excluded.last_report,
                updated_at = datetime('now')
            """,
            (
                target_game_id,
                target_year,
                int(assignment["prospect_id"]),
                new_level,
                confidence,
                personality_known,
                period.season,
                period.week,
                period.date,
                note,
            ),
        )
        con.execute(
            """
            UPDATE draft_prospects
            SET scout_confidence = ?,
                scout_grade = ?,
                scout_ceiling = ?,
                updated_at = datetime('now')
            WHERE prospect_id = ?
            """,
            (
                confidence,
                tighten_displayed_scout_read(
                    assignment["scout_grade"],
                    assignment["true_grade"],
                    confidence,
                    public_board_rank=assignment["public_board_rank"],
                    prospect_id=assignment["prospect_id"],
                    draft_year=target_year,
                ),
                tighten_displayed_scout_read(
                    assignment["scout_ceiling"],
                    assignment["ceiling_grade"],
                    confidence,
                    ceiling=True,
                    public_board_rank=assignment["public_board_rank"],
                    prospect_id=assignment["prospect_id"],
                    draft_year=target_year,
                ),
                int(assignment["prospect_id"]),
            ),
        )
        con.execute(
            """
            UPDATE scouting_assignments
            SET status = 'processed',
                processed_at = datetime('now')
            WHERE assignment_id = ?
            """,
            (int(assignment["assignment_id"]),),
        )
        add_inbox_message(
            con,
            game_id=target_game_id,
            title=f"Scouting Report: {prospect_name(assignment)}",
            body=note,
            category="Scouting",
            priority="normal" if confidence in {"Low", "Medium"} else "high",
            source="College Scouting",
            message_date=period.date,
            related_table="draft_prospects",
            related_id=int(assignment["prospect_id"]),
        )
        processed += 1
        processed_uses += specific_scouting_cost(assignment["position"])

    if processed:
        record_weekly_action(
            con,
            game_id=target_game_id,
            draft_year=target_year,
            period=period,
            action_key="specific",
            description=f"Processed {processed} queued specific scouting assignment(s).",
            uses=processed_uses,
        )

    discovered = discover_hidden_prospects(
        con,
        game_id=target_game_id,
        draft_year=target_year,
        period=period,
        rng=rng,
    )
    remaining = con.execute(
        """
        SELECT COUNT(*) AS c
        FROM scouting_assignments
        WHERE game_id = ?
          AND draft_year = ?
          AND season = ?
          AND week = ?
          AND status = 'pending'
        """,
        (target_game_id, target_year, period.season, period.week),
    ).fetchone()["c"]
    return {"processed": processed, "discovered": discovered, "pending_remaining": int(remaining or 0)}


def discover_user_extra_hidden_prospect(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    draft_class_id: int,
    period: ScoutingPeriod,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Small weekly area-scout chance that runs alongside the user's chosen scouting action."""
    if rng.random() > USER_EXTRA_HIDDEN_DISCOVERY_CHANCE:
        return []
    candidates = con.execute(
        """
        SELECT
            dp.*,
            (
                SELECT COUNT(DISTINCT csp2.team_id)
                FROM cpu_scouting_prospect_progress csp2
                WHERE csp2.game_id = ?
                  AND csp2.draft_year = ?
                  AND csp2.prospect_id = dp.prospect_id
                  AND csp2.visibility_status = 'discovered'
            )
            +
            (
                SELECT COUNT(*)
                FROM scouting_prospect_progress spp2
                WHERE spp2.game_id = ?
                  AND spp2.draft_year = ?
                  AND spp2.prospect_id = dp.prospect_id
                  AND spp2.visibility_status = 'discovered'
            ) AS discovered_elsewhere_count
        FROM draft_prospects dp
        LEFT JOIN scouting_prospect_progress spp
          ON spp.prospect_id = dp.prospect_id
         AND spp.game_id = ?
         AND spp.draft_year = ?
        WHERE dp.draft_class_id = ?
          AND COALESCE(dp.public_board_status, '') = 'off_public_board'
          AND COALESCE(dp.discovery_status, 'undiscovered') = 'undiscovered'
          AND spp.prospect_id IS NULL
        """,
        (
            game_id,
            draft_year,
            game_id,
            draft_year,
            game_id,
            draft_year,
            draft_class_id,
        ),
    ).fetchall()
    selected = choose_hidden_discovery_candidates(candidates, count=1, rng=rng)
    if not selected:
        return []
    candidate = selected[0]

    level = DISCOVERY_START_LEVEL
    confidence = DISCOVERY_START_CONFIDENCE
    report = (
        f"An area scout surfaced {prospect_name(candidate)} ({candidate['position']}, {candidate['college']}) "
        "outside the public board. The early file starts at medium confidence, but still needs follow-up work."
    )
    con.execute(
        """
        INSERT INTO scouting_prospect_progress (
            game_id, draft_year, prospect_id, visibility_status, scouting_level,
            scouting_confidence, times_scouted, last_scouted_season,
            last_scouted_week, last_scouted_date, last_report, updated_at
        )
        VALUES (?, ?, ?, 'discovered', ?, ?, 0, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(game_id, draft_year, prospect_id) DO NOTHING
        """,
        (
            game_id,
            draft_year,
            int(candidate["prospect_id"]),
            level,
            confidence,
            period.season,
            period.week,
            period.date,
            report,
        ),
    )
    if not con.execute("SELECT changes()").fetchone()[0]:
        return []

    con.execute(
        """
        UPDATE draft_prospects
        SET discovery_status = 'discovered',
            scout_confidence = CASE
                WHEN scout_confidence IN ('High', 'Very High') THEN scout_confidence
                ELSE ?
            END,
            updated_at = datetime('now')
        WHERE prospect_id = ?
        """,
        (confidence, int(candidate["prospect_id"])),
    )
    add_inbox_message(
        con,
        game_id=game_id,
        title=f"Area Scout Found: {prospect_name(candidate)}",
        body=report,
        category="Scouting",
        priority="normal",
        source="Area Scout",
        message_date=period.date,
        related_table="draft_prospects",
        related_id=int(candidate["prospect_id"]),
    )
    return [
        {
            "prospect_id": int(candidate["prospect_id"]),
            "name": prospect_name(candidate),
            "position": candidate["position"],
            "college": candidate["college"],
            "old_confidence": "Hidden",
            "new_confidence": confidence,
            "scouting_level": level,
            "source": "background_area_scout",
        }
    ]


def discover_hidden_prospects(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    period: ScoutingPeriod,
    rng: random.Random,
) -> int:
    class_row = draft_class_row(con, draft_year)
    if not class_row:
        return 0
    # Early season produces occasional area-scout names, late season produces a bit more noise.
    chance = 0.42 if period.week <= 4 else 0.58 if period.week <= 12 else 0.72
    if rng.random() > chance:
        return 0
    count = 1 + (1 if rng.random() < 0.18 else 0)
    candidates = con.execute(
        """
        SELECT
            dp.*,
            (
                SELECT COUNT(DISTINCT csp2.team_id)
                FROM cpu_scouting_prospect_progress csp2
                WHERE csp2.game_id = ?
                  AND csp2.draft_year = ?
                  AND csp2.prospect_id = dp.prospect_id
                  AND csp2.visibility_status = 'discovered'
            )
            +
            (
                SELECT COUNT(*)
                FROM scouting_prospect_progress spp2
                WHERE spp2.game_id = ?
                  AND spp2.draft_year = ?
                  AND spp2.prospect_id = dp.prospect_id
                  AND spp2.visibility_status = 'discovered'
            ) AS discovered_elsewhere_count
        FROM draft_prospects dp
        LEFT JOIN scouting_prospect_progress spp
          ON spp.prospect_id = dp.prospect_id
         AND spp.game_id = ?
         AND spp.draft_year = ?
        WHERE dp.draft_class_id = ?
          AND COALESCE(dp.public_board_status, '') = 'off_public_board'
          AND COALESCE(dp.discovery_status, 'undiscovered') = 'undiscovered'
          AND spp.prospect_id IS NULL
        """,
        (
            game_id,
            draft_year,
            game_id,
            draft_year,
            game_id,
            draft_year,
            int(class_row["draft_class_id"]),
        ),
    ).fetchall()
    for prospect in choose_hidden_discovery_candidates(candidates, count=count, rng=rng):
        level = DISCOVERY_START_LEVEL
        confidence = DISCOVERY_START_CONFIDENCE
        con.execute(
            """
            INSERT INTO scouting_prospect_progress (
                game_id, draft_year, prospect_id, visibility_status, scouting_level,
                scouting_confidence, times_scouted, last_scouted_season,
                last_scouted_week, last_scouted_date, last_report
            )
            VALUES (?, ?, ?, 'discovered', ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                game_id,
                draft_year,
                int(prospect["prospect_id"]),
                level,
                confidence,
                period.season,
                period.week,
                period.date,
                "Area scout discovery. Needs follow-up work before the board should trust the grade.",
            ),
        )
        con.execute(
            """
            UPDATE draft_prospects
            SET discovery_status = 'discovered',
                scout_confidence = ?,
                updated_at = datetime('now')
            WHERE prospect_id = ?
            """,
            (confidence, int(prospect["prospect_id"])),
        )
        add_inbox_message(
            con,
            game_id=game_id,
            title=f"New Prospect Found: {prospect_name(prospect)}",
            body=(
                f"An area scout added {prospect_name(prospect)} "
                f"({prospect['position']}, {prospect['college']}) to the watch list. "
                "The report is very light, but there may be enough traits to justify a manual look."
            ),
            category="Scouting",
            priority="normal",
            source="Area Scout",
            message_date=period.date,
            related_table="draft_prospects",
            related_id=int(prospect["prospect_id"]),
        )
    return len(candidates)


def prospect_trait_rows(con: sqlite3.Connection, prospect_id: int) -> list[sqlite3.Row]:
    if not table_exists(con, "draft_prospect_personalities"):
        return []
    return con.execute(
        """
        SELECT
            dpp.trait_key,
            dpp.intensity,
            dpp.assignment_type,
            dpp.hidden,
            dpp.notes,
            ptd.display_name,
            ptd.category,
            ptd.polarity,
            ptd.description
        FROM draft_prospect_personalities dpp
        LEFT JOIN personality_trait_definitions ptd
          ON ptd.trait_key = dpp.trait_key
        WHERE dpp.prospect_id = ?
        ORDER BY dpp.intensity DESC, dpp.trait_key
        """,
        (prospect_id,),
    ).fetchall()


def trait_payload(con: sqlite3.Connection, prospect_id: int) -> list[dict[str, Any]]:
    traits = []
    for row in prospect_trait_rows(con, prospect_id):
        traits.append(
            {
                "traitKey": row["trait_key"],
                "displayName": row["display_name"] or trait_display(con, str(row["trait_key"])),
                "intensity": int(row["intensity"] or 0),
                "category": row["category"],
                "polarity": row["polarity"],
                "notes": row["notes"],
            }
        )
    return traits


def hidden_info_payload(prospect: sqlite3.Row) -> dict[str, Any]:
    return {
        "trueGrade": prospect["true_grade"],
        "ceilingGrade": prospect["ceiling_grade"],
        "devTrait": prospect["dev_trait"],
        "riskLevel": prospect["risk_level"],
        "trueRank": prospect["true_rank"],
        "archetype": prospect["archetype"],
        "primaryRole": prospect["primary_role"],
        "secondaryRole": prospect["secondary_role"],
        "scoutingVariance": prospect["scouting_variance"],
    }


def top30_visit_note(
    prospect: sqlite3.Row,
    *,
    result_type: str,
    traits: list[dict[str, Any]],
    hidden_info: dict[str, Any] | None,
) -> str:
    name = prospect_name(prospect)
    if result_type == "full":
        trait_text = ", ".join(f"{t['displayName']} {t['intensity']}/100" for t in traits) or "no strong personality markers"
        return (
            f"Top 30 visit with {name} produced a full reveal. "
            f"Personality: {trait_text}. "
            f"Hidden eval: true grade {hidden_info.get('trueGrade') if hidden_info else '-'}, "
            f"ceiling {hidden_info.get('ceilingGrade') if hidden_info else '-'}, "
            f"development {hidden_info.get('devTrait') if hidden_info else '-'}, "
            f"risk {hidden_info.get('riskLevel') if hidden_info else '-'}."
        )
    if result_type == "personality":
        trait_text = ", ".join(f"{t['displayName']} {t['intensity']}/100" for t in traits) or "no strong personality markers"
        return f"Top 30 visit with {name} successfully revealed personality traits: {trait_text}."
    return (
        f"Top 30 visit with {name} was inconclusive. The meeting did not reveal reliable hidden traits, "
        "but the staff still logged the interview."
    )


def top30_context(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    draft_year: int | None = None,
) -> tuple[str, sqlite3.Row, int, str]:
    ensure_schema(con)
    target_game_id = active_game_id(con, game_id)
    class_row = draft_class_row(con, draft_year)
    if not class_row:
        raise ValueError("No draft class found for Top 30 visits.")
    target_year = int(class_row["draft_year"])
    initialize_for_game(con, game_id=target_game_id, draft_year=target_year, welcome_message=False)
    return target_game_id, class_row, target_year, user_team_abbr(con)


def draft_has_started(con: sqlite3.Connection, draft_class_id: int) -> bool:
    row = con.execute(
        """
        SELECT COUNT(*) AS c
        FROM draft_prospects
        WHERE draft_class_id = ?
          AND selected_pick_id IS NOT NULL
        """,
        (draft_class_id,),
    ).fetchone()
    return int(row["c"] or 0) > 0


def execute_top30_visit(
    con: sqlite3.Connection,
    *,
    prospect_id: int,
    game_id: str | None = None,
    draft_year: int | None = None,
    seed: str | None = None,
    visit_date: str | None = None,
    allow_after_draft: bool = False,
    confidence_steps: int = 2,
) -> dict[str, Any]:
    target_game_id, class_row, target_year, team_abbr = top30_context(con, game_id=game_id, draft_year=draft_year)
    draft_class_id = int(class_row["draft_class_id"])
    if not allow_after_draft and draft_has_started(con, draft_class_id):
        raise ValueError("Top 30 visits are closed once the draft has started.")
    effective_visit_date = visit_date or current_date(con)
    window = top30_window_status_for_date(con, target_year, effective_visit_date)
    if not window["open"]:
        raise ValueError(window["reason"] or "Top 30 visits are not open on the current game date.")
    used = con.execute(
        """
        SELECT COUNT(*) AS c
        FROM scouting_top30_visits
        WHERE game_id = ?
          AND draft_year = ?
          AND team_abbr = ?
        """,
        (target_game_id, target_year, team_abbr),
    ).fetchone()["c"]
    if int(used or 0) >= 30:
        raise ValueError("All 30 Top 30 visit slots have already been used.")
    existing = con.execute(
        """
        SELECT 1
        FROM scouting_top30_visits
        WHERE game_id = ?
          AND draft_year = ?
          AND team_abbr = ?
          AND prospect_id = ?
        LIMIT 1
        """,
        (target_game_id, target_year, team_abbr, prospect_id),
    ).fetchone()
    if existing:
        raise ValueError("That prospect has already had a Top 30 visit.")

    prospect = con.execute(
        """
        SELECT
            dp.*,
            spp.visibility_status,
            spp.scouting_level AS user_scouting_level,
            spp.scouting_confidence AS user_scouting_confidence
        FROM draft_prospects dp
        JOIN scouting_prospect_progress spp
          ON spp.prospect_id = dp.prospect_id
         AND spp.game_id = ?
         AND spp.draft_year = ?
        WHERE dp.draft_class_id = ?
          AND dp.prospect_id = ?
          AND (
                COALESCE(dp.public_board_status, 'public_board') <> 'off_public_board'
                OR spp.visibility_status = 'discovered'
              )
        """,
        (target_game_id, target_year, draft_class_id, prospect_id),
    ).fetchone()
    if not prospect:
        raise ValueError("That prospect is not visible to your scouting department yet.")
    if prospect["selected_pick_id"] is not None and not allow_after_draft:
        raise ValueError("That prospect has already been selected.")

    rng = random.Random(seed or f"{target_game_id}:{target_year}:{team_abbr}:{prospect_id}:top30")
    roll = rng.random()
    if roll < 0.25:
        result_type = "full"
    elif roll < 0.75:
        result_type = "personality"
    else:
        result_type = "inconclusive"

    traits = trait_payload(con, prospect_id) if result_type in {"personality", "full"} else []
    hidden_info = hidden_info_payload(prospect) if result_type == "full" else None
    notes = top30_visit_note(prospect, result_type=result_type, traits=traits, hidden_info=hidden_info)
    old_confidence = normalize_confidence(prospect["user_scouting_confidence"])
    old_level = int(prospect["user_scouting_level"] or CONFIDENCE_TARGET_LEVELS[old_confidence])
    confidence_steps = max(1, min(2, int(confidence_steps or 1)))
    visit_confidence = advance_confidence(old_confidence, confidence_steps)
    visit_level = max(old_level, CONFIDENCE_TARGET_LEVELS[visit_confidence])
    if result_type == "full":
        visit_confidence = "Very High" if confidence_steps >= 2 else advance_confidence(old_confidence, 2)
        visit_level = max(visit_level, 95)
    con.execute(
        """
        INSERT INTO scouting_top30_visits (
            game_id, draft_year, team_abbr, prospect_id, visit_date, result_type,
            personality_revealed, full_info_revealed, revealed_traits_json,
            revealed_hidden_info_json, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            target_game_id,
            target_year,
            team_abbr,
            prospect_id,
            effective_visit_date,
            result_type,
            1 if result_type in {"personality", "full"} else 0,
            1 if result_type == "full" else 0,
            json.dumps(traits),
            json.dumps(hidden_info) if hidden_info else None,
            notes,
        ),
    )

    con.execute(
        """
        UPDATE scouting_prospect_progress
        SET scouting_level = MAX(scouting_level, ?),
            scouting_confidence = CASE
                WHEN ? >= scouting_level THEN ?
                ELSE scouting_confidence
            END,
            personality_known = CASE
                WHEN ? THEN 1
                ELSE personality_known
            END,
            last_report = ?,
            last_scouted_season = ?,
            last_scouted_week = ?,
            last_scouted_date = ?,
            updated_at = datetime('now')
        WHERE game_id = ?
          AND draft_year = ?
          AND prospect_id = ?
        """,
        (
            visit_level,
            visit_level,
            visit_confidence,
            1 if result_type in {"personality", "full"} else 0,
            notes,
            current_season(con),
            current_scouting_period(con).week,
            effective_visit_date,
            target_game_id,
            target_year,
            prospect_id,
        ),
    )
    con.execute(
        """
        UPDATE draft_prospects
        SET scout_confidence = ?,
            scout_grade = ?,
            scout_ceiling = ?,
            updated_at = datetime('now')
        WHERE prospect_id = ?
        """,
        (
            visit_confidence,
            tighten_displayed_scout_read(
                prospect["scout_grade"],
                prospect["true_grade"],
                visit_confidence,
                public_board_rank=prospect["public_board_rank"],
                prospect_id=prospect["prospect_id"],
                draft_year=target_year,
            ),
            tighten_displayed_scout_read(
                prospect["scout_ceiling"],
                prospect["ceiling_grade"],
                visit_confidence,
                ceiling=True,
                public_board_rank=prospect["public_board_rank"],
                prospect_id=prospect["prospect_id"],
                draft_year=target_year,
            ),
            prospect_id,
        ),
    )

    add_inbox_message(
        con,
        game_id=target_game_id,
        title=f"Top 30 Visit: {prospect_name(prospect)}",
        body=notes,
        category="Scouting",
        priority="high" if result_type == "full" else "normal",
        source="College Scouting",
        message_date=effective_visit_date,
        related_table="draft_prospects",
        related_id=prospect_id,
    )
    return {
        "prospect_id": prospect_id,
        "name": prospect_name(prospect),
        "position": prospect["position"],
        "college": prospect["college"],
        "result_type": result_type,
        "notes": notes,
        "traits": traits,
        "hiddenInfo": hidden_info,
        "used": int(used or 0) + 1,
        "remaining": max(0, 30 - (int(used or 0) + 1)),
    }


def auto_assign_top30_visits(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    draft_year: int | None = None,
    target_count: int = 30,
    seed: str | None = None,
    visit_date: str | None = None,
) -> dict[str, Any]:
    target_game_id, class_row, target_year, team_abbr = top30_context(con, game_id=game_id, draft_year=draft_year)
    draft_class_id = int(class_row["draft_class_id"])
    if draft_has_started(con, draft_class_id):
        return {
            "status": "skipped",
            "reason": "Draft has already started.",
            "draft_year": target_year,
            "team": team_abbr,
            "created": 0,
            "used": 0,
            "remaining": target_count,
            "visits": [],
        }

    window = top30_window_status(con, target_year)
    effective_visit_date = visit_date or current_date(con)
    if visit_date:
        explicit_window = top30_window_status_for_date(con, target_year, visit_date)
        if not explicit_window["open"]:
            raise ValueError(explicit_window["reason"] or "Top 30 visits are not open on the requested visit date.")
    elif not window["open"]:
        raise ValueError(window["reason"] or "Top 30 visits are not open on the current game date.")

    used = int(
        con.execute(
            """
            SELECT COUNT(*) AS c
            FROM scouting_top30_visits
            WHERE game_id = ?
              AND draft_year = ?
              AND team_abbr = ?
            """,
            (target_game_id, target_year, team_abbr),
        ).fetchone()["c"]
        or 0
    )
    remaining = max(0, int(target_count) - used)
    if remaining <= 0:
        return {
            "status": "complete",
            "reason": "All Top 30 visit slots are already used.",
            "draft_year": target_year,
            "team": team_abbr,
            "created": 0,
            "used": used,
            "remaining": 0,
            "visits": [],
        }

    candidates = con.execute(
        """
        SELECT dp.*, spp.scouting_level, spp.scouting_confidence, spp.visibility_status
        FROM draft_prospects dp
        JOIN scouting_prospect_progress spp
          ON spp.prospect_id = dp.prospect_id
         AND spp.game_id = ?
         AND spp.draft_year = ?
        LEFT JOIN scouting_top30_visits stv
          ON stv.prospect_id = dp.prospect_id
         AND stv.game_id = ?
         AND stv.draft_year = ?
         AND stv.team_abbr = ?
        WHERE dp.draft_class_id = ?
          AND dp.selected_pick_id IS NULL
          AND stv.visit_id IS NULL
          AND (
                COALESCE(dp.public_board_status, 'public_board') <> 'off_public_board'
                OR spp.visibility_status = 'discovered'
              )
        ORDER BY
          CASE COALESCE(spp.scouting_confidence, 'Low')
            WHEN 'Low' THEN 0
            WHEN 'Medium' THEN 1
            WHEN 'High' THEN 2
            ELSE 3
          END,
          CASE WHEN dp.public_board_rank IS NULL THEN 999 ELSE dp.public_board_rank END,
          dp.scout_grade DESC,
          dp.scouting_variance DESC,
          dp.scout_ceiling DESC
        LIMIT ?
        """,
        (
            target_game_id,
            target_year,
            target_game_id,
            target_year,
            team_abbr,
            draft_class_id,
            max(remaining * 4, remaining),
        ),
    ).fetchall()
    if not candidates:
        return {
            "status": "skipped",
            "reason": "No visible unvisited prospects remain.",
            "draft_year": target_year,
            "team": team_abbr,
            "created": 0,
            "used": used,
            "remaining": remaining,
            "visits": [],
        }

    rng = random.Random(seed or f"{target_game_id}:{target_year}:{team_abbr}:auto-top30:{used}")
    team_id = user_team_id(con)
    pick_profile = cpu_draft_pick_profile(con, team_id, target_year) if team_id else {}
    need_scores = user_team_need_scores(
        con,
        game_id=target_game_id,
        season=current_season(con),
        evaluation_date=effective_visit_date,
    )
    existing_bucket_counts = {
        "first": 0,
        "late_early": 0,
        "day2": 0,
        "day3": 0,
    }
    for row in con.execute(
        """
        SELECT COALESCE(dp.public_board_rank, dp.scouting_rank, 9999) AS rank
        FROM scouting_top30_visits stv
        JOIN draft_prospects dp ON dp.prospect_id = stv.prospect_id
        WHERE stv.game_id = ?
          AND stv.draft_year = ?
          AND stv.team_abbr = ?
        """,
        (target_game_id, target_year, team_abbr),
    ).fetchall():
        rank = int(row["rank"] or 9999)
        if rank <= 32:
            existing_bucket_counts["first"] += 1
        elif rank <= 64:
            existing_bucket_counts["late_early"] += 1
        elif rank <= 112:
            existing_bucket_counts["day2"] += 1
        elif rank <= 220:
            existing_bucket_counts["day3"] += 1

    def visit_bucket(prospect: sqlite3.Row) -> str:
        rank = board_rank(prospect, 9999)
        if rank <= 32:
            return "first"
        if rank <= 64:
            return "late_early"
        if rank <= 112:
            return "day2"
        return "day3"

    bucket_caps = {
        "first": USER_AUTO_TOP30_FIRST_ROUND_CAP,
        "late_early": USER_AUTO_TOP30_LATE_EARLY_CAP,
        "day2": USER_AUTO_TOP30_DAY2_CAP,
        "day3": USER_AUTO_TOP30_DAY3_CAP,
    }

    def prospect_visit_weight(prospect: sqlite3.Row, index: int) -> float:
        rank = int(prospect["public_board_rank"] or 260)
        grade = float(prospect["scout_grade"] or 55)
        variance = float(prospect["scouting_variance"] or 0)
        confidence = normalize_confidence(prospect["scouting_confidence"])
        confidence_bonus = {"Low": 12.0, "Medium": 8.0, "High": 3.0, "Very High": 0.5}.get(confidence, 6.0)
        rank_bonus = max(0.5, 40.0 / max(1, rank))
        strategic_score = user_auto_scouting_score(
            prospect,
            need_scores=need_scores,
            pick_profile=pick_profile,
        )
        need_score = need_scores.get(prospect_position_group(prospect), 0.0)
        if need_score < 20.0 and rank > 96:
            confidence_bonus *= 0.45
        if visit_bucket(prospect) == "first" and need_score < 34.0:
            strategic_score *= 0.72
        return max(
            0.1,
            confidence_bonus
            + rank_bonus
            + strategic_score / 8.0
            + (grade - 55.0) / 10.0
            + variance / 14.0
            - index * 0.01,
        )

    pool = list(candidates)
    selected: list[sqlite3.Row] = []

    def add_weighted_from(bucket_name: str, limit: int) -> None:
        nonlocal pool
        if limit <= 0:
            return
        while pool and len(selected) < remaining and limit > 0:
            bucket_pool = [row for row in pool if visit_bucket(row) == bucket_name]
            if not bucket_pool:
                return
            weighted = [prospect_visit_weight(row, index) for index, row in enumerate(bucket_pool)]
            choice = rng.choices(bucket_pool, weights=weighted, k=1)[0]
            selected.append(choice)
            pool.remove(choice)
            limit -= 1

    for bucket_name in ("first", "late_early", "day2", "day3"):
        add_weighted_from(
            bucket_name,
            max(0, bucket_caps[bucket_name] - existing_bucket_counts.get(bucket_name, 0)),
        )

    while pool and len(selected) < remaining:
        first_count = existing_bucket_counts["first"] + sum(1 for row in selected if visit_bucket(row) == "first")
        eligible_pool = [
            row
            for row in pool
            if visit_bucket(row) != "first" or first_count < USER_AUTO_TOP30_FIRST_ROUND_CAP
        ]
        if not eligible_pool:
            break
        weighted = [prospect_visit_weight(row, index) for index, row in enumerate(eligible_pool)]
        choice = rng.choices(eligible_pool, weights=weighted, k=1)[0]
        selected.append(choice)
        pool.remove(choice)

    visits = []
    for prospect in selected:
        visits.append(
            execute_top30_visit(
                con,
                prospect_id=int(prospect["prospect_id"]),
                game_id=target_game_id,
                draft_year=target_year,
                seed=f"{seed or target_game_id}:{target_year}:{int(prospect['prospect_id'])}:auto-top30",
                visit_date=effective_visit_date,
                confidence_steps=1,
            )
        )

    if visits:
        names = ", ".join(visit["name"] for visit in visits[:8])
        if len(visits) > 8:
            names += f", and {len(visits) - 8} more"
        add_inbox_message(
            con,
            game_id=target_game_id,
            title="Staff Filled Remaining Top 30 Visits",
            body=(
                f"Your scouting staff automatically scheduled {len(visits)} remaining Top 30 visit(s) "
                f"before the facility-visit deadline. Prospects included: {names}."
            ),
            category="Scouting",
            priority="normal",
            source="College Scouting",
            message_date=effective_visit_date,
        )

    final_used = used + len(visits)
    return {
        "status": "processed",
        "reason": None,
        "draft_year": target_year,
        "team": team_abbr,
        "created": len(visits),
        "used": final_used,
        "remaining": max(0, int(target_count) - final_used),
        "visits": visits,
    }


def cpu_top30_candidate_rows(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    draft_class_id: int,
    team_id: int,
    team_abbr: str,
    limit: int,
) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT
            dp.*,
            COALESCE(csp.scouting_level, 15) AS cpu_scouting_level,
            COALESCE(csp.scouting_confidence, 'Low') AS cpu_scouting_confidence,
            COALESCE(csp.times_scouted, 0) AS cpu_times_scouted,
            COALESCE(csp.personality_known, 0) AS cpu_personality_known,
            COALESCE(csp.top30_full_info_known, 0) AS cpu_top30_full_info_known,
            CASE
              WHEN COALESCE(csp.visibility_status, '') = 'discovered'
                   OR COALESCE(dp.discovery_status, '') = 'discovered'
              THEN 'discovered'
              ELSE csp.visibility_status
            END AS cpu_visibility_status
        FROM draft_prospects dp
        LEFT JOIN cpu_scouting_prospect_progress csp
          ON csp.prospect_id = dp.prospect_id
         AND csp.game_id = ?
         AND csp.draft_year = ?
         AND csp.team_id = ?
        LEFT JOIN scouting_top30_visits stv
          ON stv.prospect_id = dp.prospect_id
         AND stv.game_id = ?
         AND stv.draft_year = ?
         AND stv.team_abbr = ?
        WHERE dp.draft_class_id = ?
          AND dp.status = 'Available'
          AND dp.selected_pick_id IS NULL
          AND stv.visit_id IS NULL
          AND (
                COALESCE(dp.public_board_status, 'public_board') <> 'off_public_board'
                OR csp.visibility_status = 'discovered'
                OR COALESCE(dp.discovery_status, '') = 'discovered'
              )
        ORDER BY
          CASE COALESCE(csp.scouting_confidence, 'Low')
            WHEN 'Low' THEN 0
            WHEN 'Medium' THEN 1
            WHEN 'High' THEN 2
            ELSE 3
          END,
          CASE WHEN dp.public_board_rank IS NULL THEN 999 ELSE dp.public_board_rank END,
          dp.scout_grade DESC,
          dp.scouting_variance DESC,
          dp.prospect_id
        LIMIT ?
        """,
        (game_id, draft_year, team_id, game_id, draft_year, team_abbr, draft_class_id, limit),
    ).fetchall()


def cpu_top30_weight(
    con: sqlite3.Connection,
    team_id: int,
    prospect: sqlite3.Row,
    *,
    game_id: str,
    draft_year: int,
    need_scores: dict[str, float] | None = None,
    pick_profile: dict[str, Any] | None = None,
) -> float:
    rank = int(prospect["public_board_rank"] or prospect["scouting_rank"] or 260)
    grade = scouting_perception.perceived_grade(
        prospect,
        game_id=game_id,
        draft_year=draft_year,
        team_id=team_id,
    )
    ceiling = scouting_perception.perceived_ceiling(
        prospect,
        game_id=game_id,
        draft_year=draft_year,
        team_id=team_id,
    )
    variance = float(prospect["scouting_variance"] or 0)
    confidence = normalize_confidence(prospect["cpu_scouting_confidence"])
    confidence_bonus = {"Low": 12.0, "Medium": 8.0, "High": 3.0, "Very High": 0.4}.get(confidence, 6.0)
    rank_bonus = max(0.5, 42.0 / max(1, rank))
    need_bonus = cpu_position_need_bonus(con, team_id, str(prospect["position"])) / 4.0
    strategic_need_bonus = cpu_scouting_need_bonus(
        prospect,
        need_scores=need_scores or {},
        pick_profile=pick_profile or {},
    ) / 5.0
    due_diligence_bonus = cpu_first_round_due_diligence_bonus(
        prospect,
        need_scores=need_scores or {},
        pick_profile=pick_profile or {},
    ) / 4.0
    qb_priority_bonus = cpu_qb_scouting_priority_bonus(
        prospect,
        need_scores=need_scores or {},
        pick_profile=pick_profile or {},
    ) / 3.0
    hidden_bonus = 3.5 if str(prospect["public_board_status"] or "") == "off_public_board" else 0.0
    upside_bonus = max(0.0, ceiling - grade) / 12.0
    return max(
        0.1,
        confidence_bonus
        + rank_bonus
        + need_bonus
        + strategic_need_bonus
        + due_diligence_bonus
        + qb_priority_bonus
        + hidden_bonus
        + (grade - 55.0) / 8.0
        + upside_bonus
        + variance / 10.0,
    )


def cpu_top30_result(rng: random.Random) -> str:
    roll = rng.random()
    if roll < 0.25:
        return "full"
    if roll < 0.75:
        return "personality"
    return "inconclusive"


def execute_cpu_top30_visit(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    team_id: int,
    team_abbr: str,
    prospect: sqlite3.Row,
    visit_date: str,
    rng: random.Random,
) -> dict[str, Any] | None:
    result_type = cpu_top30_result(rng)
    prospect_id = int(prospect["prospect_id"])
    traits = trait_payload(con, prospect_id) if result_type in {"personality", "full"} else []
    hidden_info = hidden_info_payload(prospect) if result_type == "full" else None
    notes = top30_visit_note(prospect, result_type=result_type, traits=traits, hidden_info=hidden_info)
    con.execute(
        """
        INSERT OR IGNORE INTO scouting_top30_visits (
            game_id, draft_year, team_abbr, prospect_id, visit_date, result_type,
            personality_revealed, full_info_revealed, revealed_traits_json,
            revealed_hidden_info_json, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_id,
            draft_year,
            team_abbr,
            prospect_id,
            visit_date,
            result_type,
            1 if result_type in {"personality", "full"} else 0,
            1 if result_type == "full" else 0,
            json.dumps(traits),
            json.dumps(hidden_info) if hidden_info else None,
            notes,
        ),
    )
    if not con.execute("SELECT changes() AS c").fetchone()["c"]:
        return None

    old_confidence = normalize_confidence(prospect["cpu_scouting_confidence"])
    old_level = int(prospect["cpu_scouting_level"] or CONFIDENCE_TARGET_LEVELS[old_confidence])
    visit_confidence = advance_confidence(old_confidence, 2)
    visit_level = max(old_level, CONFIDENCE_TARGET_LEVELS[visit_confidence])
    if result_type == "full":
        new_level = max(visit_level, 95)
        new_confidence = "Very High"
    else:
        new_level = visit_level
        new_confidence = visit_confidence
    visibility = "discovered" if str(prospect["public_board_status"] or "") == "off_public_board" else "known"
    if str(prospect["cpu_visibility_status"] or "") not in {"", "hidden"}:
        visibility = str(prospect["cpu_visibility_status"])
    con.execute(
        """
        INSERT INTO cpu_scouting_prospect_progress (
            game_id, draft_year, team_id, prospect_id, visibility_status,
            scouting_level, scouting_confidence, times_scouted,
            last_scouted_season, last_scouted_week, last_scouted_date,
            last_report, personality_known, top30_full_info_known, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(game_id, draft_year, team_id, prospect_id) DO UPDATE SET
            visibility_status = CASE
                WHEN cpu_scouting_prospect_progress.visibility_status = 'hidden' THEN excluded.visibility_status
                ELSE cpu_scouting_prospect_progress.visibility_status
            END,
            scouting_level = MAX(cpu_scouting_prospect_progress.scouting_level, excluded.scouting_level),
            scouting_confidence = CASE
                WHEN excluded.scouting_level >= cpu_scouting_prospect_progress.scouting_level THEN excluded.scouting_confidence
                ELSE cpu_scouting_prospect_progress.scouting_confidence
            END,
            times_scouted = cpu_scouting_prospect_progress.times_scouted + 1,
            personality_known = MAX(cpu_scouting_prospect_progress.personality_known, excluded.personality_known),
            top30_full_info_known = MAX(cpu_scouting_prospect_progress.top30_full_info_known, excluded.top30_full_info_known),
            last_scouted_season = excluded.last_scouted_season,
            last_scouted_week = excluded.last_scouted_week,
            last_scouted_date = excluded.last_scouted_date,
            last_report = excluded.last_report,
            updated_at = datetime('now')
        """,
        (
            game_id,
            draft_year,
            team_id,
            prospect_id,
            visibility,
            new_level,
            new_confidence,
            current_season(con),
            current_scouting_period(con).week,
            visit_date,
            notes,
            1 if result_type in {"personality", "full"} else 0,
            1 if result_type == "full" else 0,
        ),
    )
    return {
        "prospect_id": prospect_id,
        "name": prospect_name(prospect),
        "position": prospect["position"],
        "college": prospect["college"],
        "result_type": result_type,
    }


def auto_assign_cpu_top30_visits(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    draft_year: int | None = None,
    target_count: int = 30,
    seed: str | None = None,
    visit_date: str | None = None,
) -> dict[str, Any]:
    ensure_schema(con)
    target_game_id = active_game_id(con, game_id)
    class_row = draft_class_row(con, draft_year)
    if not class_row:
        return {"status": "skipped", "reason": "No draft class found.", "teams": 0, "created": 0}
    target_year = int(class_row["draft_year"])
    draft_class_id = int(class_row["draft_class_id"])
    if draft_has_started(con, draft_class_id):
        return {"status": "skipped", "reason": "Draft has already started.", "teams": 0, "created": 0}
    effective_visit_date = visit_date or current_date(con)
    window = top30_window_status_for_date(con, target_year, effective_visit_date)
    if not window["open"]:
        raise ValueError(window["reason"] or "Top 30 visits are not open on the requested visit date.")

    total_created = 0
    team_count = 0
    full_reveals = 0
    personality_reveals = 0
    sample: list[dict[str, Any]] = []
    for team in cpu_team_rows(con, exclude_user=True):
        team_id = int(team["team_id"])
        team_abbr = str(team["abbreviation"])
        used = int(
            con.execute(
                """
                SELECT COUNT(*) AS c
                FROM scouting_top30_visits
                WHERE game_id = ?
                  AND draft_year = ?
                  AND team_abbr = ?
                """,
                (target_game_id, target_year, team_abbr),
            ).fetchone()["c"]
            or 0
        )
        remaining = max(0, int(target_count) - used)
        if remaining <= 0:
            team_count += 1
            continue
        pick_profile = cpu_draft_pick_profile(con, team_id, target_year)
        need_scores = cpu_team_need_scores(
            con,
            team_abbr=team_abbr,
            season=current_season(con),
            game_id=target_game_id,
            evaluation_date=effective_visit_date,
        )
        first_round_multiplier = cpu_first_round_scouting_multiplier(pick_profile)
        candidate_limit = max(remaining * 4, remaining)
        if first_round_multiplier >= 1.0:
            candidate_limit = max(remaining * 6, 48)
        elif first_round_multiplier >= 0.25:
            candidate_limit = max(remaining * 5, 36)
        candidates = cpu_top30_candidate_rows(
            con,
            game_id=target_game_id,
            draft_year=target_year,
            draft_class_id=draft_class_id,
            team_id=team_id,
            team_abbr=team_abbr,
            limit=candidate_limit,
        )
        if not candidates:
            team_count += 1
            continue
        rng = random.Random(seed or f"{target_game_id}:{target_year}:{team_abbr}:cpu-top30:{used}")
        pool = list(candidates)
        selected: list[sqlite3.Row] = []
        qb_need = need_scores.get("QB", 0.0)
        if qb_need >= CPU_QB_SCOUTING_NEED_FLOOR:
            qb_target_count = min(4 if qb_need >= CPU_QB_SCOUTING_STRONG_NEED else 2, remaining)
            qb_pool = [
                prospect
                for prospect in pool
                if str(prospect["position"] or "").upper() == "QB"
                and int(prospect["public_board_rank"] or prospect["scouting_rank"] or 9999) <= (96 if pick_profile.get("has_first_round_pick") else 160)
            ]
            qb_pool.sort(
                key=lambda prospect: -cpu_top30_weight(
                    con,
                    team_id,
                    prospect,
                    game_id=target_game_id,
                    draft_year=target_year,
                    need_scores=need_scores,
                    pick_profile=pick_profile,
                )
            )
            for prospect in qb_pool[:qb_target_count]:
                selected.append(prospect)
                if prospect in pool:
                    pool.remove(prospect)
        while pool and len(selected) < remaining:
            weights = [
                cpu_top30_weight(
                    con,
                    team_id,
                    prospect,
                    game_id=target_game_id,
                    draft_year=target_year,
                    need_scores=need_scores,
                    pick_profile=pick_profile,
                )
                for prospect in pool
            ]
            choice = rng.choices(pool, weights=weights, k=1)[0]
            selected.append(choice)
            pool.remove(choice)
        for prospect in selected:
            visit = execute_cpu_top30_visit(
                con,
                game_id=target_game_id,
                draft_year=target_year,
                team_id=team_id,
                team_abbr=team_abbr,
                prospect=prospect,
                visit_date=effective_visit_date,
                rng=rng,
            )
            if not visit:
                continue
            total_created += 1
            full_reveals += 1 if visit["result_type"] == "full" else 0
            personality_reveals += 1 if visit["result_type"] in {"personality", "full"} else 0
            if len(sample) < 12:
                sample.append({"team": team_abbr, **visit})
        team_count += 1

    return {
        "status": "processed",
        "reason": None,
        "draft_year": target_year,
        "teams": team_count,
        "created": total_created,
        "target_per_team": target_count,
        "full_reveals": full_reveals,
        "personality_reveals": personality_reveals,
        "sample": sample,
    }


def decode_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def empty_top30_payload() -> dict[str, Any]:
    return {
        "used": 0,
        "remaining": 30,
        "limit": 30,
        "locked": False,
        "lockedReason": None,
        "windowStart": None,
        "windowEnd": None,
        "visits": [],
    }


def empty_senior_bowl_payload() -> dict[str, Any]:
    return {
        "eligible": 0,
        "invited": 0,
        "accepted": 0,
        "eventDate": None,
        "windowStart": None,
        "windowEnd": None,
        "locked": False,
        "lockedReason": None,
        "processed": False,
        "run": None,
        "userReports": [],
    }


def build_top30_payload(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    class_row: sqlite3.Row,
) -> dict[str, Any]:
    ensure_schema(con)
    team_abbr = user_team_abbr(con)
    used = con.execute(
        """
        SELECT COUNT(*) AS c
        FROM scouting_top30_visits
        WHERE game_id = ?
          AND draft_year = ?
          AND team_abbr = ?
        """,
        (game_id, draft_year, team_abbr),
    ).fetchone()["c"]
    draft_started = draft_has_started(con, int(class_row["draft_class_id"]))
    window = top30_window_status(con, draft_year)
    locked = draft_started or not bool(window["open"])
    locked_reason = "Top 30 visits close once the draft starts." if draft_started else window["reason"]
    visits = con.execute(
        """
        SELECT
            stv.*,
            dp.first_name || ' ' || dp.last_name AS player_name,
            dp.position,
            dp.college,
            dp.public_board_rank
        FROM scouting_top30_visits stv
        JOIN draft_prospects dp ON dp.prospect_id = stv.prospect_id
        WHERE stv.game_id = ?
          AND stv.draft_year = ?
          AND stv.team_abbr = ?
        ORDER BY stv.visit_id DESC
        LIMIT 12
        """,
        (game_id, draft_year, team_abbr),
    ).fetchall()
    visit_payload = []
    for row in visits:
        data = dict(row)
        data["revealedTraits"] = decode_json(data.pop("revealed_traits_json", None), [])
        data["revealedHiddenInfo"] = decode_json(data.pop("revealed_hidden_info_json", None), None)
        visit_payload.append(data)
    return {
        "team": team_abbr,
        "used": int(used or 0),
        "remaining": max(0, 30 - int(used or 0)),
        "limit": 30,
        "locked": locked,
        "lockedReason": locked_reason,
        "windowStart": window["start"],
        "windowEnd": window["end"],
        "visits": visit_payload,
    }


def senior_bowl_report_chance(prospect: sqlite3.Row) -> float:
    chance = 0.08
    chance += min(0.04, abs(int(prospect["scouting_variance"] or 0)) / 1000.0)
    if str(prospect["public_board_status"] or "") == "off_public_board":
        chance += 0.03
    if str(prospect["college_tier"] or "").lower() in {"small", "fcs", "group of 5"}:
        chance += 0.025
    if int(prospect["scout_grade"] or 0) >= 72:
        chance += 0.015
    return max(0.04, min(0.18, chance))


def senior_bowl_context(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    draft_year: int | None = None,
) -> tuple[str, sqlite3.Row, int, str]:
    ensure_schema(con)
    target_game_id = active_game_id(con, game_id)
    class_row = draft_class_row(con, draft_year)
    if not class_row:
        raise ValueError("No draft class found for Senior Bowl scouting.")
    target_year = int(class_row["draft_year"])
    initialize_for_game(con, game_id=target_game_id, draft_year=target_year, welcome_message=False)
    backfill_senior_bowl_fields(con, draft_year=target_year)
    return target_game_id, class_row, target_year, user_team_abbr(con)


def build_senior_bowl_payload(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    class_row: sqlite3.Row,
) -> dict[str, Any]:
    ensure_schema(con)
    counts = senior_bowl_counts(con, draft_class_id=int(class_row["draft_class_id"]))
    window = senior_bowl_window_status(con, draft_year)
    run = con.execute(
        """
        SELECT *
        FROM scouting_senior_bowl_runs
        WHERE game_id = ?
          AND draft_year = ?
        """,
        (game_id, draft_year),
    ).fetchone()
    reports = con.execute(
        """
        SELECT
            sbr.*,
            dp.first_name || ' ' || dp.last_name AS player_name,
            dp.position,
            dp.college
        FROM scouting_senior_bowl_reports sbr
        JOIN draft_prospects dp ON dp.prospect_id = sbr.prospect_id
        WHERE sbr.game_id = ?
          AND sbr.draft_year = ?
          AND sbr.team_abbr = ?
        ORDER BY sbr.report_id DESC
        LIMIT 10
        """,
        (game_id, draft_year, user_team_abbr(con)),
    ).fetchall()
    report_payload = []
    for row in reports:
        data = dict(row)
        data["revealedTraits"] = decode_json(data.pop("revealed_traits_json", None), [])
        report_payload.append(data)
    return {
        **counts,
        "eventDate": window["eventDate"],
        "windowStart": window["start"],
        "windowEnd": window["end"],
        "locked": not bool(window["open"]),
        "lockedReason": window["reason"],
        "processed": bool(run),
        "run": dict(run) if run else None,
        "userReports": report_payload,
    }


def reveal_senior_bowl_trait(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    team_abbr: str,
    prospect: sqlite3.Row,
    event_date: str,
    traits: list[dict[str, Any]],
) -> str:
    name = prospect_name(prospect)
    trait_text = ", ".join(f"{trait['displayName']} {trait['intensity']}/100" for trait in traits) or "no strong marker"
    notes = f"Senior Bowl exposure gave {team_abbr} a cleaner read on {name}'s personality: {trait_text}."
    if team_abbr == user_team_abbr(con):
        con.execute(
            """
            INSERT INTO scouting_prospect_progress (
                game_id, draft_year, prospect_id, visibility_status, scouting_level,
                scouting_confidence, times_scouted, personality_known,
                last_scouted_season, last_scouted_week, last_scouted_date, last_report, updated_at
            )
            VALUES (?, ?, ?, 'known', 15, 'Low', 1, 1, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(game_id, draft_year, prospect_id) DO UPDATE SET
                personality_known = 1,
                times_scouted = scouting_prospect_progress.times_scouted + 1,
                last_scouted_season = excluded.last_scouted_season,
                last_scouted_week = excluded.last_scouted_week,
                last_scouted_date = excluded.last_scouted_date,
                last_report = excluded.last_report,
                updated_at = datetime('now')
            """,
            (
                game_id,
                draft_year,
                int(prospect["prospect_id"]),
                current_season(con),
                current_scouting_period(con).week,
                event_date,
                notes,
            ),
        )
        add_inbox_message(
            con,
            game_id=game_id,
            title=f"Senior Bowl Note: {name}",
            body=notes,
            category="Scouting",
            priority="normal",
            source="Senior Bowl Staff",
            message_date=event_date,
            related_table="draft_prospects",
            related_id=int(prospect["prospect_id"]),
        )
    return notes


def process_senior_bowl(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    draft_year: int | None = None,
    seed: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    target_game_id, class_row, target_year, user_team = senior_bowl_context(con, game_id=game_id, draft_year=draft_year)
    draft_class_id = int(class_row["draft_class_id"])
    existing = con.execute(
        """
        SELECT *
        FROM scouting_senior_bowl_runs
        WHERE game_id = ?
          AND draft_year = ?
        """,
        (target_game_id, target_year),
    ).fetchone()
    if existing and not force:
        return {"draft_year": target_year, "already_processed": True, **dict(existing)}
    window = senior_bowl_window_status(con, target_year)
    if not force and not window["open"]:
        raise ValueError(window["reason"] or "Senior Bowl processing is not open on the current game date.")
    if force:
        con.execute("DELETE FROM scouting_senior_bowl_reports WHERE game_id = ? AND draft_year = ?", (target_game_id, target_year))
        con.execute("DELETE FROM scouting_senior_bowl_runs WHERE game_id = ? AND draft_year = ?", (target_game_id, target_year))
    teams = con.execute("SELECT team_id, abbreviation FROM teams ORDER BY abbreviation").fetchall() if table_exists(con, "teams") else []
    team_rows = [dict(row) for row in teams] or [{"team_id": None, "abbreviation": user_team}]
    prospects = con.execute(
        """
        SELECT *
        FROM draft_prospects
        WHERE draft_class_id = ?
          AND COALESCE(senior_bowl_accepted, 0) = 1
        ORDER BY COALESCE(public_board_rank, scouting_rank, 9999), prospect_id
        """,
        (draft_class_id,),
    ).fetchall()
    counts = senior_bowl_counts(con, draft_class_id=draft_class_id)
    event_date = senior_bowl_event_date(target_year)
    rng = random.Random(seed or f"{target_game_id}:{target_year}:senior-bowl")
    team_report_count = 0
    user_report_count = 0
    period = ScoutingPeriod(season=current_season(con), week=current_scouting_period(con).week, label="Senior Bowl", date=event_date)
    for prospect in prospects:
        chance = senior_bowl_report_chance(prospect)
        for team in team_rows:
            team_abbr = str(team["abbreviation"])
            team_id = int(team["team_id"]) if team.get("team_id") is not None else None
            if rng.random() >= chance:
                continue
            all_traits = trait_payload(con, int(prospect["prospect_id"]))
            reveal_trait = bool(all_traits) and rng.random() < 0.45
            result_type = "trait" if reveal_trait else "confidence"
            revealed_traits: list[dict[str, Any]] = []
            confidence_up = 0
            trait_revealed = 0
            if reveal_trait:
                revealed_traits = [rng.choice(all_traits)]
                trait_revealed = 1
                notes = reveal_senior_bowl_trait(
                    con,
                    game_id=target_game_id,
                    draft_year=target_year,
                    team_abbr=team_abbr,
                    prospect=prospect,
                    event_date=event_date,
                    traits=revealed_traits,
                )
                if team_abbr != user_team and team_id is not None:
                    mark_cpu_prospect_personality_known(
                        con,
                        game_id=target_game_id,
                        draft_year=target_year,
                        team_id=team_id,
                        period=period,
                        prospect=prospect,
                        notes=notes,
                    )
            else:
                confidence_up = 1
                notes = f"Senior Bowl practice exposure moved {team_abbr}'s grade confidence on {prospect_name(prospect)} up one tier."
                if team_abbr == user_team:
                    advance_prospect_one_tier(
                        con,
                        game_id=target_game_id,
                        draft_year=target_year,
                        period=period,
                        prospect=prospect,
                        reason="Senior Bowl practice exposure improved",
                        source="Senior Bowl Staff",
                    )
                elif team_id is not None:
                    advance_cpu_prospect_one_tier(
                        con,
                        game_id=target_game_id,
                        draft_year=target_year,
                        team_id=team_id,
                        period=period,
                        prospect=prospect,
                        reason="Senior Bowl practice exposure improved",
                    )
            con.execute(
                """
                INSERT OR IGNORE INTO scouting_senior_bowl_reports (
                    game_id, draft_year, team_abbr, prospect_id, event_date, result_type,
                    trait_revealed, confidence_up, revealed_traits_json, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_game_id,
                    target_year,
                    team_abbr,
                    int(prospect["prospect_id"]),
                    event_date,
                    result_type,
                    trait_revealed,
                    confidence_up,
                    json.dumps(revealed_traits),
                    notes,
                ),
            )
            if con.execute("SELECT changes() AS c").fetchone()["c"]:
                team_report_count += 1
                if team_abbr == user_team:
                    user_report_count += 1
    con.execute(
        """
        INSERT INTO scouting_senior_bowl_runs (
            game_id, draft_year, event_date, seed, eligible_count, invited_count, accepted_count,
            team_report_count, user_report_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            target_game_id,
            target_year,
            event_date,
            seed,
            counts["eligible"],
            counts["invited"],
            counts["accepted"],
            team_report_count,
            user_report_count,
        ),
    )
    add_inbox_message(
        con,
        game_id=target_game_id,
        title=f"{target_year} Senior Bowl Reports",
        body=(
            f"{counts['accepted']} prospects accepted Senior Bowl invites. "
            f"Your staff came away with {user_report_count} useful scouting note(s)."
        ),
        category="Scouting",
        priority="normal",
        source="Senior Bowl Staff",
        message_date=event_date,
        related_table="draft_classes",
        related_id=draft_class_id,
    )
    return {
        "draft_year": target_year,
        "event_date": event_date,
        "already_processed": False,
        "team_report_count": team_report_count,
        "user_report_count": user_report_count,
        **counts,
    }


def normalize_board_row(
    row: sqlite3.Row,
    *,
    game_id: str | None = None,
    draft_year: int | None = None,
    team_id: int | None = None,
) -> dict[str, Any]:
    data = dict(row)
    raw_scout_grade = data.get("scout_grade")
    raw_scout_ceiling = data.get("scout_ceiling")
    if game_id and draft_year and team_id is not None:
        data["public_scout_grade"] = raw_scout_grade
        data["public_scout_ceiling"] = raw_scout_ceiling
        data["scout_grade"] = int(round(scouting_perception.perceived_grade(data, game_id=game_id, draft_year=draft_year, team_id=team_id)))
        data["scout_ceiling"] = int(round(scouting_perception.perceived_ceiling(data, game_id=game_id, draft_year=draft_year, team_id=team_id)))
    data.pop("true_grade", None)
    data.pop("ceiling_grade", None)
    data["top30_revealed_traits"] = decode_json(data.pop("top30_revealed_traits_json", None), [])
    data["top30_revealed_hidden_info"] = decode_json(data.pop("top30_revealed_hidden_info_json", None), None)
    return data


def empty_audit_payload() -> dict[str, Any]:
    return {
        "available": False,
        "counts": {},
        "userConfidence": [],
        "cpuConfidence": [],
        "teamHiddenFinds": [],
        "mostDiscoveredHidden": [],
        "largestGradeGaps": [],
        "topPerceivedByTeam": [],
    }


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def audit_confidence_rows(
    con: sqlite3.Connection,
    *,
    table_name: str,
    game_id: str,
    draft_year: int,
) -> list[dict[str, Any]]:
    if not table_exists(con, table_name):
        return []
    return rows_to_dicts(
        con.execute(
            f"""
            SELECT
                COALESCE(scouting_confidence, 'Low') AS confidence,
                COALESCE(visibility_status, 'known') AS visibility,
                COUNT(*) AS count
            FROM {table_name}
            WHERE game_id = ?
              AND draft_year = ?
            GROUP BY COALESCE(scouting_confidence, 'Low'), COALESCE(visibility_status, 'known')
            ORDER BY
                CASE COALESCE(scouting_confidence, 'Low')
                    WHEN 'Very High' THEN 4
                    WHEN 'High' THEN 3
                    WHEN 'Medium' THEN 2
                    WHEN 'Low' THEN 1
                    ELSE 0
                END DESC,
                visibility
            """,
            (game_id, draft_year),
        ).fetchall()
    )


def audit_team_hidden_finds(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
) -> list[dict[str, Any]]:
    if not table_exists(con, "cpu_scouting_prospect_progress"):
        return []
    return rows_to_dicts(
        con.execute(
            """
            SELECT
                t.abbreviation AS team,
                COUNT(DISTINCT csp.prospect_id) AS hidden_found,
                SUM(CASE WHEN csp.prospect_id IS NOT NULL AND COALESCE(csp.scouting_confidence, 'Low') = 'Very High' THEN 1 ELSE 0 END) AS very_high,
                SUM(CASE WHEN csp.prospect_id IS NOT NULL AND COALESCE(csp.scouting_confidence, 'Low') = 'High' THEN 1 ELSE 0 END) AS high,
                SUM(CASE WHEN csp.prospect_id IS NOT NULL AND COALESCE(csp.scouting_confidence, 'Low') = 'Medium' THEN 1 ELSE 0 END) AS medium,
                SUM(CASE WHEN csp.prospect_id IS NOT NULL AND COALESCE(csp.scouting_confidence, 'Low') = 'Low' THEN 1 ELSE 0 END) AS low
            FROM teams t
            LEFT JOIN cpu_scouting_prospect_progress csp
              ON csp.team_id = t.team_id
             AND csp.game_id = ?
             AND csp.draft_year = ?
             AND csp.visibility_status = 'discovered'
            LEFT JOIN draft_prospects dp
              ON dp.prospect_id = csp.prospect_id
             AND COALESCE(dp.public_board_status, '') = 'off_public_board'
            GROUP BY t.team_id, t.abbreviation
            ORDER BY hidden_found DESC, t.abbreviation
            """,
            (game_id, draft_year),
        ).fetchall()
    )


def audit_most_discovered_hidden(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    draft_class_id: int,
    limit: int,
) -> list[dict[str, Any]]:
    if not table_exists(con, "cpu_scouting_prospect_progress"):
        return []
    rows = con.execute(
        """
        SELECT
            dp.prospect_id,
            dp.first_name || ' ' || dp.last_name AS player_name,
            dp.position,
            dp.college,
            dp.college_tier,
            dp.scout_grade,
            dp.scout_ceiling,
            dp.true_grade,
            dp.ceiling_grade,
            dp.scouting_variance,
            COUNT(DISTINCT csp.team_id) AS cpu_teams_found,
            CASE WHEN spp.visibility_status = 'discovered' THEN 1 ELSE 0 END AS user_found,
            GROUP_CONCAT(DISTINCT t.abbreviation) AS teams
        FROM draft_prospects dp
        LEFT JOIN cpu_scouting_prospect_progress csp
          ON csp.prospect_id = dp.prospect_id
         AND csp.game_id = ?
         AND csp.draft_year = ?
         AND csp.visibility_status = 'discovered'
        LEFT JOIN teams t ON t.team_id = csp.team_id
        LEFT JOIN scouting_prospect_progress spp
          ON spp.prospect_id = dp.prospect_id
         AND spp.game_id = ?
         AND spp.draft_year = ?
        WHERE dp.draft_class_id = ?
          AND COALESCE(dp.public_board_status, '') = 'off_public_board'
        GROUP BY dp.prospect_id
        HAVING cpu_teams_found > 0 OR user_found = 1
        ORDER BY (cpu_teams_found + user_found) DESC, dp.scouting_variance DESC, dp.scout_ceiling DESC
        LIMIT ?
        """,
        (game_id, draft_year, game_id, draft_year, draft_class_id, limit),
    ).fetchall()
    payload = []
    for row in rows:
        data = dict(row)
        data["totalFound"] = int(data.get("cpu_teams_found") or 0) + int(data.get("user_found") or 0)
        data["teams"] = [team for team in str(data.get("teams") or "").split(",") if team]
        payload.append(data)
    return payload


def audit_largest_grade_gaps(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    draft_class_id: int,
    limit: int,
) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
            dp.prospect_id,
            dp.public_board_rank,
            dp.first_name || ' ' || dp.last_name AS player_name,
            dp.position,
            dp.college,
            dp.public_board_status,
            dp.discovery_status,
            COALESCE(spp.visibility_status, CASE WHEN COALESCE(dp.public_board_status, '') = 'off_public_board' THEN 'hidden' ELSE 'known' END) AS user_visibility,
            COALESCE(spp.scouting_confidence, dp.scout_confidence, 'Low') AS user_confidence,
            dp.scout_grade,
            dp.true_grade,
            ABS(COALESCE(dp.true_grade, dp.scout_grade, 0) - COALESCE(dp.scout_grade, dp.true_grade, 0)) AS grade_gap,
            dp.scout_ceiling,
            dp.ceiling_grade,
            ABS(COALESCE(dp.ceiling_grade, dp.scout_ceiling, 0) - COALESCE(dp.scout_ceiling, dp.ceiling_grade, 0)) AS ceiling_gap,
            dp.scouting_variance
        FROM draft_prospects dp
        LEFT JOIN scouting_prospect_progress spp
          ON spp.prospect_id = dp.prospect_id
         AND spp.game_id = ?
         AND spp.draft_year = ?
        WHERE dp.draft_class_id = ?
          AND (
                COALESCE(dp.public_board_status, 'public_board') <> 'off_public_board'
                OR COALESCE(spp.visibility_status, '') = 'discovered'
                OR COALESCE(dp.discovery_status, '') = 'discovered'
              )
        ORDER BY grade_gap DESC, ceiling_gap DESC, dp.scouting_variance DESC
        LIMIT ?
        """,
        (game_id, draft_year, draft_class_id, limit),
    ).fetchall()
    return rows_to_dicts(rows)


def audit_top_perceived_by_team(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    draft_class_id: int,
    teams_limit: int,
    prospects_per_team: int,
) -> list[dict[str, Any]]:
    if not table_exists(con, "cpu_scouting_prospect_progress"):
        return []
    teams = con.execute(
        "SELECT team_id, abbreviation FROM teams ORDER BY abbreviation LIMIT ?",
        (teams_limit,),
    ).fetchall()
    output: list[dict[str, Any]] = []
    for team in teams:
        team_id = int(team["team_id"])
        rows = con.execute(
            """
            SELECT
                dp.prospect_id,
                dp.public_board_rank,
                dp.first_name || ' ' || dp.last_name AS player_name,
                dp.position,
                dp.college,
                dp.public_board_status,
                dp.discovery_status,
                dp.scout_grade,
                dp.scout_ceiling,
                dp.true_grade,
                dp.ceiling_grade,
                dp.scouting_variance,
                COALESCE(csp.scouting_level, 15) AS cpu_scouting_level,
                COALESCE(csp.scouting_confidence, 'Low') AS cpu_scouting_confidence,
                COALESCE(csp.times_scouted, 0) AS cpu_times_scouted,
                CASE
                    WHEN COALESCE(csp.visibility_status, '') = 'discovered'
                         OR COALESCE(dp.discovery_status, '') = 'discovered'
                    THEN 'discovered'
                    ELSE COALESCE(csp.visibility_status, 'known')
                END AS cpu_visibility_status
            FROM draft_prospects dp
            LEFT JOIN cpu_scouting_prospect_progress csp
              ON csp.prospect_id = dp.prospect_id
             AND csp.game_id = ?
             AND csp.draft_year = ?
             AND csp.team_id = ?
            WHERE dp.draft_class_id = ?
              AND dp.status = 'Available'
              AND (
                    COALESCE(dp.public_board_status, 'public_board') <> 'off_public_board'
                    OR COALESCE(csp.visibility_status, '') = 'discovered'
                    OR COALESCE(dp.discovery_status, '') = 'discovered'
                  )
            """,
            (game_id, draft_year, team_id, draft_class_id),
        ).fetchall()
        scored = []
        for row in rows:
            perceived_grade = scouting_perception.perceived_grade(
                row,
                game_id=game_id,
                draft_year=draft_year,
                team_id=team_id,
            )
            perceived_ceiling = scouting_perception.perceived_ceiling(
                row,
                game_id=game_id,
                draft_year=draft_year,
                team_id=team_id,
            )
            rank = int(row["public_board_rank"] or 275)
            value_anchor = max(0.0, 42.0 - (rank * 0.105))
            score = perceived_grade + (max(0.0, perceived_ceiling - perceived_grade) * 0.34) + value_anchor
            scored.append((score, perceived_grade, perceived_ceiling, row))
        scored.sort(key=lambda item: (-item[0], int(item[3]["public_board_rank"] or 9999), int(item[3]["prospect_id"])))
        output.append(
            {
                "team": team["abbreviation"],
                "top": [
                    {
                        "prospect_id": int(row["prospect_id"]),
                        "player_name": row["player_name"],
                        "position": row["position"],
                        "college": row["college"],
                        "public_board_rank": row["public_board_rank"],
                        "public_board_status": row["public_board_status"],
                        "cpu_visibility_status": row["cpu_visibility_status"],
                        "cpu_scouting_confidence": row["cpu_scouting_confidence"],
                        "perceivedGrade": round(perceived_grade, 1),
                        "perceivedCeiling": round(perceived_ceiling, 1),
                        "scout_grade": row["scout_grade"],
                        "true_grade": row["true_grade"],
                    }
                    for _score, perceived_grade, perceived_ceiling, row in scored[:prospects_per_team]
                ],
            }
        )
    return output


def build_audit_payload(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    draft_year: int | None = None,
    limit: int = 12,
    team_limit: int = 32,
    prospects_per_team: int = 3,
) -> dict[str, Any]:
    ensure_schema(con)
    target_game_id = active_game_id(con, game_id)
    class_row = draft_class_row(con, draft_year)
    if not class_row:
        payload = empty_audit_payload()
        payload["reason"] = "No draft class found."
        payload["gameId"] = target_game_id
        return payload
    target_year = int(class_row["draft_year"])
    draft_class_id = int(class_row["draft_class_id"])
    counts_row = con.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN COALESCE(public_board_status, '') = 'off_public_board' THEN 1 ELSE 0 END) AS off_board,
            SUM(CASE WHEN COALESCE(public_board_status, '') <> 'off_public_board' THEN 1 ELSE 0 END) AS public_board,
            SUM(CASE WHEN COALESCE(public_board_status, '') = 'off_public_board'
                      AND COALESCE(discovery_status, 'undiscovered') = 'undiscovered'
                     THEN 1 ELSE 0 END) AS hidden_remaining,
            SUM(CASE WHEN COALESCE(public_board_status, '') = 'off_public_board'
                      AND COALESCE(discovery_status, '') = 'discovered'
                     THEN 1 ELSE 0 END) AS globally_discovered
        FROM draft_prospects
        WHERE draft_class_id = ?
        """,
        (draft_class_id,),
    ).fetchone()
    user_hidden = 0
    if table_exists(con, "scouting_prospect_progress"):
        user_hidden = int(
            con.execute(
                """
                SELECT COUNT(DISTINCT spp.prospect_id) AS c
                FROM scouting_prospect_progress spp
                JOIN draft_prospects dp ON dp.prospect_id = spp.prospect_id
                WHERE spp.game_id = ?
                  AND spp.draft_year = ?
                  AND spp.visibility_status = 'discovered'
                  AND COALESCE(dp.public_board_status, '') = 'off_public_board'
                """,
                (target_game_id, target_year),
            ).fetchone()["c"]
            or 0
        )
    cpu_hidden_unique = 0
    cpu_hidden_events = 0
    if table_exists(con, "cpu_scouting_prospect_progress"):
        row = con.execute(
            """
            SELECT
                COUNT(DISTINCT csp.prospect_id) AS unique_found,
                COUNT(*) AS discovery_events
            FROM cpu_scouting_prospect_progress csp
            JOIN draft_prospects dp ON dp.prospect_id = csp.prospect_id
            WHERE csp.game_id = ?
              AND csp.draft_year = ?
              AND csp.visibility_status = 'discovered'
              AND COALESCE(dp.public_board_status, '') = 'off_public_board'
            """,
            (target_game_id, target_year),
        ).fetchone()
        cpu_hidden_unique = int(row["unique_found"] or 0)
        cpu_hidden_events = int(row["discovery_events"] or 0)
    most_discovered = audit_most_discovered_hidden(
        con,
        game_id=target_game_id,
        draft_year=target_year,
        draft_class_id=draft_class_id,
        limit=limit,
    )
    counts = {
        "totalProspects": int(counts_row["total"] or 0),
        "publicProspects": int(counts_row["public_board"] or 0),
        "offBoardProspects": int(counts_row["off_board"] or 0),
        "hiddenRemaining": int(counts_row["hidden_remaining"] or 0),
        "globallyDiscoveredHidden": int(counts_row["globally_discovered"] or 0),
        "userHiddenFound": user_hidden,
        "cpuHiddenUniqueFound": cpu_hidden_unique,
        "cpuHiddenDiscoveryEvents": cpu_hidden_events,
        "maxTeamsOnOneHidden": max((int(row.get("totalFound") or 0) for row in most_discovered), default=0),
    }
    return {
        "available": True,
        "gameId": target_game_id,
        "draftYear": target_year,
        "currentDate": current_date(con),
        "counts": counts,
        "userConfidence": audit_confidence_rows(
            con,
            table_name="scouting_prospect_progress",
            game_id=target_game_id,
            draft_year=target_year,
        ),
        "cpuConfidence": audit_confidence_rows(
            con,
            table_name="cpu_scouting_prospect_progress",
            game_id=target_game_id,
            draft_year=target_year,
        ),
        "teamHiddenFinds": audit_team_hidden_finds(
            con,
            game_id=target_game_id,
            draft_year=target_year,
        ),
        "mostDiscoveredHidden": most_discovered,
        "largestGradeGaps": audit_largest_grade_gaps(
            con,
            game_id=target_game_id,
            draft_year=target_year,
            draft_class_id=draft_class_id,
            limit=limit,
        ),
        "topPerceivedByTeam": audit_top_perceived_by_team(
            con,
            game_id=target_game_id,
            draft_year=target_year,
            draft_class_id=draft_class_id,
            teams_limit=team_limit,
            prospects_per_team=prospects_per_team,
        ),
    }


def build_ui_payload(con: sqlite3.Connection, *, limit: int = 40) -> dict[str, Any]:
    game_id = active_game_id(con)
    class_row = draft_class_row(con)
    period = current_scouting_period(con)
    weekly_window = weekly_scouting_window_status(con, period)
    draft_year_value = int(class_row["draft_year"]) if class_row else None
    workout_payload = workout_visibility(con, draft_year_value) if draft_year_value else {}
    if not table_exists(con, "user_inbox_messages") or not table_exists(con, "scouting_prospect_progress"):
        return {
            "gameId": game_id,
            "available": False,
            "needsSetup": True,
            "inbox": [],
            "draftYear": draft_year_value,
            "period": period.__dict__,
            "weeklyWindow": weekly_window,
            "workoutVisibility": workout_payload,
            "board": [],
            "counts": {"visible": 0, "pending": 0, "discovered": 0, "unread": 0},
            "actionsUsed": {key: False for key in SIMPLE_ACTION_LABELS},
            "weeklyChoiceUsed": False,
            "usedAction": None,
            "top30": empty_top30_payload(),
            "seniorBowl": empty_senior_bowl_payload(),
            "audit": empty_audit_payload(),
        }
    ensure_schema(con)
    inbox = inbox_rows(con, game_id=game_id, limit=12)
    if not class_row:
        return {
            "gameId": game_id,
            "available": False,
            "inbox": inbox,
            "draftYear": None,
            "period": period.__dict__,
            "weeklyWindow": weekly_window,
            "workoutVisibility": workout_payload,
            "board": [],
            "counts": {"visible": 0, "pending": 0, "unread": unread_count(con, game_id)},
            "actionsUsed": {key: False for key in SIMPLE_ACTION_LABELS},
            "weeklyChoiceUsed": False,
            "usedAction": None,
            "top30": empty_top30_payload(),
            "seniorBowl": empty_senior_bowl_payload(),
            "audit": empty_audit_payload(),
        }
    draft_year = int(class_row["draft_year"])
    workout_payload = workout_visibility(con, draft_year)
    top30_payload = build_top30_payload(con, game_id=game_id, draft_year=draft_year, class_row=class_row)
    senior_bowl_payload = build_senior_bowl_payload(con, game_id=game_id, draft_year=draft_year, class_row=class_row)
    progress_count = con.execute(
        """
        SELECT COUNT(*) AS c
        FROM scouting_prospect_progress
        WHERE game_id = ?
          AND draft_year = ?
        """,
        (game_id, draft_year),
    ).fetchone()["c"]
    if int(progress_count or 0) == 0:
        return {
            "gameId": game_id,
            "available": False,
            "needsSetup": True,
            "inbox": inbox,
            "draftYear": draft_year,
            "period": period.__dict__,
            "weeklyWindow": weekly_window,
            "workoutVisibility": workout_payload,
            "board": [],
            "counts": {"visible": 0, "pending": 0, "discovered": 0, "unread": unread_count(con, game_id)},
            "actionsUsed": {key: False for key in SIMPLE_ACTION_LABELS},
            "weeklyChoiceUsed": False,
            "usedAction": None,
            "top30": top30_payload,
            "seniorBowl": senior_bowl_payload,
            "audit": build_audit_payload(con, game_id=game_id, draft_year=draft_year, limit=8, team_limit=12, prospects_per_team=2),
        }
    board = con.execute(
        """
        SELECT
            dp.prospect_id,
            dp.public_board_rank,
            dp.scouting_rank,
            dp.public_board_status,
            dp.discovery_status,
            dp.first_name || ' ' || dp.last_name AS player_name,
            dp.position,
            dp.position_group,
            dp.college,
            dp.college_tier,
            dp.hometown,
            dp.hometown_city,
            dp.hometown_state,
            dp.hometown_region,
            dp.birth_country,
            dp.is_international,
            dp.age,
            dp.college_class,
            dp.senior_bowl_eligible,
            dp.senior_bowl_invited,
            dp.senior_bowl_accepted,
            dp.senior_bowl_result,
            dp.senior_bowl_notes,
            dp.height_in,
            dp.weight_lbs,
            dp.arm_length_in,
            dp.hand_size_in,
            dp.archetype,
            dp.primary_role,
            dp.secondary_role,
            dp.projected_round,
            dp.projected_pick,
            dp.scout_confidence,
            dp.scout_grade,
            dp.scout_ceiling,
            dp.true_grade,
            dp.ceiling_grade,
            dp.scout_risk,
            dp.scout_lens,
            dp.scouting_variance,
            dp.development_pathway,
            dp.pipeline_note,
            dp.scouting_summary,
            dp.scouting_strengths,
            dp.scouting_concerns,
            dp.scouting_projection,
            dp.scouting_report,
            dp.selected_pick_id,
            dp.selected_team_id,
            spp.visibility_status,
            spp.scouting_level,
            spp.scouting_confidence,
            spp.times_scouted,
            spp.personality_known,
            spp.last_report,
            stv.visit_id AS top30_visit_id,
            stv.result_type AS top30_result_type,
            stv.personality_revealed AS top30_personality_revealed,
            stv.full_info_revealed AS top30_full_info_revealed,
            stv.notes AS top30_notes,
            stv.revealed_traits_json AS top30_revealed_traits_json,
            stv.revealed_hidden_info_json AS top30_revealed_hidden_info_json,
            EXISTS (
                SELECT 1
                FROM scouting_assignments sa
                WHERE sa.game_id = spp.game_id
                  AND sa.draft_year = spp.draft_year
                  AND sa.prospect_id = spp.prospect_id
                  AND sa.season = ?
                  AND sa.week = ?
                  AND sa.status = 'pending'
            ) AS queued
        FROM draft_prospects dp
        JOIN scouting_prospect_progress spp
          ON spp.prospect_id = dp.prospect_id
         AND spp.game_id = ?
         AND spp.draft_year = ?
        LEFT JOIN scouting_top30_visits stv
          ON stv.prospect_id = dp.prospect_id
         AND stv.game_id = spp.game_id
         AND stv.draft_year = spp.draft_year
         AND stv.team_abbr = ?
        WHERE dp.draft_class_id = ?
          AND (
                COALESCE(dp.public_board_status, 'public_board') <> 'off_public_board'
                OR spp.visibility_status = 'discovered'
              )
        ORDER BY
            CASE WHEN dp.public_board_rank IS NULL THEN 999 ELSE dp.public_board_rank END,
            spp.scouting_level DESC,
            dp.scout_grade DESC
        LIMIT ?
        """,
        (period.season, period.week, game_id, draft_year, user_team_abbr(con), int(class_row["draft_class_id"]), limit),
    ).fetchall()
    pending_row = con.execute(
        """
        SELECT
            COUNT(*) AS pending_count,
            COALESCE(SUM(CASE WHEN UPPER(COALESCE(dp.position, '')) = 'QB' THEN ? ELSE 1 END), 0) AS pending_cost
        FROM scouting_assignments
        JOIN draft_prospects dp
          ON dp.prospect_id = scouting_assignments.prospect_id
        WHERE scouting_assignments.game_id = ?
          AND scouting_assignments.draft_year = ?
          AND scouting_assignments.season = ?
          AND scouting_assignments.week = ?
          AND scouting_assignments.status = 'pending'
        """,
        (SPECIFIC_SCOUTING_COUNT, game_id, draft_year, period.season, period.week),
    ).fetchone()
    pending = int(pending_row["pending_count"] or 0)
    pending_specific_cost = int(pending_row["pending_cost"] or 0)
    discovered = con.execute(
        """
        SELECT COUNT(*) AS c
        FROM scouting_prospect_progress
        WHERE game_id = ?
          AND draft_year = ?
          AND visibility_status = 'discovered'
        """,
        (game_id, draft_year),
    ).fetchone()["c"]
    hidden_remaining = con.execute(
        """
        SELECT COUNT(*) AS c
        FROM draft_prospects dp
        LEFT JOIN scouting_prospect_progress spp
          ON spp.prospect_id = dp.prospect_id
         AND spp.game_id = ?
         AND spp.draft_year = ?
        WHERE dp.draft_class_id = ?
          AND COALESCE(dp.public_board_status, '') = 'off_public_board'
          AND COALESCE(dp.discovery_status, 'undiscovered') = 'undiscovered'
          AND spp.prospect_id IS NULL
        """,
        (game_id, draft_year, int(class_row["draft_class_id"])),
    ).fetchone()["c"]
    used_rows = con.execute(
        """
        SELECT action_key, uses
        FROM scouting_weekly_actions
        WHERE game_id = ?
          AND draft_year = ?
          AND season = ?
          AND week = ?
        """,
        (game_id, draft_year, period.season, period.week),
    ).fetchall()
    action_uses = {str(row["action_key"]): int(row["uses"] or 0) for row in used_rows}
    pending_specific_uses = pending_specific_cost
    if pending_specific_uses:
        action_uses["specific"] = action_uses.get("specific", 0) + pending_specific_uses
    used = set(action_uses)
    non_specific_action_used = any(key != "specific" for key in used)
    specific_uses = action_uses.get("specific", 0)
    weekly_choice_used = non_specific_action_used or specific_uses >= SPECIFIC_SCOUTING_COUNT
    used_action = next(iter(used), None)
    return mask_unavailable_workouts({
        "gameId": game_id,
        "available": True,
        "draftYear": draft_year,
        "period": period.__dict__,
        "weeklyWindow": weekly_window,
        "workoutVisibility": workout_payload,
        "inbox": inbox,
        "counts": {
            "visible": len(board),
            "pending": int(pending or 0),
            "discovered": int(discovered or 0),
            "hiddenRemaining": int(hidden_remaining or 0),
            "unread": unread_count(con, game_id),
        },
        "actionsUsed": {
            key: (
                specific_uses >= SPECIFIC_SCOUTING_COUNT
                if key == "specific"
                else key in used
            )
            for key in SIMPLE_ACTION_LABELS
        },
        "actionUses": action_uses,
        "actionLimits": {
            "specific": SPECIFIC_SCOUTING_COUNT,
            "random_two": 1,
            "discover_four": 1,
            "auto_assign": 1,
        },
        "weeklyActionStarted": bool(used),
        "nonSpecificActionUsed": non_specific_action_used,
        "weeklyChoiceUsed": weekly_choice_used,
        "usedAction": used_action,
        "actionLabels": SIMPLE_ACTION_LABELS,
        "top30": top30_payload,
        "seniorBowl": senior_bowl_payload,
        "audit": build_audit_payload(con, game_id=game_id, draft_year=draft_year, limit=8, team_limit=12, prospects_per_team=2),
        "board": [
            normalize_board_row(row, game_id=game_id, draft_year=draft_year, team_id=user_team_id(con))
            for row in board
        ],
    })


def unread_count(con: sqlite3.Connection, game_id: str) -> int:
    ensure_schema(con)
    row = con.execute(
        "SELECT COUNT(*) AS c FROM user_inbox_messages WHERE game_id = ? AND is_read = 0",
        (game_id,),
    ).fetchone()
    return int(row["c"] or 0)


def inbox_rows(con: sqlite3.Connection, *, game_id: str, limit: int = 20) -> list[dict[str, Any]]:
    ensure_schema(con)
    rows = con.execute(
        """
        SELECT *
        FROM user_inbox_messages
        WHERE game_id = ?
        ORDER BY is_read ASC, message_date DESC, message_id DESC
        LIMIT ?
        """,
        (game_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def mark_inbox_read(con: sqlite3.Connection, *, game_id: str | None = None, message_id: int | None = None) -> int:
    ensure_schema(con)
    target_game_id = active_game_id(con, game_id)
    if message_id:
        cur = con.execute(
            """
            UPDATE user_inbox_messages
            SET is_read = 1
            WHERE game_id = ?
              AND message_id = ?
            """,
            (target_game_id, message_id),
        )
    else:
        cur = con.execute(
            """
            UPDATE user_inbox_messages
            SET is_read = 1
            WHERE game_id = ?
            """,
            (target_game_id,),
        )
    return cur.rowcount


def action_setup(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = initialize_for_game(
            con,
            game_id=args.game_id,
            draft_year=args.draft_year,
            reset=args.reset,
        )
        con.commit()
    print(
        f"Scouting setup ready for {result['draft_year']}: "
        f"{result['public']} public prospects, {result['hidden']} hidden/off-board prospects."
    )


def action_senior_bowl_setup(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = backfill_senior_bowl_fields(con, draft_year=args.draft_year, force=args.force)
        con.commit()
    print(
        f"Senior Bowl labels ready for {result['draft_year']}: "
        f"{result['eligible']} eligible, {result['invited']} invited, "
        f"{result['accepted']} accepted. Updated {result['updated']} prospect(s)."
    )


def action_senior_bowl_process(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = process_senior_bowl(
            con,
            game_id=args.game_id,
            draft_year=args.draft_year,
            seed=args.seed,
            force=args.force,
        )
        con.commit()
    if result.get("already_processed"):
        print(
            f"Senior Bowl already processed for {result['draft_year']}: "
            f"{result.get('user_report_count', 0)} user report(s), "
            f"{result.get('team_report_count', 0)} total team report(s). Use --force to rerun."
        )
        return
    print(
        f"Senior Bowl processed for {result['draft_year']} on {result['event_date']}: "
        f"{result['accepted']} participants, {result['user_report_count']} user report(s), "
        f"{result['team_report_count']} total team report(s)."
    )


def action_assign(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        prospect = assign_prospect(
            con,
            prospect_id=args.prospect_id,
            game_id=args.game_id,
            draft_year=args.draft_year,
            season=args.season,
            week=args.week,
            focus=args.focus,
        )
        con.commit()
    print(f"Queued scouting: {prospect_name(prospect)} ({prospect['position']}, {prospect['college']})")


def action_assign_batch(args: argparse.Namespace) -> None:
    prospect_ids = [int(value) for value in args.prospect_ids if value]
    if not prospect_ids:
        raise ValueError("Provide at least one --prospect-id.")
    queued = []
    with connect(args.db) as con:
        for prospect_id in prospect_ids:
            prospect = assign_prospect(
                con,
                prospect_id=prospect_id,
                game_id=args.game_id,
                draft_year=args.draft_year,
                season=args.season,
                week=args.week,
                focus=args.focus,
            )
            queued.append(prospect)
        con.commit()
    names = ", ".join(prospect_name(prospect) for prospect in queued)
    print(f"Queued {len(queued)} scouting assignment(s): {names}")


def action_unassign(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = unassign_prospect(
            con,
            prospect_id=args.prospect_id,
            game_id=args.game_id,
            draft_year=args.draft_year,
            season=args.season,
            week=args.week,
        )
        con.commit()
    if result["removed"]:
        print(f"Removed queued scouting: {result['name']} ({result['position']}, {result['college']})")
    else:
        print(f"No pending scouting assignment found for {result['name']}.")


def action_process_week(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = process_assignments(
            con,
            game_id=args.game_id,
            draft_year=args.draft_year,
            season=args.season,
            week=args.week,
            slots=args.slots,
            seed=args.seed,
        )
        con.commit()
    print(
        f"Processed {result['processed']} scouting assignment(s); "
        f"discovered {result['discovered']} off-board prospect(s); "
        f"{result['pending_remaining']} queued assignment(s) remain."
    )


def describe_simple_result(result: dict[str, Any]) -> str:
    rows = result.get("advanced") or []
    if not rows:
        return f"{SIMPLE_ACTION_LABELS.get(result.get('action'), result.get('action', 'Scouting'))}: no changes."
    lines = [f"{SIMPLE_ACTION_LABELS.get(result.get('action'), result.get('action', 'Scouting'))} - {result.get('period', '')}"]
    for row in rows:
        lines.append(
            f"- {row['name']} ({row['position']}, {row['college']}): "
            f"{row['old_confidence']} -> {row['new_confidence']}"
        )
    return "\n".join(lines)


def action_auto(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = auto_assign_scouts(
            con,
            game_id=args.game_id,
            draft_year=args.draft_year,
            count=args.count,
        )
        con.commit()
    print(describe_simple_result(result))


def action_scout_one(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        prospect = assign_prospect(
            con,
            prospect_id=args.prospect_id,
            game_id=args.game_id,
            draft_year=args.draft_year,
        )
        con.commit()
    print(f"Queued scouting: {prospect_name(prospect)} ({prospect['position']}, {prospect['college']})")


def action_random(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = scout_random_players(
            con,
            game_id=args.game_id,
            draft_year=args.draft_year,
            count=RANDOM_CROSSCHECK_COUNT,
            seed=args.seed,
        )
        con.commit()
    print(describe_simple_result(result))


def action_discover(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = discover_non_public_players(
            con,
            game_id=args.game_id,
            draft_year=args.draft_year,
            count=DISCOVER_NON_PUBLIC_COUNT,
            seed=args.seed,
        )
        con.commit()
    print(describe_simple_result(result))


def action_top30_visit(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = execute_top30_visit(
            con,
            prospect_id=args.prospect_id,
            game_id=args.game_id,
            draft_year=args.draft_year,
            seed=args.seed,
            allow_after_draft=args.allow_after_draft,
        )
        con.commit()
    print(f"Top 30 visit: {result['name']} ({result['position']}, {result['college']})")
    print(f"Result: {result['result_type']}")
    print(result["notes"])
    print(f"Visits used: {result['used']}/30")


def action_top30_auto(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = auto_assign_top30_visits(
            con,
            game_id=args.game_id,
            draft_year=args.draft_year,
            seed=args.seed,
            visit_date=args.visit_date,
        )
        cpu_result = (
            auto_assign_cpu_top30_visits(
                con,
                game_id=args.game_id,
                draft_year=args.draft_year,
                seed=args.seed,
                visit_date=args.visit_date,
            )
            if args.include_cpu
            else None
        )
        con.commit()
    print(f"Top 30 auto-fill: {result['status']}")
    if result.get("reason"):
        print(result["reason"])
    print(f"Visits created: {result['created']}")
    print(f"Visits used: {result['used']}/30")
    for visit in (result.get("visits") or [])[:12]:
        print(f"- {visit['name']} ({visit['position']}, {visit['college']}): {visit['result_type']}")
    if cpu_result:
        print(
            f"CPU Top 30 auto-fill: {cpu_result['status']} | "
            f"{cpu_result.get('created', 0)} visits across {cpu_result.get('teams', 0)} teams"
        )
        print(
            f"CPU reveals: {cpu_result.get('personality_reveals', 0)} personality, "
            f"{cpu_result.get('full_reveals', 0)} full."
        )


def action_board(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        payload = build_ui_payload(con, limit=args.limit)
    if not payload["available"]:
        print("No draft class available.")
        return
    print(f"{payload['draftYear']} Draft Scouting Board - {payload['period']['label']}")
    for row in payload["board"]:
        rank = row["public_board_rank"] or "DISC"
        queued = " queued" if row["queued"] else ""
        print(
            f"{str(rank):>4} {row['player_name']:<24} {row['position']:<4} "
            f"{row['college']:<20} scout {row['scout_grade']}/{row['scout_ceiling']} "
            f"{row['scouting_confidence']} L{row['scouting_level']}{queued}"
        )


def action_audit(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        payload = build_audit_payload(
            con,
            game_id=args.game_id,
            draft_year=args.draft_year,
            limit=args.limit,
            team_limit=args.team_limit,
            prospects_per_team=args.prospects_per_team,
        )
    if args.json:
        text = json.dumps(payload, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
            print(f"Wrote scouting audit to {args.output}")
        else:
            print(text)
        return
    if not payload.get("available"):
        print(f"Scouting audit unavailable: {payload.get('reason', 'No draft class found.')}")
        return
    counts = payload["counts"]
    print(f"{payload['draftYear']} Scouting Audit ({payload['gameId']})")
    print(
        f"Prospects: {counts['totalProspects']} total | {counts['publicProspects']} public | "
        f"{counts['offBoardProspects']} off-board | {counts['hiddenRemaining']} hidden remaining"
    )
    print(
        f"Hidden finds: user {counts['userHiddenFound']} | CPU unique {counts['cpuHiddenUniqueFound']} "
        f"({counts['cpuHiddenDiscoveryEvents']} team discoveries) | max overlap {counts['maxTeamsOnOneHidden']}"
    )
    print("\nCPU hidden finds by team:")
    for row in payload["teamHiddenFinds"][: args.team_limit]:
        print(
            f"  {row['team']:<3} {int(row['hidden_found'] or 0):>2} hidden "
            f"(VH {int(row['very_high'] or 0)}, H {int(row['high'] or 0)}, "
            f"M {int(row['medium'] or 0)}, L {int(row['low'] or 0)})"
        )
    print("\nMost-discovered hidden prospects:")
    if not payload["mostDiscoveredHidden"]:
        print("  None yet.")
    for row in payload["mostDiscoveredHidden"][: args.limit]:
        teams = ",".join(row.get("teams") or [])
        user = "+ user" if row.get("user_found") else ""
        print(
            f"  {int(row['totalFound'] or 0):>2} finds {user:<6} | "
            f"{row['player_name']:<24} {row['position']:<4} {row['college']:<20} "
            f"scout {row['scout_grade']}/{row['scout_ceiling']} true {row['true_grade']}/{row['ceiling_grade']} "
            f"var {row['scouting_variance']} [{teams}]"
        )
    print("\nLargest visible scout-vs-true gaps:")
    for row in payload["largestGradeGaps"][: args.limit]:
        rank = row["public_board_rank"] or "DISC"
        print(
            f"  {str(rank):>4} {row['player_name']:<24} {row['position']:<4} "
            f"scout {row['scout_grade']}/{row['scout_ceiling']} true {row['true_grade']}/{row['ceiling_grade']} "
            f"gap {row['grade_gap']}/{row['ceiling_gap']} {row['user_confidence']}"
        )


def action_inbox(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        rows = inbox_rows(con, game_id=active_game_id(con, args.game_id), limit=args.limit)
    if not rows:
        print("Inbox empty.")
        return
    for row in rows:
        status = "read" if row["is_read"] else "new"
        print(f"#{row['message_id']} [{status}] {row['message_date']} {row['title']}")
        print(f"  {row['body']}")


def action_mark_read(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        count = mark_inbox_read(con, game_id=args.game_id, message_id=args.message_id)
        con.commit()
    print(f"Marked {count} inbox message(s) read.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage user scouting and inbox messages.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Initialize scouting for the current draft class.")
    setup.add_argument("--game-id")
    setup.add_argument("--draft-year", type=int)
    setup.add_argument("--reset", action="store_true")
    setup.set_defaults(func=action_setup)

    senior_setup = subparsers.add_parser("senior-bowl-setup", help="Label class years and Senior Bowl invite status.")
    senior_setup.add_argument("--draft-year", type=int)
    senior_setup.add_argument("--force", action="store_true")
    senior_setup.set_defaults(func=action_senior_bowl_setup)

    senior_process = subparsers.add_parser("senior-bowl-process", help="Process Senior Bowl scouting exposure.")
    senior_process.add_argument("--game-id")
    senior_process.add_argument("--draft-year", type=int)
    senior_process.add_argument("--seed")
    senior_process.add_argument("--force", action="store_true")
    senior_process.set_defaults(func=action_senior_bowl_process)

    assign = subparsers.add_parser("assign", help="Queue one prospect for the current scouting period.")
    assign.add_argument("--game-id")
    assign.add_argument("--draft-year", type=int)
    assign.add_argument("--season", type=int)
    assign.add_argument("--week", type=int)
    assign.add_argument("--prospect-id", type=int, required=True)
    assign.add_argument("--focus", choices=sorted(FOCUS_LABELS), default="film")
    assign.set_defaults(func=action_assign)

    assign_batch = subparsers.add_parser("assign-batch", help="Queue multiple prospects for the current scouting period.")
    assign_batch.add_argument("--game-id")
    assign_batch.add_argument("--draft-year", type=int)
    assign_batch.add_argument("--season", type=int)
    assign_batch.add_argument("--week", type=int)
    assign_batch.add_argument("--prospect-id", dest="prospect_ids", type=int, action="append", required=True)
    assign_batch.add_argument("--focus", choices=sorted(FOCUS_LABELS), default="film")
    assign_batch.set_defaults(func=action_assign_batch)

    unassign = subparsers.add_parser("unassign", help="Remove one pending scouting assignment.")
    unassign.add_argument("--game-id")
    unassign.add_argument("--draft-year", type=int)
    unassign.add_argument("--season", type=int)
    unassign.add_argument("--week", type=int)
    unassign.add_argument("--prospect-id", type=int, required=True)
    unassign.set_defaults(func=action_unassign)

    process = subparsers.add_parser("process-week", help="Process queued scouting assignments.")
    process.add_argument("--game-id")
    process.add_argument("--draft-year", type=int)
    process.add_argument("--season", type=int)
    process.add_argument("--week", type=int)
    process.add_argument("--slots", type=int, default=8)
    process.add_argument("--seed")
    process.set_defaults(func=action_process_week)

    auto = subparsers.add_parser("auto", help="Use this week's auto-assign scouting action.")
    auto.add_argument("--game-id")
    auto.add_argument("--draft-year", type=int)
    auto.add_argument("--count", type=int, default=AUTO_ASSIGN_COUNT)
    auto.set_defaults(func=action_auto)

    scout_one = subparsers.add_parser("scout-one", help="Queue one prospect for this week's specific-player scouting action.")
    scout_one.add_argument("--game-id")
    scout_one.add_argument("--draft-year", type=int)
    scout_one.add_argument("--prospect-id", type=int, required=True)
    scout_one.set_defaults(func=action_scout_one)

    random_two = subparsers.add_parser("random", help="Use this week's random-prospect cross-check scouting action.")
    random_two.add_argument("--game-id")
    random_two.add_argument("--draft-year", type=int)
    random_two.add_argument("--seed")
    random_two.set_defaults(func=action_random)

    discover = subparsers.add_parser("discover", help="Use this week's off-board discovery action.")
    discover.add_argument("--game-id")
    discover.add_argument("--draft-year", type=int)
    discover.add_argument("--seed")
    discover.set_defaults(func=action_discover)

    top30 = subparsers.add_parser("top30-visit", help="Use one Top 30 visit on a visible prospect.")
    top30.add_argument("--game-id")
    top30.add_argument("--draft-year", type=int)
    top30.add_argument("--prospect-id", type=int, required=True)
    top30.add_argument("--seed")
    top30.add_argument("--allow-after-draft", action="store_true")
    top30.set_defaults(func=action_top30_visit)

    top30_auto = subparsers.add_parser("top30-auto", help="Auto-fill remaining Top 30 visits for the user team.")
    top30_auto.add_argument("--game-id")
    top30_auto.add_argument("--draft-year", type=int)
    top30_auto.add_argument("--seed")
    top30_auto.add_argument("--visit-date", help="Record visits on this date, which must be inside the visit window.")
    top30_auto.add_argument("--include-cpu", action="store_true", help="Also auto-fill CPU-team Top 30 visit equivalents.")
    top30_auto.set_defaults(func=action_top30_auto)

    board = subparsers.add_parser("board", help="Show the visible scouting board.")
    board.add_argument("--limit", type=int, default=40)
    board.set_defaults(func=action_board)

    audit = subparsers.add_parser("audit", help="Show scouting/debug audit for tuning draft discovery and grades.")
    audit.add_argument("--game-id")
    audit.add_argument("--draft-year", type=int)
    audit.add_argument("--limit", type=int, default=12)
    audit.add_argument("--team-limit", type=int, default=32)
    audit.add_argument("--prospects-per-team", type=int, default=3)
    audit.add_argument("--json", action="store_true", help="Print JSON instead of a text summary.")
    audit.add_argument("--output", type=Path, help="Write JSON audit output to this path.")
    audit.set_defaults(func=action_audit)

    inbox = subparsers.add_parser("inbox", help="Show inbox messages.")
    inbox.add_argument("--game-id")
    inbox.add_argument("--limit", type=int, default=20)
    inbox.set_defaults(func=action_inbox)

    read = subparsers.add_parser("mark-read", help="Mark inbox messages read.")
    read.add_argument("--game-id")
    read.add_argument("--message-id", type=int)
    read.set_defaults(func=action_mark_read)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except ValueError as exc:
        print(f"Scouting action unavailable: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
