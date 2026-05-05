"""Daily processing hooks for NFL GM Sim saves.

This module is intentionally conservative. It makes date advancement feel like
gameplay without simulating games yet:

- process calendar events once per save
- create game alerts
- toggle basic phase settings
- validate rosters when limits are active
- resolve active injuries as the calendar advances
- leave explicit future hooks for AI GMs and development
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import league_calendar
import contract_negotiations
import draft_class_bootstrap
import roster_rules


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
SOURCE = "daily_processor"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import injury_model  # noqa: E402

KEY_REMINDER_EVENTS = {
    "VETERAN_TRAINING_CAMP_REPORTING",
    "FINAL_ROSTER_CUTDOWN_53",
    "PRACTICE_SQUADS_ESTABLISHED",
    "REGULAR_SEASON_KICKOFF",
    "TRADE_DEADLINE",
    "NFL_DRAFT",
    "NEXT_NFL_LEAGUE_YEAR_START",
}

EVENT_SETTING_ACTIONS = {
    "SIM_YEAR_START": {
        "roster_limits_enforced": "0",
        "practice_squads_enabled": "0",
        "trade_window_open": "1",
    },
    "VETERAN_TRAINING_CAMP_REPORTING": {
        "roster_limits_enforced": "1",
        "roster_rule_phase": "Preseason",
        "practice_squads_enabled": "0",
        "trade_window_open": "1",
    },
    "FINAL_ROSTER_CUTDOWN_53": {
        "roster_limits_enforced": "1",
        "roster_rule_phase": "Regular Season",
    },
    "PRACTICE_SQUADS_ESTABLISHED": {
        "practice_squads_enabled": "1",
        "roster_rule_phase": "Regular Season",
    },
    "REGULAR_SEASON_KICKOFF": {
        "regular_season_statuses_enabled": "1",
        "trade_window_open": "1",
        "roster_limits_enforced": "1",
        "roster_rule_phase": "Regular Season",
    },
    "TRADE_DEADLINE": {
        "trade_window_open": "0",
    },
    "POST_SUPER_BOWL_OFFSEASON_START": {
        "roster_limits_enforced": "0",
        "practice_squads_enabled": "0",
        "regular_season_statuses_enabled": "0",
        "trade_window_open": "1",
    },
}


@dataclass
class DailyResult:
    processed_date: str
    phase_code: str
    phase_name: str
    roster_limits_enforced: int
    roster_rule_phase: str | None
    event_count: int = 0
    processed_event_count: int = 0
    alerts_created: int = 0
    teams_checked: int = 0
    roster_failures: int = 0
    roster_errors: int = 0
    roster_warnings: int = 0
    injuries_resolved: int = 0
    run_id: int | None = None


@dataclass
class RangeResult:
    game_id: str
    from_date: str
    to_date: str
    days_processed: int
    daily_results: list[DailyResult]

    @property
    def event_count(self) -> int:
        return sum(day.event_count for day in self.daily_results)

    @property
    def processed_event_count(self) -> int:
        return sum(day.processed_event_count for day in self.daily_results)

    @property
    def alerts_created(self) -> int:
        return sum(day.alerts_created for day in self.daily_results)

    @property
    def teams_checked(self) -> int:
        return sum(day.teams_checked for day in self.daily_results)

    @property
    def roster_failures(self) -> int:
        return sum(day.roster_failures for day in self.daily_results)

    @property
    def roster_errors(self) -> int:
        return sum(day.roster_errors for day in self.daily_results)

    @property
    def roster_warnings(self) -> int:
        return sum(day.roster_warnings for day in self.daily_results)

    @property
    def injuries_resolved(self) -> int:
        return sum(day.injuries_resolved for day in self.daily_results)


@dataclass
class EventRangeResult:
    game_id: str
    from_date: str
    to_date: str
    event_dates_processed: int
    event_count: int
    processed_event_count: int
    alerts_created: int


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Use YYYY-MM-DD date format, got {value!r}.") from exc


def ensure_schema(con: sqlite3.Connection) -> None:
    league_calendar.ensure_schema(con)
    roster_rules.ensure_schema(con)
    roster_rules.seed_rules(con)
    injury_model.ensure_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS game_daily_processing_runs (
            processing_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            from_date TEXT,
            to_date TEXT NOT NULL,
            processed_date TEXT NOT NULL,
            phase_code TEXT,
            phase_name TEXT,
            roster_limits_enforced INTEGER NOT NULL DEFAULT 0,
            roster_rule_phase TEXT,
            event_count INTEGER NOT NULL DEFAULT 0,
            processed_event_count INTEGER NOT NULL DEFAULT 0,
            alert_count INTEGER NOT NULL DEFAULT 0,
            teams_checked INTEGER NOT NULL DEFAULT 0,
            roster_failures INTEGER NOT NULL DEFAULT 0,
            roster_error_count INTEGER NOT NULL DEFAULT 0,
            roster_warning_count INTEGER NOT NULL DEFAULT 0,
            injuries_resolved INTEGER NOT NULL DEFAULT 0,
            ai_gm_hook_status TEXT NOT NULL DEFAULT 'not_configured',
            development_hook_status TEXT NOT NULL DEFAULT 'not_implemented',
            injury_hook_status TEXT NOT NULL DEFAULT 'enabled',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(game_id, processed_date)
        );

        CREATE TABLE IF NOT EXISTS game_processed_events (
            game_id TEXT NOT NULL,
            event_id INTEGER NOT NULL,
            event_code TEXT NOT NULL,
            event_date TEXT NOT NULL,
            processor_key TEXT NOT NULL,
            action_taken TEXT,
            details TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (game_id, event_id, processor_key)
        );

        CREATE TABLE IF NOT EXISTS game_alerts (
            alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            alert_date TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'INFO',
            alert_type TEXT NOT NULL,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            event_id INTEGER REFERENCES league_calendar_events(event_id) ON DELETE SET NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            due_date TEXT,
            status TEXT NOT NULL DEFAULT 'Open',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_game_alerts_game_status
            ON game_alerts(game_id, status, alert_date, severity);

        CREATE INDEX IF NOT EXISTS idx_game_daily_processing_runs_game_date
            ON game_daily_processing_runs(game_id, processed_date);
        """
    )
    cols = {row["name"] for row in con.execute("PRAGMA table_info(game_daily_processing_runs)").fetchall()}
    if "injuries_resolved" not in cols:
        con.execute("ALTER TABLE game_daily_processing_runs ADD COLUMN injuries_resolved INTEGER NOT NULL DEFAULT 0")


