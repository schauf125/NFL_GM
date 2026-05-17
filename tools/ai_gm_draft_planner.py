#!/usr/bin/env python3
"""Advisory AI GM draft planner.

The planner builds a team-specific draft board from roster needs, contract
pressure, pick inventory, GM tendencies, and the visible/discovered prospect
pool. Saved plans can guide CPU draft-room auto-picks, but this module does not
select players or mutate draft inventory by itself.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import ai_gm_team_evaluator as team_eval


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.draft.schema import ensure_schema as ensure_draft_schema  # noqa: E402
DEFAULT_SEASON = 2026
DEFAULT_BOARD_LIMIT = 120
PREMIUM_GROUPS = {"QB", "WR", "OL", "EDGE", "IDL", "CB"}
LOW_COST_GROUPS = {"K", "P", "LS"}
ROUND_ONE_PLAN_MIN_GRADE = 68.0
ROUND_ONE_PLAN_MIN_CEILING = 76.0
ROUND_TWO_PLAN_MIN_GRADE = 62.0
ROUND_TWO_PLAN_MIN_CEILING = 68.0
CONFIDENCE_PENALTY_BASE = {
    "unscouted": 18.0,
    "low": 14.0,
    "medium": 4.0,
    "high": 0.5,
    "very high": 0.0,
}
CONFIDENCE_ROUND_MULTIPLIER = {
    1: 1.0,
    2: 0.65,
    3: 0.40,
    4: 0.22,
    5: 0.12,
    6: 0.06,
    7: 0.0,
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


def current_season(con: sqlite3.Connection) -> int:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key IN ('current_season', 'current_league_year') ORDER BY setting_key LIMIT 1"
    ).fetchone()
    return as_int(row["setting_value"], DEFAULT_SEASON) if row else DEFAULT_SEASON


def current_date(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
    ).fetchone()
    return str(row["setting_value"]) if row else datetime.now().date().isoformat()


def active_game_id(con: sqlite3.Connection) -> str:
    row = con.execute("SELECT setting_value FROM game_settings WHERE setting_key = 'active_game_id'").fetchone()
    if row and row["setting_value"]:
        return str(row["setting_value"])
    try:
        save = con.execute(
            """
            SELECT game_id
            FROM game_saves
            WHERE status = 'active'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()
    except sqlite3.OperationalError:
        save = None
    return str(save["game_id"]) if save else "master"


