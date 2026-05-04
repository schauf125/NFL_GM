#!/usr/bin/env python3
"""Weekly processing hooks for NFL GM Sim saves.

Calendar advancement is intentionally lightweight: important calendar events run
on their dates, while heavier roster/compliance checks run once after each
completed regular-season week.
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import daily_processor
import event_generator
import game_flow
import league_calendar
import scouting


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"


@dataclass(frozen=True)
class WeekWindow:
    season: int
    week: int
    game_count: int
    played_count: int
    start_date: str
    end_date: str

    @property
    def complete(self) -> bool:
        return self.game_count > 0 and self.game_count == self.played_count


@dataclass(frozen=True)
class WeeklyResult:
    game_id: str | None
    season: int
    week: int
    week_end_date: str | None
    status: str
    message: str
    event_dates_processed: int = 0
    processed_event_count: int = 0
    event_count: int = 0
    alerts_created: int = 0
    teams_checked: int = 0
    roster_failures: int = 0
    roster_errors: int = 0
    roster_warnings: int = 0
    scouting_status: str = "not_run"
    scouting_action: str | None = None
    scouting_advanced: int = 0
    cpu_scouting_status: str = "not_run"
    cpu_scouting_teams: int = 0
    cpu_scouting_advanced: int = 0
    cpu_scouting_discoveries: int = 0
    run_id: int | None = None


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    game_flow.ensure_schema(con)
    daily_processor.ensure_schema(con)
    event_generator.ensure_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS game_weekly_processing_runs (
            weekly_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            week INTEGER NOT NULL,
            week_end_date TEXT NOT NULL,
            phase_code TEXT,
            phase_name TEXT,
            roster_limits_enforced INTEGER NOT NULL DEFAULT 0,
            roster_rule_phase TEXT,
            event_dates_processed INTEGER NOT NULL DEFAULT 0,
            event_count INTEGER NOT NULL DEFAULT 0,
            processed_event_count INTEGER NOT NULL DEFAULT 0,
            alert_count INTEGER NOT NULL DEFAULT 0,
            teams_checked INTEGER NOT NULL DEFAULT 0,
            roster_failures INTEGER NOT NULL DEFAULT 0,
            roster_error_count INTEGER NOT NULL DEFAULT 0,
            roster_warning_count INTEGER NOT NULL DEFAULT 0,
            scouting_hook_status TEXT NOT NULL DEFAULT 'not_run',
            scouting_action_key TEXT,
            scouting_prospects_advanced INTEGER NOT NULL DEFAULT 0,
            cpu_scouting_hook_status TEXT NOT NULL DEFAULT 'not_run',
            cpu_scouting_teams INTEGER NOT NULL DEFAULT 0,
            cpu_scouting_prospects_advanced INTEGER NOT NULL DEFAULT 0,
            cpu_scouting_discoveries INTEGER NOT NULL DEFAULT 0,
            ai_gm_hook_status TEXT NOT NULL DEFAULT 'not_configured',
            cap_hook_status TEXT NOT NULL DEFAULT 'pending',
            depth_chart_hook_status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(game_id, season, week)
        );

        CREATE INDEX IF NOT EXISTS idx_game_weekly_processing_runs_game_week
            ON game_weekly_processing_runs(game_id, season, week);
        """
    )
    existing = {row[1] for row in con.execute("PRAGMA table_info(game_weekly_processing_runs)").fetchall()}
    if "scouting_hook_status" not in existing:
        con.execute("ALTER TABLE game_weekly_processing_runs ADD COLUMN scouting_hook_status TEXT NOT NULL DEFAULT 'not_run'")
    if "scouting_action_key" not in existing:
        con.execute("ALTER TABLE game_weekly_processing_runs ADD COLUMN scouting_action_key TEXT")
    if "scouting_prospects_advanced" not in existing:
        con.execute("ALTER TABLE game_weekly_processing_runs ADD COLUMN scouting_prospects_advanced INTEGER NOT NULL DEFAULT 0")
    if "cpu_scouting_hook_status" not in existing:
        con.execute("ALTER TABLE game_weekly_processing_runs ADD COLUMN cpu_scouting_hook_status TEXT NOT NULL DEFAULT 'not_run'")
    if "cpu_scouting_teams" not in existing:
        con.execute("ALTER TABLE game_weekly_processing_runs ADD COLUMN cpu_scouting_teams INTEGER NOT NULL DEFAULT 0")
    if "cpu_scouting_prospects_advanced" not in existing:
        con.execute("ALTER TABLE game_weekly_processing_runs ADD COLUMN cpu_scouting_prospects_advanced INTEGER NOT NULL DEFAULT 0")
    if "cpu_scouting_discoveries" not in existing:
        con.execute("ALTER TABLE game_weekly_processing_runs ADD COLUMN cpu_scouting_discoveries INTEGER NOT NULL DEFAULT 0")


