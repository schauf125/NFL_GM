"""Export data for the static Game Center UI.

The Game Center is a command cockpit for getting through seasons and offseasons.
It does not mutate the database. It reads the current save/master snapshot and
prints exact commands the user can run from the project root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import ai_gm_ops_controller as ops_controller
import ai_gm_contract_planner as contract_planner
import ai_gm_cutdown_planner as cutdown_planner
import ai_gm_draft_planner as draft_planner
import ai_gm_free_agent_planner as free_agent_planner
import ai_gm_team_evaluator as team_eval
import contract_negotiations
import export_player_profile_ui_data
import league_news
import roster_rules
import scouting
import saved_draft_class_package
from export_player_card_ui_data import POSITION_RATING_KEYS, grade_label as rating_grade_label


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "database" / "nfl_gm.db"
DEFAULT_OUTPUT = PROJECT_ROOT / "ui" / "game_center" / "game-center-data.js"
SAVE_REGISTRY = PROJECT_ROOT / "saves" / "save_registry.json"

DRAFT_POSITION_ALIASES = {
    "ILB": "LB",
    "OLB": "LB",
    "MLB": "LB",
    "WLB": "LB",
    "SLB": "LB",
    "FS": "S",
    "SS": "S",
    "DT": "IDL",
    "NT": "IDL",
    "DE": "EDGE",
    "PK": "K",
}

PUBLIC_NEED_POSITION_GROUPS = {
    "QB": "QB",
    "RB": "RB",
    "FB": "RB",
    "WR": "WR",
    "TE": "TE",
    "LT": "OT",
    "RT": "OT",
    "OT": "OT",
    "LG": "IOL",
    "RG": "IOL",
    "OG": "IOL",
    "C": "IOL",
    "OC": "IOL",
    "DE": "EDGE",
    "EDGE": "EDGE",
    "OLB": "LB",
    "ILB": "LB",
    "MLB": "LB",
    "LB": "LB",
    "DT": "IDL",
    "NT": "IDL",
    "IDL": "IDL",
    "CB": "CB",
    "FS": "S",
    "SS": "S",
    "S": "S",
    "K": "ST",
    "PK": "ST",
    "P": "ST",
    "LS": "ST",
}

PUBLIC_NEED_MIN_COUNTS = {
    "QB": 2,
    "RB": 3,
    "WR": 5,
    "TE": 3,
    "OT": 4,
    "IOL": 5,
    "EDGE": 4,
    "IDL": 4,
    "LB": 5,
    "CB": 5,
    "S": 4,
    "ST": 3,
}

PUBLIC_PREMIUM_GROUPS = {"QB", "OT", "EDGE", "CB", "WR", "IDL"}
DRAFT_BOARD_LIMIT = 512
DRAFT_BOARD_DETAIL_LIMIT = 24
DRAFT_LIGHT_ROW_DROP_FIELDS = {
    "arm_length_in",
    "hand_size_in",
    "secondary_role",
    "scouting_strengths",
    "scouting_concerns",
    "scouting_projection",
    "scouting_report",
    "medical_notes",
    "interview_notes",
    "late_process_note",
    "combine_grade",
    "drills_completed",
    "bench_press_reps",
    "three_cone_sec",
    "twenty_yard_shuttle_sec",
    "sixty_yard_shuttle_sec",
    "combine_top_skip",
    "pro_day_status",
    "pro_day_grade",
    "pro_day_athletic_score",
    "pro_day_forty_yard_dash",
    "pro_day_vertical_jump_in",
    "pro_day_broad_jump_in",
    "pro_day_improved_from_combine",
    "pro_day_medical_recheck",
    "private_workout_status",
    "private_workout_type",
    "private_workout_interest",
    "private_workout_grade",
    "private_workout_note",
}

DRAFT_COMBINE_UI_FIELDS = {
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

DRAFT_PRO_DAY_UI_FIELDS = {
    "pro_day_status",
    "pro_day_grade",
    "pro_day_athletic_score",
    "pro_day_forty_yard_dash",
    "pro_day_vertical_jump_in",
    "pro_day_broad_jump_in",
    "pro_day_improved_from_combine",
    "pro_day_medical_recheck",
}

DRAFT_PROCESS_UI_FIELDS = {
    "medical_flag",
    "medical_risk",
    "medical_notes",
    "interview_trait",
    "interview_grade",
    "interview_notes",
    "late_process_status",
    "late_process_note",
    "public_board_delta",
    "private_workout_status",
    "private_workout_type",
    "private_workout_interest",
    "private_workout_grade",
    "private_workout_note",
}


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def relation_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({name})").fetchall()}


def rows_as_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def one_as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def json_safe(value: Any) -> Any:
    if isinstance(value, sqlite3.Row):
        return {key: json_safe(value[key]) for key in value.keys()}
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return value


def parse_json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def ai_gm_review_result_summary(row: dict[str, Any]) -> str:
    result = parse_json_object(row.get("apply_result_json"))
    status = str(row.get("lifecycle_status") or "")
    if row.get("apply_error"):
        return str(row["apply_error"])
    if result.get("blocked_reason"):
        return str(result["blocked_reason"])
    if result.get("error"):
        return str(result["error"])
    if result.get("applied"):
        if row.get("artifact_type") == "decision_queue":
            proposal_count = len(result.get("trade_proposals") or [])
            action_count = len(result.get("trade_actions") or [])
            if proposal_count:
                return f"Created {proposal_count} trade proposal(s)"
            if action_count:
                actions = result.get("trade_actions") or []
                status = actions[0].get("status") if actions and isinstance(actions[0], dict) else None
                return f"Trade response {status}" if status else f"Applied {action_count} trade response(s)"
            return "Applied queued decision"
        if row.get("artifact_type") == "contract_plan":
            count = len(result.get("extensions") or result.get("signed_extensions") or [])
            return f"Applied {count} extension(s)" if count else "Applied contract plan"
        if row.get("artifact_type") == "free_agent_plan":
            count = len(result.get("offers") or result.get("submitted_offers") or [])
            return f"Submitted {count} offer(s)" if count else "Applied free-agent plan"
        if row.get("artifact_type") == "cutdown_plan":
            return "Applied roster cutdown"
        return "Applied"
    if status == "approved":
        return f"Approved by {row.get('reviewed_by') or 'user'}"
    if status == "rejected":
        return row.get("review_note") or "Rejected"
    if status == "blocked":
        return row.get("apply_error") or "Blocked"
    if status == "pending_review":
        return "Awaiting review"
    return status.replace("_", " ").title() if status else "-"


def decorate_ai_gm_review_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decorated: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        detail = parse_json_object(item.get("detail_json"))
        apply_result = parse_json_object(item.get("apply_result_json"))
        plan = detail.get("plan") if isinstance(detail.get("plan"), dict) else {}
        queue = detail.get("queue") if isinstance(detail.get("queue"), dict) else {}
        item["artifact_label"] = (
            f"{item.get('artifact_type')}#{item.get('artifact_id')}"
            if item.get("artifact_id") is not None
            else str(item.get("artifact_type") or "-")
        )
        item["activity_time"] = (
            item.get("applied_at")
            or item.get("reviewed_at")
            or item.get("updated_at")
            or item.get("created_at")
        )
        item["result_summary"] = ai_gm_review_result_summary(item)
        item["plan_validation_status"] = plan.get("validation_status") if plan else None
        item["queued_status"] = queue.get("status") if queue else None
        item["detail"] = detail
        item["apply_result"] = apply_result
        item.pop("detail_json", None)
        item.pop("apply_result_json", None)
        decorated.append(item)
    return decorated


def decorate_ai_gm_daily_runs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decorated: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["phase"] = item.get("phase_code")
        item["mode"] = item.get("autonomy_mode")
        item["planned_operations"] = item.get("operations_planned")
        item["applied_operations"] = item.get("operations_applied")
        item["queued_operations"] = item.get("operations_enqueued")
        item["blocked_operations"] = item.get("operations_blocked")
        item["scope"] = item.get("scope_team") or ("ALL" if item.get("all_teams") else "-")
        decorated.append(item)
    return decorated


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def settings(conn: sqlite3.Connection) -> dict[str, str]:
    if not table_exists(conn, "game_settings"):
        return {}
    rows = conn.execute("SELECT setting_key, setting_value FROM game_settings").fetchall()
    return {row["setting_key"]: row["setting_value"] for row in rows}


def active_save(conn: sqlite3.Connection) -> dict[str, Any] | None:
    if table_exists(conn, "active_game_save_view"):
        row = conn.execute("SELECT * FROM active_game_save_view LIMIT 1").fetchone()
        if row:
            active = dict(row)
            game_settings = settings(conn)
            setting_date = game_settings.get("current_game_date")
            if setting_date and setting_date > str(active.get("current_date") or ""):
                active["current_date"] = setting_date
                if game_settings.get("current_league_year"):
                    active["current_league_year"] = int(game_settings["current_league_year"])
                if game_settings.get("current_calendar_phase"):
                    active["current_phase_code"] = game_settings["current_calendar_phase"]
            return active
    if not table_exists(conn, "game_saves"):
        return None
    row = conn.execute(
        """
        SELECT *
        FROM game_saves
        WHERE status = 'active'
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
        """
    ).fetchone()
    active = one_as_dict(row)
    if active:
        game_settings = settings(conn)
        setting_date = game_settings.get("current_game_date")
        if setting_date and setting_date > str(active.get("current_date") or ""):
            active["current_date"] = setting_date
            if game_settings.get("current_league_year"):
                active["current_league_year"] = int(game_settings["current_league_year"])
            if game_settings.get("current_calendar_phase"):
                active["current_phase_code"] = game_settings["current_calendar_phase"]
    return active


def save_registry() -> dict[str, Any]:
    registry = read_json(
        SAVE_REGISTRY,
        {"version": 1, "active_game_id": None, "saves": {}},
    )
    active_game_id = registry.get("active_game_id") or registry.get("activeGameId")
    saves = []
    for game_id, record in sorted(registry.get("saves", {}).items()):
        saves.append(
            {
                "gameId": game_id,
                "name": record.get("name") or game_id,
                "userTeam": record.get("user_team"),
                "currentDate": record.get("current_date"),
                "phase": record.get("current_phase_code"),
                "status": record.get("status"),
                "dbPath": record.get("db_path"),
                "active": game_id == active_game_id,
            }
        )
    return {"activeGameId": active_game_id, "saves": saves}


def default_export_db() -> Path:
    registry = read_json(
        SAVE_REGISTRY,
        {"version": 1, "active_game_id": None, "saves": {}},
    )
    active_game_id = registry.get("active_game_id") or registry.get("activeGameId")
    if active_game_id:
        record = registry.get("saves", {}).get(active_game_id)
        if record and record.get("db_path"):
            path = PROJECT_ROOT / record["db_path"]
            if path.exists():
                return path
    return DEFAULT_DB


def upcoming_events(conn: sqlite3.Connection, limit: int = 18) -> list[dict[str, Any]]:
    if not table_exists(conn, "upcoming_league_events_view"):
        return []
    rows = conn.execute(
        """
        SELECT event_start_date, event_name, event_category, phase_name, event_time_et, notes
        FROM upcoming_league_events_view
        ORDER BY event_start_date, sort_order
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return rows_as_dicts(rows)


