"""Align legacy physical columns with normalized player_ratings.

Several older systems still read players.speed/agility/strength while the
modern sim and UI use player_ratings. This keeps those legacy columns in sync
with the detailed ratings so startup variance, roster views, and future exports
do not disagree about the same player.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "database" / "nfl_gm.db"
PHYSICAL_KEYS = ("speed", "agility", "strength")


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def best_season(conn: sqlite3.Connection, requested: int | None) -> int:
    order_clause = (
        "ORDER BY CASE WHEN season = ? THEN 0 ELSE 1 END, rows DESC, season DESC"
        if requested is not None
        else "ORDER BY season DESC, rows DESC"
    )
    params = (requested,) if requested is not None else ()
    row = conn.execute(
        f"""
        SELECT season, COUNT(*) AS rows
        FROM player_ratings
        GROUP BY season
        {order_clause}
        LIMIT 1
        """,
        params,
    ).fetchone()
    return int(row["season"]) if row else (requested or 2026)


def divergence_rows(conn: sqlite3.Connection, season: int, threshold: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            WITH detail AS (
                SELECT
                    player_id,
                    MAX(CASE WHEN rating_key = 'speed' THEN rating_value END) AS speed,
                    MAX(CASE WHEN rating_key = 'agility' THEN rating_value END) AS agility,
                    MAX(CASE WHEN rating_key = 'strength' THEN rating_value END) AS strength
                FROM player_ratings
                WHERE season = ?
                GROUP BY player_id
            )
            SELECT
                p.player_id,
                p.first_name || ' ' || p.last_name AS name,
                p.position,
                COALESCE(t.abbreviation, 'FA') AS team,
                COALESCE(p.speed, 0) AS base_speed,
                detail.speed AS detail_speed,
                COALESCE(p.agility, 0) AS base_agility,
                detail.agility AS detail_agility,
                COALESCE(p.strength, 0) AS base_strength,
                detail.strength AS detail_strength
            FROM players p
            JOIN detail ON detail.player_id = p.player_id
            LEFT JOIN teams t ON t.team_id = p.team_id
            WHERE COALESCE(p.status, 'Active') != 'Retired'
              AND (
                    (detail.speed IS NOT NULL AND ABS(COALESCE(p.speed, 0) - detail.speed) >= ?)
                 OR (detail.agility IS NOT NULL AND ABS(COALESCE(p.agility, 0) - detail.agility) >= ?)
                 OR (detail.strength IS NOT NULL AND ABS(COALESCE(p.strength, 0) - detail.strength) >= ?)
              )
            ORDER BY p.position, name
            """,
            (season, threshold, threshold, threshold),
        )
    )


def align(conn: sqlite3.Connection, season: int) -> int:
    cursor = conn.execute(
        """
        WITH detail AS (
            SELECT
                player_id,
                MAX(CASE WHEN rating_key = 'speed' THEN rating_value END) AS speed,
                MAX(CASE WHEN rating_key = 'agility' THEN rating_value END) AS agility,
                MAX(CASE WHEN rating_key = 'strength' THEN rating_value END) AS strength
            FROM player_ratings
            WHERE season = ?
            GROUP BY player_id
        )
        UPDATE players
           SET speed = COALESCE((SELECT speed FROM detail WHERE detail.player_id = players.player_id), speed),
               agility = COALESCE((SELECT agility FROM detail WHERE detail.player_id = players.player_id), agility),
               strength = COALESCE((SELECT strength FROM detail WHERE detail.player_id = players.player_id), strength)
         WHERE player_id IN (SELECT player_id FROM detail)
        """,
        (season,),
    )
    return int(cursor.rowcount or 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--season", type=int, default=None, help="Rating season to use. Defaults to latest season in player_ratings.")
    parser.add_argument("--threshold", type=int, default=8)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        season = best_season(conn, args.season)
        rows = divergence_rows(conn, season, args.threshold)
        print(f"Base physical alignment: {args.db} season {season}")
        print(f"Divergences >= {args.threshold}: {len(rows)}")
        for row in rows[:80]:
            print(
                f"#{row['player_id']} {row['name']} {row['position']} {row['team']}: "
                f"speed {row['base_speed']}->{row['detail_speed']}, "
                f"agility {row['base_agility']}->{row['detail_agility']}, "
                f"strength {row['base_strength']}->{row['detail_strength']}"
            )
        if args.apply:
            updated = align(conn, season)
            conn.commit()
            remaining = len(divergence_rows(conn, season, args.threshold))
            print(f"Updated rows touched: {updated if updated >= 0 else 'unknown'}")
            print(f"Remaining divergences >= {args.threshold}: {remaining}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
