#!/usr/bin/env python3
"""Advisory AI GM free-agent planner.

This module builds a review-first target board for free agency. It does not
submit offers, sign players, release players, or mutate cap/roster tables. The
output is meant to give CPU GMs a deterministic acquisition plan before offer
automation becomes routine.
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
import free_agency_processor as fa


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_SEASON = 2026

PREMIUM_GROUPS = {"QB", "WR", "OT", "IOL", "EDGE", "IDL", "CB"}
LOW_COST_GROUPS = {"K", "P", "LS", "ST", "FB"}
EVALUATOR_GROUP = {"OT": "OL", "IOL": "OL", "ST": "ST"}

FALLBACK_BASE_AAV = {
    "QB": 9_000_000,
    "RB": 2_200_000,
    "WR": 6_000_000,
    "TE": 3_400_000,
    "OT": 6_500_000,
    "IOL": 4_000_000,
    "EDGE": 7_000_000,
    "IDL": 4_800_000,
    "LB": 3_400_000,
    "CB": 5_200_000,
    "S": 3_600_000,
    "K": 1_500_000,
    "P": 1_300_000,
    "LS": 1_050_000,
    "ST": 1_300_000,
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


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def round_money(value: float, increment: int = 50_000) -> int:
    return int(round(value / increment) * increment)


def money(value: Any) -> str:
    amount = as_int(value)
    if amount < 0:
        return "-" + money(abs(amount))
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    return f"${amount}"


def row_value(row: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(row, sqlite3.Row):
        if key not in row.keys():
            return default
        value = row[key]
    else:
        value = row.get(key, default)
    return default if value is None else value


def current_date(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
    ).fetchone()
    return str(row["setting_value"]) if row else datetime.now().date().isoformat()


def plan_date_for_league_year(con: sqlite3.Connection, league_year: int) -> str:
    try:
        period = fa.current_period(con, league_year)
    except Exception:
        period = None
    if period and period["status"] == "active" and period["current_date"]:
        return str(period["current_date"])
    return current_date(con)


def current_season(con: sqlite3.Connection) -> int:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key IN ('current_season', 'current_league_year') ORDER BY setting_key LIMIT 1"
    ).fetchone()
    return as_int(row["setting_value"], DEFAULT_SEASON) if row else DEFAULT_SEASON


def current_league_year(con: sqlite3.Connection) -> int:
    try:
        return int(fa.default_league_year(con))
    except Exception:
        return current_season(con)


def get_team(con: sqlite3.Connection, team_abbr: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM teams WHERE abbreviation = ?", (team_abbr.upper(),)).fetchone()
    if not row:
        raise ValueError(f"Team not found: {team_abbr}")
    return row


def ensure_schema(con: sqlite3.Connection) -> None:
    fa.ensure_schema(con)
    con.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS ai_gm_free_agent_plans (
            plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL DEFAULT 'master',
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            league_year INTEGER NOT NULL,
            season INTEGER NOT NULL,
            plan_date TEXT NOT NULL,
            primary_count INTEGER NOT NULL DEFAULT 0,
            value_count INTEGER NOT NULL DEFAULT 0,
            bridge_count INTEGER NOT NULL DEFAULT 0,
            monitor_count INTEGER NOT NULL DEFAULT 0,
            avoid_count INTEGER NOT NULL DEFAULT 0,
            practical_budget INTEGER NOT NULL DEFAULT 0,
            recommended_offer_aav INTEGER NOT NULL DEFAULT 0,
            market_source TEXT NOT NULL DEFAULT 'unknown',
            apply_status TEXT NOT NULL DEFAULT 'pending',
            applied_at TEXT,
            apply_log_json TEXT,
            plan_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_ai_gm_free_agent_plans_team_date
            ON ai_gm_free_agent_plans(game_id, team_id, league_year, plan_date DESC);
        """
    )
    ensure_column(con, "ai_gm_free_agent_plans", "apply_status", "apply_status TEXT NOT NULL DEFAULT 'pending'")
    ensure_column(con, "ai_gm_free_agent_plans", "applied_at", "applied_at TEXT")
    ensure_column(con, "ai_gm_free_agent_plans", "apply_log_json", "apply_log_json TEXT")
    con.executescript(
        """
        DROP VIEW IF EXISTS ai_gm_free_agent_plans_view;
        CREATE VIEW ai_gm_free_agent_plans_view AS
        SELECT
            p.plan_id,
            p.game_id,
            p.league_year,
            p.season,
            p.plan_date,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name,
            p.primary_count,
            p.value_count,
            p.bridge_count,
            p.monitor_count,
            p.avoid_count,
            p.practical_budget,
            p.recommended_offer_aav,
            p.market_source,
            p.apply_status,
            p.created_at
        FROM ai_gm_free_agent_plans p
        JOIN teams t ON t.team_id = p.team_id;
        """
    )


def profile_biases(con: sqlite3.Connection, team_id: int) -> dict[str, Any]:
    if not table_exists(con, "ai_gm_profiles_view"):
        return {}
    row = con.execute("SELECT * FROM ai_gm_profiles_view WHERE team_id = ?", (team_id,)).fetchone()
    if not row:
        return {}
    return {
        "cap_tolerance": row_value(row, "cap_tolerance", ""),
        "team_build_state": row_value(row, "team_build_state", ""),
        "free_agency_policy": row_value(row, "free_agency_policy", ""),
        "free_agent_cap_policy": row_value(row, "free_agent_cap_policy", ""),
        "position_investment_policy": row_value(row, "position_investment_policy", ""),
        "risk_profile": row_value(row, "risk_profile", ""),
    }


