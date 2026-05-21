#!/usr/bin/env python3
"""Read-only playtest preflight checks for an active NFL GM save."""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MASTER_DB = ROOT / "database" / "nfl_gm.db"
SAVE_REGISTRY = ROOT / "saves" / "save_registry.json"
GAME_CENTER_EXPORT = ROOT / "ui" / "game_center" / "game-center-data.js"


@dataclass
class Check:
    name: str
    status: str
    detail: str


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return default


def default_db_path() -> Path:
    registry = read_json(SAVE_REGISTRY, {"active_game_id": None, "saves": {}})
    active_id = registry.get("active_game_id") or registry.get("activeGameId")
    if active_id:
        record = registry.get("saves", {}).get(active_id)
        if record and record.get("db_path"):
            candidate = ROOT / record["db_path"]
            if candidate.exists():
                return candidate
    return MASTER_DB


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone() is not None


def scalar(con: sqlite3.Connection, sql: str, params: tuple[Any, ...] = (), fallback: Any = None) -> Any:
    try:
        row = con.execute(sql, params).fetchone()
    except sqlite3.Error:
        return fallback
    if not row:
        return fallback
    return row[0]


def add(checks: list[Check], name: str, status: str, detail: str) -> None:
    checks.append(Check(name=name, status=status, detail=detail))


