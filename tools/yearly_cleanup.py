#!/usr/bin/env python3
"""Yearly data cleanup for save databases.

This pass is deliberately conservative. It removes only stale, derived, or
obviously invalid data that should not shape long-term franchise saves:
never-used low-end free agents, duplicate active contracts, orphaned derived
rows, bad roster states, jersey-number conflicts, and completed-draft clutter.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import cpu_depth_chart
import jersey_numbers
import prune_unsigned_players
import setup_contract_years


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_LEAGUE_YEAR = 2026

TEAM_CONTROLLED_STATUSES = {
    "Active",
    "Questionable",
    "Doubtful",
    "Out",
    "IR",
    "PUP",
    "NFI",
    "Suspended",
    "Practice Squad",
    "Reserve/Future",
    "Injured Reserve",
}
UNCONTROLLED_STATUSES = {"Free Agent", "Released", "Waived", "Retired"}

PLAYER_DERIVED_TABLES = (
    "player_ratings",
    "player_role_scores",
    "player_role_assignments",
    "player_qb_behavior_profiles",
    "player_rb_behavior_profiles",
    "player_receiver_behavior_profiles",
    "player_ol_behavior_profiles",
    "player_edge_behavior_profiles",
    "player_idl_behavior_profiles",
    "player_lb_behavior_profiles",
    "player_secondary_behavior_profiles",
    "player_specialist_behavior_profiles",
    "player_scheme_fits",
    "player_special_teams_flex",
    "player_personalities",
    "player_personality_baselines",
    "player_development_profiles",
    "player_development_modifiers",
    "player_free_agency_preferences",
    "player_evaluation_reports",
    "player_evaluation_events",
)

DRAFT_PROSPECT_CHILD_TABLES = (
    "draft_prospect_ratings",
    "draft_prospect_role_scores",
    "draft_prospect_role_assignments",
    "draft_prospect_qb_behavior_profiles",
    "draft_prospect_rb_behavior_profiles",
    "draft_prospect_receiver_behavior_profiles",
    "draft_prospect_ol_behavior_profiles",
    "draft_prospect_edge_behavior_profiles",
    "draft_prospect_idl_behavior_profiles",
    "draft_prospect_lb_behavior_profiles",
    "draft_prospect_secondary_behavior_profiles",
    "draft_prospect_specialist_behavior_profiles",
    "draft_prospect_special_teams_flex",
    "draft_prospect_combine_results",
    "draft_prospect_pro_day_results",
    "draft_prospect_private_workouts",
    "draft_prospect_personalities",
    "draft_prospect_scouting_notes",
)

STALE_SCOUTING_TABLES = (
    "scouting_assignments",
    "scouting_weekly_actions",
    "scouting_pre_draft_sweeps",
    "scouting_prospect_progress",
    "cpu_scouting_prospect_progress",
    "scouting_senior_bowl_reports",
    "scouting_senior_bowl_runs",
    "scouting_top30_visits",
    "ai_gm_draft_plans",
)


@dataclass
class StepResult:
    key: str
    label: str
    checked: int = 0
    changed: int = 0
    details: dict[str, Any] = field(default_factory=dict)


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
        (name,),
    ).fetchone() is not None


def column_exists(con: sqlite3.Connection, table: str, column: str) -> bool:
    if not table_exists(con, table):
        return False
    return any(row["name"] == column for row in con.execute(f'PRAGMA table_info("{table}")').fetchall())


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def scalar(con: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> int:
    row = con.execute(sql, tuple(params)).fetchone()
    if not row:
        return 0
    value = row[0]
    return as_int(value)


def current_league_year(con: sqlite3.Connection) -> int:
    if table_exists(con, "game_settings"):
        for key in ("current_league_year", "current_season"):
            row = con.execute(
                "SELECT setting_value FROM game_settings WHERE setting_key = ?",
                (key,),
            ).fetchone()
            if row and row["setting_value"]:
                return as_int(row["setting_value"], DEFAULT_LEAGUE_YEAR)
    return DEFAULT_LEAGUE_YEAR


def current_game_id(con: sqlite3.Connection) -> str:
    if table_exists(con, "game_settings"):
        row = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = 'active_game_id'"
        ).fetchone()
        if row and row["setting_value"]:
            return str(row["setting_value"])
    return "master"


def current_game_date(con: sqlite3.Connection, league_year: int) -> str:
    if table_exists(con, "game_settings"):
        row = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
        ).fetchone()
        if row and row["setting_value"]:
            return str(row["setting_value"])
    return f"{league_year}-06-01"


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS yearly_cleanup_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            league_year INTEGER NOT NULL,
            run_date TEXT NOT NULL,
            applied INTEGER NOT NULL DEFAULT 0,
            summary_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_yearly_cleanup_runs_game_year
            ON yearly_cleanup_runs(game_id, league_year, run_id DESC)
        """
    )