def alerts(conn: sqlite3.Connection, limit: int = 18) -> list[dict[str, Any]]:
    if not table_exists(conn, "game_alerts"):
        return []
    rows = conn.execute(
        """
        SELECT alert_date, severity, alert_type, title, message, due_date, status
        FROM game_alerts
        WHERE status = 'Open'
        ORDER BY alert_date DESC, alert_id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return rows_as_dicts(rows)


def flow_log(conn: sqlite3.Connection, limit: int = 16) -> list[dict[str, Any]]:
    if not table_exists(conn, "game_flow_log"):
        return []
    rows = conn.execute(
        """
        SELECT game_date, log_type, event_code, title, details, created_at
        FROM game_flow_log
        ORDER BY game_date DESC, log_id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return rows_as_dicts(rows)


def game_rows(
    conn: sqlite3.Connection,
    *,
    season: int,
    where_sql: str,
    params: tuple[Any, ...],
    order_sql: str,
    limit: int,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "season_games"):
        return []
    rows = conn.execute(
        f"""
        SELECT
            g.game_id,
            g.season,
            g.week,
            g.game_type,
            g.game_date,
            g.game_time_et,
            g.played,
            g.away_score,
            g.home_score,
            away.abbreviation AS away_team,
            away.city || ' ' || away.nickname AS away_team_name,
            home.abbreviation AS home_team,
            home.city || ' ' || home.nickname AS home_team_name
        FROM season_games g
        JOIN teams away ON away.team_id = g.away_team_id
        JOIN teams home ON home.team_id = g.home_team_id
        WHERE g.season = ? AND {where_sql}
        ORDER BY {order_sql}
        LIMIT ?
        """,
        (season, *params, limit),
    ).fetchall()
    return rows_as_dicts(rows)


def parse_iso_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return fallback


def calendar_bounds(current_date: str | None) -> tuple[date, date, date]:
    focus = parse_iso_date(current_date, date.today())
    first_of_month = focus.replace(day=1)
    days_since_sunday = (first_of_month.weekday() + 1) % 7
    start = first_of_month - timedelta(days=days_since_sunday)
    return focus, start, start + timedelta(days=41)


def calendar_game_rows(
    conn: sqlite3.Connection,
    *,
    season: int,
    start_date: str,
    end_date: str,
    user_team_id: int | None = None,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "season_games"):
        return []
    team_filter = ""
    params: list[Any] = [season, start_date, end_date]
    if user_team_id:
        team_filter = "AND (g.away_team_id = ? OR g.home_team_id = ?)"
        params.extend([user_team_id, user_team_id])
    logos = team_logo_map(conn)
    rows = rows_as_dicts(
        conn.execute(
            """
            SELECT
                g.game_id,
                g.season,
                g.week,
                g.game_type,
                g.game_date,
                g.game_time_et,
                g.played,
                g.away_score,
                g.home_score,
                away.abbreviation AS away_team,
                away.city || ' ' || away.nickname AS away_team_name,
                home.abbreviation AS home_team,
                home.city || ' ' || home.nickname AS home_team_name
            FROM season_games g
            JOIN teams away ON away.team_id = g.away_team_id
            JOIN teams home ON home.team_id = g.home_team_id
            WHERE g.season = ?
              AND date(g.game_date) BETWEEN date(?) AND date(?)
              {team_filter}
            ORDER BY g.game_date, COALESCE(g.game_time_et, '99:99'), g.week, g.week_game_number, g.game_id
            """.format(team_filter=team_filter),
            tuple(params),
        ).fetchall()
    )
    for row in rows:
        row["awayLogo"] = logos.get(str(row.get("away_team")))
        row["homeLogo"] = logos.get(str(row.get("home_team")))
    return rows


def calendar_event_rows(
    conn: sqlite3.Connection,
    *,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "league_calendar_view"):
        return []
    return rows_as_dicts(
        conn.execute(
            """
            SELECT
                event_id,
                league_year,
                event_code,
                event_name,
                event_category,
                event_start_date,
                event_end_date,
                event_time_et,
                phase_name,
                notes,
                sort_order
            FROM league_calendar_view
            WHERE date(event_start_date) BETWEEN date(?) AND date(?)
            ORDER BY event_start_date, sort_order, event_id
            """,
            (start_date, end_date),
        ).fetchall()
    )


def calendar_news_rows(
    conn: sqlite3.Connection,
    *,
    game_id: str | None,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "league_news_items"):
        return []
    return rows_as_dicts(
        conn.execute(
            """
            SELECT
                ln.news_id,
                ln.news_date,
                ln.category,
                ln.priority,
                ln.source,
                ln.title,
                ln.body,
                ln.is_major,
                ln.team_id,
                ln.player_id,
                COALESCE(
                    ln.prospect_id,
                    CASE
                        WHEN LOWER(COALESCE(ln.related_table, '')) = 'draft_prospects' THEN ln.related_id
                        ELSE NULL
                    END
                ) AS prospect_id,
                ln.related_table,
                ln.related_id,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position AS player_position,
                dp.first_name || ' ' || dp.last_name AS prospect_name,
                dp.position AS prospect_position,
                dp.college AS prospect_college,
                t.abbreviation AS team,
                t.city || ' ' || t.nickname AS team_name
            FROM league_news_items ln
            LEFT JOIN teams t ON t.team_id = ln.team_id
            LEFT JOIN players p ON p.player_id = ln.player_id
            LEFT JOIN draft_prospects dp
              ON dp.prospect_id = COALESCE(
                    ln.prospect_id,
                    CASE
                        WHEN LOWER(COALESCE(ln.related_table, '')) = 'draft_prospects' THEN ln.related_id
                        ELSE NULL
                    END
                 )
            WHERE ln.game_id IN (?, 'default')
              AND date(ln.news_date) BETWEEN date(?) AND date(?)
              AND LOWER(COALESCE(ln.category, '')) <> 'injuries'
            ORDER BY ln.news_date, ln.is_major DESC, ln.news_id DESC
            """,
            (game_id or "default", start_date, end_date),
        ).fetchall()
    )


def next_calendar_event(conn: sqlite3.Connection) -> dict[str, Any] | None:
    if not table_exists(conn, "upcoming_league_events_view"):
        return None
    return one_as_dict(
        conn.execute(
            """
            SELECT event_start_date, event_name, event_category, phase_name, event_time_et, notes
            FROM upcoming_league_events_view
            ORDER BY event_start_date, sort_order
            LIMIT 1
            """
        ).fetchone()
    )


def upcoming_calendar_events(conn: sqlite3.Connection, *, current_date: str, limit: int = 10) -> list[dict[str, Any]]:
    if not table_exists(conn, "league_calendar_view"):
        return []
    return rows_as_dicts(
        conn.execute(
            """
            SELECT
                event_id,
                league_year,
                event_code,
                event_name,
                event_category,
                event_start_date,
                event_end_date,
                event_time_et,
                phase_name,
                notes,
                sort_order
            FROM league_calendar_view
            WHERE date(event_start_date) > date(?)
            ORDER BY event_start_date, sort_order, event_id
            LIMIT ?
            """,
            (current_date, limit),
        ).fetchall()
    )


def upcoming_calendar_games(
    conn: sqlite3.Connection,
    *,
    season: int,
    current_date: str,
    user_team_id: int | None = None,
    limit: int = 16,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "season_games"):
        return []
    team_filter = ""
    params: list[Any] = [season, current_date]
    if user_team_id:
        team_filter = "AND (away_team_id = ? OR home_team_id = ?)"
        params.extend([user_team_id, user_team_id])
    rows = conn.execute(
        f"""
        SELECT MIN(game_date) AS next_game_date
        FROM season_games
        WHERE season = ?
          AND played = 0
          AND date(game_date) >= date(?)
          {team_filter}
        """,
        tuple(params),
    ).fetchone()
    if not rows or not rows["next_game_date"]:
        return []
    where_sql = "g.played = 0 AND date(g.game_date) >= date(?)"
    game_params: list[Any] = [current_date]
    if user_team_id:
        where_sql += " AND (g.away_team_id = ? OR g.home_team_id = ?)"
        game_params.extend([user_team_id, user_team_id])
    return game_rows(
        conn,
        season=season,
        where_sql=where_sql,
        params=tuple(game_params),
        order_sql="g.game_date, COALESCE(g.game_time_et, '99:99'), g.week, g.week_game_number, g.game_id",
        limit=limit,
    )


def user_preseason_matchups_by_week(
    conn: sqlite3.Connection,
    *,
    season: int,
    user_team_id: int | None = None,
) -> dict[int, dict[str, Any]]:
    if not user_team_id or not table_exists(conn, "season_games"):
        return {}
    logos = team_logo_map(conn)
    rows = rows_as_dicts(
        conn.execute(
            """
            SELECT
                g.game_id,
                g.week,
                g.game_date,
                g.game_time_et,
                g.played,
                g.away_score,
                g.home_score,
                away.abbreviation AS away_team,
                away.city || ' ' || away.nickname AS away_team_name,
                home.abbreviation AS home_team,
                home.city || ' ' || home.nickname AS home_team_name
            FROM season_games g
            JOIN teams away ON away.team_id = g.away_team_id
            JOIN teams home ON home.team_id = g.home_team_id
            WHERE g.season = ?
              AND g.game_type = 'PRE'
              AND (g.away_team_id = ? OR g.home_team_id = ?)
            ORDER BY g.week, g.game_date, COALESCE(g.game_time_et, '99:99'), g.game_id
            """,
            (season, user_team_id, user_team_id),
        ).fetchall()
    )
    matchups: dict[int, dict[str, Any]] = {}
    for row in rows:
        week = int(row.get("week") or 0)
        if week <= 0 or week in matchups:
            continue
        row["awayLogo"] = logos.get(str(row.get("away_team")))
        row["homeLogo"] = logos.get(str(row.get("home_team")))
        row["label"] = f"{row.get('away_team', '-')} @ {row.get('home_team', '-')}"
        if int(row.get("played") or 0):
            row["scoreLabel"] = (
                f"{row.get('away_team', '-')} {row.get('away_score', '-')} - "
                f"{row.get('home_team', '-')} {row.get('home_score', '-')}"
            )
        matchups[week] = row
    return matchups


def attach_preseason_matchups(
    events: list[dict[str, Any]],
    *,
    matchups_by_week: dict[int, dict[str, Any]],
) -> None:
    if not events or not matchups_by_week:
        return
    for event in events:
        code = str(event.get("event_code") or "")
        if not code.startswith("PRESEASON_WEEK_"):
            continue
        try:
            week = int(code.rsplit("_", 1)[-1])
        except ValueError:
            continue
        matchup = matchups_by_week.get(week)
        if matchup:
            event["matchup"] = matchup


def default_calendar_focus_date(conn: sqlite3.Connection, *, season: int, current_date: str) -> str:
    if not table_exists(conn, "season_games"):
        return current_date
    current_text = str(current_date or "")
    if current_text and current_text < f"{season}-09-01":
        row = conn.execute(
            """
            SELECT COALESCE(
                MIN(CASE WHEN played = 0 THEN game_date END),
                MIN(game_date)
            ) AS focus_date
            FROM season_games
            WHERE season = ?
              AND game_type = 'PRE'
            """,
            (season,),
        ).fetchone()
        if row and row["focus_date"]:
            return str(row["focus_date"])
    return current_date


def calendar_summary(
    conn: sqlite3.Connection,
    *,
    season: int,
    current_date: str,
    game_id: str | None,
    user_team_id: int | None = None,
    focus_date: str | None = None,
) -> dict[str, Any]:
    display_date = focus_date or current_date
    focus, start, end = calendar_bounds(display_date)
    start_text = start.isoformat()
    end_text = end.isoformat()
    events = calendar_event_rows(conn, start_date=start_text, end_date=end_text)
    games = calendar_game_rows(
        conn,
        season=season,
        start_date=start_text,
        end_date=end_text,
        user_team_id=user_team_id,
    )
    news = calendar_news_rows(conn, game_id=game_id, start_date=start_text, end_date=end_text)

    events_by_date: dict[str, list[dict[str, Any]]] = {}
    games_by_date: dict[str, list[dict[str, Any]]] = {}
    news_by_date: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        events_by_date.setdefault(str(event["event_start_date"]), []).append(event)
    for game in games:
        games_by_date.setdefault(str(game["game_date"]), []).append(game)
    for item in news:
        news_by_date.setdefault(str(item["news_date"]), []).append(item)

    days = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        days.append(
            {
                "date": key,
                "dayNumber": cursor.day,
                "weekday": cursor.strftime("%a"),
                "isCurrentMonth": cursor.month == focus.month,
                "isToday": key == current_date,
                "isFocusDate": key == display_date,
                "events": events_by_date.get(key, []),
                "games": games_by_date.get(key, []),
                "news": news_by_date.get(key, []),
            }
        )
        cursor += timedelta(days=1)

    next_event = next_calendar_event(conn)
    upcoming_events = upcoming_calendar_events(conn, current_date=current_date, limit=12)
    next_games = upcoming_calendar_games(conn, season=season, current_date=current_date, user_team_id=user_team_id)
    preseason_matchups = user_preseason_matchups_by_week(conn, season=season, user_team_id=user_team_id)
    attach_preseason_matchups(events, matchups_by_week=preseason_matchups)
    attach_preseason_matchups(upcoming_events, matchups_by_week=preseason_matchups)
    if next_event:
        attach_preseason_matchups([next_event], matchups_by_week=preseason_matchups)
    return {
        "focusDate": display_date,
        "saveDate": current_date,
        "scope": "user_team" if user_team_id else "league",
        "monthLabel": focus.strftime("%B %Y"),
        "rangeStart": start_text,
        "rangeEnd": end_text,
        "days": days,
        "eventsInView": events,
        "gamesInView": games,
        "newsInView": news,
        "nextEvent": next_event,
        "upcomingEvents": upcoming_events,
        "upcomingGames": next_games,
        "preseasonMatchupsByWeek": {str(week): matchup for week, matchup in preseason_matchups.items()},
    }


def format_transaction(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["transaction_id"]),
        "date": row["transaction_date"],
        "season": row["season"],
        "phase": row["phase"],
        "week": row["week"],
        "type": row["transaction_type"],
        "category": row["transaction_category"],
        "team": row["team"],
        "secondaryTeam": row["secondary_team"],
        "playerId": row["player_id"],
        "player": row["player_name"],
        "position": row["player_position"],
        "fromTeam": row["from_team"],
        "toTeam": row["to_team"],
        "oldStatus": row["old_status"],
        "newStatus": row["new_status"],
        "capDeltaCurrent": int(row["cap_delta_current"] or 0),
        "capDeltaNext": int(row["cap_delta_next"] or 0),
        "cashDelta": int(row["cash_delta"] or 0),
        "description": row["description"] or "",
        "source": row["source"],
        "createdAt": row["created_at"],
    }


