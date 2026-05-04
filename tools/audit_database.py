#!/usr/bin/env python3
"""Audit the NFL GM Sim database for sim readiness.

This is a lightweight sanity checker, not a data-quality oracle. It focuses on
the things most likely to break team views, roster movement, or match sims.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import match_engine  # noqa: E402


REQUIRED_SLOTS = [
    "QB",
    "RB",
    "LWR",
    "RWR",
    "SWR",
    "TE",
    "LT",
    "LG",
    "C",
    "RG",
    "RT",
    "LEDGE",
    "LDL",
    "NT",
    "RDL",
    "REDGE",
    "MLB",
    "WLB",
    "LCB",
    "RCB",
    "NB",
    "FS",
    "SS",
    "PK",
    "PT",
]
OPTIONAL_SLOTS = ["LS"]
SPECIALIST_RATINGS = {"kick_power", "kick_accuracy", "composure"}
COACH_GROUPS = {"QB", "RB", "WR", "TE", "OL", "DL", "EDGE", "LB", "CB", "S", "ST"}
COACH_ROLES = {"Head Coach", "Offensive Coordinator", "Defensive Coordinator"}
MAX_ITEMS_PER_CATEGORY = 40


@dataclass
class Finding:
    severity: str
    category: str
    message: str


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def team_filter_clause(team_id: int | None, alias: str = "p") -> tuple[str, tuple[int, ...]]:
    if team_id is None:
        return "", ()
    return f" AND {alias}.team_id = ?", (team_id,)


def selected_teams(con: sqlite3.Connection, abbreviation: str | None) -> list[sqlite3.Row]:
    if abbreviation:
        rows = con.execute(
            "SELECT * FROM teams WHERE abbreviation = ? ORDER BY abbreviation",
            (abbreviation.upper(),),
        ).fetchall()
        if not rows:
            raise ValueError(f"Team not found: {abbreviation}")
        return rows
    return con.execute("SELECT * FROM teams ORDER BY abbreviation").fetchall()


def add(findings: list[Finding], severity: str, category: str, message: str) -> None:
    findings.append(Finding(severity, category, message))


def roster_positions(con: sqlite3.Connection, team_id: int) -> set[str]:
    return {
        row["position"]
        for row in con.execute(
            """
            SELECT DISTINCT position
            FROM players
            WHERE team_id = ?
              AND COALESCE(status, 'Active') NOT IN ('Retired')
            """,
            (team_id,),
        )
    }


def explicit_depth_slots(con: sqlite3.Connection, team_id: int) -> set[str]:
    return {
        row["position"].upper()
        for row in con.execute(
            "SELECT DISTINCT position FROM depth_charts WHERE team_id = ?",
            (team_id,),
        )
    }


def fallback_positions(slot: str) -> list[str]:
    if slot == "LS":
        return ["LS"]
    return match_engine.SLOT_POSITION_FALLBACKS.get(slot, [slot])


def audit_team_rosters(con: sqlite3.Connection, teams: list[sqlite3.Row], findings: list[Finding]) -> None:
    for team in teams:
        count = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM players
                WHERE team_id = ?
                  AND COALESCE(status, 'Active') NOT IN ('Retired')
                """,
                (team["team_id"],),
            ).fetchone()[0]
            or 0
        )
        label = team["abbreviation"]
        if count == 0:
            add(findings, "ERROR", "Roster", f"{label} has no rostered players.")
        elif count < 53:
            add(findings, "ERROR", "Roster", f"{label} has only {count} rostered players.")
        elif count < 60:
            add(findings, "WARN", "Roster", f"{label} has {count} rostered players; preseason depth may be thin.")
        elif count > 90:
            add(findings, "WARN", "Roster", f"{label} has {count} rostered players; above offseason limit.")


def audit_depth_slots(con: sqlite3.Connection, teams: list[sqlite3.Row], findings: list[Finding]) -> None:
    for team in teams:
        team_id = int(team["team_id"])
        label = team["abbreviation"]
        slots = explicit_depth_slots(con, team_id)
        positions = roster_positions(con, team_id)
        if not positions:
            continue

        for slot in REQUIRED_SLOTS:
            if slot in slots:
                continue
            fallbacks = fallback_positions(slot)
            if positions.intersection(fallbacks):
                continue
            else:
                add(
                    findings,
                    "ERROR",
                    "Depth",
                    f"{label} has no {slot} depth slot and no roster fallback from {', '.join(fallbacks)}.",
                )

        for slot in OPTIONAL_SLOTS:
            if slot in slots:
                continue
            fallbacks = fallback_positions(slot)
            if not positions.intersection(fallbacks):
                add(
                    findings,
                    "WARN",
                    "Depth",
                    f"{label} has no optional {slot} depth slot or roster fallback from {', '.join(fallbacks)}.",
                )