def upsert_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    league_calendar.upsert_setting(con, key, value, overwrite=True)


def active_game_id(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'active_game_id'"
    ).fetchone()
    if row:
        return row["setting_value"]
    row = con.execute(
        """
        SELECT game_id
        FROM game_saves
        WHERE status = 'active'
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        raise ValueError("No active game found.")
    return row["game_id"]


def current_game_date(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
    ).fetchone()
    if not row:
        raise ValueError("current_game_date is not set.")
    return row["setting_value"]


def phase_for_date(con: sqlite3.Connection, target_date: str) -> sqlite3.Row:
    phase = league_calendar.phase_for_date(con, target_date)
    if not phase:
        raise ValueError(f"No phase found for {target_date}.")
    return phase


def events_on_date(con: sqlite3.Connection, target_date: str) -> list[sqlite3.Row]:
    return list(
        con.execute(
            """
            SELECT *
            FROM league_calendar_view
            WHERE date(event_start_date) = date(?)
            ORDER BY event_start_date, sort_order
            """,
            (target_date,),
        )
    )


def event_dates_between(
    con: sqlite3.Connection,
    *,
    from_date: str,
    to_date: str,
    include_start: bool = False,
) -> list[str]:
    comparator = ">=" if include_start else ">"
    rows = con.execute(
        f"""
        SELECT DISTINCT event_start_date
        FROM league_calendar_events
        WHERE date(event_start_date) {comparator} date(?)
          AND date(event_start_date) <= date(?)
        ORDER BY event_start_date
        """,
        (from_date, to_date),
    ).fetchall()
    return [row["event_start_date"] for row in rows]


def event_already_processed(con: sqlite3.Connection, game_id: str, event_id: int, processor_key: str) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM game_processed_events
        WHERE game_id = ?
          AND event_id = ?
          AND processor_key = ?
        """,
        (game_id, event_id, processor_key),
    ).fetchone()
    return row is not None


