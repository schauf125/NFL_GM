#!/usr/bin/env python3
"""Draft room processor and UI-data exporter.

This sits above select_draft_pick.py. The selection script still owns the hard
work of turning a prospect into a normal player, contract, flex rows,
transaction rows, and cap snapshots. This module owns the draft-room state:
current pick, clock pause/resume, user-team stops, and pick skipping.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import select_draft_pick
import scouting as scouting_tools
import scouting_perception
import trade_engine


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
SOURCE = "draft_room_processor"
CONFIDENCE_RANK_PENALTY_BASE = {
    "unscouted": 34.0,
    "low": 28.0,
    "medium": 10.0,
    "high": 2.0,
    "very high": 0.0,
}
CONFIDENCE_ROUND_MULTIPLIER = {
    1: 1.0,
    2: 0.65,
    3: 0.40,
    4: 0.22,
    5: 0.12,
    6: 0.06,
    7: 0.0,
}
ROUND_ONE_PLAN_TIER_SIZE = 12
ROUND_ONE_PUBLIC_ESCAPE_RANK = 16
ROUND_ONE_PUBLIC_ESCAPE_CONFIDENCE = {"high", "very high"}
ROUND_ONE_MIN_GRADE_FLOOR = 68.0
ROUND_ONE_MIN_CEILING_FLOOR = 76.0
ROUND_TWO_MIN_GRADE_FLOOR = 62.0
ROUND_TWO_MIN_CEILING_FLOOR = 68.0
DRAFT_TRADE_LOOKAHEAD_BY_ROUND = {1: 16, 2: 9, 3: 6, 4: 5, 5: 4, 6: 3, 7: 2}
DRAFT_TRADE_CONFIDENCE_BONUS = {"very high": 16.0, "high": 8.0, "medium": 1.0, "low": -12.0, "unscouted": -22.0}
DRAFT_TRADE_PREMIUM_POSITIONS = {"QB", "OT", "EDGE", "CB", "WR"}
DRAFT_TRADE_SCORE_THRESHOLD_BY_ROUND = {1: 62.0, 2: 58.0, 3: 54.0, 4: 52.0, 5: 50.0, 6: 50.0, 7: 50.0}
DRAFT_TRADE_MAX_BY_ROUND = {1: 8, 2: 6, 3: 5, 4: 4, 5: 3, 6: 3, 7: 3}
DRAFT_TRADE_MAX_TOTAL = 24
USER_DRAFT_TRADE_MAX_PICKS = 4
EARLY_DRAFT_LOW_VALUE_POSITIONS = {"FB", "K", "P", "LS"}
EARLY_DRAFT_STRONG_CONFIDENCE = {"high", "very high"}
EARLY_DRAFT_ELITE_CONFIDENCE = {"very high"}
QB_DUPLICATE_PICK_PENALTY_BY_ROUND = {1: 95.0, 2: 95.0, 3: 90.0, 4: 72.0, 5: 42.0, 6: 20.0, 7: 8.0}
QB_ROOM_PICK_PENALTY_BY_ROUND = {1: 125.0, 2: 105.0, 3: 46.0, 4: 22.0, 5: 10.0, 6: 4.0, 7: 0.0}
QB_RECENT_INVESTMENT_BLOCK_BY_ROUND = {1: 150.0, 2: 125.0, 3: 82.0, 4: 36.0, 5: 18.0, 6: 8.0, 7: 0.0}
QB_FRANCHISE_ROOM_BLOCK_BY_ROUND = {1: 520.0, 2: 430.0, 3: 240.0, 4: 90.0, 5: 28.0, 6: 8.0, 7: 0.0}
QB_ESTABLISHED_STARTER_BLOCK_BY_ROUND = {1: 230.0, 2: 170.0, 3: 92.0, 4: 34.0, 5: 12.0, 6: 4.0, 7: 0.0}
QB_SAME_OFFSEASON_FA_BLOCK_BY_ROUND = {1: 420.0, 2: 260.0, 3: 92.0, 4: 24.0, 5: 6.0, 6: 0.0, 7: 0.0}
QB_CROWDED_ROOM_BLOCK_BY_ROUND = {1: 280.0, 2: 185.0, 3: 68.0, 4: 18.0, 5: 4.0, 6: 0.0, 7: 0.0}
QB_FRANCHISE_SEARCH_TOP10_BONUS = 92.0
QB_FRANCHISE_SEARCH_TOP5_BONUS = 122.0


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_schema(con: sqlite3.Connection) -> None:
    select_draft_pick.ensure_all_schema(con)
    scouting_tools.ensure_schema(con)
    trade_engine.ensure_schema(con)
    trade_engine.seed_charts(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS draft_room_state (
            draft_year INTEGER PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'not_started',
            current_pick_id INTEGER REFERENCES draft_picks(pick_id) ON DELETE SET NULL,
            current_pick_number INTEGER,
            current_round INTEGER,
            current_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            user_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            clock_status TEXT NOT NULL DEFAULT 'paused',
            seconds_remaining INTEGER NOT NULL DEFAULT 600,
            round1_seconds INTEGER NOT NULL DEFAULT 600,
            day2_seconds INTEGER NOT NULL DEFAULT 420,
            day3_seconds INTEGER NOT NULL DEFAULT 300,
            pick_started_at TEXT,
            started_at TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS draft_room_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_year INTEGER NOT NULL,
            pick_id INTEGER REFERENCES draft_picks(pick_id) ON DELETE SET NULL,
            pick_number INTEGER,
            round INTEGER,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            prospect_id INTEGER REFERENCES draft_prospects(prospect_id) ON DELETE SET NULL,
            player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_draft_room_events_year
            ON draft_room_events(draft_year, created_at, event_id);

        DROP VIEW IF EXISTS draft_room_state_view;
        CREATE VIEW draft_room_state_view AS
        SELECT
            s.draft_year,
            s.status,
            s.clock_status,
            s.seconds_remaining,
            s.current_pick_id,
            s.current_pick_number,
            s.current_round,
            s.current_team_id,
            current_team.abbreviation AS current_team,
            current_team.city || ' ' || current_team.nickname AS current_team_name,
            s.user_team_id,
            user_team.abbreviation AS user_team,
            user_team.city || ' ' || user_team.nickname AS user_team_name,
            s.started_at,
            s.pick_started_at,
            s.updated_at,
            s.completed_at,
            s.notes
        FROM draft_room_state s
        LEFT JOIN teams current_team ON current_team.team_id = s.current_team_id
        LEFT JOIN teams user_team ON user_team.team_id = s.user_team_id;

        DROP VIEW IF EXISTS draft_room_pick_queue_view;
        CREATE VIEW draft_room_pick_queue_view AS
        WITH ordered AS (
            SELECT
                dp.*,
                ROW_NUMBER() OVER (
                    PARTITION BY dp.draft_year
                    ORDER BY dp.round, COALESCE(dp.pick_number, dp.pick_id), dp.pick_id
                ) AS effective_pick_number,
                ROW_NUMBER() OVER (
                    PARTITION BY dp.draft_year, dp.round
                    ORDER BY COALESCE(dp.pick_number, dp.pick_id), dp.pick_id
                ) AS effective_pick_in_round
            FROM draft_picks dp
        )
        SELECT
            dp.pick_id,
            dp.draft_year,
            dp.round,
            dp.pick_number,
            dp.pick_in_round,
            dp.effective_pick_number,
            dp.effective_pick_in_round,
            dp.is_used,
            dp.current_team_id,
            current_team.abbreviation AS current_team,
            current_team.city || ' ' || current_team.nickname AS current_team_name,
            dp.original_team_id,
            original_team.abbreviation AS original_team,
            dp.trade_note,
            dp.is_comp_pick,
            dp.selected_player_id,
            selected_prospect.prospect_id AS selected_prospect_id,
            COALESCE(selected.first_name || ' ' || selected.last_name, selected_prospect.first_name || ' ' || selected_prospect.last_name) AS selected_player_name,
            COALESCE(selected.position, selected_prospect.position) AS selected_player_position
        FROM ordered dp
        LEFT JOIN teams current_team ON current_team.team_id = dp.current_team_id
        LEFT JOIN teams original_team ON original_team.team_id = dp.original_team_id
        LEFT JOIN players selected ON selected.player_id = dp.selected_player_id
        LEFT JOIN draft_prospects selected_prospect ON selected_prospect.selected_pick_id = dp.pick_id;

        DROP VIEW IF EXISTS draft_room_board_ui_view;
        CREATE VIEW draft_room_board_ui_view AS
        SELECT *
        FROM draft_board_view
        WHERE status = 'Available';
        """
    )


def money(value: int | None) -> str:
    if value is None:
        return "-"
    return f"${value / 1_000_000:.1f}M" if abs(value) >= 1_000_000 else f"${value:,}"


def current_game_date(con: sqlite3.Connection, draft_year: int) -> str:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
    ).fetchone()
    return str(row["setting_value"]) if row else f"{draft_year}-04-30"


def team_by_abbr(con: sqlite3.Connection, abbreviation: str | None) -> sqlite3.Row | None:
    if not abbreviation:
        return None
    row = con.execute(
        "SELECT * FROM teams WHERE abbreviation = ?",
        (abbreviation.upper(),),
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown team abbreviation: {abbreviation}")
    return row


def pick_sort_expr() -> str:
    return "round, COALESCE(pick_number, pick_id), pick_id"


def ordered_pick_cte() -> str:
    return """
        WITH ordered AS (
            SELECT
                dp.*,
                ROW_NUMBER() OVER (
                    PARTITION BY dp.draft_year
                    ORDER BY dp.round, COALESCE(dp.pick_number, dp.pick_id), dp.pick_id
                ) AS effective_pick_number,
                ROW_NUMBER() OVER (
                    PARTITION BY dp.draft_year, dp.round
                    ORDER BY COALESCE(dp.pick_number, dp.pick_id), dp.pick_id
                ) AS effective_pick_in_round
            FROM draft_picks dp
        )
    """


def effective_pick_number(pick: sqlite3.Row) -> int | None:
    if pick["pick_number"] is not None:
        return int(pick["pick_number"])
    keys = pick.keys()
    if "effective_pick_number" in keys and pick["effective_pick_number"] is not None:
        return int(pick["effective_pick_number"])
    return None


def next_open_pick(con: sqlite3.Connection, draft_year: int) -> sqlite3.Row | None:
    return con.execute(
        f"""
        {ordered_pick_cte()}
        SELECT dp.*, t.abbreviation AS current_team
        FROM ordered dp
        LEFT JOIN teams t ON t.team_id = dp.current_team_id
        WHERE dp.draft_year = ?
          AND COALESCE(dp.is_used, 0) = 0
        ORDER BY dp.effective_pick_number
        LIMIT 1
        """,
        (draft_year,),
    ).fetchone()


def finalized_order_slot_count(con: sqlite3.Connection, draft_year: int) -> int:
    row = con.execute(
        "SELECT COUNT(*) AS count FROM draft_order_slots WHERE draft_year = ?",
        (draft_year,),
    ).fetchone()
    return int(row["count"] or 0)


def validate_final_draft_order(con: sqlite3.Connection, draft_year: int) -> None:
    slot_count = finalized_order_slot_count(con, draft_year)
    if slot_count != 32:
        raise ValueError(
            f"{draft_year} draft order is not finalized ({slot_count}/32 slots). "
            "Complete the regular season and postseason before starting the draft."
        )
    mismatch = con.execute(
        """
        SELECT COUNT(*) AS count
        FROM draft_order_slots dos
        JOIN draft_picks dp
          ON dp.draft_year = dos.draft_year
         AND dp.original_team_id = dos.team_id
         AND dp.round = 1
         AND COALESCE(dp.is_comp_pick, 0) = 0
        WHERE dos.draft_year = ?
          AND (
              COALESCE(dp.pick_in_round, -1) != dos.slot
              OR COALESCE(dp.pick_number, -1) != dos.slot
          )
        """,
        (draft_year,),
    ).fetchone()
    if int(mismatch["count"] or 0):
        raise ValueError(
            f"{draft_year} draft picks do not match the finalized draft order. "
            "Rebuild the postseason draft order before starting the draft."
        )


def current_state(con: sqlite3.Connection, draft_year: int) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM draft_room_state WHERE draft_year = ?",
        (draft_year,),
    ).fetchone()