def audit_depth_integrity(con: sqlite3.Connection, team_id: int | None, findings: list[Finding]) -> None:
    clause, params = team_filter_clause(team_id, "dc")
    for row in con.execute(
        f"""
        SELECT
            t.abbreviation AS depth_team,
            dc.position,
            dc.depth_rank,
            dc.player_id,
            p.first_name,
            p.last_name,
            p.status,
            p.team_id AS player_team_id,
            pt.abbreviation AS player_team
        FROM depth_charts dc
        JOIN teams t ON t.team_id = dc.team_id
        LEFT JOIN players p ON p.player_id = dc.player_id
        LEFT JOIN teams pt ON pt.team_id = p.team_id
        WHERE 1 = 1 {clause}
        ORDER BY t.abbreviation, dc.position, dc.depth_rank
        """,
        params,
    ):
        player_name = (
            f"{row['first_name']} {row['last_name']}"
            if row["first_name"] is not None
            else f"missing player {row['player_id']}"
        )
        location = f"{row['depth_team']} {row['position']} #{row['depth_rank']}"
        if row["first_name"] is None:
            add(findings, "ERROR", "Depth", f"{location} points to missing player_id {row['player_id']}.")
        elif row["player_team_id"] is None or row["player_team"] != row["depth_team"]:
            add(
                findings,
                "ERROR",
                "Depth",
                f"{location} uses {player_name}, who is on {row['player_team'] or row['status']}.",
            )
        elif row["status"] == "Retired":
            add(findings, "ERROR", "Depth", f"{location} uses retired player {player_name}.")

    for row in con.execute(
        f"""
        SELECT
            t.abbreviation,
            dc.unit,
            dc.position,
            dc.depth_rank,
            COUNT(*) AS count
        FROM depth_charts dc
        JOIN teams t ON t.team_id = dc.team_id
        WHERE 1 = 1 {clause}
        GROUP BY dc.team_id, dc.unit, dc.position, dc.depth_rank
        HAVING COUNT(*) > 1
        ORDER BY t.abbreviation, dc.unit, dc.position, dc.depth_rank
        """,
        params,
    ):
        add(
            findings,
            "ERROR",
            "Depth",
            f"{row['abbreviation']} has {row['count']} players at {row['unit']} {row['position']} depth rank {row['depth_rank']}.",
        )


def audit_ratings(con: sqlite3.Connection, season: int, team_id: int | None, findings: list[Finding]) -> None:
    if not table_exists(con, "rating_definitions") or not table_exists(con, "player_ratings"):
        add(findings, "ERROR", "Ratings", "Normalized rating tables are missing.")
        return

    expected = int(con.execute("SELECT COUNT(*) FROM rating_definitions").fetchone()[0] or 0)
    if expected <= 0:
        add(findings, "ERROR", "Ratings", "No rating definitions found.")
        return

    clause, params = team_filter_clause(team_id, "p")
    for row in con.execute(
        f"""
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            COALESCE(t.abbreviation, p.status) AS team,
            p.position,
            COUNT(pr.rating_key) AS rating_count
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        LEFT JOIN player_ratings pr
            ON pr.player_id = p.player_id
           AND pr.season = ?
        WHERE COALESCE(p.status, 'Active') NOT IN ('Retired')
          {clause}
        GROUP BY p.player_id
        HAVING COUNT(pr.rating_key) < ?
        ORDER BY team, p.position, player_name
        """,
        (season, *params, expected),
    ):
        add(
            findings,
            "ERROR",
            "Ratings",
            f"{row['team']} {row['player_name']} ({row['position']}) has {row['rating_count']}/{expected} normalized ratings.",
        )

    for row in con.execute(
        f"""
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            COALESCE(t.abbreviation, p.status) AS team,
            p.position
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        LEFT JOIN player_role_scores prs
            ON prs.player_id = p.player_id
           AND prs.season = ?
           AND prs.scheme_key = 'default'
        WHERE COALESCE(p.status, 'Active') NOT IN ('Retired')
          AND p.position NOT IN ('K', 'P', 'LS')
          AND prs.player_id IS NULL
          {clause}
        ORDER BY team, p.position, player_name
        """,
        (season, *params),
    ):
        add(
            findings,
            "WARN",
            "Ratings",
            f"{row['team']} {row['player_name']} ({row['position']}) has no default role score.",
        )

    for row in con.execute(
        f"""
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            COALESCE(t.abbreviation, p.status) AS team,
            p.position,
            GROUP_CONCAT(pr.rating_key) AS present_keys
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        LEFT JOIN player_ratings pr
            ON pr.player_id = p.player_id
           AND pr.season = ?
           AND pr.rating_key IN ('kick_power', 'kick_accuracy', 'composure')
        WHERE COALESCE(p.status, 'Active') NOT IN ('Retired')
          AND p.position IN ('K', 'P', 'LS')
          {clause}
        GROUP BY p.player_id
        """,
        (season, *params),
    ):
        present = set((row["present_keys"] or "").split(",")) - {""}
        missing = sorted(SPECIALIST_RATINGS - present)
        if missing:
            add(
                findings,
                "ERROR",
                "Ratings",
                f"{row['team']} {row['player_name']} ({row['position']}) missing specialist ratings: {', '.join(missing)}.",
            )