def league_transactions_summary(conn: sqlite3.Connection, *, limit: int = 400, include_baseline: bool = False) -> dict[str, Any]:
    if not table_exists(conn, "transaction_log_view"):
        return {"items": [], "counts": {"total": 0}, "categories": [], "includeBaseline": include_baseline}
    baseline_filter = "" if include_baseline else "WHERE COALESCE(transaction_category, '') != 'Baseline'"
    rows = conn.execute(
        f"""
        SELECT *
        FROM transaction_log_view
        {baseline_filter}
        ORDER BY date(transaction_date) DESC, transaction_id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    count_rows = conn.execute(
        f"""
        SELECT COALESCE(transaction_category, 'Other') AS category, COUNT(*) AS count
        FROM transaction_log_view
        {baseline_filter}
        GROUP BY COALESCE(transaction_category, 'Other')
        ORDER BY count DESC, category
        """
    ).fetchall()
    categories = [str(row["category"]) for row in count_rows]
    counts = {str(row["category"]): int(row["count"] or 0) for row in count_rows}
    counts["total"] = sum(counts.values())
    return {
        "items": [format_transaction(row) for row in rows],
        "counts": counts,
        "categories": categories,
        "includeBaseline": include_baseline,
        "limit": limit,
    }


def injury_center_summary(
    conn: sqlite3.Connection,
    *,
    current_date: str | None = None,
    user_team_id: int | None = None,
    active_limit: int = 160,
    recent_limit: int = 120,
) -> dict[str, Any]:
    if not table_exists(conn, "active_player_injuries") or not table_exists(conn, "game_injury_events"):
        return {
            "active": [],
            "recent": [],
            "counts": {"active": 0, "userActive": 0, "majorActive": 0, "recent": 0, "userRecent": 0},
            "updatedAt": datetime.now().isoformat(timespec="seconds"),
        }
    today = current_date or date.today().isoformat()
    active_rows = conn.execute(
        """
        SELECT
            api.active_injury_id,
            api.player_id,
            TRIM(COALESCE(p.first_name, '') || ' ' || COALESCE(p.last_name, '')) AS player_name,
            p.position,
            p.status AS player_status,
            p.overall,
            api.injury_label,
            api.body_region,
            api.body_part,
            api.severity,
            api.start_date,
            api.expected_days,
            api.expected_games,
            api.status,
            api.return_earliest_date,
            api.schedule_game_id,
            api.source,
            api.notes,
            t.team_id,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name,
            CAST(MAX(0, julianday(api.return_earliest_date) - julianday(?)) AS INTEGER) AS days_remaining
        FROM active_player_injuries api
        JOIN players p ON p.player_id = api.player_id
        LEFT JOIN teams t ON t.team_id = p.team_id
        WHERE api.resolved_at IS NULL
        ORDER BY
            CASE WHEN t.team_id = ? THEN 0 ELSE 1 END,
            api.expected_games DESC,
            api.return_earliest_date,
            player_name
        LIMIT ?
        """,
        (today, user_team_id or -1, int(active_limit)),
    ).fetchall()
    recent_rows = conn.execute(
        """
        SELECT
            gie.event_id,
            gie.schedule_game_id,
            gie.season,
            gie.week,
            gie.game_date,
            gie.player_id,
            TRIM(COALESCE(p.first_name, '') || ' ' || COALESCE(p.last_name, '')) AS player_name,
            p.position,
            gie.team_id,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name,
            gie.injury_label,
            gie.body_region,
            gie.body_part,
            gie.severity,
            gie.expected_days,
            gie.expected_games,
            gie.status,
            gie.source,
            gie.description
        FROM game_injury_events gie
        JOIN players p ON p.player_id = gie.player_id
        LEFT JOIN teams t ON t.team_id = gie.team_id
        ORDER BY gie.game_date DESC, gie.event_id DESC
        LIMIT ?
        """,
        (int(recent_limit),),
    ).fetchall()

    active = [
        {
            "activeInjuryId": row["active_injury_id"],
            "playerId": row["player_id"],
            "playerName": row["player_name"],
            "position": row["position"],
            "teamId": row["team_id"],
            "team": row["team"],
            "teamName": row["team_name"],
            "overall": row["overall"],
            "injury": row["injury_label"],
            "bodyRegion": row["body_region"],
            "bodyPart": row["body_part"],
            "severity": row["severity"],
            "startDate": row["start_date"],
            "expectedDays": row["expected_days"],
            "expectedGames": row["expected_games"],
            "status": row["status"] or row["player_status"],
            "returnDate": row["return_earliest_date"],
            "daysRemaining": row["days_remaining"],
            "source": row["source"],
            "notes": row["notes"],
            "isUserTeam": bool(user_team_id and row["team_id"] == user_team_id),
        }
        for row in active_rows
    ]
    recent = [
        {
            "eventId": row["event_id"],
            "gameId": row["schedule_game_id"],
            "season": row["season"],
            "week": row["week"],
            "date": row["game_date"],
            "playerId": row["player_id"],
            "playerName": row["player_name"],
            "position": row["position"],
            "teamId": row["team_id"],
            "team": row["team"],
            "teamName": row["team_name"],
            "injury": row["injury_label"],
            "bodyRegion": row["body_region"],
            "bodyPart": row["body_part"],
            "severity": row["severity"],
            "expectedDays": row["expected_days"],
            "expectedGames": row["expected_games"],
            "status": row["status"],
            "source": row["source"],
            "description": row["description"],
            "isUserTeam": bool(user_team_id and row["team_id"] == user_team_id),
        }
        for row in recent_rows
    ]
    counts = {
        "active": len(active),
        "userActive": sum(1 for item in active if item["isUserTeam"]),
        "majorActive": sum(
            1
            for item in active
            if int(item.get("expectedGames") or 0) >= 4 or str(item.get("severity") or "").lower() in {"major", "severe"}
        ),
        "recent": len(recent),
        "userRecent": sum(1 for item in recent if item["isUserTeam"]),
    }
    return {
        "active": active,
        "recent": recent,
        "counts": counts,
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
    }


def season_summary(conn: sqlite3.Connection, season: int, user_team_id: int | None = None) -> dict[str, Any]:
    if not table_exists(conn, "season_games"):
        return {"season": season, "weeks": [], "totals": {"games": 0, "played": 0, "remaining": 0}}
    preseason_weeks = conn.execute(
        """
        SELECT
            week,
            COUNT(*) AS games,
            SUM(CASE WHEN played = 1 THEN 1 ELSE 0 END) AS played,
            MIN(game_date) AS first_date,
            MAX(game_date) AS last_date
        FROM season_games
        WHERE season = ? AND game_type = 'PRE'
        GROUP BY week
        ORDER BY week
        """,
        (season,),
    ).fetchall()
    weeks = conn.execute(
        """
        SELECT
            week,
            COUNT(*) AS games,
            SUM(CASE WHEN played = 1 THEN 1 ELSE 0 END) AS played,
            MIN(game_date) AS first_date,
            MAX(game_date) AS last_date
        FROM season_games
        WHERE season = ? AND game_type = 'REG'
        GROUP BY week
        ORDER BY week
        """,
        (season,),
    ).fetchall()
    preseason_totals = conn.execute(
        """
        SELECT
            COUNT(*) AS games,
            SUM(CASE WHEN played = 1 THEN 1 ELSE 0 END) AS played
        FROM season_games
        WHERE season = ? AND game_type = 'PRE'
        """,
        (season,),
    ).fetchone()
    totals = conn.execute(
        """
        SELECT
            COUNT(*) AS games,
            SUM(CASE WHEN played = 1 THEN 1 ELSE 0 END) AS played
        FROM season_games
        WHERE season = ? AND game_type = 'REG'
        """,
        (season,),
    ).fetchone()
    preseason_week_items = []
    next_preseason_week = None
    for row in preseason_weeks:
        games_for_week = int(row["games"] or 0)
        played_for_week = int(row["played"] or 0)
        item = dict(row)
        item["remaining"] = games_for_week - played_for_week
        item["complete"] = games_for_week > 0 and played_for_week >= games_for_week
        preseason_week_items.append(item)
        if next_preseason_week is None and item["remaining"] > 0:
            next_preseason_week = int(row["week"])
    week_items = []
    next_week = None
    for row in weeks:
        games = int(row["games"] or 0)
        played = int(row["played"] or 0)
        item = dict(row)
        item["remaining"] = games - played
        item["complete"] = games > 0 and played >= games
        week_items.append(item)
        if next_week is None and item["remaining"] > 0:
            next_week = int(row["week"])
    next_game_type = "PRE" if next_preseason_week is not None else ("REG" if next_week is not None else None)
    next_playable_week = next_preseason_week if next_preseason_week is not None else next_week
    preseason_games = int(preseason_totals["games"] or 0) if preseason_totals else 0
    preseason_played = int(preseason_totals["played"] or 0) if preseason_totals else 0
    games = int(totals["games"] or 0) if totals else 0
    played = int(totals["played"] or 0) if totals else 0
    postseason = postseason_summary(conn, season)
    completion = None
    if table_exists(conn, "season_completions"):
        completion = one_as_dict(
            conn.execute(
                "SELECT * FROM season_completions WHERE season = ?",
                (season,),
            ).fetchone()
        )
    standings = []
    if table_exists(conn, "season_team_records"):
        logos = team_logo_map(conn)
        standings = rows_as_dicts(
            conn.execute(
                """
                SELECT
                    r.season,
                    t.abbreviation,
                    t.city || ' ' || t.nickname AS team_name,
                    t.conference,
                    t.division,
                    r.wins,
                    r.losses,
                    r.ties,
                    r.points_for,
                    r.points_against
                FROM season_team_records r
                JOIN teams t ON t.team_id = r.team_id
                WHERE r.season = ?
                ORDER BY r.wins DESC, r.losses ASC, (r.points_for - r.points_against) DESC, t.abbreviation
                """,
                (season,),
            ).fetchall()
        )
        for row in standings:
            row["teamLogo"] = logos.get(str(row.get("abbreviation")))
    next_week_games = []
    if next_playable_week is not None and next_game_type:
        next_week_games = game_rows(
            conn,
            season=season,
            where_sql="g.game_type = ? AND g.week = ?",
            params=(next_game_type, next_playable_week),
            order_sql="g.week, g.week_game_number, g.game_id",
            limit=24,
        )
    recent_results = game_rows(
        conn,
        season=season,
        where_sql="g.game_type = 'REG' AND g.played = 1",
        params=(),
        order_sql="g.week DESC, g.game_id DESC",
        limit=20,
    )
    user_schedule = []
    if user_team_id:
        user_schedule = game_rows(
            conn,
            season=season,
            where_sql="g.game_type = 'REG' AND (g.away_team_id = ? OR g.home_team_id = ?)",
            params=(user_team_id, user_team_id),
            order_sql="g.week, g.game_id",
            limit=24,
        )
    return {
        "season": season,
        "preseasonWeeks": preseason_week_items,
        "weeks": week_items,
        "nextWeek": next_playable_week,
        "nextRegularWeek": next_week,
        "nextPreseasonWeek": next_preseason_week,
        "nextGameType": next_game_type,
        "preseasonTotals": {
            "games": preseason_games,
            "played": preseason_played,
            "remaining": preseason_games - preseason_played,
        },
        "totals": {"games": games, "played": played, "remaining": games - played},
        "postseason": postseason,
        "completion": completion,
        "standings": standings,
        "nextWeekGames": next_week_games,
        "recentResults": recent_results,
        "userTeamSchedule": user_schedule,
    }


def postseason_summary(conn: sqlite3.Connection, season: int) -> dict[str, Any]:
    if not table_exists(conn, "playoff_games"):
        return {"games": 0, "played": 0, "remaining": 0, "rounds": [], "matchups": [], "visible": False}
    logos = team_logo_map(conn)
    matchups = rows_as_dicts(
        conn.execute(
            """
            SELECT
                pg.season,
                pg.round_code,
                pg.round_name,
                pg.game_number,
                pg.conference,
                pg.high_seed,
                pg.low_seed,
                pg.schedule_game_id AS game_id,
                pg.winner_team_id,
                pg.loser_team_id,
                away.abbreviation AS away_team,
                away.city || ' ' || away.nickname AS away_team_name,
                home.abbreviation AS home_team,
                home.city || ' ' || home.nickname AS home_team_name,
                winner.abbreviation AS winner_team,
                sg.game_date,
                sg.game_time_et,
                COALESCE(sg.played, CASE WHEN pg.winner_team_id IS NOT NULL THEN 1 ELSE 0 END) AS played,
                sg.away_score,
                sg.home_score
            FROM playoff_games pg
            JOIN teams away ON away.team_id = pg.away_team_id
            JOIN teams home ON home.team_id = pg.home_team_id
            LEFT JOIN teams winner ON winner.team_id = pg.winner_team_id
            LEFT JOIN season_games sg ON sg.game_id = pg.schedule_game_id
            WHERE pg.season = ?
            ORDER BY
                CASE pg.round_code
                    WHEN 'WC' THEN 1
                    WHEN 'DIV' THEN 2
                    WHEN 'CONF' THEN 3
                    WHEN 'SB' THEN 4
                    ELSE 9
                END,
                CASE COALESCE(pg.conference, '')
                    WHEN 'AFC' THEN 1
                    WHEN 'NFC' THEN 2
                    ELSE 3
                END,
                pg.game_number
            """,
            (season,),
        ).fetchall()
    )
    for matchup in matchups:
        matchup["awayLogo"] = logos.get(str(matchup.get("away_team")))
        matchup["homeLogo"] = logos.get(str(matchup.get("home_team")))
        matchup["winnerLogo"] = logos.get(str(matchup.get("winner_team")))
    rows = conn.execute(
        """
        SELECT
            round_code,
            round_name,
            COUNT(*) AS games,
            SUM(CASE WHEN winner_team_id IS NOT NULL THEN 1 ELSE 0 END) AS played
        FROM playoff_games
        WHERE season = ?
        GROUP BY round_code, round_name
        ORDER BY
            CASE round_code
                WHEN 'WC' THEN 1
                WHEN 'DIV' THEN 2
                WHEN 'CONF' THEN 3
                WHEN 'SB' THEN 4
                ELSE 9
            END
        """,
        (season,),
    ).fetchall()
    rounds = []
    games = 0
    played = 0
    for row in rows:
        item = dict(row)
        item["games"] = int(row["games"] or 0)
        item["played"] = int(row["played"] or 0)
        item["remaining"] = item["games"] - item["played"]
        rounds.append(item)
        games += item["games"]
        played += item["played"]
    regular_done = False
    if table_exists(conn, "season_games"):
        row = conn.execute(
            """
            SELECT COUNT(*) AS games, SUM(CASE WHEN played = 1 THEN 1 ELSE 0 END) AS played
            FROM season_games
            WHERE season = ? AND game_type = 'REG'
            """,
            (season,),
        ).fetchone()
        regular_games = int(row["games"] or 0) if row else 0
        regular_played = int(row["played"] or 0) if row else 0
        regular_done = regular_games > 0 and regular_played >= regular_games
    return {
        "games": games,
        "played": played,
        "remaining": games - played,
        "rounds": rounds,
        "matchups": matchups,
        "visible": regular_done or games > 0,
    }


def draft_year(conn: sqlite3.Connection, season: int) -> int:
    if table_exists(conn, "draft_classes"):
        row = conn.execute(
            """
            SELECT draft_year
            FROM draft_classes
            WHERE draft_year >= ?
            ORDER BY draft_year
            LIMIT 1
            """,
            (season + 1,),
        ).fetchone()
        if row:
            return int(row["draft_year"])
    return season + 1


def draft_event_date(conn: sqlite3.Connection, year: int) -> str | None:
    if not table_exists(conn, "league_calendar_events"):
        return None
    row = conn.execute(
        """
        SELECT event_start_date
        FROM league_calendar_events
        WHERE event_code = 'NFL_DRAFT'
          AND (league_year = ? OR event_name = ?)
        ORDER BY event_start_date
        LIMIT 1
        """,
        (year - 1, f"{year} NFL Draft"),
    ).fetchone()
    return str(row["event_start_date"]) if row else None


def draft_class_setup(conn: sqlite3.Connection, season: int, year: int, *, active_game: bool = True) -> dict[str, Any]:
    pending_year = settings(conn).get("draft_class_setup_pending_year")
    class_row = None
    prospect_count = 0
    if table_exists(conn, "draft_classes"):
        class_row = conn.execute(
            """
            SELECT dc.*,
                   COUNT(dp.prospect_id) AS prospect_count,
                   SUM(CASE WHEN dp.public_board_status = 'off_public_board' THEN 1 ELSE 0 END) AS off_board_count
            FROM draft_classes dc
            LEFT JOIN draft_prospects dp ON dp.draft_class_id = dc.draft_class_id
            WHERE dc.draft_year = ?
            GROUP BY dc.draft_class_id
            """,
            (year,),
        ).fetchone()
        if class_row:
            prospect_count = int(class_row["prospect_count"] or 0)
    packages: list[dict[str, Any]] = []
    try:
        packages = saved_draft_class_package.list_packages()
    except Exception as exc:
        packages = [{"valid": False, "name": "Saved class folder unavailable", "error": str(exc)}]
    required = bool(active_game) and prospect_count == 0 and (not pending_year or str(pending_year) == str(year))
    return {
        "draftYear": year,
        "season": season,
        "required": required,
        "pendingYear": pending_year,
        "exists": prospect_count > 0,
        "prospectCount": prospect_count,
        "class": one_as_dict(class_row) if class_row else None,
        "packages": packages,
        "packageRoot": str(saved_draft_class_package.DEFAULT_PACKAGE_ROOT),
    }


def draft_position_keys(position: str | None) -> list[str]:
    normalized = DRAFT_POSITION_ALIASES.get(str(position or "").upper(), str(position or "").upper())
    return POSITION_RATING_KEYS.get(normalized, [
        "speed",
        "strength",
        "agility",
        "play_recognition",
        "processing_speed",
        "composure",
        "consistency",
    ])


def scout_noise(prospect_id: int, rating_key: str, confidence: str | None) -> float:
    sigma = {
        "high": 2.4,
        "medium": 4.2,
        "low": 6.4,
    }.get(str(confidence or "medium").lower(), 4.2)
    digest = hashlib.sha256(f"draft-scout-v1:{prospect_id}:{rating_key}".encode("utf-8")).digest()
    first = (digest[0] / 255.0) - 0.5
    second = (digest[1] / 255.0) - 0.5
    return (first + second) * sigma * 1.7


def scout_display_value(value: float) -> float:
    # Stretch the useful scouting band so 62/68/74 do not look nearly identical.
    return round(max(5.0, min(98.0, 50.0 + (value - 60.0) * 1.55)), 1)


def scout_range_margin(confidence: str | None, variance: int | None = None) -> float:
    base = {
        "very high": 3.0,
        "high": 5.0,
        "medium": 8.0,
        "low": 12.0,
    }.get(str(confidence or "medium").lower(), 8.0)
    variance_bonus = max(0.0, min(5.0, (float(variance or 50) - 50.0) / 10.0))
    return base + variance_bonus


def draft_scout_attributes(
    conn: sqlite3.Connection,
    prospects: list[dict[str, Any]],
) -> dict[int, dict[str, list[dict[str, Any]]]]:
    if not prospects or not table_exists(conn, "draft_prospect_ratings"):
        return {}
    prospect_ids = [int(row["prospect_id"]) for row in prospects]
    placeholders = ",".join("?" for _ in prospect_ids)
    definitions = {
        row["rating_key"]: {
            "label": row["display_name"],
            "group": row["rating_group"],
        }
        for row in conn.execute(
            "SELECT rating_key, display_name, rating_group FROM rating_definitions"
        ).fetchall()
    } if table_exists(conn, "rating_definitions") else {}
    true_grades = {
        int(row["prospect_id"]): row["true_grade"]
        for row in conn.execute(
            f"SELECT prospect_id, true_grade FROM draft_prospects WHERE prospect_id IN ({placeholders})",
            prospect_ids,
        ).fetchall()
    } if table_exists(conn, "draft_prospects") else {}
    rating_rows = conn.execute(
        f"""
        SELECT prospect_id, rating_key, rating_value, confidence
        FROM draft_prospect_ratings
        WHERE prospect_id IN ({placeholders})
        """,
        prospect_ids,
    ).fetchall()
    ratings: dict[int, dict[str, sqlite3.Row]] = {prospect_id: {} for prospect_id in prospect_ids}
    for row in rating_rows:
        ratings[int(row["prospect_id"])][row["rating_key"]] = row

    grouped: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for prospect in prospects:
        prospect_id = int(prospect["prospect_id"])
        by_key = ratings.get(prospect_id, {})
        keys = draft_position_keys(prospect.get("position"))
        ordered_keys = [key for key in keys if key in by_key]
        if len(ordered_keys) < 8:
            extras = [
                key
                for key, row in sorted(
                    by_key.items(),
                    key=lambda item: int(item[1]["rating_value"] or 0),
                    reverse=True,
                )
                if key not in set(ordered_keys)
            ]
            ordered_keys.extend(extras[: max(0, 10 - len(ordered_keys))])
        scout_grade = prospect.get("scout_grade")
        true_grade = true_grades.get(prospect_id)
        grade_bias = 0.0
        if scout_grade is not None and true_grade is not None:
            grade_bias = max(-6.0, min(6.0, (float(scout_grade) - float(true_grade)) * 0.35))
        attributes = []
        for key in ordered_keys[:12]:
            row = by_key[key]
            true_value = float(row["rating_value"] or 0)
            confidence = prospect.get("scout_confidence") or row["confidence"] or "Medium"
            scouted = max(
                0.0,
                min(
                    100.0,
                    true_value + grade_bias + scout_noise(prospect_id, key, confidence),
                ),
            )
            margin = scout_range_margin(confidence, prospect.get("scouting_variance"))
            low = max(0.0, scouted - margin)
            high = min(100.0, scouted + margin)
            definition = definitions.get(key, {})
            attributes.append({
                "key": key,
                "label": definition.get("label", key.replace("_", " ").title()),
                "group": definition.get("group", "scouting"),
                "scoutValue": round(scouted, 1),
                "displayValue": scout_display_value(scouted),
                "rangeLow": round(low, 1),
                "rangeHigh": round(high, 1),
                "rangeDisplayLow": scout_display_value(low),
                "rangeDisplayHigh": scout_display_value(high),
                "grade": rating_grade_label(scouted),
                "confidence": confidence,
            })
        grouped[prospect_id] = {
            "attributes": attributes,
            "strengths": sorted(attributes, key=lambda item: item["scoutValue"], reverse=True)[:5],
            "concerns": sorted(attributes, key=lambda item: item["scoutValue"])[:5],
        }
    return grouped


def slim_draft_board_rows(board: list[dict[str, Any]], detail_limit: int = DRAFT_BOARD_DETAIL_LIMIT) -> None:
    """Keep the draft board fast while preserving full cards for the top slice."""
    for index, prospect in enumerate(board):
        detail_exported = index < detail_limit
        prospect["details_exported"] = detail_exported
        if detail_exported:
            continue
        summary = prospect.get("scouting_summary")
        if isinstance(summary, str) and len(summary) > 190:
            prospect["scouting_summary"] = summary[:187].rstrip() + "..."
        for field in DRAFT_LIGHT_ROW_DROP_FIELDS:
            prospect.pop(field, None)
        prospect["scout_attributes"] = []
        prospect["scout_strengths"] = []
        prospect["scout_concerns"] = []


def mask_draft_workouts_for_calendar(
    conn: sqlite3.Connection,
    board: list[dict[str, Any]],
    draft_year_value: int,
    current_date_value: str | None,
) -> dict[str, Any]:
    if not hasattr(scouting, "workout_visibility"):
        return {}
    visibility = scouting.workout_visibility(conn, draft_year_value, current_date_value)
    if visibility.get("combineAvailable") and visibility.get("proDayAvailable"):
        return visibility
    for prospect in board:
        if not visibility.get("combineAvailable"):
            for field in DRAFT_COMBINE_UI_FIELDS:
                if field in prospect:
                    prospect[field] = None
            prospect["combine_status"] = "Pending"
        if not visibility.get("proDayAvailable"):
            for field in DRAFT_PRO_DAY_UI_FIELDS:
                if field in prospect:
                    prospect[field] = None
            prospect["pro_day_status"] = "Pending"
    return visibility


def team_logo_map(conn: sqlite3.Connection) -> dict[str, str]:
    if not table_exists(conn, "team_graphics_assets"):
        return {}
    rows = rows_as_dicts(
        conn.execute(
            """
            WITH ranked AS (
                SELECT
                    t.abbreviation,
                    a.local_path,
                    ROW_NUMBER() OVER (
                        PARTITION BY t.team_id
                        ORDER BY
                            CASE a.variant
                                WHEN 'scoreboard' THEN 0
                                WHEN 'primary' THEN 1
                                WHEN 'scoreboard_dark' THEN 2
                                WHEN 'dark' THEN 3
                                ELSE 9
                            END,
                            a.asset_id
                    ) AS rn
                FROM team_graphics_assets a
                JOIN teams t ON t.team_id = a.team_id
                WHERE a.asset_type = 'logo'
            )
            SELECT abbreviation, local_path
            FROM ranked
            WHERE rn = 1
            """
        ).fetchall()
    )
    return {
        row["abbreviation"]: "/" + str(row["local_path"]).replace("\\", "/").lstrip("/")
        for row in rows
    }


def public_need_group(position: str | None) -> str:
    normalized = str(position or "").upper()
    return PUBLIC_NEED_POSITION_GROUPS.get(normalized, normalized or "DEPTH")


def public_need_label(score: float) -> str:
    if score >= 82:
        return "Major need"
    if score >= 68:
        return "Need"
    if score >= 54:
        return "Depth need"
    if score >= 42:
        return "Roster fit"
    return "Luxury"


def public_grade_label(score: float) -> str:
    if score >= 94:
        return "A+"
    if score >= 89:
        return "A"
    if score >= 84:
        return "A-"
    if score >= 80:
        return "B+"
    if score >= 76:
        return "B"
    if score >= 72:
        return "B-"
    if score >= 68:
        return "C+"
    if score >= 64:
        return "C"
    if score >= 60:
        return "C-"
    if score >= 55:
        return "D"
    return "F"


def draft_selected_player_ids(conn: sqlite3.Connection, year: int) -> set[int]:
    if not table_exists(conn, "draft_prospects") or not table_exists(conn, "draft_classes"):
        return set()
    return {
        int(row["player_id"])
        for row in conn.execute(
            """
            SELECT dp.player_id
            FROM draft_prospects dp
            JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
            WHERE dc.draft_year = ?
              AND dp.player_id IS NOT NULL
            """,
            (year,),
        ).fetchall()
    }


def draft_team_need_scores(conn: sqlite3.Connection, year: int) -> dict[int, dict[str, dict[str, Any]]]:
    if not table_exists(conn, "players") or not table_exists(conn, "teams"):
        return {}
    selected_ids = draft_selected_player_ids(conn, year)
    exclude_sql = ""
    params: list[Any] = []
    if selected_ids:
        placeholders = ",".join("?" for _ in selected_ids)
        exclude_sql = f"AND p.player_id NOT IN ({placeholders})"
        params.extend(sorted(selected_ids))
    rooms: dict[int, dict[str, list[float]]] = {}
    rows = conn.execute(
        f"""
        SELECT p.team_id, p.position, p.overall
        FROM players p
        WHERE p.team_id IS NOT NULL
          AND COALESCE(p.status, 'Active') != 'Retired'
          {exclude_sql}
        """,
        params,
    ).fetchall()
    for row in rows:
        team_id = int(row["team_id"])
        group = public_need_group(row["position"])
        rooms.setdefault(team_id, {}).setdefault(group, []).append(float(row["overall"] or 50))

    team_ids = [
        int(row["team_id"])
        for row in conn.execute("SELECT team_id FROM teams").fetchall()
    ]
    scores: dict[int, dict[str, dict[str, Any]]] = {}
    for team_id in team_ids:
        scores[team_id] = {}
        for group, min_count in PUBLIC_NEED_MIN_COUNTS.items():
            values = rooms.get(team_id, {}).get(group, [])
            count = len(values)
            avg = sum(values) / count if count else 50.0
            best = max(values) if values else 50.0
            if count == 0:
                score = 88.0
            else:
                score = 40.0
                if count < min_count:
                    score += (min_count - count) * 12.0
                if avg < 70:
                    score += (70 - avg) * 1.0
                if best < 75:
                    score += (75 - best) * 0.7
                if count >= min_count + 2 and avg >= 74:
                    score -= 16.0
                if group in PUBLIC_PREMIUM_GROUPS and score >= 52:
                    score += 4.0
            score = max(18.0, min(96.0, score))
            scores[team_id][group] = {
                "group": group,
                "score": round(score, 1),
                "label": public_need_label(score),
                "players": count,
                "avg_overall": round(avg, 1) if count else None,
                "best_overall": round(best, 1) if count else None,
            }
    return scores


def public_pick_grade(
    *,
    pick_number: int | None,
    public_rank: int | None,
    need: dict[str, Any] | None,
) -> dict[str, Any]:
    need_score = float((need or {}).get("score") or 50.0)
    value_delta = None
    if pick_number and public_rank:
        value_delta = int(pick_number) - int(public_rank)
        value_score = max(35.0, min(98.0, 78.0 + value_delta * 2.2))
    else:
        value_score = 70.0
    score = round(value_score * 0.68 + need_score * 0.32, 1)
    if value_delta is None:
        value_note = "No firm public board value"
    elif value_delta >= 14:
        value_note = "Public steal"
    elif value_delta >= 5:
        value_note = "Good value"
    elif value_delta <= -16:
        value_note = "Major reach"
    elif value_delta <= -6:
        value_note = "Reach"
    else:
        value_note = "Fair value"
    need_note = (need or {}).get("label") or "Neutral need"
    return {
        "score": score,
        "grade": public_grade_label(score),
        "valueScore": round(value_score, 1),
        "valueDelta": value_delta,
        "needScore": round(need_score, 1),
        "note": f"{value_note}; {need_note.lower()}",
    }


def draft_selection_ticker(conn: sqlite3.Connection, year: int) -> list[dict[str, Any]]:
    if not table_exists(conn, "draft_picks") or not table_exists(conn, "teams"):
        return []
    logos = team_logo_map(conn)
    needs = draft_team_need_scores(conn, year)
    has_prospects = table_exists(conn, "draft_prospects")
    prospect_select = """
        pr.prospect_id,
        pr.public_board_rank,
        pr.scouting_rank,
        pr.projected_pick,
        pr.scout_grade,
        pr.position AS prospect_position,
        pr.college AS prospect_college
    """ if has_prospects else """
        NULL AS prospect_id,
        NULL AS public_board_rank,
        NULL AS scouting_rank,
        NULL AS projected_pick,
        NULL AS scout_grade,
        NULL AS prospect_position,
        NULL AS prospect_college
    """
    prospect_join = "LEFT JOIN draft_prospects pr ON pr.selected_pick_id = dp.pick_id" if has_prospects else ""
    rows = rows_as_dicts(
        conn.execute(
            f"""
            SELECT
                dp.pick_id,
                dp.round,
                dp.pick_number,
                dp.pick_in_round,
                dp.current_team_id,
                t.abbreviation AS team,
                t.city || ' ' || t.nickname AS team_name,
                dp.selected_player_id,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position AS player_position,
                p.college AS player_college,
                {prospect_select}
            FROM draft_picks dp
            JOIN teams t ON t.team_id = dp.current_team_id
            LEFT JOIN players p ON p.player_id = dp.selected_player_id
            {prospect_join}
            WHERE dp.draft_year = ?
              AND dp.is_used = 1
              AND dp.selected_player_id IS NOT NULL
            ORDER BY COALESCE(dp.pick_number, dp.pick_id), dp.pick_id
            LIMIT 96
            """,
            (year,),
        ).fetchall()
    )
    selections: list[dict[str, Any]] = []
    for row in rows:
        pick_number = row.get("pick_number")
        public_rank = row.get("public_board_rank") or row.get("scouting_rank") or row.get("projected_pick")
        position = row.get("prospect_position") or row.get("player_position")
        need_group = public_need_group(position)
        need = needs.get(int(row["current_team_id"]), {}).get(need_group, {
            "group": need_group,
            "score": 50,
            "label": "Neutral need",
        })
        grade = public_pick_grade(
            pick_number=int(pick_number) if pick_number else None,
            public_rank=int(public_rank) if public_rank else None,
            need=need,
        )
        selections.append({
            "pickId": row["pick_id"],
            "pickNumber": row["pick_number"],
            "round": row["round"],
            "pickInRound": row["pick_in_round"],
            "team": row["team"],
            "teamName": row["team_name"],
            "teamLogo": logos.get(str(row["team"])),
            "playerId": row["selected_player_id"],
            "prospectId": row["prospect_id"],
            "playerName": row["player_name"],
            "position": position,
            "college": row.get("prospect_college") or row.get("player_college"),
            "publicBoardRank": public_rank,
            "scoutGrade": row.get("scout_grade"),
            "needGroup": need_group,
            "needLabel": need.get("label"),
            "needScore": need.get("score"),
            "publicGrade": grade["grade"],
            "publicGradeScore": grade["score"],
            "publicValueScore": grade["valueScore"],
            "publicValueDelta": grade["valueDelta"],
            "publicGradeNote": grade["note"],
        })
    return selections


def draft_user_selections(
    conn: sqlite3.Connection,
    year: int,
    user_team_id: int | None,
) -> list[dict[str, Any]]:
    if not user_team_id or not table_exists(conn, "draft_picks") or not table_exists(conn, "teams"):
        return []
    logos = team_logo_map(conn)
    needs = draft_team_need_scores(conn, year)
    has_prospects = table_exists(conn, "draft_prospects")
    prospect_select = """
        pr.prospect_id,
        pr.public_board_rank,
        pr.scouting_rank,
        pr.projected_pick,
        pr.scout_grade,
        pr.scout_ceiling,
        pr.scout_risk,
        pr.archetype,
        pr.primary_role,
        pr.position AS prospect_position,
        pr.college AS prospect_college,
        pr.age AS prospect_age,
        pr.height_in AS prospect_height_in,
        pr.weight_lbs AS prospect_weight_lbs,
        pr.scouting_summary,
        pr.scouting_projection
    """ if has_prospects else """
        NULL AS prospect_id,
        NULL AS public_board_rank,
        NULL AS scouting_rank,
        NULL AS projected_pick,
        NULL AS scout_grade,
        NULL AS scout_ceiling,
        NULL AS scout_risk,
        NULL AS archetype,
        NULL AS primary_role,
        NULL AS prospect_position,
        NULL AS prospect_college,
        NULL AS prospect_age,
        NULL AS prospect_height_in,
        NULL AS prospect_weight_lbs,
        NULL AS scouting_summary,
        NULL AS scouting_projection
    """
    prospect_join = "LEFT JOIN draft_prospects pr ON pr.selected_pick_id = dp.pick_id" if has_prospects else ""
    rows = rows_as_dicts(
        conn.execute(
            f"""
            SELECT
                dp.pick_id,
                dp.round,
                dp.pick_number,
                dp.pick_in_round,
                dp.current_team_id,
                t.abbreviation AS team,
                t.city || ' ' || t.nickname AS team_name,
                dp.selected_player_id,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position AS player_position,
                p.college AS player_college,
                p.age AS player_age,
                p.height_in AS player_height_in,
                p.weight_lbs AS player_weight_lbs,
                {prospect_select}
            FROM draft_picks dp
            JOIN teams t ON t.team_id = dp.current_team_id
            LEFT JOIN players p ON p.player_id = dp.selected_player_id
            {prospect_join}
            WHERE dp.draft_year = ?
              AND dp.current_team_id = ?
              AND dp.is_used = 1
              AND dp.selected_player_id IS NOT NULL
            ORDER BY COALESCE(dp.pick_number, dp.pick_id), dp.pick_id
            """,
            (year, int(user_team_id)),
        ).fetchall()
    )
    selections: list[dict[str, Any]] = []
    for row in rows:
        pick_number = row.get("pick_number")
        public_rank = row.get("public_board_rank") or row.get("scouting_rank") or row.get("projected_pick")
        position = row.get("prospect_position") or row.get("player_position")
        need_group = public_need_group(position)
        need = needs.get(int(user_team_id), {}).get(need_group, {
            "group": need_group,
            "score": 50,
            "label": "Neutral need",
        })
        grade = public_pick_grade(
            pick_number=int(pick_number) if pick_number else None,
            public_rank=int(public_rank) if public_rank else None,
            need=need,
        )
        selections.append({
            "pickId": row["pick_id"],
            "pickNumber": row["pick_number"],
            "round": row["round"],
            "pickInRound": row["pick_in_round"],
            "team": row["team"],
            "teamName": row["team_name"],
            "teamLogo": logos.get(str(row["team"])),
            "playerId": row["selected_player_id"],
            "prospectId": row["prospect_id"],
            "playerName": row["player_name"],
            "position": position,
            "college": row.get("prospect_college") or row.get("player_college"),
            "age": row.get("prospect_age") or row.get("player_age"),
            "heightIn": row.get("prospect_height_in") or row.get("player_height_in"),
            "weightLbs": row.get("prospect_weight_lbs") or row.get("player_weight_lbs"),
            "publicBoardRank": public_rank,
            "scoutGrade": row.get("scout_grade"),
            "scoutCeiling": row.get("scout_ceiling"),
            "scoutRisk": row.get("scout_risk"),
            "archetype": row.get("archetype"),
            "primaryRole": row.get("primary_role"),
            "scoutingSummary": row.get("scouting_summary"),
            "scoutingProjection": row.get("scouting_projection"),
            "needGroup": need_group,
            "needLabel": need.get("label"),
            "needScore": need.get("score"),
            "publicGrade": grade["grade"],
            "publicGradeScore": grade["score"],
            "publicGradeNote": grade["note"],
        })
    return selections


def draft_user_trade_assets(
    conn: sqlite3.Connection,
    year: int,
    user_team_id: int | None,
    *,
    current_pick_number: int | None = None,
) -> list[dict[str, Any]]:
    if not user_team_id or not table_exists(conn, "draft_picks"):
        return []
    end_year = int(year) + 3
    current_pick_number = int(current_pick_number or 0)
    rows = rows_as_dicts(
        conn.execute(
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
                WHERE dp.draft_year BETWEEN ? AND ?
            )
            SELECT
                o.pick_id AS pickId,
                o.draft_year AS draftYear,
                o.round,
                o.pick_number AS pickNumber,
                o.pick_in_round AS pickInRound,
                o.effective_pick_number AS effectivePickNumber,
                o.effective_pick_in_round AS effectivePickInRound,
                o.current_team_id AS currentTeamId,
                current_team.abbreviation AS currentTeam,
                o.original_team_id AS originalTeamId,
                original_team.abbreviation AS originalTeam,
                o.is_used AS isUsed,
                o.is_comp_pick AS isCompPick,
                o.is_traded AS isTraded,
                o.trade_note AS tradeNote
            FROM ordered o
            LEFT JOIN teams current_team ON current_team.team_id = o.current_team_id
            LEFT JOIN teams original_team ON original_team.team_id = o.original_team_id
            WHERE o.current_team_id = ?
              AND COALESCE(o.is_used, 0) = 0
              AND (
                  o.draft_year > ?
                  OR o.effective_pick_number > ?
              )
            ORDER BY o.draft_year, o.round, o.effective_pick_number, o.pick_id
            """,
            (year, end_year, user_team_id, year, current_pick_number),
        ).fetchall()
    )
    for row in rows:
        row["isFuture"] = int(row.get("draftYear") or year) > int(year)
        original = row.get("originalTeam")
        current = row.get("currentTeam")
        suffix = ""
        if original and current and original != current:
            suffix = f" from {original}"
        elif original:
            suffix = f" {original}"
        pick_number = row.get("effectivePickNumber") or row.get("pickNumber")
        if row["isFuture"]:
            label = f"{row.get('draftYear')} R{row.get('round')}{suffix}"
        else:
            label = f"{row.get('draftYear')} #{pick_number} (R{row.get('round')}){suffix}"
        if row.get("isCompPick"):
            label += " comp"
        row["label"] = label
    return rows