def run_unsigned_no_snap_cleanup(
    con: sqlite3.Connection,
    *,
    league_year: int,
    apply: bool,
    min_unsigned_years: int,
    max_overall: int,
) -> StepResult:
    candidates = prune_unsigned_players.candidate_rows(
        con,
        league_year=league_year,
        min_unsigned_years=min_unsigned_years,
        max_overall=max_overall,
        include_historical=False,
        include_drafted=False,
    )
    deleted = prune_unsigned_players.prune_candidates(con, candidates) if apply else 0
    return StepResult(
        key="unsigned_no_snap_players",
        label="Unsigned no-snap players",
        checked=len(candidates),
        changed=deleted if apply else 0,
        details={
            "candidate_count": len(candidates),
            "deleted_player_ids": [candidate.player_id for candidate in candidates[:100]] if apply else [],
            "min_unsigned_years": min_unsigned_years,
            "max_overall": max_overall,
        },
    )


def contract_priority(row: sqlite3.Row, player_team_id: int | None, league_year: int) -> tuple[int, int, int, int, int, int]:
    team_match = 1 if player_team_id is not None and as_int(row["team_id"]) == player_team_id else 0
    covers_current = 1 if as_int(row["start_year"], 9999) <= league_year <= as_int(row["end_year"], -9999) else 0
    type_bonus = {
        "Extension": 5,
        "Standard": 4,
        "FranchiseTag": 4,
        "ExclusiveFranchiseTag": 4,
        "TransitionTag": 3,
        "FifthYearOption": 3,
        "RookieScale": 2,
        "VetMin": 1,
    }.get(str(row["contract_type"] or ""), 0)
    return (
        team_match,
        covers_current,
        type_bonus,
        as_int(row["end_year"]),
        as_int(row["aav"]),
        as_int(row["contract_id"]),
    )


def contracts_overlap(left: sqlite3.Row, right: sqlite3.Row) -> bool:
    return max(as_int(left["start_year"]), as_int(right["start_year"])) <= min(
        as_int(left["end_year"]),
        as_int(right["end_year"]),
    )


def duplicate_contract_ids(con: sqlite3.Connection, *, league_year: int) -> list[int]:
    if not table_exists(con, "contracts"):
        return []
    rows = con.execute(
        """
        SELECT c.*, p.team_id AS player_team_id
        FROM contracts c
        LEFT JOIN players p ON p.player_id = c.player_id
        WHERE COALESCE(c.is_active, 1) = 1
          AND c.player_id IS NOT NULL
        ORDER BY c.player_id, c.start_year, c.end_year, c.contract_id
        """
    ).fetchall()
    by_player: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        by_player.setdefault(as_int(row["player_id"]), []).append(row)

    deactivate: set[int] = set()
    for player_contracts in by_player.values():
        player_team_id = player_contracts[0]["player_team_id"]
        kept: list[sqlite3.Row] = []
        ordered = sorted(
            player_contracts,
            key=lambda row: contract_priority(row, player_team_id, league_year),
            reverse=True,
        )
        for contract in ordered:
            if any(contracts_overlap(contract, keep) for keep in kept):
                deactivate.add(as_int(contract["contract_id"]))
            else:
                kept.append(contract)
    return sorted(deactivate)


def fix_duplicate_contracts(con: sqlite3.Connection, *, league_year: int, apply: bool) -> StepResult:
    ids = duplicate_contract_ids(con, league_year=league_year)
    if apply and ids:
        placeholders = ", ".join("?" for _ in ids)
        con.execute(f"UPDATE contracts SET is_active = 0 WHERE contract_id IN ({placeholders})", ids)
        if table_exists(con, "contract_years"):
            con.execute(f"UPDATE contract_years SET is_active = 0 WHERE contract_id IN ({placeholders})", ids)
        setup_contract_years.rebuild_contract_years(con)
        setup_contract_years.sync_team_cap_space(con)
    return StepResult(
        key="duplicate_contracts",
        label="Duplicate active contracts",
        checked=len(ids),
        changed=len(ids) if apply else 0,
        details={"deactivated_contract_ids": ids if apply else []},
    )


