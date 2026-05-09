#!/usr/bin/env python3
"""Phase-aware AI GM operations controller.

This is the deterministic traffic cop for AI GM modules. It scans team state,
calendar phase, roster limits, cap pressure, injuries, and evaluator signals,
then emits reviewable operations. It does not mutate rosters, contracts, cap
tables, or draft inventory.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import ai_gm_team_evaluator as team_eval
import roster_rules


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_SEASON = 2026

OPS_PHASES = {
    "auto",
    "all",
    "roster_cutdown",
    "weekly_roster",
    "offseason_extensions",
    "free_agency",
    "draft_prep",
    "trade_market",
}

SPECIALIST_GROUPS = {"K", "P", "LS"}
ACTIVE_ROSTER_STATUSES = {"Active", "Questionable", "Doubtful", "Out"}


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
        (name,),
    ).fetchone()
    return row is not None


def json_dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def current_setting(con: sqlite3.Connection, key: str, default: str) -> str:
    if not table_exists(con, "game_settings"):
        return default
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = ?",
        (key,),
    ).fetchone()
    return str(row["setting_value"]) if row else default


def resolve_game_state(con: sqlite3.Connection, game_id: str | None = None) -> dict[str, Any]:
    row = None
    if table_exists(con, "game_saves"):
        if game_id:
            row = con.execute("SELECT * FROM game_saves WHERE game_id = ?", (game_id,)).fetchone()
        else:
            active_game_id = current_setting(con, "active_game_id", "")
            if active_game_id:
                row = con.execute(
                    "SELECT * FROM game_saves WHERE game_id = ? AND status = 'active'",
                    (active_game_id,),
                ).fetchone()
            if row is None:
                row = con.execute(
                    """
                    SELECT *
                    FROM game_saves
                    WHERE status = 'active'
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT 1
                    """
                ).fetchone()

    if row:
        current_date = str(row["current_date"])
        season = as_int(row["current_league_year"], DEFAULT_SEASON)
        resolved_game_id = str(row["game_id"])
        phase_code = str(row["current_phase_code"])
    else:
        current_date = current_setting(con, "current_game_date", f"{DEFAULT_SEASON}-06-01")
        season = as_int(
            current_setting(
                con,
                "current_season",
                current_setting(con, "current_league_year", str(DEFAULT_SEASON)),
            ),
            DEFAULT_SEASON,
        )
        resolved_game_id = game_id or "master"
        phase_code = current_setting(con, "current_calendar_phase", "OFFSEASON_OPEN")

    phase = None
    if table_exists(con, "league_phase_windows"):
        phase = con.execute(
            """
            SELECT *
            FROM league_phase_windows
            WHERE date(?) BETWEEN date(start_date) AND date(end_date)
            ORDER BY league_year
            LIMIT 1
            """,
            (current_date,),
        ).fetchone()

    return {
        "game_id": resolved_game_id,
        "current_date": current_date,
        "season": season,
        "phase_code": str(phase["phase_code"]) if phase else phase_code,
        "phase_name": str(phase["phase_name"]) if phase else phase_code,
        "roster_limits_enforced": as_int(phase["roster_limits_enforced"]) if phase else 0,
        "roster_rule_phase": phase["roster_rule_phase"] if phase else None,
        "transactions_open": as_int(phase["transactions_open"], 1) if phase else 1,
        "salary_cap_mode": str(phase["salary_cap_mode"]) if phase else "TOP_51_ALWAYS",
    }


def team_rows(con: sqlite3.Connection, *, team_abbr: str | None = None) -> list[sqlite3.Row]:
    if team_abbr:
        row = con.execute(
            "SELECT * FROM teams WHERE abbreviation = ?",
            (team_abbr.upper(),),
        ).fetchone()
        if not row:
            raise ValueError(f"Team not found: {team_abbr}")
        return [row]
    return con.execute("SELECT * FROM teams ORDER BY abbreviation").fetchall()


def active_position_counts(con: sqlite3.Connection, team_id: int) -> dict[str, int]:
    rows = con.execute(
        """
        SELECT position, COUNT(*) AS count
        FROM players
        WHERE team_id = ?
          AND status IN ('Active', 'Questionable', 'Doubtful', 'Out')
        GROUP BY position
        """,
        (team_id,),
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        group = team_eval.position_group(row["position"])
        counts[group] = counts.get(group, 0) + as_int(row["count"])
    return counts


def rule_limits(con: sqlite3.Connection, game_state: dict[str, Any]) -> dict[str, int]:
    rule_phase = game_state.get("roster_rule_phase") or "Regular Season"
    try:
        rules = roster_rules.get_rule_set(con, as_int(game_state["season"], DEFAULT_SEASON), str(rule_phase))
        return {
            "active_roster_limit": as_int(rules["active_roster_limit"], 53),
            "practice_squad_limit": as_int(rules["practice_squad_limit"], 16),
        }
    except Exception:
        return {"active_roster_limit": 53, "practice_squad_limit": 16}


def resolved_ops_phase(game_state: dict[str, Any], requested_phase: str) -> str:
    if requested_phase != "auto":
        return requested_phase
    phase_code = str(game_state.get("phase_code") or "").upper()
    if phase_code in {"OFFSEASON_OPEN", "POST_SUPER_BOWL_OFFSEASON"}:
        return "free_agency"
    if phase_code in {"FINAL_CUTDOWN", "TRAINING_CAMP", "CAMP_REPORTING"}:
        return "roster_cutdown"
    if phase_code in {"REGULAR_SEASON", "POSTSEASON"}:
        return "weekly_roster"
    if "DRAFT" in phase_code:
        return "draft_prep"
    if "FREE_AGENCY" in phase_code:
        return "free_agency"
    return "offseason_extensions"


def apply_date_override(con: sqlite3.Connection, game_state: dict[str, Any], current_date: str | None) -> dict[str, Any]:
    if not current_date:
        return game_state
    updated = dict(game_state)
    updated["current_date"] = current_date
    if table_exists(con, "league_phase_windows"):
        phase = con.execute(
            """
            SELECT *
            FROM league_phase_windows
            WHERE date(?) BETWEEN date(start_date) AND date(end_date)
            ORDER BY league_year
            LIMIT 1
            """,
            (current_date,),
        ).fetchone()
        if phase:
            updated.update(
                {
                    "phase_code": str(phase["phase_code"]),
                    "phase_name": str(phase["phase_name"]),
                    "roster_limits_enforced": as_int(phase["roster_limits_enforced"]),
                    "roster_rule_phase": phase["roster_rule_phase"],
                    "transactions_open": as_int(phase["transactions_open"], 1),
                    "salary_cap_mode": str(phase["salary_cap_mode"]),
                }
            )
    return updated


def command_for_operation(operation_type: str, team: str, season: int) -> str:
    if operation_type == "review_cutdown_plan":
        return f"python tools\\play.py ai-gm cutdown-plan --team {team} --season {season} --persist"
    if operation_type == "review_saved_cutdown_plans":
        return f"python tools\\play.py ai-gm cutdown-plans --team {team}"
    if operation_type == "contract_plan":
        return f"python tools\\play.py ai-gm contract-plan --team {team} --season {season} --persist"
    if operation_type in {"free_agent_shortlist", "free_agent_plan"}:
        return f"python tools\\play.py ai-gm free-agent-plan --team {team} --league-year {season} --season {season} --persist"
    if operation_type == "practice_squad_priorities":
        return f"python tools\\play.py ai-gm run --team {team} --decision-type practice_squad_priorities"
    if operation_type == "depth_chart_review":
        return f"python tools\\play.py ai-gm run --team {team} --decision-type depth_chart_review"
    if operation_type == "extension_review":
        return f"python tools\\play.py ai-gm run --team {team} --decision-type extension_interest"
    if operation_type == "trade_block_review":
        return f"python tools\\play.py ai-gm run --team {team} --decision-type trade_block_update"
    if operation_type == "draft_strategy":
        return f"python tools\\play.py ai-gm run --team {team} --decision-type draft_strategy_update"
    return f"python tools\\play.py ai-gm evaluate --team {team} --season {season}"


def make_operation(
    *,
    game_state: dict[str, Any],
    team: sqlite3.Row,
    evaluation: dict[str, Any],
    ops_phase: str,
    operation_type: str,
    decision_type: str,
    priority: int,
    summary: str,
    drivers: list[str],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    team_abbr = str(team["abbreviation"])
    season = as_int(game_state["season"], DEFAULT_SEASON)
    operation_key = f"{game_state['game_id']}:{team_abbr}:{ops_phase}:{operation_type}"
    return {
        "operation_key": operation_key,
        "game_id": game_state["game_id"],
        "decision_date": game_state["current_date"],
        "calendar_phase": game_state["phase_code"],
        "ops_phase": ops_phase,
        "team_id": as_int(team["team_id"]),
        "team": team_abbr,
        "team_name": f"{team['city']} {team['nickname']}",
        "operation_type": operation_type,
        "decision_type": decision_type,
        "priority": max(1, min(10, priority)),
        "summary": summary,
        "drivers": drivers,
        "team_phase": evaluation["team_direction"]["team_phase"],
        "command": command_for_operation(operation_type, team_abbr, season),
        "payload": payload or {},
        "advisory_only": True,
    }


def missing_specialists(position_counts: dict[str, int]) -> list[str]:
    return sorted(group for group in SPECIALIST_GROUPS if position_counts.get(group, 0) < 1)


def build_team_operations(
    con: sqlite3.Connection,
    *,
    team: sqlite3.Row,
    game_state: dict[str, Any],
    requested_phase: str,
    include_low: bool = False,
) -> list[dict[str, Any]]:
    ops_phase = resolved_ops_phase(game_state, requested_phase)
    evaluation = team_eval.evaluate_team(
        con,
        team_abbr=team["abbreviation"],
        season=as_int(game_state["season"], DEFAULT_SEASON),
        game_id=str(game_state["game_id"]),
        evaluation_date=str(game_state["current_date"]),
        persist=False,
    )
    metrics = evaluation["metrics"]
    limits = rule_limits(con, game_state)
    active_count = as_int(metrics.get("active_count"))
    ps_count = as_int(metrics.get("practice_squad_count"))
    position_counts = active_position_counts(con, as_int(team["team_id"]))
    specialists_missing = missing_specialists(position_counts)
    top_needs = evaluation.get("roster_needs", [])
    high_needs = [need for need in top_needs if need.get("priority") in {"urgent", "high"}]
    surplus = evaluation.get("roster_surplus", [])
    flags = evaluation.get("risk_flags", [])
    extension_candidates = evaluation.get("extension_candidates", [])
    trade_block = evaluation.get("trade_block_candidates", [])
    contract_pressure = evaluation.get("contract_pressure", [])
    cut_candidates = evaluation.get("cut_candidates", [])

    operations: list[dict[str, Any]] = []

    def add(*, operation_type: str, decision_type: str, priority: int, summary: str, drivers: list[str], payload: dict[str, Any] | None = None) -> None:
        if not include_low and priority < 5:
            return
        operations.append(
            make_operation(
                game_state=game_state,
                team=team,
                evaluation=evaluation,
                ops_phase=ops_phase,
                operation_type=operation_type,
                decision_type=decision_type,
                priority=priority,
                summary=summary,
                drivers=drivers,
                payload=payload,
            )
        )

    include_roster_cutdown = ops_phase in {"all", "roster_cutdown"}
    include_weekly = ops_phase in {"all", "weekly_roster"}
    include_extensions = ops_phase in {"all", "offseason_extensions"}
    include_free_agency = ops_phase in {"all", "free_agency"}
    include_draft = ops_phase in {"all", "draft_prep"}
    include_trade = ops_phase in {"all", "trade_market", "offseason_extensions"}

    over_active = active_count > limits["active_roster_limit"]
    ps_short = ps_count < limits["practice_squad_limit"]
    meaningful_ps_short = ps_count <= max(0, limits["practice_squad_limit"] - 2)
    if include_roster_cutdown and (over_active or ps_short or specialists_missing or active_count >= 60):
        drivers = []
        if over_active:
            drivers.append(f"active roster {active_count}/{limits['active_roster_limit']}")
        if ps_short:
            drivers.append(f"practice squad {ps_count}/{limits['practice_squad_limit']}")
        if specialists_missing:
            drivers.append("missing " + "/".join(specialists_missing))
        if not drivers:
            drivers.append("camp roster review")
        priority = 10 if over_active and game_state.get("roster_limits_enforced") else 8 if over_active else 7
        add(
            operation_type="review_cutdown_plan",
            decision_type="camp_cutdown_recommendation",
            priority=priority,
            summary=(
                f"{team['abbreviation']} needs a reviewed cutdown/practice-squad plan "
                f"for {active_count} active and {ps_count} PS players."
            ),
            drivers=drivers,
            payload={
                "active_count": active_count,
                "practice_squad_count": ps_count,
                "limits": limits,
                "missing_specialists": specialists_missing,
            },
        )

    if (include_roster_cutdown or include_weekly) and specialists_missing:
        add(
            operation_type="free_agent_shortlist",
            decision_type="free_agent_shortlist",
            priority=10,
            summary=f"{team['abbreviation']} is missing active specialist coverage: {', '.join(specialists_missing)}.",
            drivers=["required specialist coverage"],
            payload={"target_positions": specialists_missing, "role": "minimum-cost specialist"},
        )

    injury_flags = [flag for flag in flags if flag.get("risk_type") == "injury"]
    if include_weekly and injury_flags:
        severity = str(injury_flags[0].get("severity") or "medium")
        priority = 8 if severity == "high" else 6
        # Deterministic roster/depth repair already runs before AI weekly ops.
        # Avoid queuing dozens of advisory-only depth-chart reviews during full
        # season sims; reserve weekly AI work for actionable market cover.
        if severity == "high":
            add(
                operation_type="free_agent_shortlist",
                decision_type="free_agent_shortlist",
                priority=max(5, priority - 1),
                summary=f"{team['abbreviation']} should scan free agents for injury cover.",
                drivers=["major injury pressure", "weekly roster maintenance"],
                payload={"risk_flag": injury_flags[0], "top_needs": high_needs[:4]},
            )

    if (include_weekly or include_free_agency) and high_needs:
        cap_band = str(metrics.get("cap_band"))
        priority = 8 if any(need.get("priority") == "urgent" for need in high_needs) else 6
        if cap_band in {"over_cap", "critical"}:
            priority = max(5, priority - 2)
        add(
            operation_type="free_agent_shortlist",
            decision_type="free_agent_shortlist",
            priority=priority,
            summary=(
                f"{team['abbreviation']} should build a shortlist for "
                f"{', '.join(need['position_group'] for need in high_needs[:3])}."
            ),
            drivers=[f"{need['position_group']} {need['priority']}" for need in high_needs[:4]],
            payload={"needs": high_needs[:5], "cap_band": cap_band},
        )

    fa_depth_needs = [
        need
        for need in top_needs
        if need.get("priority") in {"medium", "depth"}
    ]
    if include_free_agency and not high_needs and fa_depth_needs:
        cap_band = str(metrics.get("cap_band"))
        if cap_band not in {"over_cap", "critical"}:
            add(
                operation_type="free_agent_shortlist",
                decision_type="free_agent_shortlist",
                priority=5,
                summary=(
                    f"{team['abbreviation']} should scan value free agents for "
                    f"{', '.join(need['position_group'] for need in fa_depth_needs[:3])}."
                ),
                drivers=[f"{need['position_group']} {need['priority']}" for need in fa_depth_needs[:4]],
                payload={"needs": fa_depth_needs[:5], "cap_band": cap_band, "market_role": "value/depth"},
            )

    if include_free_agency and not high_needs and not fa_depth_needs:
        cap_band = str(metrics.get("cap_band"))
        if cap_band not in {"over_cap", "critical"}:
            add(
                operation_type="free_agent_shortlist",
                decision_type="free_agent_shortlist",
                priority=5,
                summary=f"{team['abbreviation']} should check the value free-agent board for low-risk upgrades.",
                drivers=["open roster-building window", f"cap {cap_band or 'normal'}"],
                payload={"needs": top_needs[:5], "cap_band": cap_band, "market_role": "best-value depth"},
            )

    if (include_weekly or include_roster_cutdown) and meaningful_ps_short and active_count <= limits["active_roster_limit"]:
        add(
            operation_type="practice_squad_priorities",
            decision_type="practice_squad_priorities",
            priority=5,
            summary=f"{team['abbreviation']} has {ps_count}/{limits['practice_squad_limit']} practice squad spots filled.",
            drivers=["practice squad shortfall"],
            payload={"practice_squad_count": ps_count, "practice_squad_limit": limits["practice_squad_limit"]},
        )

    if include_extensions and extension_candidates:
        top_names = ", ".join(candidate["player_name"] for candidate in extension_candidates[:3])
        add(
            operation_type="contract_plan",
            decision_type="extension_interest",
            priority=7 if len(extension_candidates) >= 3 else 6,
            summary=f"{team['abbreviation']} should build a contract plan for extension candidates: {top_names}.",
            drivers=["expiring core players"],
            payload={"extension_candidates": extension_candidates[:6], "contract_pressure": contract_pressure[:6]},
        )

    cap_band = str(metrics.get("cap_band"))
    if include_trade and (cap_band in {"over_cap", "critical", "tight"} or trade_block or surplus):
        drivers = []
        if cap_band in {"over_cap", "critical", "tight"}:
            drivers.append(f"cap {cap_band}")
        if surplus:
            drivers.append("surplus " + ", ".join(row["position_group"] for row in surplus[:3]))
        if trade_block:
            drivers.append(f"{len(trade_block)} trade-block candidates")
        priority = 9 if cap_band in {"over_cap", "critical"} else 6 if trade_block or surplus else 5
        add(
            operation_type="trade_block_review",
            decision_type="trade_block_update",
            priority=priority,
            summary=f"{team['abbreviation']} should review trade-block/cap-lever options.",
            drivers=drivers or ["market housekeeping"],
            payload={
                "cap_band": cap_band,
                "cap_space_display": metrics.get("cap_space_display"),
                "surplus": surplus[:5],
                "trade_block_candidates": trade_block[:6],
                "cut_candidates": cut_candidates[:6],
            },
        )

    if include_draft and (top_needs or contract_pressure):
        add(
            operation_type="draft_strategy",
            decision_type="draft_strategy_update",
            priority=6 if high_needs or contract_pressure else 5,
            summary=f"{team['abbreviation']} should refresh draft priorities from needs and contract cliffs.",
            drivers=[
                *(f"need {need['position_group']}" for need in top_needs[:3]),
                *(f"contract {row['player_name']}" for row in contract_pressure[:2]),
            ],
            payload={"needs": top_needs[:8], "contract_pressure": contract_pressure[:6]},
        )

    operations.sort(key=lambda op: (-as_int(op["priority"]), op["team"], op["operation_type"]))
    return operations


def build_operations(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    team_abbr: str | None = None,
    all_teams: bool = False,
    phase: str = "auto",
    include_low: bool = False,
    limit: int | None = None,
    current_date: str | None = None,
) -> dict[str, Any]:
    if phase not in OPS_PHASES:
        raise ValueError(f"Unknown AI GM ops phase: {phase}")
    if not team_abbr and not all_teams:
        raise ValueError("Provide team_abbr or all_teams=True.")
    game_state = apply_date_override(con, resolve_game_state(con, game_id), current_date)
    rows = team_rows(con, team_abbr=None if all_teams else team_abbr)
    operations: list[dict[str, Any]] = []
    for team in rows:
        operations.extend(
            build_team_operations(
                con,
                team=team,
                game_state=game_state,
                requested_phase=phase,
                include_low=include_low,
            )
        )
    operations.sort(key=lambda op: (-as_int(op["priority"]), op["team"], op["operation_type"]))
    if limit is not None and limit > 0:
        operations = operations[:limit]
    counts_by_type: dict[str, int] = {}
    for operation in operations:
        counts_by_type[operation["operation_type"]] = counts_by_type.get(operation["operation_type"], 0) + 1
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "game": game_state,
        "requested_phase": phase,
        "resolved_phase": resolved_ops_phase(game_state, phase),
        "team": team_abbr.upper() if team_abbr else None,
        "all_teams": all_teams,
        "counts": {
            "operations": len(operations),
            "teams_scanned": len(rows),
            "by_type": counts_by_type,
        },
        "operations": operations,
        "advisory_only": True,
    }


def print_operations(result: dict[str, Any], *, detail_limit: int = 20) -> None:
    game = result["game"]
    print(
        f"AI GM ops scan: {result['resolved_phase']} "
        f"({game['phase_code']} on {game['current_date']}) | "
        f"{result['counts']['operations']} operations across {result['counts']['teams_scanned']} teams"
    )
    if not result["operations"]:
        print("  No AI GM operations recommended.")
        return
    for op in result["operations"][:detail_limit]:
        drivers = "; ".join(op["drivers"][:3])
        print(f"  [{op['priority']}] {op['team']} {op['operation_type']} -> {op['decision_type']}")
        print(f"      {op['summary']}")
        if drivers:
            print(f"      Drivers: {drivers}")
        if op.get("queue_status"):
            queue_id = op.get("queue_id") or "-"
            print(f"      Queue: {op['queue_status']} ({queue_id})")
        print(f"      Command: {op['command']}")
    remaining = len(result["operations"]) - detail_limit
    if remaining > 0:
        print(f"  ... {remaining} more operations hidden by detail limit.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase-aware AI GM operations scanner.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--game-id")
    parser.add_argument("--team")
    parser.add_argument("--all", action="store_true", help="Scan all teams.")
    parser.add_argument("--phase", choices=sorted(OPS_PHASES), default="auto")
    parser.add_argument("--include-low", action="store_true")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--detail-limit", type=int, default=20)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    con = connect(args.db)
    try:
        result = build_operations(
            con,
            game_id=args.game_id,
            team_abbr=args.team,
            all_teams=args.all,
            phase=args.phase,
            include_low=args.include_low,
            limit=args.limit,
        )
        if args.json:
            print(json_dumps(result))
        else:
            print_operations(result, detail_limit=args.detail_limit)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
