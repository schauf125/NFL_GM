#!/usr/bin/env python3
"""Advisory AI GM contract/extension planner.

This module creates a review-first contract board for expiring players. It does
not sign, tag, release, trade, or expire anyone. The output is meant to guide CPU
GM behavior before free agency and replace blind re-sign randomness over time.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import ai_gm_team_evaluator as team_eval
import contract_negotiations
import pro_player_fog


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_SEASON = 2026

PREMIUM_GROUPS = {"QB", "WR", "OT", "IOL", "EDGE", "IDL", "CB"}
TAG_ESTIMATES = {
    "QB": 41_000_000,
    "RB": 11_000_000,
    "WR": 26_500_000,
    "TE": 13_000_000,
    "OT": 23_000_000,
    "IOL": 21_000_000,
    "EDGE": 24_000_000,
    "IDL": 23_000_000,
    "LB": 22_000_000,
    "CB": 20_000_000,
    "S": 18_000_000,
    "ST": 5_000_000,
}


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


def column_exists(con: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return any(row["name"] == column_name for row in con.execute(f'PRAGMA table_info("{table_name}")'))


def ensure_column(con: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
    if not table_exists(con, table_name):
        return
    if not column_exists(con, table_name, column_name):
        con.execute(f'ALTER TABLE "{table_name}" ADD COLUMN {column_sql}')


def json_dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
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


def current_season(con: sqlite3.Connection) -> int:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key IN ('current_season', 'current_league_year') ORDER BY setting_key LIMIT 1"
    ).fetchone()
    return as_int(row["setting_value"], DEFAULT_SEASON) if row else DEFAULT_SEASON


def current_date(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
    ).fetchone()
    if row and row["setting_value"]:
        return str(row["setting_value"])
    if table_exists(con, "active_game_save_view"):
        row = con.execute('SELECT "current_date" FROM active_game_save_view LIMIT 1').fetchone()
        if row and row["current_date"]:
            return str(row["current_date"])
    return f"{current_season(con)}-06-01"


def get_team(con: sqlite3.Connection, team_abbr: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM teams WHERE abbreviation = ?", (team_abbr.upper(),)).fetchone()
    if not row:
        raise ValueError(f"Team not found: {team_abbr}")
    return row


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS ai_gm_contract_plans (
            plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL DEFAULT 'master',
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            plan_date TEXT NOT NULL,
            extension_count INTEGER NOT NULL DEFAULT 0,
            tag_count INTEGER NOT NULL DEFAULT 0,
            trade_count INTEGER NOT NULL DEFAULT 0,
            walk_count INTEGER NOT NULL DEFAULT 0,
            defer_count INTEGER NOT NULL DEFAULT 0,
            projected_cap_space INTEGER NOT NULL DEFAULT 0,
            recommended_extension_aav INTEGER NOT NULL DEFAULT 0,
            apply_status TEXT NOT NULL DEFAULT 'pending',
            applied_at TEXT,
            apply_log_json TEXT,
            plan_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_ai_gm_contract_plans_team_date
            ON ai_gm_contract_plans(game_id, team_id, season, plan_date DESC);
        """
    )
    ensure_column(con, "ai_gm_contract_plans", "apply_status", "apply_status TEXT NOT NULL DEFAULT 'pending'")
    ensure_column(con, "ai_gm_contract_plans", "applied_at", "applied_at TEXT")
    ensure_column(con, "ai_gm_contract_plans", "apply_log_json", "apply_log_json TEXT")
    con.executescript(
        """
        DROP VIEW IF EXISTS ai_gm_contract_plans_view;
        CREATE VIEW ai_gm_contract_plans_view AS
        SELECT
            p.plan_id,
            p.game_id,
            p.season,
            p.plan_date,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name,
            p.extension_count,
            p.tag_count,
            p.trade_count,
            p.walk_count,
            p.defer_count,
            p.projected_cap_space,
            p.recommended_extension_aav,
            p.apply_status,
            p.created_at
        FROM ai_gm_contract_plans p
        JOIN teams t ON t.team_id = p.team_id;
        """
    )


