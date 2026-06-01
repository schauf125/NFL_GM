#!/usr/bin/env python3
"""Audit whether CPU teams are maintaining coherent roster cores.

The report is intentionally football-ops focused: franchise quarterback
handling, aging-vet succession, rookie-starting paths, fifth-year options,
tag/extension pressure, and scheme-fit drift.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import contract_negotiations


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_SEASON = 2026

ACTIVE_STATUSES = {
    "Active",
    "Questionable",
    "Doubtful",
    "Out",
    "IR",
    "PUP",
    "NFI",
    "Suspended",
    "Reserve/Future",
    "Practice Squad",
}
PREMIUM_GROUPS = {"QB", "WR", "OL", "EDGE", "IDL", "CB"}
OFFENSE_GROUPS = {"QB", "RB", "WR", "TE", "OL"}
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


def money(value: Any) -> str:
    amount = as_int(value)
    if amount < 0:
        return "-" + money(abs(amount))
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    return f"${amount}"


def json_dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


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
    if row and row["setting_value"]:
        return str(row["setting_value"])
    return f"{current_season(con)}-06-01"


def active_game_id(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'active_game_id'"
    ).fetchone()
    return str(row["setting_value"]) if row and row["setting_value"] else "master"


def team_rows(con: sqlite3.Connection, team: str | None = None) -> list[sqlite3.Row]:
    if team:
        row = con.execute("SELECT * FROM teams WHERE abbreviation = ?", (team.upper(),)).fetchone()
        return [row] if row else []
    return con.execute("SELECT * FROM teams ORDER BY abbreviation").fetchall()


def team_scheme(con: sqlite3.Connection, team_id: int, season: int) -> dict[str, str]:
    if not table_exists(con, "team_scheme_identities_view"):
        return {}
    row = con.execute(
        """
        SELECT offense_scheme_key, defense_scheme_key, offense_scheme, defense_scheme
        FROM team_scheme_identities_view
        WHERE team_id = ?
          AND season = ?
        LIMIT 1
        """,
        (team_id, season),
    ).fetchone()
    return dict(row) if row else {}


def depth_rank_map(con: sqlite3.Connection, team_id: int) -> dict[int, int]:
    if not table_exists(con, "depth_charts"):
        return {}
    rows = con.execute(
        """
        SELECT player_id, MIN(depth_rank) AS depth_rank
        FROM depth_charts
        WHERE team_id = ?
        GROUP BY player_id
        """,
        (team_id,),
    ).fetchall()
    return {as_int(row["player_id"]): as_int(row["depth_rank"], 99) for row in rows}


def draft_pick_map(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    if not table_exists(con, "draft_picks"):
        return {}
    rows = con.execute(
        """
        SELECT selected_player_id, draft_year, round, pick_number, pick_in_round, original_team_id, current_team_id
        FROM draft_picks
        WHERE selected_player_id IS NOT NULL
        """
    ).fetchall()
    return {
        as_int(row["selected_player_id"]): {
            "draft_year": as_int(row["draft_year"]),
            "round": as_int(row["round"]),
            "pick_number": as_int(row["pick_number"]),
            "pick_in_round": as_int(row["pick_in_round"]),
            "original_team_id": as_int(row["original_team_id"]),
            "current_team_id": as_int(row["current_team_id"]),
        }
        for row in rows
        if row["selected_player_id"] is not None
    }


def role_score_maps(con: sqlite3.Connection, season: int) -> tuple[dict[tuple[int, str], float], dict[int, float]]:
    if not table_exists(con, "player_role_scores"):
        return {}, {}
    rows = con.execute(
        """
        SELECT player_id, scheme_key, MAX(role_score) AS role_score
        FROM player_role_scores
        WHERE season = ?
        GROUP BY player_id, scheme_key
        """,
        (season,),
    ).fetchall()
    by_scheme: dict[tuple[int, str], float] = {}
    best: dict[int, float] = {}
    for row in rows:
        player_id = as_int(row["player_id"])
        score = as_float(row["role_score"])
        by_scheme[(player_id, str(row["scheme_key"] or ""))] = score
        best[player_id] = max(best.get(player_id, 0.0), score)
    return by_scheme, best


def roster_players(
    con: sqlite3.Connection,
    *,
    team_id: int,
    season: int,
    scheme: dict[str, str],
    draft_map: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    depth = depth_rank_map(con, team_id)
    by_scheme, best_role = role_score_maps(con, season)
    rows = con.execute(
        """
        WITH active_contract_years AS (
            SELECT *
            FROM (
                SELECT
                    cy.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY cy.player_id, cy.team_id, cy.season
                        ORDER BY cy.cap_hit DESC, cy.contract_id DESC
                    ) AS rn
                FROM contract_years cy
                WHERE cy.season = ?
                  AND COALESCE(cy.is_active, 1) = 1
            )
            WHERE rn = 1
        )
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.years_exp,
            p.overall,
            p.potential,
            p.dev_trait,
            p.status,
            p.is_rookie,
            c.contract_type,
            c.end_year,
            (
                SELECT MAX(c2.end_year)
                FROM contracts c2
                WHERE c2.player_id = p.player_id
                  AND c2.team_id = p.team_id
                  AND c2.is_active = 1
            ) AS control_end_year,
            c.aav,
            cy.cap_hit,
            cy.dead_cap_if_cut_pre_june1
        FROM players p
        LEFT JOIN contracts c
          ON c.player_id = p.player_id
         AND c.team_id = p.team_id
         AND c.is_active = 1
         AND COALESCE(c.start_year, ?) <= ?
         AND COALESCE(c.end_year, ?) >= ?
        LEFT JOIN active_contract_years cy
          ON cy.contract_id = c.contract_id
         AND cy.player_id = p.player_id
         AND cy.team_id = p.team_id
        WHERE p.team_id = ?
          AND COALESCE(p.status, 'Active') NOT IN ('Free Agent', 'Retired')
        ORDER BY COALESCE(p.overall, 0) DESC, COALESCE(p.potential, 0) DESC, player_name
        """,
        (season, season, season, season, season, team_id),
    ).fetchall()
    players: list[dict[str, Any]] = []
    for row in rows:
        player = dict(row)
        player_id = as_int(player["player_id"])
        group = position_group(player.get("position"))
        scheme_key = scheme.get("offense_scheme_key") if group in OFFENSE_GROUPS else scheme.get("defense_scheme_key")
        scheme_score = by_scheme.get((player_id, str(scheme_key or "")), best_role.get(player_id))
        player.update(
            {
                "position_group": group,
                "overall": as_int(player.get("overall"), 50),
                "potential": as_int(player.get("potential"), as_int(player.get("overall"), 50)),
                "age": as_int(player.get("age"), 0),
                "years_exp": as_int(player.get("years_exp"), 0),
                "end_year": as_int(player.get("control_end_year"), as_int(player.get("end_year"), 0)) or player.get("end_year"),
                "depth_rank": depth.get(player_id),
                "scheme_role_score": round(scheme_score, 1) if scheme_score is not None else None,
                "best_role_score": round(best_role.get(player_id), 1) if player_id in best_role else None,
                "draft": draft_map.get(player_id),
            }
        )
        players.append(player)
    return players


def compact_player(player: dict[str, Any]) -> dict[str, Any]:
    draft = player.get("draft") or {}
    value = {
        "player_id": as_int(player.get("player_id")),
        "player_name": player.get("player_name"),
        "position": player.get("position"),
        "age": as_int(player.get("age")),
        "overall": as_int(player.get("overall")),
        "potential": as_int(player.get("potential")),
        "status": player.get("status"),
        "depth_rank": player.get("depth_rank"),
        "end_year": player.get("end_year"),
        "cap_hit": as_int(player.get("cap_hit")),
        "aav": as_int(player.get("aav")),
    }
    if draft:
        value["draft"] = {
            "year": draft.get("draft_year"),
            "round": draft.get("round"),
            "pick": draft.get("pick_number"),
        }
    return value


def extension_watchlist(players: list[dict[str, Any]], season: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for player in players:
        group = str(player["position_group"])
        end_year = as_int(player.get("end_year"), 9999)
        if end_year > season + 1:
            continue
        overall = as_int(player["overall"])
        potential = as_int(player["potential"], overall)
        age = as_int(player["age"], 27)
        role = as_float(player.get("scheme_role_score"), overall)
        if group == "QB":
            keep = age <= 34 and (overall >= 76 or (overall >= 70 and potential >= 84) or potential >= 88)
        elif group in PREMIUM_GROUPS:
            keep = age <= 29 and (overall >= 74 or (overall >= 70 and potential >= 84) or potential >= 88)
        elif group in {"TE", "S", "LB"}:
            keep = age <= 30 and (overall >= 78 or potential >= 86)
        else:
            keep = overall >= 82
        if keep or (overall >= 70 and role >= overall + 4):
            result.append(player)
    result.sort(key=lambda p: (as_int(p["overall"]), as_int(p["potential"]), -as_int(p["age"])), reverse=True)
    return result


def fifth_year_candidates(con: sqlite3.Connection, team_id: int, season: int) -> list[dict[str, Any]]:
    try:
        return contract_negotiations.fifth_year_option_candidates(con, team_id, season + 1)
    except Exception:
        return []


def tag_candidates(con: sqlite3.Connection, team_id: int, season: int) -> list[dict[str, Any]]:
    try:
        rows = contract_negotiations.tag_eligible_players(con, team_id, season)
    except Exception:
        return []
    return [row for row in rows if row.get("tag_eligible")]


def issue(team: str, severity: str, area: str, summary: str, recommendation: str, **extra: Any) -> dict[str, Any]:
    return {
        "team": team,
        "severity": severity,
        "area": area,
        "summary": summary,
        "recommendation": recommendation,
        **extra,
    }


def audit_qb_continuity(team: sqlite3.Row, players: list[dict[str, Any]], season: int) -> list[dict[str, Any]]:
    abbr = str(team["abbreviation"])
    qbs = sorted(
        [p for p in players if p["position_group"] == "QB" and p.get("status") in ACTIVE_STATUSES],
        key=lambda p: (as_int(p["overall"]), as_int(p["potential"]), -as_int(p["age"])),
        reverse=True,
    )
    if not qbs:
        return [
            issue(
                abbr,
                "high",
                "franchise_qb",
                "No controlled quarterback is on the active roster tree.",
                "Prioritize a starter-quality QB solution before other non-premium needs.",
            )
        ]
    best = qbs[0]
    overall = as_int(best["overall"])
    potential = as_int(best["potential"], overall)
    age = as_int(best["age"], 27)
    end_year = as_int(best.get("end_year"), 0)
    franchise = overall >= 84 or (overall >= 80 and potential >= 88) or (age <= 26 and potential >= 90)
    bridge_only = overall < 76 and potential < 84
    protected = bool(end_year and end_year > season + 1)
    results: list[dict[str, Any]] = []
    if franchise and not protected:
        severity = "high" if not end_year or end_year <= season else "medium"
        results.append(
            issue(
                abbr,
                severity,
                "franchise_qb",
                f"{best['player_name']} looks like a franchise QB but is not protected beyond {season + 1}.",
                "Push extension, fifth-year option, or tag planning ahead of open-market spending.",
                players=[compact_player(best)],
            )
        )
    if bridge_only:
        results.append(
            issue(
                abbr,
                "medium",
                "franchise_qb",
                f"QB room is bridge-level; best option is {best['player_name']} ({overall}/{potential}).",
                "Keep veteran spending short and prioritize draft/scouting QB pathways.",
                players=[compact_player(p) for p in qbs[:3]],
            )
        )
    expensive_depth = [
        p for p in qbs[1:]
        if as_int(p.get("aav")) >= 8_000_000 and as_int(p["overall"]) <= overall - 4
    ]
    if expensive_depth:
        results.append(
            issue(
                abbr,
                "medium",
                "franchise_qb",
                "Quarterback room has expensive depth behind a better incumbent.",
                "Avoid stacking starter-priced backups unless the team is intentionally bridging.",
                players=[compact_player(p) for p in [best, *expensive_depth[:2]]],
            )
        )
    return results


def audit_rookie_paths(team: sqlite3.Row, players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    abbr = str(team["abbreviation"])
    results: list[dict[str, Any]] = []
    by_group: dict[str, list[dict[str, Any]]] = {}
    for player in players:
        by_group.setdefault(str(player["position_group"]), []).append(player)
    for group_players in by_group.values():
        group_players.sort(key=lambda p: (as_int(p["overall"]), as_float(p.get("scheme_role_score"), p["overall"])), reverse=True)
    for player in players:
        draft = player.get("draft") or {}
        if not draft or as_int(draft.get("round"), 99) > 2:
            continue
        if as_int(player.get("years_exp")) > 1 and not as_int(player.get("is_rookie")):
            continue
        group = str(player["position_group"])
        rank_in_room = 1 + next(
            (idx for idx, p in enumerate(by_group.get(group, [])) if as_int(p["player_id"]) == as_int(player["player_id"])),
            99,
        )
        depth_rank = as_int(player.get("depth_rank"), 99)
        overall = as_int(player["overall"])
        potential = as_int(player["potential"], overall)
        first_round = as_int(draft.get("round")) == 1
        if group == "QB":
            if first_round and potential >= 84 and rank_in_room <= 2 and depth_rank > 2:
                results.append(
                    issue(
                        abbr,
                        "medium",
                        "rookie_starter_path",
                        f"First-round QB {player['player_name']} has a blocked depth path.",
                        "Give the rookie a clear QB2 or planned takeover/development path.",
                        players=[compact_player(player)],
                    )
                )
        elif potential >= 80 and overall >= 68 and depth_rank > 3 and rank_in_room <= 3:
            results.append(
                issue(
                    abbr,
                    "low",
                    "rookie_starter_path",
                    f"Day-one/two rookie {player['player_name']} has starter-track traits but is buried.",
                    "Rebuild depth charts after draft/free agency so high-investment rookies get role access.",
                    players=[compact_player(player)],
                )
            )
    return results


def audit_aging_core(team: sqlite3.Row, players: list[dict[str, Any]], season: int) -> list[dict[str, Any]]:
    abbr = str(team["abbreviation"])
    controlled = [p for p in players if p.get("status") != "Practice Squad" and p["position_group"] not in {"K", "P", "LS"}]
    top22 = sorted(controlled, key=lambda p: as_int(p["overall"]), reverse=True)[:22]
    aging = [
        p for p in top22
        if as_int(p["age"]) >= (35 if p["position_group"] == "QB" else 30)
    ]
    results: list[dict[str, Any]] = []
    if len(aging) >= 5:
        results.append(
            issue(
                abbr,
                "medium",
                "aging_vets",
                f"{len(aging)} top-22 players are past the normal age curve.",
                "Keep succession plans warm and avoid tagging older non-QB veterans.",
                players=[compact_player(p) for p in aging[:6]],
            )
        )
    expensive_aging = [
        p for p in aging
        if as_int(p.get("cap_hit")) >= 12_000_000
        and as_int(p.get("end_year"), season + 9) <= season + 1
        and p["position_group"] not in {"QB", "OL"}
    ]
    if expensive_aging:
        results.append(
            issue(
                abbr,
                "low",
                "aging_vets",
                "Aging expensive core pieces are close to contract decisions.",
                "Prefer short extensions or replacement planning unless the player is still elite.",
                players=[compact_player(p) for p in expensive_aging[:4]],
            )
        )
    return results


def audit_scheme_continuity(team: sqlite3.Row, players: list[dict[str, Any]], scheme: dict[str, str]) -> list[dict[str, Any]]:
    abbr = str(team["abbreviation"])
    if not scheme:
        return []
    core = sorted(
        [p for p in players if p["position_group"] not in {"K", "P", "LS"} and p.get("status") != "Practice Squad"],
        key=lambda p: as_int(p["overall"]),
        reverse=True,
    )[:16]
    role_scores = [as_float(p.get("scheme_role_score"), 0.0) for p in core if p.get("scheme_role_score") is not None]
    if not role_scores:
        return []
    weak_fits = [
        p for p in core
        if p.get("scheme_role_score") is not None
        and as_float(p.get("scheme_role_score")) <= as_int(p.get("overall")) - 7
        and as_int(p.get("overall")) >= 72
    ]
    avg_role = sum(role_scores) / len(role_scores)
    results: list[dict[str, Any]] = []
    if avg_role < 68 or len(weak_fits) >= 4:
        results.append(
            issue(
                abbr,
                "medium",
                "scheme_core",
                f"Core scheme fit is soft for {scheme.get('offense_scheme')} / {scheme.get('defense_scheme')}.",
                "Favor extensions and acquisitions that fit the installed scheme identity.",
                scheme={
                    "offense": scheme.get("offense_scheme"),
                    "defense": scheme.get("defense_scheme"),
                    "avg_core_role_score": round(avg_role, 1),
                },
                players=[compact_player(p) | {"scheme_role_score": p.get("scheme_role_score")} for p in weak_fits[:5]],
            )
        )
    return results


def audit_contract_controls(
    con: sqlite3.Connection,
    team: sqlite3.Row,
    players: list[dict[str, Any]],
    season: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    abbr = str(team["abbreviation"])
    team_id = as_int(team["team_id"])
    extensions = extension_watchlist(players, season)
    tags = tag_candidates(con, team_id, season)
    options = fifth_year_candidates(con, team_id, season)
    results: list[dict[str, Any]] = []
    option_exercises = [row for row in options if str(row.get("recommendation")) == "Exercise"]
    if option_exercises:
        results.append(
            issue(
                abbr,
                "low",
                "fifth_year_options",
                f"{len(option_exercises)} fifth-year option candidate(s) should be exercised.",
                "Handle options before free agency so the team does not leak first-round value.",
                players=[
                    {
                        "player_id": as_int(row.get("player_id")),
                        "player_name": row.get("player_name"),
                        "position": row.get("position"),
                        "market_score": row.get("market_score"),
                        "potential": row.get("potential"),
                        "option_salary": row.get("option_salary"),
                    }
                    for row in option_exercises[:5]
                ],
            )
        )
    unprotected_extensions = [
        p for p in extensions
        if as_int(p.get("end_year"), 9999) <= season + 1
        and p["position_group"] in {"QB", "WR", "OL", "EDGE", "IDL", "CB"}
    ]
    if unprotected_extensions:
        results.append(
            issue(
                abbr,
                "medium",
                "extensions_tags",
                f"{len(unprotected_extensions)} premium core player(s) need extension/tag decisions soon.",
                "Prioritize these players before spending on outside free agents.",
                players=[compact_player(p) for p in unprotected_extensions[:5]],
            )
        )
    tag_rows = [
        {
            "player_id": as_int(row.get("player_id")),
            "player_name": row.get("player_name"),
            "position": row.get("position"),
            "market_score": row.get("market_score"),
            "franchise_tag_aav": row.get("franchise_tag_aav"),
            "transition_tag_aav": row.get("transition_tag_aav"),
            "recommendation": row.get("tag_recommendation"),
        }
        for row in tags[:5]
    ]
    summary = {
        "extension_watchlist": [compact_player(p) for p in extensions[:6]],
        "tag_candidates": tag_rows,
        "fifth_year_options": option_exercises[:6],
    }
    return results, summary


def audit_team(con: sqlite3.Connection, team: sqlite3.Row, season: int, draft_map: dict[int, dict[str, Any]]) -> dict[str, Any]:
    scheme = team_scheme(con, as_int(team["team_id"]), season)
    players = roster_players(
        con,
        team_id=as_int(team["team_id"]),
        season=season,
        scheme=scheme,
        draft_map=draft_map,
    )
    issues: list[dict[str, Any]] = []
    contract_issues, contract_summary = audit_contract_controls(con, team, players, season)
    issues.extend(audit_qb_continuity(team, players, season))
    issues.extend(audit_aging_core(team, players, season))
    issues.extend(audit_rookie_paths(team, players))
    issues.extend(audit_scheme_continuity(team, players, scheme))
    issues.extend(contract_issues)
    severity_weight = {"high": 14, "medium": 7, "low": 3}
    score = 100 - sum(severity_weight.get(str(item["severity"]), 4) for item in issues)
    qbs = [p for p in players if p["position_group"] == "QB"]
    top_qb = sorted(qbs, key=lambda p: (as_int(p["overall"]), as_int(p["potential"])), reverse=True)[:1]
    return {
        "team": {
            "team_id": as_int(team["team_id"]),
            "abbreviation": team["abbreviation"],
            "name": f"{team['city']} {team['nickname']}",
        },
        "season": season,
        "score": round(clamp(score), 1),
        "issue_count": len(issues),
        "severity_counts": {
            severity: sum(1 for item in issues if item["severity"] == severity)
            for severity in ("high", "medium", "low")
        },
        "qb_summary": [compact_player(p) for p in top_qb],
        "contract_controls": contract_summary,
        "scheme": {
            "offense": scheme.get("offense_scheme"),
            "defense": scheme.get("defense_scheme"),
        },
        "issues": issues,
    }


def ensure_audit_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS cpu_roster_continuity_audits (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            audit_date TEXT NOT NULL,
            team_id INTEGER,
            team_abbr TEXT,
            continuity_score REAL NOT NULL,
            issue_count INTEGER NOT NULL,
            high_count INTEGER NOT NULL,
            medium_count INTEGER NOT NULL,
            low_count INTEGER NOT NULL,
            summary_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_cpu_roster_continuity_audits_team
            ON cpu_roster_continuity_audits(game_id, season, team_id, audit_id DESC)
        """
    )


