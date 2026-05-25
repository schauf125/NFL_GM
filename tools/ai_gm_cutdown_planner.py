#!/usr/bin/env python3
"""Advisory AI GM cutdown and practice-squad planner.

This planner does not mutate rosters. It uses the deterministic roster-cutdown
selector as the baseline, then applies the AI GM team evaluator's needs,
surplus, cut-watch, practice-squad, extension, and trade-block signals to
produce a 53-man active roster recommendation and 16-player practice squad
priority list.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import ai_gm_team_evaluator as team_eval
import roster_cutdown
import roster_rules


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_SEASON = 2026
PHASE = "Regular Season"


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


def backup_sqlite(source: Path, label: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = source.with_name(f"{source.stem}.pre_{label}_{timestamp}{source.suffix}")
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return destination


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


def current_season(con: sqlite3.Connection) -> int:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key IN ('current_league_year', 'current_season') ORDER BY setting_key LIMIT 1"
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
    row = con.execute(
        "SELECT * FROM teams WHERE abbreviation = ?",
        (team_abbr.upper(),),
    ).fetchone()
    if not row:
        raise ValueError(f"Team not found: {team_abbr}")
    return row


def rule_limits(
    con: sqlite3.Connection,
    season: int,
    *,
    active_limit: int | None = None,
    practice_squad_limit: int | None = None,
) -> dict[str, int]:
    resolved_active = active_limit or 53
    resolved_ps = practice_squad_limit or 16
    try:
        rule_set = roster_rules.get_rule_set(con, season, PHASE)
        resolved_active = active_limit or as_int(rule_set["active_roster_limit"], 53)
        resolved_ps = practice_squad_limit or as_int(rule_set["practice_squad_limit"], 16)
    except Exception:
        pass
    return {"active_roster_limit": resolved_active, "practice_squad_limit": resolved_ps}


def ensure_schema(con: sqlite3.Connection) -> None:
    for column_name, column_sql in [
        ("apply_status", "apply_status TEXT NOT NULL DEFAULT 'pending'"),
        ("applied_at", "applied_at TEXT"),
        ("apply_log_json", "apply_log_json TEXT"),
    ]:
        ensure_column(con, "ai_gm_cutdown_plans", column_name, column_sql)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS ai_gm_cutdown_plans (
            plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL DEFAULT 'master',
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            plan_date TEXT NOT NULL,
            active_limit INTEGER NOT NULL,
            practice_squad_limit INTEGER NOT NULL,
            active_count INTEGER NOT NULL,
            practice_squad_count INTEGER NOT NULL,
            release_count INTEGER NOT NULL,
            validation_status TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            apply_status TEXT NOT NULL DEFAULT 'pending',
            applied_at TEXT,
            apply_log_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(validation_status IN ('valid', 'warning', 'invalid'))
        );

        CREATE INDEX IF NOT EXISTS idx_ai_gm_cutdown_plans_team_date
            ON ai_gm_cutdown_plans(game_id, team_id, season, plan_date DESC);

        DROP VIEW IF EXISTS ai_gm_cutdown_plans_view;
        CREATE VIEW ai_gm_cutdown_plans_view AS
        SELECT
            p.plan_id,
            p.game_id,
            p.season,
            p.plan_date,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name,
            p.active_limit,
            p.practice_squad_limit,
            p.active_count,
            p.practice_squad_count,
            p.release_count,
            p.validation_status,
            p.apply_status,
            p.applied_at,
            p.created_at
        FROM ai_gm_cutdown_plans p
        JOIN teams t ON t.team_id = p.team_id;
        """
    )