def draft_summary(
    conn: sqlite3.Connection,
    year: int,
    *,
    user_team_id: int | None = None,
    game_id: str | None = None,
    current_date_value: str | None = None,
) -> dict[str, Any]:
    state = None
    queue: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    board: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    user_trade_assets: list[dict[str, Any]] = []
    order_slot_count = 0
    order_finalized = False
    workout_visibility: dict[str, Any] = {}
    logos = team_logo_map(conn)
    if table_exists(conn, "draft_order_slots"):
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM draft_order_slots WHERE draft_year = ?",
            (year,),
        ).fetchone()
        order_slot_count = int(row["count"] or 0)
        if order_slot_count == 32 and table_exists(conn, "draft_picks"):
            mismatch = conn.execute(
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
                (year,),
            ).fetchone()
            order_finalized = int(mismatch["count"] or 0) == 0
    if table_exists(conn, "draft_room_state_view"):
        state = one_as_dict(
            conn.execute(
                "SELECT * FROM draft_room_state_view WHERE draft_year = ?",
                (year,),
            ).fetchone()
        )
    if order_finalized and table_exists(conn, "draft_room_pick_queue_view"):
        queue = rows_as_dicts(
            conn.execute(
                """
                SELECT *
                FROM draft_room_pick_queue_view
                WHERE draft_year = ?
                ORDER BY effective_pick_number
                """,
                (year,),
            ).fetchall()
        )
        if queue and table_exists(conn, "draft_prospects"):
            pick_ids = [int(pick["pick_id"]) for pick in queue if pick.get("pick_id") is not None]
            if pick_ids:
                placeholders = ",".join("?" for _ in pick_ids)
                prospect_columns = relation_columns(conn, "draft_prospects")
                hometown_columns = []
                for column in ("hometown", "hometown_city", "hometown_state", "hometown_region"):
                    hometown_columns.append(column if column in prospect_columns else f"'' AS {column}")
                prospect_rows = rows_as_dicts(
                    conn.execute(
                        f"""
                        SELECT
                            selected_pick_id AS pick_id,
                            prospect_id,
                            first_name || ' ' || last_name AS prospect_name,
                            first_name || ' ' || last_name AS player_name,
                            first_name,
                            last_name,
                            position AS prospect_position,
                            position,
                            position_group,
                            college,
                            college_tier,
                            college_class,
                            {", ".join(hometown_columns)},
                            age,
                            height_in,
                            weight_lbs,
                            arm_length_in,
                            hand_size_in,
                            archetype,
                            primary_role,
                            secondary_role,
                            public_board_rank,
                            scouting_rank,
                            projected_round,
                            projected_pick,
                            scout_lens,
                            scout_confidence,
                            scout_grade,
                            scout_ceiling,
                            scout_risk,
                            scouting_summary,
                            scouting_strengths,
                            scouting_concerns,
                            scouting_projection,
                            scouting_report,
                            medical_flag,
                            medical_risk,
                            interview_trait,
                            interview_grade,
                            late_process_status,
                            late_process_note,
                            public_board_delta,
                            senior_bowl_eligible,
                            senior_bowl_invited,
                            senior_bowl_accepted,
                            status
                        FROM draft_prospects
                        WHERE selected_pick_id IN ({placeholders})
                        """,
                        pick_ids,
                    ).fetchall()
                )
                scout_attributes = draft_scout_attributes(conn, prospect_rows)
                for prospect in prospect_rows:
                    prospect_attributes = scout_attributes.get(int(prospect["prospect_id"]), {})
                    prospect["scout_attributes"] = prospect_attributes.get("attributes", [])
                    prospect["scout_strengths"] = prospect_attributes.get("strengths", [])
                    prospect["scout_concerns"] = prospect_attributes.get("concerns", [])
                    prospect["details_exported"] = True
                prospect_by_pick = {int(row["pick_id"]): row for row in prospect_rows if row.get("pick_id") is not None}
                for pick in queue:
                    prospect = prospect_by_pick.get(int(pick["pick_id"])) if pick.get("pick_id") is not None else None
                    if not prospect:
                        continue
                    pick.setdefault("selected_prospect_id", prospect.get("prospect_id"))
                    pick["selected_prospect_id"] = pick.get("selected_prospect_id") or prospect.get("prospect_id")
                    pick["selected_player_name"] = pick.get("selected_player_name") or prospect.get("prospect_name")
                    pick["selected_player_position"] = pick.get("selected_player_position") or prospect.get("prospect_position")
                    pick["selectedProspect"] = prospect
        for pick in queue:
            team = pick.get("current_team") or pick.get("team")
            pick["teamLogo"] = logos.get(str(team)) if team else None
    current_pick_number = None
    if state:
        try:
            current_pick_number = int(state.get("current_pick_number") or 0)
        except (TypeError, ValueError):
            current_pick_number = None
    user_trade_assets = draft_user_trade_assets(
        conn,
        year,
        user_team_id,
        current_pick_number=current_pick_number,
    )
    if table_exists(conn, "draft_room_events"):
        events = rows_as_dicts(
            conn.execute(
                """
                SELECT event_type, pick_number, round, message, created_at
                FROM draft_room_events
                WHERE draft_year = ?
                ORDER BY event_id DESC
                LIMIT 24
                """,
                (year,),
            ).fetchall()
        )
    if table_exists(conn, "draft_room_board_ui_view"):
        board_columns = relation_columns(conn, "draft_room_board_ui_view")
        draft_date_value = draft_event_date(conn, year)
        draft_day_reveal = False
        if state:
            draft_day_reveal = True
        elif current_date_value and draft_date_value:
            try:
                draft_day_reveal = date.fromisoformat(str(current_date_value)) >= date.fromisoformat(str(draft_date_value))
            except ValueError:
                draft_day_reveal = str(current_date_value) >= str(draft_date_value)
        discovery_columns = []
        for column, fallback in (
            ("public_board_status", "'ranked'"),
            ("discovery_status", "'public_board'"),
            ("scouting_variance", "0"),
            ("discovery_notes", "''"),
            ("college_class", "''"),
            ("hometown", "''"),
            ("hometown_city", "''"),
            ("hometown_state", "''"),
            ("hometown_region", "''"),
            ("senior_bowl_eligible", "0"),
            ("senior_bowl_invited", "0"),
            ("senior_bowl_accepted", "0"),
            ("senior_bowl_result", "''"),
            ("senior_bowl_notes", "''"),
        ):
            discovery_columns.append(column if column in board_columns else f"{fallback} AS {column}")
        process_columns = []
        for column, fallback in (
            ("medical_flag", "'Clean file'"),
            ("medical_risk", "'Clear'"),
            ("medical_notes", "''"),
            ("interview_trait", "'Not logged'"),
            ("interview_grade", "NULL"),
            ("interview_notes", "''"),
            ("late_process_status", "'Stable'"),
            ("late_process_note", "''"),
            ("public_board_delta", "0"),
            ("private_workout_status", "'None logged'"),
            ("private_workout_type", "'None'"),
            ("private_workout_interest", "'Normal'"),
            ("private_workout_grade", "NULL"),
            ("private_workout_note", "''"),
        ):
            process_columns.append(column if column in board_columns else f"{fallback} AS {column}")
        visibility_filter = ""
        params: list[Any] = [year]
        if not draft_day_reveal:
            if game_id and table_exists(conn, "scouting_prospect_progress"):
                visibility_filter = """
                  AND (
                      COALESCE(public_board_status, 'public_board') <> 'off_public_board'
                      OR EXISTS (
                          SELECT 1
                          FROM scouting_prospect_progress spp
                          WHERE spp.game_id = ?
                            AND spp.draft_year = draft_room_board_ui_view.draft_year
                            AND spp.prospect_id = draft_room_board_ui_view.prospect_id
                            AND spp.visibility_status = 'discovered'
                      )
                  )
                """
                params.append(game_id)
            else:
                visibility_filter = " AND COALESCE(public_board_status, 'public_board') <> 'off_public_board'"
        params.append(DRAFT_BOARD_LIMIT)
        board = rows_as_dicts(
            conn.execute(
                f"""
                SELECT
                    prospect_id,
                    public_board_rank,
                    scouting_rank,
                    {", ".join(discovery_columns)},
                    projected_round,
                    projected_pick,
                    first_name || ' ' || last_name AS player_name,
                    first_name,
                    last_name,
                    position,
                    position_group,
                    college,
                    college_tier,
                    age,
                    height_in,
                    weight_lbs,
                    arm_length_in,
                    hand_size_in,
                    archetype,
                    primary_role,
                    secondary_role,
                    scout_lens,
                    scout_confidence,
                    scout_grade,
                    scout_ceiling,
                    scout_risk,
                    scouting_summary,
                    scouting_strengths,
                    scouting_concerns,
                    scouting_projection,
                    scouting_report,
                    {", ".join(process_columns)},
                    combine_status,
                    combine_grade,
                    athletic_score,
                    drills_completed,
                    forty_yard_dash,
                    ten_yard_split,
                    bench_press_reps,
                    vertical_jump_in,
                    broad_jump_in,
                    three_cone_sec,
                    twenty_yard_shuttle_sec,
                    sixty_yard_shuttle_sec,
                    combine_injured,
                    combine_top_skip,
                    pro_day_status,
                    pro_day_grade,
                    pro_day_athletic_score,
                    pro_day_forty_yard_dash,
                    pro_day_vertical_jump_in,
                    pro_day_broad_jump_in,
                    pro_day_improved_from_combine,
                    pro_day_medical_recheck
                FROM draft_room_board_ui_view
                WHERE draft_year = ?
                {visibility_filter}
                ORDER BY COALESCE(public_board_rank, scouting_rank, 9999), prospect_id
                LIMIT ?
                """,
                params,
            ).fetchall()
        )
        detailed_board = board[:DRAFT_BOARD_DETAIL_LIMIT]
        scout_attributes = draft_scout_attributes(conn, detailed_board)
        for prospect in board:
            prospect_attributes = scout_attributes.get(int(prospect["prospect_id"]), {})
            prospect["scout_attributes"] = prospect_attributes.get("attributes", [])
            prospect["scout_strengths"] = prospect_attributes.get("strengths", [])
            prospect["scout_concerns"] = prospect_attributes.get("concerns", [])
        workout_visibility = mask_draft_workouts_for_calendar(conn, board, year, current_date_value)
        slim_draft_board_rows(board)
    if table_exists(conn, "draft_classes"):
        classes = rows_as_dicts(
            conn.execute(
                "SELECT * FROM draft_classes ORDER BY draft_year DESC LIMIT 6"
            ).fetchall()
        )
    selections = draft_selection_ticker(conn, year)
    user_selections = draft_user_selections(conn, year, user_team_id)
    pick_totals = {"total": 0, "used": 0, "remaining": 0}
    if table_exists(conn, "draft_picks"):
        row = conn.execute(
            """
            SELECT COUNT(*) AS total, SUM(CASE WHEN is_used = 1 THEN 1 ELSE 0 END) AS used
            FROM draft_picks
            WHERE draft_year = ?
            """,
            (year,),
        ).fetchone()
        total = int(row["total"] or 0)
        used = int(row["used"] or 0)
        pick_totals = {"total": total, "used": used, "remaining": total - used}
    return {
        "year": year,
        "draftDate": draft_event_date(conn, year),
        "state": state,
        "orderFinalized": order_finalized,
        "orderSlotCount": order_slot_count,
        "orderWarning": None if order_finalized else f"{year} draft order is not finalized ({order_slot_count}/32 slots).",
        "pickQueue": queue,
        "events": events,
        "selections": selections,
        "userSelections": user_selections,
        "userTradeAssets": user_trade_assets,
        "board": board,
        "classes": classes,
        "pickTotals": pick_totals,
        "workoutVisibility": workout_visibility,
    }