def mark_event_processed(
    con: sqlite3.Connection,
    *,
    game_id: str,
    event: sqlite3.Row,
    processor_key: str,
    action_taken: str,
    details: str | None,
) -> bool:
    if event_already_processed(con, game_id, int(event["event_id"]), processor_key):
        return False
    con.execute(
        """
        INSERT INTO game_processed_events (
            game_id, event_id, event_code, event_date, processor_key,
            action_taken, details
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_id,
            int(event["event_id"]),
            event["event_code"],
            event["event_start_date"],
            processor_key,
            action_taken,
            details,
        ),
    )
    return True


def alert_exists(
    con: sqlite3.Connection,
    *,
    game_id: str,
    alert_date: str,
    alert_type: str,
    title: str,
    team_id: int | None,
    event_id: int | None,
) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM game_alerts
        WHERE game_id = ?
          AND alert_date = ?
          AND alert_type = ?
          AND title = ?
          AND COALESCE(team_id, -1) = COALESCE(?, -1)
          AND COALESCE(event_id, -1) = COALESCE(?, -1)
        """,
        (game_id, alert_date, alert_type, title, team_id, event_id),
    ).fetchone()
    return row is not None


def create_alert(
    con: sqlite3.Connection,
    *,
    game_id: str,
    alert_date: str,
    severity: str,
    alert_type: str,
    title: str,
    message: str,
    due_date: str | None = None,
    team_id: int | None = None,
    event_id: int | None = None,
) -> bool:
    if alert_exists(
        con,
        game_id=game_id,
        alert_date=alert_date,
        alert_type=alert_type,
        title=title,
        team_id=team_id,
        event_id=event_id,
    ):
        return False
    con.execute(
        """
        INSERT INTO game_alerts (
            game_id, alert_date, severity, alert_type, team_id, event_id,
            title, message, due_date
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (game_id, alert_date, severity, alert_type, team_id, event_id, title, message, due_date),
    )
    return True


def apply_event_settings(con: sqlite3.Connection, event: sqlite3.Row) -> list[str]:
    settings = EVENT_SETTING_ACTIONS.get(event["event_code"], {})
    applied = []
    for key, value in settings.items():
        upsert_setting(con, key, value)
        applied.append(f"{key}={value}")
    return applied


def process_calendar_events(con: sqlite3.Connection, game_id: str, target_date: str) -> tuple[int, int]:
    events = events_on_date(con, target_date)
    processed = 0
    alerts = 0
    for event in events:
        if event_already_processed(con, game_id, int(event["event_id"]), "calendar_event"):
            continue
        applied = apply_event_settings(con, event)
        details = event["notes"] or ""
        if event["event_code"] == "NEXT_NFL_LEAGUE_YEAR_START":
            contract_year = parse_date(event["event_start_date"]).year
            expiration_result = contract_negotiations.process_expired_contracts(
                con,
                expiring_season=contract_year - 1,
                contract_league_year=contract_year,
                transaction_date=event["event_start_date"],
            )
            details = (
                f"{details} Expired contracts processed: "
                f"{expiration_result['processed']} player(s)."
            ).strip()
        if event["event_code"] == "SIM_YEAR_START":
            draft_year = int(event["league_year"]) + 1
            draft_result = draft_class_bootstrap.ensure_draft_class(
                con,
                draft_year=draft_year,
                seed=f"{game_id}:draft-class:{draft_year}",
                notes=f"Generated at sim year start {event['event_start_date']}.",
                refresh_legacy_without_offboard=True,
            )
            details = f"{details} {draft_result.message}".strip()
        if applied:
            details = f"{details} Settings updated: {', '.join(applied)}".strip()
        action = "settings_updated" if applied else "logged"
        if mark_event_processed(
            con,
            game_id=game_id,
            event=event,
            processor_key="calendar_event",
            action_taken=action,
            details=details,
        ):
            processed += 1
            if event["event_category"] in {"Roster", "System", "Transaction", "Game Phase"}:
                severity = "WARNING" if event["event_category"] == "Roster" else "INFO"
                if create_alert(
                    con,
                    game_id=game_id,
                    alert_date=target_date,
                    severity=severity,
                    alert_type="CALENDAR_EVENT",
                    event_id=int(event["event_id"]),
                    title=event["event_name"],
                    message=details or f"{event['event_name']} reached.",
                    due_date=event["event_start_date"],
                ):
                    alerts += 1
    return processed, alerts


def create_upcoming_event_alerts(con: sqlite3.Connection, game_id: str, target_date: str) -> int:
    alerts = 0
    today = parse_date(target_date)
    end = today + timedelta(days=14)
    rows = con.execute(
        """
        SELECT *
        FROM league_calendar_view
        WHERE date(event_start_date) > date(?)
          AND date(event_start_date) <= date(?)
        ORDER BY event_start_date, sort_order
        """,
        (target_date, end.isoformat()),
    ).fetchall()
    for event in rows:
        if event["event_code"] not in KEY_REMINDER_EVENTS:
            continue
        days_left = (parse_date(event["event_start_date"]) - today).days
        if days_left not in {14, 7, 3, 1}:
            continue
        severity = "WARNING" if event["event_code"] in {"FINAL_ROSTER_CUTDOWN_53", "TRADE_DEADLINE"} else "INFO"
        title = f"{event['event_name']} in {days_left} day{'s' if days_left != 1 else ''}"
        message = event["notes"] or f"{event['event_name']} is coming up."
        if create_alert(
            con,
            game_id=game_id,
            alert_date=target_date,
            severity=severity,
            alert_type="UPCOMING_EVENT",
            event_id=int(event["event_id"]),
            title=title,
            message=message,
            due_date=event["event_start_date"],
        ):
            alerts += 1
    return alerts


def validate_rosters_if_needed(
    con: sqlite3.Connection,
    game_id: str,
    target_date: str,
    phase: sqlite3.Row,
) -> tuple[int, int, int, int, int]:
    if not int(phase["roster_limits_enforced"] or 0) or not phase["roster_rule_phase"]:
        return 0, 0, 0, 0, 0

    rule_set = roster_rules.get_rule_set(con, int(phase["league_year"]), phase["roster_rule_phase"])
    teams = con.execute("SELECT * FROM teams ORDER BY abbreviation").fetchall()
    teams_checked = 0
    failures = 0
    error_count = 0
    warning_count = 0
    alerts = 0

    for team in teams:
        summary, issues = roster_rules.validate_team(con, team, rule_set, include_info=False)
        teams_checked += 1
        team_errors = int(summary["error_count"])
        team_warnings = int(summary["warning_count"])
        error_count += team_errors
        warning_count += team_warnings
        if team_errors or team_warnings:
            failures += 1 if team_errors else 0
            severity = "ERROR" if team_errors else "WARNING"
            title = f"{summary['team']} roster compliance"
            message = (
                f"{summary['team']} has {team_errors} errors and {team_warnings} warnings "
                f"for {rule_set['phase']}. Active {summary['active_count']}/"
                f"{rule_set['active_roster_limit']}, controlled {summary['total_controlled_count']}/"
                f"{rule_set['total_roster_limit']}, cap space "
                f"{roster_rules.format_money(int(summary['cap_space']))}."
            )
            if issues:
                message += f" First issue: {issues[0]['message']}"
            if create_alert(
                con,
                game_id=game_id,
                alert_date=target_date,
                severity=severity,
                alert_type="ROSTER_COMPLIANCE",
                team_id=int(summary["team_id"]),
                title=title,
                message=message,
                due_date=target_date,
            ):
                alerts += 1
    return teams_checked, failures, error_count, warning_count, alerts


def run_already_exists(con: sqlite3.Connection, game_id: str, target_date: str) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT *
        FROM game_daily_processing_runs
        WHERE game_id = ? AND processed_date = ?
        """,
        (game_id, target_date),
    ).fetchone()