def persist_report(con: sqlite3.Connection, report: dict[str, Any]) -> None:
    ensure_audit_schema(con)
    game_id = report["game_id"]
    season = as_int(report["season"])
    audit_date = report["audit_date"]
    for team in report["teams"]:
        counts = team["severity_counts"]
        con.execute(
            """
            INSERT INTO cpu_roster_continuity_audits (
                game_id, season, audit_date, team_id, team_abbr, continuity_score,
                issue_count, high_count, medium_count, low_count, summary_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_id,
                season,
                audit_date,
                as_int(team["team"]["team_id"]),
                team["team"]["abbreviation"],
                as_float(team["score"]),
                as_int(team["issue_count"]),
                as_int(counts.get("high")),
                as_int(counts.get("medium")),
                as_int(counts.get("low")),
                json_dumps(team),
            ),
        )


def build_report(con: sqlite3.Connection, *, season: int | None, team: str | None) -> dict[str, Any]:
    target_season = season or current_season(con)
    draft_map = draft_pick_map(con)
    teams = [
        audit_team(con, row, target_season, draft_map)
        for row in team_rows(con, team)
    ]
    teams.sort(key=lambda item: (item["score"], -item["issue_count"], item["team"]["abbreviation"]))
    league_score = round(sum(as_float(item["score"]) for item in teams) / len(teams), 1) if teams else 0.0
    issues = [issue for item in teams for issue in item["issues"]]
    return {
        "game_id": active_game_id(con),
        "season": target_season,
        "audit_date": current_date(con),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "league_score": league_score,
        "team_count": len(teams),
        "issue_count": len(issues),
        "severity_counts": {
            severity: sum(1 for item in issues if item["severity"] == severity)
            for severity in ("high", "medium", "low")
        },
        "teams": teams,
    }


def print_report(report: dict[str, Any], limit: int) -> None:
    print(
        f"CPU roster continuity audit: league score {report['league_score']} "
        f"across {report['team_count']} team(s), {report['issue_count']} issue(s)."
    )
    counts = report["severity_counts"]
    print(f"Severity: high {counts['high']}, medium {counts['medium']}, low {counts['low']}")
    printed = 0
    for team in report["teams"]:
        if printed >= limit:
            break
        if not team["issues"]:
            continue
        print(f"\n{team['team']['abbreviation']} score {team['score']} ({team['issue_count']} issue(s))")
        for item in team["issues"]:
            if printed >= limit:
                break
            print(f"  [{item['severity']}] {item['area']}: {item['summary']}")
            print(f"      -> {item['recommendation']}")
            printed += 1
    if printed == 0:
        print("No continuity issues found.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--season", type=int)
    parser.add_argument("--team", help="Audit one team abbreviation.")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--persist", action="store_true", help="Persist team audit summaries to the database.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if high-severity issues are found.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with connect(args.db) as con:
        report = build_report(con, season=args.season, team=args.team)
        if args.persist:
            persist_report(con, report)
            con.commit()
    if args.json:
        print(json_dumps(report))
    else:
        print_report(report, args.limit)
    return 1 if args.strict and report["severity_counts"]["high"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
