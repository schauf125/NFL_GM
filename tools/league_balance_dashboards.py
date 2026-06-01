#!/usr/bin/env python3
"""League balance snapshots for long-running saves.

These dashboards sit next to the talent supply guardrails. They are intentionally
lightweight: each category can be calculated from the live save, and yearly
rollover can persist the same read so long saves get a trend line.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import depth_packages  # noqa: E402


SOURCE = "league_balance_dashboards"
DEFAULT_LEAGUE_YEAR = 2026
MILLION = 1_000_000


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone() is not None


def current_league_year(con: sqlite3.Connection) -> int:
    if table_exists(con, "game_settings"):
        for key in ("current_league_year", "current_season"):
            row = con.execute(
                "SELECT setting_value FROM game_settings WHERE setting_key = ?",
                (key,),
            ).fetchone()
            if row and row["setting_value"]:
                return int(row["setting_value"])
    return DEFAULT_LEAGUE_YEAR


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS league_balance_dashboard_snapshots (
            league_year INTEGER NOT NULL,
            category TEXT NOT NULL,
            metric_key TEXT NOT NULL,
            metric_label TEXT NOT NULL,
            metric_value REAL,
            metric_text TEXT,
            warning_level TEXT NOT NULL DEFAULT 'ok',
            summary_json TEXT,
            source TEXT NOT NULL DEFAULT 'league_balance_dashboards',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (league_year, category, metric_key)
        );

        CREATE TABLE IF NOT EXISTS league_balance_dashboard_flags (
            flag_id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_year INTEGER NOT NULL,
            category TEXT NOT NULL,
            severity TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 100,
            metric_key TEXT,
            current_value REAL,
            message TEXT NOT NULL,
            related_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            related_player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            fingerprint TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL DEFAULT 'league_balance_dashboards',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        DROP VIEW IF EXISTS league_balance_dashboard_flags_view;
        CREATE VIEW league_balance_dashboard_flags_view AS
        SELECT
            f.*,
            t.abbreviation AS team,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position AS player_position,
            CASE f.severity
                WHEN 'critical' THEN 1
                WHEN 'warning' THEN 2
                ELSE 3
            END AS severity_order
        FROM league_balance_dashboard_flags f
        LEFT JOIN teams t ON t.team_id = f.related_team_id
        LEFT JOIN players p ON p.player_id = f.related_player_id;
        """
    )


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def round1(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 1)


def money_text(value: float | int | None) -> str:
    amount = float(value or 0)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= MILLION:
        return f"{sign}${amount / MILLION:.1f}M"
    return f"{sign}${amount:,.0f}"


def pct_text(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.1f}%"


def avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def row_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def team_name(row: sqlite3.Row | dict[str, Any]) -> str:
    city = row["city"] if isinstance(row, sqlite3.Row) else row.get("city")
    nickname = row["nickname"] if isinstance(row, sqlite3.Row) else row.get("nickname")
    return f"{city or ''} {nickname or ''}".strip()


def metric(key: str, label: str, value: float | int | None, text: str, detail: str = "", tone: str = "ok") -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": value,
        "text": text,
        "detail": detail,
        "tone": tone,
    }


def flag(category: str, severity: str, message: str, *, metric_key: str = "", current_value: float | None = None,
         team_id: int | None = None, player_id: int | None = None, sort_order: int = 100) -> dict[str, Any]:
    return {
        "category": category,
        "severity": severity,
        "message": message,
        "metric_key": metric_key,
        "current_value": current_value,
        "team_id": team_id,
        "player_id": player_id,
        "sort_order": sort_order,
    }


