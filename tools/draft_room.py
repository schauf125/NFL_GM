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
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import select_draft_pick
import scouting as scouting_tools
import scouting_perception


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
SOURCE = "draft_room_processor"


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    select_draft_pick.ensure_all_schema(con)
    scouting_tools.ensure_schema(con)
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


def start_draft(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
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


def choose_auto_prospect(con: sqlite3.Connection, draft_year: int, pick: sqlite3.Row) -> sqlite3.Row:
    team_id = int(pick["current_team_id"])
    game_id = active_game_id(con)
    candidates = cpu_auto_candidate_rows(con, draft_year, team_id, limit=96)
    if not candidates:
        raise ValueError("No available prospects remain on the board.")

    round_number = int(pick["round"])
    scored: list[tuple[float, int, sqlite3.Row]] = []
    for row in candidates:
        base_rank = cpu_base_rank(row, game_id=game_id, draft_year=draft_year, team_id=team_id)
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
        adjusted = base_rank - bonus - premium_bonus - scouting_adjustment
        scored.append((adjusted, base_rank, row))
    scored.sort(key=lambda item: (item[0], item[1], item[2]["prospect_id"]))
    return scored[0][2]


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
        raise ValueError("No current pick is set.")
    if int(pick["is_used"] or 0):
        set_current_pick(con, args.draft_year, next_open_pick(con, args.draft_year))
        raise ValueError("Current pick had already been used; advanced the room to the next pick.")

    prospect_id = args.prospect_id
    if prospect_id is None:
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