def process_date(
    con: sqlite3.Connection,
    *,
    game_id: str,
    target_date: str,
    from_date: str | None = None,
    force: bool = False,
) -> DailyResult:
    ensure_schema(con)
    phase = phase_for_date(con, target_date)
    existing = run_already_exists(con, game_id, target_date)
    if existing and not force:
        return DailyResult(
            processed_date=target_date,
            phase_code=phase["phase_code"],
            phase_name=phase["phase_name"],
            roster_limits_enforced=int(phase["roster_limits_enforced"] or 0),
            roster_rule_phase=phase["roster_rule_phase"],
            event_count=0,
            processed_event_count=0,
            alerts_created=0,
            injuries_resolved=int(existing["injuries_resolved"] or 0) if "injuries_resolved" in existing.keys() else 0,
            run_id=int(existing["processing_run_id"]),
        )

    if force and existing:
        con.execute(
            "DELETE FROM game_daily_processing_runs WHERE processing_run_id = ?",
            (int(existing["processing_run_id"]),),
        )

    upsert_setting(con, "current_calendar_phase", phase["phase_code"])
    upsert_setting(con, "roster_limits_enforced", str(int(phase["roster_limits_enforced"] or 0)))
    if phase["roster_rule_phase"]:
        upsert_setting(con, "roster_rule_phase", phase["roster_rule_phase"])

    event_count = len(events_on_date(con, target_date))
    processed_events, event_alerts = process_calendar_events(con, game_id, target_date)
    reminder_alerts = create_upcoming_event_alerts(con, game_id, target_date)
    teams_checked, failures, roster_errors, roster_warnings, roster_alerts = validate_rosters_if_needed(
        con,
        game_id,
        target_date,
        phase,
    )
    injuries_resolved = injury_model.resolve_available_injuries(con, target_date)
    alerts = event_alerts + reminder_alerts + roster_alerts

    cur = con.execute(
        """
        INSERT INTO game_daily_processing_runs (
            game_id, from_date, to_date, processed_date, phase_code, phase_name,
            roster_limits_enforced, roster_rule_phase, event_count,
            processed_event_count, alert_count, teams_checked, roster_failures,
            roster_error_count, roster_warning_count, injuries_resolved, injury_hook_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_id,
            from_date,
            target_date,
            target_date,
            phase["phase_code"],
            phase["phase_name"],
            int(phase["roster_limits_enforced"] or 0),
            phase["roster_rule_phase"],
            event_count,
            processed_events,
            alerts,
            teams_checked,
            failures,
            roster_errors,
            roster_warnings,
            injuries_resolved,
            "enabled",
        ),
    )
    return DailyResult(
        processed_date=target_date,
        phase_code=phase["phase_code"],
        phase_name=phase["phase_name"],
        roster_limits_enforced=int(phase["roster_limits_enforced"] or 0),
        roster_rule_phase=phase["roster_rule_phase"],
        event_count=event_count,
        processed_event_count=processed_events,
        alerts_created=alerts,
        teams_checked=teams_checked,
        roster_failures=failures,
        roster_errors=roster_errors,
        roster_warnings=roster_warnings,
        injuries_resolved=injuries_resolved,
        run_id=int(cur.lastrowid),
    )


def process_range(
    con: sqlite3.Connection,
    *,
    game_id: str,
    from_date: str,
    to_date: str,
    include_start: bool = False,
    force: bool = False,
) -> RangeResult:
    start = parse_date(from_date)
    end = parse_date(to_date)
    if end < start:
        raise ValueError("to_date must be on or after from_date.")

    cursor = start if include_start else start + timedelta(days=1)
    results: list[DailyResult] = []
    while cursor <= end:
        results.append(
            process_date(
                con,
                game_id=game_id,
                target_date=cursor.isoformat(),
                from_date=from_date,
                force=force,
            )
        )
        cursor += timedelta(days=1)
    return RangeResult(
        game_id=game_id,
        from_date=from_date,
        to_date=to_date,
        days_processed=len(results),
        daily_results=results,
    )


def process_event_range(
    con: sqlite3.Connection,
    *,
    game_id: str,
    from_date: str,
    to_date: str,
    include_start: bool = False,
    force: bool = False,
) -> EventRangeResult:
    ensure_schema(con)
    start = parse_date(from_date)
    end = parse_date(to_date)
    if end < start:
        raise ValueError("to_date must be on or after from_date.")

    dates = event_dates_between(
        con,
        from_date=from_date,
        to_date=to_date,
        include_start=include_start,
    )
    if force and dates:
        con.execute(
            """
            DELETE FROM game_processed_events
            WHERE game_id = ?
              AND processor_key = 'calendar_event'
              AND date(event_date) >= date(?)
              AND date(event_date) <= date(?)
            """,
            (game_id, dates[0], dates[-1]),
        )

    event_count = 0
    processed_event_count = 0
    alerts_created = 0
    for target_date in dates:
        event_count += len(events_on_date(con, target_date))
        processed, alerts = process_calendar_events(con, game_id, target_date)
        processed_event_count += processed
        alerts_created += alerts

    return EventRangeResult(
        game_id=game_id,
        from_date=from_date,
        to_date=to_date,
        event_dates_processed=len(dates),
        event_count=event_count,
        processed_event_count=processed_event_count,
        alerts_created=alerts_created,
    )


def open_alerts(con: sqlite3.Connection, game_id: str, limit: int) -> list[sqlite3.Row]:
    ensure_schema(con)
    return list(
        con.execute(
            """
            SELECT ga.*, t.abbreviation AS team
            FROM game_alerts ga
            LEFT JOIN teams t ON t.team_id = ga.team_id
            WHERE ga.game_id = ?
              AND ga.status = 'Open'
            ORDER BY
                CASE ga.severity
                    WHEN 'ERROR' THEN 1
                    WHEN 'WARNING' THEN 2
                    ELSE 3
                END,
                date(COALESCE(ga.due_date, ga.alert_date)),
                ga.alert_id
            LIMIT ?
            """,
            (game_id, limit),
        )
    )


def print_range_result(result: RangeResult) -> None:
    print("Daily processing:")
    print(f"  Days processed: {result.days_processed}")
    print(f"  Calendar events processed: {result.processed_event_count}/{result.event_count}")
    print(f"  Alerts created: {result.alerts_created}")
    if result.teams_checked:
        print(
            f"  Roster checks: {result.teams_checked} team-checks, "
            f"{result.roster_failures} teams with errors, "
            f"{result.roster_errors} errors, {result.roster_warnings} warnings"
        )
    print(f"  Injuries resolved: {result.injuries_resolved}")
    print("  Hooks: AI GM not configured, development not implemented, injuries enabled")


def print_event_range_result(result: EventRangeResult) -> None:
    print("Event processing:")
    print(f"  Event dates checked: {result.event_dates_processed}")
    print(f"  Calendar events processed: {result.processed_event_count}/{result.event_count}")
    print(f"  Alerts created: {result.alerts_created}")
    print("  Roster checks: weekly only")


def print_alerts(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("No open alerts.")
        return
    for row in rows:
        team = f" {row['team']}" if row["team"] else ""
        due = f" due {row['due_date']}" if row["due_date"] else ""
        print(f"[{row['severity']}] {row['alert_date']}{team}{due}: {row['title']}")
        print(f"  {row['message']}")


def action_setup(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    con.commit()
    print("Daily processor schema is ready.")


def action_process(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_id = args.game_id or active_game_id(con)
    to_date = args.to_date or current_game_date(con)
    from_date = args.from_date or to_date
    result = process_range(
        con,
        game_id=game_id,
        from_date=from_date,
        to_date=to_date,
        include_start=args.include_start,
        force=args.force,
    )
    con.commit()
    print_range_result(result)


def action_process_events(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_id = args.game_id or active_game_id(con)
    to_date = args.to_date or current_game_date(con)
    from_date = args.from_date or to_date
    result = process_event_range(
        con,
        game_id=game_id,
        from_date=from_date,
        to_date=to_date,
        include_start=args.include_start or from_date == to_date,
        force=args.force,
    )
    con.commit()
    print_event_range_result(result)


def action_alerts(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game_id = args.game_id or active_game_id(con)
    print_alerts(open_alerts(con, game_id, args.limit))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run daily processing hooks.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("setup", help="Create daily processing tables.")

    process_parser = subparsers.add_parser("process", help="Process one date or a date range.")
    process_parser.add_argument("--game-id")
    process_parser.add_argument("--from-date")
    process_parser.add_argument("--to-date")
    process_parser.add_argument("--include-start", action="store_true")
    process_parser.add_argument("--force", action="store_true")

    event_parser = subparsers.add_parser("process-events", help="Process only calendar events in a date range.")
    event_parser.add_argument("--game-id")
    event_parser.add_argument("--from-date")
    event_parser.add_argument("--to-date")
    event_parser.add_argument("--include-start", action="store_true")
    event_parser.add_argument("--force", action="store_true")

    alerts_parser = subparsers.add_parser("alerts", help="Show open alerts.")
    alerts_parser.add_argument("--game-id")
    alerts_parser.add_argument("--limit", type=int, default=20)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    con = connect(args.db)
    try:
        if args.command == "setup":
            action_setup(con, args)
        elif args.command == "process":
            action_process(con, args)
        elif args.command == "process-events":
            action_process_events(con, args)
        elif args.command == "alerts":
            action_alerts(con, args)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
