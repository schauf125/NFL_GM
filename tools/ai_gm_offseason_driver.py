#!/usr/bin/env python3
"""CPU AI GM offseason driver.

This module turns the advisory contract and free-agent planners into a cautious
offseason workflow. It still relies on the existing reviewed apply bridges, so
the caller controls transaction commit/rollback.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import ai_gm_contract_planner as contract_planner
import ai_gm_free_agent_planner as free_agent_planner
import contract_negotiations
import free_agency_processor as fa


PHASES = {"pre-free-agency", "free-agency-wave1", "full"}
DEFAULT_MAX_EXTENSIONS_PER_TEAM = 2
DEFAULT_MAX_OFFERS_PER_TEAM = 2
DEFAULT_MARKET_LIMIT = 120


def json_dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def money(value: Any) -> str:
    amount = as_int(value)
    if amount < 0:
        return "-" + money(abs(amount))
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    return f"${amount}"


def ensure_schema(con: sqlite3.Connection) -> None:
    contract_planner.ensure_schema(con)
    free_agent_planner.ensure_schema(con)
    fa.ensure_schema(con)


def active_user_team(con: sqlite3.Connection) -> str | None:
    try:
        return contract_negotiations.active_user_team(con)
    except Exception:
        return None


def team_abbreviations(
    con: sqlite3.Connection,
    *,
    team_abbr: str | None = None,
    all_teams: bool = False,
    include_user_team: bool = False,
    max_teams: int | None = None,
) -> tuple[list[str], str | None]:
    if team_abbr:
        row = con.execute(
            "SELECT abbreviation FROM teams WHERE abbreviation = ?",
            (team_abbr.upper(),),
        ).fetchone()
        if not row:
            raise ValueError(f"Team not found: {team_abbr}")
        return [str(row["abbreviation"])], active_user_team(con)
    if not all_teams:
        raise ValueError("Provide --team TEAM or --all.")

    user_team = active_user_team(con)
    rows = con.execute("SELECT abbreviation FROM teams ORDER BY abbreviation").fetchall()
    teams = [str(row["abbreviation"]) for row in rows]
    if user_team and not include_user_team:
        teams = [abbr for abbr in teams if abbr != user_team]
    if max_teams is not None and max_teams > 0:
        teams = teams[:max_teams]
    return teams, user_team


def apply_result_status(apply_result: dict[str, Any], *, apply_mode: bool) -> str:
    if apply_result.get("applied"):
        return "applied" if apply_mode else "dry_run"
    return "blocked"


def preflight_counts(apply_result: dict[str, Any] | None) -> dict[str, int]:
    if not apply_result:
        return {"warnings": 0, "errors": 0, "skipped": 0}
    preflight = apply_result.get("preflight") or {}
    return {
        "warnings": len(preflight.get("warnings") or []),
        "errors": len(preflight.get("errors") or []),
        "skipped": len(preflight.get("skipped") or []),
    }


def run_contract_team(
    con: sqlite3.Connection,
    *,
    team_abbr: str,
    season: int,
    game_id: str,
    plan_date: str | None,
    apply_mode: bool,
    allow_stale: bool,
    max_extensions_per_team: int,
    max_extension_aav: int | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"team": team_abbr, "phase": "pre-free-agency", "status": "pending"}
    try:
        plan = contract_planner.build_contract_plan(
            con,
            team_abbr=team_abbr,
            season=season,
            game_id=game_id,
            plan_date=plan_date,
            persist=True,
        )
        plan_id = as_int(plan.get("plan_id"))
        target_count = as_int((plan.get("counts") or {}).get("extension_targets"))
        result.update(
            {
                "status": "planned_only" if target_count == 0 else "planned",
                "plan_id": plan_id,
                "extension_targets": target_count,
                "recommended_extension_aav": as_int(
                    (plan.get("budget") or {}).get("recommended_extension_aav")
                ),
                "operations": [],
                "warnings": [],
                "errors": [],
                "skipped": [],
            }
        )
        if target_count == 0:
            return result
        apply_result = contract_planner.apply_contract_plan(
            con,
            plan_id=plan_id,
            allow_stale=allow_stale,
            max_extensions=max(1, max_extensions_per_team),
            max_total_aav=max_extension_aav,
        )
        preflight = apply_result.get("preflight") or {}
        operations = apply_result.get("operations") or []
        result.update(
            {
                "status": apply_result_status(apply_result, apply_mode=apply_mode),
                "apply_result": apply_result,
                "operations": operations,
                "operation_count": len(operations),
                "warnings": preflight.get("warnings") or [],
                "errors": preflight.get("errors") or [],
                "skipped": preflight.get("skipped") or [],
            }
        )
        return result
    except Exception as exc:
        result.update({"status": "error", "error": str(exc), "operations": []})
        return result


def run_free_agency_team(
    con: sqlite3.Connection,
    *,
    team_abbr: str,
    season: int,
    league_year: int,
    game_id: str,
    apply_mode: bool,
    allow_stale: bool,
    max_offers_per_team: int,
    max_fa_aav: int | None,
    refresh_market: bool,
    market_limit: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {"team": team_abbr, "phase": "free-agency-wave1", "status": "pending"}
    try:
        plan = free_agent_planner.build_free_agent_plan(
            con,
            team_abbr=team_abbr,
            league_year=league_year,
            season=season,
            game_id=game_id,
            persist=True,
            refresh_market=refresh_market,
            market_limit=market_limit,
        )
        plan_id = as_int(plan.get("plan_id"))
        counts = plan.get("counts") or {}
        target_count = (
            as_int(counts.get("primary_targets"))
            + as_int(counts.get("value_targets"))
            + as_int(counts.get("bridge_or_depth"))
        )
        result.update(
            {
                "status": "planned_only" if target_count == 0 else "planned",
                "plan_id": plan_id,
                "offer_targets": target_count,
                "primary_targets": as_int(counts.get("primary_targets")),
                "value_targets": as_int(counts.get("value_targets")),
                "bridge_or_depth": as_int(counts.get("bridge_or_depth")),
                "recommended_offer_aav": as_int((plan.get("budget") or {}).get("recommended_offer_aav")),
                "operations": [],
                "warnings": [],
                "errors": [],
                "skipped": [],
            }
        )
        if target_count == 0:
            return result
        apply_result = free_agent_planner.apply_free_agent_plan(
            con,
            plan_id=plan_id,
            allow_stale=allow_stale,
            max_offers=max(1, max_offers_per_team),
            max_total_aav=max_fa_aav,
        )
        preflight = apply_result.get("preflight") or {}
        operations = apply_result.get("operations") or []
        result.update(
            {
                "status": apply_result_status(apply_result, apply_mode=apply_mode),
                "apply_result": apply_result,
                "operations": operations,
                "operation_count": len(operations),
                "warnings": preflight.get("warnings") or [],
                "errors": preflight.get("errors") or [],
                "skipped": preflight.get("skipped") or [],
            }
        )
        return result
    except Exception as exc:
        result.update({"status": "error", "error": str(exc), "operations": []})
        return result


def active_free_agency_status(con: sqlite3.Connection, league_year: int) -> dict[str, Any]:
    try:
        period = fa.current_period(con, league_year)
    except Exception as exc:
        return {"active": False, "error": str(exc)}
    if not period:
        return {"active": False, "status": None}
    return {
        "active": str(period["status"]) == "active",
        "status": period["status"],
        "current_stage": period["current_stage"],
        "current_date": period["current_date"],
        "current_hour": period["current_hour"],
    }


def calculate_totals(result: dict[str, Any]) -> dict[str, int]:
    totals = {
        "teams_scanned": len(result.get("teams") or []),
        "contract_plans": 0,
        "extension_operations": 0,
        "free_agent_plans": 0,
        "free_agent_offer_operations": 0,
        "blocked_results": 0,
        "error_results": 0,
        "warnings": 0,
        "errors": 0,
        "skipped": 0,
    }
    for team_result in result.get("teams") or []:
        contract = team_result.get("contract")
        if contract:
            if contract.get("plan_id"):
                totals["contract_plans"] += 1
            totals["extension_operations"] += len(contract.get("operations") or [])
            totals["warnings"] += len(contract.get("warnings") or [])
            totals["errors"] += len(contract.get("errors") or []) + (1 if contract.get("status") == "error" else 0)
            totals["skipped"] += len(contract.get("skipped") or [])
            if contract.get("status") == "blocked":
                totals["blocked_results"] += 1
            if contract.get("status") == "error":
                totals["error_results"] += 1
        free_agency = team_result.get("free_agency")
        if free_agency:
            if free_agency.get("plan_id"):
                totals["free_agent_plans"] += 1
            totals["free_agent_offer_operations"] += len(free_agency.get("operations") or [])
            totals["warnings"] += len(free_agency.get("warnings") or [])
            totals["errors"] += len(free_agency.get("errors") or []) + (1 if free_agency.get("status") == "error" else 0)
            totals["skipped"] += len(free_agency.get("skipped") or [])
            if free_agency.get("status") == "blocked":
                totals["blocked_results"] += 1
            if free_agency.get("status") == "error":
                totals["error_results"] += 1
    return totals


def run_offseason(
    con: sqlite3.Connection,
    *,
    phase: str,
    game_id: str,
    season: int,
    league_year: int,
    current_date: str | None = None,
    team_abbr: str | None = None,
    all_teams: bool = False,
    include_user_team: bool = False,
    max_teams: int | None = None,
    apply_mode: bool = False,
    allow_stale: bool = False,
    max_extensions_per_team: int = DEFAULT_MAX_EXTENSIONS_PER_TEAM,
    max_extension_aav: int | None = None,
    max_offers_per_team: int = DEFAULT_MAX_OFFERS_PER_TEAM,
    max_fa_aav: int | None = None,
    refresh_market: bool = False,
    market_limit: int = DEFAULT_MARKET_LIMIT,
) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError(f"Unknown offseason phase: {phase}")
    ensure_schema(con)
    teams, user_team = team_abbreviations(
        con,
        team_abbr=team_abbr,
        all_teams=all_teams,
        include_user_team=include_user_team,
        max_teams=max_teams,
    )
    result: dict[str, Any] = {
        "phase": phase,
        "mode": "apply" if apply_mode else "dry_run",
        "game_id": game_id,
        "season": season,
        "league_year": league_year,
        "current_date": current_date,
        "team_scope": {
            "all": bool(all_teams),
            "team": team_abbr.upper() if team_abbr else None,
            "include_user_team": bool(include_user_team),
            "active_user_team": user_team,
            "max_teams": max_teams,
        },
        "free_agency_period": active_free_agency_status(con, league_year),
        "teams": [],
        "committable": False,
    }

    run_contracts = phase in {"pre-free-agency", "full"}
    run_free_agents = phase in {"free-agency-wave1", "full"}
    for abbr in teams:
        team_result: dict[str, Any] = {"team": abbr}
        if run_contracts:
            contract_result = run_contract_team(
                con,
                team_abbr=abbr,
                season=season,
                game_id=game_id,
                plan_date=current_date,
                apply_mode=apply_mode,
                allow_stale=allow_stale,
                max_extensions_per_team=max_extensions_per_team,
                max_extension_aav=max_extension_aav,
            )
            team_result["contract"] = contract_result
            if contract_result.get("plan_id") or contract_result.get("operations"):
                result["committable"] = True
        if run_free_agents:
            free_agency_result = run_free_agency_team(
                con,
                team_abbr=abbr,
                season=season,
                league_year=league_year,
                game_id=game_id,
                apply_mode=apply_mode,
                allow_stale=allow_stale,
                max_offers_per_team=max_offers_per_team,
                max_fa_aav=max_fa_aav,
                refresh_market=refresh_market,
                market_limit=market_limit,
            )
            team_result["free_agency"] = free_agency_result
            if free_agency_result.get("plan_id") or free_agency_result.get("operations"):
                result["committable"] = True
        result["teams"].append(team_result)

    result["totals"] = calculate_totals(result)
    return result


def result_line(label: str, row: dict[str, Any] | None) -> str:
    if not row:
        return f"{label}: -"
    status = row.get("status") or "unknown"
    plan_id = row.get("plan_id") or "-"
    operations = len(row.get("operations") or [])
    warnings = len(row.get("warnings") or [])
    errors = len(row.get("errors") or [])
    skipped = len(row.get("skipped") or [])
    if label == "Contracts":
        planned = row.get("extension_targets", 0)
        aav = money(row.get("recommended_extension_aav"))
        noun = "extension"
    else:
        planned = row.get("offer_targets", 0)
        aav = money(row.get("recommended_offer_aav"))
        noun = "offer"
    suffix = f", {warnings} warn, {errors} err, {skipped} skip" if warnings or errors or skipped else ""
    if row.get("error"):
        suffix = f", error: {row['error']}"
    return f"{label}: plan {plan_id}, {status}, {operations}/{planned} {noun}(s), {aav} AAV{suffix}"


def print_offseason_result(
    result: dict[str, Any],
    *,
    applied: bool,
    backup: Path | None = None,
    detail_limit: int = 32,
) -> None:
    mode = "APPLY" if applied else "DRY RUN"
    totals = result.get("totals") or {}
    print(
        f"AI GM offseason-run {mode}: {result['phase']} | "
        f"season {result['season']} | league year {result['league_year']}"
    )
    if backup:
        print(f"Backup: {backup}")
    period = result.get("free_agency_period") or {}
    if result["phase"] in {"free-agency-wave1", "full"}:
        status = period.get("status") or "not started"
        print(f"Free agency period: {status}")
    print(
        "Totals: "
        f"{totals.get('teams_scanned', 0)} teams, "
        f"{totals.get('contract_plans', 0)} contract plans, "
        f"{totals.get('extension_operations', 0)} extension ops, "
        f"{totals.get('free_agent_plans', 0)} FA plans, "
        f"{totals.get('free_agent_offer_operations', 0)} FA offer ops, "
        f"{totals.get('blocked_results', 0)} blocked, "
        f"{totals.get('error_results', 0)} errors"
    )
    print()
    for team_result in (result.get("teams") or [])[: max(0, detail_limit)]:
        print(team_result["team"])
        if "contract" in team_result:
            print(f"  {result_line('Contracts', team_result.get('contract'))}")
        if "free_agency" in team_result:
            print(f"  {result_line('Free Agency', team_result.get('free_agency'))}")
    hidden = len(result.get("teams") or []) - max(0, detail_limit)
    if hidden > 0:
        print(f"... {hidden} more team(s) hidden by --detail-limit")