def seconds_for_round(round_number: int, round1: int, day2: int, day3: int) -> int:
    if round_number <= 1:
        return round1
    if round_number <= 3:
        return day2
    return day3


def log_event(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    event_type: str,
    message: str,
    pick: sqlite3.Row | None = None,
    prospect_id: int | None = None,
    player_id: int | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO draft_room_events (
            draft_year, pick_id, pick_number, round, team_id,
            prospect_id, player_id, event_type, message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            draft_year,
            pick["pick_id"] if pick else None,
            effective_pick_number(pick) if pick else None,
            pick["round"] if pick else None,
            pick["current_team_id"] if pick else None,
            prospect_id,
            player_id,
            event_type,
            message,
        ),
    )


def set_current_pick(con: sqlite3.Connection, draft_year: int, pick: sqlite3.Row | None) -> None:
    state = current_state(con, draft_year)
    if not state:
        raise ValueError(f"Draft room has not been started for {draft_year}.")

    if not pick:
        udfa_results = select_draft_pick.convert_undrafted_available_prospects(con, draft_year)
        cap_cleanup: dict[str, int] = {}
        try:
            import free_agency_processor

            user_team = None
            if table_exists(con, "active_game_save_view"):
                user_row = con.execute("SELECT user_team FROM active_game_save_view LIMIT 1").fetchone()
                user_team = str(user_row["user_team"]) if user_row and user_row["user_team"] else None
            cap_cleanup = free_agency_processor.cpu_cap_compliance_sweep(
                con,
                draft_year,
                user_team=user_team,
                min_space=1_000_000,
                max_moves_per_team=4,
                max_teams=32,
                time_budget_seconds=12.0,
            )
        except Exception as exc:
            cap_cleanup = {"error": str(exc)}
        con.execute(
            """
            UPDATE draft_room_state
            SET status = 'complete',
                current_pick_id = NULL,
                current_pick_number = NULL,
                current_round = NULL,
                current_team_id = NULL,
                clock_status = 'paused',
                seconds_remaining = 0,
                completed_at = COALESCE(completed_at, datetime('now')),
                updated_at = datetime('now')
            WHERE draft_year = ?
            """,
            (draft_year,),
        )
        log_event(
            con,
            draft_year=draft_year,
            event_type="draft_complete",
            message=f"The {draft_year} draft is complete.",
        )
        if cap_cleanup:
            if "error" in cap_cleanup:
                message = f"Post-draft cap compliance sweep skipped: {cap_cleanup['error']}."
            else:
                message = (
                    "Post-draft cap compliance sweep: "
                    f"{cap_cleanup.get('teams', 0)} team(s), "
                    f"{cap_cleanup.get('restructures', 0)} restructure(s), "
                    f"{cap_cleanup.get('releases', 0)} release(s), "
                    f"{cap_cleanup.get('still_over', 0)} team(s) still over target."
                )
            log_event(
                con,
                draft_year=draft_year,
                event_type="post_draft_cap_compliance",
                message=message,
            )
        if udfa_results:
            top_names = ", ".join(
                f"{row['player_name']} ({row['position']})"
                for row in udfa_results[:5]
            )
            extra = "..." if len(udfa_results) > 5 else ""
            log_event(
                con,
                draft_year=draft_year,
                event_type="udfa_pool_created",
                message=(
                    f"{len(udfa_results)} undrafted prospect(s) entered the free-agent pool. "
                    f"Top names: {top_names}{extra}"
                ),
            )
        return

    seconds = seconds_for_round(
        int(pick["round"]),
        int(state["round1_seconds"]),
        int(state["day2_seconds"]),
        int(state["day3_seconds"]),
    )
    con.execute(
        """
        UPDATE draft_room_state
        SET status = 'active',
            current_pick_id = ?,
            current_pick_number = ?,
            current_round = ?,
            current_team_id = ?,
            seconds_remaining = ?,
            pick_started_at = datetime('now'),
            updated_at = datetime('now')
        WHERE draft_year = ?
        """,
        (
            pick["pick_id"],
            effective_pick_number(pick),
            pick["round"],
            pick["current_team_id"],
            seconds,
            draft_year,
        ),
    )


def complete_draft_if_no_open_picks(con: sqlite3.Connection, draft_year: int) -> bool:
    state = current_state(con, draft_year)
    if not state or state["status"] == "complete":
        return False
    if next_open_pick(con, draft_year) is not None:
        return False
    set_current_pick(con, draft_year, None)
    return True


