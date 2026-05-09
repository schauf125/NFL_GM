#!/usr/bin/env python3
"""Deterministic team evaluator for AI GM context packets.

The AI GM can be creative, but it needs a stable football-ops baseline first.
This module turns roster, cap, contract, development, and injury data into a
save-scoped team snapshot: phase, needs, surplus, pressure points, and candidate
lists. It is advisory only and does not change rosters or contracts.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_SEASON = 2026

CONTROLLED_STATUS_EXCLUSIONS = {"Free Agent", "Retired"}

POSITION_GROUPS: dict[str, tuple[str, ...]] = {
    "QB": ("QB",),
    "RB": ("RB", "FB"),
    "WR": ("WR",),
    "TE": ("TE",),
    "OL": ("OT", "OG", "C"),
    "EDGE": ("EDGE", "DE", "OLB"),
    "IDL": ("IDL", "DT", "NT"),
    "LB": ("ILB", "MLB", "LB"),
    "CB": ("CB", "NB"),
    "S": ("FS", "SS", "S"),
    "K": ("K",),
    "P": ("P",),
    "LS": ("LS",),
}

POSITION_TO_GROUP = {
    position: group
    for group, positions in POSITION_GROUPS.items()
    for position in positions
}

ROOM_TARGETS: dict[str, dict[str, Any]] = {
    "QB": {"starters": 1, "ideal": 3, "max": 4, "starter_floor": 77, "depth_floor": 61, "tier": "premium"},
    "RB": {"starters": 2, "ideal": 4, "max": 6, "starter_floor": 72, "depth_floor": 62, "tier": "starter_or_depth"},
    "WR": {"starters": 3, "ideal": 6, "max": 8, "starter_floor": 73, "depth_floor": 63, "tier": "premium"},
    "TE": {"starters": 1, "ideal": 3, "max": 5, "starter_floor": 70, "depth_floor": 61, "tier": "starter_or_depth"},
    "OL": {"starters": 5, "ideal": 9, "max": 11, "starter_floor": 71, "depth_floor": 62, "tier": "premium"},
    "EDGE": {"starters": 2, "ideal": 5, "max": 7, "starter_floor": 73, "depth_floor": 62, "tier": "premium"},
    "IDL": {"starters": 2, "ideal": 5, "max": 7, "starter_floor": 72, "depth_floor": 62, "tier": "premium"},
    "LB": {"starters": 3, "ideal": 5, "max": 7, "starter_floor": 70, "depth_floor": 61, "tier": "starter_or_depth"},
    "CB": {"starters": 3, "ideal": 6, "max": 8, "starter_floor": 72, "depth_floor": 62, "tier": "premium"},
    "S": {"starters": 2, "ideal": 5, "max": 6, "starter_floor": 71, "depth_floor": 61, "tier": "starter_or_depth"},
    "K": {"starters": 1, "ideal": 1, "max": 2, "starter_floor": 68, "depth_floor": 58, "tier": "low_cost_role"},
    "P": {"starters": 1, "ideal": 1, "max": 2, "starter_floor": 68, "depth_floor": 58, "tier": "low_cost_role"},
    "LS": {"starters": 1, "ideal": 1, "max": 2, "starter_floor": 60, "depth_floor": 55, "tier": "low_cost_role"},
}

DEV_TRAIT_BONUS = {
    "Normal": 0.0,
    "Star": 5.0,
    "Superstar": 8.0,
    "X-Factor": 11.0,
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


def rows_as_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def json_dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def avg(values: list[float], default: float = 0.0) -> float:
    filtered = [value for value in values if value is not None]
    if not filtered:
        return default
    return sum(filtered) / len(filtered)


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


def position_group(position: str | None) -> str:
    return POSITION_TO_GROUP.get(str(position or "").upper(), str(position or "OTHER").upper())


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


def get_team(con: sqlite3.Connection, team_abbr: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM teams WHERE abbreviation = ?",
        (team_abbr.upper(),),
    ).fetchone()
    if not row:
        raise ValueError(f"Team not found: {team_abbr}")
    return row


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS ai_gm_team_evaluations (
            evaluation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL DEFAULT 'master',
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            evaluation_date TEXT NOT NULL,
            team_phase TEXT NOT NULL,
            competitiveness_score REAL NOT NULL,
            roster_quality_score REAL NOT NULL,
            cap_health_score REAL NOT NULL,
            age_curve_score REAL NOT NULL,
            evaluation_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_ai_gm_team_evaluations_team_date
            ON ai_gm_team_evaluations(game_id, team_id, season, evaluation_date DESC);

        DROP VIEW IF EXISTS ai_gm_team_evaluations_view;
        CREATE VIEW ai_gm_team_evaluations_view AS
        SELECT
            e.evaluation_id,
            e.game_id,
            e.season,
            e.evaluation_date,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name,
            e.team_phase,
            e.competitiveness_score,
            e.roster_quality_score,
            e.cap_health_score,
            e.age_curve_score,
            e.created_at
        FROM ai_gm_team_evaluations e
        JOIN teams t ON t.team_id = e.team_id;
        """
    )