def active_game(con: sqlite3.Connection) -> sqlite3.Row | None:
    if table_exists(con, "active_game_save_view"):
        return con.execute("SELECT * FROM active_game_save_view LIMIT 1").fetchone()
    if table_exists(con, "game_saves"):
        return con.execute(
            """
            SELECT *
            FROM game_saves
            WHERE status = 'active'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()
    return None


def check_database(con: sqlite3.Connection, checks: list[Check]) -> None:
    quick = scalar(con, "PRAGMA quick_check", fallback="failed")
    add(checks, "SQLite quick_check", "OK" if quick == "ok" else "FAIL", str(quick))

    required = [
        "teams",
        "players",
        "contracts",
        "season_games",
        "draft_classes",
        "draft_prospects",
        "draft_picks",
        "league_calendar_events",
        "league_news_items",
        "scouting_prospect_progress",
    ]
    missing = [name for name in required if not table_exists(con, name)]
    add(
        checks,
        "Core tables",
        "OK" if not missing else "FAIL",
        "All core tables present." if not missing else f"Missing: {', '.join(missing)}",
    )


def check_active_save(con: sqlite3.Connection, checks: list[Check], db_path: Path) -> sqlite3.Row | None:
    game = active_game(con)
    if not game:
        status = "WARN" if db_path.resolve() == MASTER_DB.resolve() else "FAIL"
        detail = (
            "No active game save row found in the master template DB."
            if status == "WARN"
            else "No active game save row found."
        )
        add(checks, "Active save", status, detail)
        return None
    game_id = game["game_id"] if "game_id" in game.keys() else "-"
    current_date = game["current_date"] if "current_date" in game.keys() else "-"
    phase = game["current_phase_code"] if "current_phase_code" in game.keys() else game["phase_name"] if "phase_name" in game.keys() else "-"
    user_team = game["user_team"] if "user_team" in game.keys() else game["user_team_id"] if "user_team_id" in game.keys() else "-"
    add(checks, "Active save", "OK", f"{game_id} | {user_team} | {current_date} | {phase}")
    return game


def check_teams_and_rosters(con: sqlite3.Connection, checks: list[Check]) -> None:
    team_count = scalar(con, "SELECT COUNT(*) FROM teams", fallback=0)
    add(checks, "Teams", "OK" if team_count == 32 else "WARN", f"{team_count} team(s) found.")

    player_count = scalar(con, "SELECT COUNT(*) FROM players", fallback=0)
    add(checks, "Players", "OK" if player_count and player_count >= 2200 else "WARN", f"{player_count} player(s) found.")

    if not table_exists(con, "players") or not table_exists(con, "teams"):
        return
    rows = con.execute(
        """
        SELECT t.abbreviation, COUNT(p.player_id) AS player_count
        FROM teams t
        LEFT JOIN players p ON p.team_id = t.team_id
        GROUP BY t.team_id
        ORDER BY player_count, t.abbreviation
        """
    ).fetchall()
    under_53 = [f"{row['abbreviation']} {int(row['player_count'] or 0)}" for row in rows if int(row["player_count"] or 0) < 53]
    under_60 = [f"{row['abbreviation']} {int(row['player_count'] or 0)}" for row in rows if int(row["player_count"] or 0) < 60]
    roster_limits_enforced = 1
    if table_exists(con, "active_game_save_view"):
        phase_row = con.execute(
            "SELECT roster_limits_enforced FROM active_game_save_view LIMIT 1"
        ).fetchone()
        if phase_row:
            roster_limits_enforced = int(phase_row["roster_limits_enforced"] or 0)
    if under_53:
        status = "FAIL" if roster_limits_enforced else "WARN"
        context = "" if roster_limits_enforced else " (roster limits currently off)"
        add(checks, "Roster counts", status, "Under 53" + context + ": " + ", ".join(under_53[:12]))
    elif under_60:
        add(checks, "Roster counts", "WARN", "Under 60: " + ", ".join(under_60[:12]))
    else:
        add(checks, "Roster counts", "OK", "All teams have at least 60 players.")

    fa_count = scalar(con, "SELECT COUNT(*) FROM players WHERE team_id IS NULL", fallback=0)
    add(checks, "Free-agent pool", "OK" if fa_count >= 150 else "WARN", f"{fa_count} free agent(s).")


def check_schedule(con: sqlite3.Connection, checks: list[Check], season: int) -> None:
    if not table_exists(con, "season_games"):
        add(checks, "Schedule", "FAIL", "season_games table missing.")
        return
    row = con.execute(
        """
        SELECT
            COUNT(*) AS games,
            COUNT(DISTINCT week) AS weeks,
            SUM(CASE WHEN played = 1 THEN 1 ELSE 0 END) AS played
        FROM season_games
        WHERE season = ?
          AND game_type = 'REG'
        """,
        (season,),
    ).fetchone()
    games = int(row["games"] or 0)
    weeks = int(row["weeks"] or 0)
    played = int(row["played"] or 0)
    if games == 272 and weeks == 18:
        status = "OK"
    elif games:
        status = "WARN"
    else:
        status = "FAIL"
    add(checks, "Regular-season schedule", status, f"{games} game(s), {weeks} week(s), {played} played.")


def check_draft(con: sqlite3.Connection, checks: list[Check], season: int, *, has_active_save: bool) -> None:
    draft_year = season + 1
    if not table_exists(con, "draft_classes") or not table_exists(con, "draft_prospects"):
        add(checks, "Draft class", "FAIL", "Draft class tables missing.")
        return
    class_row = con.execute(
        "SELECT draft_class_id, status FROM draft_classes WHERE draft_year = ? LIMIT 1",
        (draft_year,),
    ).fetchone()
    if not class_row:
        status = "FAIL" if has_active_save else "WARN"
        detail = (
            f"No {draft_year} draft class found for the active save."
            if has_active_save
            else f"No {draft_year} draft class in the template DB; new saves generate one at start."
        )
        add(checks, "Draft class", status, detail)
        return
    draft_class_id = int(class_row["draft_class_id"])
    counts = con.execute(
        """
        SELECT
            COUNT(*) AS prospects,
            SUM(CASE WHEN COALESCE(public_board_status, '') = 'off_public_board' THEN 1 ELSE 0 END) AS off_board,
            SUM(CASE WHEN COALESCE(discovery_status, '') = 'discovered' THEN 1 ELSE 0 END) AS discovered
        FROM draft_prospects
        WHERE draft_class_id = ?
        """,
        (draft_class_id,),
    ).fetchone()
    prospects = int(counts["prospects"] or 0)
    off_board = int(counts["off_board"] or 0)
    discovered = int(counts["discovered"] or 0)
    status = "OK" if prospects >= 300 and off_board >= 40 else "WARN"
    add(
        checks,
        "Draft class",
        status,
        f"{draft_year}: {prospects} prospect(s), {off_board} off-board, {discovered} discovered.",
    )

    combine_count = scalar(con, "SELECT COUNT(*) FROM draft_prospect_combine_results", fallback=0)
    rating_count = scalar(con, "SELECT COUNT(*) FROM draft_prospect_ratings", fallback=0)
    add(
        checks,
        "Draft details",
        "OK" if combine_count >= prospects and rating_count >= prospects else "WARN",
        f"{combine_count} combine row(s), {rating_count} rating row(s).",
    )

    if table_exists(con, "draft_room_board_ui_view"):
        sample = scalar(con, "SELECT COUNT(*) FROM draft_room_board_ui_view", fallback=0)
        add(checks, "Draft board view", "OK" if sample else "WARN", f"{sample} available prospect row(s).")
    else:
        add(checks, "Draft board view", "WARN", "draft_room_board_ui_view missing.")


def check_year_to_year_flow(
    con: sqlite3.Connection,
    checks: list[Check],
    game: sqlite3.Row | None,
    season: int,
) -> None:
    if not game:
        add(checks, "Year-to-year flow", "WARN", "No active save to inspect for rollover readiness.")
        return

    game_id = str(game["game_id"] if "game_id" in game.keys() else "")
    current_date = str(game["current_date"] if "current_date" in game.keys() else "")
    start_year = int(game["start_league_year"] or season) if "start_league_year" in game.keys() else season

    pending_year = scalar(
        con,
        "SELECT setting_value FROM game_settings WHERE setting_key = 'draft_class_setup_pending_year'",
        fallback=None,
    ) if table_exists(con, "game_settings") else None
    if pending_year:
        prospects = scalar(
            con,
            """
            SELECT COUNT(dp.prospect_id)
            FROM draft_classes dc
            LEFT JOIN draft_prospects dp ON dp.draft_class_id = dc.draft_class_id
            WHERE dc.draft_year = ?
            """,
            (int(pending_year),),
            fallback=0,
        )
        if int(prospects or 0) > 0:
            add(checks, "Draft class gate", "WARN", f"{pending_year} draft class exists but setup is still marked pending.")
        else:
            add(checks, "Draft class gate", "OK", f"{pending_year} draft class setup is pending and should block calendar advance.")
    else:
        add(checks, "Draft class gate", "OK", "No stale draft class setup gate.")

    rating_count = scalar(
        con,
        "SELECT COUNT(DISTINCT player_id) FROM player_ratings WHERE season = ?",
        (season,),
        fallback=0,
    )
    add(
        checks,
        "Current-year ratings",
        "OK" if int(rating_count or 0) >= 2000 else "WARN",
        f"{season}: {int(rating_count or 0)} player rating row(s).",
    )

    if season > start_year and table_exists(con, "player_progression_runs"):
        previous = season - 1
        run = scalar(
            con,
            """
            SELECT COUNT(*)
            FROM player_progression_runs
            WHERE game_id = ?
              AND from_season = ?
              AND to_season = ?
            """,
            (game_id, previous, season),
            fallback=0,
        )
        status = "OK" if int(run or 0) > 0 else "WARN"
        add(checks, "Progression rollover", status, f"{previous}->{season}: {int(run or 0)} progression run(s).")
    elif season > start_year:
        add(checks, "Progression rollover", "WARN", "player_progression_runs table missing.")
    else:
        add(checks, "Progression rollover", "OK", "Still in the first league year.")

    next_draft_year = season + 1
    if table_exists(con, "draft_classes"):
        class_row = con.execute(
            """
            SELECT dc.draft_class_id, COUNT(dp.prospect_id) AS prospects
            FROM draft_classes dc
            LEFT JOIN draft_prospects dp ON dp.draft_class_id = dc.draft_class_id
            WHERE dc.draft_year = ?
            GROUP BY dc.draft_class_id
            """,
            (next_draft_year,),
        ).fetchone()
    else:
        class_row = None
    if class_row and int(class_row["prospects"] or 0) > 0 and table_exists(con, "scouting_prospect_progress"):
        user_progress = scalar(
            con,
            """
            SELECT COUNT(*)
            FROM scouting_prospect_progress
            WHERE game_id = ?
              AND draft_year = ?
            """,
            (game_id, next_draft_year),
            fallback=0,
        )
        cpu_progress = scalar(
            con,
            """
            SELECT COUNT(*)
            FROM cpu_scouting_prospect_progress
            WHERE game_id = ?
              AND draft_year = ?
            """,
            (game_id, next_draft_year),
            fallback=0,
        ) if table_exists(con, "cpu_scouting_prospect_progress") else 0
        status = "OK" if int(user_progress or 0) > 0 else "WARN"
        add(
            checks,
            "Scouting reset",
            status,
            f"{next_draft_year}: {int(user_progress or 0)} user report row(s), {int(cpu_progress or 0)} CPU report row(s).",
        )
    else:
        add(checks, "Scouting reset", "OK", f"{next_draft_year} draft class not loaded yet.")

    duplicate_contracts = 0
    if table_exists(con, "contracts"):
        duplicate_contracts = scalar(
            con,
            """
            SELECT COUNT(*)
            FROM (
                SELECT player_id
                FROM contracts
                WHERE COALESCE(is_active, 1) = 1
                  AND start_year <= ?
                  AND end_year >= ?
                GROUP BY player_id
                HAVING COUNT(*) > 1
            )
            """,
            (season, season),
            fallback=0,
        )
    add(
        checks,
        "Active contracts",
        "OK" if int(duplicate_contracts or 0) == 0 else "FAIL",
        f"{int(duplicate_contracts or 0)} player(s) have duplicate active contracts for {season}.",
    )

    if current_date and table_exists(con, "game_saves") and table_exists(con, "game_settings"):
        settings_date = scalar(con, "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'", fallback=current_date)
        add(
            checks,
            "Date sync",
            "OK" if str(settings_date) == current_date else "WARN",
            f"save={current_date}, settings={settings_date}.",
        )


def check_system_hooks(con: sqlite3.Connection, checks: list[Check]) -> None:
    hook_tables = {
        "Weekly hooks": "game_weekly_processing_runs",
        "Event generator": "league_event_generation_runs",
        "League news": "league_news_items",
        "Inbox/scouting": "user_inbox_messages",
    }
    for label, table in hook_tables.items():
        add(checks, label, "OK" if table_exists(con, table) else "WARN", f"{table} {'ready' if table_exists(con, table) else 'not created yet'}.")


def check_ui_export(db_path: Path, checks: list[Check]) -> None:
    if not GAME_CENTER_EXPORT.exists():
        add(checks, "Game Center export", "WARN", "game-center-data.js has not been exported yet.")
        return
    try:
        db_mtime = db_path.stat().st_mtime
        export_mtime = GAME_CENTER_EXPORT.stat().st_mtime
    except OSError:
        add(checks, "Game Center export", "WARN", "Could not compare export timestamp.")
        return
    if export_mtime >= db_mtime:
        status = "OK"
        detail = "UI export is newer than the active DB."
    else:
        status = "WARN"
        db_time = datetime.fromtimestamp(db_mtime).strftime("%Y-%m-%d %H:%M:%S")
        export_time = datetime.fromtimestamp(export_mtime).strftime("%Y-%m-%d %H:%M:%S")
        detail = f"UI export may be stale. DB {db_time}, export {export_time}."
    add(checks, "Game Center export", status, detail)


def run_checks(db_path: Path) -> dict[str, Any]:
    checks: list[Check] = []
    with connect(db_path) as con:
        check_database(con, checks)
        game = check_active_save(con, checks, db_path)
        season = 2026
        if game:
            if "current_league_year" in game.keys() and game["current_league_year"]:
                season = int(game["current_league_year"])
            elif table_exists(con, "game_settings"):
                season = int(scalar(con, "SELECT setting_value FROM game_settings WHERE setting_key = 'current_season'", fallback=2026))
        check_teams_and_rosters(con, checks)
        check_schedule(con, checks, season)
        check_draft(con, checks, season, has_active_save=game is not None)
        check_year_to_year_flow(con, checks, game, season)
        check_system_hooks(con, checks)
    check_ui_export(db_path, checks)
    counts = {
        "OK": sum(1 for check in checks if check.status == "OK"),
        "WARN": sum(1 for check in checks if check.status == "WARN"),
        "FAIL": sum(1 for check in checks if check.status == "FAIL"),
    }
    if counts["FAIL"]:
        status = "FAIL"
    elif counts["WARN"]:
        status = "WARN"
    else:
        status = "OK"
    return {
        "database": str(db_path),
        "status": status,
        "counts": counts,
        "checks": [check.__dict__ for check in checks],
    }


def print_report(report: dict[str, Any]) -> None:
    print("Playtest preflight")
    print(f"Database: {report['database']}")
    print(f"Overall: {report['status']}  OK={report['counts']['OK']} WARN={report['counts']['WARN']} FAIL={report['counts']['FAIL']}")
    print()
    for check in report["checks"]:
        print(f"[{check['status']:<4}] {check['name']}: {check['detail']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only playtest readiness checks.")
    parser.add_argument("--db", type=Path, default=None, help="SQLite DB path. Defaults to the active save DB.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on warnings as well as failures.")
    args = parser.parse_args()

    db_path = args.db or default_db_path()
    report = run_checks(db_path)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    if report["status"] == "FAIL" or (args.strict and report["status"] == "WARN"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
