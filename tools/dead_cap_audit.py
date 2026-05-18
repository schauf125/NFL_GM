#!/usr/bin/env python3
"""Audit contract-year dead-cap and cap accounting sanity."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from tools import setup_contract_years  # noqa: E402


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def money(value: Any) -> str:
    amount = int(value or 0)
    if abs(amount) >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    return f"${amount:,}"


def audit(con: sqlite3.Connection, *, season: int | None = None, limit: int = 50) -> dict[str, list[dict[str, Any]]]:
    setup_contract_years.ensure_schema(con)
    params: list[Any] = []
    season_sql = ""
    if season is not None:
        season_sql = " AND cy.season = ?"
        params.append(season)

    negative_rows = con.execute(
        f"""
        SELECT cy.*, p.first_name || ' ' || p.last_name AS player_name, t.abbreviation AS team
        FROM contract_years cy
        JOIN players p ON p.player_id = cy.player_id
        JOIN teams t ON t.team_id = cy.team_id
        WHERE (
            cy.base_salary < 0 OR cy.cap_hit < 0 OR cy.cash_due < 0
            OR cy.dead_cap_if_cut_pre_june1 < 0
            OR cy.dead_cap_if_cut_post_june1_current < 0
            OR cy.dead_cap_if_cut_post_june1_next < 0
        )
        {season_sql}
        ORDER BY cy.season, team, player_name
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()

    split_rows = con.execute(
        f"""
        SELECT cy.*, p.first_name || ' ' || p.last_name AS player_name, t.abbreviation AS team
        FROM contract_years cy
        JOIN players p ON p.player_id = cy.player_id
        JOIN teams t ON t.team_id = cy.team_id
        WHERE cy.dead_cap_if_cut_post_june1_current + cy.dead_cap_if_cut_post_june1_next
              > cy.dead_cap_if_cut_pre_june1 + 1000
        {season_sql}
        ORDER BY cy.season, team, player_name
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()

    inactive_rows = con.execute(
        f"""
        SELECT cy.*, c.is_active AS contract_active,
               p.first_name || ' ' || p.last_name AS player_name, t.abbreviation AS team
        FROM contract_years cy
        JOIN contracts c ON c.contract_id = cy.contract_id
        JOIN players p ON p.player_id = cy.player_id
        JOIN teams t ON t.team_id = cy.team_id
        WHERE c.is_active = 0
          AND cy.is_active = 1
        {season_sql}
        ORDER BY cy.season, team, player_name
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()

    missing_dead_rows = con.execute(
        f"""
        SELECT cy.*, c.signing_bonus, c.is_guaranteed,
               p.first_name || ' ' || p.last_name AS player_name, t.abbreviation AS team
        FROM contract_years cy
        JOIN contracts c ON c.contract_id = cy.contract_id
        JOIN players p ON p.player_id = cy.player_id
        JOIN teams t ON t.team_id = cy.team_id
        WHERE c.is_active = 1
          AND (COALESCE(c.signing_bonus, 0) > 0 OR COALESCE(c.is_guaranteed, 0) = 1)
          AND cy.dead_cap_if_cut_pre_june1 = 0
        {season_sql}
        ORDER BY cy.season, team, player_name
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()

    return {
        "negative_values": [dict(row) for row in negative_rows],
        "bad_post_june_split": [dict(row) for row in split_rows],
        "inactive_contract_active_years": [dict(row) for row in inactive_rows],
        "missing_dead_cap": [dict(row) for row in missing_dead_rows],
    }


def print_rows(label: str, rows: list[dict[str, Any]]) -> None:
    print(f"{label}: {len(rows)}")
    for row in rows[:20]:
        print(
            f"  {row.get('season')} {row.get('team')} {row.get('player_name')} "
            f"cap={money(row.get('cap_hit'))} dead={money(row.get('dead_cap_if_cut_pre_june1'))} "
            f"post={money(row.get('dead_cap_if_cut_post_june1_current'))}/{money(row.get('dead_cap_if_cut_post_june1_next'))}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit contract dead-cap accounting.")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--season", type=int)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    with connect(Path(args.db)) as con:
        result = audit(con, season=args.season, limit=args.limit)
    for key, rows in result.items():
        print_rows(key, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