def profile_biases(con: sqlite3.Connection, team_id: int) -> dict[str, float | str | None]:
    if not table_exists(con, "ai_gm_profiles_view"):
        return {
            "youth_bias": 1.0,
            "cap_bias": 1.0,
            "trade_bias": 1.0,
            "team_build_state": None,
            "gm_name": None,
        }
    row = con.execute(
        "SELECT * FROM ai_gm_profiles_view WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    if not row:
        return {
            "youth_bias": 1.0,
            "cap_bias": 1.0,
            "trade_bias": 1.0,
            "team_build_state": None,
            "gm_name": None,
        }
    patience = str(row["patience_with_young_players"] or "").lower()
    cap = str(row["cap_tolerance"] or "").lower()
    trade = str(row["trade_aggression"] or "").lower()
    return {
        "youth_bias": 1.18 if "high patience" in patience else 0.95 if "low" in patience else 1.05,
        "cap_bias": 1.25 if "tight cap" in cap else 0.9 if "flexible" in cap else 1.0,
        "trade_bias": 1.18 if "aggressive" in trade or "opportunistic" in trade else 1.0,
        "team_build_state": row["team_build_state"],
        "gm_name": row["gm_name"],
    }


def keyed_scores(rows: list[dict[str, Any]]) -> dict[int, float]:
    return {as_int(row.get("player_id")): as_float(row.get("score")) for row in rows}


def annotate_candidate(
    candidate: roster_cutdown.PlayerCandidate,
    *,
    action: str,
    adjusted_keep_score: float,
    adjusted_ps_score: float,
    reasons: list[str],
    waiver_claim_risk: str | None = None,
) -> dict[str, Any]:
    value = {
        "player_id": candidate.player_id,
        "player_name": candidate.name,
        "position": candidate.position,
        "position_group": candidate.group,
        "age": candidate.age,
        "years_exp": candidate.years_exp,
        "is_rookie": candidate.is_rookie,
        "is_international_pathway": candidate.is_international_pathway,
        "overall": round(candidate.overall, 1),
        "potential": round(candidate.potential, 1),
        "role_score": round(candidate.role_score, 1),
        "depth_rank": candidate.depth_rank,
        "original_keep_score": round(candidate.keep_score, 1),
        "adjusted_keep_score": round(adjusted_keep_score, 1),
        "original_ps_score": round(candidate.ps_score, 1),
        "adjusted_ps_score": round(adjusted_ps_score, 1),
        "recommended_action": action,
        "reasons": reasons,
    }
    bucket, bucket_reason = roster_rules.practice_squad_bucket(
        {
            "years_exp": candidate.years_exp,
            "is_rookie": candidate.is_rookie,
            "is_international_pathway": candidate.is_international_pathway,
        }
    )
    value["practice_squad_bucket"] = bucket
    value["practice_squad_eligibility_reason"] = bucket_reason
    if waiver_claim_risk:
        value["waiver_claim_risk"] = waiver_claim_risk
    return value


def waiver_risk(candidate: roster_cutdown.PlayerCandidate) -> str:
    if candidate.position in roster_cutdown.SPECIALIST_POSITIONS:
        return "low"
    if candidate.age >= 27 and candidate.potential < 80:
        return "low"
    if candidate.age >= 30:
        return "low"
    if candidate.potential >= 78 or (candidate.age <= 24 and candidate.overall >= 65):
        return "high"
    if candidate.potential >= 72 or candidate.overall >= 68:
        return "medium"
    return "low"


def waiver_risk_from_row(row: dict[str, Any]) -> str:
    if str(row.get("position") or "") in roster_cutdown.SPECIALIST_POSITIONS:
        return "low"
    age = as_int(row.get("age"))
    potential = as_float(row.get("potential"))
    overall = as_float(row.get("overall"))
    if age >= 27 and potential < 80:
        return "low"
    if age >= 30:
        return "low"
    if potential >= 78 or (age <= 24 and overall >= 65):
        return "high"
    if potential >= 72 or overall >= 68:
        return "medium"
    return "low"


def adjust_candidates(
    candidates: list[roster_cutdown.PlayerCandidate],
    evaluation: dict[str, Any],
    biases: dict[str, float | str | None],
) -> tuple[list[roster_cutdown.PlayerCandidate], dict[int, list[str]]]:
    needs = {row["position_group"]: row for row in evaluation.get("roster_needs", [])}
    surplus = {row["position_group"]: row for row in evaluation.get("roster_surplus", [])}
    cut_scores = keyed_scores(evaluation.get("cut_candidates", []))
    ps_scores = keyed_scores(evaluation.get("practice_squad_priorities", []))
    extension_scores = keyed_scores(evaluation.get("extension_candidates", []))
    trade_scores = keyed_scores(evaluation.get("trade_block_candidates", []))
    contract_scores = keyed_scores(evaluation.get("contract_pressure", []))
    youth_bias = as_float(biases.get("youth_bias"), 1.0)
    cap_bias = as_float(biases.get("cap_bias"), 1.0)
    trade_bias = as_float(biases.get("trade_bias"), 1.0)

    adjusted: list[roster_cutdown.PlayerCandidate] = []
    reasons_by_id: dict[int, list[str]] = {}
    for candidate in candidates:
        keep = float(candidate.keep_score)
        ps = float(candidate.ps_score)
        reasons: list[str] = []
        need = needs.get(candidate.group)
        if need:
            need_score = as_float(need.get("need_score"))
            if need_score >= 35:
                keep += min(10.0, need_score * 0.18)
                reasons.append("protect thin need room")
            elif need_score >= 22:
                keep += min(5.0, need_score * 0.10)
                reasons.append("position room needs depth")
        room_surplus = surplus.get(candidate.group)
        if room_surplus:
            surplus_score = as_float(room_surplus.get("surplus_score"))
            keep -= min(14.0, surplus_score * 0.13)
            reasons.append("surplus room")

        if candidate.player_id in cut_scores:
            keep -= min(18.0, cut_scores[candidate.player_id] * 0.28)
            reasons.append("evaluator cut watch")
        if candidate.player_id in ps_scores:
            ps += min(24.0, ps_scores[candidate.player_id] * 0.36)
            keep += min(5.0, ps_scores[candidate.player_id] * 0.08)
            reasons.append("practice squad priority")
        if candidate.player_id in extension_scores:
            keep += min(22.0, extension_scores[candidate.player_id] * 0.24)
            ps -= 12.0
            reasons.append("extension/core watch")
        if candidate.player_id in trade_scores:
            keep -= min(8.0, trade_scores[candidate.player_id] * 0.08 * trade_bias)
            reasons.append("trade market candidate")
        if candidate.player_id in contract_scores and candidate.age >= 29:
            keep -= min(7.0, contract_scores[candidate.player_id] * 0.07 * cap_bias)
            reasons.append("age/cap pressure")

        upside = max(0.0, candidate.potential - candidate.overall)
        if candidate.age <= 24 or candidate.is_rookie:
            keep += min(8.0, (2.0 + upside * 0.25) * youth_bias)
            ps += min(10.0, (4.0 + upside * 0.35) * youth_bias)
            reasons.append("youth/upside")
        elif candidate.age >= 31 and candidate.position not in roster_cutdown.SPECIALIST_POSITIONS:
            keep -= min(7.0, (candidate.age - 30) * 1.5)
            ps -= 10.0
            reasons.append("older fringe player")

        if candidate.depth_rank == 1:
            keep += 8.0
            reasons.append("current starter/depth chart leader")
        elif candidate.depth_rank == 2:
            keep += 3.0
            reasons.append("top backup")

        if candidate.position in roster_cutdown.SPECIALIST_POSITIONS:
            ps -= 30.0
            if candidate.depth_rank == 1 or candidate.overall >= 68:
                keep += 8.0
                reasons.append("specialist slot")

        adjusted.append(
            replace(
                candidate,
                keep_score=keep,
                ps_score=ps,
            )
        )
        reasons_by_id[candidate.player_id] = reasons or ["deterministic roster score"]

    return adjusted, reasons_by_id


def compact_free_agent_candidate(candidate: roster_cutdown.PlayerCandidate) -> dict[str, Any]:
    value = {
        "player_id": candidate.player_id,
        "player_name": candidate.name,
        "position": candidate.position,
        "position_group": candidate.group,
        "age": candidate.age,
        "years_exp": candidate.years_exp,
        "is_rookie": candidate.is_rookie,
        "is_international_pathway": candidate.is_international_pathway,
        "overall": round(candidate.overall, 1),
        "potential": round(candidate.potential, 1),
        "ps_score": round(candidate.ps_score, 1),
        "recommended_action": "free_agent_practice_squad_option",
        "reasons": ["fills open practice squad slot"],
    }
    bucket, bucket_reason = roster_rules.practice_squad_bucket(
        {
            "years_exp": candidate.years_exp,
            "is_rookie": candidate.is_rookie,
            "is_international_pathway": candidate.is_international_pathway,
        }
    )
    value["practice_squad_bucket"] = bucket
    value["practice_squad_eligibility_reason"] = bucket_reason
    return value


def plausible_free_agent_practice_squad_candidate(candidate: roster_cutdown.PlayerCandidate) -> bool:
    if candidate.position in roster_cutdown.SPECIALIST_POSITIONS:
        return False
    if candidate.overall >= 68:
        return False
    if candidate.age >= 28 and candidate.overall >= 62:
        return False
    if candidate.age >= 30 and candidate.potential >= 70:
        return False
    return True


def compact_active_specialist_candidate(candidate: roster_cutdown.PlayerCandidate) -> dict[str, Any]:
    return {
        "player_id": candidate.player_id,
        "player_name": candidate.name,
        "position": candidate.position,
        "position_group": candidate.group,
        "age": candidate.age,
        "is_rookie": candidate.is_rookie,
        "overall": round(candidate.overall, 1),
        "potential": round(candidate.potential, 1),
        "role_score": round(candidate.role_score, 1),
        "depth_rank": None,
        "original_keep_score": round(candidate.keep_score, 1),
        "adjusted_keep_score": round(candidate.keep_score + 30.0, 1),
        "original_ps_score": round(candidate.ps_score, 1),
        "adjusted_ps_score": round(candidate.ps_score, 1),
        "recommended_action": "sign_active_specialist",
        "reasons": [f"required {candidate.position} coverage missing from controlled roster"],
        "source": "free_agent",
    }


def free_agent_specialist_options(
    con: sqlite3.Connection,
    *,
    season: int,
    missing_groups: list[str],
) -> list[dict[str, Any]]:
    positions = [group for group in missing_groups if group in roster_cutdown.SPECIALIST_POSITIONS]
    if not positions:
        return []
    options: list[dict[str, Any]] = []
    for position in positions:
        row = con.execute(
            """
            SELECT p.*
            FROM players p
            WHERE p.team_id IS NULL
              AND COALESCE(p.status, 'Free Agent') = 'Free Agent'
              AND p.position = ?
            ORDER BY COALESCE(p.overall, 50) DESC, COALESCE(p.potential, p.overall, 50) DESC, age ASC
            LIMIT 1
            """,
            (position,),
        ).fetchone()
        if not row:
            continue
        candidate = roster_cutdown.player_candidate(con, row, season=season, team_id=None)
        options.append(compact_active_specialist_candidate(candidate))
    return options


def integrate_required_active_specialists(
    active_rows: list[dict[str, Any]],
    release_rows: list[dict[str, Any]],
    free_agent_active_rows: list[dict[str, Any]],
    active_limit: int,
) -> None:
    """Make missing K/P/LS repairs fit inside the 53-man advisory plan."""
    for option in free_agent_active_rows:
        group = str(option.get("position_group") or option.get("position") or "")
        if not group:
            continue
        if any(str(row.get("position_group")) == group for row in active_rows):
            continue
        if len(active_rows) >= active_limit:
            removable = [
                row for row in active_rows
                if str(row.get("position")) not in roster_cutdown.SPECIALIST_POSITIONS
                and as_int(row.get("depth_rank"), 99) != 1
            ]
            if not removable:
                removable = [
                    row for row in active_rows
                    if str(row.get("position")) not in roster_cutdown.SPECIALIST_POSITIONS
                ]
            if removable:
                drop = min(
                    removable,
                    key=lambda row: (
                        as_float(row.get("adjusted_keep_score"), 100.0),
                        as_float(row.get("potential"), 100.0),
                    ),
                )
                active_rows.remove(drop)
                drop = dict(drop)
                drop["recommended_action"] = "release_or_waive_for_specialist"
                drop["waiver_claim_risk"] = drop.get("waiver_claim_risk") or waiver_risk_from_row(drop)
                drop["reasons"] = list(drop.get("reasons") or []) + [f"creates active slot for required {group} signing"]
                release_rows.append(drop)
        if len(active_rows) < active_limit:
            active_rows.append(option)


def position_group_counts(plan_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in plan_rows:
        group = str(row.get("position_group") or "OTHER")
        counts[group] = counts.get(group, 0) + 1
    return dict(sorted(counts.items()))


def validate_plan(
    *,
    active: list[dict[str, Any]],
    practice_squad: list[dict[str, Any]],
    free_agent_practice_squad_options: list[dict[str, Any]],
    releases: list[dict[str, Any]],
    free_agent_active_options: list[dict[str, Any]],
    active_limit: int,
    practice_squad_limit: int,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    active_ids = {row["player_id"] for row in active}
    ps_ids = {
        row["player_id"]
        for row in [*practice_squad, *free_agent_practice_squad_options]
    }
    release_ids = {row["player_id"] for row in releases}
    all_ids = [row["player_id"] for row in active + practice_squad + free_agent_practice_squad_options + releases]
    total_ps_count = len(practice_squad) + len(free_agent_practice_squad_options)
    if len(all_ids) != len(set(all_ids)):
        errors.append("A player appears in more than one cutdown bucket.")
    if len(active) != active_limit:
        errors.append(f"Active roster recommendation has {len(active)} players, expected {active_limit}.")
    if total_ps_count > practice_squad_limit:
        errors.append(
            f"Practice squad recommendation has {total_ps_count} players, limit is {practice_squad_limit}."
        )
    elif total_ps_count < practice_squad_limit:
        warnings.append(f"Practice squad recommendation has {total_ps_count}/{practice_squad_limit} players.")
    active_counts = position_group_counts(active)
    fa_active_groups = {
        str(row.get("position_group") or row.get("position") or "OTHER")
        for row in free_agent_active_options
    }
    for group, target in roster_cutdown.DEFAULT_ACTIVE_TARGETS.items():
        count = active_counts.get(group, 0)
        if group in {"QB", "K", "P", "LS"} and count < min(target, 1):
            if group in {"K", "P", "LS"} and group in fa_active_groups:
                warnings.append(f"Active roster needs a free-agent {group} signing before finalizing.")
            else:
                errors.append(f"Active roster is missing required {group} coverage.")
        elif count < max(1, target - 1):
            warnings.append(f"{group} active count is light: {count} versus target {target}.")
    for row in practice_squad:
        if row["position"] in roster_cutdown.SPECIALIST_POSITIONS:
            warnings.append(f"{row['player_name']} is a specialist listed for practice squad.")
    if free_agent_active_options:
        warnings.append(f"{len(free_agent_active_options)} active free-agent specialist signing required before finalizing.")
    risky_cuts = [
        row for row in releases
        if row.get("waiver_claim_risk") in {"high", "medium"}
    ]
    if risky_cuts:
        warnings.append(f"{len(risky_cuts)} release candidates carry medium/high waiver risk.")
    status = "invalid" if errors else "warning" if warnings else "valid"
    return {
        "status": status,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "active": len(active),
            "practice_squad": total_ps_count,
            "own_practice_squad": len(practice_squad),
            "free_agent_practice_squad": len(free_agent_practice_squad_options),
            "release": len(releases),
            "active_position_groups": active_counts,
            "practice_squad_position_groups": position_group_counts(
                [*practice_squad, *free_agent_practice_squad_options]
            ),
        },
        "id_sets": {
            "active": sorted(active_ids),
            "practice_squad": sorted(ps_ids),
            "release": sorted(release_ids),
        },
    }


def compare_to_deterministic(
    original_candidates: list[roster_cutdown.PlayerCandidate],
    *,
    ai_active_ids: set[int],
    ai_ps_ids: set[int],
    active_limit: int,
    practice_squad_limit: int,
) -> dict[str, Any]:
    by_id = {candidate.player_id: candidate for candidate in original_candidates}
    deterministic_active = roster_cutdown.choose_active_roster(original_candidates, active_limit)
    deterministic_ps = roster_cutdown.choose_practice_squad(
        original_candidates,
        deterministic_active,
        practice_squad_limit,
    )

    def player_list(ids: set[int]) -> list[dict[str, Any]]:
        rows = []
        for player_id in sorted(ids, key=lambda pid: by_id[pid].name if pid in by_id else str(pid)):
            candidate = by_id.get(player_id)
            if not candidate:
                continue
            rows.append(
                {
                    "player_id": candidate.player_id,
                    "player_name": candidate.name,
                    "position": candidate.position,
                    "position_group": candidate.group,
                    "overall": round(candidate.overall, 1),
                    "potential": round(candidate.potential, 1),
                }
            )
        return rows

    return {
        "deterministic_active_count": len(deterministic_active),
        "deterministic_practice_squad_count": len(deterministic_ps),
        "ai_active_over_fallback": player_list(ai_active_ids - deterministic_active),
        "fallback_active_over_ai": player_list(deterministic_active - ai_active_ids),
        "ai_ps_over_fallback": player_list(ai_ps_ids - deterministic_ps),
        "fallback_ps_over_ai": player_list(deterministic_ps - ai_ps_ids),
    }


def build_cutdown_plan(
    con: sqlite3.Connection,
    *,
    team_abbr: str,
    season: int | None = None,
    game_id: str = "master",
    plan_date: str | None = None,
    active_limit: int | None = None,
    practice_squad_limit: int | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    season = season or current_season(con)
    plan_date = plan_date or current_date(con)
    team = get_team(con, team_abbr)
    limits = rule_limits(
        con,
        season,
        active_limit=active_limit,
        practice_squad_limit=practice_squad_limit,
    )
    active_limit = limits["active_roster_limit"]
    practice_squad_limit = limits["practice_squad_limit"]
    evaluation = team_eval.evaluate_team(
        con,
        team_abbr=team["abbreviation"],
        season=season,
        game_id=game_id,
        evaluation_date=plan_date,
        persist=False,
    )
    biases = profile_biases(con, as_int(team["team_id"]))
    original_candidates = roster_cutdown.active_candidates(con, as_int(team["team_id"]), season)
    adjusted_candidates, reasons_by_id = adjust_candidates(original_candidates, evaluation, biases)
    adjusted_by_id = {candidate.player_id: candidate for candidate in adjusted_candidates}
    active_ids = roster_cutdown.choose_active_roster(adjusted_candidates, active_limit)
    own_ps_ids = roster_cutdown.choose_practice_squad(
        adjusted_candidates,
        active_ids,
        practice_squad_limit,
    )
    open_ps_slots = max(0, practice_squad_limit - len(own_ps_ids))
    raw_free_agent_ps_options = roster_cutdown.free_agent_practice_squad_candidates(
        con,
        season=season,
        exclude_ids={candidate.player_id for candidate in adjusted_candidates},
        limit=max(open_ps_slots * 12, 80) if open_ps_slots else 0,
    )
    free_agent_ps_options = [
        candidate for candidate in raw_free_agent_ps_options
        if plausible_free_agent_practice_squad_candidate(candidate)
    ][:open_ps_slots]

    active_rows: list[dict[str, Any]] = []
    ps_rows: list[dict[str, Any]] = []
    release_rows: list[dict[str, Any]] = []
    for candidate in sorted(adjusted_candidates, key=lambda item: (item.group, -item.keep_score, item.name)):
        reasons = reasons_by_id.get(candidate.player_id, [])
        if candidate.player_id in active_ids:
            active_rows.append(
                annotate_candidate(
                    candidate,
                    action="keep_active",
                    adjusted_keep_score=candidate.keep_score,
                    adjusted_ps_score=candidate.ps_score,
                    reasons=reasons,
                )
            )
        elif candidate.player_id in own_ps_ids:
            ps_rows.append(
                annotate_candidate(
                    candidate,
                    action="practice_squad_priority",
                    adjusted_keep_score=candidate.keep_score,
                    adjusted_ps_score=candidate.ps_score,
                    reasons=reasons,
                    waiver_claim_risk=waiver_risk(candidate),
                )
            )
        else:
            release_rows.append(
                annotate_candidate(
                    candidate,
                    action="release_or_waive",
                    adjusted_keep_score=candidate.keep_score,
                    adjusted_ps_score=candidate.ps_score,
                    reasons=reasons,
                    waiver_claim_risk=waiver_risk(candidate),
                )
            )

    active_rows.sort(key=lambda row: (row["position_group"], row["depth_rank"] or 99, -row["adjusted_keep_score"]))
    ps_rows.sort(key=lambda row: (-row["adjusted_ps_score"], row["player_name"]))
    release_rows.sort(key=lambda row: (row["waiver_claim_risk"] != "high", row["waiver_claim_risk"] != "medium", row["adjusted_keep_score"]))

    active_group_counts = position_group_counts(active_rows)
    missing_specialist_groups = [
        group for group in ("K", "P", "LS")
        if active_group_counts.get(group, 0) < 1
    ]
    free_agent_active_rows = free_agent_specialist_options(
        con,
        season=season,
        missing_groups=missing_specialist_groups,
    )
    free_agent_rows = [compact_free_agent_candidate(candidate) for candidate in free_agent_ps_options]
    integrate_required_active_specialists(
        active_rows,
        release_rows,
        free_agent_active_rows,
        active_limit,
    )
    active_rows.sort(key=lambda row: (row["position_group"], row.get("depth_rank") or 99, -as_float(row.get("adjusted_keep_score"))))
    release_rows.sort(key=lambda row: (row.get("waiver_claim_risk") != "high", row.get("waiver_claim_risk") != "medium", as_float(row.get("adjusted_keep_score"))))
    validation = validate_plan(
        active=active_rows,
        practice_squad=ps_rows,
        free_agent_practice_squad_options=free_agent_rows,
        releases=release_rows,
        free_agent_active_options=free_agent_active_rows,
        active_limit=active_limit,
        practice_squad_limit=practice_squad_limit,
    )
    comparison = compare_to_deterministic(
        original_candidates,
        ai_active_ids={as_int(row["player_id"]) for row in active_rows},
        ai_ps_ids={as_int(row["player_id"]) for row in [*ps_rows, *free_agent_rows]},
        active_limit=active_limit,
        practice_squad_limit=practice_squad_limit,
    )
    top_needs = [row["position_group"] for row in evaluation.get("roster_needs", [])[:3]]
    total_ps_count = len(ps_rows) + len(free_agent_rows)
    summary = (
        f"{team['abbreviation']} advisory cutdown plan: keep {len(active_rows)} active, "
        f"prioritize {total_ps_count} practice-squad players "
        f"({len(ps_rows)} own, {len(free_agent_rows)} free agent), release/waive {len(release_rows)}. "
        f"Team phase {evaluation['team_direction']['team_phase']}; top needs {', '.join(top_needs) or 'none'}."
    )
    plan = {
        "game_id": game_id,
        "season": season,
        "plan_date": plan_date,
        "advisory_only": True,
        "team": {
            "team_id": as_int(team["team_id"]),
            "abbreviation": team["abbreviation"],
            "name": f"{team['city']} {team['nickname']}",
        },
        "limits": limits,
        "summary": summary,
        "gm_biases": biases,
        "team_evaluation_summary": {
            "team_phase": evaluation["team_direction"]["team_phase"],
            "recommended_posture": evaluation["team_direction"]["recommended_posture"],
            "top_needs": evaluation.get("roster_needs", [])[:5],
            "top_surplus": evaluation.get("roster_surplus", [])[:5],
            "risk_flags": evaluation.get("risk_flags", [])[:5],
        },
        "plan": {
            "active_roster": active_rows,
            "practice_squad_priorities": ps_rows,
            "release_or_waive": release_rows,
            "free_agent_active_options": free_agent_active_rows,
            "free_agent_practice_squad_options": free_agent_rows,
        },
        "validation": validation,
        "comparison_to_deterministic_fallback": comparison,
        "action_taken": "ADVISORY_ONLY: no roster, contract, cap, or depth-chart tables were changed.",
    }
    if persist:
        persist_cutdown_plan(con, plan)
    return plan


def build_league_cutdown_plans(
    con: sqlite3.Connection,
    *,
    season: int | None = None,
    game_id: str = "master",
    active_limit: int | None = None,
    practice_squad_limit: int | None = None,
    persist: bool = False,
) -> list[dict[str, Any]]:
    rows = con.execute("SELECT abbreviation FROM teams ORDER BY abbreviation").fetchall()
    return [
        build_cutdown_plan(
            con,
            team_abbr=row["abbreviation"],
            season=season,
            game_id=game_id,
            active_limit=active_limit,
            practice_squad_limit=practice_squad_limit,
            persist=persist,
        )
        for row in rows
    ]


def persist_cutdown_plan(con: sqlite3.Connection, plan: dict[str, Any]) -> int:
    ensure_schema(con)
    cur = con.execute(
        """
        INSERT INTO ai_gm_cutdown_plans (
            game_id, team_id, season, plan_date, active_limit, practice_squad_limit,
            active_count, practice_squad_count, release_count, validation_status, plan_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan["game_id"],
            as_int(plan["team"]["team_id"]),
            as_int(plan["season"]),
            plan["plan_date"],
            as_int(plan["limits"]["active_roster_limit"]),
            as_int(plan["limits"]["practice_squad_limit"]),
            len(plan["plan"]["active_roster"]),
            as_int(plan["validation"]["counts"]["practice_squad"]),
            len(plan["plan"]["release_or_waive"]),
            plan["validation"]["status"],
            json_dumps(plan),
        ),
    )
    plan_id = as_int(cur.lastrowid)
    plan["plan_id"] = plan_id
    return plan_id


def list_cutdown_plans(
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
        FROM ai_gm_cutdown_plans_view
        {clause}
        ORDER BY created_at DESC, plan_id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def load_cutdown_plan(con: sqlite3.Connection, plan_id: int) -> tuple[sqlite3.Row, dict[str, Any]]:
    ensure_schema(con)
    row = con.execute(
        "SELECT * FROM ai_gm_cutdown_plans WHERE plan_id = ?",
        (plan_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Cutdown plan not found: {plan_id}")
    plan = json.loads(row["plan_json"])
    plan["plan_id"] = plan_id
    return row, plan


def planned_id_set(plan: dict[str, Any], bucket: str) -> set[int]:
    return {
        as_int(row.get("player_id"))
        for row in plan.get("plan", {}).get(bucket, [])
        if row.get("player_id") is not None
    }


def current_plan_drift(saved_plan: dict[str, Any], current_plan: dict[str, Any]) -> dict[str, Any]:
    comparisons = {
        "active_roster": (
            planned_id_set(saved_plan, "active_roster"),
            planned_id_set(current_plan, "active_roster"),
        ),
        "practice_squad_priorities": (
            planned_id_set(saved_plan, "practice_squad_priorities"),
            planned_id_set(current_plan, "practice_squad_priorities"),
        ),
        "free_agent_practice_squad_options": (
            planned_id_set(saved_plan, "free_agent_practice_squad_options"),
            planned_id_set(current_plan, "free_agent_practice_squad_options"),
        ),
        "release_or_waive": (
            planned_id_set(saved_plan, "release_or_waive"),
            planned_id_set(current_plan, "release_or_waive"),
        ),
        "free_agent_active_options": (
            planned_id_set(saved_plan, "free_agent_active_options"),
            planned_id_set(current_plan, "free_agent_active_options"),
        ),
    }
    buckets: dict[str, dict[str, list[int]]] = {}
    for bucket, (saved_ids, current_ids) in comparisons.items():
        missing = sorted(saved_ids - current_ids)
        added = sorted(current_ids - saved_ids)
        if missing or added:
            buckets[bucket] = {"missing_from_current": missing, "new_in_current": added}
    return {"stale": bool(buckets), "buckets": buckets}


def player_current_state(con: sqlite3.Connection, player_id: int) -> dict[str, Any] | None:
    row = con.execute(
        """
        SELECT player_id, team_id, status, first_name || ' ' || last_name AS player_name, position
        FROM players
        WHERE player_id = ?
        """,
        (player_id,),
    ).fetchone()
    return dict(row) if row else None


def sign_free_agent_to_active(
    con: sqlite3.Connection,
    *,
    player_id: int,
    team_id: int,
    season: int,
    notes: str,
) -> int:
    player = con.execute("SELECT * FROM players WHERE player_id = ?", (player_id,)).fetchone()
    if not player:
        raise ValueError(f"Player not found: {player_id}")
    if player["team_id"] is not None or player["status"] != "Free Agent":
        raise ValueError(
            f"{player['first_name']} {player['last_name']} is not a free agent "
            f"(team_id={player['team_id']}, status={player['status']})."
        )
    old_status = player["status"] or "Free Agent"
    minimum_aav = max(
        915_000,
        min(1_500_000, int((as_int(player["overall"], 60) * 18_000) // 10_000 * 10_000)),
    )
    cur = con.execute(
        """
        INSERT INTO contracts (
            player_id, team_id, signed_date, start_year, end_year,
            total_value, total_years, aav, signing_bonus, roster_bonus,
            workout_bonus, is_guaranteed, dead_cap_current, dead_cap_next,
            contract_type, is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, 0, 0, 0, 0, 0, 0, 'Minimum', 1)
        """,
        (
            player_id,
            team_id,
            roster_cutdown.current_date(con),
            season,
            season,
            minimum_aav,
            minimum_aav,
        ),
    )
    contract_id = as_int(cur.lastrowid)
    con.execute(
        "UPDATE players SET team_id = ?, status = 'Active' WHERE player_id = ?",
        (team_id, player_id),
    )
    roster_cutdown.status_history(
        con,
        player=player,
        old_status=old_status,
        new_status="Active",
        season=season,
        reason=notes,
    )
    roster_cutdown.log_roster_transaction(
        con,
        transaction_type="Signing",
        player=player,
        team_id=team_id,
        from_team_id=None,
        to_team_id=team_id,
        old_status=old_status,
        new_status="Active",
        season=season,
        description=f"AI GM reviewed cutdown plan signed {player['first_name']} {player['last_name']} to the active roster.",
        contract_id=contract_id,
    )
    return contract_id


def validate_saved_plan_for_apply(
    *,
    saved_plan: dict[str, Any],
    current_plan: dict[str, Any],
    allow_warning: bool,
    allow_stale: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not saved_plan.get("advisory_only", False):
        errors.append("Plan is not marked advisory_only.")
    status = saved_plan.get("validation", {}).get("status")
    if status == "invalid":
        errors.append("Cannot apply an invalid cutdown plan.")
    elif status == "warning" and not allow_warning:
        errors.append("Plan has warnings. Re-run with --allow-warning after review.")
    drift = current_plan_drift(saved_plan, current_plan)
    if drift["stale"] and not allow_stale:
        errors.append("Saved plan no longer matches the current generated plan. Re-run with --allow-stale after review.")
    if drift["stale"]:
        warnings.append("Saved plan differs from the current roster-derived plan.")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "drift": drift,
        "saved_validation_status": status,
        "current_validation_status": current_plan.get("validation", {}).get("status"),
    }


def apply_cutdown_plan(
    con: sqlite3.Connection,
    *,
    plan_id: int,
    allow_warning: bool = False,
    allow_stale: bool = False,
    save_validation: bool = True,
) -> dict[str, Any]:
    row, saved_plan = load_cutdown_plan(con, plan_id)
    if row["apply_status"] == "applied":
        raise ValueError(f"Cutdown plan {plan_id} has already been applied.")
    team_abbr = saved_plan["team"]["abbreviation"]
    team = get_team(con, team_abbr)
    season = as_int(saved_plan["season"])
    game_id = saved_plan.get("game_id") or "master"
    limits = saved_plan.get("limits", {})
    current = build_cutdown_plan(
        con,
        team_abbr=team_abbr,
        season=season,
        game_id=game_id,
        active_limit=as_int(limits.get("active_roster_limit"), 53),
        practice_squad_limit=as_int(limits.get("practice_squad_limit"), 16),
        persist=False,
    )
    preflight = validate_saved_plan_for_apply(
        saved_plan=saved_plan,
        current_plan=current,
        allow_warning=allow_warning,
        allow_stale=allow_stale,
    )
    if not preflight["valid"]:
        return {
            "plan_id": plan_id,
            "team": team_abbr,
            "applied": False,
            "preflight": preflight,
            "operations": [],
            "post_validation": None,
        }

    buckets = saved_plan["plan"]
    notes = f"AI GM reviewed cutdown plan {plan_id} for {season}."
    operations: list[dict[str, Any]] = []

    for item in buckets.get("practice_squad_priorities", []):
        state = player_current_state(con, as_int(item["player_id"]))
        if state and state["team_id"] == team["team_id"] and state["status"] != "Practice Squad":
            if roster_cutdown.move_to_practice_squad(con, as_int(item["player_id"]), season, notes):
                operations.append({"action": "move_to_practice_squad", **item})

    for item in buckets.get("release_or_waive", []):
        state = player_current_state(con, as_int(item["player_id"]))
        if state and state["team_id"] == team["team_id"] and state["status"] != "Free Agent":
            roster_cutdown.release_player(con, as_int(item["player_id"]), season, notes)
            operations.append({"action": "release_or_waive", **item})

    for item in buckets.get("free_agent_active_options", []):
        sign_free_agent_to_active(
            con,
            player_id=as_int(item["player_id"]),
            team_id=as_int(team["team_id"]),
            season=season,
            notes=notes,
        )
        operations.append({"action": "sign_active_free_agent", **item})

    for item in buckets.get("free_agent_practice_squad_options", []):
        if roster_cutdown.sign_free_agent_to_practice_squad(
            con,
            player_id=as_int(item["player_id"]),
            team_id=as_int(team["team_id"]),
            season=season,
            notes=notes,
        ):
            operations.append({"action": "sign_free_agent_to_practice_squad", **item})

    roster_cutdown.resolve_roster_alerts(con, game_id, as_int(team["team_id"]))
    roster_cutdown.rebuild_contract_years(con)
    roster_cutdown.sync_team_cap_space(con)
    roster_cutdown.snapshot_cap_ledger(
        con,
        label=f"after_ai_gm_cutdown_plan_{plan_id}",
        phase=PHASE,
        source="ai_gm_cutdown_plan",
        replace=True,
    )
    rule_set = roster_rules.get_rule_set(con, season, PHASE)
    after_active, after_ps, errors, warnings = roster_cutdown.validate_after(
        con,
        team=team,
        rule_set=rule_set,
        save_validation=save_validation,
    )
    post_validation = {
        "active": after_active,
        "practice_squad": after_ps,
        "errors": errors,
        "warnings": warnings,
    }
    apply_log = {
        "plan_id": plan_id,
        "team": team_abbr,
        "season": season,
        "operations": operations,
        "preflight": preflight,
        "post_validation": post_validation,
    }
    con.execute(
        """
        UPDATE ai_gm_cutdown_plans
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
        "applied": True,
        "preflight": preflight,
        "operations": operations,
        "post_validation": post_validation,
    }


def print_plan_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No persisted AI GM cutdown plans found.")
        return
    print(f"{'ID':>4} {'TEAM':<4} {'GAME':<16} {'SEASON':>6} {'DATE':<10} {'ACTIVE':>7} {'PS':>4} {'REL':>4} {'VALID':<8} {'APPLY':<8}")
    for row in rows:
        print(
            f"{row['plan_id']:>4} {row['team']:<4} {str(row['game_id'])[:16]:<16} "
            f"{row['season']:>6} {row['plan_date']:<10} {row['active_count']:>7} "
            f"{row['practice_squad_count']:>4} {row['release_count']:>4} "
            f"{row['validation_status']:<8} {row.get('apply_status') or 'pending':<8}"
        )


def print_apply_result(result: dict[str, Any], *, applied: bool, backup: Path | None = None) -> None:
    mode = "APPLIED" if applied and result.get("applied") else "DRY RUN" if result.get("applied") else "BLOCKED"
    print(f"AI GM cutdown plan {result['plan_id']} {mode}")
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
    print(f"Operations: {len(result['operations'])}")
    for op in result["operations"][:20]:
        print(f"  - {op['action']}: {op.get('player_name')} {op.get('position')}")
    if result["post_validation"]:
        post = result["post_validation"]
        print(
            f"Post-validation: active {post['active']}, PS {post['practice_squad']}, "
            f"errors {post['errors']}, warnings {post['warnings']}"
        )


def print_cutdown_plan(plan: dict[str, Any], *, detail_limit: int = 8) -> None:
    team = plan["team"]["abbreviation"]
    validation = plan["validation"]
    buckets = plan["plan"]
    print(f"{team}: {plan['team_evaluation_summary']['team_phase']} cutdown plan ({validation['status']})")
    print(
        f"  Active {len(buckets['active_roster'])}/{plan['limits']['active_roster_limit']} | "
        f"PS {validation['counts']['practice_squad']}/{plan['limits']['practice_squad_limit']} "
        f"({validation['counts']['own_practice_squad']} own, {validation['counts']['free_agent_practice_squad']} FA) | "
        f"Release {len(buckets['release_or_waive'])}"
    )
    print(f"  {plan['summary']}")
    if validation["errors"]:
        print("  Errors: " + "; ".join(validation["errors"]))
    if validation["warnings"]:
        print("  Warnings: " + "; ".join(validation["warnings"][:3]))
    print(
        "  Active groups: "
        + ", ".join(f"{group}:{count}" for group, count in validation["counts"]["active_position_groups"].items())
    )
    ps = buckets["practice_squad_priorities"][:detail_limit]
    if ps:
        print("  PS priorities: " + ", ".join(f"{row['player_name']} {row['position']}" for row in ps))
    releases = buckets["release_or_waive"][:detail_limit]
    if releases:
        print("  Release/waive: " + ", ".join(f"{row['player_name']} {row['position']}({row.get('waiver_claim_risk')})" for row in releases))
    active_options = buckets.get("free_agent_active_options", [])[:detail_limit]
    if active_options:
        print("  Active FA options: " + ", ".join(f"{row['player_name']} {row['position']}" for row in active_options))
    fa_ps = buckets.get("free_agent_practice_squad_options", [])[:detail_limit]
    if fa_ps:
        print("  FA PS options: " + ", ".join(f"{row['player_name']} {row['position']}" for row in fa_ps))
    diff = plan["comparison_to_deterministic_fallback"]
    if diff["ai_active_over_fallback"] or diff["fallback_active_over_ai"]:
        print(
            f"  Diff vs fallback: AI keeps {len(diff['ai_active_over_fallback'])} different active, "
            f"fallback keeps {len(diff['fallback_active_over_ai'])} different active."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Advisory AI GM cutdown planner.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--season", type=int)
    parser.add_argument("--game-id", default="master")
    parser.add_argument("--team")
    parser.add_argument("--all", action="store_true", help="Plan for all teams.")
    parser.add_argument("--list-plans", action="store_true", help="List persisted advisory cutdown plans.")
    parser.add_argument("--apply-plan-id", type=int, help="Review and apply one persisted cutdown plan.")
    parser.add_argument("--allow-warning", action="store_true", help="Allow applying a plan with warning validation.")
    parser.add_argument("--allow-stale", action="store_true", help="Allow applying a plan that no longer matches the current generated plan.")
    parser.add_argument("--apply", action="store_true", help="Commit roster changes. Without this, apply-plan is a dry run.")
    parser.add_argument("--no-backup", action="store_true", help="Skip backup before a committed apply.")
    parser.add_argument("--no-validation-save", action="store_true", help="Do not persist roster validation rows during apply.")
    parser.add_argument("--limit", type=int, default=20, help="Rows to show when listing persisted plans.")
    parser.add_argument("--active-limit", type=int)
    parser.add_argument("--practice-squad-limit", type=int)
    parser.add_argument("--persist", action="store_true", help="Store plan snapshot in ai_gm_cutdown_plans.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--detail-limit", type=int, default=8)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    con = connect(args.db)
    try:
        if args.list_plans:
            rows = list_cutdown_plans(
                con,
                team_abbr=args.team,
                game_id=args.game_id,
                limit=args.limit,
            )
            if args.json:
                print(json_dumps(rows))
            else:
                print_plan_rows(rows)
            return 0

        if args.apply_plan_id:
            backup = None
            if args.apply and not args.no_backup:
                backup = backup_sqlite(args.db, f"ai_gm_cutdown_plan_{args.apply_plan_id}")
            result = apply_cutdown_plan(
                con,
                plan_id=args.apply_plan_id,
                allow_warning=args.allow_warning,
                allow_stale=args.allow_stale,
                save_validation=not args.no_validation_save,
            )
            if args.apply and result.get("applied"):
                con.commit()
            else:
                con.rollback()
            if args.json:
                print(json_dumps(result))
            else:
                print_apply_result(result, applied=args.apply, backup=backup)
            return 0

        if not args.team and not args.all:
            raise SystemExit("Provide --team TEAM, --all, --list-plans, or --apply-plan-id ID.")

        if args.all:
            plans = build_league_cutdown_plans(
                con,
                season=args.season,
                game_id=args.game_id,
                active_limit=args.active_limit,
                practice_squad_limit=args.practice_squad_limit,
                persist=args.persist,
            )
            if args.json:
                print(json_dumps(plans))
            else:
                for plan in plans:
                    print_cutdown_plan(plan, detail_limit=args.detail_limit)
            if args.persist:
                con.commit()
        else:
            plan = build_cutdown_plan(
                con,
                team_abbr=args.team,
                season=args.season,
                game_id=args.game_id,
                active_limit=args.active_limit,
                practice_squad_limit=args.practice_squad_limit,
                persist=args.persist,
            )
            if args.json:
                print(json_dumps(plan))
            else:
                print_cutdown_plan(plan, detail_limit=args.detail_limit)
            if args.persist:
                con.commit()
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