def enrich_scouting_payload_with_draft_board(scouting_payload: dict[str, Any], draft_payload: dict[str, Any]) -> dict[str, Any]:
    """Reuse draft-board detail rows so scouting prospects can open the same rich card."""
    board = scouting_payload.get("board") or []
    draft_board = draft_payload.get("board") or []
    if not board or not draft_board:
        return scouting_payload
    draft_by_id = {
        int(row["prospect_id"]): row
        for row in draft_board
        if row.get("prospect_id") is not None
    }
    detail_fields = [
        "first_name",
        "last_name",
        "arm_length_in",
        "hand_size_in",
        "archetype",
        "primary_role",
        "secondary_role",
        "scouting_variance",
        "scouting_strengths",
        "scouting_concerns",
        "scouting_projection",
        "scouting_report",
        "medical_flag",
        "medical_risk",
        "medical_notes",
        "interview_trait",
        "interview_grade",
        "interview_notes",
        "late_process_status",
        "late_process_note",
        "public_board_delta",
        "private_workout_status",
        "private_workout_type",
        "private_workout_interest",
        "private_workout_grade",
        "private_workout_note",
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
        "pro_day_status",
        "pro_day_grade",
        "pro_day_athletic_score",
        "pro_day_forty_yard_dash",
        "pro_day_vertical_jump_in",
        "pro_day_broad_jump_in",
        "pro_day_improved_from_combine",
        "pro_day_medical_recheck",
    ]
    for prospect in board:
        draft_row = draft_by_id.get(int(prospect.get("prospect_id") or 0))
        if not draft_row:
            continue
        for field in detail_fields:
            if field not in prospect or prospect[field] in (None, ""):
                prospect[field] = draft_row.get(field)
        prospect["details_exported"] = draft_row.get("details_exported", prospect.get("details_exported", False))
        prospect["scout_attributes"] = draft_row.get("scout_attributes", prospect.get("scout_attributes", []))
        prospect["scout_strengths"] = draft_row.get("scout_strengths", prospect.get("scout_strengths", []))
        prospect["scout_concerns"] = draft_row.get("scout_concerns", prospect.get("scout_concerns", []))
    if hasattr(scouting, "mask_unavailable_workouts"):
        scouting.mask_unavailable_workouts(scouting_payload)
    return scouting_payload