def delete_orphan_rows(
    con: sqlite3.Connection,
    *,
    tables: Iterable[str],
    reference_table: str,
    column: str,
    apply: bool,
) -> tuple[int, dict[str, int]]:
    total = 0
    by_table: dict[str, int] = {}
    for table in tables:
        if not table_exists(con, table) or not column_exists(con, table, column):
            continue
        count = scalar(
            con,
            f"""
            SELECT COUNT(*)
            FROM "{table}"
            WHERE "{column}" IS NOT NULL
              AND "{column}" NOT IN (SELECT "{column}" FROM "{reference_table}")
            """,
        )
        by_table[table] = count
        total += count
        if apply and count:
            con.execute(
                f"""
                DELETE FROM "{table}"
                WHERE "{column}" IS NOT NULL
                  AND "{column}" NOT IN (SELECT "{column}" FROM "{reference_table}")
                """
            )
    return total, by_table


def cleanup_orphaned_player_rows(con: sqlite3.Connection, *, apply: bool) -> StepResult:
    total, by_table = delete_orphan_rows(
        con,
        tables=PLAYER_DERIVED_TABLES,
        reference_table="players",
        column="player_id",
        apply=apply,
    )
    contract_year_orphans = 0
    contract_orphans = 0
    if table_exists(con, "contract_years"):
        contract_year_orphans += scalar(
            con,
            """
            SELECT COUNT(*)
            FROM contract_years
            WHERE contract_id NOT IN (SELECT contract_id FROM contracts)
               OR player_id NOT IN (SELECT player_id FROM players)
            """,
        )
        if apply and contract_year_orphans:
            con.execute(
                """
                DELETE FROM contract_years
                WHERE contract_id NOT IN (SELECT contract_id FROM contracts)
                   OR player_id NOT IN (SELECT player_id FROM players)
                """
            )
    if table_exists(con, "contracts"):
        contract_orphans = scalar(
            con,
            "SELECT COUNT(*) FROM contracts WHERE player_id NOT IN (SELECT player_id FROM players)",
        )
        if apply and contract_orphans:
            con.execute("UPDATE contracts SET is_active = 0 WHERE player_id NOT IN (SELECT player_id FROM players)")
    changed = total + contract_year_orphans + contract_orphans
    details = {
        "derived_player_tables": by_table,
        "contract_year_orphans": contract_year_orphans,
                "orphan_contracts_deactivated": contract_orphans,
    }
    return StepResult(
        key="orphaned_player_rows",
        label="Orphaned player derived rows",
        checked=changed,
        changed=changed if apply else 0,
        details=details,
    )