def latest_cap_rows(con: sqlite3.Connection, league_year: int) -> list[dict[str, Any]]:
    rows: list[sqlite3.Row] = []
    if table_exists(con, "team_cap_ledger_latest_view"):
        rows = con.execute(
            """
            SELECT s.*, t.abbreviation, t.city, t.nickname, t.conference, t.division
            FROM team_cap_ledger_latest_view s
            JOIN teams t ON t.team_id = s.team_id
            WHERE s.season = ?
            ORDER BY t.abbreviation
            """,
            (league_year,),
        ).fetchall()
    if not rows and table_exists(con, "team_cap_view"):
        rows = con.execute(
            """
            SELECT *
            FROM team_cap_view
            ORDER BY abbreviation
            """
        ).fetchall()
    return [dict(row) for row in rows]


def build_cap_health(con: sqlite3.Connection, league_year: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = latest_cap_rows(con, league_year)
    flags: list[dict[str, Any]] = []
    if not rows:
        return {
            "key": "capHealth",
            "title": "Cap Health",
            "subtitle": "No cap snapshot found",
            "metrics": [],
            "rows": [],
            "secondaryRows": [],
        }, flags
    for row in rows:
        cap = max(1, as_float(row.get("salary_cap")))
        row["cap_space_pct"] = round(as_float(row.get("cap_space")) * 100.0 / cap, 1)
        row["dead_cap_pct"] = round(as_float(row.get("dead_cap_charges")) * 100.0 / cap, 1)
        row["teamName"] = team_name(row)
    team_count = len(rows)
    cap_spaces = [as_float(row.get("cap_space")) for row in rows]
    dead_pcts = [as_float(row.get("dead_cap_pct")) for row in rows]
    negative = [row for row in rows if as_float(row.get("cap_space")) < 0]
    tight = [row for row in rows if 0 <= as_float(row.get("cap_space")) < 5 * MILLION]
    healthy = [row for row in rows if as_float(row.get("cap_space")) >= 20 * MILLION]
    high_dead = [row for row in rows if as_float(row.get("dead_cap_pct")) >= 12.0 or as_float(row.get("dead_cap_charges")) >= 35 * MILLION]
    if len(negative) >= 4:
        flags.append(flag("capHealth", "critical", f"{len(negative)} teams are over the cap.", metric_key="negative_teams", current_value=len(negative), sort_order=10))
    elif negative:
        flags.append(flag("capHealth", "warning", f"{len(negative)} teams are over the cap.", metric_key="negative_teams", current_value=len(negative), sort_order=20))
    if len(high_dead) >= 6:
        flags.append(flag("capHealth", "warning", f"{len(high_dead)} teams carry heavy dead-cap loads.", metric_key="high_dead_teams", current_value=len(high_dead), sort_order=30))
    avg_space = avg(cap_spaces)
    if avg_space is not None and avg_space < 5 * MILLION:
        flags.append(flag("capHealth", "warning", "Average league cap room is very tight.", metric_key="avg_cap_space", current_value=avg_space, sort_order=40))
    return {
        "key": "capHealth",
        "title": "Cap Health",
        "subtitle": f"{league_year} latest cap view",
        "metrics": [
            metric("avg_cap_space", "Avg Cap Room", avg_space, money_text(avg_space), "League average", "warn" if avg_space is not None and avg_space < 5 * MILLION else "ok"),
            metric("negative_teams", "Over Cap", len(negative), str(len(negative)), "Teams below zero", "bad" if negative else "ok"),
            metric("tight_teams", "Tight Teams", len(tight), str(len(tight)), "Under $5M room", "warn" if len(tight) >= 6 else "ok"),
            metric("healthy_teams", "Flexible", len(healthy), str(len(healthy)), "$20M+ room"),
            metric("avg_dead_cap_pct", "Dead Cap", avg(dead_pcts), pct_text(avg(dead_pcts)), "Avg of team cap"),
        ],
        "rows": sorted(rows, key=lambda row: as_float(row.get("cap_space")))[:10],
        "secondaryRows": sorted(high_dead, key=lambda row: as_float(row.get("dead_cap_pct")), reverse=True)[:10],
        "counts": {"teams": team_count, "negative": len(negative), "tight": len(tight), "healthy": len(healthy), "highDead": len(high_dead)},
    }, flags


def starting_qb_rows(con: sqlite3.Connection) -> list[dict[str, Any]]:
    if not table_exists(con, "depth_charts"):
        return []
    rows = con.execute(
        """
        SELECT
            t.team_id,
            t.abbreviation,
            t.city,
            t.nickname,
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.years_exp,
            p.overall,
            p.potential,
            p.status
        FROM teams t
        LEFT JOIN depth_charts dc
          ON dc.team_id = t.team_id
         AND dc.position = 'QB'
         AND dc.depth_rank = 1
        LEFT JOIN players p ON p.player_id = dc.player_id
        ORDER BY t.abbreviation
        """
    ).fetchall()
    return [dict(row) | {"teamName": team_name(row)} for row in rows]


def build_qb_supply(con: sqlite3.Connection, league_year: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    starters = starting_qb_rows(con)
    all_qbs = []
    if table_exists(con, "players"):
        all_qbs = [
            dict(row)
            for row in con.execute(
                """
                SELECT player_id, first_name || ' ' || last_name AS player_name, team_id, age, years_exp, overall, potential, status
                FROM players
                WHERE position = 'QB'
                  AND COALESCE(status, 'Active') != 'Retired'
                  AND overall IS NOT NULL
                """
            ).fetchall()
        ]
    franchise = [row for row in starters if as_float(row.get("overall")) >= 85 or (as_float(row.get("potential")) >= 88 and as_int(row.get("age"), 99) <= 28)]
    playable = [row for row in starters if as_float(row.get("overall")) >= 75]
    bridge = [row for row in starters if 68 <= as_float(row.get("overall")) < 75 or (as_int(row.get("age")) >= 34 and as_float(row.get("overall")) < 82)]
    problem = [row for row in starters if row.get("player_id") is None or as_float(row.get("overall")) < 68]
    young_upside = [row for row in starters if as_int(row.get("age"), 99) <= 26 and (as_float(row.get("overall")) >= 78 or as_float(row.get("potential")) >= 85)]
    flags: list[dict[str, Any]] = []
    if len(problem) >= 7:
        flags.append(flag("qbSupply", "critical", f"{len(problem)} teams have sub-bridge starting QB situations.", metric_key="problem_qb_teams", current_value=len(problem), sort_order=10))
    elif len(problem) >= 4:
        flags.append(flag("qbSupply", "warning", f"{len(problem)} teams have sub-bridge starting QB situations.", metric_key="problem_qb_teams", current_value=len(problem), sort_order=20))
    if len(franchise) < 9:
        flags.append(flag("qbSupply", "warning", "Franchise QB supply is thin relative to league demand.", metric_key="franchise_qbs", current_value=len(franchise), sort_order=30))
    active_qb_75 = sum(1 for row in all_qbs if as_float(row.get("overall")) >= 75)
    if active_qb_75 < 32:
        flags.append(flag("qbSupply", "warning", "There are fewer than 32 playable QBs in the active player pool.", metric_key="active_qb_75", current_value=active_qb_75, sort_order=40))
    for row in starters:
        row["supply_label"] = "Franchise" if row in franchise else "Starter" if row in playable else "Bridge" if row in bridge else "Problem"
    return {
        "key": "qbSupply",
        "title": "QB Supply",
        "subtitle": f"{len(starters)} team QB rooms",
        "metrics": [
            metric("franchise_qbs", "Franchise", len(franchise), str(len(franchise)), "Starter or high-upside young QBs", "warn" if len(franchise) < 9 else "ok"),
            metric("playable_starters", "Playable", len(playable), str(len(playable)), "75+ starting QBs"),
            metric("bridge_qbs", "Bridge", len(bridge), str(len(bridge)), "Short-term starter tier"),
            metric("problem_qb_teams", "Problem Rooms", len(problem), str(len(problem)), "Below bridge quality", "bad" if len(problem) >= 7 else "warn" if len(problem) >= 4 else "ok"),
            metric("young_upside_qbs", "Young Upside", len(young_upside), str(len(young_upside)), "Age 26 or younger"),
        ],
        "rows": sorted(starters, key=lambda row: (as_float(row.get("overall")), as_float(row.get("potential"))))[:12],
        "secondaryRows": sorted(starters, key=lambda row: as_float(row.get("overall")), reverse=True)[:12],
        "counts": {"starters": len(starters), "allQbs": len(all_qbs), "activeQb75": active_qb_75},
    }, flags


STARTER_SLOTS = {
    "QB", "RB", "FB", "LWR", "RWR", "SWR", "TE", "LT", "LG", "C", "RG", "RT",
    "LEDGE", "REDGE", "LDL", "RDL", "NT", "WLB", "MLB", "SLB", "LCB", "RCB", "NB", "FS", "SS",
}


def starter_age_rows(con: sqlite3.Connection) -> list[dict[str, Any]]:
    if not table_exists(con, "depth_charts"):
        return []
    rows = con.execute(
        """
        SELECT
            t.team_id,
            t.abbreviation,
            t.city,
            t.nickname,
            dc.position AS slot,
            dc.unit,
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.overall,
            p.potential
        FROM depth_charts dc
        JOIN teams t ON t.team_id = dc.team_id
        JOIN players p ON p.player_id = dc.player_id
        WHERE dc.depth_rank = 1
          AND dc.unit IN ('Offense', 'Defense')
          AND p.age IS NOT NULL
        """
    ).fetchall()
    starters: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        slot = depth_packages.canonical_slot(row["slot"])
        if slot not in STARTER_SLOTS:
            continue
        key = (int(row["team_id"]), int(row["player_id"]))
        starters.setdefault(key, dict(row) | {"canonicalSlot": slot, "teamName": team_name(row)})
    return list(starters.values())


def build_starter_age(con: sqlite3.Connection, league_year: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = starter_age_rows(con)
    flags: list[dict[str, Any]] = []
    by_team: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = by_team.setdefault(
            int(row["team_id"]),
            {"team_id": row["team_id"], "abbreviation": row["abbreviation"], "teamName": row["teamName"], "ages": [], "over30": 0, "under25": 0},
        )
        age = as_float(row.get("age"))
        item["ages"].append(age)
        item["over30"] += int(age >= 30)
        item["under25"] += int(age <= 24)
    team_rows = []
    for item in by_team.values():
        starter_count = len(item["ages"])
        item["starter_count"] = starter_count
        item["avg_age"] = avg(item["ages"]) or 0
        item["over30_pct"] = round(item["over30"] * 100.0 / starter_count, 1) if starter_count else 0
        item["under25_pct"] = round(item["under25"] * 100.0 / starter_count, 1) if starter_count else 0
        item.pop("ages", None)
        team_rows.append(item)
    league_avg = avg([as_float(row.get("age")) for row in rows])
    over30_pct = round(sum(1 for row in rows if as_float(row.get("age")) >= 30) * 100.0 / len(rows), 1) if rows else 0
    old_teams = [row for row in team_rows if as_float(row.get("avg_age")) >= 28.8]
    young_teams = [row for row in team_rows if as_float(row.get("avg_age")) <= 25.2]
    if league_avg is not None and league_avg >= 28.2:
        flags.append(flag("starterAge", "warning", "League starter age is drifting old.", metric_key="league_avg_age", current_value=league_avg, sort_order=20))
    if len(old_teams) >= 8:
        flags.append(flag("starterAge", "warning", f"{len(old_teams)} teams have old starting cores.", metric_key="old_teams", current_value=len(old_teams), sort_order=30))
    return {
        "key": "starterAge",
        "title": "Starter Age",
        "subtitle": f"{len(rows)} offensive/defensive starters counted",
        "metrics": [
            metric("league_avg_age", "Avg Starter Age", league_avg, f"{league_avg or 0:.1f}", "Offense + defense"),
            metric("old_teams", "Old Cores", len(old_teams), str(len(old_teams)), "Avg 28.8+ years", "warn" if len(old_teams) >= 8 else "ok"),
            metric("young_teams", "Young Cores", len(young_teams), str(len(young_teams)), "Avg 25.2 or lower"),
            metric("over30_pct", "30+ Starters", over30_pct, pct_text(over30_pct), "Leaguewide share"),
        ],
        "rows": sorted(team_rows, key=lambda row: as_float(row.get("avg_age")), reverse=True)[:10],
        "secondaryRows": sorted(team_rows, key=lambda row: as_float(row.get("avg_age")))[:10],
        "counts": {"starters": len(rows), "teams": len(team_rows), "oldTeams": len(old_teams), "youngTeams": len(young_teams)},
    }, flags


def build_retirements(con: sqlite3.Connection, league_year: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    flags: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    if table_exists(con, "player_retirement_decisions"):
        rows = [
            dict(row)
            for row in con.execute(
                """
                SELECT
                    d.*,
                    p.first_name || ' ' || p.last_name AS player_name,
                    p.position,
                    p.overall,
                    p.potential,
                    t.abbreviation,
                    t.city,
                    t.nickname
                FROM player_retirement_decisions d
                JOIN players p ON p.player_id = d.player_id
                LEFT JOIN teams t ON t.team_id = d.team_id
                WHERE d.season = ?
                  AND d.retired = 1
                ORDER BY d.quality_score DESC, d.age DESC
                """,
                (league_year,),
            ).fetchall()
        ]
    for row in rows:
        row["teamName"] = team_name(row) if row.get("abbreviation") else ""
    ages = [as_float(row.get("age")) for row in rows if row.get("age") is not None]
    high_quality = [row for row in rows if max(as_float(row.get("quality_score")), as_float(row.get("overall"))) >= 78]
    low_end = [row for row in rows if max(as_float(row.get("quality_score")), as_float(row.get("overall"))) < 60]
    by_position = Counter(str(row.get("position") or "-") for row in rows)
    if rows and len(rows) > 120:
        flags.append(flag("retirements", "warning", "Retirement volume is unusually high.", metric_key="retired_count", current_value=len(rows), sort_order=20))
    if len(high_quality) > 10:
        flags.append(flag("retirements", "warning", f"{len(high_quality)} starter-quality players retired in one offseason.", metric_key="high_quality_retirements", current_value=len(high_quality), sort_order=30))
    return {
        "key": "retirements",
        "title": "Retirements",
        "subtitle": f"{league_year} offseason decisions",
        "metrics": [
            metric("retired_count", "Retired", len(rows), str(len(rows)), "Recorded decisions"),
            metric("avg_age", "Avg Age", avg(ages), f"{avg(ages) or 0:.1f}", "Retiring players"),
            metric("high_quality", "Notable", len(high_quality), str(len(high_quality)), "78+ quality/OVR", "warn" if len(high_quality) > 10 else "ok"),
            metric("low_end", "Low-End", len(low_end), str(len(low_end)), "Under 60 quality"),
        ],
        "rows": rows[:12],
        "secondaryRows": [{"position": key, "count": value} for key, value in by_position.most_common(12)],
        "counts": {"retired": len(rows), "highQuality": len(high_quality), "lowEnd": len(low_end)},
    }, flags


def drafted_player_rows(con: sqlite3.Connection, league_year: int) -> list[dict[str, Any]]:
    start_year = max(2020, league_year - 4)
    if table_exists(con, "draft_class_pick_history"):
        rows = con.execute(
            """
            SELECT
                h.draft_year,
                h.round,
                h.pick_number,
                h.pick_in_round,
                h.team_id,
                h.player_id,
                COALESCE(h.player_name, p.first_name || ' ' || p.last_name) AS player_name,
                COALESCE(h.position, p.position) AS position,
                h.true_grade,
                h.potential AS drafted_potential,
                p.overall,
                p.potential,
                p.age,
                p.years_exp,
                p.status,
                t.abbreviation,
                t.city,
                t.nickname
            FROM draft_class_pick_history h
            LEFT JOIN players p ON p.player_id = h.player_id
            LEFT JOIN teams t ON t.team_id = h.team_id
            WHERE h.draft_year BETWEEN ? AND ?
              AND h.player_id IS NOT NULL
            """,
            (start_year, league_year),
        ).fetchall()
        if rows:
            return [dict(row) | {"teamName": team_name(row) if row["abbreviation"] else ""} for row in rows]
    if table_exists(con, "draft_picks"):
        rows = con.execute(
            """
            SELECT
                dp.draft_year,
                dp.round,
                dp.pick_number,
                dp.pick_in_round,
                dp.current_team_id AS team_id,
                p.player_id,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position,
                p.overall AS true_grade,
                p.potential AS drafted_potential,
                p.overall,
                p.potential,
                p.age,
                p.years_exp,
                p.status,
                t.abbreviation,
                t.city,
                t.nickname
            FROM draft_picks dp
            JOIN players p ON p.player_id = dp.selected_player_id
            LEFT JOIN teams t ON t.team_id = dp.current_team_id
            WHERE dp.draft_year BETWEEN ? AND ?
              AND dp.is_used = 1
            """,
            (start_year, league_year),
        ).fetchall()
        return [dict(row) | {"teamName": team_name(row) if row["abbreviation"] else ""} for row in rows]
    return []


def rookie_hit_floor(round_value: int) -> tuple[int, int]:
    if round_value <= 1:
        return 75, 82
    if round_value <= 3:
        return 72, 78
    if round_value <= 4:
        return 70, 76
    return 68, 75


def classify_rookie_hit(row: dict[str, Any], league_year: int) -> str:
    draft_round = max(1, as_int(row.get("round"), 7))
    overall_floor, potential_floor = rookie_hit_floor(draft_round)
    overall = as_int(row.get("overall"))
    potential = max(as_int(row.get("potential")), as_int(row.get("drafted_potential")))
    years_since = max(0, league_year - as_int(row.get("draft_year"), league_year))
    if overall >= 82 or potential >= 88:
        return "premium"
    if overall >= overall_floor or potential >= potential_floor:
        return "hit"
    if years_since >= 2 and draft_round <= 2 and overall < 68 and potential < 74:
        return "miss"
    if years_since >= 3 and overall < 65 and potential < 72:
        return "miss"
    return "developing"


def build_rookie_hit_rate(con: sqlite3.Connection, league_year: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = drafted_player_rows(con, league_year)
    flags: list[dict[str, Any]] = []
    by_year: dict[int, dict[str, Any]] = {}
    for row in rows:
        classification = classify_rookie_hit(row, league_year)
        row["hit_class"] = classification
        year = as_int(row.get("draft_year"), league_year)
        item = by_year.setdefault(year, {"draftYear": year, "selected": 0, "premium": 0, "hits": 0, "misses": 0, "developing": 0, "day2Hits": 0, "day3Gems": 0, "round1Misses": 0})
        item["selected"] += 1
        if classification == "premium":
            item["premium"] += 1
        if classification in {"premium", "hit"}:
            item["hits"] += 1
            if 2 <= as_int(row.get("round"), 0) <= 3:
                item["day2Hits"] += 1
            if as_int(row.get("round"), 0) >= 5:
                item["day3Gems"] += 1
        elif classification == "miss":
            item["misses"] += 1
            if as_int(row.get("round"), 0) == 1:
                item["round1Misses"] += 1
        else:
            item["developing"] += 1
    year_rows = []
    for item in sorted(by_year.values(), key=lambda value: value["draftYear"], reverse=True):
        item["hitRate"] = round(item["hits"] * 100.0 / item["selected"], 1) if item["selected"] else 0
        item["premiumRate"] = round(item["premium"] * 100.0 / item["selected"], 1) if item["selected"] else 0
        year_rows.append(item)
    mature = [row for row in year_rows if league_year - row["draftYear"] >= 2]
    avg_hit_rate = avg([row["hitRate"] for row in mature]) if mature else avg([row["hitRate"] for row in year_rows])
    round1_misses = sum(row["round1Misses"] for row in year_rows)
    day2_hits = sum(row["day2Hits"] for row in year_rows)
    day3_gems = sum(row["day3Gems"] for row in year_rows)
    if mature and avg_hit_rate is not None and avg_hit_rate < 22:
        flags.append(flag("rookieHitRate", "warning", "Recent mature draft classes are producing too few usable players.", metric_key="avg_hit_rate", current_value=avg_hit_rate, sort_order=20))
    if round1_misses >= 8:
        flags.append(flag("rookieHitRate", "warning", "Recent first rounds have too many early misses.", metric_key="round1_misses", current_value=round1_misses, sort_order=30))
    notable = sorted(
        rows,
        key=lambda row: ({"premium": 3, "hit": 2, "developing": 1, "miss": 0}.get(row.get("hit_class"), 0), as_int(row.get("round"), 9) * -1, as_int(row.get("overall"))),
        reverse=True,
    )[:14]
    return {
        "key": "rookieHitRate",
        "title": "Rookie Hit Rate",
        "subtitle": f"Drafts {max(2020, league_year - 4)}-{league_year}",
        "metrics": [
            metric("avg_hit_rate", "Hit Rate", avg_hit_rate, pct_text(avg_hit_rate), "Mature recent classes" if mature else "Early read"),
            metric("premium_hits", "Premium Hits", sum(row["premium"] for row in year_rows), str(sum(row["premium"] for row in year_rows)), "82+ or high ceiling"),
            metric("day2_hits", "Day 2 Hits", day2_hits, str(day2_hits), "Rounds 2-3"),
            metric("day3_gems", "Day 3 Gems", day3_gems, str(day3_gems), "Rounds 5-7"),
            metric("round1_misses", "R1 Misses", round1_misses, str(round1_misses), "Mature concern flags", "warn" if round1_misses >= 8 else "ok"),
        ],
        "rows": year_rows,
        "secondaryRows": notable,
        "counts": {"classes": len(year_rows), "players": len(rows), "day2Hits": day2_hits, "day3Gems": day3_gems, "round1Misses": round1_misses},
    }, flags


def build_summary(con: sqlite3.Connection, league_year: int) -> dict[str, Any]:
    categories: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    for builder in (build_cap_health, build_qb_supply, build_starter_age, build_retirements, build_rookie_hit_rate):
        category, category_flags = builder(con, league_year)
        categories.append(category)
        flags.extend(category_flags)
    severity_order = {"critical": 1, "warning": 2, "note": 3, "ok": 4}
    flags.sort(key=lambda item: (severity_order.get(item["severity"], 9), item.get("sort_order", 100), item["category"]))
    return {
        "leagueYear": league_year,
        "categories": categories,
        "flags": flags,
        "counts": {
            "categories": len(categories),
            "flags": len(flags),
            "critical": sum(1 for row in flags if row["severity"] == "critical"),
            "warning": sum(1 for row in flags if row["severity"] == "warning"),
        },
    }


def warning_level_for_category(category: dict[str, Any], flags: list[dict[str, Any]]) -> str:
    category_flags = [row for row in flags if row["category"] == category["key"]]
    if any(row["severity"] == "critical" for row in category_flags):
        return "critical"
    if any(row["severity"] == "warning" for row in category_flags):
        return "warning"
    return "ok"


def persist_summary(con: sqlite3.Connection, summary: dict[str, Any]) -> None:
    ensure_schema(con)
    league_year = int(summary["leagueYear"])
    con.execute(
        "DELETE FROM league_balance_dashboard_flags WHERE league_year = ? AND source = ?",
        (league_year, SOURCE),
    )
    for category in summary["categories"]:
        level = warning_level_for_category(category, summary["flags"])
        for item in category.get("metrics", []):
            con.execute(
                """
                INSERT INTO league_balance_dashboard_snapshots (
                    league_year, category, metric_key, metric_label, metric_value,
                    metric_text, warning_level, summary_json, source, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(league_year, category, metric_key) DO UPDATE SET
                    metric_label = excluded.metric_label,
                    metric_value = excluded.metric_value,
                    metric_text = excluded.metric_text,
                    warning_level = excluded.warning_level,
                    summary_json = excluded.summary_json,
                    source = excluded.source,
                    updated_at = datetime('now')
                """,
                (
                    league_year,
                    category["key"],
                    item["key"],
                    item["label"],
                    item.get("value"),
                    item.get("text"),
                    level,
                    json.dumps(category, sort_keys=True, default=str),
                    SOURCE,
                ),
            )
    for index, item in enumerate(summary["flags"]):
        fingerprint = f"{SOURCE}:{league_year}:{item['category']}:{item.get('metric_key') or index}:{item['message'][:80]}"
        con.execute(
            """
            INSERT INTO league_balance_dashboard_flags (
                league_year, category, severity, sort_order, metric_key, current_value,
                message, related_team_id, related_player_id, fingerprint, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(fingerprint) DO UPDATE SET
                severity = excluded.severity,
                sort_order = excluded.sort_order,
                current_value = excluded.current_value,
                message = excluded.message,
                related_team_id = excluded.related_team_id,
                related_player_id = excluded.related_player_id,
                source = excluded.source,
                updated_at = datetime('now')
            """,
            (
                league_year,
                item["category"],
                item["severity"],
                item.get("sort_order", 100),
                item.get("metric_key"),
                item.get("current_value"),
                item["message"],
                item.get("team_id"),
                item.get("player_id"),
                fingerprint,
                SOURCE,
            ),
        )


def run_dashboards(con: sqlite3.Connection, *, league_year: int, apply: bool = True) -> dict[str, Any]:
    ensure_schema(con)
    summary = build_summary(con, league_year)
    if apply:
        persist_summary(con, summary)
    return summary


def latest_summary(con: sqlite3.Connection, *, league_year: int | None = None) -> dict[str, Any]:
    ensure_schema(con)
    target_year = int(league_year or current_league_year(con))
    summary = build_summary(con, target_year)
    history_rows: list[dict[str, Any]] = []
    if table_exists(con, "league_balance_dashboard_snapshots"):
        history_rows = [
            dict(row)
            for row in con.execute(
                """
                SELECT league_year,
                       category,
                       metric_key,
                       metric_label,
                       metric_value,
                       metric_text,
                       warning_level
                FROM league_balance_dashboard_snapshots
                WHERE league_year <= ?
                ORDER BY league_year DESC, category, metric_key
                LIMIT 120
                """,
                (target_year,),
            ).fetchall()
        ]
    summary["history"] = history_rows
    return summary


def print_summary(summary: dict[str, Any]) -> None:
    print(f"League balance dashboard: {summary['leagueYear']}")
    for category in summary.get("categories", []):
        print(f"  {category['title']}:")
        for item in category.get("metrics", [])[:5]:
            print(f"    {item['label']}: {item['text']} ({item.get('detail') or 'snapshot'})")
    flags = summary.get("flags", [])
    print(f"  Flags: {len(flags)}")
    for item in flags[:12]:
        print(f"    [{item['severity']}] {item['message']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build long-term league balance dashboards.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--league-year", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with connect(args.db) as con:
        league_year = int(args.league_year or current_league_year(con))
        summary = run_dashboards(con, league_year=league_year, apply=bool(args.apply))
        if args.apply:
            con.commit()
        else:
            con.rollback()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    else:
        print_summary(summary)
        if not args.apply:
            print("Dry run only. Add --apply to save dashboard snapshots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