def free_agency_summary(conn: sqlite3.Connection, league_year: int) -> dict[str, Any]:
    period = None
    board: list[dict[str, Any]] = []
    offers: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    team_logos: dict[str, str] = {}
    best_offers: dict[int, dict[str, Any]] = {}
    if table_exists(conn, "free_agency_periods"):
        period = one_as_dict(
            conn.execute(
                "SELECT * FROM free_agency_periods WHERE league_year = ?",
                (league_year,),
            ).fetchone()
        )
    if not period and table_exists(conn, "league_phase_windows"):
        game_settings = settings(conn)
        current_date = game_settings.get("current_game_date")
        if not current_date:
            active = active_save(conn) or {}
            current_date = active.get("current_date")
        if current_date:
            phase = conn.execute(
                """
                SELECT *
                FROM league_phase_windows
                WHERE ? BETWEEN start_date AND end_date
                ORDER BY league_year DESC, sort_order DESC
                LIMIT 1
                """,
                (current_date,),
            ).fetchone()
            if phase and int(phase["transactions_open"] or 0):
                period = {
                    "league_year": league_year,
                    "status": "active",
                    "current_stage": "street_market",
                    "current_date": current_date,
                    "current_hour": None,
                    "day_count": None,
                    "first_day_start_hour": None,
                    "first_day_end_hour": None,
                    "started_at": None,
                    "updated_at": None,
                    "completed_at": None,
                    "notes": "Street free agency is open during the current transaction window.",
                    "virtual": True,
                }
    if table_exists(conn, "free_agency_board_view"):
        board = rows_as_dicts(
            conn.execute(
                """
                SELECT
                    *,
                    MAX(COALESCE(asking_aav, 0), COALESCE(minimum_aav, 0)) AS offer_floor_aav
                FROM free_agency_board_view
                WHERE league_year = ?
                ORDER BY
                    CASE market_status WHEN 'available' THEN 0 WHEN 'signed' THEN 1 ELSE 2 END,
                    market_heat DESC,
                    asking_aav DESC
                LIMIT 100
                """,
                (league_year,),
            ).fetchall()
        )
    if not board and table_exists(conn, "free_agent_pool_view"):
        board = rows_as_dicts(
            conn.execute(
                """
                SELECT
                    player_id,
                    player_name,
                    position,
                    position_group,
                    market_tier,
                    asking_aav,
                    minimum_aav,
                    MAX(COALESCE(asking_aav, 0), COALESCE(minimum_aav, 0)) AS offer_floor_aav,
                    NULL AS market_status,
                    NULL AS pending_offers,
                    NULL AS best_aav,
                    NULL AS market_heat,
                    motivation,
                    signing_notes
                FROM free_agent_pool_view
                ORDER BY asking_aav DESC, player_name
                LIMIT 100
                """
            ).fetchall()
        )
    for player in board:
        asking = int(player.get("asking_aav") or 0)
        minimum = int(player.get("minimum_aav") or 0)
        player["offer_floor_aav"] = max(asking, minimum)
    if table_exists(conn, "free_agency_offers_view"):
        offers = rows_as_dicts(
            conn.execute(
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
        )
        best_offer_rows = rows_as_dicts(
            conn.execute(
                """
                WITH ranked AS (
                    SELECT
                        o.player_id,
                        o.team,
                        o.team_name,
                        o.aav,
                        ROW_NUMBER() OVER (
                            PARTITION BY o.player_id
                            ORDER BY o.aav DESC, o.signing_bonus DESC, o.offer_id DESC
                        ) AS rn
                    FROM free_agency_offers_view o
                    WHERE o.league_year = ?
                      AND o.status = 'pending'
                )
                SELECT *
                FROM ranked
                WHERE rn = 1
                """,
                (league_year,),
            ).fetchall()
        )
        best_offers = {int(row["player_id"]): row for row in best_offer_rows}
    if table_exists(conn, "team_graphics_assets"):
        rows = rows_as_dicts(
            conn.execute(
                """
                WITH ranked AS (
                    SELECT
                        t.abbreviation,
                        a.local_path,
                        ROW_NUMBER() OVER (
                            PARTITION BY t.team_id
                            ORDER BY
                                CASE a.variant
                                    WHEN 'scoreboard' THEN 0
                                    WHEN 'primary' THEN 1
                                    WHEN 'dark' THEN 2
                                    ELSE 9
                                END,
                                a.asset_id
                        ) AS rn
                    FROM team_graphics_assets a
                    JOIN teams t ON t.team_id = a.team_id
                    WHERE a.asset_type = 'logo'
                )
                SELECT abbreviation, local_path
                FROM ranked
                WHERE rn = 1
                """
            ).fetchall()
        )
        team_logos = {
            row["abbreviation"]: "/" + str(row["local_path"]).replace("\\", "/").lstrip("/")
            for row in rows
        }
    for player in board:
        best = best_offers.get(int(player["player_id"]))
        if best:
            player["best_offer_team"] = best.get("team")
            player["best_offer_team_name"] = best.get("team_name")
            player["best_offer_team_logo"] = team_logos.get(str(best.get("team")))
    if table_exists(conn, "free_agency_events"):
        events = rows_as_dicts(
            conn.execute(
                """
                SELECT event_date, event_hour, event_type, message, created_at
                FROM free_agency_events
                WHERE league_year = ?
                ORDER BY event_id DESC
                LIMIT 32
                """,
                (league_year,),
            ).fetchall()
        )
    market_counts = {"available": 0, "signed": 0, "pendingOffers": 0}
    if table_exists(conn, "free_agency_player_markets"):
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM free_agency_player_markets
            WHERE league_year = ?
            GROUP BY status
            """,
            (league_year,),
        ).fetchall()
        for row in rows:
            market_counts[row["status"]] = int(row["count"] or 0)
    if market_counts["available"] == 0 and not period and table_exists(conn, "free_agent_pool_view"):
        row = conn.execute("SELECT COUNT(*) AS count FROM free_agent_pool_view").fetchone()
        market_counts["available"] = int(row["count"] or 0) if row else 0
    if table_exists(conn, "free_agency_offers"):
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM free_agency_offers
            WHERE league_year = ? AND status = 'pending'
            """,
            (league_year,),
        ).fetchone()
        market_counts["pendingOffers"] = int(row["count"] or 0) if row else 0
    return {
        "leagueYear": league_year,
        "startDate": free_agency_start_date(conn, league_year),
        "period": period,
        "board": board,
        "offers": offers,
        "events": events,
        "counts": market_counts,
    }


def free_agency_start_date(conn: sqlite3.Connection, league_year: int) -> str:
    if table_exists(conn, "league_calendar_events"):
        row = conn.execute(
            """
            SELECT event_start_date
            FROM league_calendar_events
            WHERE event_code = 'NEXT_NFL_LEAGUE_YEAR_START'
              AND strftime('%Y', event_start_date) = ?
            ORDER BY event_start_date
            LIMIT 1
            """,
            (str(league_year),),
        ).fetchone()
        if row and row["event_start_date"]:
            return str(row["event_start_date"])
    return f"{league_year}-03-10"


def free_agency_league_year(
    conn: sqlite3.Connection,
    *,
    current_season: int,
    current_date: str | None,
    draft_year_value: int,
    game_settings: dict[str, str] | None = None,
) -> int:
    settings_payload = game_settings or settings(conn)
    contract_year = int(
        settings_payload.get("current_contract_year")
        or settings_payload.get("current_league_year")
        or current_season
    )
    fa_start = free_agency_start_date(conn, draft_year_value)
    if current_date and current_date >= fa_start:
        return draft_year_value
    return contract_year


def contract_negotiation_summary(
    conn: sqlite3.Connection,
    season: int,
    user_team: str | None,
) -> dict[str, Any]:
    if not user_team:
        return {
            "season": season,
            "team": None,
            "cap": None,
            "projectedCap": None,
            "currentCap": None,
            "expiring": [],
            "fifthYearOptions": [],
            "capCasualties": [],
            "restructureCandidates": [],
            "counts": {"total": 0, "priority": 0, "negotiable": 0, "capCasualties": 0, "restructures": 0},
        }
    try:
        expiring = contract_negotiations.expiring_players(conn, user_team, season)
        fifth_year_options = contract_negotiations.fifth_year_option_candidates(conn, user_team, season + 1)
        current_cap = contract_negotiations.cap_summary(conn, user_team)
        projected_cap = contract_negotiations.projected_cap_summary(conn, user_team, season + 1)
        cap_casualties = contract_negotiations.cap_casualty_candidates(conn, user_team, season + 1)
        restructure_candidates = contract_negotiations.restructure_candidates(conn, user_team, season + 1)
    except Exception as exc:
        return {
            "season": season,
            "team": user_team,
            "cap": None,
            "projectedCap": None,
            "currentCap": None,
            "expiring": [],
            "fifthYearOptions": [],
            "capCasualties": [],
            "restructureCandidates": [],
            "error": str(exc),
            "counts": {"total": 0, "priority": 0, "negotiable": 0, "capCasualties": 0, "restructures": 0},
        }
    return {
        "season": season,
        "team": user_team,
        "extensionStartYear": season + 1,
        "cap": projected_cap,
        "projectedCap": projected_cap,
        "currentCap": current_cap,
        "expiring": expiring,
        "fifthYearOptions": fifth_year_options,
        "capCasualties": cap_casualties,
        "restructureCandidates": restructure_candidates,
        "counts": {
            "total": len(expiring),
            "fifthYearOptions": len(fifth_year_options),
            "priority": sum(1 for player in expiring if player.get("priority") == "Priority"),
            "negotiable": sum(1 for player in expiring if player.get("priority") == "Negotiable"),
            "tagCandidates": sum(
                1
                for player in expiring
                if player.get("rights_type") == "UFA"
                and player.get("franchise_tag_aav")
                and float(player.get("market_score") or 0) >= 76
            ),
            "rfaCandidates": sum(1 for player in expiring if player.get("rights_type") == "RFA"),
            "erfaCandidates": sum(1 for player in expiring if player.get("rights_type") == "ERFA"),
            "capCasualties": len(cap_casualties),
            "restructures": len(restructure_candidates),
        },
    }


def player_leaders(
    conn: sqlite3.Connection,
    season: int,
    *,
    stat_keys: list[str],
    sort_key: str,
    limit: int = 15,
    positions: list[str] | None = None,
    exclude_positions: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "season_player_stats_view"):
        return []
    aliases = {key: key for key in stat_keys}
    stat_placeholders = ", ".join("?" for _key in stat_keys)
    select_stats = ",\n".join(
        f"SUM(CASE WHEN stat_key = ? THEN stat_value ELSE 0 END) AS {alias}"
        for key, alias in aliases.items()
    )
    position_sql = ""
    position_params: list[Any] = []
    if positions:
        position_sql = f" AND position IN ({', '.join('?' for _ in positions)})"
        position_params.extend(positions)
    if exclude_positions:
        position_sql += f" AND position NOT IN ({', '.join('?' for _ in exclude_positions)})"
        position_params.extend(exclude_positions)
    params: list[Any] = [
        *stat_keys,
        season,
        *stat_keys,
        *position_params,
        limit,
    ]
    rows = conn.execute(
        f"""
        SELECT
            player_id,
            player_name,
            position,
            team,
            {select_stats}
        FROM season_player_stats_view
        WHERE season = ?
          AND stat_key IN ({stat_placeholders})
          {position_sql}
        GROUP BY player_id, player_name, position, team
        HAVING {sort_key} > 0
        ORDER BY {sort_key} DESC, player_name
        LIMIT ?
        """,
        params,
    ).fetchall()
    return rows_as_dicts(rows)


def stat_leaders(conn: sqlite3.Connection, season: int) -> dict[str, Any]:
    passing = player_leaders(
        conn,
        season,
        stat_keys=[
            "pass_yards",
            "pass_tds",
            "pass_completions",
            "pass_attempts",
            "interceptions_thrown",
            "interceptions",
            "sacks_taken",
        ],
        sort_key="pass_yards",
        positions=["QB"],
    )
    for row in passing:
        row["interceptions_thrown"] = row.get("interceptions_thrown") or row.get("interceptions") or 0
    kicking = player_leaders(
        conn,
        season,
        stat_keys=["fg_made", "fg_attempts", "xp_made", "xp_attempts", "long_fg"],
        sort_key="fg_made",
        positions=["K", "PK"],
    )
    for row in kicking:
        long_fg = row.get("long_fg") or 0
        if long_fg and long_fg > 100:
            row["long_fg"] = min(66, round(float(long_fg) / 10.0))
        row["field_goals_made"] = row.get("fg_made") or 0
        row["field_goal_attempts"] = row.get("fg_attempts") or 0
        row["extra_points_made"] = row.get("xp_made") or 0
        row["extra_point_attempts"] = row.get("xp_attempts") or 0

    return {
        "passing": passing,
        "rushing": player_leaders(
            conn,
            season,
            stat_keys=["rush_yards", "rush_tds", "rush_attempts"],
            sort_key="rush_yards",
        ),
        "receiving": player_leaders(
            conn,
            season,
            stat_keys=["receiving_yards", "receiving_tds", "receptions", "targets"],
            sort_key="receiving_yards",
        ),
        "sacks": player_leaders(
            conn,
            season,
            stat_keys=["sacks", "tackles", "forced_fumbles"],
            sort_key="sacks",
            exclude_positions=["QB"],
        ),
        "tackles": player_leaders(
            conn,
            season,
            stat_keys=["tackles", "sacks", "interceptions", "forced_fumbles"],
            sort_key="tackles",
            exclude_positions=["QB"],
        ),
        "interceptions": player_leaders(
            conn,
            season,
            stat_keys=["interceptions", "pass_deflections", "solo_tackles", "assisted_tackles", "tackles"],
            sort_key="interceptions",
            exclude_positions=["QB"],
        ),
        "kicking": kicking,
        "snaps": player_leaders(
            conn,
            season,
            stat_keys=["offensive_snaps", "defensive_snaps", "special_teams_snaps", "total_snaps"],
            sort_key="total_snaps",
        ),
    }


def best_roles(conn: sqlite3.Connection, season: int, player_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not player_ids or not table_exists(conn, "player_role_scores"):
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT player_id, role_key, role_score
        FROM player_role_scores
        WHERE season = ? AND scheme_key = 'default' AND player_id IN ({placeholders})
        ORDER BY player_id, role_score DESC
        """,
        [season, *player_ids],
    ).fetchall()
    roles: dict[int, dict[str, Any]] = {}
    for row in rows:
        player_id = int(row["player_id"])
        if player_id not in roles:
            roles[player_id] = {
                "key": row["role_key"],
                "score": round(float(row["role_score"] or 0), 1),
            }
    return roles


def flex_positions(conn: sqlite3.Connection, player_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not player_ids or not table_exists(conn, "player_position_flex"):
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT player_id, position, experience, potential, is_primary
        FROM player_position_flex
        WHERE player_id IN ({placeholders})
        ORDER BY player_id, is_primary DESC, experience DESC, potential DESC, position
        """,
        player_ids,
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["player_id"]), []).append({
            "position": row["position"],
            "current": int(row["experience"] or 0),
            "potential": int(row["potential"] or 0),
            "primary": bool(row["is_primary"]),
        })
    return grouped


def depth_chart_summary(conn: sqlite3.Connection, team: str | None, season: int) -> dict[str, Any]:
    if not team or not table_exists(conn, "depth_charts"):
        return {"team": team, "rows": [], "roster": [], "units": []}
    team_row = conn.execute(
        "SELECT team_id, abbreviation, city || ' ' || nickname AS team_name FROM teams WHERE abbreviation = ?",
        (team.upper(),),
    ).fetchone()
    if not team_row:
        return {"team": team, "rows": [], "roster": [], "units": []}
    team_id = int(team_row["team_id"])
    roster_rows = conn.execute(
        """
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.overall,
            p.potential,
            p.age,
            p.jersey_number,
            p.status,
            p.is_rookie,
            p.height_in,
            p.weight_lbs,
            cy.cap_hit AS contract_cap_hit,
            cy.cash_due AS contract_cash_due,
            cy.base_salary AS contract_base_salary,
            c.contract_id,
            c.start_year AS contract_start_year,
            c.end_year AS contract_end_year,
            c.aav AS contract_aav,
            c.total_value AS contract_total_value,
            c.total_years AS contract_total_years,
            c.contract_type
        FROM players p
        LEFT JOIN contract_years cy
          ON cy.player_id = p.player_id
         AND cy.team_id = p.team_id
         AND cy.season = ?
         AND COALESCE(cy.is_active, 1) = 1
        LEFT JOIN contracts c
          ON c.contract_id = cy.contract_id
         AND c.player_id = p.player_id
         AND c.team_id = p.team_id
         AND COALESCE(c.is_active, 1) = 1
        WHERE p.team_id = ?
          AND COALESCE(p.status, 'Active') != 'Retired'
        ORDER BY p.position, p.last_name, p.first_name
        """,
        (season, team_id),
    ).fetchall()
    player_ids = [int(row["player_id"]) for row in roster_rows]
    roles = best_roles(conn, season, player_ids)
    flex = flex_positions(conn, player_ids)
    headshots = export_player_profile_ui_data.headshots(conn)
    roster = []
    for row in roster_rows:
        player_id = int(row["player_id"])
        roster.append({
            "player_id": player_id,
            "player_name": row["player_name"],
            "position": row["position"],
            "overall": row["overall"],
            "potential": row["potential"],
            "age": row["age"],
            "jersey_number": row["jersey_number"],
            "status": row["status"] or "Active",
            "is_rookie": bool(row["is_rookie"]),
            "height_in": row["height_in"],
            "weight_lbs": row["weight_lbs"],
            "headshot": headshots.get(player_id),
            "contract": {
                "contract_id": row["contract_id"],
                "start_year": row["contract_start_year"],
                "end_year": row["contract_end_year"],
                "aav": row["contract_aav"],
                "total_value": row["contract_total_value"],
                "total_years": row["contract_total_years"],
                "cap_hit": row["contract_cap_hit"],
                "cash_due": row["contract_cash_due"],
                "base_salary": row["contract_base_salary"],
                "type": row["contract_type"],
            } if row["contract_id"] is not None else None,
            "role": roles.get(player_id, {}),
            "flex": flex.get(player_id, []),
        })

    depth_rows = rows_as_dicts(
        conn.execute(
            """
            SELECT
                dc.depth_chart_id,
                dc.unit,
                dc.position AS slot,
                dc.depth_rank,
                p.player_id,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position,
                p.overall,
                p.potential,
                p.age,
                p.jersey_number
            FROM depth_charts dc
            JOIN players p ON p.player_id = dc.player_id
            WHERE dc.team_id = ?
            ORDER BY
                CASE dc.unit
                    WHEN 'Offense' THEN 1
                    WHEN 'Defense' THEN 2
                    WHEN 'Special Teams' THEN 3
                    ELSE 9
                END,
                dc.position,
                dc.depth_rank
            """,
            (team_id,),
        ).fetchall()
    )
    units: dict[str, dict[str, Any]] = {}
    for row in depth_rows:
        row["role"] = roles.get(int(row["player_id"]), {})
        unit = units.setdefault(row["unit"], {"unit": row["unit"], "slots": {}})
        slot = unit["slots"].setdefault(row["slot"], {"slot": row["slot"], "players": []})
        slot["players"].append(row)
    unit_list = []
    for unit in units.values():
        unit_list.append({
            "unit": unit["unit"],
            "slots": list(unit["slots"].values()),
        })
    return {
        "team": team_row["abbreviation"],
        "teamName": team_row["team_name"],
        "rows": depth_rows,
        "roster": roster,
        "units": unit_list,
    }