def audit_coaches(con: sqlite3.Connection, teams: list[sqlite3.Row], findings: list[Finding]) -> None:
    if not table_exists(con, "coaches"):
        add(findings, "WARN", "Coaches", "Coaches table is missing.")
        return
    has_position_ratings = table_exists(con, "coach_position_ratings")
    if not has_position_ratings:
        add(findings, "WARN", "Coaches", "Coach position rating table is missing.")

    for team in teams:
        team_id = int(team["team_id"])
        label = team["abbreviation"]
        rows = con.execute(
            "SELECT * FROM coaches WHERE team_id = ? ORDER BY role",
            (team_id,),
        ).fetchall()
        roles = {row["role"] for row in rows}
        names = [row["name"] for row in rows]

        missing_roles = sorted(COACH_ROLES - roles)
        if missing_roles:
            add(findings, "ERROR", "Coaches", f"{label} is missing coach roles: {', '.join(missing_roles)}.")
        if len(rows) < 3:
            add(findings, "ERROR", "Coaches", f"{label} has only {len(rows)} coaches assigned.")
        if len(set(names)) < len(names):
            add(findings, "WARN", "Coaches", f"{label} has duplicate coach names assigned across roles.")

        for row in rows:
            if row["overall"] is None or not 1 <= int(row["overall"]) <= 20:
                add(findings, "WARN", "Coaches", f"{label} {row['role']} {row['name']} has invalid overall {row['overall']}.")

            if not has_position_ratings:
                continue
            rating_rows = con.execute(
                """
                SELECT position_group, rating
                FROM coach_position_ratings
                WHERE coach_id = ?
                """,
                (row["coach_id"],),
            ).fetchall()
            groups = {rating["position_group"] for rating in rating_rows}
            missing_groups = sorted(COACH_GROUPS - groups)
            if missing_groups:
                add(
                    findings,
                    "WARN",
                    "Coaches",
                    f"{label} {row['role']} {row['name']} missing group ratings: {', '.join(missing_groups)}.",
                )
            for rating in rating_rows:
                if not 1 <= int(rating["rating"]) <= 20:
                    add(
                        findings,
                        "WARN",
                        "Coaches",
                        f"{label} {row['role']} {row['name']} has invalid {rating['position_group']} rating {rating['rating']}.",
                    )


def print_findings(findings: list[Finding]) -> None:
    if not findings:
        print("Audit complete: no findings.")
        return

    by_category: dict[tuple[str, str], list[Finding]] = defaultdict(list)
    for finding in findings:
        by_category[(finding.severity, finding.category)].append(finding)

    print("Audit findings:")
    for severity in ("ERROR", "WARN"):
        for (_sev, category), items in sorted(by_category.items(), key=lambda item: item[0][1]):
            if _sev != severity:
                continue
            print(f"\n{severity} - {category}: {len(items)}")
            for finding in items[:MAX_ITEMS_PER_CATEGORY]:
                print(f"  - {finding.message}")
            if len(items) > MAX_ITEMS_PER_CATEGORY:
                print(f"  ...and {len(items) - MAX_ITEMS_PER_CATEGORY} more")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit NFL GM Sim database readiness.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--season", type=int, default=match_engine.DEFAULT_SEASON)
    parser.add_argument("--team", help="Limit audit to one team abbreviation.")
    parser.add_argument("--strict", action="store_true", help="Return exit code 1 when errors exist.")
    args = parser.parse_args()

    if not args.db.exists():
        raise FileNotFoundError(args.db)

    con = connect(args.db)
    try:
        teams = selected_teams(con, args.team)
        team_id = int(teams[0]["team_id"]) if args.team else None
        findings: list[Finding] = []

        audit_team_rosters(con, teams, findings)
        audit_depth_slots(con, teams, findings)
        audit_depth_integrity(con, team_id, findings)
        audit_ratings(con, args.season, team_id, findings)
        audit_coaches(con, teams, findings)
        print_findings(findings)

        errors = sum(1 for finding in findings if finding.severity == "ERROR")
        warnings = sum(1 for finding in findings if finding.severity == "WARN")
        print(f"\nSummary: {errors} error(s), {warnings} warning(s).")
        return 1 if args.strict and errors else 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