def week_window(con: sqlite3.Connection, season: int, week: int) -> WeekWindow:
    row = con.execute(
        """
        SELECT COUNT(*) AS game_count,
               SUM(CASE WHEN played = 1 THEN 1 ELSE 0 END) AS played_count,
               MIN(game_date) AS start_date,
               MAX(game_date) AS end_date
        FROM season_games
        WHERE season = ?
          AND week = ?
          AND game_type = 'REG'
        """,
        (season, week),
    ).fetchone()
    if not row or int(row["game_count"] or 0) == 0:
        raise ValueError(f"No regular-season games found for {season} Week {week}.")
    return WeekWindow(
        season=season,
        week=week,
        game_count=int(row["game_count"] or 0),
        played_count=int(row["played_count"] or 0),
        start_date=row["start_date"],
        end_date=row["end_date"],
    )


def active_game_or_none(con: sqlite3.Connection) -> game_flow.ActiveGame | None:
    try:
        return game_flow.active_game(con)
    except (sqlite3.OperationalError, ValueError):
        return None


def existing_weekly_run(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    week: int,
) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT *
        FROM game_weekly_processing_runs
        WHERE game_id = ?
          AND season = ?
          AND week = ?
        """,
        (game_id, season, week),
    ).fetchone()


def phase_for_week_end(con: sqlite3.Connection, target_date: str) -> sqlite3.Row:
    phase = league_calendar.phase_for_date(con, target_date)
    if not phase:
        raise ValueError(f"No calendar phase found for {target_date}.")
    return phase


def process_week(
    con: sqlite3.Connection,
    *,
    season: int,
    week: int,
    game_id: str | None = None,
    force: bool = False,
    require_complete: bool = True,
    advance_date: bool = True,
) -> WeeklyResult:
    ensure_schema(con)
    window = week_window(con, season, week)
    if require_complete and not window.complete:
        raise ValueError(
            f"{season} Week {week} is not complete: "
            f"{window.played_count}/{window.game_count} games played."
        )

    game = active_game_or_none(con)
    target_game_id = game_id or (game.game_id if game else None)
    if not target_game_id:
        return WeeklyResult(
            game_id=None,
            season=season,
            week=week,
            week_end_date=window.end_date,
            status="skipped",
            message="No active game save found; weekly hooks skipped.",
        )

    existing = existing_weekly_run(con, game_id=target_game_id, season=season, week=week)
    if existing and not force:
        return WeeklyResult(
            game_id=target_game_id,
            season=season,
            week=week,
            week_end_date=existing["week_end_date"],
            status="already_processed",
            message=f"{season} Week {week} weekly hooks already processed.",
            event_dates_processed=int(existing["event_dates_processed"] or 0),
            processed_event_count=int(existing["processed_event_count"] or 0),
            event_count=int(existing["event_count"] or 0),
            alerts_created=int(existing["alert_count"] or 0),
            teams_checked=int(existing["teams_checked"] or 0),
            roster_failures=int(existing["roster_failures"] or 0),
            roster_errors=int(existing["roster_error_count"] or 0),
            roster_warnings=int(existing["roster_warning_count"] or 0),
            scouting_status=str(existing["scouting_hook_status"] or "unknown") if "scouting_hook_status" in existing.keys() else "unknown",
            scouting_action=str(existing["scouting_action_key"]) if "scouting_action_key" in existing.keys() and existing["scouting_action_key"] else None,
            scouting_advanced=int(existing["scouting_prospects_advanced"] or 0) if "scouting_prospects_advanced" in existing.keys() else 0,
            cpu_scouting_status=str(existing["cpu_scouting_hook_status"] or "unknown") if "cpu_scouting_hook_status" in existing.keys() else "unknown",
            cpu_scouting_teams=int(existing["cpu_scouting_teams"] or 0) if "cpu_scouting_teams" in existing.keys() else 0,
            cpu_scouting_advanced=int(existing["cpu_scouting_prospects_advanced"] or 0) if "cpu_scouting_prospects_advanced" in existing.keys() else 0,
            cpu_scouting_discoveries=int(existing["cpu_scouting_discoveries"] or 0) if "cpu_scouting_discoveries" in existing.keys() else 0,
            run_id=int(existing["weekly_run_id"]),
        )
    if existing and force:
        con.execute(
            "DELETE FROM game_weekly_processing_runs WHERE weekly_run_id = ?",
            (int(existing["weekly_run_id"]),),
        )

    from_date = game.current_date if game else window.start_date
    include_start = True
    if game and date.fromisoformat(window.end_date) < date.fromisoformat(game.current_date):
        from_date = window.end_date

    event_result = daily_processor.process_event_range(
        con,
        game_id=target_game_id,
        from_date=from_date,
        to_date=window.end_date,
        include_start=include_start,
        force=force,
    )

    if game and advance_date and date.fromisoformat(window.end_date) > date.fromisoformat(game.current_date):
        phase, _crossed_events = game_flow.update_active_game_date(con, game, window.end_date)
    else:
        phase = phase_for_week_end(con, window.end_date)

    reminder_alerts = daily_processor.create_upcoming_event_alerts(con, target_game_id, window.end_date)
    teams_checked, failures, roster_errors, roster_warnings, roster_alerts = daily_processor.validate_rosters_if_needed(
        con,
        target_game_id,
        window.end_date,
        phase,
    )
    alerts = event_result.alerts_created + reminder_alerts + roster_alerts
    scouting.ensure_schema(con)
    news_result = event_generator.generate_weekly_events(
        con,
        game_id=target_game_id,
        season=season,
        week=week,
        event_date=window.end_date,
        force=force,
        apply=True,
    )
    try:
        scouting_result = scouting.auto_assign_scouts(
            con,
            game_id=target_game_id,
            season=season,
            week=week,
        )
        scouting_status = "auto_assigned"
        scouting_action = str(scouting_result.get("action") or "auto_assign")
        scouting_advanced = len(scouting_result.get("advanced") or [])
        user_background_discoveries = len(scouting_result.get("background_discoveries") or [])
    except Exception as exc:
        scouting_status = "skipped"
        scouting_action = None
        scouting_advanced = 0
        user_background_discoveries = 0
        scouting_skip_reason = str(exc)
    else:
        scouting_skip_reason = None
    try:
        cpu_scouting_result = scouting.run_cpu_weekly_scouting(
            con,
            game_id=target_game_id,
            season=season,
            week=week,
        )
        cpu_scouting_status = str(cpu_scouting_result.get("status") or "processed")
        cpu_scouting_teams = int(cpu_scouting_result.get("teams") or 0)
        cpu_scouting_advanced = int(cpu_scouting_result.get("advanced") or 0)
        cpu_scouting_discoveries = int(cpu_scouting_result.get("discoveries") or 0)
        cpu_scouting_skip_reason = str(cpu_scouting_result.get("reason") or "")
    except Exception as exc:
        cpu_scouting_status = "skipped"
        cpu_scouting_teams = 0
        cpu_scouting_advanced = 0
        cpu_scouting_discoveries = 0
        cpu_scouting_skip_reason = str(exc)
    scouting_note = (
        f" Scouting auto-assigned {scouting_advanced} prospect(s)"
        + (
            f" and area scouts found {user_background_discoveries} off-board prospect(s)."
            if user_background_discoveries
            else "."
        )
        if scouting_status == "auto_assigned"
        else f" Scouting auto-assign skipped: {scouting_skip_reason}."
    )
    scouting_note += (
        f" CPU scouting advanced {cpu_scouting_advanced} reports across {cpu_scouting_teams} team(s)"
        f" and found {cpu_scouting_discoveries} hidden prospect(s)."
        if cpu_scouting_status == "processed"
        else f" CPU scouting skipped: {cpu_scouting_skip_reason}."
    )
    scouting_note += (
        f" League news rolled {news_result.planned_count} public event(s)."
    )

    cur = con.execute(
        """
        INSERT INTO game_weekly_processing_runs (
            game_id, season, week, week_end_date, phase_code, phase_name,
            roster_limits_enforced, roster_rule_phase, event_dates_processed,
            event_count, processed_event_count, alert_count, teams_checked,
            roster_failures, roster_error_count, roster_warning_count,
            scouting_hook_status, scouting_action_key, scouting_prospects_advanced,
            cpu_scouting_hook_status, cpu_scouting_teams, cpu_scouting_prospects_advanced,
            cpu_scouting_discoveries,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            target_game_id,
            season,
            week,
            window.end_date,
            phase["phase_code"],
            phase["phase_name"],
            int(phase["roster_limits_enforced"] or 0),
            phase["roster_rule_phase"],
            event_result.event_dates_processed,
            event_result.event_count,
            event_result.processed_event_count,
            alerts,
            teams_checked,
            failures,
            roster_errors,
            roster_warnings,
            scouting_status,
            scouting_action,
            scouting_advanced,
            cpu_scouting_status,
            cpu_scouting_teams,
            cpu_scouting_advanced,
            cpu_scouting_discoveries,
        ),
    )
    return WeeklyResult(
        game_id=target_game_id,
        season=season,
        week=week,
        week_end_date=window.end_date,
        status="processed",
        message=f"{season} Week {week} weekly hooks processed.{scouting_note}",
        event_dates_processed=event_result.event_dates_processed,
        processed_event_count=event_result.processed_event_count,
        event_count=event_result.event_count,
        alerts_created=alerts,
        teams_checked=teams_checked,
        roster_failures=failures,
        roster_errors=roster_errors,
        roster_warnings=roster_warnings,
        scouting_status=scouting_status,
        scouting_action=scouting_action,
        scouting_advanced=scouting_advanced,
        cpu_scouting_status=cpu_scouting_status,
        cpu_scouting_teams=cpu_scouting_teams,
        cpu_scouting_advanced=cpu_scouting_advanced,
        cpu_scouting_discoveries=cpu_scouting_discoveries,
        run_id=int(cur.lastrowid),
    )


