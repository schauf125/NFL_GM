#!/usr/bin/env python3
"""Track league-wide talent supply by position and flag long-term drift."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_LEAGUE_YEAR = 2026
SOURCE = "talent_supply_guardrails"

POSITION_ORDER = {
    "QB": 10,
    "RB": 20,
    "FB": 25,
    "WR": 30,
    "TE": 40,
    "OT": 50,
    "OG": 55,
    "C": 60,
    "IDL": 70,
    "EDGE": 75,
    "LB": 80,
    "CB": 90,
    "NB": 95,
    "S": 100,
    "K": 110,
    "P": 120,
    "LS": 130,
}

POSITION_GROUPS = {
    "QB": "QB",
    "RB": "RB",
    "FB": "RB",
    "WR": "WR",
    "TE": "TE",
    "OT": "OL",
    "OG": "OL",
    "C": "OL",
    "IDL": "DL",
    "EDGE": "EDGE",
    "LB": "LB",
    "CB": "DB",
    "NB": "DB",
    "S": "DB",
    "K": "ST",
    "P": "ST",
    "LS": "ST",
}

POSITION_ALIASES = {
    "HB": "RB",
    "DE": "EDGE",
    "OLB": "LB",
    "ILB": "LB",
    "MLB": "LB",
    "DT": "IDL",
    "NT": "IDL",
    "FS": "S",
    "SS": "S",
}

STARTER_THRESHOLDS = {
    "QB": 75,
    "RB": 73,
    "FB": 68,
    "WR": 72,
    "TE": 72,
    "OT": 72,
    "OG": 72,
    "C": 72,
    "IDL": 72,
    "EDGE": 72,
    "LB": 72,
    "CB": 72,
    "NB": 71,
    "S": 72,
    "K": 74,
    "P": 74,
    "LS": 70,
}

REPLACEMENT_THRESHOLDS = {
    "QB": 62,
    "RB": 60,
    "FB": 58,
    "WR": 60,
    "TE": 60,
    "OT": 60,
    "OG": 60,
    "C": 60,
    "IDL": 60,
    "EDGE": 60,
    "LB": 60,
    "CB": 60,
    "NB": 60,
    "S": 60,
    "K": 64,
    "P": 64,
    "LS": 62,
}

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

COUNT_METRICS = (
    "count_90_plus",
    "count_85_plus",
    "count_80_plus",
    "count_starter_level",
    "count_replacement_level",
)


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


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS league_talent_supply_snapshots (
            league_year INTEGER NOT NULL,
            position TEXT NOT NULL,
            position_group TEXT NOT NULL,
            position_order INTEGER NOT NULL,
            population_scope TEXT NOT NULL DEFAULT 'all_non_retired',
            total_count INTEGER NOT NULL DEFAULT 0,
            team_controlled_count INTEGER NOT NULL DEFAULT 0,
            active_roster_count INTEGER NOT NULL DEFAULT 0,
            free_agent_count INTEGER NOT NULL DEFAULT 0,
            avg_overall REAL,
            median_overall REAL,
            avg_potential REAL,
            count_90_plus INTEGER NOT NULL DEFAULT 0,
            count_85_plus INTEGER NOT NULL DEFAULT 0,
            count_80_plus INTEGER NOT NULL DEFAULT 0,
            count_starter_level INTEGER NOT NULL DEFAULT 0,
            count_replacement_level INTEGER NOT NULL DEFAULT 0,
            count_below_replacement INTEGER NOT NULL DEFAULT 0,
            starter_threshold INTEGER NOT NULL,
            replacement_threshold INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'talent_supply_guardrails',
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (league_year, position, population_scope)
        );

        CREATE TABLE IF NOT EXISTS league_talent_supply_baselines (
            position TEXT PRIMARY KEY,
            position_group TEXT NOT NULL,
            baseline_league_year INTEGER NOT NULL,
            total_count INTEGER NOT NULL DEFAULT 0,
            avg_overall REAL,
            count_90_plus INTEGER NOT NULL DEFAULT 0,
            count_85_plus INTEGER NOT NULL DEFAULT 0,
            count_80_plus INTEGER NOT NULL DEFAULT 0,
            count_starter_level INTEGER NOT NULL DEFAULT 0,
            count_replacement_level INTEGER NOT NULL DEFAULT 0,
            starter_threshold INTEGER NOT NULL,
            replacement_threshold INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'talent_supply_guardrails',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS league_talent_supply_flags (
            flag_id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_year INTEGER NOT NULL,
            position TEXT NOT NULL,
            position_group TEXT NOT NULL,
            metric_key TEXT NOT NULL,
            comparison TEXT NOT NULL,
            severity TEXT NOT NULL,
            direction TEXT NOT NULL,
            baseline_value REAL,
            current_value REAL NOT NULL,
            absolute_delta REAL NOT NULL,
            pct_delta REAL,
            message TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'talent_supply_guardrails',
            fingerprint TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        DROP VIEW IF EXISTS league_talent_supply_snapshots_view;
        CREATE VIEW league_talent_supply_snapshots_view AS
        SELECT
            s.*,
            ROUND(COALESCE(s.count_90_plus, 0) * 100.0 / NULLIF(s.total_count, 0), 1) AS pct_90_plus,
            ROUND(COALESCE(s.count_85_plus, 0) * 100.0 / NULLIF(s.total_count, 0), 1) AS pct_85_plus,
            ROUND(COALESCE(s.count_80_plus, 0) * 100.0 / NULLIF(s.total_count, 0), 1) AS pct_80_plus,
            ROUND(COALESCE(s.count_starter_level, 0) * 100.0 / NULLIF(s.total_count, 0), 1) AS pct_starter_level,
            ROUND(COALESCE(s.count_replacement_level, 0) * 100.0 / NULLIF(s.total_count, 0), 1) AS pct_replacement_level
        FROM league_talent_supply_snapshots s;

        DROP VIEW IF EXISTS league_talent_supply_flags_view;
        CREATE VIEW league_talent_supply_flags_view AS
        SELECT
            f.*,
            CASE f.severity
                WHEN 'critical' THEN 1
                WHEN 'warning' THEN 2
                ELSE 3
            END AS severity_order
        FROM league_talent_supply_flags f;
        """
    )