def market_tier(score: float, group: str) -> str:
    if group == "QB":
        if score >= 80:
            return "Premium"
        if score >= 72:
            return "Starter"
        if score >= 64:
            return "Rotation"
        return "Depth"
    if group in LOW_COST_GROUPS:
        return "Starter" if score >= 70 else "Depth"
    if score >= 82:
        return "Premium"
    if score >= 74:
        return "Starter"
    if score >= 66:
        return "Rotation"
    if score >= 58:
        return "Depth"
    return "Camp"


def fallback_asking_aav(score: float, group: str, tier: str, age: int) -> int:
    base = FALLBACK_BASE_AAV.get(group, 2_000_000)
    tier_multiplier = {
        "Premium": 2.8,
        "Starter": 1.65,
        "Rotation": 0.92,
        "Depth": 0.52,
        "Camp": 0.38,
    }.get(tier, 0.65)
    score_multiplier = 1.0 + clamp((score - 68) * 0.018, -0.25, 0.45)
    age_factor = 1.0
    if group == "RB" and age >= 29:
        age_factor -= 0.16
    elif age >= 32 and group not in {"QB", "OT", "IOL", "K", "P", "LS"}:
        age_factor -= 0.18
    elif age <= 25 and tier in {"Premium", "Starter"}:
        age_factor += 0.08
    return max(840_000, round_money(base * tier_multiplier * score_multiplier * age_factor, 100_000))


def normalized_group(raw_group: Any, position: Any) -> str:
    group = str(raw_group or "").upper()
    if not group:
        group = fa.position_group_for(str(position or ""))
    if group in {"LT", "RT"}:
        return "OT"
    if group in {"LG", "RG", "C", "OG"}:
        return "IOL"
    if group in {"DE", "OLB"}:
        return "EDGE"
    if group in {"DT", "NT"}:
        return "IDL"
    if group in {"ILB", "MLB"}:
        return "LB"
    if group in {"NB"}:
        return "CB"
    if group in {"FS", "SS"}:
        return "S"
    return group or "UNK"


