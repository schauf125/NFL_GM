#!/usr/bin/env python3
"""Weekly processing hooks for NFL GM Sim saves.

Calendar advancement is intentionally lightweight: important calendar events run
on their dates, while heavier roster/compliance checks run once after each
completed regular-season week.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import ai_gm
import cpu_depth_chart
import daily_processor
import event_generator
import free_agency_processor
import game_flow
import league_calendar
import league_news
import roster_cutdown
import roster_rules
import scouting
import season_storylines
import pro_player_fog
import trade_engine


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
AI_GM_FULL_SCAN_WEEKS = {1, 4, 8, 12, 16}
CPU_SCOUTING_WEEKS = {2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 17, 18}
AI_GM_MAJOR_INJURY_GAMES = 5
ROSTER_MAINTENANCE_WEEKS = {1, 4, 8, 12, 16, 18}
PRACTICE_SQUAD_SANITY_WEEKS = {1, 8, 16}
PRACTICE_SQUAD_POACH_WEEKS = {2, 6, 10, 14, 17}
DEPTH_SANITY_WEEKS = {1, 8, 16, 18}
CPU_TRADE_MARKET_WEEKS = {4, 8}
YOUTH_EVALUATION_NEWS_WEEKS = {8, 10, 12, 14, 16, 18}
YOUTH_EVALUATION_POSITIONS = {
    "RB",
    "WR",
    "TE",
    "EDGE",
    "IDL",
    "DT",
    "NT",
    "LB",
    "ILB",
    "OLB",
    "CB",
    "NB",
    "FS",
    "SS",
    "S",
}


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
    ai_gm_hook_status: str = "not_run"
    ai_gm_operations_queued: int = 0
    ai_gm_operations_skipped: int = 0
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
    season_storylines.ensure_schema(con)
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
            ai_gm_operations_queued INTEGER NOT NULL DEFAULT 0,
            ai_gm_operations_skipped INTEGER NOT NULL DEFAULT 0,
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
    if "ai_gm_hook_status" not in existing:
        con.execute("ALTER TABLE game_weekly_processing_runs ADD COLUMN ai_gm_hook_status TEXT NOT NULL DEFAULT 'not_configured'")
    if "ai_gm_operations_queued" not in existing:
        con.execute("ALTER TABLE game_weekly_processing_runs ADD COLUMN ai_gm_operations_queued INTEGER NOT NULL DEFAULT 0")
    if "ai_gm_operations_skipped" not in existing:
        con.execute("ALTER TABLE game_weekly_processing_runs ADD COLUMN ai_gm_operations_skipped INTEGER NOT NULL DEFAULT 0")
    if "hook_timing_json" not in existing:
        con.execute("ALTER TABLE game_weekly_processing_runs ADD COLUMN hook_timing_json TEXT")


def json_dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone() is not None


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


def major_injury_pressure_this_week(
    con: sqlite3.Connection,
    *,
    start_date: str,
    end_date: str,
) -> int:
    try:
        row = con.execute(
            """
            SELECT COUNT(*) AS count
            FROM active_player_injuries
            WHERE resolved_at IS NULL
              AND status IN ('Out', 'IR', 'PUP', 'NFI')
              AND date(start_date) BETWEEN date(?) AND date(?)
              AND COALESCE(expected_games, 0) >= ?
            """,
            (start_date, end_date, AI_GM_MAJOR_INJURY_GAMES),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["count"] or 0) if row else 0


def unavailable_injuries_this_week(
    con: sqlite3.Connection,
    *,
    start_date: str,
    end_date: str,
) -> int:
    try:
        row = con.execute(
            """
            SELECT COUNT(*) AS count
            FROM active_player_injuries
            WHERE resolved_at IS NULL
              AND status IN ('Out', 'IR', 'PUP', 'NFI')
              AND date(start_date) BETWEEN date(?) AND date(?)
            """,
            (start_date, end_date),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["count"] or 0) if row else 0


def empty_roster_result(reason: str = "cadence_skip") -> dict[str, int | str]:
    return {
        "teams": 0,
        "promoted": 0,
        "signed": 0,
        "swapped": 0,
        "swaps": 0,
        "released": 0,
        "moved_to_ps": 0,
        "skipped": 1,
        "reason": reason,
    }


def roster_move_count(*results: dict[str, object]) -> int:
    move_keys = ("promoted", "signed", "poached", "swapped", "swaps", "released", "moved_to_ps")
    total = 0
    for result in results:
        for key in move_keys:
            value = result.get(key, 0)
            total += int(value or 0)
    return total


def roster_maintenance_plan(
    con: sqlite3.Connection,
    *,
    week: int,
    window: WeekWindow,
) -> dict[str, bool | int | str]:
    new_unavailable = unavailable_injuries_this_week(
        con,
        start_date=window.start_date,
        end_date=window.end_date,
    )
    checkpoint = week in ROSTER_MAINTENANCE_WEEKS
    return {
        "reason": "checkpoint" if checkpoint else "new_unavailable_injury" if new_unavailable else "cadence_skip",
        "new_unavailable_injuries": new_unavailable,
        "practice_squad": week in PRACTICE_SQUAD_SANITY_WEEKS,
        "injury_replacements": checkpoint or new_unavailable > 0,
        "position_replacements": checkpoint or new_unavailable > 0,
        "veteran_free_agents": checkpoint or new_unavailable > 0,
        "practice_squad_poaching": week in PRACTICE_SQUAD_POACH_WEEKS or new_unavailable > 0,
        "depth_swaps": week in DEPTH_SANITY_WEEKS,
        "active_trim": checkpoint or new_unavailable > 0,
        "validation": checkpoint or new_unavailable > 0,
    }


def should_run_ai_gm_weekly(
    con: sqlite3.Connection,
    *,
    week: int,
    window: WeekWindow,
) -> tuple[bool, str, int]:
    if week in AI_GM_FULL_SCAN_WEEKS:
        return True, "scheduled_full_scan", 32
    major_injuries = major_injury_pressure_this_week(
        con,
        start_date=window.start_date,
        end_date=window.end_date,
    )
    if major_injuries:
        return True, f"major_injury_pressure:{major_injuries}", 16
    return False, "cadence_skip", 0


def should_run_cpu_scouting_weekly(week: int) -> bool:
    return week in CPU_SCOUTING_WEEKS


def rebuilding_evaluation_score(*, week: int, wins: int, losses: int, ties: int, point_diff: int) -> float:
    games = wins + losses + ties
    if games < 6:
        return 0.0
    win_pct = (wins + ties * 0.5) / max(1, games)
    point_diff_per_game = point_diff / max(1, games)
    score = 0.0
    if week >= 8 and (win_pct < 0.35 or point_diff_per_game < -7.0):
        score = max(score, 0.42)
    if week >= 11 and (win_pct < 0.45 or point_diff_per_game < -4.0):
        score = max(score, 0.62)
    if week >= 14 and win_pct < 0.50:
        score = max(score, 0.78)
    if week >= 16 and win_pct < 0.56 and point_diff_per_game < -1.5:
        score = max(score, 0.92)
    return min(1.0, max(0.0, score))


def create_youth_evaluation_news(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    week: int,
    news_date: str,
) -> int:
    if week not in YOUTH_EVALUATION_NEWS_WEEKS:
        return 0
    required_tables = {"game_player_stats", "game_sim_runs", "season_team_records", "players", "teams"}
    if any(not table_exists(con, table) for table in required_tables):
        return 0
    league_news.ensure_schema(con)

    created = 0
    record_rows = con.execute(
        """
        SELECT
            str.team_id,
            str.wins,
            str.losses,
            str.ties,
            COALESCE(str.points_for, 0) - COALESCE(str.points_against, 0) AS point_diff,
            t.abbreviation
        FROM season_team_records str
        JOIN teams t ON t.team_id = str.team_id
        WHERE str.season = ?
        """,
        (season,),
    ).fetchall()
    evaluation_teams = {
        int(row["team_id"]): {
            "score": rebuilding_evaluation_score(
                week=week,
                wins=int(row["wins"] or 0),
                losses=int(row["losses"] or 0),
                ties=int(row["ties"] or 0),
                point_diff=int(row["point_diff"] or 0),
            ),
            "record": f"{int(row['wins'] or 0)}-{int(row['losses'] or 0)}"
            + (f"-{int(row['ties'] or 0)}" if int(row["ties"] or 0) else ""),
            "abbreviation": str(row["abbreviation"]),
        }
        for row in record_rows
    }
    evaluation_teams = {team_id: data for team_id, data in evaluation_teams.items() if float(data["score"]) > 0}
    if not evaluation_teams:
        return 0

    placeholders = ",".join("?" for _ in evaluation_teams)
    rows = con.execute(
        f"""
        SELECT
            gps.team_id,
            gps.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.years_exp,
            p.is_rookie,
            SUM(CASE WHEN gps.stat_key = 'offensive_snaps' THEN gps.stat_value ELSE 0 END) AS off_snaps,
            SUM(CASE WHEN gps.stat_key = 'defensive_snaps' THEN gps.stat_value ELSE 0 END) AS def_snaps,
            SUM(CASE WHEN gps.stat_key = 'special_teams_snaps' THEN gps.stat_value ELSE 0 END) AS st_snaps,
            SUM(CASE WHEN gps.stat_key = 'total_snaps' THEN gps.stat_value ELSE 0 END) AS total_snaps
        FROM game_player_stats gps
        JOIN game_sim_runs gsr ON gsr.run_id = gps.run_id
        JOIN players p ON p.player_id = gps.player_id
        WHERE gsr.season = ?
          AND gsr.week = ?
          AND gsr.status = 'final'
          AND gps.team_id IN ({placeholders})
          AND p.position IN ({",".join("?" for _ in YOUTH_EVALUATION_POSITIONS)})
          AND (COALESCE(p.is_rookie, 0) = 1 OR (COALESCE(p.age, 26) <= 23 AND COALESCE(p.years_exp, 0) <= 2))
        GROUP BY gps.team_id, gps.player_id
        HAVING (off_snaps + def_snaps) >= 16
        ORDER BY (off_snaps + def_snaps) DESC, total_snaps DESC
        """,
        (season, week, *evaluation_teams.keys(), *sorted(YOUTH_EVALUATION_POSITIONS)),
    ).fetchall()

    seen_teams: set[int] = set()
    for row in rows:
        team_id = int(row["team_id"])
        if team_id in seen_teams:
            continue
        team_data = evaluation_teams.get(team_id)
        if not team_data:
            continue
        primary_snaps = int((row["off_snaps"] or 0) + (row["def_snaps"] or 0))
        total_snaps = int(row["total_snaps"] or primary_snaps + int(row["st_snaps"] or 0))
        if primary_snaps < 20 and total_snaps < 38:
            continue
        player_id = int(row["player_id"])
        fingerprint = f"youth-evaluation:{game_id}:{season}:{week}:{team_id}:{player_id}"
        if con.execute(
            "SELECT 1 FROM league_news_items WHERE game_id = ? AND fingerprint = ?",
            (game_id, fingerprint),
        ).fetchone():
            seen_teams.add(team_id)
            continue
        player_name = str(row["player_name"])
        position = str(row["position"])
        team_abbr = str(team_data["abbreviation"])
        record = str(team_data["record"])
        title = f"{team_abbr} giving {player_name} a longer look"
        body = (
            f"At {record}, {team_abbr} appears to be using more late-season evaluation snaps. "
            f"{player_name}, a young {position}, played {primary_snaps} offensive/defensive snaps this week as the staff weighs future role decisions."
        )
        league_news.add_news_item(
            con,
            game_id=game_id,
            news_date=news_date,
            category="Roster",
            priority="normal",
            source="Team Wire",
            title=title,
            body=body,
            team_id=team_id,
            player_id=player_id,
            tags=["development", "depth_chart", "rebuild"],
            is_major=False,
            fingerprint=fingerprint,
        )
        pro_player_fog.apply_evaluation_event(
            con,
            game_id=game_id,
            player_id=player_id,
            team_id=team_id,
            season=season,
            event_date=news_date,
            event_type="youth_evaluation_snaps",
            signal_strength=0.55,
            snap_count=primary_snaps,
            source="weekly_youth_evaluation_pro_fog",
            notes="Late-season evaluation snaps gave the staff a clearer pro read.",
        )
        created += 1
        seen_teams.add(team_id)
        if created >= 6:
            break
    return created


def process_week(
    con: sqlite3.Connection,
    *,
    season: int,
    week: int,
    game_id: str | None = None,
    force: bool = False,
    require_complete: bool = True,
    advance_date: bool = True,
    ai_gm_enabled: bool = True,
) -> WeeklyResult:
    ensure_schema(con)
    timings: dict[str, float] = {}

    def mark_timing(label: str, started: float) -> None:
        timings[label] = round(time.perf_counter() - started, 3)

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
            ai_gm_hook_status=str(existing["ai_gm_hook_status"] or "unknown") if "ai_gm_hook_status" in existing.keys() else "unknown",
            ai_gm_operations_queued=int(existing["ai_gm_operations_queued"] or 0) if "ai_gm_operations_queued" in existing.keys() else 0,
            ai_gm_operations_skipped=int(existing["ai_gm_operations_skipped"] or 0) if "ai_gm_operations_skipped" in existing.keys() else 0,
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

    started = time.perf_counter()
    event_result = daily_processor.process_event_range(
        con,
        game_id=target_game_id,
        from_date=from_date,
        to_date=window.end_date,
        include_start=include_start,
        force=force,
        process_ai_gm=ai_gm_enabled,
    )
    mark_timing("daily_events", started)

    started = time.perf_counter()
    if game and advance_date and date.fromisoformat(window.end_date) > date.fromisoformat(game.current_date):
        phase, _crossed_events = game_flow.update_active_game_date(con, game, window.end_date)
    else:
        phase = phase_for_week_end(con, window.end_date)
    mark_timing("date_advance", started)

    roster_plan = roster_maintenance_plan(con, week=week, window=window)

    started = time.perf_counter()
    if roster_plan["practice_squad"]:
        practice_squad_sanity_result = roster_cutdown.sanitize_cpu_practice_squads(
            con,
            season=season,
            game_id=target_game_id,
            include_user_team=False,
        )
    else:
        practice_squad_sanity_result = empty_roster_result(str(roster_plan["reason"]))
    mark_timing("practice_squad_sanity", started)

    started = time.perf_counter()
    if roster_plan["injury_replacements"]:
        injury_replacement_result = roster_cutdown.process_cpu_injury_replacements(
            con,
            season=season,
            game_id=target_game_id,
            include_user_team=False,
        )
    else:
        injury_replacement_result = empty_roster_result(str(roster_plan["reason"]))
    mark_timing("injury_replacements", started)

    started = time.perf_counter()
    if roster_plan["position_replacements"]:
        position_replacement_result = roster_cutdown.process_cpu_position_depth_replacements(
            con,
            season=season,
            game_id=target_game_id,
            include_user_team=False,
        )
    else:
        position_replacement_result = empty_roster_result(str(roster_plan["reason"]))
    mark_timing("position_replacements", started)

    started = time.perf_counter()
    if roster_plan["veteran_free_agents"]:
        veteran_fa_depth_result = roster_cutdown.process_cpu_veteran_free_agent_depth_signings(
            con,
            season=season,
            game_id=target_game_id,
            include_user_team=False,
        )
    else:
        veteran_fa_depth_result = empty_roster_result(str(roster_plan["reason"]))
    mark_timing("veteran_fa_depth", started)

    started = time.perf_counter()
    if roster_plan["practice_squad_poaching"]:
        practice_squad_poach_result = roster_cutdown.process_cpu_practice_squad_poaching(
            con,
            season=season,
            game_id=target_game_id,
            include_user_team=False,
        )
    else:
        practice_squad_poach_result = empty_roster_result(str(roster_plan["reason"]))
    mark_timing("practice_squad_poaching", started)

    started = time.perf_counter()
    if roster_plan["depth_swaps"]:
        depth_swap_result = roster_cutdown.optimize_cpu_same_position_depth(
            con,
            season=season,
            game_id=target_game_id,
            include_user_team=False,
        )
    else:
        depth_swap_result = empty_roster_result(str(roster_plan["reason"]))
    mark_timing("depth_swaps", started)

    started = time.perf_counter()
    if roster_plan["active_trim"]:
        active_trim_result = roster_cutdown.trim_cpu_active_roster_overages(
            con,
            season=season,
            game_id=target_game_id,
            include_user_team=False,
        )
    else:
        active_trim_result = empty_roster_result(str(roster_plan["reason"]))
    mark_timing("active_trim", started)

    started = time.perf_counter()
    if roster_rules.table_exists(con, "waiver_wire"):
        waiver_settlement_result = roster_rules.settle_expired_waivers(
            con,
            season=season,
            target_date=window.end_date,
            game_id=target_game_id,
            include_user_team=False,
            max_claims_per_team=3,
            max_claims_total=64,
            post_cutdown=True,
            max_rounds=10,
        )
    else:
        waiver_settlement_result = {"claims": 0, "processed": 0, "claimed": 0, "cleared": 0}
    mark_timing("waiver_settlement", started)

    started = time.perf_counter()
    reminder_alerts = daily_processor.create_upcoming_event_alerts(con, target_game_id, window.end_date)
    roster_moves = roster_move_count(
        practice_squad_sanity_result,
        injury_replacement_result,
        position_replacement_result,
        veteran_fa_depth_result,
        practice_squad_poach_result,
        depth_swap_result,
        active_trim_result,
    )
    if roster_plan["validation"] or roster_moves or int(waiver_settlement_result.get("processed", 0) or 0):
        teams_checked, failures, roster_errors, roster_warnings, roster_alerts = daily_processor.validate_rosters_if_needed(
            con,
            target_game_id,
            window.end_date,
            phase,
        )
    else:
        teams_checked = failures = roster_errors = roster_warnings = roster_alerts = 0
    alerts = event_result.alerts_created + reminder_alerts + roster_alerts
    mark_timing("alerts_and_roster_validation", started)
    started = time.perf_counter()
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
    mark_timing("league_news", started)
    started = time.perf_counter()
    youth_evaluation_news = create_youth_evaluation_news(
        con,
        game_id=target_game_id,
        season=season,
        week=week,
        news_date=window.end_date,
    )
    mark_timing("youth_evaluation_news", started)
    started = time.perf_counter()
    storyline_result = season_storylines.process_weekly_storylines(
        con,
        game_id=target_game_id,
        season=season,
        week=week,
        event_date=window.end_date,
        seed=f"{target_game_id}:{season}:{week}:storylines",
        emit_messages=True,
    )
    mark_timing("season_storylines", started)
    try:
        started = time.perf_counter()
        queued_scouting_result = scouting.process_assignments(
            con,
            game_id=target_game_id,
            season=season,
            week=week,
            slots=scouting.SPECIFIC_SCOUTING_COUNT,
        )
        if int(queued_scouting_result.get("processed") or 0) > 0:
            scouting_result = queued_scouting_result
            scouting_status = "processed_queue"
            scouting_action = "specific"
            scouting_advanced = int(queued_scouting_result.get("processed") or 0)
            user_background_discoveries = int(queued_scouting_result.get("discovered") or 0)
        else:
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
    mark_timing("user_scouting", started)
    try:
        started = time.perf_counter()
        if should_run_cpu_scouting_weekly(week):
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
        else:
            cpu_scouting_status = "cadence_skip"
            cpu_scouting_teams = 0
            cpu_scouting_advanced = 0
            cpu_scouting_discoveries = 0
            cpu_scouting_skip_reason = "CPU scouting runs on selected season checkpoints."
    except Exception as exc:
        cpu_scouting_status = "skipped"
        cpu_scouting_teams = 0
        cpu_scouting_advanced = 0
        cpu_scouting_discoveries = 0
        cpu_scouting_skip_reason = str(exc)
    mark_timing("cpu_scouting", started)
    scouting_note = (
        f" Scouting auto-assigned {scouting_advanced} prospect(s)"
        + (
            f" and area scouts found {user_background_discoveries} off-board prospect(s)."
            if user_background_discoveries
            else "."
        )
        if scouting_status == "auto_assigned"
        else (
            f" Scouting processed {scouting_advanced} queued prospect(s)"
            + (
                f" and area scouts found {user_background_discoveries} off-board prospect(s)."
                if user_background_discoveries
                else "."
            )
            if scouting_status == "processed_queue"
            else f" Scouting auto-assign skipped: {scouting_skip_reason}."
        )
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
    if youth_evaluation_news:
        scouting_note += f" Youth evaluation generated {youth_evaluation_news} roster note(s)."
    if int(storyline_result.get("inserted", 0)) or int(storyline_result.get("trade_rumors", 0)):
        scouting_note += (
            f" Storylines added {int(storyline_result.get('inserted', 0))} player note(s)"
            f" and {int(storyline_result.get('trade_rumors', 0))} trade rumor(s)."
        )
    sanity_moves = (
        int(practice_squad_sanity_result.get("promoted", 0))
        + int(practice_squad_sanity_result.get("swapped", 0)) * 2
        + int(practice_squad_sanity_result.get("released", 0))
    )
    scouting_note += (
        f" CPU practice squad sanity made {sanity_moves} move(s) "
        f"for {int(practice_squad_sanity_result.get('teams', 0))} team(s)."
        if sanity_moves
        else (
            " CPU practice squad sanity skipped by cadence."
            if practice_squad_sanity_result.get("skipped")
            else " CPU practice squad sanity found no bad CPU stashes."
        )
    )
    replacement_moves = int(injury_replacement_result.get("promoted", 0)) + int(injury_replacement_result.get("signed", 0))
    position_moves = int(position_replacement_result.get("promoted", 0)) + int(position_replacement_result.get("signed", 0))
    veteran_fa_moves = int(veteran_fa_depth_result.get("signed", 0))
    poach_moves = int(practice_squad_poach_result.get("poached", 0))
    scouting_note += (
        f" CPU injury replacements made {replacement_moves} move(s) "
        f"for {int(injury_replacement_result.get('teams', 0))} team(s)."
        if replacement_moves
        else (
            " CPU injury replacements skipped by cadence."
            if injury_replacement_result.get("skipped")
            else " CPU injury replacements found no short CPU rosters."
        )
    )
    if position_moves:
        scouting_note += f" CPU position-depth replacements made {position_moves} move(s)."
    if veteran_fa_moves:
        scouting_note += f" CPU veteran FA depth signings added {veteran_fa_moves} player(s)."
    if poach_moves:
        scouting_note += (
            f" CPU practice squad poaching signed {poach_moves} player(s) "
            f"for {int(practice_squad_poach_result.get('teams', 0))} team(s)."
        )
    elif practice_squad_poach_result.get("skipped"):
        scouting_note += " CPU practice squad poaching skipped by cadence."
    if int(depth_swap_result.get("swaps", 0)):
        scouting_note += f" CPU depth sanity swapped {int(depth_swap_result.get('swaps', 0))} same-position stash(es)."
    trim_moves = int(active_trim_result.get("moved_to_ps", 0)) + int(active_trim_result.get("released", 0))
    if trim_moves:
        scouting_note += f" CPU roster sanity trimmed {trim_moves} active overage(s)."
    if int(waiver_settlement_result.get("processed", 0) or 0):
        scouting_note += (
            f" Waivers settled {int(waiver_settlement_result.get('processed', 0) or 0)} entr"
            f"{'y' if int(waiver_settlement_result.get('processed', 0) or 0) == 1 else 'ies'}."
        )
    fa_signings = 0
    fa_resolve_note = ""
    trade_market_note = ""
    ai_gm_queued = 0
    ai_gm_skipped = 0
    ai_gm_status = "not_run"
    if ai_gm_enabled:
        should_run_ai, ai_reason, ai_limit = should_run_ai_gm_weekly(con, week=week, window=window)
        if should_run_ai:
            try:
                started = time.perf_counter()
                ai_gm_result = ai_gm.run_daily_autonomy(
                    con,
                    game_id=target_game_id,
                    team_abbr=None,
                    all_teams=True,
                    phase="weekly_roster",
                    limit=ai_limit,
                    include_low=False,
                    persist=True,
                    apply_mode=True,
                    include_user_team=False,
                    mode_override=None,
                    max_players=10,
                    max_free_agents=6,
                    current_date=window.end_date,
                )
                mark_timing("ai_gm", started)
                ai_gm_queued = len(ai_gm_result.get("review_items") or [])
                ai_gm_applied = int(ai_gm_result["counts"].get("applied", 0))
                ai_gm_skipped = int(ai_gm_result["counts"].get("skipped", 0))
                ai_gm_status = "applied" if ai_gm_applied else "review_items" if ai_gm_queued else "no_new_ops"
                scouting_note += (
                    f" AI GM weekly actions applied {ai_gm_applied}, "
                    f"review items {ai_gm_queued}, skipped {ai_gm_skipped}"
                    f" ({ai_reason}, limit {ai_limit})."
                )
            except Exception as exc:
                ai_gm_queued = 0
                ai_gm_skipped = 0
                ai_gm_status = "skipped"
                scouting_note += f" AI GM weekly review generation skipped: {exc}."
        else:
            timings["ai_gm"] = 0.0
            ai_gm_status = "cadence_skip"
            scouting_note += f" AI GM weekly scan skipped ({ai_reason})."
        try:
            started = time.perf_counter()
            fa_period = free_agency_processor.active_period(con, season)
            fa_signings = free_agency_processor.resolve_pending_offers(
                con,
                fa_period,
                limit=12,
                write_cap_snapshot=False,
            )
            mark_timing("fa_offer_resolution", started)
            if fa_signings:
                started = time.perf_counter()
                post_fa_trim = roster_cutdown.trim_cpu_active_roster_overages(
                    con,
                    season=season,
                    game_id=target_game_id,
                    include_user_team=False,
                )
                mark_timing("post_fa_trim", started)
                post_fa_trim_moves = (
                    int(post_fa_trim.get("moved_to_ps", 0))
                    + int(post_fa_trim.get("released", 0))
                )
                fa_resolve_note = f" CPU free agency resolved {fa_signings} pending signing(s)."
                if post_fa_trim_moves:
                    fa_resolve_note += f" Trimmed {post_fa_trim_moves} post-signing roster overage(s)."
        except Exception as fa_exc:
            fa_resolve_note = f" CPU free agency resolution skipped: {fa_exc}."
        if week in CPU_TRADE_MARKET_WEEKS:
            try:
                started = time.perf_counter()
                trade_result = trade_engine.ai_gm_process_trade_market(
                    con,
                    game_id=target_game_id,
                    season=season,
                    limit_teams=6,
                    max_proposals_per_team=1,
                    include_user_team_as_target=True,
                    execute_cpu_cpu=True,
                    current_date=window.end_date,
                )
                mark_timing("trade_market", started)
                if trade_result.get("skipped"):
                    trade_market_note = f" CPU trade market skipped: {trade_result.get('skip_reason')}."
                else:
                    counts = trade_result.get("counts") or {}
                    trade_market_note = (
                        " CPU trade market generated "
                        f"{int(counts.get('generated', 0))} proposal(s), "
                        f"{int(counts.get('executed', 0))} CPU trade(s), "
                        f"{int(counts.get('user_pending', 0))} user offer(s)."
                    )
            except Exception as trade_exc:
                timings["trade_market"] = 0.0
                trade_market_note = f" CPU trade market skipped: {trade_exc}."
        else:
            timings["trade_market"] = 0.0
    else:
        ai_gm_status = "disabled"
        scouting_note += " AI GM weekly review generation disabled."
        timings["trade_market"] = 0.0
    scouting_note += fa_resolve_note + trade_market_note

    try:
        started = time.perf_counter()
        depth_refresh = cpu_depth_chart.rebuild_dirty_depth_charts(
            con,
            season=season,
            apply=True,
        )
        mark_timing("depth_chart_refresh", started)
        refreshed_teams = int(depth_refresh.get("teams", 0) or 0)
        if refreshed_teams:
            scouting_note += f" CPU depth charts refreshed for {refreshed_teams} roster-changed team(s)."
    except Exception as depth_exc:
        timings["depth_chart_refresh"] = 0.0
        scouting_note += f" CPU depth chart refresh skipped: {depth_exc}."

    cur = con.execute(
        """
        INSERT INTO game_weekly_processing_runs (
            game_id, season, week, week_end_date, phase_code, phase_name,
            roster_limits_enforced, roster_rule_phase, event_dates_processed,
            event_count, processed_event_count, alert_count, teams_checked,
            roster_failures, roster_error_count, roster_warning_count,
            scouting_hook_status, scouting_action_key, scouting_prospects_advanced,
            cpu_scouting_hook_status, cpu_scouting_teams, cpu_scouting_prospects_advanced,
            cpu_scouting_discoveries, ai_gm_hook_status, ai_gm_operations_queued,
            ai_gm_operations_skipped,
            hook_timing_json,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
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
            ai_gm_status,
            ai_gm_queued,
            ai_gm_skipped,
            json_dumps(timings),
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
        ai_gm_hook_status=ai_gm_status,
        ai_gm_operations_queued=ai_gm_queued,
        ai_gm_operations_skipped=ai_gm_skipped,
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
        print("  Roster checks: skipped or not required this week")
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
    if result.ai_gm_hook_status in {"queued", "review_items", "no_new_ops"}:
        print(
            f"  AI GM review items: {result.ai_gm_operations_queued}, "
            f"skipped {result.ai_gm_operations_skipped}"
        )
    elif result.ai_gm_hook_status not in {"not_run", "not_configured"}:
        print(f"  AI GM ops: {result.ai_gm_hook_status}")


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
            ai_gm_enabled=not args.no_ai_gm,
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
    week_parser.add_argument("--no-ai-gm", action="store_true", help="Skip AI GM weekly enqueue hook.")
    week_parser.set_defaults(func=action_process_week)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