def start_draft(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    validate_final_draft_order(con, args.draft_year)
    scouting_tools.run_pre_draft_public_scouting_sweep(
        con,
        draft_year=args.draft_year,
        seed=f"draft-room-start:{args.draft_year}",
    )
    user_team = team_by_abbr(con, args.user_team)
    pick = next_open_pick(con, args.draft_year)
    if not pick:
        raise ValueError(f"No open picks found for {args.draft_year}.")

    con.execute(
        """
        INSERT INTO draft_room_state (
            draft_year, status, user_team_id, clock_status,
            round1_seconds, day2_seconds, day3_seconds, started_at, updated_at, notes
        )
        VALUES (?, 'active', ?, ?, ?, ?, ?, datetime('now'), datetime('now'), ?)
        ON CONFLICT(draft_year) DO UPDATE SET
            status = 'active',
            user_team_id = excluded.user_team_id,
            clock_status = excluded.clock_status,
            round1_seconds = excluded.round1_seconds,
            day2_seconds = excluded.day2_seconds,
            day3_seconds = excluded.day3_seconds,
            completed_at = NULL,
            updated_at = datetime('now'),
            notes = excluded.notes
        """,
        (
            args.draft_year,
            user_team["team_id"] if user_team else None,
            "paused" if args.paused else "running",
            args.round1_seconds,
            args.day2_seconds,
            args.day3_seconds,
            args.notes,
        ),
    )
    set_current_pick(con, args.draft_year, pick)
    log_event(
        con,
        draft_year=args.draft_year,
        event_type="draft_started",
        pick=pick,
        message=f"{args.draft_year} draft room started at pick {effective_pick_number(pick)} ({pick['current_team']}).",
    )


def print_status(con: sqlite3.Connection, draft_year: int) -> None:
    ensure_schema(con)
    row = con.execute(
        "SELECT * FROM draft_room_state_view WHERE draft_year = ?",
        (draft_year,),
    ).fetchone()
    if not row:
        print(f"No draft room state exists for {draft_year}. Run setup/start first.")
        return
    print(f"{draft_year} Draft Room")
    print(f"Status: {row['status']} | Clock: {row['clock_status']} ({row['seconds_remaining']}s)")
    if row["current_pick_id"]:
        print(
            f"Current pick: #{row['current_pick_number']} R{row['current_round']} "
            f"{row['current_team']} ({row['current_team_name']})"
        )
    if row["user_team"]:
        print(f"User team stop: {row['user_team']} ({row['user_team_name']})")
    events = con.execute(
        """
        SELECT event_type, message, created_at
        FROM draft_room_events
        WHERE draft_year = ?
        ORDER BY event_id DESC
        LIMIT 6
        """,
        (draft_year,),
    ).fetchall()
    for event in reversed(events):
        print(f"- {event['created_at']} [{event['event_type']}] {event['message']}")


def board_rows(con: sqlite3.Connection, draft_year: int, limit: int, position: str | None = None) -> list[sqlite3.Row]:
    filters = ["draft_year = ?", "status = 'Available'"]
    params: list[Any] = [draft_year]
    if position:
        filters.append("position = ?")
        params.append(position.upper())
    return con.execute(
        f"""
        SELECT *
        FROM draft_room_board_ui_view
        WHERE {' AND '.join(filters)}
        ORDER BY COALESCE(public_board_rank, scouting_rank, 9999), prospect_id
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()


POSITION_TARGETS = {
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


def position_need_bonus(con: sqlite3.Connection, team_id: int, position: str) -> int:
    target = POSITION_TARGETS.get(position.upper(), 3)
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


def qb_room_summary(con: sqlite3.Connection, team_id: int) -> dict[str, Any]:
    qbs = con.execute(
        """
        SELECT
            player_id,
            first_name,
            last_name,
            age,
            years_exp,
            overall,
            potential,
            COALESCE(dev_trait, '') AS trait
        FROM players
        WHERE team_id = ?
          AND position = 'QB'
          AND status IN ('Active', 'Out', 'Reserve/Future', 'Practice Squad', 'PUP', 'IR')
        ORDER BY
            COALESCE(overall, 0) DESC,
            COALESCE(potential, 0) DESC,
            player_id
        LIMIT 5
        """,
        (team_id,),
    ).fetchall()
    best_overall = max((int(qb["overall"] or 0) for qb in qbs), default=0)
    best_potential = max((int(qb["potential"] or 0) for qb in qbs), default=0)
    best_young_potential = max(
        (
            int(qb["potential"] or 0)
            for qb in qbs
            if int(qb["age"] or 99) <= 27 or int(qb["years_exp"] or 99) <= 4
        ),
        default=0,
    )
    franchise_qb = best_overall >= 86 or (best_overall >= 82 and best_potential >= 88)
    young_franchise_qb = best_young_potential >= 88
    recent_high_investment = any(
        int(qb["years_exp"] or 99) <= 3
        and (
            int(qb["potential"] or 0) >= 85
            or (int(qb["overall"] or 0) >= 78 and int(qb["potential"] or 0) >= 83)
        )
        for qb in qbs
    )
    unresolved = not franchise_qb and not young_franchise_qb and not recent_high_investment
    urgent = unresolved and (best_overall < 76 or best_potential < 82)
    return {
        "qbs": qbs,
        "best_overall": best_overall,
        "best_potential": best_potential,
        "best_young_potential": best_young_potential,
        "franchise_qb": franchise_qb,
        "young_franchise_qb": young_franchise_qb,
        "recent_high_investment": recent_high_investment,
        "unresolved": unresolved,
        "urgent": urgent,
    }


def franchise_qb_search_bonus(
    con: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    team_id: int,
    round_number: int,
    overall_pick_number: int | None,
    base_rank: int,
    perceived_grade: float,
    perceived_ceiling: float,
) -> float:
    """Make top-10 teams without an answer at QB act like QB-needy NFL teams."""
    if str(row["position"] or "").upper() != "QB" or round_number > 2:
        return 0.0
    pick_number = overall_pick_number or ((max(1, round_number) - 1) * 32) + 16
    room = qb_room_summary(con, team_id)
    if not room["unresolved"]:
        return 0.0

    viable_round_one_target = (
        base_rank <= 24
        and perceived_grade >= 72
        and perceived_ceiling >= 84
    )
    premium_franchise_target = (
        base_rank <= 12
        and perceived_grade >= 76
        and perceived_ceiling >= 88
    )
    if not viable_round_one_target and not (round_number == 2 and perceived_ceiling >= 86 and perceived_grade >= 68):
        return 0.0

    bonus = 0.0
    if round_number == 1:
        if pick_number <= 5:
            bonus = QB_FRANCHISE_SEARCH_TOP5_BONUS
        elif pick_number <= 10:
            bonus = QB_FRANCHISE_SEARCH_TOP10_BONUS
        elif pick_number <= 15:
            bonus = 78.0
        elif pick_number <= 20:
            bonus = 54.0
        else:
            bonus = 28.0
    elif round_number == 2:
        bonus = 22.0

    if room["urgent"]:
        bonus *= 1.25
    if premium_franchise_target:
        bonus *= 1.22
    if perceived_grade >= 79 and perceived_ceiling >= 90:
        bonus *= 1.12
    if room["best_overall"] >= 78 and room["best_potential"] >= 84:
        bonus *= 0.65
    return bonus


def team_abbr(con: sqlite3.Connection, team_id: int | None) -> str:
    if team_id is None:
        return "?"
    row = con.execute("SELECT abbreviation FROM teams WHERE team_id = ?", (int(team_id),)).fetchone()
    return str(row["abbreviation"]) if row else f"TEAM{team_id}"


def active_game_id(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'active_game_id'"
    ).fetchone()
    if row and row["setting_value"]:
        return str(row["setting_value"])
    if select_draft_pick.active_game_id(con):
        return str(select_draft_pick.active_game_id(con))
    return "default"


def cpu_auto_candidate_rows(
    con: sqlite3.Connection,
    draft_year: int,
    team_id: int,
    *,
    limit: int = 80,
) -> list[sqlite3.Row]:
    game_id = active_game_id(con)
    return con.execute(
        """
        SELECT
            dp.*,
            CASE
              WHEN COALESCE(dp.public_board_status, 'public_board') = 'off_public_board'
                   AND (
                        COALESCE(csp.visibility_status, '') = 'discovered'
                        OR COALESCE(dp.discovery_status, '') = 'discovered'
                   )
              THEN CAST(MAX(40, MIN(260, ROUND(
                    252
                    - ((COALESCE(dp.scout_grade, dp.true_grade, dp.overall, 50) - 50) * 4.6)
                    - (MAX(COALESCE(dp.scout_ceiling, dp.ceiling_grade, dp.potential, 50) - 70, 0) * 1.8)
                    - (COALESCE(csp.scouting_level, 15) * 0.12)
              ))) AS INTEGER)
              ELSE COALESCE(dp.public_board_rank, dp.scouting_rank)
            END AS board_rank,
            CASE
              WHEN COALESCE(dp.public_board_status, 'public_board') = 'off_public_board'
                   AND (
                        COALESCE(csp.visibility_status, '') = 'discovered'
                        OR COALESCE(dp.discovery_status, '') = 'discovered'
                   )
              THEN COALESCE(csp.scouting_level, 15)
              ELSE COALESCE(csp.scouting_level, 0)
            END AS cpu_scouting_level,
            CASE
              WHEN COALESCE(dp.public_board_status, 'public_board') = 'off_public_board'
                   AND (
                        COALESCE(csp.visibility_status, '') = 'discovered'
                        OR COALESCE(dp.discovery_status, '') = 'discovered'
                   )
              THEN COALESCE(csp.scouting_confidence, 'Low')
              ELSE COALESCE(csp.scouting_confidence, 'Unscouted')
            END AS cpu_scouting_confidence,
            COALESCE(csp.times_scouted, 0) AS cpu_times_scouted,
            CASE
              WHEN COALESCE(csp.visibility_status, '') = 'discovered'
                   OR COALESCE(dp.discovery_status, '') = 'discovered'
              THEN 'discovered'
              ELSE csp.visibility_status
            END AS cpu_visibility_status
        FROM draft_prospects dp
        JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
        LEFT JOIN cpu_scouting_prospect_progress csp
          ON csp.prospect_id = dp.prospect_id
         AND csp.game_id = ?
         AND csp.draft_year = ?
         AND csp.team_id = ?
        WHERE dc.draft_year = ?
          AND dp.status = 'Available'
          AND (
                COALESCE(dp.public_board_status, 'public_board') <> 'off_public_board'
                OR csp.visibility_status = 'discovered'
                OR COALESCE(dp.discovery_status, '') = 'discovered'
              )
        ORDER BY
            CASE WHEN board_rank IS NULL THEN 999 ELSE board_rank END,
            dp.prospect_id
        LIMIT ?
        """,
        (game_id, draft_year, team_id, draft_year, limit),
    ).fetchall()


def cpu_confidence_weight(row: sqlite3.Row) -> float:
    return scouting_perception.confidence_weight(
        str(row["cpu_scouting_confidence"] or "Unscouted"),
        int(row["cpu_scouting_level"] or 0),
    )


def cpu_confidence_rank_penalty(row: sqlite3.Row, round_number: int) -> float:
    confidence = str(row["cpu_scouting_confidence"] or "Unscouted").strip().lower()
    base = CONFIDENCE_RANK_PENALTY_BASE.get(confidence, 10.0)
    multiplier = CONFIDENCE_ROUND_MULTIPLIER.get(max(1, min(7, round_number)), 0.0)
    return base * multiplier


def row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


def row_float(row: sqlite3.Row, key: str, default: float = 0.0) -> float:
    value = row_value(row, key, None)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def cpu_early_round_value_penalty(
    row: sqlite3.Row,
    *,
    round_number: int,
    overall_pick_number: int | None,
    base_rank: int,
    perceived_grade: float,
    perceived_ceiling: float,
) -> float:
    """Add early-draft guardrails without making later rounds too sterile."""
    position = str(row["position"] or "").upper()
    confidence = str(row["cpu_scouting_confidence"] or "Unscouted").strip().lower()
    pick_number = overall_pick_number or ((max(1, round_number) - 1) * 32) + 16
    true_rank_value = row_value(row, "true_rank", None)
    true_rank = int(row_float(row, "true_rank", 0)) if true_rank_value is not None else None
    true_grade = row_float(row, "true_grade", row_float(row, "overall", perceived_grade))
    potential = row_float(row, "potential", row_float(row, "ceiling_grade", perceived_ceiling))
    true_ceiling = row_float(row, "ceiling_grade", potential)
    upside_gap = max(0.0, perceived_ceiling - perceived_grade)
    wild_ceiling_miss = perceived_ceiling - true_ceiling >= 16 and true_ceiling < 72
    penalty = 0.0

    if position in EARLY_DRAFT_LOW_VALUE_POSITIONS:
        if round_number == 1:
            penalty += 95.0 if position == "FB" else 130.0
        elif round_number == 2:
            penalty += 32.0 if position == "FB" else 55.0
        elif round_number == 3:
            penalty += 12.0 if position == "FB" else 24.0

    if position == "QB":
        if round_number == 1:
            if perceived_ceiling < 82:
                penalty += 58.0
            if perceived_grade < 68:
                penalty += 26.0
            if base_rank > 40 and confidence not in EARLY_DRAFT_STRONG_CONFIDENCE:
                penalty += 20.0
        elif round_number == 2:
            if perceived_ceiling < 80:
                penalty += 32.0
            if perceived_grade < 64:
                penalty += 16.0
        elif round_number == 3 and perceived_ceiling < 76 and perceived_grade < 62:
            penalty += 10.0

    if round_number == 1:
        early = pick_number <= 16
        late = pick_number >= 25
        if perceived_grade < ROUND_ONE_MIN_GRADE_FLOOR:
            penalty += (92.0 if early else 58.0) + ((ROUND_ONE_MIN_GRADE_FLOOR - perceived_grade) * 3.6)
        elif perceived_grade < 70.0 and early:
            penalty += 18.0
        if perceived_ceiling < ROUND_ONE_MIN_CEILING_FLOOR:
            penalty += (78.0 if early else 48.0) + ((ROUND_ONE_MIN_CEILING_FLOOR - perceived_ceiling) * 3.0)
        elif perceived_ceiling < 80.0 and early:
            penalty += 16.0
        if confidence in {"unscouted", "low"}:
            if base_rank > 40:
                penalty += 38.0 if early else 24.0
            if perceived_grade < 64:
                penalty += 34.0 if early else 20.0
            if upside_gap >= 18:
                penalty += 10.0
        elif confidence == "medium":
            if base_rank > 48 and early:
                penalty += 18.0
            if perceived_grade < 61:
                penalty += 20.0 if early else 10.0

        if true_rank is not None and true_rank > 80 and confidence not in EARLY_DRAFT_ELITE_CONFIDENCE:
            penalty += 62.0 if early else 34.0
        elif true_rank is not None and true_rank > 55 and confidence not in EARLY_DRAFT_STRONG_CONFIDENCE:
            penalty += 30.0 if early else 14.0
        elif true_rank is not None and true_rank > 40 and early and confidence not in EARLY_DRAFT_STRONG_CONFIDENCE:
            penalty += 16.0

        if true_grade < 60 and potential >= 82:
            penalty += 36.0 if early else 18.0
        elif true_grade < 63 and potential >= 86 and confidence not in EARLY_DRAFT_STRONG_CONFIDENCE:
            penalty += 18.0 if early else 8.0

        if late and confidence in EARLY_DRAFT_STRONG_CONFIDENCE:
            penalty *= 0.72
        if true_rank is not None and true_rank > 95 and true_grade < 58:
            penalty += 30.0 if early else 18.0
    elif round_number == 2:
        if position in EARLY_DRAFT_LOW_VALUE_POSITIONS:
            return penalty
        if perceived_grade < ROUND_TWO_MIN_GRADE_FLOOR:
            penalty += 52.0 + ((ROUND_TWO_MIN_GRADE_FLOOR - perceived_grade) * 2.8)
        if perceived_ceiling < ROUND_TWO_MIN_CEILING_FLOOR:
            penalty += 38.0 + ((ROUND_TWO_MIN_CEILING_FLOOR - perceived_ceiling) * 2.2)
        if confidence in EARLY_DRAFT_STRONG_CONFIDENCE and wild_ceiling_miss:
            penalty += 34.0
        if confidence in {"unscouted", "low"} and base_rank > 80:
            penalty += 12.0
        if true_rank is not None and true_rank > 115 and true_grade < 60 and confidence not in EARLY_DRAFT_STRONG_CONFIDENCE:
            penalty += 18.0
        if true_rank is not None and true_rank > 125 and confidence not in EARLY_DRAFT_STRONG_CONFIDENCE:
            penalty += 18.0
        if true_grade < 57 and potential >= 82 and confidence != "very high":
            penalty += 12.0
        if confidence in EARLY_DRAFT_STRONG_CONFIDENCE:
            if perceived_grade < 63 and perceived_ceiling < 70:
                penalty += 64.0
            elif perceived_grade < 66 and perceived_ceiling < 73:
                penalty += 38.0
    elif round_number == 3:
        if position in EARLY_DRAFT_LOW_VALUE_POSITIONS:
            penalty += 4.0
        if confidence in EARLY_DRAFT_STRONG_CONFIDENCE and wild_ceiling_miss:
            penalty += 18.0
        if confidence in EARLY_DRAFT_STRONG_CONFIDENCE:
            if perceived_grade < 60 and perceived_ceiling < 66:
                penalty += 72.0
            elif perceived_grade < 62 and perceived_ceiling < 70:
                penalty += 34.0
        elif confidence == "medium" and perceived_grade < 58 and perceived_ceiling < 66:
            penalty += 18.0

    if round_number <= 3 and confidence in EARLY_DRAFT_STRONG_CONFIDENCE:
        known_low_floor = perceived_grade < 60 and perceived_ceiling < 68
        known_depth_only = perceived_grade < 64 and perceived_ceiling < 70 and base_rank > pick_number + 24
        if known_low_floor:
            penalty += {1: 180.0, 2: 115.0, 3: 62.0}.get(round_number, 0.0)
        elif known_depth_only:
            penalty += {1: 92.0, 2: 56.0, 3: 24.0}.get(round_number, 0.0)

    return penalty


def drafted_position_counts_before(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    team_id: int,
    before_pick_number: int,
) -> dict[str, int]:
    rows = con.execute(
        f"""
        {ordered_pick_cte()}
        SELECT p.position, COUNT(*) AS count
        FROM ordered dp
        JOIN players p ON p.player_id = dp.selected_player_id
        WHERE dp.draft_year = ?
          AND dp.current_team_id = ?
          AND COALESCE(dp.is_used, 0) = 1
          AND dp.effective_pick_number < ?
        GROUP BY p.position
        """,
        (draft_year, team_id, before_pick_number),
    ).fetchall()
    return {str(row["position"] or "").upper(): int(row["count"] or 0) for row in rows}


def drafted_qb_investments_before(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    team_id: int,
    before_pick_number: int,
) -> list[sqlite3.Row]:
    return con.execute(
        f"""
        {ordered_pick_cte()}
        SELECT
            dp.effective_pick_number,
            dp.round,
            p.player_id,
            p.first_name,
            p.last_name,
            COALESCE(p.overall, 0) AS overall,
            COALESCE(p.potential, 0) AS potential
        FROM ordered dp
        JOIN players p ON p.player_id = dp.selected_player_id
        WHERE dp.draft_year = ?
          AND dp.current_team_id = ?
          AND COALESCE(dp.is_used, 0) = 1
          AND dp.effective_pick_number < ?
          AND p.position = 'QB'
        ORDER BY dp.effective_pick_number
        """,
        (draft_year, team_id, before_pick_number),
    ).fetchall()


def cpu_duplicate_position_penalty(
    row: sqlite3.Row,
    *,
    round_number: int,
    base_rank: int,
    overall_pick_number: int,
    perceived_grade: float,
    perceived_ceiling: float,
    selected_counts: dict[str, int],
    qb_investments: list[sqlite3.Row] | None = None,
) -> float:
    position = str(row["position"] or "").upper()
    if position != "QB" or selected_counts.get("QB", 0) <= 0:
        return 0.0
    penalty = QB_DUPLICATE_PICK_PENALTY_BY_ROUND.get(max(1, min(7, round_number)), 20.0)
    qb_investments = qb_investments or []
    premium_qb_taken = any(
        int(qb["round"] or 7) <= 3
        and (int(qb["overall"] or 0) >= 66 or int(qb["potential"] or 0) >= 78)
        for qb in qb_investments
    )
    developmental_qb_taken = any(int(qb["potential"] or 0) >= 70 for qb in qb_investments)
    exceptional_value = (
        perceived_grade >= 76
        and perceived_ceiling >= 88
        and base_rank <= max(1, overall_pick_number - 35)
    )
    if premium_qb_taken:
        penalty += {1: 260.0, 2: 220.0, 3: 170.0, 4: 120.0, 5: 92.0, 6: 72.0, 7: 54.0}.get(
            max(1, min(7, round_number)),
            54.0,
        )
    elif developmental_qb_taken:
        penalty += {1: 130.0, 2: 110.0, 3: 82.0, 4: 58.0, 5: 38.0, 6: 24.0, 7: 14.0}.get(
            max(1, min(7, round_number)),
            14.0,
        )
    if exceptional_value:
        penalty *= 0.55 if premium_qb_taken else 0.35
    return penalty


def cpu_qb_room_pick_penalty(
    con: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    draft_year: int,
    team_id: int,
    round_number: int,
    overall_pick_number: int,
    base_rank: int,
    perceived_grade: float,
    perceived_ceiling: float,
) -> float:
    if str(row["position"] or "").upper() != "QB":
        return 0.0
    qbs = con.execute(
        """
        SELECT
            player_id,
            first_name,
            last_name,
            age,
            years_exp,
            overall,
            potential,
            COALESCE(dev_trait, '') AS trait
        FROM players
        WHERE team_id = ?
          AND position = 'QB'
          AND status IN ('Active', 'Out', 'Reserve/Future', 'Practice Squad', 'PUP', 'IR')
        ORDER BY
            COALESCE(overall, 0) DESC,
            COALESCE(potential, 0) DESC,
            player_id
        LIMIT 4
        """,
        (team_id,),
    ).fetchall()
    if not qbs:
        return 0.0

    best_overall = max(int(qb["overall"] or 0) for qb in qbs)
    best_potential = max(int(qb["potential"] or 0) for qb in qbs)
    viable_qbs = sum(
        1
        for qb in qbs
        if int(qb["overall"] or 0) >= 70 or int(qb["potential"] or 0) >= 74
    )
    best_young_potential = max(
        (int(qb["potential"] or 0) for qb in qbs if int(qb["age"] or 99) <= 26 or int(qb["years_exp"] or 99) <= 2),
        default=0,
    )
    best_young_core = max(
        (
            int(qb["potential"] or 0)
            for qb in qbs
            if (int(qb["age"] or 99) <= 27 or int(qb["years_exp"] or 99) <= 4)
            and int(qb["overall"] or 0) >= 80
        ),
        default=0,
    )
    established_starter = any(
        int(qb["overall"] or 0) >= 82
        and int(qb["potential"] or 0) >= 84
        and int(qb["age"] or 99) <= 33
        for qb in qbs
    )
    recent_high_investment = any(
        int(qb["years_exp"] or 99) <= 3
        and (
            int(qb["potential"] or 0) >= 85
            or (int(qb["overall"] or 0) >= 78 and int(qb["potential"] or 0) >= 83)
        )
        for qb in qbs
    )
    franchise_qb = best_overall >= 86 or (best_overall >= 82 and best_potential >= 88)
    young_franchise_qb = best_young_potential >= 88
    young_core_franchise_qb = best_young_core >= 88
    same_offseason_fa_qb = None
    if table_exists(con, "transaction_log"):
        same_offseason_fa_qb = con.execute(
            """
            SELECT
                COUNT(*) AS signings,
                MAX(COALESCE(c.aav, 0)) AS max_aav,
                MAX(COALESCE(p.overall, 0)) AS max_overall,
                MAX(COALESCE(p.potential, 0)) AS max_potential
            FROM transaction_log tl
            JOIN players p ON p.player_id = tl.player_id
            LEFT JOIN contracts c ON c.contract_id = tl.contract_id
            WHERE tl.team_id = ?
              AND tl.season = ?
              AND tl.transaction_type = 'Signing'
              AND p.position = 'QB'
              AND COALESCE(tl.source, '') = 'free_agency_processor'
            """,
            (team_id, draft_year),
        ).fetchone()

    best_young_investment = max(
        (int(qb["potential"] or 0) for qb in qbs if int(qb["years_exp"] or 99) <= 3),
        default=0,
    )
    exceptional_value = (
        perceived_grade >= 78
        and perceived_ceiling >= 92
        and base_rank <= max(1, overall_pick_number - 28)
    )
    signed_qb_aav = int(same_offseason_fa_qb["max_aav"] or 0) if same_offseason_fa_qb else 0
    signed_qb_overall = int(same_offseason_fa_qb["max_overall"] or 0) if same_offseason_fa_qb else 0
    signed_qb_potential = int(same_offseason_fa_qb["max_potential"] or 0) if same_offseason_fa_qb else 0
    meaningful_same_offseason_qb = (
        bool(same_offseason_fa_qb and int(same_offseason_fa_qb["signings"] or 0))
        and (signed_qb_aav >= 10_000_000 or signed_qb_overall >= 74 or signed_qb_potential >= 78)
    )
    crowded_viable_room = viable_qbs >= 3 and best_overall >= 75 and best_potential >= 78
    unresolved_room = (
        not franchise_qb
        and not young_franchise_qb
        and not young_core_franchise_qb
        and not recent_high_investment
        and not established_starter
    )
    premium_franchise_target = (
        round_number == 1
        and overall_pick_number <= 10
        and base_rank <= 12
        and perceived_grade >= 76
        and perceived_ceiling >= 88
    )
    bridge_same_offseason_qb = (
        meaningful_same_offseason_qb
        and signed_qb_aav < 20_000_000
        and signed_qb_overall < 76
        and signed_qb_potential < 80
    )
    if unresolved_room and premium_franchise_target and bridge_same_offseason_qb:
        meaningful_same_offseason_qb = False

    if (
        unresolved_room
        and not meaningful_same_offseason_qb
        and not crowded_viable_room
    ):
        return 0.0

    penalty = 0.0
    if meaningful_same_offseason_qb:
        fa_penalty = QB_SAME_OFFSEASON_FA_BLOCK_BY_ROUND.get(max(1, min(7, round_number)), 0.0)
        if viable_qbs >= 2:
            fa_penalty *= 1.25
        if exceptional_value and round_number >= 3:
            fa_penalty *= 0.35
        elif exceptional_value:
            fa_penalty *= 0.70
        penalty += fa_penalty

    if crowded_viable_room:
        crowd_penalty = QB_CROWDED_ROOM_BLOCK_BY_ROUND.get(max(1, min(7, round_number)), 0.0)
        if exceptional_value and round_number >= 3:
            crowd_penalty *= 0.35
        elif exceptional_value:
            crowd_penalty *= 0.65
        penalty += crowd_penalty

    if young_core_franchise_qb and not exceptional_value:
        return penalty + QB_FRANCHISE_ROOM_BLOCK_BY_ROUND.get(max(1, min(7, round_number)), 12.0)

    if established_starter and perceived_ceiling <= best_potential + 6 and perceived_grade < best_overall + 3:
        room_penalty = QB_ESTABLISHED_STARTER_BLOCK_BY_ROUND.get(max(1, min(7, round_number)), 12.0)
        if perceived_ceiling < best_potential:
            room_penalty *= 1.35
        if perceived_grade < best_overall - 10:
            room_penalty *= 1.35
    elif recent_high_investment and perceived_ceiling <= best_young_investment + 2:
        room_penalty = QB_RECENT_INVESTMENT_BLOCK_BY_ROUND.get(max(1, min(7, round_number)), 12.0)
        if perceived_grade < best_overall + 4:
            room_penalty *= 1.15
    else:
        room_penalty = QB_ROOM_PICK_PENALTY_BY_ROUND.get(max(1, min(7, round_number)), 12.0)
    if young_franchise_qb:
        room_penalty *= 1.10
    elif recent_high_investment:
        room_penalty *= 1.10
    if franchise_qb and best_overall >= 90:
        room_penalty *= 1.18

    if exceptional_value and round_number >= 3:
        room_penalty *= 0.25
    elif exceptional_value:
        room_penalty *= 0.55
    return penalty + room_penalty


def cpu_perceived_grade(row: sqlite3.Row, *, game_id: str, draft_year: int, team_id: int) -> float:
    return scouting_perception.perceived_grade(
        row,
        game_id=game_id,
        draft_year=draft_year,
        team_id=team_id,
    )


def cpu_perceived_ceiling(row: sqlite3.Row, *, game_id: str, draft_year: int, team_id: int) -> float:
    return scouting_perception.perceived_ceiling(
        row,
        game_id=game_id,
        draft_year=draft_year,
        team_id=team_id,
    )


def cpu_base_rank(
    row: sqlite3.Row,
    *,
    game_id: str,
    draft_year: int,
    team_id: int,
) -> int:
    board_rank = row["board_rank"]
    if board_rank is not None:
        return int(board_rank)
    if str(row["public_board_status"] or "") == "off_public_board" and row["cpu_visibility_status"] == "discovered":
        grade = cpu_perceived_grade(row, game_id=game_id, draft_year=draft_year, team_id=team_id)
        return max(120, min(260, round(270 - ((grade - 50) * 5.0))))
    return 9999


def latest_plan_rank_map(
    con: sqlite3.Connection,
    *,
    team_id: int,
    draft_year: int,
    game_id: str | None,
) -> dict[int, int]:
    try:
        import ai_gm_draft_planner

        row = ai_gm_draft_planner.latest_plan_row(
            con,
            team_id=team_id,
            draft_year=draft_year,
            game_id=game_id,
        )
        if not row and game_id:
            row = ai_gm_draft_planner.latest_plan_row(
                con,
                team_id=team_id,
                draft_year=draft_year,
                game_id=None,
            )
        if not row:
            return {}
        plan = json.loads(row["plan_json"])
    except Exception:
        return {}

    ranks: dict[int, int] = {}
    for index, item in enumerate(plan.get("board") or []):
        prospect_id = int(item.get("prospect_id") or 0)
        if prospect_id:
            ranks.setdefault(prospect_id, index)
    return ranks


def choose_auto_prospect(con: sqlite3.Connection, draft_year: int, pick: sqlite3.Row) -> sqlite3.Row:
    team_id = int(pick["current_team_id"])
    game_id = active_game_id(con)
    candidates = cpu_auto_candidate_rows(con, draft_year, team_id, limit=96)
    if not candidates:
        raise ValueError("No available prospects remain on the board.")

    round_number = int(pick["round"])
    overall_pick_number = effective_pick_number(pick)
    plan_ranks = latest_plan_rank_map(con, team_id=team_id, draft_year=draft_year, game_id=game_id)
    selected_counts = drafted_position_counts_before(
        con,
        draft_year=draft_year,
        team_id=team_id,
        before_pick_number=overall_pick_number,
    )
    qb_investments = drafted_qb_investments_before(
        con,
        draft_year=draft_year,
        team_id=team_id,
        before_pick_number=overall_pick_number,
    )
    scored: list[tuple[float, int, sqlite3.Row]] = []
    plan_tier_ids: set[int] = set()
    if round_number == 1 and plan_ranks:
        available_plan_rows = [
            row
            for row in candidates
            if int(row["prospect_id"]) in plan_ranks
        ]
        available_plan_rows.sort(
            key=lambda row: (
                plan_ranks[int(row["prospect_id"])],
                cpu_base_rank(row, game_id=game_id, draft_year=draft_year, team_id=team_id),
                int(row["prospect_id"]),
            )
        )
        plan_tier_ids = {
            int(row["prospect_id"])
            for row in available_plan_rows[:ROUND_ONE_PLAN_TIER_SIZE]
        }
    for row in candidates:
        base_rank = cpu_base_rank(row, game_id=game_id, draft_year=draft_year, team_id=team_id)
        confidence = str(row["cpu_scouting_confidence"] or "Unscouted").strip().lower()
        if round_number == 1:
            public_escape = base_rank <= ROUND_ONE_PUBLIC_ESCAPE_RANK and confidence in ROUND_ONE_PUBLIC_ESCAPE_CONFIDENCE
            in_plan_tier = int(row["prospect_id"]) in plan_tier_ids
            if confidence in {"unscouted", "low"} and not public_escape:
                continue
            if confidence == "medium" and not in_plan_tier and not public_escape:
                continue
        bonus = position_need_bonus(con, team_id, str(row["position"]))
        premium_bonus = 0
        if round_number <= 2 and row["position"] in {"QB", "OT", "EDGE", "CB", "WR"}:
            premium_bonus = 4
        if round_number >= 6 and row["position"] in {"K", "P", "LS"}:
            premium_bonus = 8
        public_grade = float(row["scout_grade"] or row["overall"] or row["true_grade"] or 50)
        public_ceiling = float(row["scout_ceiling"] or row["potential"] or row["ceiling_grade"] or public_grade)
        perceived_grade = cpu_perceived_grade(
            row,
            game_id=game_id,
            draft_year=draft_year,
            team_id=team_id,
        )
        perceived_ceiling = cpu_perceived_ceiling(
            row,
            game_id=game_id,
            draft_year=draft_year,
            team_id=team_id,
        )
        grade_delta = perceived_grade - public_grade
        ceiling_delta = perceived_ceiling - public_ceiling
        scouting_adjustment = (grade_delta * 2.2) + (ceiling_delta * 0.75)
        if row["cpu_visibility_status"] == "discovered" and str(row["public_board_status"] or "") == "off_public_board":
            scouting_adjustment += 8
        plan_index = plan_ranks.get(int(row["prospect_id"]))
        plan_bonus = max(0.0, 36.0 - (min(plan_index, 72) * 0.5)) if plan_index is not None else 0.0
        confidence_penalty = cpu_confidence_rank_penalty(row, round_number)
        early_value_penalty = cpu_early_round_value_penalty(
            row,
            round_number=round_number,
            overall_pick_number=overall_pick_number,
            base_rank=base_rank,
            perceived_grade=perceived_grade,
            perceived_ceiling=perceived_ceiling,
        )
        duplicate_position_penalty = cpu_duplicate_position_penalty(
            row,
            round_number=round_number,
            base_rank=base_rank,
            overall_pick_number=overall_pick_number,
            perceived_grade=perceived_grade,
            perceived_ceiling=perceived_ceiling,
            selected_counts=selected_counts,
            qb_investments=qb_investments,
        )
        qb_room_penalty = cpu_qb_room_pick_penalty(
            con,
            row,
            draft_year=draft_year,
            team_id=team_id,
            round_number=round_number,
            overall_pick_number=overall_pick_number,
            base_rank=base_rank,
            perceived_grade=perceived_grade,
            perceived_ceiling=perceived_ceiling,
        )
        qb_search_bonus = franchise_qb_search_bonus(
            con,
            row,
            team_id=team_id,
            round_number=round_number,
            overall_pick_number=overall_pick_number,
            base_rank=base_rank,
            perceived_grade=perceived_grade,
            perceived_ceiling=perceived_ceiling,
        )
        adjusted = (
            base_rank
            - bonus
            - premium_bonus
            - scouting_adjustment
            - plan_bonus
            - qb_search_bonus
            + confidence_penalty
            + early_value_penalty
            + duplicate_position_penalty
            + qb_room_penalty
        )
        scored.append((adjusted, base_rank, row))
    if round_number == 1 and plan_tier_ids:
        guarded = []
        for item in scored:
            _adjusted, base_rank, row = item
            confidence = str(row["cpu_scouting_confidence"] or "Unscouted").strip().lower()
            top_public_escape = (
                base_rank <= ROUND_ONE_PUBLIC_ESCAPE_RANK
                and confidence in ROUND_ONE_PUBLIC_ESCAPE_CONFIDENCE
            )
            if int(row["prospect_id"]) in plan_tier_ids or top_public_escape:
                guarded.append(item)
        if guarded:
            scored = guarded
    if not scored:
        for row in candidates:
            base_rank = cpu_base_rank(row, game_id=game_id, draft_year=draft_year, team_id=team_id)
            perceived_grade = cpu_perceived_grade(
                row,
                game_id=game_id,
                draft_year=draft_year,
                team_id=team_id,
            )
            perceived_ceiling = cpu_perceived_ceiling(
                row,
                game_id=game_id,
                draft_year=draft_year,
                team_id=team_id,
            )
            confidence_penalty = cpu_confidence_rank_penalty(row, round_number) * 0.35
            duplicate_position_penalty = cpu_duplicate_position_penalty(
                row,
                round_number=round_number,
                base_rank=base_rank,
                overall_pick_number=overall_pick_number,
                perceived_grade=perceived_grade,
                perceived_ceiling=perceived_ceiling,
                selected_counts=selected_counts,
                qb_investments=qb_investments,
            )
            qb_room_penalty = cpu_qb_room_pick_penalty(
                con,
                row,
                draft_year=draft_year,
                team_id=team_id,
                round_number=round_number,
                overall_pick_number=overall_pick_number,
                base_rank=base_rank,
                perceived_grade=perceived_grade,
                perceived_ceiling=perceived_ceiling,
            )
            qb_search_bonus = franchise_qb_search_bonus(
                con,
                row,
                team_id=team_id,
                round_number=round_number,
                overall_pick_number=overall_pick_number,
                base_rank=base_rank,
                perceived_grade=perceived_grade,
                perceived_ceiling=perceived_ceiling,
            )
            adjusted = (
                base_rank
                - position_need_bonus(con, team_id, str(row["position"]))
                - qb_search_bonus
                - max(0.0, perceived_grade - 65.0) * 0.45
                - max(0.0, perceived_ceiling - perceived_grade) * 0.2
                + confidence_penalty
                + duplicate_position_penalty
                + qb_room_penalty
            )
            scored.append((adjusted, base_rank, row))
    if not scored:
        raise ValueError("No CPU-visible prospects remain on the board.")
    scored.sort(key=lambda item: (item[0], item[1], item[2]["prospect_id"]))
    return scored[0][2]


def pick_chart_value(con: sqlite3.Connection, pick: sqlite3.Row, chart: str) -> float:
    pick_number = effective_pick_number(pick)
    if pick_number:
        return trade_engine.pick_value(con, chart, int(pick_number))
    return trade_engine.pick_value_for_round(con, chart, int(pick["draft_year"]), int(pick["round"]), int(pick["current_team_id"] or 0))


def next_pick_for_team_after(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    team_id: int,
    after_pick_number: int,
) -> sqlite3.Row | None:
    return con.execute(
        f"""
        {ordered_pick_cte()}
        SELECT dp.*, t.abbreviation AS current_team
        FROM ordered dp
        LEFT JOIN teams t ON t.team_id = dp.current_team_id
        WHERE dp.draft_year = ?
          AND dp.current_team_id = ?
          AND COALESCE(dp.is_used, 0) = 0
          AND dp.effective_pick_number > ?
        ORDER BY dp.effective_pick_number
        LIMIT 1
        """,
        (draft_year, team_id, after_pick_number),
    ).fetchone()


def available_trade_picks_after(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    team_id: int,
    after_pick_number: int,
    limit: int = 10,
) -> list[sqlite3.Row]:
    return con.execute(
        f"""
        {ordered_pick_cte()}
        SELECT dp.*, t.abbreviation AS current_team
        FROM ordered dp
        LEFT JOIN teams t ON t.team_id = dp.current_team_id
        WHERE dp.current_team_id = ?
          AND COALESCE(dp.is_used, 0) = 0
          AND (
                dp.draft_year > ?
                OR (dp.draft_year = ? AND dp.effective_pick_number > ?)
              )
        ORDER BY dp.draft_year, dp.round, COALESCE(dp.effective_pick_number, dp.pick_number, dp.pick_id), dp.pick_id
        LIMIT ?
        """,
        (team_id, draft_year, draft_year, after_pick_number, limit),
    ).fetchall()


def draft_trade_target_for_team(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    team_id: int,
    current_pick_number: int,
    next_pick_number: int,
    round_number: int,
) -> tuple[float, sqlite3.Row | None, str]:
    lookahead = DRAFT_TRADE_LOOKAHEAD_BY_ROUND.get(max(1, min(7, round_number)), 6)
    danger_rank = min(next_pick_number - 1, current_pick_number + lookahead)
    rows = cpu_auto_candidate_rows(con, draft_year, team_id, limit=160)
    game_id = active_game_id(con)
    best: tuple[float, sqlite3.Row | None, str] = (0.0, None, "")
    for row in rows:
        board_rank = cpu_base_rank(row, game_id=game_id, draft_year=draft_year, team_id=team_id)
        position = str(row["position"] or "").upper()
        need = position_need_bonus(con, team_id, position)
        confidence = str(row["cpu_scouting_confidence"] or "Unscouted").strip().lower()
        times = int(row["cpu_times_scouted"] or 0)
        if need <= 0 and position not in DRAFT_TRADE_PREMIUM_POSITIONS:
            continue
        if confidence in {"low", "unscouted"} and times <= 0:
            continue
        if round_number <= 1 and confidence not in {"high", "very high"}:
            continue
        if round_number <= 3 and confidence in {"low", "unscouted"}:
            continue
        perceived_grade = cpu_perceived_grade(row, game_id=game_id, draft_year=draft_year, team_id=team_id)
        perceived_ceiling = cpu_perceived_ceiling(row, game_id=game_id, draft_year=draft_year, team_id=team_id)
        qb_search_bonus = franchise_qb_search_bonus(
            con,
            row,
            team_id=team_id,
            round_number=round_number,
            overall_pick_number=current_pick_number,
            base_rank=board_rank,
            perceived_grade=perceived_grade,
            perceived_ceiling=perceived_ceiling,
        )
        premium_sliding_qb = (
            position == "QB"
            and qb_search_bonus >= 50.0
            and confidence in {"high", "very high"}
            and board_rank <= max(24, current_pick_number + 4)
            and perceived_grade >= 74
            and perceived_ceiling >= 86
        )
        if not premium_sliding_qb and (board_rank < current_pick_number - 4 or board_rank > danger_rank):
            continue
        qb_room_penalty = cpu_qb_room_pick_penalty(
            con,
            row,
            draft_year=draft_year,
            team_id=team_id,
            round_number=round_number,
            overall_pick_number=current_pick_number,
            base_rank=board_rank,
            perceived_grade=perceived_grade,
            perceived_ceiling=perceived_ceiling,
        )
        if qb_room_penalty >= 90:
            continue
        early_value_penalty = cpu_early_round_value_penalty(
            row,
            round_number=round_number,
            overall_pick_number=current_pick_number,
            base_rank=board_rank,
            perceived_grade=perceived_grade,
            perceived_ceiling=perceived_ceiling,
        )
        if early_value_penalty >= 90:
            continue
        urgency = max(0, next_pick_number - board_rank)
        premium = 7.0 if position in DRAFT_TRADE_PREMIUM_POSITIONS and round_number <= 3 else 0.0
        if premium_sliding_qb:
            premium += 28.0
        hidden = 8.0 if row["cpu_visibility_status"] == "discovered" and str(row["public_board_status"] or "") == "off_public_board" else 0.0
        score = (
            need
            + DRAFT_TRADE_CONFIDENCE_BONUS.get(confidence, 0.0)
            + min(16.0, urgency * 0.85)
            + max(0.0, perceived_grade - 70.0) * 0.8
            + max(0.0, perceived_ceiling - perceived_grade) * 0.5
            + min(42.0, qb_search_bonus * 0.34)
            + premium
            + hidden
            - (qb_room_penalty * 0.25)
            - (early_value_penalty * 0.18)
        )
        if score > best[0]:
            reason = f"{position} need target, {confidence} confidence, public rank {board_rank}"
            best = (score, row, reason)
    return best


def seller_should_keep_pick(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    seller_team_id: int,
    pick: sqlite3.Row,
) -> bool:
    """Block CPU trade-downs when the team on the clock should make the obvious pick."""
    round_number = int(pick["round"])
    current_pick_number = effective_pick_number(pick) or 0
    if round_number > 1 or current_pick_number <= 0:
        return False
    try:
        best = choose_auto_prospect(con, draft_year, pick)
    except Exception:
        return False
    position = str(best["position"] or "").upper()
    room = qb_room_summary(con, seller_team_id)
    game_id = active_game_id(con)
    base_rank = cpu_base_rank(best, game_id=game_id, draft_year=draft_year, team_id=seller_team_id)
    perceived_grade = cpu_perceived_grade(best, game_id=game_id, draft_year=draft_year, team_id=seller_team_id)
    perceived_ceiling = cpu_perceived_ceiling(best, game_id=game_id, draft_year=draft_year, team_id=seller_team_id)
    confidence = str(best["cpu_scouting_confidence"] or "Unscouted").strip().lower()
    if position != "QB":
        need = position_need_bonus(con, seller_team_id, position)
        premium_position = position in DRAFT_TRADE_PREMIUM_POSITIONS or position in {"IDL", "DT", "NT", "FS", "SS", "S"}
        premium_target = (
            premium_position
            and confidence in {"high", "very high"}
            and need > 0
            and base_rank <= max(12, current_pick_number + 2)
            and perceived_grade >= 78
            and perceived_ceiling >= 84
        )
        blue_chip_target = (
            premium_position
            and confidence == "very high"
            and base_rank <= max(8, current_pick_number)
            and perceived_grade >= 80
            and perceived_ceiling >= 88
        )
        return bool(
            (current_pick_number <= 12 and (premium_target or blue_chip_target))
            or (current_pick_number <= 20 and need >= 18 and premium_target)
        )

    if not room["unresolved"]:
        return False
    premium_target = (
        base_rank <= max(18, current_pick_number + 4)
        and perceived_grade >= 74
        and perceived_ceiling >= 86
        and confidence in {"high", "very high"}
    )
    if not premium_target:
        return False
    return bool(room["urgent"] or current_pick_number <= 15)


def build_trade_up_offer(
    con: sqlite3.Connection,
    *,
    buyer_team_id: int,
    seller_team_id: int,
    current_pick: sqlite3.Row,
    buyer_next_pick: sqlite3.Row,
) -> tuple[list[sqlite3.Row], float, float, str] | None:
    chart = trade_engine.gm_chart(con, seller_team_id)
    target_value = pick_chart_value(con, current_pick, chart)
    offered: list[sqlite3.Row] = [buyer_next_pick]
    offer_value = pick_chart_value(con, buyer_next_pick, chart)
    max_ratio = 1.32 if int(current_pick["round"]) <= 2 else 1.22
    min_ratio = 0.88 if int(current_pick["round"]) <= 3 else 0.80
    candidates = [
        row
        for row in available_trade_picks_after(
            con,
            draft_year=int(current_pick["draft_year"]),
            team_id=buyer_team_id,
            after_pick_number=effective_pick_number(current_pick) or 0,
            limit=12,
        )
        if int(row["pick_id"]) != int(buyer_next_pick["pick_id"])
    ]
    for candidate in candidates:
        if offer_value >= target_value * min_ratio:
            break
        if len(offered) >= 3:
            break
        candidate_value = pick_chart_value(con, candidate, chart)
        if offer_value + candidate_value <= target_value * max_ratio:
            offered.append(candidate)
            offer_value += candidate_value
    if offer_value < target_value * min_ratio or offer_value > target_value * max_ratio:
        return None
    summary = ", ".join(f"{row['draft_year']} R{row['round']}" for row in offered)
    return offered, offer_value, target_value, summary


def seller_trade_down_willingness(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    seller_team_id: int,
    pick: sqlite3.Row,
) -> float:
    best = choose_auto_prospect(con, draft_year, pick)
    confidence = str(best["cpu_scouting_confidence"] or "Unscouted").strip().lower()
    need = position_need_bonus(con, seller_team_id, str(best["position"] or ""))
    round_number = int(pick["round"])
    willingness = 0.0
    if need <= 0:
        willingness += 0.10
    if confidence in {"unscouted", "low"} and round_number <= 2:
        willingness += 0.08
    if str(best["position"] or "").upper() not in DRAFT_TRADE_PREMIUM_POSITIONS:
        willingness += 0.04
    return willingness


def execute_draft_pick_trade(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    seller_team_id: int,
    buyer_team_id: int,
    current_pick: sqlite3.Row,
    offered_picks: list[sqlite3.Row],
    target: sqlite3.Row,
    offer_value: float,
    target_value: float,
    reason: str,
) -> sqlite3.Row:
    buyer = team_abbr(con, buyer_team_id)
    seller = team_abbr(con, seller_team_id)
    current_pick_number = effective_pick_number(current_pick)
    con.execute(
        """
        UPDATE draft_picks
        SET current_team_id = ?,
            is_traded = 1,
            trade_note = ?
        WHERE pick_id = ?
        """,
        (
            buyer_team_id,
            f"{seller} -> {buyer}: {draft_year} draft-room trade up for {target['first_name']} {target['last_name']}.",
            int(current_pick["pick_id"]),
        ),
    )
    for pick in offered_picks:
        con.execute(
            """
            UPDATE draft_picks
            SET current_team_id = ?,
                is_traded = 1,
                trade_note = ?
            WHERE pick_id = ?
            """,
            (
                seller_team_id,
                f"{buyer} -> {seller}: compensation for {draft_year} draft-room trade up.",
                int(pick["pick_id"]),
            ),
        )
    refreshed = con.execute(
        f"""
        {ordered_pick_cte()}
        SELECT dp.*, t.abbreviation AS current_team
        FROM ordered dp
        LEFT JOIN teams t ON t.team_id = dp.current_team_id
        WHERE dp.pick_id = ?
        """,
        (int(current_pick["pick_id"]),),
    ).fetchone()
    sent_summary = ", ".join(f"{p['draft_year']} R{p['round']}" for p in offered_picks)
    log_event(
        con,
        draft_year=draft_year,
        event_type="draft_trade",
        pick=refreshed,
        prospect_id=int(target["prospect_id"]),
        message=(
            f"{buyer} traded up with {seller} to pick #{current_pick_number} for a shot at "
            f"{target['first_name']} {target['last_name']} ({target['position']}). "
            f"Sent {sent_summary} "
            f"(value {offer_value:.1f} vs {target_value:.1f}). {reason}."
        ),
    )
    return refreshed


def pick_description(pick: sqlite3.Row) -> str:
    pick_number = effective_pick_number(pick)
    if pick_number:
        return f"{pick['draft_year']} #{pick_number} (R{pick['round']})"
    return f"{pick['draft_year']} R{pick['round']}"


def pick_owner_team_id(pick: sqlite3.Row) -> int:
    return int(pick["current_team_id"] or 0)


def user_pick_trade_offer(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    user_team_id: int,
    seller_team_id: int,
    target_pick: sqlite3.Row,
    offered_pick_ids: list[int] | None = None,
) -> tuple[list[sqlite3.Row], float, float, str, float]:
    chart = trade_engine.gm_chart(con, seller_team_id)
    target_value = pick_chart_value(con, target_pick, chart)
    if target_value <= 0:
        raise ValueError("Target pick has no trade value.")

    if offered_pick_ids:
        unique_offered_pick_ids = list(dict.fromkeys(int(pick_id) for pick_id in offered_pick_ids))
        if len(unique_offered_pick_ids) > USER_DRAFT_TRADE_MAX_PICKS:
            raise ValueError(
                f"Draft pick trade offers are limited to {USER_DRAFT_TRADE_MAX_PICKS} picks."
            )
        placeholders = ",".join("?" for _ in unique_offered_pick_ids)
        offered = con.execute(
            f"""
            {ordered_pick_cte()}
            SELECT dp.*, t.abbreviation AS current_team
            FROM ordered dp
            LEFT JOIN teams t ON t.team_id = dp.current_team_id
            WHERE dp.pick_id IN ({placeholders})
            ORDER BY dp.draft_year, dp.round, COALESCE(dp.effective_pick_number, dp.pick_number, dp.pick_id), dp.pick_id
            """,
            tuple(unique_offered_pick_ids),
        ).fetchall()
        if len(offered) != len(unique_offered_pick_ids):
            raise ValueError("One or more offered picks could not be found.")
    else:
        target_number = effective_pick_number(target_pick) or 0
        offered = []
        user_next = next_pick_for_team_after(
            con,
            draft_year=draft_year,
            team_id=user_team_id,
            after_pick_number=target_number,
        )
        if user_next:
            offered.append(user_next)
        for candidate in available_trade_picks_after(
            con,
            draft_year=draft_year,
            team_id=user_team_id,
            after_pick_number=target_number,
            limit=18,
        ):
            if any(int(existing["pick_id"]) == int(candidate["pick_id"]) for existing in offered):
                continue
            if len(offered) >= USER_DRAFT_TRADE_MAX_PICKS:
                break
            current_value = sum(pick_chart_value(con, pick, chart) for pick in offered)
            if current_value >= target_value * 0.90:
                break
            offered.append(candidate)
    if not offered:
        raise ValueError("No user-owned picks are available to offer for that target.")

    seen: set[int] = set()
    clean_offered: list[sqlite3.Row] = []
    for pick in offered:
        pick_id = int(pick["pick_id"])
        if pick_id in seen:
            continue
        seen.add(pick_id)
        if int(pick["is_used"] or 0):
            raise ValueError(f"Offered pick {pick_description(pick)} has already been used.")
        if pick_owner_team_id(pick) != user_team_id:
            raise ValueError(f"Offered pick {pick_description(pick)} is not owned by the user team.")
        if pick_id == int(target_pick["pick_id"]):
            raise ValueError("Cannot offer the same pick being acquired.")
        if int(pick["draft_year"]) == draft_year:
            pick_number = effective_pick_number(pick) or 9999
            target_number = effective_pick_number(target_pick) or 0
            if pick_number <= target_number:
                raise ValueError("Offered current-year picks must come after the target pick.")
        clean_offered.append(pick)

    offer_value = sum(pick_chart_value(con, pick, chart) for pick in clean_offered)
    ratio = offer_value / target_value if target_value else 0.0
    summary = ", ".join(pick_description(pick) for pick in clean_offered)
    return clean_offered, offer_value, target_value, summary, ratio


def cpu_accepts_user_pick_trade(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    seller_team_id: int,
    target_pick: sqlite3.Row,
    offer_value: float,
    target_value: float,
) -> tuple[bool, float, str]:
    round_number = int(target_pick["round"])
    state = current_state(con, draft_year)
    current_pick_id = int(state["current_pick_id"] or 0) if state and state["current_pick_id"] is not None else 0
    on_clock = current_pick_id == int(target_pick["pick_id"])
    try:
        willingness = seller_trade_down_willingness(con, draft_year=draft_year, seller_team_id=seller_team_id, pick=target_pick)
    except Exception:
        willingness = 0.0
    floor = 0.92 - min(0.12, willingness)
    if round_number == 1:
        floor += 0.05
    elif round_number == 2:
        floor += 0.03
    if on_clock:
        floor += 0.03
    floor = max(0.80, min(1.10, floor))
    ratio = offer_value / target_value if target_value else 0.0
    if ratio >= floor:
        return True, floor, f"accepted at {ratio:.2f}x pick value"
    return False, floor, f"needs about {floor:.2f}x value; offer is {ratio:.2f}x"


def execute_user_pick_trade(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    user_team_id: int,
    target_pick: sqlite3.Row,
    offered_picks: list[sqlite3.Row],
    offer_value: float,
    target_value: float,
    acceptance_note: str,
) -> dict[str, Any]:
    seller_team_id = pick_owner_team_id(target_pick)
    buyer = team_abbr(con, user_team_id)
    seller = team_abbr(con, seller_team_id)
    con.execute(
        """
        UPDATE draft_picks
        SET current_team_id = ?,
            is_traded = 1,
            trade_note = ?
        WHERE pick_id = ?
        """,
        (
            user_team_id,
            f"{seller} -> {buyer}: user draft-room trade for {pick_description(target_pick)}.",
            int(target_pick["pick_id"]),
        ),
    )
    for pick in offered_picks:
        con.execute(
            """
            UPDATE draft_picks
            SET current_team_id = ?,
                is_traded = 1,
                trade_note = ?
            WHERE pick_id = ?
            """,
            (
                seller_team_id,
                f"{buyer} -> {seller}: user draft-room trade compensation.",
                int(pick["pick_id"]),
            ),
        )
    refreshed = con.execute(
        f"""
        {ordered_pick_cte()}
        SELECT dp.*, t.abbreviation AS current_team
        FROM ordered dp
        LEFT JOIN teams t ON t.team_id = dp.current_team_id
        WHERE dp.pick_id = ?
        """,
        (int(target_pick["pick_id"]),),
    ).fetchone()
    state = current_state(con, draft_year)
    if state and int(state["current_pick_id"] or 0) == int(target_pick["pick_id"]):
        con.execute(
            """
            UPDATE draft_room_state
            SET current_team_id = ?,
                updated_at = datetime('now')
            WHERE draft_year = ?
            """,
            (user_team_id, draft_year),
        )
    sent_summary = ", ".join(pick_description(pick) for pick in offered_picks)
    target_number = effective_pick_number(target_pick)
    log_event(
        con,
        draft_year=draft_year,
        event_type="user_draft_trade",
        pick=refreshed,
        message=(
            f"{buyer} acquired pick #{target_number} from {seller}. "
            f"Sent {sent_summary} (value {offer_value:.1f} vs {target_value:.1f}); {acceptance_note}."
        ),
    )
    return {
        "accepted": True,
        "target_pick_id": int(target_pick["pick_id"]),
        "target_pick_number": target_number,
        "seller": seller,
        "buyer": buyer,
        "offered_picks": [int(pick["pick_id"]) for pick in offered_picks],
        "offer_summary": sent_summary,
        "offer_value": round(offer_value, 2),
        "target_value": round(target_value, 2),
        "ratio": round(offer_value / target_value, 3) if target_value else 0,
        "note": acceptance_note,
    }


def propose_user_pick_trade(con: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    ensure_schema(con)
    state = current_state(con, args.draft_year)
    if not state or state["status"] not in {"active", "paused"}:
        raise ValueError(f"Draft room is not active for {args.draft_year}.")
    user_team_id = int(state["user_team_id"] or 0)
    if args.user_team:
        team = team_by_abbr(con, args.user_team)
        user_team_id = int(team["team_id"]) if team else user_team_id
    if user_team_id <= 0:
        raise ValueError("No user team is set for the draft room.")
    target_pick = con.execute(
        f"""
        {ordered_pick_cte()}
        SELECT dp.*, t.abbreviation AS current_team
        FROM ordered dp
        LEFT JOIN teams t ON t.team_id = dp.current_team_id
        WHERE dp.pick_id = ?
        """,
        (args.target_pick_id,),
    ).fetchone()
    if not target_pick:
        raise ValueError("Target pick was not found.")
    if int(target_pick["draft_year"]) != int(args.draft_year):
        raise ValueError("Target pick is not in this draft year.")
    if int(target_pick["is_used"] or 0):
        raise ValueError("Target pick has already been used.")
    seller_team_id = pick_owner_team_id(target_pick)
    if seller_team_id <= 0:
        raise ValueError("Target pick has no current owner.")
    if seller_team_id == user_team_id:
        raise ValueError("User team already owns that pick.")
    current_pick_number = int(state["current_pick_number"] or 0)
    target_pick_number = effective_pick_number(target_pick) or 9999
    if current_pick_number and target_pick_number < current_pick_number:
        raise ValueError("Cannot trade for a pick that has already passed.")
    if current_pick_number and target_pick_number > current_pick_number + int(args.max_ahead or 48):
        raise ValueError("That pick is too far away for draft-room trade talks.")

    offered, offer_value, target_value, summary, ratio = user_pick_trade_offer(
        con,
        draft_year=args.draft_year,
        user_team_id=user_team_id,
        seller_team_id=seller_team_id,
        target_pick=target_pick,
        offered_pick_ids=args.offer_pick_id,
    )
    accepted, floor, note = cpu_accepts_user_pick_trade(
        con,
        draft_year=args.draft_year,
        seller_team_id=seller_team_id,
        target_pick=target_pick,
        offer_value=offer_value,
        target_value=target_value,
    )
    if not accepted:
        log_event(
            con,
            draft_year=args.draft_year,
            event_type="user_draft_trade_rejected",
            pick=target_pick,
            message=(
                f"{team_abbr(con, seller_team_id)} rejected {team_abbr(con, user_team_id)}'s trade offer "
                f"for pick #{target_pick_number}. Sent {summary} "
                f"(value {offer_value:.1f} vs {target_value:.1f}); {note}."
            ),
        )
        return {
            "accepted": False,
            "target_pick_id": int(target_pick["pick_id"]),
            "target_pick_number": target_pick_number,
            "seller": team_abbr(con, seller_team_id),
            "offer_summary": summary,
            "offer_value": round(offer_value, 2),
            "target_value": round(target_value, 2),
            "ratio": round(ratio, 3),
            "needed_ratio": round(floor, 3),
            "note": note,
        }
    return execute_user_pick_trade(
        con,
        draft_year=args.draft_year,
        user_team_id=user_team_id,
        target_pick=target_pick,
        offered_picks=offered,
        offer_value=offer_value,
        target_value=target_value,
        acceptance_note=note,
    )


def maybe_execute_cpu_draft_trade_up(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    pick: sqlite3.Row,
) -> tuple[sqlite3.Row, int | None]:
    seller_team_id = int(pick["current_team_id"] or 0)
    if seller_team_id <= 0:
        return pick, None
    state = current_state(con, draft_year)
    user_team_id = int(state["user_team_id"] or 0) if state and state["user_team_id"] is not None else 0
    if seller_team_id == user_team_id:
        return pick, None
    if seller_should_keep_pick(con, draft_year=draft_year, seller_team_id=seller_team_id, pick=pick):
        return pick, None
    current_pick_number = effective_pick_number(pick) or 0
    round_number = int(pick["round"])
    total_trades = con.execute(
        """
        SELECT COUNT(*)
        FROM draft_room_events
        WHERE draft_year = ?
          AND event_type = 'draft_trade'
        """,
        (draft_year,),
    ).fetchone()[0]
    if int(total_trades or 0) >= DRAFT_TRADE_MAX_TOTAL:
        return pick, None
    round_trades = con.execute(
        """
        SELECT COUNT(*)
        FROM draft_room_events
        WHERE draft_year = ?
          AND event_type = 'draft_trade'
          AND round = ?
        """,
        (draft_year, round_number),
    ).fetchone()[0]
    if int(round_trades or 0) >= DRAFT_TRADE_MAX_BY_ROUND.get(max(1, min(7, round_number)), 3):
        return pick, None
    rng = random.Random(f"{active_game_id(con)}:{draft_year}:{current_pick_number}:draft-trade-up")
    buyer_candidates: list[tuple[float, int, sqlite3.Row, sqlite3.Row, str]] = []
    for team in con.execute("SELECT team_id FROM teams WHERE team_id <> ? ORDER BY team_id", (seller_team_id,)).fetchall():
        buyer_team_id = int(team["team_id"])
        if buyer_team_id == user_team_id:
            continue
        previous_trade_up = con.execute(
            """
            SELECT 1
            FROM draft_room_events
            WHERE draft_year = ?
              AND event_type = 'draft_trade'
              AND team_id = ?
            LIMIT 1
            """,
            (draft_year, buyer_team_id),
        ).fetchone()
        if previous_trade_up:
            continue
        next_pick = next_pick_for_team_after(
            con,
            draft_year=draft_year,
            team_id=buyer_team_id,
            after_pick_number=current_pick_number,
        )
        if not next_pick:
            continue
        next_pick_number = effective_pick_number(next_pick) or 999
        distance = next_pick_number - current_pick_number
        if distance <= 0 or distance > DRAFT_TRADE_LOOKAHEAD_BY_ROUND.get(max(1, min(7, round_number)), 6):
            continue
        score, target, reason = draft_trade_target_for_team(
            con,
            draft_year=draft_year,
            team_id=buyer_team_id,
            current_pick_number=current_pick_number,
            next_pick_number=next_pick_number,
            round_number=round_number,
        )
        if not target:
            continue
        score += rng.uniform(-4.0, 5.0)
        if score >= DRAFT_TRADE_SCORE_THRESHOLD_BY_ROUND.get(max(1, min(7, round_number)), 44.0):
            buyer_candidates.append((score, buyer_team_id, next_pick, target, reason))
    buyer_candidates.sort(key=lambda item: item[0], reverse=True)
    seller_willingness = seller_trade_down_willingness(con, draft_year=draft_year, seller_team_id=seller_team_id, pick=pick)
    for score, buyer_team_id, next_pick, target, reason in buyer_candidates[:5]:
        offer = build_trade_up_offer(
            con,
            buyer_team_id=buyer_team_id,
            seller_team_id=seller_team_id,
            current_pick=pick,
            buyer_next_pick=next_pick,
        )
        if not offer:
            continue
        offered_picks, offer_value, target_value, summary = offer
        ratio = offer_value / target_value if target_value else 0.0
        accept_floor = 0.92 - min(0.14, seller_willingness)
        if ratio >= accept_floor or score >= 64.0:
            traded_pick = execute_draft_pick_trade(
                con,
                draft_year=draft_year,
                seller_team_id=seller_team_id,
                buyer_team_id=buyer_team_id,
                current_pick=pick,
                offered_picks=offered_picks,
                target=target,
                offer_value=offer_value,
                target_value=target_value,
                reason=f"{reason}; compensation {summary}",
            )
            return traded_pick, int(target["prospect_id"])
    return pick, None


def run_selection(
    con: sqlite3.Connection,
    *,
    draft_year: int,
    pick: sqlite3.Row,
    prospect_id: int,
    no_cap_snapshot: bool = False,
) -> dict[str, Any]:
    args = SimpleNamespace(
        draft_year=draft_year,
        pick_id=int(pick["pick_id"]),
        team=None,
        round=None,
        pick_in_round=None,
        prospect_id=prospect_id,
        prospect=None,
        board_rank=None,
        overall_pick=effective_pick_number(pick),
        signed_date=current_game_date(con, draft_year),
        no_cap_snapshot=no_cap_snapshot,
        schema_ready=True,
    )
    return select_draft_pick.select_prospect(con, args)


def select_for_current_pick(con: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    if not getattr(args, "schema_ready", False):
        ensure_schema(con)
    state = current_state(con, args.draft_year)
    if not state or state["status"] not in {"active", "paused"}:
        raise ValueError(f"Draft room is not active for {args.draft_year}.")
    pick = con.execute(
        """
        WITH ordered AS (
            SELECT
                dp.*,
                ROW_NUMBER() OVER (
                    PARTITION BY dp.draft_year
                    ORDER BY dp.round, COALESCE(dp.pick_number, dp.pick_id), dp.pick_id
                ) AS effective_pick_number,
                ROW_NUMBER() OVER (
                    PARTITION BY dp.draft_year, dp.round
                    ORDER BY COALESCE(dp.pick_number, dp.pick_id), dp.pick_id
                ) AS effective_pick_in_round
            FROM draft_picks dp
        )
        SELECT dp.*, t.abbreviation AS current_team
        FROM ordered dp
        LEFT JOIN teams t ON t.team_id = dp.current_team_id
        WHERE dp.pick_id = ?
        """,
        (state["current_pick_id"],),
    ).fetchone()
    if not pick:
        if complete_draft_if_no_open_picks(con, args.draft_year):
            return {
                "draft_year": args.draft_year,
                "draft_complete": True,
                "message": f"The {args.draft_year} draft is complete.",
            }
        raise ValueError("No current pick is set.")
    if int(pick["is_used"] or 0):
        next_pick = next_open_pick(con, args.draft_year)
        set_current_pick(con, args.draft_year, next_pick)
        if next_pick is None:
            return {
                "draft_year": args.draft_year,
                "draft_complete": True,
                "message": f"The {args.draft_year} draft is complete.",
            }
        raise ValueError("Current pick had already been used; advanced the room to the next pick.")

    prospect_id = args.prospect_id
    if prospect_id is None:
        pick, trade_target_id = maybe_execute_cpu_draft_trade_up(con, draft_year=args.draft_year, pick=pick)
        state = current_state(con, args.draft_year)
        if state and int(state["current_pick_id"] or 0) == int(pick["pick_id"]):
            con.execute(
                """
                UPDATE draft_room_state
                SET current_team_id = ?,
                    updated_at = datetime('now')
                WHERE draft_year = ?
                """,
                (pick["current_team_id"], args.draft_year),
            )
        if trade_target_id is not None:
            prospect_id = trade_target_id
        else:
            prospect = choose_auto_prospect(con, args.draft_year, pick)
            prospect_id = int(prospect["prospect_id"])

    result = run_selection(
        con,
        draft_year=args.draft_year,
        pick=pick,
        prospect_id=int(prospect_id),
        no_cap_snapshot=args.no_cap_snapshot,
    )
    log_event(
        con,
        draft_year=args.draft_year,
        event_type="pick_made",
        pick=pick,
        prospect_id=result["prospect_id"],
        player_id=result["player_id"],
        message=(
            f"Pick #{result['effective_pick_number']}: {result['team']} selected "
            f"{result['player_name']} ({result['position']}, {result['college']})."
        ),
    )
    set_current_pick(con, args.draft_year, next_open_pick(con, args.draft_year))
    return result


def run_skip(con: sqlite3.Connection, args: argparse.Namespace) -> list[dict[str, Any]]:
    ensure_schema(con)
    results: list[dict[str, Any]] = []
    for _ in range(args.count):
        state = current_state(con, args.draft_year)
        if not state or state["status"] == "complete":
            break
        if (
            args.until_user_pick
            and state["user_team_id"] is not None
            and state["current_team_id"] == state["user_team_id"]
            and not args.include_user_pick
        ):
            break
        pick_args = SimpleNamespace(
            draft_year=args.draft_year,
            prospect_id=None,
            no_cap_snapshot=args.no_cap_snapshot,
            schema_ready=True,
        )
        results.append(select_for_current_pick(con, pick_args))
        if args.apply and getattr(args, "commit_each", False):
            con.commit()
            con.execute("BEGIN")
    complete_draft_if_no_open_picks(con, args.draft_year)
    return results


def update_clock(con: sqlite3.Connection, args: argparse.Namespace, status: str) -> None:
    ensure_schema(con)
    state = current_state(con, args.draft_year)
    if not state:
        raise ValueError(f"Draft room has not been started for {args.draft_year}.")
    con.execute(
        """
        UPDATE draft_room_state
        SET clock_status = ?,
            status = CASE WHEN status = 'active' THEN 'active' ELSE status END,
            updated_at = datetime('now')
        WHERE draft_year = ?
        """,
        (status, args.draft_year),
    )
    log_event(
        con,
        draft_year=args.draft_year,
        event_type=f"clock_{status}",
        message=f"Draft clock {status}.",
    )


def export_ui_data(con: sqlite3.Connection, draft_year: int) -> dict[str, Any]:
    ensure_schema(con)
    state = con.execute(
        "SELECT * FROM draft_room_state_view WHERE draft_year = ?",
        (draft_year,),
    ).fetchone()
    queue = con.execute(
        f"""
        SELECT *
        FROM draft_room_pick_queue_view
        WHERE draft_year = ?
        ORDER BY effective_pick_number
        LIMIT 40
        """,
        (draft_year,),
    ).fetchall()
    events = con.execute(
        """
        SELECT *
        FROM draft_room_events
        WHERE draft_year = ?
        ORDER BY event_id DESC
        LIMIT 30
        """,
        (draft_year,),
    ).fetchall()
    return {
        "draft_year": draft_year,
        "state": dict(state) if state else None,
        "board": [dict(row) for row in board_rows(con, draft_year, limit=80)],
        "pick_queue": [dict(row) for row in queue],
        "recent_events": [dict(row) for row in events],
    }


def run_mutation(con: sqlite3.Connection, args: argparse.Namespace, func) -> Any:
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
    print("Draft room schema ready.")


def action_start(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        run_mutation(con, args, start_draft)
    print(f"Draft room {'started' if args.apply else 'start dry run'} for {args.draft_year}.")
    if not args.apply:
        print("Dry run only. Add --apply to save the draft-room state.")


def action_status(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        print_status(con, args.draft_year)


def action_board(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_schema(con)
        for row in board_rows(con, args.draft_year, args.limit, args.position):
            print(
                f"{row['public_board_rank'] or row['scouting_rank'] or '-':>4} "
                f"{row['first_name']} {row['last_name']:<18} {row['position']:<4} "
                f"{row['college'] or '-':<18} grade {row['scout_grade'] or row['true_grade'] or '-'}"
            )


def action_pick(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = run_mutation(con, args, select_for_current_pick)
    select_draft_pick.print_selection(result, dry_run=not args.apply)


def action_skip(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        results = run_mutation(con, args, run_skip)
    print(f"{'Skipped' if args.apply else 'Would skip'} {len(results)} pick(s).")
    for result in results:
        if result.get("draft_complete"):
            print(f"- {result.get('message', 'Draft complete.')}")
            continue
        print(
            f"- #{result['effective_pick_number']} {result['team']}: "
            f"{result['player_name']} ({result['position']})"
        )
    if not args.apply:
        print("Dry run only. Add --apply to commit the skipped picks.")


def action_pause(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        run_mutation(con, args, lambda c, a: update_clock(c, a, "paused"))
    print(f"Draft clock pause {'saved' if args.apply else 'dry run'} for {args.draft_year}.")


def action_resume(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        run_mutation(con, args, lambda c, a: update_clock(c, a, "running"))
    print(f"Draft clock resume {'saved' if args.apply else 'dry run'} for {args.draft_year}.")


def action_ui_data(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        payload = export_ui_data(con, args.draft_year)
    text = json.dumps(payload, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(text)


def action_user_trade(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = run_mutation(con, args, propose_user_pick_trade)
    if result.get("accepted"):
        print(
            f"Trade accepted: {result['buyer']} acquired pick #{result['target_pick_number']} "
            f"from {result['seller']} for {result['offer_summary']} "
            f"({result['ratio']:.2f}x value)."
        )
    else:
        print(
            f"Trade rejected by {result['seller']}: {result['note']}. "
            f"Offer: {result['offer_summary']} ({result['ratio']:.2f}x value)."
        )
    if not args.apply:
        print("Dry run only. Add --apply to execute an accepted trade.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Create draft room tables/views.")
    setup.set_defaults(func=action_setup)

    start = subparsers.add_parser("start", help="Start or reset a draft room.")
    start.add_argument("--draft-year", type=int, required=True)
    start.add_argument("--user-team")
    start.add_argument("--round1-seconds", type=int, default=600)
    start.add_argument("--day2-seconds", type=int, default=420)
    start.add_argument("--day3-seconds", type=int, default=300)
    start.add_argument("--paused", action="store_true", help="Start with the clock paused.")
    start.add_argument("--notes")
    start.add_argument("--apply", action="store_true")
    start.set_defaults(func=action_start)

    status = subparsers.add_parser("status", help="Show draft room status.")
    status.add_argument("--draft-year", type=int, required=True)
    status.set_defaults(func=action_status)

    board = subparsers.add_parser("board", help="Show available board.")
    board.add_argument("--draft-year", type=int, required=True)
    board.add_argument("--position")
    board.add_argument("--limit", type=int, default=30)
    board.set_defaults(func=action_board)

    pick = subparsers.add_parser("pick", help="Make the current pick.")
    pick.add_argument("--draft-year", type=int, required=True)
    pick.add_argument("--prospect-id", type=int, help="Omit to auto-pick.")
    pick.add_argument("--no-cap-snapshot", action="store_true")
    pick.add_argument("--apply", action="store_true")
    pick.set_defaults(func=action_pick)

    skip = subparsers.add_parser("skip", help="Auto-pick through one or more picks.")
    skip.add_argument("--draft-year", type=int, required=True)
    skip.add_argument("--count", type=int, default=1)
    skip.add_argument("--until-user-pick", action="store_true")
    skip.add_argument("--include-user-pick", action="store_true")
    skip.add_argument("--no-cap-snapshot", action="store_true")
    skip.add_argument("--commit-each", action="store_true", help="Commit after each auto-pick so live UIs can refresh during long skips.")
    skip.add_argument("--apply", action="store_true")
    skip.set_defaults(func=action_skip)

    pause = subparsers.add_parser("pause", help="Pause the draft clock.")
    pause.add_argument("--draft-year", type=int, required=True)
    pause.add_argument("--apply", action="store_true")
    pause.set_defaults(func=action_pause)

    resume = subparsers.add_parser("resume", help="Resume the draft clock.")
    resume.add_argument("--draft-year", type=int, required=True)
    resume.add_argument("--apply", action="store_true")
    resume.set_defaults(func=action_resume)

    ui_data = subparsers.add_parser("ui-data", help="Export JSON for a draft room UI.")
    ui_data.add_argument("--draft-year", type=int, required=True)
    ui_data.add_argument("--output", type=Path)
    ui_data.set_defaults(func=action_ui_data)

    user_trade = subparsers.add_parser("user-trade", help="Offer user-owned picks to move up for a CPU-owned pick.")
    user_trade.add_argument("--draft-year", type=int, required=True)
    user_trade.add_argument("--target-pick-id", type=int, required=True)
    user_trade.add_argument("--offer-pick-id", type=int, action="append", help="Specific user pick to include. Omit to auto-package fair value.")
    user_trade.add_argument("--user-team")
    user_trade.add_argument("--max-ahead", type=int, default=48)
    user_trade.add_argument("--apply", action="store_true")
    user_trade.set_defaults(func=action_user_trade)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