def cap_snapshot(con: sqlite3.Connection, team_id: int, season: int) -> dict[str, Any]:
    if table_exists(con, "team_cap_view"):
        row = con.execute(
            "SELECT * FROM team_cap_view WHERE team_id = ?",
            (team_id,),
        ).fetchone()
        if row:
            return dict(row)
    team = con.execute("SELECT salary_cap, cap_space FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    return {
        "team_id": team_id,
        "season": season,
        "salary_cap": as_int(team["salary_cap"], 0) if team else 0,
        "cap_space": as_int(team["cap_space"], 0) if team else 0,
    }


def roster_counts(con: sqlite3.Connection, team_id: int) -> dict[str, Any]:
    if table_exists(con, "team_roster_counts_view"):
        row = con.execute(
            "SELECT * FROM team_roster_counts_view WHERE team_id = ?",
            (team_id,),
        ).fetchone()
        if row:
            return dict(row)
    row = con.execute(
        """
        SELECT
            SUM(
                CASE
                    WHEN COALESCE(status, 'Active') IN ('Active', 'Questionable', 'Doubtful', 'Out') THEN 1
                    ELSE 0
                END
            ) AS active_roster_count,
            SUM(CASE WHEN COALESCE(status, '') = 'Practice Squad' THEN 1 ELSE 0 END) AS practice_squad_count,
            COUNT(*) AS total_controlled_count
        FROM players
        WHERE team_id = ?
          AND COALESCE(status, 'Active') NOT IN ('Free Agent', 'Retired')
        """,
        (team_id,),
    ).fetchone()
    return dict(row) if row else {}


def team_record(con: sqlite3.Connection, team_id: int, season: int) -> dict[str, Any]:
    if not table_exists(con, "season_standings_view"):
        return {}
    row = con.execute(
        "SELECT * FROM season_standings_view WHERE team_id = ? AND season = ?",
        (team_id, season),
    ).fetchone()
    return dict(row) if row else {}


def role_score_map(con: sqlite3.Connection, season: int) -> dict[int, float]:
    if not table_exists(con, "player_role_scores"):
        return {}
    rows = con.execute(
        """
        SELECT player_id, MAX(role_score) AS role_score
        FROM player_role_scores
        WHERE season = ?
        GROUP BY player_id
        """,
        (season,),
    ).fetchall()
    return {as_int(row["player_id"]): as_float(row["role_score"]) for row in rows}