def fetch_market_rows(
    con: sqlite3.Connection,
    *,
    league_year: int,
    refresh_market: bool = False,
    limit: int = 120,
) -> tuple[str, list[dict[str, Any]]]:
    ensure_schema(con)
    if refresh_market:
        fa.ensure_market(con, league_year)
    rows: list[sqlite3.Row] = []
    if table_exists(con, "free_agency_board_view"):
        rows = con.execute(
            """
            SELECT *
            FROM free_agency_board_view
            WHERE league_year = ?
              AND market_status = 'available'
            ORDER BY market_heat DESC, asking_aav DESC, market_score DESC, player_name
            LIMIT ?
            """,
            (league_year, limit),
        ).fetchall()
    if rows:
        source = "free_agency_board"
    else:
        source = "free_agent_profiles_fallback"
        rows = con.execute(
            """
            SELECT
                NULL AS league_year,
                p.player_id,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position,
                p.age,
                p.years_exp,
                p.status AS player_status,
                COALESCE(fap.position_group, p.position) AS position_group,
                fap.market_tier,
                fap.asking_aav,
                fap.minimum_aav,
                COALESCE(fap.preferred_years, 1) AS preferred_years,
                COALESCE(fap.guarantee_pct, 0) AS guarantee_pct,
                COALESCE(fap.patience, 8) AS patience,
                'available' AS market_status,
                fap.previous_team,
                fap.preferred_teams,
                fap.hometown_teams,
                fap.motivation,
                fap.signing_notes,
                COALESCE(score.role_score, p.overall, 60) AS market_score,
                NULL AS pending_offers,
                NULL AS best_aav,
                10 AS money_priority,
                10 AS security_priority,
                10 AS contender_priority,
                10 AS role_priority,
                10 AS loyalty_priority,
                8 AS location_priority
            FROM players p
            LEFT JOIN free_agent_profiles fap ON fap.player_id = p.player_id
            LEFT JOIN (
                SELECT player_id, MAX(role_score) AS role_score
                FROM player_role_scores
                WHERE scheme_key = 'default'
                  AND season = (SELECT CAST(setting_value AS INTEGER) FROM game_settings WHERE setting_key = 'current_season')
                GROUP BY player_id
            ) score ON score.player_id = p.player_id
            WHERE p.team_id IS NULL
              AND p.status = 'Free Agent'
            ORDER BY COALESCE(score.role_score, p.overall, 60) DESC, player_name
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    normalized: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        group = normalized_group(item.get("position_group"), item.get("position"))
        score = as_float(item.get("market_score"), 60.0)
        age = as_int(item.get("age"), 28)
        tier = str(item.get("market_tier") or market_tier(score, group)).title()
        if tier in {"Core", "Franchise"}:
            tier = "Premium"
        asking = as_int(item.get("asking_aav"))
        minimum = as_int(item.get("minimum_aav"))
        if asking <= 0:
            asking = fallback_asking_aav(score, group, tier, age)
        if minimum <= 0:
            minimum = max(840_000, round_money(asking * 0.62, 100_000))
        item.update(
            {
                "position_group": group,
                "market_tier": tier,
                "market_score": round(score, 1),
                "asking_aav": asking,
                "minimum_aav": min(minimum, asking),
                "preferred_years": max(1, as_int(item.get("preferred_years"), 1)),
                "guarantee_pct": max(0, as_int(item.get("guarantee_pct"), 0)),
            }
        )
        normalized.append(item)
    return source, normalized


def cap_budget(projected_space: int, biases: dict[str, Any]) -> dict[str, Any]:
    tolerance = str(biases.get("cap_tolerance") or "").lower()
    reserve = 10_000_000
    if "tight cap" in tolerance:
        reserve = 14_000_000
    elif "flexible" in tolerance:
        reserve = 8_000_000
    if projected_space < reserve:
        cap_band = "critical" if projected_space >= 0 else "over_cap"
    elif projected_space < 25_000_000:
        cap_band = "tight"
    elif projected_space < 55_000_000:
        cap_band = "workable"
    else:
        cap_band = "flexible"
    return {
        "projected_cap_space": projected_space,
        "recommended_reserve": reserve,
        "practical_free_agent_budget": max(0, projected_space - reserve),
        "cap_band": cap_band,
    }


def need_maps(evaluation: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    needs = evaluation.get("roster_needs", []) or []
    by_group: dict[str, dict[str, Any]] = {}
    target_groups: list[str] = []
    for row in needs:
        group = str(row.get("position_group") or "")
        if not group:
            continue
        if group == "OL":
            target_groups.extend(["OT", "IOL"])
        else:
            target_groups.append(group)
        current = by_group.get(group)
        if current is None or as_float(row.get("need_score")) > as_float(current.get("need_score")):
            by_group[group] = row
    return by_group, list(dict.fromkeys(target_groups))


def surplus_maps(evaluation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    surplus = evaluation.get("roster_surplus", []) or []
    by_group: dict[str, dict[str, Any]] = {}
    for row in surplus:
        group = str(row.get("position_group") or "")
        if not group:
            continue
        groups = ["OT", "IOL"] if group == "OL" else [group]
        for mapped_group in groups:
            current = by_group.get(mapped_group)
            if current is None or as_float(row.get("surplus_score")) > as_float(current.get("surplus_score")):
                by_group[mapped_group] = row
    return by_group


def need_for_player(player: dict[str, Any], need_by_group: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    group = str(player.get("position_group") or "")
    eval_group = EVALUATOR_GROUP.get(group, group)
    return need_by_group.get(eval_group)


def recommended_years(player: dict[str, Any], bucket: str) -> int:
    preferred = max(1, as_int(player.get("preferred_years"), 1))
    age = as_int(player.get("age"), 28)
    group = str(player.get("position_group") or "")
    tier = str(player.get("market_tier") or "")
    if bucket in {"bridge_or_depth", "monitor"}:
        return 1 if age >= 29 or tier in {"Depth", "Camp"} else min(2, preferred)
    if group == "RB" and age >= 27:
        return min(2, preferred)
    if age >= 31 and group not in {"QB", "OT", "IOL", "K", "P", "LS"}:
        return min(2, preferred)
    if tier == "Premium" and age <= 28:
        return max(3, min(5, preferred + 1))
    if tier == "Starter":
        return max(2, min(4, preferred))
    return min(3, preferred)


def offer_shape(player: dict[str, Any], bucket: str, remaining_budget: int, cap_band: str) -> dict[str, Any]:
    asking = as_int(player.get("asking_aav"))
    minimum = as_int(player.get("minimum_aav"))
    tier = str(player.get("market_tier") or "Depth")
    group = str(player.get("position_group") or "")
    if bucket == "primary_targets":
        pct = 1.02 if tier == "Premium" else 0.98
    elif bucket == "value_targets":
        pct = 0.90 if cap_band in {"tight", "critical", "over_cap"} else 0.94
    elif bucket == "bridge_or_depth":
        pct = 0.82
    else:
        pct = 0.76
    initial = max(minimum, round_money(asking * pct, 50_000))
    max_pct = 1.12 if bucket == "primary_targets" and group in PREMIUM_GROUPS else 1.02
    if bucket in {"bridge_or_depth", "monitor"}:
        max_pct = 0.92
    max_aav = max(initial, round_money(asking * max_pct, 50_000))
    if remaining_budget > 0 and bucket in {"primary_targets", "value_targets", "bridge_or_depth"}:
        max_aav = min(max_aav, max(minimum, remaining_budget))
        initial = min(initial, max_aav)
    years = recommended_years(player, bucket)
    guarantee = max(as_int(player.get("guarantee_pct")), 15)
    if bucket == "primary_targets":
        guarantee += 14
    elif bucket == "value_targets":
        guarantee += 6
    elif bucket == "bridge_or_depth":
        guarantee = min(25, guarantee)
    guarantee = max(0, min(72, guarantee))
    bonus_pct = 0.12 if bucket == "primary_targets" else 0.07 if bucket == "value_targets" else 0.03
    signing_bonus = round_money(initial * years * bonus_pct, 50_000)
    return {
        "recommended_years": years,
        "initial_aav": initial,
        "max_aav": max_aav,
        "signing_bonus": signing_bonus,
        "guarantee_pct": guarantee,
        "total_value": initial * years,
    }


def player_fit_score(
    player: dict[str, Any],
    need: dict[str, Any] | None,
    *,
    surplus: dict[str, Any] | None,
    budget: int,
    biases: dict[str, Any],
) -> tuple[float, list[str]]:
    score = as_float(player.get("market_score"), 60.0)
    asking = as_int(player.get("asking_aav"))
    age = as_int(player.get("age"), 28)
    group = str(player.get("position_group") or "")
    tier = str(player.get("market_tier") or "")
    reasons: list[str] = []
    fit = score
    if need:
        need_score = as_float(need.get("need_score"), 0.0)
        fit += need_score * 0.42
        reasons.append(f"{need.get('position_group')} need {need.get('priority')}")
    else:
        fit -= 8
        reasons.append("not a current top need")
    if group in PREMIUM_GROUPS:
        fit += 4
        reasons.append("premium position")
    if tier == "Premium":
        fit += 6
    elif tier == "Starter":
        fit += 3
    if age <= 25 and score >= 68:
        fit += 4
        reasons.append("young market option")
    if surplus:
        surplus_score = as_float(surplus.get("surplus_score"), 0.0)
        fit -= min(22, surplus_score * 0.30)
        reasons.append(f"{surplus.get('position_group')} room surplus")
    if group == "RB" and age >= 29:
        fit -= 10
        reasons.append("older running back price risk")
    elif age >= 31 and group not in {"QB", "OT", "IOL", "K", "P", "LS"}:
        fit -= 8
        reasons.append("age curve risk")
    if asking > budget and budget > 0:
        fit -= min(22, (asking - budget) / 1_000_000 * 0.9)
        reasons.append("above current FA budget")
    elif budget > 0 and asking <= budget:
        fit += 4
        reasons.append("fits current FA budget")
    policy_text = " ".join(str(biases.get(key) or "") for key in ("free_agency_policy", "free_agent_cap_policy")).lower()
    if "one-year" in policy_text or "low-guarantee" in policy_text:
        if age >= 29 and tier not in {"Premium"}:
            fit += 2
            reasons.append("short-term policy fit")
    if "starter" in policy_text and tier in {"Premium", "Starter"} and need:
        fit += 3
    return round(clamp(fit, 0, 120), 1), list(dict.fromkeys(reason for reason in reasons if reason))


def classify_player(
    player: dict[str, Any],
    *,
    need: dict[str, Any] | None,
    surplus: dict[str, Any] | None,
    fit_score: float,
    remaining_budget: int,
    total_budget: int,
    cap_band: str,
) -> str:
    score = as_float(player.get("market_score"))
    asking = as_int(player.get("asking_aav"))
    minimum = as_int(player.get("minimum_aav"))
    age = as_int(player.get("age"), 28)
    group = str(player.get("position_group") or "")
    tier = str(player.get("market_tier") or "")
    need_score = as_float(need.get("need_score")) if need else 0.0
    need_priority = str(need.get("priority") if need else "").lower()
    surplus_score = as_float(surplus.get("surplus_score")) if surplus else 0.0
    real_need = need_priority in {"urgent", "high", "medium"} or need_score >= 42

    if cap_band in {"over_cap", "critical"} and asking > max(2_500_000, total_budget):
        return "monitor" if need_score >= 60 or score >= 78 else "avoid"
    if group in LOW_COST_GROUPS and not need:
        return "monitor" if score >= 70 else "avoid"
    if surplus_score > 0 and not real_need and group not in LOW_COST_GROUPS:
        return "monitor" if score >= 72 or tier in {"Premium", "Starter"} else "avoid"
    if surplus_score >= 20 and need_priority in {"monitor", "low", ""} and group not in LOW_COST_GROUPS:
        return "monitor" if score >= 72 or tier in {"Premium", "Starter"} else "avoid"
    if fit_score >= 88 and real_need and asking <= remaining_budget and not (group == "RB" and age >= 29):
        return "primary_targets"
    if fit_score >= 80 and real_need and asking <= remaining_budget and tier in {"Premium", "Starter"}:
        return "primary_targets"
    if fit_score >= 70 and ((need and surplus_score < 20) or tier in {"Starter", "Rotation"}) and minimum <= remaining_budget:
        return "value_targets"
    if fit_score >= 60 and minimum <= remaining_budget and ((need and surplus_score < 20) or score >= 64 or group in LOW_COST_GROUPS):
        return "bridge_or_depth"
    if need or score >= 72 or tier in {"Premium", "Starter"}:
        return "monitor"
    return "avoid"


def player_summary(
    player: dict[str, Any],
    *,
    bucket: str,
    need: dict[str, Any] | None,
    reasons: list[str],
    fit_score: float,
    offer: dict[str, Any],
) -> dict[str, Any]:
    return {
        "player_id": as_int(player.get("player_id")),
        "player_name": player.get("player_name"),
        "position": player.get("position"),
        "position_group": player.get("position_group"),
        "age": as_int(player.get("age")),
        "market_score": as_float(player.get("market_score")),
        "market_tier": player.get("market_tier"),
        "asking_aav": as_int(player.get("asking_aav")),
        "minimum_aav": as_int(player.get("minimum_aav")),
        "preferred_years": as_int(player.get("preferred_years"), 1),
        "pending_offers": as_int(player.get("pending_offers")),
        "best_aav": as_int(player.get("best_aav")),
        "need_fit": bool(need),
        "need_priority": need.get("priority") if need else None,
        "need_score": as_float(need.get("need_score")) if need else 0.0,
        "recommended_action": bucket,
        "fit_score": fit_score,
        "expected_role": (
            "starter"
            if player.get("market_tier") in {"Premium", "Starter"} or as_float(player.get("market_score")) >= 74
            else "rotation"
            if as_float(player.get("market_score")) >= 66
            else "depth"
        ),
        "offer": offer,
        "reasons": list(dict.fromkeys(reason for reason in reasons if reason)),
    }


def build_free_agent_plan(
    con: sqlite3.Connection,
    *,
    team_abbr: str,
    league_year: int | None = None,
    season: int | None = None,
    game_id: str = "master",
    plan_date: str | None = None,
    persist: bool = False,
    refresh_market: bool = False,
    market_limit: int = 120,
) -> dict[str, Any]:
    ensure_schema(con)
    league_year = league_year or current_league_year(con)
    season = season or league_year
    plan_date = plan_date or plan_date_for_league_year(con, league_year)
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
    need_by_group, target_groups = need_maps(evaluation)
    surplus_by_group = surplus_maps(evaluation)
    projected_cap = (
        contract_negotiations.projected_cap_summary(con, team_id, league_year)
        or contract_negotiations.cap_summary(con, team_id)
        or {}
    )
    budget = cap_budget(as_int(projected_cap.get("cap_space")), biases)
    remaining = as_int(budget["practical_free_agent_budget"])
    market_source, market_rows = fetch_market_rows(
        con,
        league_year=league_year,
        refresh_market=refresh_market,
        limit=market_limit,
    )

    buckets: dict[str, list[dict[str, Any]]] = {
        "primary_targets": [],
        "value_targets": [],
        "bridge_or_depth": [],
        "monitor": [],
        "avoid": [],
    }
    candidates = []
    for player in market_rows:
        need = need_for_player(player, need_by_group)
        surplus = surplus_by_group.get(str(player.get("position_group") or ""))
        fit_score, reasons = player_fit_score(
            player,
            need,
            surplus=surplus,
            budget=remaining or as_int(budget["practical_free_agent_budget"]),
            biases=biases,
        )
        candidates.append((fit_score, player, need, surplus, reasons))
    candidates.sort(key=lambda item: (item[0], as_float(item[1].get("market_score")), -as_int(item[1].get("asking_aav"))), reverse=True)

    for fit_score, player, need, surplus, reasons in candidates:
        bucket = classify_player(
            player,
            need=need,
            surplus=surplus,
            fit_score=fit_score,
            remaining_budget=remaining,
            total_budget=as_int(budget["practical_free_agent_budget"]),
            cap_band=str(budget["cap_band"]),
        )
        offer = offer_shape(player, bucket, remaining, str(budget["cap_band"]))
        if bucket in {"primary_targets", "value_targets", "bridge_or_depth"}:
            remaining = max(0, remaining - as_int(offer["initial_aav"]))
        buckets[bucket].append(
            player_summary(
                player,
                bucket=bucket,
                need=need,
                reasons=reasons,
                fit_score=fit_score,
                offer=offer,
            )
        )

    for key in buckets:
        buckets[key].sort(
            key=lambda row: (
                as_float(row.get("fit_score")),
                as_float(row.get("market_score")),
                -as_int(row.get("asking_aav")),
            ),
            reverse=True,
        )
    buckets["avoid"] = buckets["avoid"][:18]

    recommended_aav = sum(
        as_int(row["offer"]["initial_aav"])
        for key in ("primary_targets", "value_targets", "bridge_or_depth")
        for row in buckets[key]
    )
    summary = (
        f"{team['abbreviation']} free-agent plan: {len(market_rows)} market player(s) reviewed, "
        f"{len(buckets['primary_targets'])} primary target(s), "
        f"{len(buckets['value_targets'])} value target(s), "
        f"{len(buckets['bridge_or_depth'])} bridge/depth option(s), "
        f"{len(buckets['monitor'])} monitor(s)."
    )
    plan = {
        "game_id": game_id,
        "league_year": league_year,
        "season": season,
        "plan_date": plan_date,
        "advisory_only": True,
        "team": {
            "team_id": team_id,
            "abbreviation": team["abbreviation"],
            "name": f"{team['city']} {team['nickname']}",
        },
        "summary": summary,
        "market_source": market_source,
        "budget": {
            **budget,
            "recommended_offer_aav": recommended_aav,
            "remaining_free_agent_budget": remaining,
            "projected_cap_space_display": money(budget["projected_cap_space"]),
            "practical_free_agent_budget_display": money(budget["practical_free_agent_budget"]),
            "recommended_offer_aav_display": money(recommended_aav),
        },
        "team_evaluation_summary": {
            "team_phase": evaluation["team_direction"]["team_phase"],
            "cap_band": evaluation.get("metrics", {}).get("cap_band"),
            "target_groups": target_groups[:8],
            "top_needs": evaluation.get("roster_needs", [])[:6],
        },
        "gm_biases": biases,
        "plan": buckets,
        "counts": {key: len(value) for key, value in buckets.items()},
        "action_taken": "ADVISORY_ONLY: no offers, signings, contracts, roster moves, cap, or transaction tables were changed.",
    }
    if persist:
        persist_free_agent_plan(con, plan)
    return plan


def build_league_free_agent_plans(
    con: sqlite3.Connection,
    *,
    league_year: int | None = None,
    season: int | None = None,
    game_id: str = "master",
    persist: bool = False,
    refresh_market: bool = False,
    market_limit: int = 120,
) -> list[dict[str, Any]]:
    return [
        build_free_agent_plan(
            con,
            team_abbr=row["abbreviation"],
            league_year=league_year,
            season=season,
            game_id=game_id,
            persist=persist,
            refresh_market=refresh_market,
            market_limit=market_limit,
        )
        for row in con.execute("SELECT abbreviation FROM teams ORDER BY abbreviation").fetchall()
    ]


def persist_free_agent_plan(con: sqlite3.Connection, plan: dict[str, Any]) -> int:
    ensure_schema(con)
    cur = con.execute(
        """
        INSERT INTO ai_gm_free_agent_plans (
            game_id, team_id, league_year, season, plan_date,
            primary_count, value_count, bridge_count, monitor_count, avoid_count,
            practical_budget, recommended_offer_aav, market_source, plan_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan["game_id"],
            as_int(plan["team"]["team_id"]),
            as_int(plan["league_year"]),
            as_int(plan["season"]),
            plan["plan_date"],
            plan["counts"]["primary_targets"],
            plan["counts"]["value_targets"],
            plan["counts"]["bridge_or_depth"],
            plan["counts"]["monitor"],
            plan["counts"]["avoid"],
            as_int(plan["budget"]["practical_free_agent_budget"]),
            as_int(plan["budget"]["recommended_offer_aav"]),
            plan["market_source"],
            json_dumps(plan),
        ),
    )
    plan_id = as_int(cur.lastrowid)
    plan["plan_id"] = plan_id
    return plan_id