def cleanup_invalid_roster_states(con: sqlite3.Connection, *, apply: bool) -> StepResult:
    if not table_exists(con, "players"):
        return StepResult("invalid_roster_states", "Invalid roster states")
    invalid_team_ids = scalar(
        con,
        """
        SELECT COUNT(*)
        FROM players
        WHERE team_id IS NOT NULL
          AND team_id NOT IN (SELECT team_id FROM teams)
        """,
    )
    uncontrolled_with_team = scalar(
        con,
        f"""
        SELECT COUNT(*)
        FROM players
        WHERE team_id IS NOT NULL
          AND COALESCE(status, '') IN ({", ".join("?" for _ in UNCONTROLLED_STATUSES)})
        """,
        UNCONTROLLED_STATUSES,
    )
    controlled_without_team = scalar(
        con,
        f"""
        SELECT COUNT(*)
        FROM players
        WHERE team_id IS NULL
          AND COALESCE(status, 'Active') IN ({", ".join("?" for _ in TEAM_CONTROLLED_STATUSES)})
        """,
        TEAM_CONTROLLED_STATUSES,
    )
    invalid_depth = 0
    if table_exists(con, "depth_charts"):
        invalid_depth = scalar(
            con,
            f"""
            SELECT COUNT(*)
            FROM depth_charts dc
            LEFT JOIN players p ON p.player_id = dc.player_id
            WHERE p.player_id IS NULL
               OR p.team_id IS NULL
               OR p.team_id <> dc.team_id
               OR COALESCE(p.status, '') IN ({", ".join("?" for _ in UNCONTROLLED_STATUSES)})
            """,
            UNCONTROLLED_STATUSES,
        )
    if apply:
        con.execute(
            """
            UPDATE players
            SET team_id = NULL,
                status = CASE WHEN COALESCE(status, '') = 'Retired' THEN 'Retired' ELSE 'Free Agent' END,
                jersey_number = NULL
            WHERE team_id IS NOT NULL
              AND team_id NOT IN (SELECT team_id FROM teams)
            """
        )
        con.execute(
            f"""
            UPDATE players
            SET team_id = NULL,
                jersey_number = NULL
            WHERE team_id IS NOT NULL
              AND COALESCE(status, '') IN ({", ".join("?" for _ in UNCONTROLLED_STATUSES)})
            """,
            tuple(UNCONTROLLED_STATUSES),
        )
        con.execute(
            f"""
            UPDATE players
            SET status = 'Free Agent',
                jersey_number = NULL
            WHERE team_id IS NULL
              AND COALESCE(status, 'Active') IN ({", ".join("?" for _ in TEAM_CONTROLLED_STATUSES)})
            """,
            tuple(TEAM_CONTROLLED_STATUSES),
        )
        if table_exists(con, "depth_charts"):
            con.execute(
                f"""
                DELETE FROM depth_charts
                WHERE depth_chart_id IN (
                    SELECT dc.depth_chart_id
                    FROM depth_charts dc
                    LEFT JOIN players p ON p.player_id = dc.player_id
                    WHERE p.player_id IS NULL
                       OR p.team_id IS NULL
                       OR p.team_id <> dc.team_id
                       OR COALESCE(p.status, '') IN ({", ".join("?" for _ in UNCONTROLLED_STATUSES)})
                )
                """,
                tuple(UNCONTROLLED_STATUSES),
            )
        if invalid_depth or controlled_without_team or uncontrolled_with_team or invalid_team_ids:
            cpu_depth_chart.mark_all_cpu_depth_charts_stale(con, reason="yearly cleanup roster-state repair")
    total = invalid_team_ids + uncontrolled_with_team + controlled_without_team + invalid_depth
    return StepResult(
        key="invalid_roster_states",
        label="Invalid roster states",
        checked=total,
        changed=total if apply else 0,
        details={
            "invalid_team_ids": invalid_team_ids,
            "uncontrolled_status_with_team": uncontrolled_with_team,
            "controlled_status_without_team": controlled_without_team,
            "invalid_depth_rows": invalid_depth,
        },
    )


def cleanup_jersey_numbers(con: sqlite3.Connection, *, apply: bool) -> StepResult:
    before = jersey_numbers.audit_numbers(con) if table_exists(con, "players") else {"missing": 0, "duplicates": 0, "illegal": 0}
    changed = 0
    result: dict[str, int] = {}
    if apply:
        result = jersey_numbers.assign_missing_numbers(con, source="yearly_cleanup")
        after = jersey_numbers.audit_numbers(con)
        changed = int(result.get("changed", 0))
    else:
        after = before
    checked = int(before.get("missing", 0)) + int(before.get("duplicates", 0)) + int(before.get("illegal", 0))
    return StepResult(
        key="jersey_numbers",
        label="Jersey numbers",
        checked=checked,
        changed=changed,
        details={"before": before, "after": after, "assignment_result": result},
    )


def completed_draft_years(con: sqlite3.Connection, league_year: int) -> list[int]:
    if not table_exists(con, "draft_classes"):
        return []
    years = [
        as_int(row["draft_year"])
        for row in con.execute(
            "SELECT draft_year FROM draft_classes WHERE draft_year <= ? ORDER BY draft_year",
            (league_year,),
        ).fetchall()
    ]
    complete: list[int] = []
    for year in years:
        state_complete = False
        if table_exists(con, "draft_room_state"):
            state = con.execute(
                "SELECT status FROM draft_room_state WHERE draft_year = ?",
                (year,),
            ).fetchone()
            state_complete = bool(state and str(state["status"] or "").lower() == "completed")
        picks_complete = False
        if table_exists(con, "draft_picks"):
            remaining = scalar(
                con,
                """
                SELECT COUNT(*)
                FROM draft_picks
                WHERE draft_year = ?
                  AND COALESCE(is_used, 0) = 0
                """,
                (year,),
            )
            picks_complete = remaining == 0
        if state_complete or picks_complete:
            complete.append(year)
    return complete