def injury_risk_map(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    if not table_exists(con, "player_injury_risk_view"):
        return {}
    rows = con.execute(
        """
        SELECT
            player_id,
            SUM(injury_count) AS injury_count,
            SUM(major_count) AS major_injury_count,
            SUM(games_missed) AS injury_games_missed,
            MAX(max_recurrence_risk) AS recurrence_risk,
            MAX(active_status) AS active_status,
            MAX(active_return_date) AS active_return_date
        FROM player_injury_risk_view
        GROUP BY player_id
        """
    ).fetchall()
    return {as_int(row["player_id"]): dict(row) for row in rows}


def active_injury_map(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    if not table_exists(con, "active_player_injuries"):
        return {}
    rows = con.execute(
        """
        SELECT
            player_id,
            COUNT(*) AS active_injury_count,
            MAX(expected_games) AS max_expected_games,
            GROUP_CONCAT(injury_label, ', ') AS active_injury_labels
        FROM active_player_injuries
        WHERE status IN ('Questionable', 'Doubtful', 'Out', 'IR', 'PUP', 'NFI')
        GROUP BY player_id
        """
    ).fetchall()
    return {as_int(row["player_id"]): dict(row) for row in rows}


def depth_rank_map(con: sqlite3.Connection) -> dict[int, int]:
    if not table_exists(con, "depth_charts"):
        return {}
    rows = con.execute(
        """
        SELECT player_id, MIN(depth_rank) AS depth_rank
        FROM depth_charts
        GROUP BY player_id
        """
    ).fetchall()
    return {as_int(row["player_id"]): as_int(row["depth_rank"]) for row in rows}


def controlled_players(con: sqlite3.Connection, team_id: int, season: int) -> list[dict[str, Any]]:
    roles = role_score_map(con, season)
    injuries = injury_risk_map(con)
    active_injuries = active_injury_map(con)
    depth = depth_rank_map(con)
    rows = con.execute(
        """
        SELECT
            p.player_id,
            p.first_name,
            p.last_name,
            p.position,
            p.age,
            p.years_exp,
            p.overall,
            p.potential,
            p.dev_trait,
            p.status,
            p.is_rookie,
            c.end_year,
            c.aav,
            cy.cap_hit,
            cy.dead_cap_if_cut_pre_june1
        FROM players p
        LEFT JOIN contracts c
          ON c.player_id = p.player_id
         AND c.is_active = 1
        LEFT JOIN contract_years cy
          ON cy.contract_id = c.contract_id
         AND cy.season = ?
        WHERE p.team_id = ?
          AND COALESCE(p.status, 'Active') NOT IN ('Free Agent', 'Retired')
        ORDER BY p.overall DESC, p.potential DESC, p.last_name, p.first_name
        """,
        (season, team_id),
    ).fetchall()
    players: list[dict[str, Any]] = []
    for row in rows:
        player = dict(row)
        player_id = as_int(player["player_id"])
        group = position_group(player.get("position"))
        role_score = roles.get(player_id)
        overall = as_float(player.get("overall"), 50.0)
        potential = as_float(player.get("potential"), overall)
        effective_role = role_score if role_score is not None else overall
        dev_bonus = DEV_TRAIT_BONUS.get(str(player.get("dev_trait") or "Normal"), 0.0)
        injury = injuries.get(player_id, {})
        active = active_injuries.get(player_id, {})
        injury_games = as_float(injury.get("injury_games_missed"), 0.0)
        active_games = as_float(active.get("max_expected_games"), 0.0)
        current_score = round((overall * 0.70) + (effective_role * 0.30), 1)
        future_score = round(
            (overall * 0.45)
            + (effective_role * 0.20)
            + (potential * 0.25)
            + dev_bonus
            - min(8.0, injury_games * 0.08)
            - min(8.0, active_games * 0.8),
            1,
        )
        player.update(
            {
                "player_name": f"{player['first_name']} {player['last_name']}",
                "position_group": group,
                "role_score": round(role_score, 1) if role_score is not None else None,
                "current_score": current_score,
                "future_score": round(clamp(future_score, 0, 100), 1),
                "depth_rank": depth.get(player_id),
                "injury_count": as_int(injury.get("injury_count")),
                "major_injury_count": as_int(injury.get("major_injury_count")),
                "injury_games_missed": as_int(injury.get("injury_games_missed")),
                "recurrence_risk": as_float(injury.get("recurrence_risk")),
                "active_injury_count": as_int(active.get("active_injury_count")),
                "active_injury_labels": active.get("active_injury_labels"),
                "active_injury_expected_games": as_int(active.get("max_expected_games")),
                "cap_hit": as_int(player.get("cap_hit")),
                "dead_cap_if_cut_pre_june1": as_int(player.get("dead_cap_if_cut_pre_june1")),
                "aav": as_int(player.get("aav")),
                "end_year": as_int(player.get("end_year"), 0) or None,
                "age": as_int(player.get("age"), 0),
                "years_exp": as_int(player.get("years_exp"), 0),
                "is_rookie": as_int(player.get("is_rookie"), 0),
            }
        )
        players.append(player)
    return players


def score_priority(score: float) -> str:
    if score >= 72:
        return "urgent"
    if score >= 55:
        return "high"
    if score >= 38:
        return "medium"
    if score >= 22:
        return "low"
    return "monitor"


def room_summary(players: list[dict[str, Any]], season: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {group: [] for group in ROOM_TARGETS}
    for player in players:
        grouped.setdefault(str(player["position_group"]), []).append(player)

    needs: list[dict[str, Any]] = []
    surplus: list[dict[str, Any]] = []
    for group, config in ROOM_TARGETS.items():
        room = sorted(grouped.get(group, []), key=lambda p: (p["current_score"], p["future_score"]), reverse=True)
        starter_count = as_int(config["starters"], 1)
        ideal = as_int(config["ideal"], starter_count)
        max_count = as_int(config["max"], ideal + 1)
        starter_floor = as_float(config["starter_floor"], 70.0)
        depth_floor = as_float(config["depth_floor"], 60.0)
        starters = room[:starter_count]
        depth = room[starter_count:ideal]
        best_score = room[0]["current_score"] if room else 0.0
        starter_avg = avg([p["current_score"] for p in starters], default=0.0)
        depth_avg = avg([p["current_score"] for p in depth], default=0.0)
        role_avg = avg([as_float(p.get("role_score"), p["overall"]) for p in room], default=0.0)
        expiring_core = [
            p for p in starters
            if p.get("end_year") and as_int(p["end_year"]) <= season + 1
        ]
        aging_core = [
            p for p in starters
            if p["age"] >= (34 if group == "QB" else 30)
        ]
        active_injuries = [p for p in room if p.get("active_injury_count")]
        injury_history = [p for p in room if as_int(p.get("major_injury_count")) > 0 or as_int(p.get("injury_games_missed")) >= 8]

        drivers: list[str] = []
        score = 0.0
        if len(room) < starter_count:
            score += (starter_count - len(room)) * 28
            drivers.append("missing starter bodies")
        if len(room) < ideal:
            score += (ideal - len(room)) * 9
            drivers.append("thin depth")
        if starter_avg and starter_avg < starter_floor:
            score += (starter_floor - starter_avg) * 1.5
            drivers.append("starter quality gap")
        if not starter_avg and room:
            score += starter_floor * 0.6
            drivers.append("no trusted starter grade")
        if room and depth_avg and depth_avg < depth_floor:
            score += (depth_floor - depth_avg) * 0.8
            drivers.append("depth quality gap")
        if role_avg and role_avg < 68 and group not in {"K", "P", "LS"}:
            score += (68 - role_avg) * 0.7
            drivers.append("scheme fit gap")
        if expiring_core:
            score += min(18, len(expiring_core) * 7)
            drivers.append("contract cliff")
        if aging_core:
            score += min(14, len(aging_core) * 6)
            drivers.append("age curve risk")
        if active_injuries:
            score += min(18, len(active_injuries) * 6)
            drivers.append("active injury")
        elif injury_history:
            score += min(10, len(injury_history) * 3)
            drivers.append("injury recurrence risk")
        if group in {"QB", "OT", "EDGE", "CB", "WR", "IDL"} and best_score < starter_floor + 2:
            score += 7
            drivers.append("premium-position ceiling")

        score = round(clamp(score), 1)
        needs.append(
            {
                "position_group": group,
                "priority": score_priority(score),
                "need_score": score,
                "investment_tier": config["tier"],
                "player_count": len(room),
                "ideal_count": ideal,
                "max_count": max_count,
                "best_score": round(best_score, 1),
                "starter_avg": round(starter_avg, 1),
                "depth_avg": round(depth_avg, 1),
                "avg_role_score": round(role_avg, 1),
                "drivers": drivers or ["stable room"],
                "core_expiring": [
                    compact_player(p, include_contract=True)
                    for p in expiring_core[:3]
                ],
                "aging_core": [compact_player(p) for p in aging_core[:3]],
            }
        )

        surplus_players = room[max(starter_count, ideal - 1):]
        surplus_score = 0.0
        surplus_drivers: list[str] = []
        if len(room) > max_count:
            surplus_score += (len(room) - max_count) * 18
            surplus_drivers.append("over roster max")
        if len(room) > ideal and depth_avg >= depth_floor + 4:
            surplus_score += (len(room) - ideal) * 7
            surplus_drivers.append("tradable depth")
        if len(room) > ideal and any(p["age"] >= 29 and p["current_score"] < best_score - 5 for p in room):
            surplus_score += 8
            surplus_drivers.append("older depth can be replaced")
        if surplus_score > 0:
            surplus.append(
                {
                    "position_group": group,
                    "surplus_score": round(clamp(surplus_score), 1),
                    "player_count": len(room),
                    "ideal_count": ideal,
                    "max_count": max_count,
                    "drivers": surplus_drivers,
                    "candidate_players": [compact_player(p, include_contract=True) for p in surplus_players[:5]],
                }
            )

    needs.sort(key=lambda row: (-row["need_score"], row["position_group"]))
    surplus.sort(key=lambda row: (-row["surplus_score"], row["position_group"]))
    return needs, surplus


def compact_player(player: dict[str, Any], *, include_contract: bool = False) -> dict[str, Any]:
    value = {
        "player_id": as_int(player["player_id"]),
        "player_name": player["player_name"],
        "position": player["position"],
        "position_group": player["position_group"],
        "age": as_int(player["age"]),
        "status": player.get("status"),
        "overall": as_int(player.get("overall")),
        "potential": as_int(player.get("potential")),
        "current_score": player.get("current_score"),
        "future_score": player.get("future_score"),
        "dev_trait": player.get("dev_trait"),
    }
    if include_contract:
        value.update(
            {
                "end_year": player.get("end_year"),
                "cap_hit": player.get("cap_hit"),
                "aav": player.get("aav"),
                "dead_cap_if_cut_pre_june1": player.get("dead_cap_if_cut_pre_june1"),
            }
        )
    return value


def build_candidate_lists(
    players: list[dict[str, Any]],
    needs: list[dict[str, Any]],
    surplus: list[dict[str, Any]],
    season: int,
) -> dict[str, list[dict[str, Any]]]:
    surplus_groups = {row["position_group"]: row for row in surplus}
    need_by_group = {row["position_group"]: row for row in needs}
    cut_candidates: list[dict[str, Any]] = []
    ps_candidates: list[dict[str, Any]] = []
    extension_candidates: list[dict[str, Any]] = []
    trade_candidates: list[dict[str, Any]] = []
    contract_pressure: list[dict[str, Any]] = []

    for player in players:
        group = str(player["position_group"])
        current = as_float(player["current_score"])
        potential = as_float(player.get("potential"), current)
        future = as_float(player["future_score"], current)
        cap_hit = as_int(player.get("cap_hit"))
        dead_cap = as_int(player.get("dead_cap_if_cut_pre_june1"))
        end_year = player.get("end_year")
        age = as_int(player["age"])
        years_exp = as_int(player["years_exp"])
        status = str(player.get("status") or "Active")
        surplus_bonus = as_float(surplus_groups.get(group, {}).get("surplus_score"), 0.0) * 0.18
        need_penalty = as_float(need_by_group.get(group, {}).get("need_score"), 0.0) * 0.12

        release_score = (
            96
            - current
            - max(0, potential - current) * 0.9
            - (12 if years_exp <= 1 else 0)
            - (8 if status == "Practice Squad" else 0)
            + surplus_bonus
            + (5 if age >= 30 and current < 72 else 0)
            + (6 if cap_hit and dead_cap <= cap_hit * 0.25 else 0)
            - (min(18, dead_cap / 1_000_000) if dead_cap else 0)
            - need_penalty
        )
        if group in {"K", "P", "LS"} and current >= 66:
            release_score -= 20
        if current >= 78:
            release_score -= 35
        if release_score >= 25:
            reasons = []
            if group in surplus_groups:
                reasons.append("surplus room")
            if current < 64:
                reasons.append("low current grade")
            if potential <= current + 2:
                reasons.append("limited upside")
            if age >= 30:
                reasons.append("age")
            if dead_cap and cap_hit and dead_cap > cap_hit * 0.5:
                reasons.append("dead-cap warning")
            cut_candidates.append(candidate(player, release_score, reasons or ["bottom-roster review"], include_contract=True))

        ps_score = (
            max(0, potential - current) * 2.0
            + max(0, future - current)
            + (14 if years_exp <= 1 else 7 if years_exp == 2 else 0)
            + (8 if age <= 24 else 3 if age <= 26 else -8)
            + (8 if status == "Practice Squad" else 0)
            + min(8, as_float(player.get("role_score"), current) - 60)
            - max(0, current - 70) * 1.4
        )
        if years_exp >= 3 and 60 <= current <= 69 and age <= 31:
            ps_score += 10
        elif years_exp >= 3 and 69 < current < 74 and age <= 31:
            ps_score += 5
        if ps_score >= 18 and current < 74 and group not in {"K", "P"}:
            reasons = []
            if potential > current + 4:
                reasons.append("development upside")
            if years_exp <= 1:
                reasons.append("young player")
            if years_exp >= 3 and age <= 31:
                reasons.append("veteran injury call-up depth")
            if status == "Practice Squad":
                reasons.append("already on practice squad")
            ps_candidates.append(candidate(player, ps_score, reasons or ["stash candidate"], include_contract=False))

        expiring_soon = bool(end_year and as_int(end_year) <= season + 1)
        if expiring_soon and age <= 29 and current >= 74:
            extension_score = (
                current
                + max(0, potential - current) * 0.8
                + DEV_TRAIT_BONUS.get(str(player.get("dev_trait") or "Normal"), 0.0)
                - min(10, as_float(player.get("injury_games_missed")) * 0.08)
                - max(0, (cap_hit - 18_000_000) / 3_000_000)
            )
            reasons = ["expiring core player"]
            if group in {"QB", "WR", "OL", "EDGE", "IDL", "CB"}:
                reasons.append("premium position")
            elif group in {"K", "P", "LS"}:
                extension_score -= 18
                reasons.append("specialist priority discount")
            if player.get("dev_trait") in {"Star", "Superstar", "X-Factor"}:
                reasons.append("development trait")
            extension_candidates.append(candidate(player, extension_score, reasons, include_contract=True))

        pressure_score = 0.0
        pressure_reasons: list[str] = []
        if expiring_soon and current >= 68:
            pressure_score += 25 + max(0, current - 70)
            pressure_reasons.append("contract expiring soon")
        if cap_hit >= 10_000_000:
            pressure_score += min(28, cap_hit / 750_000)
            pressure_reasons.append("large cap hit")
        if age >= 30 and cap_hit >= 5_000_000:
            pressure_score += 12
            pressure_reasons.append("aging paid veteran")
        if pressure_score:
            contract_pressure.append(candidate(player, pressure_score, pressure_reasons, include_contract=True))

        trade_score = (
            surplus_bonus * 2.2
            + (12 if age >= 29 and cap_hit >= 4_000_000 else 0)
            + (10 if expiring_soon and current < 78 else 0)
            + (8 if current >= 64 else 0)
            - (20 if current >= 84 and age <= 28 else 0)
            - (14 if group == "QB" and current >= 74 else 0)
            - need_penalty
        )
        if trade_score >= 15:
            reasons = []
            if group in surplus_groups:
                reasons.append("surplus position")
            if expiring_soon:
                reasons.append("expiring contract")
            if age >= 29 and cap_hit >= 4_000_000:
                reasons.append("age/cap fit")
            trade_candidates.append(candidate(player, trade_score, reasons or ["market check"], include_contract=True))

    return {
        "cut_candidates": sorted(cut_candidates, key=lambda row: -row["score"])[:14],
        "practice_squad_priorities": sorted(ps_candidates, key=lambda row: -row["score"])[:14],
        "extension_candidates": sorted(extension_candidates, key=lambda row: -row["score"])[:12],
        "trade_block_candidates": sorted(trade_candidates, key=lambda row: -row["score"])[:12],
        "contract_pressure": sorted(contract_pressure, key=lambda row: -row["score"])[:12],
    }


def candidate(
    player: dict[str, Any],
    score: float,
    reasons: list[str],
    *,
    include_contract: bool,
) -> dict[str, Any]:
    value = compact_player(player, include_contract=include_contract)
    value["score"] = round(clamp(score), 1)
    value["reasons"] = reasons
    if player.get("active_injury_count"):
        value["active_injury"] = player.get("active_injury_labels")
        value["expected_games_missed"] = player.get("active_injury_expected_games")
    return value


def team_quality_metrics(
    players: list[dict[str, Any]],
    cap: dict[str, Any],
    record: dict[str, Any],
    counts: dict[str, Any],
) -> dict[str, Any]:
    controlled = [p for p in players if str(p.get("status") or "Active") != "Practice Squad"]
    sorted_players = sorted(controlled or players, key=lambda p: p["current_score"], reverse=True)
    top12_avg = avg([p["current_score"] for p in sorted_players[:12]], default=65.0)
    starter22_avg = avg([p["current_score"] for p in sorted_players[:22]], default=62.0)
    avg_age = avg([as_float(p["age"]) for p in sorted_players], default=27.0)
    premium_groups = {"QB", "WR", "OL", "EDGE", "IDL", "CB"}
    premium_best = []
    for group in premium_groups:
        group_players = [p for p in sorted_players if p["position_group"] == group]
        if group_players:
            premium_best.append(max(p["current_score"] for p in group_players))
    premium_core_avg = avg(premium_best, default=starter22_avg)

    salary_cap = as_int(cap.get("salary_cap"))
    cap_space = as_int(cap.get("cap_space"))
    cap_ratio = cap_space / salary_cap if salary_cap else 0.0
    if cap_space < 0:
        cap_band = "over_cap"
    elif cap_space < 7_500_000:
        cap_band = "critical"
    elif cap_space < 15_000_000:
        cap_band = "tight"
    elif cap_space < 35_000_000:
        cap_band = "workable"
    else:
        cap_band = "flexible"

    roster_quality_score = clamp((top12_avg - 58) * 2.2 + (starter22_avg - 58) * 1.2 + (premium_core_avg - 65) * 1.0)
    cap_health_score = clamp(45 + cap_ratio * 180)
    if cap_space < 0:
        cap_health_score = clamp(20 + cap_ratio * 100)
    age_curve_score = clamp(100 - abs(avg_age - 26.7) * 10)
    active_count = as_int(counts.get("active_roster_count"), len(controlled))
    practice_count = as_int(counts.get("practice_squad_count"), 0)

    wins = as_int(record.get("wins"))
    losses = as_int(record.get("losses"))
    ties = as_int(record.get("ties"))
    games = wins + losses + ties
    win_pct = ((wins + ties * 0.5) / games) if games else None
    record_score = clamp((win_pct or 0.5) * 100)
    record_weight = 0.25 if games >= 6 else 0.08
    competitiveness_score = clamp(
        roster_quality_score * (0.62 - record_weight / 2)
        + premium_core_avg * 0.22
        + cap_health_score * 0.08
        + age_curve_score * 0.08
        + record_score * record_weight
    )

    return {
        "top12_avg": round(top12_avg, 1),
        "starter22_avg": round(starter22_avg, 1),
        "premium_core_avg": round(premium_core_avg, 1),
        "avg_roster_age": round(avg_age, 1),
        "active_count": active_count,
        "practice_squad_count": practice_count,
        "cap_space": cap_space,
        "salary_cap": salary_cap,
        "cap_band": cap_band,
        "cap_space_display": money(cap_space),
        "roster_quality_score": round(roster_quality_score, 1),
        "cap_health_score": round(cap_health_score, 1),
        "age_curve_score": round(age_curve_score, 1),
        "competitiveness_score": round(competitiveness_score, 1),
        "record": {
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "games": games,
            "win_pct": round(win_pct, 3) if win_pct is not None else None,
        },
    }


def choose_team_phase(metrics: dict[str, Any], needs: list[dict[str, Any]]) -> dict[str, Any]:
    quality = as_float(metrics["roster_quality_score"])
    competitiveness = as_float(metrics["competitiveness_score"])
    cap_band = str(metrics["cap_band"])
    avg_age = as_float(metrics["avg_roster_age"])
    active_count = as_int(metrics["active_count"])
    urgent_needs = [row for row in needs if row["priority"] in {"urgent", "high"}]
    record_games = as_int(metrics["record"].get("games"))
    win_pct = metrics["record"].get("win_pct")

    if active_count >= 70:
        phase = "camp_evaluation"
        posture = "sort roster depth, protect youth, and avoid locking fringe veterans into future money"
    elif cap_band in {"over_cap", "critical"} and quality >= 62:
        phase = "cap_reset"
        posture = "preserve core players while clearing cap pressure and replacing expensive depth"
    elif competitiveness >= 74 and quality >= 66:
        phase = "contending"
        posture = "buy selectively at premium needs and protect the playoff roster from avoidable holes"
    elif avg_age <= 26.3 and quality >= 55:
        phase = "ascending_young_core"
        posture = "prioritize development, extensions, and premium-position draft solutions"
    elif competitiveness < 46 or (record_games >= 8 and win_pct is not None and win_pct < 0.35):
        phase = "rebuilding"
        posture = "sell non-core veterans, add picks, and keep snaps available for young players"
    elif urgent_needs or cap_band == "tight":
        phase = "retooling"
        posture = "patch the highest-leverage holes without spending future premium assets casually"
    else:
        phase = "balanced_competitive"
        posture = "stay disciplined, improve depth, and let value dictate aggressive moves"

    return {
        "team_phase": phase,
        "recommended_posture": posture,
        "phase_drivers": {
            "competitiveness_score": metrics["competitiveness_score"],
            "roster_quality_score": metrics["roster_quality_score"],
            "cap_band": cap_band,
            "avg_roster_age": metrics["avg_roster_age"],
            "urgent_or_high_needs": len(urgent_needs),
            "active_count": active_count,
        },
    }


def risk_flags(
    metrics: dict[str, Any],
    players: list[dict[str, Any]],
    needs: list[dict[str, Any]],
    candidate_lists: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    if metrics["cap_band"] in {"over_cap", "critical", "tight"}:
        flags.append(
            {
                "risk_type": "cap",
                "severity": "high" if metrics["cap_band"] in {"over_cap", "critical"} else "medium",
                "summary": f"Cap band is {metrics['cap_band']} with {metrics['cap_space_display']} available.",
            }
        )
    active_injuries = [p for p in players if p.get("active_injury_count")]
    if active_injuries:
        flags.append(
            {
                "risk_type": "injury",
                "severity": "high" if len(active_injuries) >= 4 else "medium",
                "summary": f"{len(active_injuries)} controlled players have active injuries.",
                "players": [compact_player(p) for p in active_injuries[:5]],
            }
        )
    aging_core = [
        p for p in sorted(players, key=lambda p: p["current_score"], reverse=True)[:22]
        if p["age"] >= (34 if p["position_group"] == "QB" else 30)
    ]
    if len(aging_core) >= 4:
        flags.append(
            {
                "risk_type": "age_curve",
                "severity": "medium",
                "summary": f"{len(aging_core)} top-22 players are on the older side of the age curve.",
                "players": [compact_player(p) for p in aging_core[:5]],
            }
        )
    urgent_needs = [row for row in needs if row["priority"] == "urgent"]
    if urgent_needs:
        flags.append(
            {
                "risk_type": "roster_need",
                "severity": "high",
                "summary": "Urgent need rooms should be protected from casual cuts or asset-light planning.",
                "rooms": urgent_needs[:4],
            }
        )
    if len(candidate_lists.get("contract_pressure", [])) >= 6:
        flags.append(
            {
                "risk_type": "contract_stack",
                "severity": "medium",
                "summary": "Multiple contracts require extension, trade, or replacement planning within the next two seasons.",
            }
        )
    return flags


def evaluate_team(
    con: sqlite3.Connection,
    *,
    team_abbr: str,
    season: int | None = None,
    game_id: str = "master",
    evaluation_date: str | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    season = season or current_season(con)
    evaluation_date = evaluation_date or current_date(con)
    team = get_team(con, team_abbr)
    team_id = as_int(team["team_id"])
    players = controlled_players(con, team_id, season)
    cap = cap_snapshot(con, team_id, season)
    counts = roster_counts(con, team_id)
    record = team_record(con, team_id, season)
    needs, surplus = room_summary(players, season)
    candidates = build_candidate_lists(players, needs, surplus, season)
    metrics = team_quality_metrics(players, cap, record, counts)
    phase = choose_team_phase(metrics, needs)
    top_needs = needs[:8]
    top_surplus = surplus[:6]
    flags = risk_flags(metrics, players, top_needs, candidates)
    summary = (
        f"{team['abbreviation']} profiles as {phase['team_phase']} "
        f"(competitiveness {metrics['competitiveness_score']}, cap {metrics['cap_band']}, "
        f"top needs: {', '.join(row['position_group'] for row in top_needs[:3]) or 'none'})."
    )
    evaluation = {
        "game_id": game_id,
        "season": season,
        "evaluation_date": evaluation_date,
        "team": {
            "team_id": team_id,
            "abbreviation": team["abbreviation"],
            "name": f"{team['city']} {team['nickname']}",
            "conference": team["conference"],
            "division": team["division"],
        },
        "summary": summary,
        "team_direction": phase,
        "metrics": metrics,
        "roster_needs": top_needs,
        "roster_surplus": top_surplus,
        "contract_pressure": candidates["contract_pressure"],
        "cut_candidates": candidates["cut_candidates"],
        "practice_squad_priorities": candidates["practice_squad_priorities"],
        "extension_candidates": candidates["extension_candidates"],
        "trade_block_candidates": candidates["trade_block_candidates"],
        "risk_flags": flags,
    }
    if persist:
        persist_evaluation(con, evaluation)
    return evaluation


def evaluate_league(
    con: sqlite3.Connection,
    *,
    season: int | None = None,
    game_id: str = "master",
    persist: bool = False,
) -> list[dict[str, Any]]:
    rows = con.execute("SELECT abbreviation FROM teams ORDER BY abbreviation").fetchall()
    return [
        evaluate_team(
            con,
            team_abbr=row["abbreviation"],
            season=season,
            game_id=game_id,
            persist=persist,
        )
        for row in rows
    ]


def persist_evaluation(con: sqlite3.Connection, evaluation: dict[str, Any]) -> int:
    ensure_schema(con)
    team = evaluation["team"]
    direction = evaluation["team_direction"]
    metrics = evaluation["metrics"]
    cur = con.execute(
        """
        INSERT INTO ai_gm_team_evaluations (
            game_id, team_id, season, evaluation_date, team_phase,
            competitiveness_score, roster_quality_score, cap_health_score,
            age_curve_score, evaluation_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evaluation["game_id"],
            as_int(team["team_id"]),
            as_int(evaluation["season"]),
            evaluation["evaluation_date"],
            direction["team_phase"],
            as_float(metrics["competitiveness_score"]),
            as_float(metrics["roster_quality_score"]),
            as_float(metrics["cap_health_score"]),
            as_float(metrics["age_curve_score"]),
            json_dumps(evaluation),
        ),
    )
    return as_int(cur.lastrowid)


def print_evaluation(evaluation: dict[str, Any], *, detail_limit: int = 5) -> None:
    team = evaluation["team"]["abbreviation"]
    direction = evaluation["team_direction"]
    metrics = evaluation["metrics"]
    print(f"{team}: {direction['team_phase']} | competitiveness {metrics['competitiveness_score']} | cap {metrics['cap_band']} ({metrics['cap_space_display']})")
    print(f"  Posture: {direction['recommended_posture']}")
    needs = evaluation["roster_needs"][:detail_limit]
    print("  Needs: " + ", ".join(f"{row['position_group']} {row['priority']}({row['need_score']})" for row in needs))
    surplus = evaluation["roster_surplus"][:detail_limit]
    if surplus:
        print("  Surplus: " + ", ".join(f"{row['position_group']}({row['surplus_score']})" for row in surplus))
    cuts = evaluation["cut_candidates"][:detail_limit]
    if cuts:
        print("  Cut watch: " + ", ".join(f"{row['player_name']} {row['position']}({row['score']})" for row in cuts))
    extensions = evaluation["extension_candidates"][:detail_limit]
    if extensions:
        print("  Extension watch: " + ", ".join(f"{row['player_name']} {row['position']}({row['score']})" for row in extensions))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic AI GM team evaluator.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--season", type=int)
    parser.add_argument("--game-id", default="master")
    parser.add_argument("--team", help="Team abbreviation. Omit with --all to evaluate every team.")
    parser.add_argument("--all", action="store_true", help="Evaluate all teams.")
    parser.add_argument("--persist", action="store_true", help="Store an evaluation snapshot in ai_gm_team_evaluations.")
    parser.add_argument("--json", action="store_true", help="Print full JSON.")
    parser.add_argument("--detail-limit", type=int, default=5)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.team and not args.all:
        raise SystemExit("Provide --team TEAM or --all.")
    con = connect(args.db)
    try:
        if args.all:
            evaluations = evaluate_league(
                con,
                season=args.season,
                game_id=args.game_id,
                persist=args.persist,
            )
            if args.json:
                print(json_dumps(evaluations))
            else:
                for evaluation in evaluations:
                    print_evaluation(evaluation, detail_limit=args.detail_limit)
            if args.persist:
                con.commit()
        else:
            evaluation = evaluate_team(
                con,
                team_abbr=args.team,
                season=args.season,
                game_id=args.game_id,
                persist=args.persist,
            )
            if args.json:
                print(json_dumps(evaluation))
            else:
                print_evaluation(evaluation, detail_limit=args.detail_limit)
            if args.persist:
                con.commit()
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