def row_value(row: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(row, sqlite3.Row):
        if key not in row.keys():
            return default
        value = row[key]
    else:
        value = row.get(key, default)
    return default if value is None else value


def prospect_name(row: sqlite3.Row | dict[str, Any]) -> str:
    return f"{row_value(row, 'first_name', '')} {row_value(row, 'last_name', '')}".strip()


def position_group(position: str | None) -> str:
    return team_eval.position_group(position)


def get_team(con: sqlite3.Connection, team_abbr: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM teams WHERE abbreviation = ?", (team_abbr.upper(),)).fetchone()
    if not row:
        raise ValueError(f"Team not found: {team_abbr}")
    return row


def ensure_schema(con: sqlite3.Connection) -> None:
    ensure_draft_schema(con)
    team_eval.ensure_schema(con)
    ensure_column(con, "ai_gm_draft_plans", "apply_status", "apply_status TEXT NOT NULL DEFAULT 'pending'")
    con.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS ai_gm_draft_plans (
            plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL DEFAULT 'master',
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            draft_year INTEGER NOT NULL,
            season INTEGER NOT NULL,
            plan_date TEXT NOT NULL,
            board_count INTEGER NOT NULL DEFAULT 0,
            priority_count INTEGER NOT NULL DEFAULT 0,
            pick_count INTEGER NOT NULL DEFAULT 0,
            top_prospect_id INTEGER REFERENCES draft_prospects(prospect_id) ON DELETE SET NULL,
            top_prospect_name TEXT,
            top_position TEXT,
            top_score REAL,
            strategy_summary TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            apply_status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_ai_gm_draft_plans_team_year
            ON ai_gm_draft_plans(game_id, team_id, draft_year, created_at DESC);

        DROP VIEW IF EXISTS ai_gm_draft_plans_view;
        CREATE VIEW ai_gm_draft_plans_view AS
        SELECT
            p.plan_id,
            p.game_id,
            p.team_id,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name,
            p.draft_year,
            p.season,
            p.plan_date,
            p.board_count,
            p.priority_count,
            p.pick_count,
            p.top_prospect_id,
            p.top_prospect_name,
            p.top_position,
            p.top_score,
            p.strategy_summary,
            p.apply_status,
            p.created_at,
            p.updated_at
        FROM ai_gm_draft_plans p
        JOIN teams t ON t.team_id = p.team_id;
        """
    )


def draft_class(con: sqlite3.Connection, draft_year: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM draft_classes WHERE draft_year = ?", (draft_year,)).fetchone()
    if not row:
        raise ValueError(f"No draft class found for {draft_year}.")
    return row


def draft_picks_for_team(con: sqlite3.Connection, team_id: int, draft_year: int) -> list[dict[str, Any]]:
    if not table_exists(con, "draft_picks"):
        return []
    rows = con.execute(
        """
        WITH ordered AS (
            SELECT
                dp.*,
                ROW_NUMBER() OVER (
                    PARTITION BY dp.draft_year
                    ORDER BY dp.round, COALESCE(dp.pick_number, dp.pick_id), dp.pick_id
                ) AS effective_pick_number,
                ROW_NUMBER() OVER (
                    PARTITION BY dp.draft_year, dp.round
                    ORDER BY COALESCE(dp.pick_number, dp.pick_id), dp.pick_id
                ) AS effective_pick_in_round
            FROM draft_picks dp
        )
        SELECT
            dp.pick_id,
            dp.draft_year,
            dp.round,
            dp.pick_number,
            dp.effective_pick_number,
            dp.effective_pick_in_round,
            dp.original_team_id,
            original.abbreviation AS original_team,
            dp.is_comp_pick,
            dp.is_traded,
            dp.trade_note
        FROM ordered dp
        LEFT JOIN teams original ON original.team_id = dp.original_team_id
        WHERE dp.draft_year = ?
          AND dp.current_team_id = ?
          AND COALESCE(dp.is_used, 0) = 0
        ORDER BY dp.effective_pick_number
        """,
        (draft_year, team_id),
    ).fetchall()
    return [dict(row) for row in rows]


def gm_profile(con: sqlite3.Connection, team_id: int) -> dict[str, Any]:
    if not table_exists(con, "ai_gm_profiles"):
        return {}
    row = con.execute("SELECT * FROM ai_gm_profiles WHERE team_id = ?", (team_id,)).fetchone()
    return dict(row) if row else {}


def profile_biases(profile: dict[str, Any]) -> dict[str, float]:
    text = " ".join(
        str(profile.get(key) or "")
        for key in (
            "draft_tendency",
            "draft_policy",
            "draft_pick_policy",
            "position_investment_policy",
            "risk_profile",
            "team_build_state",
        )
    ).lower()
    return {
        "premium_bonus": 4.0 if "premium" in text or "core-player" in text else 2.0,
        "need_bonus": 3.0 if "need" in text or "scheme" in text else 1.5,
        "trade_down_bonus": 2.5 if "trade down" in text or "preserve" in text else 0.0,
        "upside_bonus": 2.5 if "upside" in text or "development" in text or "young" in text else 0.5,
        "risk_penalty": 3.5 if "avoid" in text or "disciplined" in text else 1.5,
    }


def position_priority_map(evaluation: dict[str, Any], profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    priorities: dict[str, dict[str, Any]] = {}
    biases = profile_biases(profile)
    for index, need in enumerate(evaluation.get("roster_needs") or []):
        group = str(need.get("position_group") or "")
        if not group:
            continue
        score = as_float(need.get("need_score")) + max(0, 8 - index) * 1.4 + biases["need_bonus"]
        if group in PREMIUM_GROUPS:
            score += biases["premium_bonus"]
        priorities[group] = {
            "position_group": group,
            "priority": need.get("priority") or "medium",
            "need_score": as_float(need.get("need_score")),
            "draft_priority_score": round(score, 1),
            "drivers": need.get("drivers") or [],
        }

    for player in evaluation.get("contract_pressure") or []:
        group = str(player.get("position_group") or "")
        if not group:
            continue
        existing = priorities.setdefault(
            group,
            {
                "position_group": group,
                "priority": "future",
                "need_score": 0.0,
                "draft_priority_score": 0.0,
                "drivers": [],
            },
        )
        pressure = 8.0
        if as_int(player.get("years_until_expiry"), 9) <= 1:
            pressure += 5.0
        if group in PREMIUM_GROUPS:
            pressure += 3.0
        existing["draft_priority_score"] = round(as_float(existing["draft_priority_score"]) + pressure, 1)
        existing["drivers"] = list(existing.get("drivers") or []) + [
            f"contract successor: {player.get('player_name') or 'starter'}"
        ]
        if existing.get("priority") == "future":
            existing["priority"] = "contract_cliff"

    for group in LOW_COST_GROUPS:
        if group in priorities:
            priorities[group]["draft_priority_score"] = round(as_float(priorities[group]["draft_priority_score"]) * 0.55, 1)
            priorities[group]["drivers"] = list(priorities[group].get("drivers") or []) + [
                "low-cost role; prefer late board value"
            ]
    return priorities


def available_prospects(
    con: sqlite3.Connection,
    draft_year: int,
    *,
    team_id: int,
    limit: int,
) -> list[sqlite3.Row]:
    scouting_join = ""
    scouting_cols = "0 AS cpu_scouting_level, 'Unscouted' AS cpu_scouting_confidence, 0 AS cpu_times_scouted, NULL AS cpu_visibility_status"
    visibility_clause = ""
    game_id = active_game_id(con)
    params: list[Any] = []
    if table_exists(con, "cpu_scouting_prospect_progress"):
        scouting_join = """
        LEFT JOIN cpu_scouting_prospect_progress csp
          ON csp.prospect_id = dp.prospect_id
         AND csp.game_id = ?
         AND csp.draft_year = ?
         AND csp.team_id = ?
        """
        scouting_cols = (
            "COALESCE(MAX(csp.scouting_level), 0) AS cpu_scouting_level, "
            "COALESCE(MAX(csp.scouting_confidence), 'Unscouted') AS cpu_scouting_confidence, "
            "COALESCE(MAX(csp.times_scouted), 0) AS cpu_times_scouted, "
            "MAX(csp.visibility_status) AS cpu_visibility_status"
        )
        visibility_clause = "OR COALESCE(csp.visibility_status, '') = 'discovered'"
        params.extend([game_id, draft_year, team_id])
    params.extend([draft_year, limit])
    return con.execute(
        f"""
        SELECT
            dp.*,
            dc.draft_year,
            COALESCE(dp.public_board_rank, dp.scouting_rank, dp.true_rank, 9999) AS board_rank,
            dpc.combine_status,
            dpc.athletic_score AS combine_athletic_score,
            dpc.is_injured AS combine_injured,
            dpd.medical_recheck AS pro_day_medical_recheck,
            dpd.athletic_score AS pro_day_athletic_score,
            {scouting_cols}
        FROM draft_prospects dp
        JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
        LEFT JOIN draft_prospect_combine_results dpc ON dpc.prospect_id = dp.prospect_id
        LEFT JOIN draft_prospect_pro_day_results dpd ON dpd.prospect_id = dp.prospect_id
        {scouting_join}
        WHERE dc.draft_year = ?
          AND dp.status = 'Available'
          AND (
                COALESCE(dp.public_board_status, 'ranked') <> 'off_public_board'
                OR COALESCE(dp.discovery_status, '') = 'discovered'
                {visibility_clause}
              )
        GROUP BY dp.prospect_id
        ORDER BY board_rank, dp.prospect_id
        LIMIT ?
        """,
        params,
    ).fetchall()


def pick_portfolio_summary(picks: list[dict[str, Any]]) -> dict[str, Any]:
    rounds: dict[int, int] = {}
    for pick in picks:
        round_number = as_int(pick.get("round"))
        rounds[round_number] = rounds.get(round_number, 0) + 1
    premium = sum(count for round_number, count in rounds.items() if round_number <= 3)
    day_three = sum(count for round_number, count in rounds.items() if round_number >= 4)
    earliest = min((as_int(pick.get("effective_pick_number") or pick.get("pick_number"), 999) for pick in picks), default=None)
    return {
        "pick_count": len(picks),
        "rounds": [{"round": round_number, "count": rounds[round_number]} for round_number in sorted(rounds)],
        "premium_picks_rounds_1_to_3": premium,
        "day_three_picks_rounds_4_to_7": day_three,
        "earliest_pick": earliest,
    }


def round_fit_note(row: sqlite3.Row, picks: list[dict[str, Any]]) -> str:
    projected = as_int(row_value(row, "projected_round"))
    if projected <= 0:
        rank = as_int(row_value(row, "board_rank"), 999)
        projected = max(1, min(7, int((rank - 1) / 32) + 1))
    owned_rounds = {as_int(pick.get("round")) for pick in picks}
    if projected in owned_rounds:
        return f"fits owned round {projected}"
    later = [round_number for round_number in owned_rounds if round_number > projected]
    earlier = [round_number for round_number in owned_rounds if round_number < projected]
    if later:
        return f"could fall to round {min(later)}"
    if earlier:
        return f"requires early value at round {max(earlier)}"
    return f"projected round {projected}"


def projected_round(row: sqlite3.Row | dict[str, Any]) -> int:
    projected = as_int(row_value(row, "projected_round"))
    if projected > 0:
        return max(1, min(7, projected))
    rank = as_int(row_value(row, "board_rank"), 999)
    return max(1, min(7, int((rank - 1) / 32) + 1))


def confidence_context_round(row: sqlite3.Row | dict[str, Any], picks: list[dict[str, Any]]) -> int:
    """Estimate the earliest pick round where this prospect could tempt the GM."""
    projected = projected_round(row)
    owned_rounds = sorted({as_int(pick.get("round")) for pick in picks if as_int(pick.get("round")) > 0})
    if not owned_rounds:
        return projected
    rank = as_int(row_value(row, "board_rank"), 999)
    earliest = owned_rounds[0]
    if earliest == 1 and projected <= 2 and rank <= 64:
        return 1
    if projected in owned_rounds:
        return projected
    later = [round_number for round_number in owned_rounds if round_number > projected]
    if later:
        return min(later)
    return max(owned_rounds)


def confidence_penalty(row: sqlite3.Row | dict[str, Any], picks: list[dict[str, Any]]) -> tuple[float, int, str]:
    confidence = str(row_value(row, "cpu_scouting_confidence", "Unscouted") or "Unscouted").strip()
    key = confidence.lower()
    context_round = confidence_context_round(row, picks)
    base = CONFIDENCE_PENALTY_BASE.get(key, 4.0)
    multiplier = CONFIDENCE_ROUND_MULTIPLIER.get(max(1, min(7, context_round)), 0.0)
    return base * multiplier, context_round, confidence


def score_prospect(
    row: sqlite3.Row,
    *,
    priorities: dict[str, dict[str, Any]],
    profile: dict[str, Any],
    picks: list[dict[str, Any]],
) -> dict[str, Any]:
    group = position_group(str(row["position"]))
    priority = priorities.get(group, {})
    biases = profile_biases(profile)
    rank = as_int(row["board_rank"], 999)
    grade = as_float(row_value(row, "scout_grade", row_value(row, "overall", row_value(row, "true_grade", 50))))
    ceiling = as_float(row_value(row, "scout_ceiling", row_value(row, "potential", row_value(row, "ceiling_grade", grade))))
    athletic = max(
        as_float(row_value(row, "combine_athletic_score")),
        as_float(row_value(row, "pro_day_athletic_score")),
    )

    board_value = clamp(96 - (rank * 0.23), 8, 96)
    grade_value = clamp((grade - 50) * 1.4, 0, 55)
    ceiling_value = clamp((ceiling - grade) * 0.9, -5, 18)
    need_value = min(28.0, as_float(priority.get("draft_priority_score")) * 0.42)
    premium_value = biases["premium_bonus"] if group in PREMIUM_GROUPS else 0.0
    upside_value = biases["upside_bonus"] if ceiling >= grade + 8 else 0.0
    athletic_value = 2.0 if athletic >= 82 else 1.0 if athletic >= 74 else 0.0
    risk_penalty = 0.0
    risk = str(row_value(row, "scout_risk", row_value(row, "risk_level", "")) or "").lower()
    if "high" in risk:
        risk_penalty += 5.0 + biases["risk_penalty"]
    elif "medium" in risk:
        risk_penalty += 2.0
    if as_int(row_value(row, "combine_injured")) or as_int(row_value(row, "pro_day_medical_recheck")):
        risk_penalty += 3.0
    medical_risk = str(row_value(row, "medical_risk", "") or "").lower()
    if medical_risk == "red flag":
        risk_penalty += 7.0 + biases["risk_penalty"] * 0.45
    elif medical_risk == "concern":
        risk_penalty += 3.5 + biases["risk_penalty"] * 0.25
    elif medical_risk == "monitor":
        risk_penalty += 1.0
    interview_grade = as_float(row_value(row, "interview_grade"))
    interview_trait = str(row_value(row, "interview_trait", "") or "").lower()
    interview_value = 0.0
    if interview_grade:
        interview_value = clamp((interview_grade - 60) * 0.08, -3.0, 3.0)
        if group == "QB":
            interview_value *= 1.6
        else:
            estimated_round = projected_round(row)
            if estimated_round <= 2:
                interview_value *= 1.15
    if "concern" in interview_trait or "entitlement" in interview_trait:
        risk_penalty += 2.0
    if group in LOW_COST_GROUPS and any(as_int(pick.get("round")) <= 4 for pick in picks):
        risk_penalty += 6.0
    confidence_discount, confidence_round, confidence = confidence_penalty(row, picks)
    early_quality_penalty = 0.0
    if confidence_round == 1:
        if grade < ROUND_ONE_PLAN_MIN_GRADE:
            early_quality_penalty += 34.0 + ((ROUND_ONE_PLAN_MIN_GRADE - grade) * 2.1)
        if ceiling < ROUND_ONE_PLAN_MIN_CEILING:
            early_quality_penalty += 26.0 + ((ROUND_ONE_PLAN_MIN_CEILING - ceiling) * 1.8)
    elif confidence_round == 2:
        if grade < ROUND_TWO_PLAN_MIN_GRADE:
            early_quality_penalty += 18.0 + ((ROUND_TWO_PLAN_MIN_GRADE - grade) * 1.6)
        if ceiling < ROUND_TWO_PLAN_MIN_CEILING:
            early_quality_penalty += 14.0 + ((ROUND_TWO_PLAN_MIN_CEILING - ceiling) * 1.2)

    score = (
        board_value
        + grade_value
        + ceiling_value
        + need_value
        + premium_value
        + upside_value
        + athletic_value
        + interview_value
        - risk_penalty
        - confidence_discount
        - early_quality_penalty
    )
    reasons = []
    if priority:
        reasons.extend(list(priority.get("drivers") or [])[:2])
    if group in PREMIUM_GROUPS:
        reasons.append("premium-position pick value")
    if ceiling >= grade + 8:
        reasons.append("ceiling materially higher than current grade")
    if athletic >= 82:
        reasons.append("plus athletic testing")
    if risk_penalty >= 5:
        reasons.append("risk discount applied")
    if interview_value >= 1.5:
        reasons.append("strong private/interview context")
    elif interview_value <= -1.5:
        reasons.append("private/interview concern")
    if confidence_discount >= 2:
        reasons.append(f"{confidence.lower()} scouting confidence round-{confidence_round} discount")
    if early_quality_penalty >= 12:
        reasons.append("early-round grade floor discount")
    if not reasons:
        reasons.append("board value fit")

    return {
        "prospect_id": as_int(row["prospect_id"]),
        "player_name": prospect_name(row),
        "position": str(row["position"]),
        "position_group": group,
        "college": row_value(row, "college"),
        "board_rank": rank,
        "public_board_rank": row_value(row, "public_board_rank"),
        "projected_round": row_value(row, "projected_round"),
        "grade": round(grade, 1),
        "ceiling": round(ceiling, 1),
        "risk": row_value(row, "scout_risk", row_value(row, "risk_level")),
        "scout_confidence": confidence,
        "confidence_context_round": confidence_round,
        "confidence_penalty": round(confidence_discount, 2),
        "early_quality_penalty": round(early_quality_penalty, 2),
        "archetype": row_value(row, "archetype"),
        "score": round(score, 2),
        "round_fit": round_fit_note(row, picks),
        "priority_score": as_float(priority.get("draft_priority_score")),
        "reasons": reasons[:4],
    }


def round_plan(
    board: list[dict[str, Any]],
    picks: list[dict[str, Any]],
    priorities: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for pick in picks:
        round_number = as_int(pick.get("round"))
        board_band = [
            row for row in board
            if (
                as_int(row.get("projected_round")) == round_number
                or abs(max(1, as_int(row.get("board_rank"), 999)) - max(1, as_int(pick.get("effective_pick_number") or pick.get("pick_number"), 999))) <= 24
            )
        ]
        targets = sorted(
            priorities.values(),
            key=lambda row: as_float(row.get("draft_priority_score")),
            reverse=True,
        )[:4]
        items.append(
            {
                "pick_id": pick.get("pick_id"),
                "round": round_number,
                "pick_number": pick.get("pick_number") or pick.get("effective_pick_number"),
                "original_team": pick.get("original_team"),
                "target_groups": [
                    {
                        "position_group": row["position_group"],
                        "priority": row["priority"],
                        "score": row["draft_priority_score"],
                    }
                    for row in targets
                ],
                "best_board_fits": board_band[:6],
            }
        )
    return items


def build_draft_plan(
    con: sqlite3.Connection,
    *,
    team_abbr: str,
    draft_year: int | None = None,
    season: int | None = None,
    game_id: str = "master",
    plan_date: str | None = None,
    board_limit: int = DEFAULT_BOARD_LIMIT,
    persist: bool = False,
) -> dict[str, Any]:
    ensure_schema(con)
    season = season or current_season(con)
    draft_year = draft_year or season + 1
    plan_date = plan_date or current_date(con)
    klass = draft_class(con, draft_year)
    team = get_team(con, team_abbr)
    team_id = as_int(team["team_id"])
    profile = gm_profile(con, team_id)
    evaluation = team_eval.evaluate_team(
        con,
        team_abbr=team_abbr,
        season=season,
        game_id=game_id,
        evaluation_date=plan_date,
        persist=False,
    )
    picks = draft_picks_for_team(con, team_id, draft_year)
    priorities = position_priority_map(evaluation, profile)
    prospects = available_prospects(con, draft_year, team_id=team_id, limit=max(25, board_limit))
    scored = [
        score_prospect(row, priorities=priorities, profile=profile, picks=picks)
        for row in prospects
    ]
    scored.sort(key=lambda row: (-as_float(row["score"]), as_int(row["board_rank"]), as_int(row["prospect_id"])))
    board = scored[:board_limit]
    priority_rows = sorted(priorities.values(), key=lambda row: as_float(row["draft_priority_score"]), reverse=True)
    pick_summary = pick_portfolio_summary(picks)
    summary = (
        f"{team['abbreviation']} {draft_year} draft plan: "
        f"{pick_summary['pick_count']} pick(s), "
        f"top priorities {', '.join(row['position_group'] for row in priority_rows[:3]) or 'value board'}, "
        f"top board fit {board[0]['player_name'] if board else 'none'}."
    )
    plan = {
        "game_id": game_id,
        "season": season,
        "draft_year": draft_year,
        "plan_date": plan_date,
        "advisory_only": True,
        "draft_class": {
            "draft_class_id": as_int(klass["draft_class_id"]),
            "class_name": klass["class_name"],
            "class_strength": as_int(klass["class_strength"]),
            "status": klass["status"],
        },
        "team": {
            "team_id": team_id,
            "abbreviation": team["abbreviation"],
            "name": f"{team['city']} {team['nickname']}",
        },
        "summary": summary,
        "team_direction": evaluation.get("team_direction"),
        "pick_portfolio": pick_summary,
        "picks": picks,
        "position_priorities": priority_rows,
        "board": board,
        "round_plan": round_plan(board, picks, priorities),
        "counts": {
            "board": len(board),
            "priorities": len(priority_rows),
            "picks": len(picks),
        },
        "action_taken": "ADVISORY_ONLY: no picks, prospect statuses, players, roster, cap, or transaction tables were changed.",
    }
    if persist:
        persist_draft_plan(con, plan)
    return plan


def build_league_draft_plans(
    con: sqlite3.Connection,
    *,
    draft_year: int | None = None,
    season: int | None = None,
    game_id: str = "master",
    board_limit: int = DEFAULT_BOARD_LIMIT,
    persist: bool = False,
) -> list[dict[str, Any]]:
    return [
        build_draft_plan(
            con,
            team_abbr=row["abbreviation"],
            draft_year=draft_year,
            season=season,
            game_id=game_id,
            board_limit=board_limit,
            persist=persist,
        )
        for row in con.execute("SELECT abbreviation FROM teams ORDER BY abbreviation").fetchall()
    ]


def persist_draft_plan(con: sqlite3.Connection, plan: dict[str, Any]) -> int:
    ensure_schema(con)
    board = plan.get("board") or []
    top = board[0] if board else {}
    cur = con.execute(
        """
        INSERT INTO ai_gm_draft_plans (
            game_id, team_id, draft_year, season, plan_date,
            board_count, priority_count, pick_count, top_prospect_id,
            top_prospect_name, top_position, top_score, strategy_summary, plan_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan["game_id"],
            as_int(plan["team"]["team_id"]),
            as_int(plan["draft_year"]),
            as_int(plan["season"]),
            plan["plan_date"],
            as_int(plan["counts"]["board"]),
            as_int(plan["counts"]["priorities"]),
            as_int(plan["counts"]["picks"]),
            as_int(top.get("prospect_id")) or None,
            top.get("player_name"),
            top.get("position"),
            as_float(top.get("score")) if top else None,
            plan["summary"],
            json_dumps(plan),
        ),
    )
    plan_id = as_int(cur.lastrowid)
    plan["plan_id"] = plan_id
    return plan_id


def list_draft_plans(
    con: sqlite3.Connection,
    *,
    team_abbr: str | None = None,
    game_id: str | None = None,
    draft_year: int | None = None,
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
    if draft_year:
        where.append("draft_year = ?")
        params.append(draft_year)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = con.execute(
        f"""
        SELECT *
        FROM ai_gm_draft_plans_view
        {clause}
        ORDER BY created_at DESC, plan_id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def load_plan_json(con: sqlite3.Connection, plan_id: int) -> dict[str, Any]:
    row = con.execute("SELECT plan_json FROM ai_gm_draft_plans WHERE plan_id = ?", (plan_id,)).fetchone()
    if not row:
        raise ValueError(f"Draft plan not found: {plan_id}")
    return json.loads(row["plan_json"])


def latest_plan_row(con: sqlite3.Connection, *, team_id: int, draft_year: int, game_id: str | None = None) -> sqlite3.Row | None:
    if not table_exists(con, "ai_gm_draft_plans"):
        return None
    params: list[Any] = [team_id, draft_year]
    where = ["team_id = ?", "draft_year = ?"]
    if game_id:
        where.append("game_id = ?")
        params.append(game_id)
    return con.execute(
        f"""
        SELECT *
        FROM ai_gm_draft_plans
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC, plan_id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()


def choose_candidate_from_latest_plan(
    con: sqlite3.Connection,
    *,
    team_id: int,
    draft_year: int,
    candidate_ids: set[int],
    game_id: str | None = None,
) -> int | None:
    """Return the top still-available candidate from the latest saved plan."""
    if not candidate_ids:
        return None
    row = latest_plan_row(con, team_id=team_id, draft_year=draft_year, game_id=game_id)
    if not row and game_id:
        row = latest_plan_row(con, team_id=team_id, draft_year=draft_year, game_id=None)
    if not row:
        return None
    try:
        plan = json.loads(row["plan_json"])
    except json.JSONDecodeError:
        return None
    for item in plan.get("board") or []:
        prospect_id = as_int(item.get("prospect_id"))
        if prospect_id in candidate_ids:
            available = con.execute(
                "SELECT status FROM draft_prospects WHERE prospect_id = ?",
                (prospect_id,),
            ).fetchone()
            if available and available["status"] == "Available":
                return prospect_id
    return None


def print_draft_plan(plan: dict[str, Any], *, detail_limit: int = 10) -> None:
    team = plan["team"]["abbreviation"]
    print(plan["summary"])
    direction = plan.get("team_direction") or {}
    print(f"Posture: {direction.get('recommended_posture') or '-'}")
    portfolio = plan.get("pick_portfolio") or {}
    print(
        f"Picks: {portfolio.get('pick_count', 0)} | "
        f"premium {portfolio.get('premium_picks_rounds_1_to_3', 0)} | "
        f"day three {portfolio.get('day_three_picks_rounds_4_to_7', 0)}"
    )
    print("Priorities:")
    for row in (plan.get("position_priorities") or [])[:detail_limit]:
        print(
            f"  {row['position_group']:<4} {row.get('priority'):<14} "
            f"score {row.get('draft_priority_score'):>5} "
            f"{'; '.join((row.get('drivers') or [])[:2])}"
        )
    print("Board:")
    for row in (plan.get("board") or [])[:detail_limit]:
        print(
            f"  {row['score']:>6.2f} #{row['board_rank']:<4} "
            f"{row['player_name']:<24} {row['position']:<4} "
            f"{row.get('college') or '-':<18} {row['round_fit']} "
            f"({'; '.join(row.get('reasons') or [])})"
        )


def print_plan_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No saved AI GM draft plans found.")
        return
    print(" ID Team Year Date       Picks Pri Board Top Prospect              Pos Score Status")
    for row in rows:
        top = str(row.get("top_prospect_name") or "-")[:24]
        print(
            f"{row['plan_id']:>3} {row['team']:<4} {row['draft_year']:>4} "
            f"{row['plan_date']:<10} {row['pick_count']:>5} "
            f"{row['priority_count']:>3} {row['board_count']:>5} "
            f"{top:<24} {row.get('top_position') or '-':<3} "
            f"{as_float(row.get('top_score')):>5.1f} {row.get('apply_status') or 'pending'}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Build an advisory AI GM draft plan.")
    plan_parser.add_argument("--game-id", default="master")
    plan_parser.add_argument("--team")
    plan_parser.add_argument("--all", action="store_true")
    plan_parser.add_argument("--draft-year", type=int)
    plan_parser.add_argument("--season", type=int)
    plan_parser.add_argument("--board-limit", type=int, default=DEFAULT_BOARD_LIMIT)
    plan_parser.add_argument("--persist", action="store_true")
    plan_parser.add_argument("--json", action="store_true")
    plan_parser.add_argument("--detail-limit", type=int, default=10)

    list_parser = subparsers.add_parser("plans", help="List saved AI GM draft plans.")
    list_parser.add_argument("--game-id")
    list_parser.add_argument("--team")
    list_parser.add_argument("--draft-year", type=int)
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    con = connect(args.db)
    try:
        if args.command == "plan":
            season = args.season or current_season(con)
            draft_year = args.draft_year or season + 1
            if not args.team and not args.all:
                raise ValueError("Provide --team TEAM or --all.")
            if args.all:
                plans = build_league_draft_plans(
                    con,
                    draft_year=draft_year,
                    season=season,
                    game_id=args.game_id,
                    board_limit=args.board_limit,
                    persist=args.persist,
                )
                if args.json:
                    print(json_dumps(plans))
                else:
                    for plan in plans:
                        print_draft_plan(plan, detail_limit=args.detail_limit)
                        print()
                if args.persist:
                    con.commit()
            else:
                plan = build_draft_plan(
                    con,
                    team_abbr=args.team,
                    draft_year=draft_year,
                    season=season,
                    game_id=args.game_id,
                    board_limit=args.board_limit,
                    persist=args.persist,
                )
                if args.json:
                    print(json_dumps(plan))
                else:
                    print_draft_plan(plan, detail_limit=args.detail_limit)
                if args.persist:
                    con.commit()
        elif args.command == "plans":
            rows = list_draft_plans(
                con,
                team_abbr=args.team,
                game_id=args.game_id,
                draft_year=args.draft_year,
                limit=args.limit,
            )
            if args.json:
                print(json_dumps(rows))
            else:
                print_plan_rows(rows)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
