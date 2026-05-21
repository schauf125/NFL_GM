#!/usr/bin/env python3
"""Audit offseason free-agency outcomes for repeated CPU sanity issues."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "database" / "nfl_gm.db"


GROUP_BY_POS = {
    "QB": "QB",
    "RB": "RB",
    "FB": "RB",
    "WR": "WR",
    "SWR": "WR",
    "TE": "TE",
    "LT": "OT",
    "RT": "OT",
    "OT": "OT",
    "LG": "IOL",
    "RG": "IOL",
    "OG": "IOL",
    "C": "IOL",
    "EDGE": "EDGE",
    "DE": "EDGE",
    "OLB": "EDGE",
    "IDL": "IDL",
    "DT": "IDL",
    "NT": "IDL",
    "LB": "LB",
    "ILB": "LB",
    "MLB": "LB",
    "CB": "CB",
    "NB": "CB",
    "FS": "S",
    "SS": "S",
    "S": "S",
    "K": "ST",
    "P": "ST",
    "LS": "ST",
}

STARTER_SLOTS = {
    "QB": 1,
    "RB": 2,
    "WR": 3,
    "TE": 1,
    "OT": 2,
    "IOL": 3,
    "EDGE": 2,
    "IDL": 2,
    "LB": 2,
    "CB": 3,
    "S": 2,
    "ST": 1,
}

PREMIUM_AAV = {
    "QB": 14_000_000,
    "RB": 8_000_000,
    "WR": 13_000_000,
    "TE": 10_000_000,
    "OT": 14_000_000,
    "IOL": 10_000_000,
    "EDGE": 13_000_000,
    "IDL": 13_000_000,
    "LB": 10_000_000,
    "CB": 12_000_000,
    "S": 10_000_000,
    "ST": 3_000_000,
}


def money(value: Any) -> str:
    if value is None:
        return "-"
    return f"${int(value) / 1_000_000:.1f}M"


def group_for(pos: str) -> str:
    return GROUP_BY_POS.get(str(pos or "").upper(), str(pos or "").upper())


def active_db_from_registry(root: Path) -> Path:
    saves = root / "saves"
    dbs = sorted(saves.glob("*/nfl_gm_save.db"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not dbs:
        raise FileNotFoundError(f"No save DBs found under {saves}")
    return dbs[0]


def audit(db: Path, league_year: int | None, *, fail: bool) -> int:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    if league_year is None:
        period = con.execute("SELECT MAX(league_year) AS league_year FROM free_agency_periods").fetchone()
        league_year = int(period["league_year"] or 0) if period else 0
    issues: list[str] = []

    print(f"Free Agency Audit: {db}")
    print(f"League year: {league_year}")

    print("\nCap Space Below $4M")
    cap_rows = con.execute(
        """
        SELECT abbreviation, cap_space
        FROM team_cap_view
        WHERE cap_space < 4000000
        ORDER BY cap_space
        """
    ).fetchall()
    if not cap_rows:
        print("  none")
    for row in cap_rows:
        line = f"  {row['abbreviation']}: {money(row['cap_space'])}"
        print(line)
        issues.append(f"low_cap:{row['abbreviation']}")

    print("\nDuplicate Active Contracts")
    duplicate_rows = con.execute(
        """
        SELECT p.first_name || ' ' || p.last_name AS player_name, p.position, COUNT(*) AS contracts
        FROM contracts c
        JOIN players p ON p.player_id = c.player_id
        WHERE c.is_active = 1
          AND COALESCE(c.start_year, ?) <= ?
          AND COALESCE(c.end_year, ?) >= ?
        GROUP BY c.player_id
        HAVING COUNT(*) > 1
        ORDER BY contracts DESC, player_name
        """,
        (league_year, league_year, league_year, league_year),
    ).fetchall()
    if not duplicate_rows:
        print("  none")
    for row in duplicate_rows:
        print(f"  {row['player_name']} {row['position']}: {row['contracts']} active contracts")
        issues.append(f"duplicate_contract:{row['player_name']}")

    signed_rows = con.execute(
        """
        WITH active_deals AS (
            SELECT
                c.player_id,
                c.team_id,
                c.aav,
                MAX(c.contract_id) AS contract_id
            FROM contracts c
            WHERE c.is_active = 1
              AND COALESCE(c.start_year, ?) <= ?
              AND COALESCE(c.end_year, ?) >= ?
            GROUP BY c.player_id, c.team_id, c.aav
        )
        SELECT
            t.abbreviation,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            COALESCE(p.overall, 50) AS overall,
            COALESCE(p.potential, p.overall, 50) AS potential,
            o.aav,
            o.years,
            o.notes
        FROM free_agency_player_markets m
        JOIN players p ON p.player_id = m.player_id
        JOIN teams t ON t.team_id = m.signed_team_id
        LEFT JOIN free_agency_offers o ON o.offer_id = m.signed_offer_id
        JOIN active_deals ad
          ON ad.player_id = m.player_id
         AND ad.team_id = m.signed_team_id
         AND ad.aav = o.aav
        WHERE m.league_year = ?
          AND m.status = 'signed'
        ORDER BY o.aav DESC
        """,
        (league_year, league_year, league_year, league_year, league_year),
    ).fetchall()

    print("\nLow-70s / Aging Big Deals")
    bad_value = []
    for row in signed_rows:
        group = group_for(row["position"])
        aav = int(row["aav"] or 0)
        overall = int(row["overall"] or 0)
        age = int(row["age"] or 0)
        if aav >= 12_000_000 and (overall <= 73 or (overall <= 75 and age >= 31)):
            bad_value.append(row)
    if not bad_value:
        print("  none")
    for row in bad_value[:25]:
        print(
            f"  {row['abbreviation']}: {row['player_name']} {row['position']} "
            f"age {row['age']} {row['overall']}/{row['potential']} at {money(row['aav'])}"
        )
        issues.append(f"bad_value:{row['abbreviation']}:{row['player_name']}")

    print("\nBridge QB Overpays")
    bridge_qbs = []
    for row in signed_rows:
        if group_for(row["position"]) != "QB":
            continue
        aav = int(row["aav"] or 0)
        years = int(row["years"] or 1)
        overall = int(row["overall"] or 0)
        potential = int(row["potential"] or overall)
        if overall <= 76 and potential < 82 and (aav > 16_500_000 or years > 1):
            bridge_qbs.append(row)
        elif overall < 78 and potential < 84 and years > 2:
            bridge_qbs.append(row)
    if not bridge_qbs:
        print("  none")
    for row in bridge_qbs[:15]:
        print(
            f"  {row['abbreviation']}: {row['player_name']} {row['overall']}/{row['potential']} "
            f"{row['years']}y at {money(row['aav'])}"
        )
        issues.append(f"bridge_qb:{row['abbreviation']}:{row['player_name']}")

    print("\nPremium Position Stacking")
    grouped: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in signed_rows:
        grouped.setdefault((str(row["abbreviation"]), group_for(row["position"])), []).append(row)
    for (team, group), rows in sorted(grouped.items()):
        premium_rows = [row for row in rows if int(row["aav"] or 0) >= PREMIUM_AAV.get(group, 10_000_000)]
        if len(premium_rows) > STARTER_SLOTS.get(group, 2):
            total = sum(int(row["aav"] or 0) for row in premium_rows)
            names = ", ".join(f"{row['player_name']} {money(row['aav'])}" for row in premium_rows[:5])
            print(f"  {team} {group}: {len(premium_rows)} premium FA deals, {money(total)} total - {names}")
            issues.append(f"stacking:{team}:{group}")
    if not any(issue.startswith("stacking:") for issue in issues):
        print("  none")

    print("\nQB Starter-Path Issues")
    qb_rooms = con.execute(
        """
        SELECT
            t.abbreviation,
            p.first_name || ' ' || p.last_name AS player_name,
            p.age,
            COALESCE(p.overall, 50) AS overall,
            COALESCE(p.potential, p.overall, 50) AS potential,
            COALESCE(c.aav, 0) AS aav
        FROM players p
        JOIN teams t ON t.team_id = p.team_id
        LEFT JOIN contracts c
          ON c.player_id = p.player_id
         AND c.team_id = p.team_id
         AND c.is_active = 1
         AND COALESCE(c.start_year, ?) <= ?
         AND COALESCE(c.end_year, ?) >= ?
        WHERE p.status = 'Active'
          AND p.position = 'QB'
        ORDER BY t.abbreviation, overall DESC, potential DESC
        """,
        (league_year, league_year, league_year, league_year),
    ).fetchall()
    rooms: dict[str, list[sqlite3.Row]] = {}
    for row in qb_rooms:
        rooms.setdefault(str(row["abbreviation"]), []).append(row)
    any_qb = False
    for team, rows in rooms.items():
        if len(rows) < 2:
            continue
        best = int(rows[0]["overall"] or 0)
        paid_backups = [
            row for row in rows[1:]
            if int(row["aav"] or 0) >= 8_500_000
            and int(row["overall"] or 0) <= best - 4
            and not (
                best <= 84
                and int(row["potential"] or 0) >= 90
                and int(row["age"] or 99) <= 24
                and int(row["aav"] or 0) <= 12_000_000
            )
        ]
        if best >= 82 and paid_backups:
            any_qb = True
            names = ", ".join(f"{row['player_name']} {row['overall']}/{row['potential']} {money(row['aav'])}" for row in paid_backups)
            print(f"  {team}: established QB {rows[0]['player_name']} {best}, expensive blocked QB(s): {names}")
            issues.append(f"qb_path:{team}")
    if not any_qb:
        print("  none")

    print(f"\nIssue count: {len(issues)}")
    return 1 if fail and issues else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--save-root", type=Path, default=ROOT)
    parser.add_argument("--league-year", type=int)
    parser.add_argument("--fail", action="store_true")
    args = parser.parse_args()
    db = args.db or active_db_from_registry(args.save_root)
    return audit(db, args.league_year, fail=args.fail)


if __name__ == "__main__":
    raise SystemExit(main())