def print_weekly_result(result: WeeklyResult) -> None:
    print(f"Weekly hooks: {result.message}")
    if result.status == "skipped":
        return
    print(f"  Week end date: {result.week_end_date}")
    print(f"  Calendar events processed: {result.processed_event_count}/{result.event_count}")
    print(f"  Alerts created: {result.alerts_created}")
    if result.teams_checked:
        print(
            f"  Roster checks: {result.teams_checked} teams, "
            f"{result.roster_failures} failed, "
            f"{result.roster_errors} errors, {result.roster_warnings} warnings"
        )
    else:
        print("  Roster checks: skipped; limits are off")
    if result.scouting_status == "auto_assigned":
        print(f"  Scouting: auto-assigned {result.scouting_advanced} prospect(s)")
    elif result.scouting_status != "not_run":
        print(f"  Scouting: {result.scouting_status}")
    if result.cpu_scouting_status == "processed":
        print(
            f"  CPU scouting: {result.cpu_scouting_teams} teams, "
            f"{result.cpu_scouting_advanced} reports, "
            f"{result.cpu_scouting_discoveries} hidden discoveries"
        )
    elif result.cpu_scouting_status != "not_run":
        print(f"  CPU scouting: {result.cpu_scouting_status}")


def action_setup(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        ensure_schema(con)
        con.commit()
        print("Weekly processor schema is ready.")
    finally:
        con.close()


def action_process_week(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        result = process_week(
            con,
            season=args.season,
            week=args.week,
            game_id=args.game_id,
            force=args.force,
            require_complete=not args.allow_incomplete,
            advance_date=not args.no_advance_date,
        )
        if args.apply:
            con.commit()
            print_weekly_result(result)
        else:
            con.rollback()
            print("Dry run only. Add --apply to save weekly hook results.")
            print_weekly_result(result)
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run weekly NFL GM Sim processing hooks.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Create weekly processing tables.")
    setup_parser.set_defaults(func=action_setup)

    week_parser = subparsers.add_parser("process-week", help="Run weekly hooks after a completed week.")
    week_parser.add_argument("week", type=int)
    week_parser.add_argument("--season", type=int, default=2026)
    week_parser.add_argument("--game-id")
    week_parser.add_argument("--apply", action="store_true")
    week_parser.add_argument("--force", action="store_true")
    week_parser.add_argument("--allow-incomplete", action="store_true")
    week_parser.add_argument("--no-advance-date", action="store_true")
    week_parser.set_defaults(func=action_process_week)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