def cleanup_draft_data(con: sqlite3.Connection, *, league_year: int, apply: bool) -> StepResult:
    completed_years = completed_draft_years(con, league_year)
    prospect_orphans, prospect_orphan_tables = delete_orphan_rows(
        con,
        tables=DRAFT_PROSPECT_CHILD_TABLES,
        reference_table="draft_prospects",
        column="prospect_id",
        apply=apply,
    )
    stale_scouting = 0
    stale_scouting_by_table: dict[str, int] = {}
    if completed_years:
        placeholders = ", ".join("?" for _ in completed_years)
        for table in STALE_SCOUTING_TABLES:
            if not table_exists(con, table) or not column_exists(con, table, "draft_year"):
                continue
            count = scalar(con, f'SELECT COUNT(*) FROM "{table}" WHERE draft_year IN ({placeholders})', completed_years)
            stale_scouting_by_table[table] = count
            stale_scouting += count
            if apply and count:
                con.execute(f'DELETE FROM "{table}" WHERE draft_year IN ({placeholders})', completed_years)

    pick_inconsistencies = 0
    selected_prospect_inconsistencies = 0
    orphaned_prospect_player_links = 0
    archived_classes = 0
    if table_exists(con, "draft_picks"):
        pick_inconsistencies = scalar(
            con,
            "SELECT COUNT(*) FROM draft_picks WHERE selected_player_id IS NOT NULL AND COALESCE(is_used, 0) = 0",
        )
        if apply and pick_inconsistencies:
            con.execute("UPDATE draft_picks SET is_used = 1 WHERE selected_player_id IS NOT NULL AND COALESCE(is_used, 0) = 0")
    if table_exists(con, "draft_prospects"):
        selected_prospect_inconsistencies = scalar(
            con,
            """
            SELECT COUNT(*)
            FROM draft_prospects
            WHERE (selected_pick_id IS NOT NULL OR selected_team_id IS NOT NULL OR player_id IS NOT NULL)
              AND LOWER(COALESCE(status, '')) NOT IN ('selected', 'drafted')
            """,
        )
        orphaned_prospect_player_links = scalar(
            con,
            """
            SELECT COUNT(*)
            FROM draft_prospects
            WHERE player_id IS NOT NULL
              AND player_id NOT IN (SELECT player_id FROM players)
            """,
        )
        if apply and selected_prospect_inconsistencies:
            con.execute(
                """
                UPDATE draft_prospects
                SET status = 'selected',
                    updated_at = datetime('now')
                WHERE (selected_pick_id IS NOT NULL OR selected_team_id IS NOT NULL OR player_id IS NOT NULL)
                  AND LOWER(COALESCE(status, '')) NOT IN ('selected', 'drafted')
                """
            )
        if apply and orphaned_prospect_player_links:
            con.execute(
                """
                UPDATE draft_prospects
                SET player_id = NULL,
                    updated_at = datetime('now')
                WHERE player_id IS NOT NULL
                  AND player_id NOT IN (SELECT player_id FROM players)
                """
            )
    if table_exists(con, "draft_classes") and completed_years:
        placeholders = ", ".join("?" for _ in completed_years)
        archived_classes = scalar(
            con,
            f"""
            SELECT COUNT(*)
            FROM draft_classes
            WHERE draft_year IN ({placeholders})
              AND LOWER(COALESCE(status, '')) NOT IN ('archived', 'completed')
            """,
            completed_years,
        )
        if apply and archived_classes:
            con.execute(
                f"""
                UPDATE draft_classes
                SET status = 'archived',
                    updated_at = datetime('now')
                WHERE draft_year IN ({placeholders})
                  AND LOWER(COALESCE(status, '')) NOT IN ('archived', 'completed')
                """,
                completed_years,
            )
    stale_gate_keys = 0
    if table_exists(con, "game_settings"):
        pending_year = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = 'draft_class_setup_pending_year'"
        ).fetchone()
        if pending_year and as_int(pending_year["setting_value"], 9999) <= league_year:
            stale_gate_keys = scalar(
                con,
                """
                SELECT COUNT(*)
                FROM game_settings
                WHERE setting_key IN ('draft_class_setup_pending_year', 'draft_class_setup_pending_reason')
                """,
            )
            if apply and stale_gate_keys:
                con.execute(
                    """
                    DELETE FROM game_settings
                    WHERE setting_key IN ('draft_class_setup_pending_year', 'draft_class_setup_pending_reason')
                    """
                )
    checked = (
        prospect_orphans
        + stale_scouting
        + pick_inconsistencies
        + selected_prospect_inconsistencies
        + orphaned_prospect_player_links
        + archived_classes
        + stale_gate_keys
    )
    return StepResult(
        key="stale_draft_data",
        label="Stale draft data",
        checked=checked,
        changed=checked if apply else 0,
        details={
            "completed_draft_years": completed_years,
            "orphaned_prospect_child_rows": prospect_orphans,
            "orphaned_prospect_child_tables": prospect_orphan_tables,
            "stale_scouting_rows": stale_scouting,
            "stale_scouting_tables": stale_scouting_by_table,
            "pick_inconsistencies": pick_inconsistencies,
            "selected_prospect_inconsistencies": selected_prospect_inconsistencies,
            "orphaned_prospect_player_links": orphaned_prospect_player_links,
            "archived_classes": archived_classes,
            "stale_gate_keys": stale_gate_keys,
        },
    )