def ai_gm_summary(
    conn: sqlite3.Connection,
    team: str | None,
    game_id: str | None,
    season: int,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "team": team,
        "gameId": game_id,
        "config": None,
        "profile": None,
        "autonomy": None,
        "dailyRuns": [],
        "reviewInbox": [],
        "reviewActivity": [],
        "reviewStatusCounts": {},
        "evaluation": None,
        "evaluationError": None,
        "cutdownPlan": None,
        "cutdownPlanError": None,
        "cutdownPlans": [],
        "contractPlan": None,
        "contractPlanError": None,
        "contractPlans": [],
        "draftPlan": None,
        "draftPlanError": None,
        "draftPlans": [],
        "freeAgentPlan": None,
        "freeAgentPlanError": None,
        "freeAgentPlans": [],
        "practiceSquad": None,
        "practiceSquadError": None,
        "ops": None,
        "opsError": None,
        "queue": [],
        "logs": [],
        "counts": {
            "profiles": 0,
            "logs": 0,
            "dailyRuns": 0,
            "reviewInbox": 0,
            "reviewActivity": 0,
            "cutdownPlans": 0,
            "contractPlans": 0,
            "draftPlans": 0,
            "freeAgentPlans": 0,
            "practiceSquadEligible": 0,
            "ops": 0,
            "queue": 0,
        },
    }
    if table_exists(conn, "ai_gm_profiles"):
        row = conn.execute("SELECT COUNT(*) AS count FROM ai_gm_profiles").fetchone()
        summary["counts"]["profiles"] = int(row["count"] or 0) if row else 0
    if game_id and table_exists(conn, "ai_gm_llm_config"):
        summary["config"] = one_as_dict(
            conn.execute(
                "SELECT * FROM ai_gm_llm_config WHERE game_id = ?",
                (game_id,),
            ).fetchone()
        )
    if team and table_exists(conn, "ai_gm_profiles_view"):
        summary["profile"] = one_as_dict(
            conn.execute(
                "SELECT * FROM ai_gm_profiles_view WHERE abbreviation = ?",
                (team.upper(),),
            ).fetchone()
        )
    if table_exists(conn, "ai_gm_autonomy_settings_view"):
        rows = rows_as_dicts(
            conn.execute(
                """
                SELECT *
                FROM ai_gm_autonomy_settings_view
                WHERE game_id = ?
                  AND (team IS NULL OR team = ?)
                ORDER BY team IS NOT NULL DESC, team
                LIMIT 2
                """,
                (game_id or "master", team.upper() if team else None),
            ).fetchall()
        )
        summary["autonomy"] = rows[0] if rows else None
    if table_exists(conn, "ai_gm_daily_runs_view"):
        params: list[Any] = []
        where = []
        if game_id:
            where.append("game_id = ?")
            params.append(game_id)
        if team:
            where.append("(scope_team = ? OR all_teams = 1)")
            params.append(team.upper())
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        summary["dailyRuns"] = decorate_ai_gm_daily_runs(rows_as_dicts(
            conn.execute(
                f"""
                SELECT *
                FROM ai_gm_daily_runs_view
                {clause}
                ORDER BY created_at DESC, run_id DESC
                LIMIT 8
                """,
                params,
            ).fetchall()
        ))
        summary["counts"]["dailyRuns"] = len(summary["dailyRuns"])
    if table_exists(conn, "ai_gm_review_items_view"):
        params = []
        where = ["lifecycle_status IN ('pending_review', 'blocked', 'approved')"]
        if game_id:
            where.append("game_id = ?")
            params.append(game_id)
        if team:
            where.append("team = ?")
            params.append(team.upper())
        clause = f"WHERE {' AND '.join(where)}"
        summary["reviewInbox"] = decorate_ai_gm_review_rows(rows_as_dicts(
            conn.execute(
                f"""
                SELECT *
                FROM ai_gm_review_items_view
                {clause}
                ORDER BY
                    CASE lifecycle_status
                        WHEN 'pending_review' THEN 0
                        WHEN 'blocked' THEN 1
                        WHEN 'approved' THEN 2
                        ELSE 3
                    END,
                    CASE risk_tier WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                    priority DESC,
                    created_at DESC,
                    review_id DESC
                LIMIT 12
                """,
                params,
            ).fetchall()
        ))
        summary["counts"]["reviewInbox"] = len(summary["reviewInbox"])
        count_params: list[Any] = []
        count_where = []
        if game_id:
            count_where.append("game_id = ?")
            count_params.append(game_id)
        if team:
            count_where.append("team = ?")
            count_params.append(team.upper())
        count_clause = f"WHERE {' AND '.join(count_where)}" if count_where else ""
        summary["reviewStatusCounts"] = {
            row["lifecycle_status"]: int(row["count"] or 0)
            for row in conn.execute(
                f"""
                SELECT lifecycle_status, COUNT(*) AS count
                FROM ai_gm_review_items_view
                {count_clause}
                GROUP BY lifecycle_status
                """,
                count_params,
            ).fetchall()
        }
        activity_params: list[Any] = []
        activity_where = []
        if game_id:
            activity_where.append("r.game_id = ?")
            activity_params.append(game_id)
        if team:
            activity_where.append("t.abbreviation = ?")
            activity_params.append(team.upper())
        activity_clause = f"WHERE {' AND '.join(activity_where)}" if activity_where else ""
        summary["reviewActivity"] = decorate_ai_gm_review_rows(rows_as_dicts(
            conn.execute(
                f"""
                SELECT
                    r.review_id,
                    r.game_id,
                    r.team_id,
                    t.abbreviation AS team,
                    t.city || ' ' || t.nickname AS team_name,
                    r.run_id,
                    r.review_date,
                    r.season,
                    r.phase_code,
                    r.item_type,
                    r.artifact_type,
                    r.artifact_id,
                    r.operation_type,
                    r.decision_type,
                    r.risk_tier,
                    r.priority,
                    r.title,
                    r.summary,
                    r.lifecycle_status,
                    r.review_note,
                    r.reviewed_at,
                    r.reviewed_by,
                    r.applied_at,
                    r.apply_result_json,
                    r.apply_error,
                    r.detail_json,
                    r.created_at,
                    r.updated_at
                FROM ai_gm_review_items r
                JOIN teams t ON t.team_id = r.team_id
                {activity_clause}
                ORDER BY COALESCE(r.applied_at, r.reviewed_at, r.updated_at, r.created_at) DESC,
                         r.review_id DESC
                LIMIT 16
                """,
                activity_params,
            ).fetchall()
        ))
        summary["counts"]["reviewActivity"] = len(summary["reviewActivity"])
    if team:
        try:
            summary["evaluation"] = team_eval.evaluate_team(
                conn,
                team_abbr=team,
                season=season,
                game_id=game_id or "master",
                persist=False,
            )
        except Exception as exc:
            summary["evaluationError"] = str(exc)
        try:
            summary["cutdownPlan"] = cutdown_planner.build_cutdown_plan(
                conn,
                team_abbr=team,
                season=season,
                game_id=game_id or "master",
                persist=False,
            )
        except Exception as exc:
            summary["cutdownPlanError"] = str(exc)
        try:
            roster_rules.ensure_schema(conn)
            roster_rules.seed_rules(conn)
            team_row = roster_rules.get_team(conn, team)
            rule_set = roster_rules.practice_squad_rule_set(conn, season, "Regular Season")
            usage = roster_rules.practice_squad_usage(conn, int(team_row["team_id"]), rule_set)
            candidates = roster_rules.practice_squad_eligibility_rows(
                conn,
                team=team_row,
                season=season,
                rule_set=rule_set,
                include_active=True,
                include_all_active=False,
                include_current=True,
                include_blocked=True,
                limit=24,
            )
            summary["practiceSquad"] = {
                "rules": {
                    "phase": rule_set["phase"],
                    "base_limit": int(rule_set["practice_squad_limit"] or 0),
                    "international_exemption_limit": int(rule_set["practice_squad_international_exemption_limit"] or 0),
                    "developmental_limit": int(rule_set["practice_squad_developmental_limit"] or 0),
                    "veteran_exception_limit": int(rule_set["practice_squad_veteran_exception_limit"] or 0),
                    "elevation_limit": int(rule_set["practice_squad_elevation_limit"] or 0),
                    "weekly_elevation_limit": int(rule_set["practice_squad_weekly_elevation_limit"] or 0),
                    "source_url": rule_set["source_url"],
                    "notes": rule_set["notes"],
                },
                "usage": usage,
                "candidates": candidates,
            }
            summary["counts"]["practiceSquadEligible"] = len([row for row in candidates if row.get("eligible")])
        except Exception as exc:
            summary["practiceSquadError"] = str(exc)
        try:
            summary["contractPlan"] = contract_planner.build_contract_plan(
                conn,
                team_abbr=team,
                season=season,
                game_id=game_id or "master",
                persist=False,
            )
        except Exception as exc:
            summary["contractPlanError"] = str(exc)
        try:
            summary["draftPlan"] = draft_planner.build_draft_plan(
                conn,
                team_abbr=team,
                draft_year=season + 1,
                season=season,
                game_id=game_id or "master",
                persist=False,
                board_limit=60,
            )
        except Exception as exc:
            summary["draftPlanError"] = str(exc)
        try:
            summary["freeAgentPlan"] = free_agent_planner.build_free_agent_plan(
                conn,
                team_abbr=team,
                league_year=season,
                season=season,
                game_id=game_id or "master",
                persist=False,
            )
        except Exception as exc:
            summary["freeAgentPlanError"] = str(exc)
        try:
            summary["ops"] = ops_controller.build_operations(
                conn,
                game_id=game_id or "master",
                team_abbr=team,
                all_teams=False,
                phase="auto",
                limit=8,
            )
            summary["counts"]["ops"] = summary["ops"]["counts"]["operations"]
        except Exception as exc:
            summary["opsError"] = str(exc)
    if table_exists(conn, "ai_gm_cutdown_plans_view"):
        params = []
        where = []
        if game_id:
            where.append("game_id = ?")
            params.append(game_id)
        if team:
            where.append("team = ?")
            params.append(team.upper())
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        summary["cutdownPlans"] = rows_as_dicts(
            conn.execute(
                f"""
                SELECT *
                FROM ai_gm_cutdown_plans_view
                {clause}
                ORDER BY created_at DESC, plan_id DESC
                LIMIT 8
                """,
                params,
            ).fetchall()
        )
        summary["counts"]["cutdownPlans"] = len(summary["cutdownPlans"])
    if table_exists(conn, "ai_gm_contract_plans_view"):
        params = []
        where = []
        if game_id:
            where.append("game_id = ?")
            params.append(game_id)
        if team:
            where.append("team = ?")
            params.append(team.upper())
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        summary["contractPlans"] = rows_as_dicts(
            conn.execute(
                f"""
                SELECT *
                FROM ai_gm_contract_plans_view
                {clause}
                ORDER BY created_at DESC, plan_id DESC
                LIMIT 8
                """,
                params,
            ).fetchall()
        )
        summary["counts"]["contractPlans"] = len(summary["contractPlans"])
    if table_exists(conn, "ai_gm_draft_plans_view"):
        params = []
        where = []
        if game_id:
            where.append("game_id = ?")
            params.append(game_id)
        if team:
            where.append("team = ?")
            params.append(team.upper())
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        summary["draftPlans"] = rows_as_dicts(
            conn.execute(
                f"""
                SELECT *
                FROM ai_gm_draft_plans_view
                {clause}
                ORDER BY created_at DESC, plan_id DESC
                LIMIT 8
                """,
                params,
            ).fetchall()
        )
        summary["counts"]["draftPlans"] = len(summary["draftPlans"])
    if table_exists(conn, "ai_gm_free_agent_plans_view"):
        params = []
        where = []
        if game_id:
            where.append("game_id = ?")
            params.append(game_id)
        if team:
            where.append("team = ?")
            params.append(team.upper())
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        summary["freeAgentPlans"] = rows_as_dicts(
            conn.execute(
                f"""
                SELECT *
                FROM ai_gm_free_agent_plans_view
                {clause}
                ORDER BY created_at DESC, plan_id DESC
                LIMIT 8
                """,
                params,
            ).fetchall()
        )
        summary["counts"]["freeAgentPlans"] = len(summary["freeAgentPlans"])
    if table_exists(conn, "ai_gm_decision_queue"):
        params = []
        where = ["q.status IN ('queued', 'running')"]
        if game_id:
            where.append("q.game_id = ?")
            params.append(game_id)
        if team:
            where.append("t.abbreviation = ?")
            params.append(team.upper())
        clause = f"WHERE {' AND '.join(where)}"
        queue_rows = rows_as_dicts(
            conn.execute(
                f"""
                SELECT
                    q.decision_id,
                    q.game_id,
                    q.decision_date,
                    q.decision_type,
                    q.status,
                    q.priority,
                    q.context_json,
                    q.created_at,
                    q.updated_at,
                    t.abbreviation AS team
                FROM ai_gm_decision_queue q
                JOIN teams t ON t.team_id = q.team_id
                {clause}
                ORDER BY q.priority DESC, q.decision_date ASC, q.decision_id ASC
                LIMIT 12
                """,
                params,
            ).fetchall()
        )
        for row in queue_rows:
            context_json = row.pop("context_json", None)
            try:
                context = json.loads(context_json or "{}")
                operation = context.get("ai_gm_operation") or {}
                row["operation_type"] = operation.get("operation_type")
                row["ops_phase"] = operation.get("ops_phase")
                row["summary"] = operation.get("summary")
            except json.JSONDecodeError:
                row["context_error"] = "Invalid context JSON"
        summary["queue"] = queue_rows
        summary["counts"]["queue"] = len(summary["queue"])
    if table_exists(conn, "ai_gm_decision_log_view"):
        params: list[Any] = []
        where = []
        if game_id:
            where.append("game_id = ?")
            params.append(game_id)
        if team:
            where.append("team = ?")
            params.append(team.upper())
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        summary["logs"] = rows_as_dicts(
            conn.execute(
                f"""
                SELECT *
                FROM ai_gm_decision_log_view
                {clause}
                ORDER BY created_at DESC, decision_log_id DESC
                LIMIT 12
                """,
                params,
            ).fetchall()
        )
        summary["counts"]["logs"] = len(summary["logs"])
    return json_safe(summary)