def list_free_agent_plans(
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
        FROM ai_gm_free_agent_plans_view
        {clause}
        ORDER BY created_at DESC, plan_id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def load_free_agent_plan(con: sqlite3.Connection, plan_id: int) -> tuple[sqlite3.Row, dict[str, Any]]:
    ensure_schema(con)
    row = con.execute(
        "SELECT * FROM ai_gm_free_agent_plans WHERE plan_id = ?",
        (plan_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Free-agent plan not found: {plan_id}")
    plan = json.loads(row["plan_json"])
    plan["plan_id"] = plan_id
    return row, plan


def target_offer_items(plan: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for bucket in ("primary_targets", "value_targets", "bridge_or_depth"):
        for row in plan.get("plan", {}).get(bucket, []):
            item = dict(row)
            item["bucket"] = bucket
            items.append(item)
    return items


def target_id_set(plan: dict[str, Any]) -> set[int]:
    return {as_int(item.get("player_id")) for item in target_offer_items(plan) if item.get("player_id") is not None}


def current_plan_drift(saved_plan: dict[str, Any], current_plan: dict[str, Any]) -> dict[str, Any]:
    saved_ids = target_id_set(saved_plan)
    current_ids = target_id_set(current_plan)
    missing = sorted(saved_ids - current_ids)
    added = sorted(current_ids - saved_ids)
    return {
        "stale": bool(missing or added),
        "target_ids": {
            "missing_from_current": missing,
            "new_in_current": added,
        },
    }


def player_market_state(con: sqlite3.Connection, league_year: int, player_id: int) -> dict[str, Any] | None:
    if table_exists(con, "free_agency_board_view"):
        row = con.execute(
            """
            SELECT *
            FROM free_agency_board_view
            WHERE league_year = ?
              AND player_id = ?
            """,
            (league_year, player_id),
        ).fetchone()
        if row:
            return dict(row)
    row = con.execute(
        """
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.team_id,
            p.status AS player_status,
            m.status AS market_status,
            m.asking_aav,
            m.minimum_aav,
            m.preferred_years,
            m.guarantee_pct,
            COALESCE(offers.pending_offers, 0) AS pending_offers,
            offers.best_aav
        FROM players p
        LEFT JOIN free_agency_player_markets m
          ON m.player_id = p.player_id
         AND m.league_year = ?
        LEFT JOIN (
            SELECT
                league_year,
                player_id,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_offers,
                MAX(CASE WHEN status = 'pending' THEN aav ELSE NULL END) AS best_aav
            FROM free_agency_offers
            GROUP BY league_year, player_id
        ) offers
          ON offers.league_year = ?
         AND offers.player_id = p.player_id
        WHERE p.player_id = ?
        """,
        (league_year, league_year, player_id),
    ).fetchone()
    return dict(row) if row else None


def duplicate_pending_offer(con: sqlite3.Connection, *, league_year: int, team_id: int, player_id: int) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM free_agency_offers
        WHERE league_year = ?
          AND team_id = ?
          AND player_id = ?
          AND status = 'pending'
        LIMIT 1
        """,
        (league_year, team_id, player_id),
    ).fetchone()
    return row is not None


def current_team_cap_space(con: sqlite3.Connection, team_id: int, league_year: int) -> int:
    summary = contract_negotiations.cap_summary(con, team_id)
    if summary and summary.get("cap_space") is not None:
        return as_int(summary["cap_space"])
    projected = contract_negotiations.projected_cap_summary(con, team_id, league_year)
    if projected and projected.get("cap_space") is not None:
        return as_int(projected["cap_space"])
    row = con.execute("SELECT salary_cap FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    return as_int(row["salary_cap"]) if row else 0


def validate_saved_plan_for_apply(
    con: sqlite3.Connection,
    *,
    saved_plan: dict[str, Any],
    current_plan: dict[str, Any] | None,
    period: sqlite3.Row | None,
    allow_stale: bool,
    max_offers: int,
    max_total_aav: int | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    errors: list[str] = []
    warnings: list[str] = []
    skipped: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    if not saved_plan.get("advisory_only", False):
        errors.append("Plan is not marked advisory_only.")
    if period is None:
        errors.append("Free agency is not active for this league year. Run free-agency start before submitting offers.")
    else:
        period_date = str(period["current_date"])
        if str(saved_plan.get("plan_date")) != period_date:
            message = f"Plan date {saved_plan.get('plan_date')} differs from active free-agency date {period_date}."
            if allow_stale:
                warnings.append(message)
            else:
                errors.append(message + " Re-run with --allow-stale after review.")
    if current_plan:
        drift = current_plan_drift(saved_plan, current_plan)
        if drift["stale"]:
            message = "Saved target board differs from the current generated free-agent plan."
            if allow_stale:
                warnings.append(message)
            else:
                errors.append(message + " Re-run with --allow-stale after review.")
    else:
        drift = {"stale": False, "target_ids": {"missing_from_current": [], "new_in_current": []}}

    team_id = as_int(saved_plan.get("team", {}).get("team_id"))
    league_year = as_int(saved_plan.get("league_year"))
    reserve = as_int(saved_plan.get("budget", {}).get("recommended_reserve"), 8_000_000)
    practical_budget = as_int(saved_plan.get("budget", {}).get("practical_free_agent_budget"))
    current_cap = current_team_cap_space(con, team_id, league_year)
    total_budget = max_total_aav if max_total_aav is not None else min(practical_budget, max(0, current_cap - reserve))
    if total_budget < 0:
        total_budget = 0

    total_aav = 0
    accepted_count = 0
    for item in target_offer_items(saved_plan):
        player_id = as_int(item.get("player_id"))
        offer = dict(item.get("offer") or {})
        aav = as_int(offer.get("initial_aav"))
        if accepted_count >= max_offers:
            skipped.append({"player_id": player_id, "player_name": item.get("player_name"), "reason": "max offer count reached"})
            continue
        if aav <= 0:
            errors.append(f"{item.get('player_name')} has no positive planned AAV.")
            continue
        if total_aav + aav > total_budget:
            skipped.append({"player_id": player_id, "player_name": item.get("player_name"), "reason": "max total AAV budget reached"})
            continue

        market = player_market_state(con, league_year, player_id)
        if not market:
            errors.append(f"{item.get('player_name')} no longer exists in the player table.")
            continue
        if market.get("team_id") is not None or market.get("player_status") != "Free Agent":
            errors.append(f"{item.get('player_name')} is no longer a free agent.")
            continue
        market_status = market.get("market_status")
        if market_status and market_status != "available":
            errors.append(f"{item.get('player_name')} is not available in the free-agent market ({market_status}).")
            continue
        market_minimum = as_int(market.get("minimum_aav"))
        if market_minimum and aav < market_minimum:
            errors.append(f"{item.get('player_name')} planned AAV {money(aav)} is below current market minimum {money(market_minimum)}.")
            continue
        if duplicate_pending_offer(con, league_year=league_year, team_id=team_id, player_id=player_id):
            errors.append(f"{item.get('player_name')} already has a pending offer from this team.")
            continue
        best_aav = as_int(market.get("best_aav"))
        max_aav = as_int(offer.get("max_aav"), aav)
        if best_aav > max_aav:
            skipped.append({"player_id": player_id, "player_name": item.get("player_name"), "reason": f"market already above max AAV ({money(best_aav)} > {money(max_aav)})"})
            continue

        accepted_count += 1
        total_aav += aav
        operations.append(
            {
                "action": "submit_free_agent_offer",
                "player_id": player_id,
                "player_name": item.get("player_name"),
                "position": item.get("position"),
                "bucket": item.get("bucket"),
                "years": max(1, as_int(offer.get("recommended_years"), 1)),
                "aav": aav,
                "max_aav": max_aav,
                "signing_bonus": max(0, as_int(offer.get("signing_bonus"))),
                "guarantee_pct": max(0, min(100, as_int(offer.get("guarantee_pct")))),
                "expected_role": item.get("expected_role"),
                "reasons": item.get("reasons") or [],
            }
        )

    if not operations and not errors:
        warnings.append("No offer operations survived plan limits and market checks.")
    return (
        {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "skipped": skipped,
            "drift": drift,
            "plan_date": saved_plan.get("plan_date"),
            "period_date": str(period["current_date"]) if period else None,
            "current_cap_space": current_cap,
            "recommended_reserve": reserve,
            "max_total_aav": total_budget,
            "planned_offer_aav": total_aav,
            "max_offers": max_offers,
        },
        operations,
    )


def apply_free_agent_plan(
    con: sqlite3.Connection,
    *,
    plan_id: int,
    allow_stale: bool = False,
    max_offers: int = 4,
    max_total_aav: int | None = None,
) -> dict[str, Any]:
    row, saved_plan = load_free_agent_plan(con, plan_id)
    if row["apply_status"] == "applied":
        raise ValueError(f"Free-agent plan {plan_id} has already been applied.")
    league_year = as_int(saved_plan["league_year"])
    team_abbr = saved_plan["team"]["abbreviation"]
    period = None
    try:
        period = fa.active_period(con, league_year)
    except Exception:
        period = None
    current = None
    try:
        current = build_free_agent_plan(
            con,
            team_abbr=team_abbr,
            league_year=league_year,
            season=as_int(saved_plan.get("season"), league_year),
            game_id=saved_plan.get("game_id") or "master",
            plan_date=plan_date_for_league_year(con, league_year),
            persist=False,
        )
    except Exception:
        current = None
    preflight, operations = validate_saved_plan_for_apply(
        con,
        saved_plan=saved_plan,
        current_plan=current,
        period=period,
        allow_stale=allow_stale,
        max_offers=max(1, max_offers),
        max_total_aav=max_total_aav,
    )
    if not preflight["valid"]:
        return {
            "plan_id": plan_id,
            "team": team_abbr,
            "league_year": league_year,
            "applied": False,
            "preflight": preflight,
            "operations": [],
        }

    team_id = as_int(saved_plan["team"]["team_id"])
    offer_date, offer_hour = fa.event_time(period) if period else (current_date(con), None)
    submitted: list[dict[str, Any]] = []
    for operation in operations:
        offer_id = fa.submit_offer(
            con,
            league_year=league_year,
            team_id=team_id,
            player_id=as_int(operation["player_id"]),
            years=as_int(operation["years"], 1),
            aav=as_int(operation["aav"]),
            signing_bonus=as_int(operation["signing_bonus"]),
            guarantee_pct=as_int(operation["guarantee_pct"]),
            submitted_date=offer_date,
            submitted_hour=offer_hour,
            notes=(
                f"AI GM reviewed free-agent plan {plan_id}: {operation['bucket']} "
                f"({operation.get('expected_role') or 'role TBD'})."
            ),
        )
        fa.log_event(
            con,
            league_year=league_year,
            event_date=offer_date,
            event_hour=offer_hour,
            event_type="ai_gm_offer_submitted",
            team_id=team_id,
            player_id=as_int(operation["player_id"]),
            offer_id=offer_id,
            message=(
                f"{team_abbr} submitted an AI GM reviewed offer for {operation['player_name']} "
                f"at {money(operation['aav'])} AAV from plan {plan_id}."
            ),
        )
        submitted.append({"offer_id": offer_id, **operation})

    apply_log = {
        "plan_id": plan_id,
        "team": team_abbr,
        "league_year": league_year,
        "preflight": preflight,
        "operations": submitted,
    }
    con.execute(
        """
        UPDATE ai_gm_free_agent_plans
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
        "league_year": league_year,
        "applied": True,
        "preflight": preflight,
        "operations": submitted,
    }


def print_apply_result(result: dict[str, Any], *, applied: bool, backup: Path | None = None) -> None:
    mode = "APPLIED" if applied and result.get("applied") else "DRY RUN" if result.get("applied") else "BLOCKED"
    print(f"AI GM free-agent plan {result['plan_id']} {mode}")
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
        f"Offer AAV: {money(preflight['planned_offer_aav'])} / {money(preflight['max_total_aav'])}; "
        f"cap {money(preflight['current_cap_space'])}; reserve {money(preflight['recommended_reserve'])}."
    )
    print(f"Operations: {len(result['operations'])}")
    for op in result["operations"][:20]:
        suffix = f" offer_id={op['offer_id']}" if op.get("offer_id") else ""
        print(f"  - {op['action']}: {op.get('player_name')} {op.get('position')} {money(op.get('aav'))} AAV{suffix}")


def print_plan_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No persisted AI GM free-agent plans found.")
        return
    print(f"{'ID':>4} {'TEAM':<4} {'YEAR':>4} {'DATE':<10} {'PRI':>3} {'VAL':>3} {'BR':>3} {'MON':>3} {'AAV':>8} {'APPLY':<8}")
    for row in rows:
        print(
            f"{row['plan_id']:>4} {row['team']:<4} {row['league_year']:>4} {row['plan_date']:<10} "
            f"{row['primary_count']:>3} {row['value_count']:>3} {row['bridge_count']:>3} "
            f"{row['monitor_count']:>3} {money(row['recommended_offer_aav']):>8} {row.get('apply_status') or 'pending':<8}"
        )


def print_free_agent_plan(plan: dict[str, Any], *, detail_limit: int = 8) -> None:
    print(f"{plan['team']['abbreviation']}: free-agent plan")
    print(f"  {plan['summary']}")
    print(
        f"  Budget {plan['budget']['practical_free_agent_budget_display']}; "
        f"recommended offer AAV {plan['budget']['recommended_offer_aav_display']}; "
        f"source {plan['market_source']}."
    )
    labels = [
        ("primary_targets", "Primary"),
        ("value_targets", "Value"),
        ("bridge_or_depth", "Bridge/Depth"),
        ("monitor", "Monitor"),
        ("avoid", "Avoid"),
    ]
    for key, label in labels:
        rows = plan["plan"].get(key, [])[:detail_limit]
        if rows:
            print(
                f"  {label}: "
                + ", ".join(
                    f"{row['player_name']} {row['position']} "
                    f"{money(row['offer']['initial_aav'] if key != 'avoid' else row['asking_aav'])}"
                    for row in rows
                )
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Advisory AI GM free-agent planner.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--league-year", type=int)
    parser.add_argument("--season", type=int)
    parser.add_argument("--game-id", default="master")
    parser.add_argument("--team")
    parser.add_argument("--all", action="store_true", help="Plan for all teams.")
    parser.add_argument("--list-plans", action="store_true")
    parser.add_argument("--apply-plan-id", type=int, help="Dry-run/apply one persisted free-agent plan.")
    parser.add_argument("--apply", action="store_true", help="Commit pending offer creation for --apply-plan-id.")
    parser.add_argument("--allow-stale", action="store_true")
    parser.add_argument("--max-offers", type=int, default=4)
    parser.add_argument("--max-total-aav", type=int)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--market-limit", type=int, default=120)
    parser.add_argument("--refresh-market", action="store_true", help="Refresh free_agency_player_markets before planning.")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--detail-limit", type=int, default=8)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    con = connect(args.db)
    try:
        if args.apply_plan_id:
            result = apply_free_agent_plan(
                con,
                plan_id=args.apply_plan_id,
                allow_stale=args.allow_stale,
                max_offers=args.max_offers,
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
            rows = list_free_agent_plans(con, team_abbr=args.team, game_id=args.game_id, limit=args.limit)
            if args.json:
                print(json_dumps(rows))
            else:
                print_plan_rows(rows)
            return 0
        if not args.team and not args.all:
            raise SystemExit("Provide --team TEAM, --all, or --list-plans.")
        if args.all:
            plans = build_league_free_agent_plans(
                con,
                league_year=args.league_year,
                season=args.season,
                game_id=args.game_id,
                persist=args.persist,
                refresh_market=args.refresh_market,
                market_limit=args.market_limit,
            )
            if args.json:
                print(json_dumps(plans))
            else:
                for plan in plans:
                    print_free_agent_plan(plan, detail_limit=args.detail_limit)
            if args.persist:
                con.commit()
            return 0
        plan = build_free_agent_plan(
            con,
            team_abbr=args.team,
            league_year=args.league_year,
            season=args.season,
            game_id=args.game_id,
            persist=args.persist,
            refresh_market=args.refresh_market,
            market_limit=args.market_limit,
        )
        if args.json:
            print(json_dumps(plan))
        else:
            print_free_agent_plan(plan, detail_limit=args.detail_limit)
        if args.persist:
            con.commit()
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