def run_yearly_cleanup(
    con: sqlite3.Connection,
    *,
    league_year: int | None = None,
    apply: bool = False,
    min_unsigned_years: int = 2,
    max_unsigned_overall: int = 60,
    persist: bool = True,
) -> dict[str, Any]:
    ensure_schema(con)
    target_year = int(league_year or current_league_year(con))
    steps = [
        run_unsigned_no_snap_cleanup(
            con,
            league_year=target_year,
            apply=apply,
            min_unsigned_years=min_unsigned_years,
            max_overall=max_unsigned_overall,
        ),
        fix_duplicate_contracts(con, league_year=target_year, apply=apply),
        cleanup_orphaned_player_rows(con, apply=apply),
        cleanup_invalid_roster_states(con, apply=apply),
        cleanup_jersey_numbers(con, apply=apply),
        cleanup_draft_data(con, league_year=target_year, apply=apply),
    ]
    if apply:
        setup_contract_years.sync_team_cap_space(con)
    summary = {
        "game_id": current_game_id(con),
        "league_year": target_year,
        "run_date": current_game_date(con, target_year),
        "applied": bool(apply),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "checked": sum(step.checked for step in steps),
        "changed": sum(step.changed for step in steps),
        "steps": [
            {
                "key": step.key,
                "label": step.label,
                "checked": step.checked,
                "changed": step.changed,
                "details": step.details,
            }
            for step in steps
        ],
    }
    if persist:
        con.execute(
            """
            INSERT INTO yearly_cleanup_runs (game_id, league_year, run_date, applied, summary_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                summary["game_id"],
                summary["league_year"],
                summary["run_date"],
                1 if apply else 0,
                json.dumps(summary, indent=2, sort_keys=True),
            ),
        )
    return summary


def print_summary(summary: dict[str, Any]) -> None:
    mode = "APPLIED" if summary["applied"] else "DRY RUN"
    print(
        f"Yearly cleanup {mode}: {summary['game_id']} {summary['league_year']} "
        f"checked {summary['checked']}, changed {summary['changed']}."
    )
    for step in summary["steps"]:
        print(f"  {step['label']}: checked {step['checked']}, changed {step['changed']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run yearly save cleanup.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--league-year", type=int)
    parser.add_argument("--min-unsigned-years", type=int, default=2)
    parser.add_argument("--max-unsigned-overall", type=int, default=60)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args(argv)

    with connect(args.db) as con:
        try:
            summary = run_yearly_cleanup(
                con,
                league_year=args.league_year,
                apply=args.apply,
                min_unsigned_years=args.min_unsigned_years,
                max_unsigned_overall=args.max_unsigned_overall,
                persist=args.apply and not args.no_persist,
            )
            if args.apply:
                con.commit()
            else:
                con.rollback()
        except Exception:
            con.rollback()
            raise
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