def command_set(
    season: int,
    draft_year_value: int,
    user_team: str | None,
    free_agency_date: str | None = None,
    free_agency_year: int | None = None,
) -> dict[str, str]:
    team = user_team or "MIN"
    next_week = "<week>"
    fa_year = int(free_agency_year or draft_year_value)
    fa_start = free_agency_date or f"{fa_year}-03-10"
    return {
        "newGame": f'python tools\\play.py new --game-id my_save --name "My Save" --user-team {team} --start-year {season}',
        "newJune1Save": f'python tools\\play.py new --game-id {team.lower()}_{season}_june1 --name "{team} June 1 Start" --user-team {team} --start-year {season}',
        "status": "python tools\\play.py status",
        "preflight": "python tools\\play.py preflight",
        "advanceNextEvent": "python tools\\play.py advance-to-next-event",
        "advanceNextLeagueYear": "python tools\\play.py advance-to-next-league-year",
        "processEvents": "python tools\\play.py process-events --from-date <from> --to-date <to> --include-start --apply",
        "validateRosters": "python tools\\play.py validate-rosters --summary-only",
        "practiceSquadEligibility": f"python tools\\play.py roster-rules ps-eligibility --team {team} --season {season} --phase \"Regular Season\"",
        "autoCutdown": f"python tools\\play.py roster-cutdown --season {season} --apply",
        "boxScore": "python tools\\view_box_score.py --game-id <schedule_game_id>",
        "simNextWeek": f"python tools\\play.py sim-week {next_week} --season {season} --apply",
        "simSeason": f"python tools\\play.py sim-season --season {season} --apply --seed {season}00",
        "postseason": f"python tools\\play.py postseason run --season {season} --apply --seed {season}99",
        "completeSeason": f"python tools\\play.py complete-season --season {season} --apply --seed {season}99",
        "contractList": f"python tools\\play.py contract list --season {season} --team {team}",
        "contractExtend": f"python tools\\play.py contract extend --season {season} --team {team} --player-id <id> --apply",
        "contractRelease": f"python tools\\play.py contract release --season {season} --team {team} --player-id <id> --apply",
        "contractRestructure": f"python tools\\play.py contract restructure --season {season} --team {team} --player-id <id> --apply",
        "freeAgencyStart": f"python tools\\play.py free-agency start --league-year {fa_year} --start-date {fa_start} --no-cap-snapshot --apply",
        "freeAgencyCpuSeed": f"python tools\\play.py free-agency cpu-seed --league-year {fa_year} --no-cap-snapshot --apply",
        "freeAgencyHour": f"python tools\\play.py free-agency advance-hour --league-year {fa_year} --no-cap-snapshot --apply",
        "freeAgencyDay": f"python tools\\play.py free-agency advance-day --league-year {fa_year} --no-cap-snapshot --apply",
        "freeAgencyOffer": f"python tools\\play.py free-agency offer --league-year {fa_year} --team {team} --player <id> --years <years> --aav <aav> --apply",
        "draftGenerate": f"python tools\\play.py draft --year {draft_year_value} --count 330 --seed {draft_year_value} --apply",
        "draftClassGenerate": f"python tools\\play.py draft-class generate --draft-year {draft_year_value}",
        "draftClassImport": f"python tools\\play.py draft-class import --draft-year {draft_year_value} --package <saved_class_folder>",
        "draftValidate": f"python tools\\play.py validate-draft db --draft-year {draft_year_value}",
        "advanceToDraft": f"python tools\\play.py advance-to-draft --draft-year {draft_year_value} --user-team {team}",
        "draftStart": f"python tools\\play.py draft-room start --draft-year {draft_year_value} --user-team {team} --paused --apply",
        "draftSkipOne": f"python tools\\play.py draft-room skip --draft-year {draft_year_value} --count 1 --until-user-pick --no-cap-snapshot --apply",
        "draftSkipToUser": f"python tools\\play.py draft-room skip --draft-year {draft_year_value} --count 999 --until-user-pick --no-cap-snapshot --apply",
        "draftSkip": f"python tools\\play.py draft-room skip --draft-year {draft_year_value} --count 999 --until-user-pick --no-cap-snapshot --apply",
        "draftFinish": f"python tools\\play.py draft-room skip --draft-year {draft_year_value} --count 999 --include-user-pick --no-cap-snapshot --apply",
        "draftPick": f"python tools\\play.py draft-room pick --draft-year {draft_year_value} --prospect-id <id> --no-cap-snapshot --apply",
        "depthChartShow": f"python tools\\play.py depth-chart show --team {team}",
        "depthChartSet": f"python tools\\play.py depth-chart set --team {team} --position <slot> --rank <rank> --player-id <id> --apply",
        "depthChartMove": f"python tools\\play.py depth-chart move --team {team} --position <slot> --player-id <id> --direction <up|down> --apply",
        "scoutingSetup": f"python tools\\play.py scouting setup --draft-year {draft_year_value}",
        "scoutingAuto": "python tools\\play.py scouting auto",
        "scoutingOne": "python tools\\play.py scouting scout-one --prospect-id <id>",
        "scoutingRandomTwo": "python tools\\play.py scouting random",
        "scoutingDiscoverFour": "python tools\\play.py scouting discover",
        "scoutingSeniorBowlSetup": "python tools\\play.py scouting senior-bowl-setup",
        "scoutingSeniorBowlProcess": "python tools\\play.py scouting senior-bowl-process",
        "scoutingTop30Visit": "python tools\\play.py scouting top30-visit --prospect-id <id>",
        "scoutingTop30Auto": "python tools\\play.py scouting top30-auto --include-cpu",
        "scoutingAudit": "python tools\\play.py scouting audit",
        "inboxMarkRead": "python tools\\play.py scouting mark-read",
        "leagueNewsList": "python tools\\play.py league-news list --limit 25",
        "leagueNewsSeed": "python tools\\play.py league-news seed",
        "eventGenerateWeek": f"python tools\\play.py event-gen weekly --season {season} --week <week> --run-key manual --apply",
        "aiGmSetup": f"python tools\\play.py ai-gm setup --season {season} --no-backup",
        "aiGmProfiles": f"python tools\\play.py ai-gm profiles --team {team} --season {season}",
        "aiGmEvaluate": f"python tools\\play.py ai-gm evaluate --team {team} --season {season}",
        "aiGmCutdownPlan": f"python tools\\play.py ai-gm cutdown-plan --team {team} --season {season}",
        "aiGmCutdownPlanPersist": f"python tools\\play.py ai-gm cutdown-plan --team {team} --season {season} --persist",
        "aiGmCutdownPlans": f"python tools\\play.py ai-gm cutdown-plans --team {team} --limit 12",
        "aiGmDryRunCutdownApply": "python tools\\play.py ai-gm apply-cutdown-plan --plan-id <plan_id>",
        "aiGmApplyCutdownPlan": "python tools\\play.py ai-gm apply-cutdown-plan --plan-id <plan_id> --allow-warning --apply",
        "aiGmContractPlan": f"python tools\\play.py ai-gm contract-plan --team {team} --season {season}",
        "aiGmContractPlanPersist": f"python tools\\play.py ai-gm contract-plan --team {team} --season {season} --persist",
        "aiGmContractPlans": f"python tools\\play.py ai-gm contract-plans --team {team} --limit 12",
        "aiGmDryRunContractApply": "python tools\\play.py ai-gm apply-contract-plan --plan-id <plan_id>",
        "aiGmApplyContractPlan": "python tools\\play.py ai-gm apply-contract-plan --plan-id <plan_id> --apply",
        "aiGmFreeAgentPlan": f"python tools\\play.py ai-gm free-agent-plan --team {team} --league-year {season} --season {season}",
        "aiGmFreeAgentPlanPersist": f"python tools\\play.py ai-gm free-agent-plan --team {team} --league-year {season} --season {season} --persist",
        "aiGmFreeAgentPlans": f"python tools\\play.py ai-gm free-agent-plans --team {team} --limit 12",
        "aiGmDryRunFreeAgentApply": "python tools\\play.py ai-gm apply-free-agent-plan --plan-id <plan_id>",
        "aiGmApplyFreeAgentPlan": "python tools\\play.py ai-gm apply-free-agent-plan --plan-id <plan_id> --apply",
        "aiGmDraftPlan": f"python tools\\play.py ai-gm draft-plan --team {team} --draft-year {draft_year_value} --season {season}",
        "aiGmDraftPlanPersist": f"python tools\\play.py ai-gm draft-plan --team {team} --draft-year {draft_year_value} --season {season} --persist",
        "aiGmDraftPlans": f"python tools\\play.py ai-gm draft-plans --team {team} --draft-year {draft_year_value} --limit 12",
        "aiGmDraftPlanAll": f"python tools\\play.py ai-gm draft-plan --all --draft-year {draft_year_value} --season {season} --persist",
        "aiGmOffseasonPreFaDryRun": f"python tools\\play.py ai-gm offseason-run --all --phase pre-free-agency --season {season}",
        "aiGmOffseasonPreFaApply": f"python tools\\play.py ai-gm offseason-run --all --phase pre-free-agency --season {season} --apply",
        "aiGmOffseasonFaWave1DryRun": f"python tools\\play.py ai-gm offseason-run --all --phase free-agency-wave1 --league-year {season} --season {season}",
        "aiGmOffseasonFaWave1Apply": f"python tools\\play.py ai-gm offseason-run --all --phase free-agency-wave1 --league-year {season} --season {season} --apply",
        "aiGmOps": f"python tools\\play.py ai-gm ops --team {team}",
        "aiGmOpsAll": "python tools\\play.py ai-gm ops --all --limit 40",
        "aiGmOpsEnqueue": f"python tools\\play.py ai-gm ops --team {team} --enqueue",
        "aiGmOpsEnqueueAll": "python tools\\play.py ai-gm ops --all --enqueue --limit 40",
        "aiGmQueue": f"python tools\\play.py ai-gm queue --team {team}",
        "aiGmProcessQueue": f"python tools\\play.py ai-gm process-queue --team {team} --limit 3",
        "aiGmProcessQueueAll": "python tools\\play.py ai-gm process-queue --all --limit 20",
        "aiGmEnableOllama": "python tools\\play.py ai-gm config --provider ollama --endpoint http://127.0.0.1:11434/api/chat --model llama3.1:8b --enable",
        "aiGmShowConfig": "python tools\\play.py ai-gm show-config",
        "aiGmAutonomyShow": "python tools\\play.py ai-gm autonomy-show",
        "aiGmAutonomyAdvisory": "python tools\\play.py ai-gm autonomy-config --mode advisory_only --queue-llm --no-auto-apply-low-risk",
        "aiGmAutonomyLowRisk": "python tools\\play.py ai-gm autonomy-config --mode auto_apply_low_risk --queue-llm --auto-apply-low-risk",
        "aiGmDailyRun": f"python tools\\play.py ai-gm daily-run --team {team} --phase auto",
        "aiGmDailyRunPersist": f"python tools\\play.py ai-gm daily-run --team {team} --phase auto --persist",
        "aiGmDailyRunAllPersist": "python tools\\play.py ai-gm daily-run --all --phase auto --persist --limit 20",
        "aiGmDailyRunApply": "python tools\\play.py ai-gm daily-run --all --phase auto --mode auto_apply_low_risk --apply --limit 20",
        "aiGmReviewInbox": f"python tools\\play.py ai-gm review-inbox --team {team}",
        "aiGmReviewInboxAll": "python tools\\play.py ai-gm review-inbox --status pending_review --limit 40",
        "aiGmReviewHistory": f"python tools\\play.py ai-gm review-history --team {team} --limit 20",
        "aiGmReviewHistoryAll": "python tools\\play.py ai-gm review-history --status all --limit 40",
        "aiGmReviewShow": "python tools\\play.py ai-gm review-show --review-id <review_id>",
        "aiGmReviewApprove": "python tools\\play.py ai-gm review-update --review-id <review_id> --status approved",
        "aiGmReviewReject": "python tools\\play.py ai-gm review-update --review-id <review_id> --status rejected --note \"reason\"",
        "aiGmReviewApply": "python tools\\play.py ai-gm review-apply --review-id <review_id>",
        "aiGmReviewApplyCommit": "python tools\\play.py ai-gm review-apply --review-id <review_id> --apply",
        "aiGmReviewApplyAllApproved": f"python tools\\play.py ai-gm review-apply --all-approved --team {team}",
        "aiGmReviewApplyAllApprovedCommit": f"python tools\\play.py ai-gm review-apply --all-approved --team {team} --apply",
        "aiGmDevSeedReview": f"python tools\\play.py ai-gm dev-seed-review --team {team} --clear-existing",
        "aiGmDevClearReviews": f"python tools\\play.py ai-gm dev-clear-reviews --team {team}",
        "aiGmContext": f"python tools\\play.py ai-gm context --team {team} --decision-type draft_strategy_update",
        "aiGmRunDraft": f"python tools\\play.py ai-gm run --team {team} --decision-type draft_strategy_update",
        "aiGmRunDepth": f"python tools\\play.py ai-gm run --team {team} --decision-type depth_chart_review",
        "aiGmRunFreeAgency": f"python tools\\play.py ai-gm run --team {team} --decision-type free_agent_shortlist",
        "aiGmLogs": f"python tools\\play.py ai-gm logs --team {team} --limit 12",
        "schemeSummary": f"python tools\\play.py schemes summary --team {team}",
        "schemePlayer": f"python tools\\play.py schemes player --team {team} --player <name>",
        "progressionDryRun": f"python tools\\play.py progression run --from-season {season} --to-season {season + 1} --seed {season}{season + 1}",
        "progressionApply": f"python tools\\play.py progression run --from-season {season} --to-season {season + 1} --seed {season}{season + 1} --apply",
        "progressionSummary": "python tools\\play.py progression summary",
        "progressionShowTeam": f"python tools\\play.py progression show --team {team}",
        "exportGameCenter": "python tools\\export_game_center_ui_data.py",
        "exportFrontOffice": "python tools\\export_front_office_ui_data.py",
    }


def build_payload(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        game_settings = settings(conn)
        active = active_save(conn)
        current_season = int(
            (active or {}).get("current_league_year")
            or game_settings.get("current_league_year")
            or game_settings.get("current_season")
            or 2026
        )
        current_date = (
            (active or {}).get("current_date")
            or game_settings.get("current_game_date")
            or f"{current_season}-06-01"
        )
        phase = (
            (active or {}).get("phase_name")
            or (active or {}).get("current_phase_code")
            or game_settings.get("current_calendar_phase")
            or "OFFSEASON_OPEN"
        )
        user_team = (active or {}).get("user_team")
        user_team_id = (active or {}).get("user_team_id")
        game_id = (active or {}).get("game_id") or (active or {}).get("save_id")
        draft_year_value = draft_year(conn, current_season)
        fa_league_year = free_agency_league_year(
            conn,
            current_season=current_season,
            current_date=current_date,
            draft_year_value=draft_year_value,
            game_settings=game_settings,
        )
        fa_start = free_agency_start_date(conn, fa_league_year)
        draft_payload = draft_summary(
            conn,
            draft_year_value,
            user_team_id=user_team_id,
            game_id=game_id,
            current_date_value=current_date,
        )
        scouting_payload = enrich_scouting_payload_with_draft_board(
            scouting.build_ui_payload(conn, limit=80),
            draft_payload,
        )
        return {
            "database": str(db_path),
            "currentDate": current_date,
            "currentSeason": current_season,
            "currentPhase": phase,
            "settings": game_settings,
            "activeSave": active,
            "registry": save_registry(),
            "events": upcoming_events(conn),
            "calendar": calendar_summary(
                conn,
                season=current_season,
                current_date=current_date,
                focus_date=default_calendar_focus_date(conn, season=current_season, current_date=current_date),
                game_id=game_id,
                user_team_id=user_team_id,
            ),
            "alerts": alerts(conn),
            "log": flow_log(conn),
            "season": season_summary(conn, current_season, user_team_id),
            "stats": stat_leaders(conn, current_season),
            "transactions": league_transactions_summary(conn, limit=400),
            "injuries": injury_center_summary(conn, current_date=current_date, user_team_id=user_team_id),
            "leagueNews": league_news.build_ui_payload(conn, limit=80),
            "contractNegotiations": contract_negotiation_summary(conn, current_season, user_team),
            "depthChart": depth_chart_summary(conn, user_team, current_season),
            "draft": draft_payload,
            "draftClassSetup": draft_class_setup(conn, current_season, draft_year_value, active_game=bool(active)),
            "rookieClass": {
                "year": current_season,
                "selections": draft_user_selections(conn, current_season, user_team_id),
            },
            "scouting": scouting_payload,
            "freeAgency": free_agency_summary(conn, fa_league_year),
            "aiGm": ai_gm_summary(conn, user_team or "MIN", game_id, current_season),
            "commands": command_set(
                current_season,
                draft_year_value,
                user_team,
                fa_start,
                free_agency_year=fa_league_year,
            ),
        }
    finally:
        conn.close()


def export(db_path: Path, output_path: Path) -> dict[str, Any]:
    payload = build_payload(db_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "/* Generated by tools/export_game_center_ui_data.py. */\n"
        "window.GAME_CENTER_DATA = "
        + json.dumps(payload, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Export data for the static Game Center UI.")
    parser.add_argument("--db", help="Path to nfl_gm.db. Defaults to the active save DB when available.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JS file")
    args = parser.parse_args()
    db_path = Path(args.db) if args.db else default_export_db()
    export(db_path, Path(args.output))
    print(f"Exported game center data from {db_path} to {Path(args.output)}")


if __name__ == "__main__":
    main()