def profile_biases(con: sqlite3.Connection, team_id: int) -> dict[str, Any]:
    if not table_exists(con, "ai_gm_profiles_view"):
        return {"cap_tolerance": "", "negotiation_style": "", "team_build_state": ""}
    row = con.execute("SELECT * FROM ai_gm_profiles_view WHERE team_id = ?", (team_id,)).fetchone()
    if not row:
        return {"cap_tolerance": "", "negotiation_style": "", "team_build_state": ""}
    return {
        "cap_tolerance": row["cap_tolerance"] or "",
        "negotiation_style": row["negotiation_style"] or "",
        "team_build_state": row["team_build_state"] or "",
        "contract_policy": row["contract_policy"] or "",
    }


def candidate_lookup(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {as_int(row.get("player_id")): row for row in rows}


def tag_estimate(position_group: str) -> int:
    return TAG_ESTIMATES.get(position_group, 14_000_000)


def tag_age_fit(group: str, age: int, score: float, potential: float) -> bool:
    if group == "QB":
        return age <= 34 and score >= 84
    if group == "RB":
        return age <= 27 and score >= 86
    if group in {"WR", "TE", "EDGE", "CB", "S", "LB"}:
        return age <= 30 and (score >= 84 or potential >= 88)
    if group in {"OT", "IOL", "IDL"}:
        return age <= 31 and (score >= 84 or potential >= 88)
    return False


def cap_budget(projected_space: int, biases: dict[str, Any]) -> dict[str, int]:
    tolerance = str(biases.get("cap_tolerance") or "").lower()
    reserve = 18_000_000
    if "tight cap" in tolerance:
        reserve = 22_000_000
    elif "flexible" in tolerance:
        reserve = 14_000_000
    spendable = max(0, projected_space - reserve)
    return {
        "projected_cap_space": projected_space,
        "recommended_reserve": reserve,
        "extension_aav_budget": spendable,
    }


def core_score(player: dict[str, Any], evaluator_row: dict[str, Any] | None) -> float:
    score = as_float(player.get("market_score"))
    priority = str(player.get("priority") or "")
    group = str(player.get("position_group") or "")
    age = as_int(player.get("age"), 27)
    potential = as_float(player.get("potential"), score)
    role_score = as_float(player.get("best_role_score") or player.get("role_score"), score)
    value = score
    if evaluator_row:
        value += as_float(evaluator_row.get("score")) * 0.18
    if priority == "Priority":
        value += 5
    if group in PREMIUM_GROUPS:
        value += 4
    if group == "QB" and score >= 74:
        value += 6
    if group == "QB" and age <= 30 and potential >= 86:
        value += 7
    elif group == "QB" and potential >= 90:
        value += 5
    if group in PREMIUM_GROUPS and age <= 27 and potential >= 86:
        value += 4
    if group in PREMIUM_GROUPS and age <= 29 and potential >= 90:
        value += 3
    if role_score >= score + 4 and score >= 68:
        value += 3
    if group == "ST":
        value -= 8
    if group == "RB" and age >= 28:
        value -= 8
    elif age >= 31 and group not in {"QB", "OT", "IOL", "ST"}:
        value -= 7
    if age >= 30 and role_score <= score - 6 and group not in {"QB", "ST"}:
        value -= 3
    return value


def classify_player(
    player: dict[str, Any],
    *,
    evaluator_extension: dict[str, Any] | None,
    evaluator_trade: dict[str, Any] | None,
    evaluator_pressure: dict[str, Any] | None,
    budget_remaining: int,
    cap_band: str,
) -> tuple[str, list[str], int]:
    group = str(player.get("position_group") or "")
    score = as_float(player.get("market_score"))
    age = as_int(player.get("age"), 27)
    potential = as_float(player.get("potential"), score)
    ask = as_int(player.get("asking_aav"))
    priority = str(player.get("priority") or "")
    value = core_score(player, evaluator_extension)
    reasons: list[str] = []

    if evaluator_extension:
        reasons.extend(str(reason) for reason in evaluator_extension.get("reasons", [])[:2])
    if group in PREMIUM_GROUPS:
        reasons.append("premium position")
    if priority == "Priority":
        reasons.append("club priority estimate")
    if cap_band in {"over_cap", "critical", "tight"}:
        reasons.append(f"cap {cap_band}")

    if value >= 88 and ask <= max(1_000_000, budget_remaining) and not (group == "RB" and age >= 29):
        return "extension_targets", reasons or ["core retention"], ask
    if value >= 82 and ask <= max(1_000_000, budget_remaining) * 0.75:
        return "extension_targets", reasons or ["value retention"], ask
    if group == "QB" and score >= 76 and ask <= max(1_000_000, budget_remaining + 12_000_000):
        return "extension_targets", reasons or ["quarterback continuity"], ask
    young_qb_core = group == "QB" and age <= 34 and (score >= 74 or (score >= 68 and potential >= 84) or potential >= 88)
    young_premium_core = group in PREMIUM_GROUPS and age <= 29 and (score >= 76 or (score >= 70 and potential >= 86) or potential >= 90)
    if young_qb_core and ask <= max(1_000_000, budget_remaining + 16_000_000):
        reasons.append("quarterback continuity")
        return "extension_targets", reasons or ["quarterback continuity"], ask
    if young_premium_core and ask <= max(1_000_000, budget_remaining + 8_000_000):
        reasons.append("young premium-position core")
        return "extension_targets", reasons or ["young core retention"], ask

    tag_cost = tag_estimate(group)
    tag_fit = tag_age_fit(group, age, score, potential)
    young_elite = age <= 29 and group in {"WR", "OT", "EDGE", "CB", "IDL", "QB"} and (score >= 84 or potential >= 88)
    if (
        tag_fit
        and group in PREMIUM_GROUPS
        and (value >= 90 or young_elite)
        and tag_cost <= max(1_000_000, budget_remaining + 8_000_000)
    ):
        reasons.append("tag protects leverage")
        return "tag_candidates", reasons, tag_cost

    if evaluator_trade or (evaluator_pressure and score >= 70 and age >= 28):
        if evaluator_trade:
            reasons.extend(str(reason) for reason in evaluator_trade.get("reasons", [])[:2])
        reasons.append("recover value before free agency")
        return "trade_before_walk", reasons, 0

    if score < 68 or (age >= 30 and group not in {"QB", "OT", "IOL", "ST"} and priority != "Priority"):
        reasons.append("replacement-level or age/value mismatch")
        return "let_walk", reasons, 0

    if ask > budget_remaining and cap_band in {"over_cap", "critical", "tight"}:
        reasons.append("price exceeds current cap plan")
        return "defer", reasons, 0

    reasons.append("needs negotiation/market check")
    return "defer", reasons, 0


def player_summary(player: dict[str, Any], action: str, reasons: list[str], estimated_aav: int) -> dict[str, Any]:
    return {
        "player_id": as_int(player.get("player_id")),
        "player_name": player.get("player_name"),
        "position": player.get("position"),
        "position_group": player.get("position_group"),
        "age": as_int(player.get("age")),
        "status": player.get("status"),
        "market_score": as_float(player.get("market_score")),
        "market_tier": player.get("market_tier"),
        "priority": player.get("priority"),
        "current_aav": as_int(player.get("aav")),
        "asking_aav": as_int(player.get("asking_aav")),
        "minimum_aav": as_int(player.get("minimum_aav")),
        "suggested_years": as_int(player.get("suggested_years"), 1),
        "guarantee_pct": as_int(player.get("guarantee_pct")),
        "recommended_action": action,
        "estimated_aav": estimated_aav,
        "estimated_total_value": estimated_aav * as_int(player.get("suggested_years"), 1),
        "reasons": list(dict.fromkeys(reason for reason in reasons if reason)),
    }


def build_contract_plan(
    con: sqlite3.Connection,
    *,
    team_abbr: str,
    season: int | None = None,
    game_id: str = "master",
    plan_date: str | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    ensure_schema(con)
    season = season or current_season(con)
    plan_date = plan_date or current_date(con)
    team = get_team(con, team_abbr)
    team_id = as_int(team["team_id"])
    biases = profile_biases(con, team_id)
    evaluation = team_eval.evaluate_team(
        con,
        team_abbr=team_abbr,
        season=season,
        game_id=game_id,
        evaluation_date=plan_date,
        persist=False,
    )
    expiring = contract_negotiations.expiring_players(con, team_id, season)
    projected_cap = contract_negotiations.projected_cap_summary(con, team_id, season + 1) or {}
    projected_space = as_int(projected_cap.get("cap_space"))
    budget = cap_budget(projected_space, biases)
    remaining = budget["extension_aav_budget"]
    cap_band = str(evaluation.get("metrics", {}).get("cap_band") or "unknown")
    extension_lookup = candidate_lookup(evaluation.get("extension_candidates", []))
    trade_lookup = candidate_lookup(evaluation.get("trade_block_candidates", []))
    pressure_lookup = candidate_lookup(evaluation.get("contract_pressure", []))
    staff_reads, created_staff_reads = pro_player_fog.evaluations_for_team(
        con,
        game_id=game_id,
        season=season,
        evaluator_team_id=team_id,
        player_ids=[as_int(player.get("player_id")) for player in expiring],
        create_missing=True,
    )
    if created_staff_reads:
        con.commit()

    buckets: dict[str, list[dict[str, Any]]] = {
        "extension_targets": [],
        "tag_candidates": [],
        "trade_before_walk": [],
        "let_walk": [],
        "defer": [],
    }
    for player in expiring:
        player_id = as_int(player.get("player_id"))
        read = staff_reads.get(player_id)
        if read:
            true_score = as_float(player.get("overall"), as_float(player.get("market_score"), 60.0))
            perceived_score = as_float(read.get("overall"), true_score)
            perceived_potential = as_float(read.get("potential"), perceived_score)
            player["true_overall"] = true_score
            player["true_potential"] = player.get("potential")
            player["overall"] = perceived_score
            player["potential"] = perceived_potential
            player["evaluation_confidence"] = read.get("confidenceLabel") or read.get("confidence")
            player["market_score"] = round(
                max(45.0, as_float(player.get("market_score"), true_score) + (perceived_score - true_score) * 0.75),
                1,
            )
        bucket, reasons, estimated_aav = classify_player(
            player,
            evaluator_extension=extension_lookup.get(player_id),
            evaluator_trade=trade_lookup.get(player_id),
            evaluator_pressure=pressure_lookup.get(player_id),
            budget_remaining=remaining,
            cap_band=cap_band,
        )
        if bucket == "extension_targets":
            remaining = max(0, remaining - estimated_aav)
        buckets[bucket].append(player_summary(player, bucket, reasons, estimated_aav))

    for key in buckets:
        buckets[key].sort(
            key=lambda row: (
                as_int(row.get("estimated_aav")),
                as_float(row.get("market_score")),
                as_int(row.get("asking_aav")),
            ),
            reverse=True,
        )

    recommended_aav = sum(as_int(row["estimated_aav"]) for row in buckets["extension_targets"])
    summary = (
        f"{team['abbreviation']} contract plan: {len(expiring)} expiring player(s), "
        f"{len(buckets['extension_targets'])} extension target(s), "
        f"{len(buckets['tag_candidates'])} tag candidate(s), "
        f"{len(buckets['trade_before_walk'])} trade-before-walk candidate(s), "
        f"{len(buckets['let_walk'])} let-walk recommendation(s)."
    )
    plan = {
        "game_id": game_id,
        "season": season,
        "plan_date": plan_date,
        "advisory_only": True,
        "team": {
            "team_id": team_id,
            "abbreviation": team["abbreviation"],
            "name": f"{team['city']} {team['nickname']}",
        },
        "summary": summary,
        "budget": {
            **budget,
            "recommended_extension_aav": recommended_aav,
            "remaining_extension_aav_budget": remaining,
            "projected_cap_space_display": money(projected_space),
            "recommended_extension_aav_display": money(recommended_aav),
        },
        "team_evaluation_summary": {
            "team_phase": evaluation["team_direction"]["team_phase"],
            "cap_band": cap_band,
            "top_needs": evaluation.get("roster_needs", [])[:5],
            "contract_pressure": evaluation.get("contract_pressure", [])[:5],
        },
        "gm_biases": biases,
        "plan": buckets,
        "counts": {key: len(value) for key, value in buckets.items()},
        "action_taken": "ADVISORY_ONLY: no contract, roster, cap, or transaction tables were changed.",
    }
    if persist:
        persist_contract_plan(con, plan)
    return plan


def build_league_contract_plans(
    con: sqlite3.Connection,
    *,
    season: int | None = None,
    game_id: str = "master",
    persist: bool = False,
) -> list[dict[str, Any]]:
    return [
        build_contract_plan(
            con,
            team_abbr=row["abbreviation"],
            season=season,
            game_id=game_id,
            persist=persist,
        )
        for row in con.execute("SELECT abbreviation FROM teams ORDER BY abbreviation").fetchall()
    ]


def persist_contract_plan(con: sqlite3.Connection, plan: dict[str, Any]) -> int:
    ensure_schema(con)
    cur = con.execute(
        """
        INSERT INTO ai_gm_contract_plans (
            game_id, team_id, season, plan_date, extension_count, tag_count,
            trade_count, walk_count, defer_count, projected_cap_space,
            recommended_extension_aav, plan_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan["game_id"],
            as_int(plan["team"]["team_id"]),
            as_int(plan["season"]),
            plan["plan_date"],
            plan["counts"]["extension_targets"],
            plan["counts"]["tag_candidates"],
            plan["counts"]["trade_before_walk"],
            plan["counts"]["let_walk"],
            plan["counts"]["defer"],
            as_int(plan["budget"]["projected_cap_space"]),
            as_int(plan["budget"]["recommended_extension_aav"]),
            json_dumps(plan),
        ),
    )
    plan_id = as_int(cur.lastrowid)
    plan["plan_id"] = plan_id
    return plan_id


def list_contract_plans(
    con: sqlite3.Connection,
    *,
    team_abbr: str | None = None,
    game_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    ensure_schema(con)
    params: list[Any] = []
    where: list[str] = []
    if team_abbr:
        where.append("team = ?")
        params.append(team_abbr.upper())
    if game_id:
        where.append("game_id = ?")
        params.append(game_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = con.execute(
        f"""
        SELECT *
        FROM ai_gm_contract_plans_view
        {clause}
        ORDER BY created_at DESC, plan_id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def load_contract_plan(con: sqlite3.Connection, plan_id: int) -> tuple[sqlite3.Row, dict[str, Any]]:
    ensure_schema(con)
    row = con.execute(
        "SELECT * FROM ai_gm_contract_plans WHERE plan_id = ?",
        (plan_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Contract plan not found: {plan_id}")
    plan = json.loads(row["plan_json"])
    plan["plan_id"] = plan_id
    return row, plan


def extension_items(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in plan.get("plan", {}).get("extension_targets", [])]


def extension_id_set(plan: dict[str, Any]) -> set[int]:
    return {as_int(item.get("player_id")) for item in extension_items(plan) if item.get("player_id") is not None}


def current_plan_drift(saved_plan: dict[str, Any], current_plan: dict[str, Any]) -> dict[str, Any]:
    saved_ids = extension_id_set(saved_plan)
    current_ids = extension_id_set(current_plan)
    missing = sorted(saved_ids - current_ids)
    added = sorted(current_ids - saved_ids)
    return {
        "stale": bool(missing or added),
        "extension_ids": {
            "missing_from_current": missing,
            "new_in_current": added,
        },
    }


def player_contract_state(con: sqlite3.Connection, player_id: int, season: int) -> dict[str, Any] | None:
    row = con.execute(
        """
        SELECT
            p.player_id,
            p.team_id,
            p.status,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            c.contract_id,
            c.start_year,
            c.end_year,
            c.aav,
            future.contract_id AS future_contract_id,
            future.start_year AS future_start_year
        FROM players p
        LEFT JOIN contracts c
          ON c.player_id = p.player_id
         AND c.is_active = 1
         AND COALESCE(c.start_year, ?) <= ?
         AND c.end_year >= ?
        LEFT JOIN contracts future
          ON future.player_id = p.player_id
         AND future.is_active = 1
         AND COALESCE(future.start_year, ?) > ?
        WHERE p.player_id = ?
        ORDER BY c.end_year DESC, future.start_year
        LIMIT 1
        """,
        (season, season, season, season, season, player_id),
    ).fetchone()
    return dict(row) if row else None


def validate_saved_plan_for_apply(
    con: sqlite3.Connection,
    *,
    saved_plan: dict[str, Any],
    current_plan: dict[str, Any] | None,
    allow_stale: bool,
    max_extensions: int,
    max_total_aav: int | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    errors: list[str] = []
    warnings: list[str] = []
    skipped: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    if not saved_plan.get("advisory_only", False):
        errors.append("Plan is not marked advisory_only.")

    current_day = current_date(con)
    if str(saved_plan.get("plan_date")) != current_day:
        message = f"Plan date {saved_plan.get('plan_date')} differs from current game date {current_day}."
        if allow_stale:
            warnings.append(message)
        else:
            errors.append(message + " Re-run with --allow-stale after review.")

    if current_plan:
        drift = current_plan_drift(saved_plan, current_plan)
        if drift["stale"]:
            message = "Saved extension board differs from the current generated contract plan."
            if allow_stale:
                warnings.append(message)
            else:
                errors.append(message + " Re-run with --allow-stale after review.")
    else:
        drift = {"stale": False, "extension_ids": {"missing_from_current": [], "new_in_current": []}}

    team_id = as_int(saved_plan.get("team", {}).get("team_id"))
    season = as_int(saved_plan.get("season"))
    projected = contract_negotiations.projected_cap_summary(con, team_id, season + 1) or {}
    projected_space = as_int(projected.get("cap_space"))
    reserve = as_int(saved_plan.get("budget", {}).get("recommended_reserve"), 18_000_000)
    plan_budget = as_int(saved_plan.get("budget", {}).get("extension_aav_budget"))
    total_budget = max_total_aav if max_total_aav is not None else min(plan_budget, max(0, projected_space - reserve))
    total_budget = max(0, total_budget)

    total_aav = 0
    accepted_count = 0
    for item in extension_items(saved_plan):
        player_id = as_int(item.get("player_id"))
        aav = as_int(item.get("estimated_aav") or item.get("asking_aav"))
        years = max(1, as_int(item.get("suggested_years"), 1))
        if accepted_count >= max_extensions:
            skipped.append({"player_id": player_id, "player_name": item.get("player_name"), "reason": "max extension count reached"})
            continue
        if aav <= 0:
            errors.append(f"{item.get('player_name')} has no positive planned AAV.")
            continue
        if total_aav + aav > total_budget:
            skipped.append({"player_id": player_id, "player_name": item.get("player_name"), "reason": "max total AAV budget reached"})
            continue
        state = player_contract_state(con, player_id, season)
        if not state:
            errors.append(f"{item.get('player_name')} no longer exists in the player table.")
            continue
        if as_int(state.get("team_id")) != team_id:
            errors.append(f"{item.get('player_name')} is no longer on {saved_plan['team']['abbreviation']}.")
            continue
        if state.get("status") not in {"Active", "Practice Squad", "Injured Reserve", "PUP", "NFI", "Suspended"}:
            errors.append(f"{item.get('player_name')} has unsupported roster status for extension: {state.get('status')}.")
            continue
        if state.get("future_contract_id"):
            errors.append(f"{item.get('player_name')} already has a future active contract.")
            continue
        if as_int(state.get("end_year"), 9999) > season:
            errors.append(f"{item.get('player_name')} is not expiring in {season}.")
            continue
        minimum = as_int(item.get("minimum_aav"))
        if minimum and aav < minimum:
            errors.append(f"{item.get('player_name')} planned AAV {money(aav)} is below minimum {money(minimum)}.")
            continue

        accepted_count += 1
        total_aav += aav
        operations.append(
            {
                "action": "extend_player",
                "player_id": player_id,
                "player_name": item.get("player_name"),
                "position": item.get("position"),
                "years": years,
                "aav": aav,
                "signing_bonus": 0,
                "guarantee_pct": as_int(item.get("guarantee_pct")),
                "reasons": item.get("reasons") or [],
            }
        )

    if not operations and not errors:
        warnings.append("No extension operations survived plan limits and cap checks.")
    return (
        {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "skipped": skipped,
            "drift": drift,
            "plan_date": saved_plan.get("plan_date"),
            "current_date": current_day,
            "projected_cap_space": projected_space,
            "recommended_reserve": reserve,
            "max_total_aav": total_budget,
            "planned_extension_aav": total_aav,
            "max_extensions": max_extensions,
        },
        operations,
    )


def apply_contract_plan(
    con: sqlite3.Connection,
    *,
    plan_id: int,
    allow_stale: bool = False,
    max_extensions: int = 4,
    max_total_aav: int | None = None,
) -> dict[str, Any]:
    row, saved_plan = load_contract_plan(con, plan_id)
    if row["apply_status"] == "applied":
        raise ValueError(f"Contract plan {plan_id} has already been applied.")
    season = as_int(saved_plan["season"])
    team_abbr = saved_plan["team"]["abbreviation"]
    current = None
    try:
        current = build_contract_plan(
            con,
            team_abbr=team_abbr,
            season=season,
            game_id=saved_plan.get("game_id") or "master",
            persist=False,
        )
    except Exception:
        current = None
    preflight, operations = validate_saved_plan_for_apply(
        con,
        saved_plan=saved_plan,
        current_plan=current,
        allow_stale=allow_stale,
        max_extensions=max(1, max_extensions),
        max_total_aav=max_total_aav,
    )
    if not preflight["valid"]:
        return {
            "plan_id": plan_id,
            "team": team_abbr,
            "season": season,
            "applied": False,
            "preflight": preflight,
            "operations": [],
        }

    submitted: list[dict[str, Any]] = []
    for operation in operations:
        contract_id = contract_negotiations.extend_player(
            con,
            team=team_abbr,
            season=season,
            player_id=as_int(operation["player_id"]),
            years=as_int(operation["years"], 1),
            aav=as_int(operation["aav"]),
            signing_bonus=as_int(operation["signing_bonus"]),
            apply=True,
            force=False,
            quiet=True,
            rebuild_all_contracts=False,
            sync_cap=True,
            write_cap_snapshot=False,
        )
        submitted.append({"contract_id": contract_id, **operation})

    apply_log = {
        "plan_id": plan_id,
        "team": team_abbr,
        "season": season,
        "preflight": preflight,
        "operations": submitted,
    }
    con.execute(
        """
        UPDATE ai_gm_contract_plans
        SET apply_status = ?,
            applied_at = datetime('now'),
            apply_log_json = ?
        WHERE plan_id = ?
        """,
        ("applied", json_dumps(apply_log), plan_id),
    )
    return {
        "plan_id": plan_id,
        "team": team_abbr,
        "season": season,
        "applied": True,
        "preflight": preflight,
        "operations": submitted,
    }


def print_apply_result(result: dict[str, Any], *, applied: bool, backup: Path | None = None) -> None:
    mode = "APPLIED" if applied and result.get("applied") else "DRY RUN" if result.get("applied") else "BLOCKED"
    print(f"AI GM contract plan {result['plan_id']} {mode}")
    if backup:
        print(f"Backup: {backup}")
    preflight = result["preflight"]
    if preflight["errors"]:
        print("Preflight errors:")
        for error in preflight["errors"]:
            print(f"  - {error}")
    if preflight["warnings"]:
        print("Preflight warnings:")
        for warning in preflight["warnings"]:
            print(f"  - {warning}")
    if preflight["skipped"]:
        print("Skipped:")
        for skipped in preflight["skipped"][:12]:
            print(f"  - {skipped.get('player_name')}: {skipped.get('reason')}")
    print(
        f"Extension AAV: {money(preflight['planned_extension_aav'])} / {money(preflight['max_total_aav'])}; "
        f"projected cap {money(preflight['projected_cap_space'])}; reserve {money(preflight['recommended_reserve'])}."
    )
    print(f"Operations: {len(result['operations'])}")
    for op in result["operations"][:20]:
        suffix = f" contract_id={op['contract_id']}" if op.get("contract_id") else ""
        print(f"  - {op['action']}: {op.get('player_name')} {op.get('position')} {op.get('years')} yr {money(op.get('aav'))} AAV{suffix}")


def print_plan_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No persisted AI GM contract plans found.")
        return
    print(f"{'ID':>4} {'TEAM':<4} {'SEASON':>6} {'DATE':<10} {'EXT':>3} {'TAG':>3} {'TRADE':>5} {'WALK':>4} {'AAV':>8} {'APPLY':<8}")
    for row in rows:
        print(
            f"{row['plan_id']:>4} {row['team']:<4} {row['season']:>6} {row['plan_date']:<10} "
            f"{row['extension_count']:>3} {row['tag_count']:>3} {row['trade_count']:>5} "
            f"{row['walk_count']:>4} {money(row['recommended_extension_aav']):>8} {row.get('apply_status') or 'pending':<8}"
        )


def print_contract_plan(plan: dict[str, Any], *, detail_limit: int = 8) -> None:
    print(f"{plan['team']['abbreviation']}: contract plan")
    print(f"  {plan['summary']}")
    print(
        f"  Projected cap {plan['budget']['projected_cap_space_display']}; "
        f"recommended extension AAV {plan['budget']['recommended_extension_aav_display']}."
    )
    buckets = plan["plan"]
    labels = [
        ("extension_targets", "Extend"),
        ("tag_candidates", "Tag"),
        ("trade_before_walk", "Trade Before Walk"),
        ("let_walk", "Let Walk"),
        ("defer", "Defer"),
    ]
    for key, label in labels:
        rows = buckets.get(key, [])[:detail_limit]
        if rows:
            print(
                f"  {label}: "
                + ", ".join(
                    f"{row['player_name']} {row['position']} {money(row['estimated_aav'] or row['asking_aav'])}"
                    for row in rows
                )
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Advisory AI GM contract/extension planner.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--season", type=int)
    parser.add_argument("--game-id", default="master")
    parser.add_argument("--team")
    parser.add_argument("--all", action="store_true", help="Plan for all teams.")
    parser.add_argument("--list-plans", action="store_true")
    parser.add_argument("--apply-plan-id", type=int, help="Dry-run/apply one persisted contract plan.")
    parser.add_argument("--apply", action="store_true", help="Commit extensions for --apply-plan-id.")
    parser.add_argument("--allow-stale", action="store_true")
    parser.add_argument("--max-extensions", type=int, default=4)
    parser.add_argument("--max-total-aav", type=int)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--detail-limit", type=int, default=8)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    con = connect(args.db)
    try:
        if args.apply_plan_id:
            result = apply_contract_plan(
                con,
                plan_id=args.apply_plan_id,
                allow_stale=args.allow_stale,
                max_extensions=args.max_extensions,
                max_total_aav=args.max_total_aav,
            )
            if args.apply and result.get("applied"):
                con.commit()
            else:
                con.rollback()
            if args.json:
                print(json_dumps(result))
            else:
                print_apply_result(result, applied=args.apply)
            return 0
        if args.list_plans:
            rows = list_contract_plans(con, team_abbr=args.team, game_id=args.game_id, limit=args.limit)
            if args.json:
                print(json_dumps(rows))
            else:
                print_plan_rows(rows)
            return 0
        if not args.team and not args.all:
            raise SystemExit("Provide --team TEAM, --all, or --list-plans.")
        if args.all:
            plans = build_league_contract_plans(
                con,
                season=args.season,
                game_id=args.game_id,
                persist=args.persist,
            )
            if args.json:
                print(json_dumps(plans))
            else:
                for plan in plans:
                    print_contract_plan(plan, detail_limit=args.detail_limit)
            if args.persist:
                con.commit()
            return 0
        plan = build_contract_plan(
            con,
            team_abbr=args.team,
            season=args.season,
            game_id=args.game_id,
            persist=args.persist,
        )
        if args.json:
            print(json_dumps(plan))
        else:
            print_contract_plan(plan, detail_limit=args.detail_limit)
        if args.persist:
            con.commit()
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