def current_league_year(con: sqlite3.Connection) -> int:
    for key in ("current_league_year", "current_season"):
        if not table_exists(con, "game_settings"):
            break
        row = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = ?",
            (key,),
        ).fetchone()
        if row and row["setting_value"]:
            return int(row["setting_value"])
    return DEFAULT_LEAGUE_YEAR


def normalize_position(position: str | None) -> str:
    raw = str(position or "UNK").strip().upper()
    return POSITION_ALIASES.get(raw, raw)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[mid], 1)
    return round((ordered[mid - 1] + ordered[mid]) / 2.0, 1)


def metric_value(row: sqlite3.Row | dict[str, Any], key: str) -> float:
    return float(row[key] if isinstance(row, sqlite3.Row) else row.get(key) or 0)


def collect_position_rows(con: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    if not table_exists(con, "players"):
        return {}
    grouped: dict[str, dict[str, Any]] = {}
    rows = con.execute(
        """
        SELECT player_id, position, team_id, status, overall, potential
        FROM players
        WHERE COALESCE(status, 'Active') != 'Retired'
          AND overall IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        position = normalize_position(row["position"])
        starter_threshold = STARTER_THRESHOLDS.get(position, 72)
        replacement_threshold = REPLACEMENT_THRESHOLDS.get(position, 60)
        item = grouped.setdefault(
            position,
            {
                "position": position,
                "position_group": POSITION_GROUPS.get(position, "OTHER"),
                "position_order": POSITION_ORDER.get(position, 999),
                "overalls": [],
                "potentials": [],
                "total_count": 0,
                "team_controlled_count": 0,
                "active_roster_count": 0,
                "free_agent_count": 0,
                "count_90_plus": 0,
                "count_85_plus": 0,
                "count_80_plus": 0,
                "count_starter_level": 0,
                "count_replacement_level": 0,
                "count_below_replacement": 0,
                "starter_threshold": starter_threshold,
                "replacement_threshold": replacement_threshold,
            },
        )
        overall = float(row["overall"] or 0)
        potential = float(row["potential"] if row["potential"] is not None else overall)
        status = str(row["status"] or "Active")
        item["total_count"] += 1
        item["overalls"].append(overall)
        item["potentials"].append(potential)
        if status in TEAM_CONTROLLED_STATUSES or row["team_id"] is not None:
            item["team_controlled_count"] += 1
        if row["team_id"] is not None and status in {"Active", "Questionable", "Doubtful", "Out", "Suspended"}:
            item["active_roster_count"] += 1
        if row["team_id"] is None or status in {"Free Agent", "Released", "Waived"}:
            item["free_agent_count"] += 1
        if overall >= 90:
            item["count_90_plus"] += 1
        if overall >= 85:
            item["count_85_plus"] += 1
        if overall >= 80:
            item["count_80_plus"] += 1
        if overall >= starter_threshold:
            item["count_starter_level"] += 1
        if overall >= replacement_threshold:
            item["count_replacement_level"] += 1
        else:
            item["count_below_replacement"] += 1
    for item in grouped.values():
        item["avg_overall"] = round(sum(item["overalls"]) / len(item["overalls"]), 1) if item["overalls"] else None
        item["median_overall"] = median(item["overalls"])
        item["avg_potential"] = round(sum(item["potentials"]) / len(item["potentials"]), 1) if item["potentials"] else None
    return grouped


def upsert_snapshot(con: sqlite3.Connection, league_year: int, item: dict[str, Any]) -> None:
    con.execute(
        """
        INSERT INTO league_talent_supply_snapshots (
            league_year, position, position_group, position_order, population_scope,
            total_count, team_controlled_count, active_roster_count, free_agent_count,
            avg_overall, median_overall, avg_potential, count_90_plus, count_85_plus,
            count_80_plus, count_starter_level, count_replacement_level,
            count_below_replacement, starter_threshold, replacement_threshold,
            source, updated_at
        )
        VALUES (?, ?, ?, ?, 'all_non_retired', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(league_year, position, population_scope) DO UPDATE SET
            position_group = excluded.position_group,
            position_order = excluded.position_order,
            total_count = excluded.total_count,
            team_controlled_count = excluded.team_controlled_count,
            active_roster_count = excluded.active_roster_count,
            free_agent_count = excluded.free_agent_count,
            avg_overall = excluded.avg_overall,
            median_overall = excluded.median_overall,
            avg_potential = excluded.avg_potential,
            count_90_plus = excluded.count_90_plus,
            count_85_plus = excluded.count_85_plus,
            count_80_plus = excluded.count_80_plus,
            count_starter_level = excluded.count_starter_level,
            count_replacement_level = excluded.count_replacement_level,
            count_below_replacement = excluded.count_below_replacement,
            starter_threshold = excluded.starter_threshold,
            replacement_threshold = excluded.replacement_threshold,
            source = excluded.source,
            updated_at = datetime('now')
        """,
        (
            league_year,
            item["position"],
            item["position_group"],
            item["position_order"],
            item["total_count"],
            item["team_controlled_count"],
            item["active_roster_count"],
            item["free_agent_count"],
            item["avg_overall"],
            item["median_overall"],
            item["avg_potential"],
            item["count_90_plus"],
            item["count_85_plus"],
            item["count_80_plus"],
            item["count_starter_level"],
            item["count_replacement_level"],
            item["count_below_replacement"],
            item["starter_threshold"],
            item["replacement_threshold"],
            SOURCE,
        ),
    )


def ensure_baseline(con: sqlite3.Connection, item: dict[str, Any], league_year: int) -> bool:
    existing = con.execute(
        "SELECT 1 FROM league_talent_supply_baselines WHERE position = ?",
        (item["position"],),
    ).fetchone()
    if existing:
        return False
    con.execute(
        """
        INSERT INTO league_talent_supply_baselines (
            position, position_group, baseline_league_year, total_count, avg_overall,
            count_90_plus, count_85_plus, count_80_plus, count_starter_level,
            count_replacement_level, starter_threshold, replacement_threshold, source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item["position"],
            item["position_group"],
            league_year,
            item["total_count"],
            item["avg_overall"],
            item["count_90_plus"],
            item["count_85_plus"],
            item["count_80_plus"],
            item["count_starter_level"],
            item["count_replacement_level"],
            item["starter_threshold"],
            item["replacement_threshold"],
            SOURCE,
        ),
    )
    return True


def comparison_limits(metric: str, baseline_value: float) -> tuple[float, float, float | None, float | None]:
    if metric == "count_90_plus":
        return max(2.0, math.ceil(baseline_value * 0.50)), max(4.0, math.ceil(baseline_value * 1.00)), 0.50, 1.00
    if metric == "count_85_plus":
        return max(3.0, math.ceil(baseline_value * 0.35)), max(5.0, math.ceil(baseline_value * 0.55)), 0.35, 0.55
    if metric == "count_80_plus":
        return max(5.0, math.ceil(baseline_value * 0.25)), max(8.0, math.ceil(baseline_value * 0.40)), 0.25, 0.40
    if metric in {"count_starter_level", "count_replacement_level"}:
        return max(8.0, math.ceil(baseline_value * 0.18)), max(12.0, math.ceil(baseline_value * 0.28)), 0.18, 0.28
    if metric == "avg_overall":
        return 1.5, 3.0, None, None
    return 10.0, 20.0, 0.25, 0.40


def yearly_limits(metric: str, previous_value: float) -> tuple[float, float, float | None, float | None]:
    if metric == "avg_overall":
        return 1.2, 2.0, None, None
    if metric == "count_90_plus":
        return max(2.0, math.ceil(previous_value * 0.45)), max(3.0, math.ceil(previous_value * 0.80)), 0.45, 0.80
    if metric == "count_85_plus":
        return max(3.0, math.ceil(previous_value * 0.28)), max(5.0, math.ceil(previous_value * 0.45)), 0.28, 0.45
    if metric == "count_80_plus":
        return max(5.0, math.ceil(previous_value * 0.18)), max(8.0, math.ceil(previous_value * 0.32)), 0.18, 0.32
    return max(8.0, math.ceil(previous_value * 0.15)), max(12.0, math.ceil(previous_value * 0.25)), 0.15, 0.25


def severity_for_delta(
    *,
    metric: str,
    baseline_value: float,
    current_value: float,
    comparison: str,
) -> tuple[str | None, float, float | None]:
    delta = current_value - baseline_value
    abs_delta = abs(delta)
    pct_delta = None if baseline_value == 0 else delta / baseline_value
    if baseline_value == 0 and current_value == 0:
        return None, delta, pct_delta
    if comparison == "year_over_year":
        warn_abs, critical_abs, warn_pct, critical_pct = yearly_limits(metric, abs(baseline_value))
    else:
        warn_abs, critical_abs, warn_pct, critical_pct = comparison_limits(metric, abs(baseline_value))
    if metric == "avg_overall":
        if abs_delta >= critical_abs:
            return "critical", delta, pct_delta
        if abs_delta >= warn_abs:
            return "warning", delta, pct_delta
        return None, delta, pct_delta
    if baseline_value == 0:
        if current_value >= critical_abs:
            return "critical", delta, pct_delta
        if current_value >= warn_abs:
            return "warning", delta, pct_delta
        return None, delta, pct_delta
    pct_hit_warning = warn_pct is None or abs(pct_delta or 0.0) >= warn_pct
    pct_hit_critical = critical_pct is None or abs(pct_delta or 0.0) >= critical_pct
    if abs_delta >= critical_abs and pct_hit_critical:
        return "critical", delta, pct_delta
    if abs_delta >= warn_abs and pct_hit_warning:
        return "warning", delta, pct_delta
    return None, delta, pct_delta


def metric_label(metric: str) -> str:
    labels = {
        "count_90_plus": "90+ players",
        "count_85_plus": "85+ players",
        "count_80_plus": "80+ players",
        "count_starter_level": "starter-level players",
        "count_replacement_level": "replacement-level players",
        "avg_overall": "average overall",
    }
    return labels.get(metric, metric.replace("_", " "))


def insert_flag(
    con: sqlite3.Connection,
    *,
    league_year: int,
    position: str,
    position_group: str,
    metric: str,
    comparison: str,
    severity: str,
    baseline_value: float,
    current_value: float,
    delta: float,
    pct_delta: float | None,
) -> None:
    direction = "inflated" if delta > 0 else "deflated"
    comparator = "baseline" if comparison == "baseline" else "last year"
    if pct_delta is None:
        pct_text = "new supply"
    else:
        pct_text = f"{pct_delta * 100:+.1f}%"
    message = (
        f"{position} {metric_label(metric)} looks {direction}: "
        f"{current_value:.1f} vs {baseline_value:.1f} {comparator} ({delta:+.1f}, {pct_text})."
    )
    fingerprint = f"talent-supply:{league_year}:{position}:{metric}:{comparison}"
    con.execute(
        """
        INSERT INTO league_talent_supply_flags (
            league_year, position, position_group, metric_key, comparison, severity,
            direction, baseline_value, current_value, absolute_delta, pct_delta,
            message, source, fingerprint, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(fingerprint) DO UPDATE SET
            severity = excluded.severity,
            direction = excluded.direction,
            baseline_value = excluded.baseline_value,
            current_value = excluded.current_value,
            absolute_delta = excluded.absolute_delta,
            pct_delta = excluded.pct_delta,
            message = excluded.message,
            updated_at = datetime('now')
        """,
        (
            league_year,
            position,
            position_group,
            metric,
            comparison,
            severity,
            direction,
            baseline_value,
            current_value,
            abs(delta),
            pct_delta,
            message,
            SOURCE,
            fingerprint,
        ),
    )


def load_baselines(con: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {
        str(row["position"]): row
        for row in con.execute("SELECT * FROM league_talent_supply_baselines").fetchall()
    }


def load_previous_snapshots(con: sqlite3.Connection, league_year: int) -> dict[str, sqlite3.Row]:
    rows = con.execute(
        """
        SELECT *
        FROM league_talent_supply_snapshots
        WHERE league_year = (
            SELECT MAX(league_year)
            FROM league_talent_supply_snapshots
            WHERE league_year < ?
        )
        """,
        (league_year,),
    ).fetchall()
    return {str(row["position"]): row for row in rows}


def clear_current_flags(con: sqlite3.Connection, league_year: int) -> None:
    con.execute(
        "DELETE FROM league_talent_supply_flags WHERE league_year = ? AND source = ?",
        (league_year, SOURCE),
    )


def generate_flags(con: sqlite3.Connection, league_year: int, snapshots: dict[str, dict[str, Any]]) -> int:
    clear_current_flags(con, league_year)
    baselines = load_baselines(con)
    previous = load_previous_snapshots(con, league_year)
    before = con.total_changes
    for position, item in snapshots.items():
        baseline = baselines.get(position)
        if baseline and int(baseline["baseline_league_year"]) != league_year:
            for metric in (*COUNT_METRICS, "avg_overall"):
                severity, delta, pct_delta = severity_for_delta(
                    metric=metric,
                    baseline_value=metric_value(baseline, metric),
                    current_value=metric_value(item, metric),
                    comparison="baseline",
                )
                if severity:
                    insert_flag(
                        con,
                        league_year=league_year,
                        position=position,
                        position_group=item["position_group"],
                        metric=metric,
                        comparison="baseline",
                        severity=severity,
                        baseline_value=metric_value(baseline, metric),
                        current_value=metric_value(item, metric),
                        delta=delta,
                        pct_delta=pct_delta,
                    )
        previous_row = previous.get(position)
        if previous_row:
            for metric in ("count_90_plus", "count_85_plus", "count_80_plus", "count_starter_level", "avg_overall"):
                severity, delta, pct_delta = severity_for_delta(
                    metric=metric,
                    baseline_value=metric_value(previous_row, metric),
                    current_value=metric_value(item, metric),
                    comparison="year_over_year",
                )
                if severity:
                    insert_flag(
                        con,
                        league_year=league_year,
                        position=position,
                        position_group=item["position_group"],
                        metric=metric,
                        comparison="year_over_year",
                        severity=severity,
                        baseline_value=metric_value(previous_row, metric),
                        current_value=metric_value(item, metric),
                        delta=delta,
                        pct_delta=pct_delta,
                    )
    return con.total_changes - before


def run_guardrails(
    con: sqlite3.Connection,
    *,
    league_year: int,
    apply: bool = True,
    persist_baseline: bool = True,
) -> dict[str, Any]:
    ensure_schema(con)
    snapshots = collect_position_rows(con)
    for item in snapshots.values():
        upsert_snapshot(con, league_year, item)
    baseline_created = 0
    if persist_baseline:
        for item in snapshots.values():
            if ensure_baseline(con, item, league_year):
                baseline_created += 1
    flags_changed = generate_flags(con, league_year, snapshots)
    flags = [
        dict(row)
        for row in con.execute(
            """
            SELECT *
            FROM league_talent_supply_flags_view
            WHERE league_year = ?
            ORDER BY severity_order, position_group, position, metric_key
            """,
            (league_year,),
        ).fetchall()
    ]
    top_positions = sorted(
        snapshots.values(),
        key=lambda item: (item["position_order"], item["position"]),
    )
    summary = {
        "leagueYear": league_year,
        "positionsTracked": len(snapshots),
        "totalPlayers": sum(int(item["total_count"]) for item in snapshots.values()),
        "baselineRowsCreated": baseline_created,
        "flagsChanged": flags_changed,
        "flagCount": len(flags),
        "criticalFlags": sum(1 for row in flags if row["severity"] == "critical"),
        "warningFlags": sum(1 for row in flags if row["severity"] == "warning"),
        "positions": [
            {
                key: item[key]
                for key in (
                    "position",
                    "position_group",
                    "total_count",
                    "avg_overall",
                    "count_90_plus",
                    "count_85_plus",
                    "count_80_plus",
                    "count_starter_level",
                    "count_replacement_level",
                )
            }
            for item in top_positions
        ],
        "flags": flags,
    }
    return summary


def latest_summary(con: sqlite3.Connection, *, league_year: int | None = None, limit_flags: int = 24) -> dict[str, Any]:
    ensure_schema(con)
    if league_year is None:
        row = con.execute("SELECT MAX(league_year) AS league_year FROM league_talent_supply_snapshots").fetchone()
        league_year = int(row["league_year"]) if row and row["league_year"] is not None else current_league_year(con)
    snapshots = [
        dict(row)
        for row in con.execute(
            """
            SELECT *
            FROM league_talent_supply_snapshots_view
            WHERE league_year = ?
            ORDER BY position_order, position
            """,
            (league_year,),
        ).fetchall()
    ]
    flags = [
        dict(row)
        for row in con.execute(
            """
            SELECT *
            FROM league_talent_supply_flags_view
            WHERE league_year = ?
            ORDER BY severity_order, position_group, position, metric_key
            LIMIT ?
            """,
            (league_year, limit_flags),
        ).fetchall()
    ]
    history = [
        dict(row)
        for row in con.execute(
            """
            SELECT league_year,
                   SUM(count_90_plus) AS count_90_plus,
                   SUM(count_85_plus) AS count_85_plus,
                   SUM(count_80_plus) AS count_80_plus,
                   SUM(count_starter_level) AS count_starter_level,
                   SUM(count_replacement_level) AS count_replacement_level,
                   ROUND(AVG(avg_overall), 1) AS avg_position_overall
            FROM league_talent_supply_snapshots
            GROUP BY league_year
            ORDER BY league_year DESC
            LIMIT 8
            """
        ).fetchall()
    ]
    return {
        "leagueYear": league_year,
        "positions": snapshots,
        "flags": flags,
        "history": history,
        "counts": {
            "positions": len(snapshots),
            "flags": len(flags),
            "critical": sum(1 for row in flags if row["severity"] == "critical"),
            "warning": sum(1 for row in flags if row["severity"] == "warning"),
        },
    }


def print_summary(summary: dict[str, Any]) -> None:
    print(f"Talent supply snapshot: {summary['leagueYear']}")
    print(f"  Positions tracked: {summary['positionsTracked']}")
    print(f"  Players counted: {summary['totalPlayers']}")
    print(f"  Baseline rows created: {summary['baselineRowsCreated']}")
    print(f"  Flags: {summary['flagCount']} ({summary['criticalFlags']} critical, {summary['warningFlags']} warning)")
    for flag in summary["flags"][:12]:
        print(f"  [{flag['severity']}] {flag['message']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Snapshot league talent supply by position and flag drift.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--league-year", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-baseline", action="store_true", help="Do not create missing baseline rows from this snapshot.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with connect(args.db) as con:
        league_year = int(args.league_year or current_league_year(con))
        summary = run_guardrails(
            con,
            league_year=league_year,
            apply=bool(args.apply),
            persist_baseline=not args.no_baseline,
        )
        if args.apply:
            con.commit()
        else:
            con.rollback()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    else:
        print_summary(summary)
        if not args.apply:
            print("Dry run only. Add --apply to save talent supply rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
