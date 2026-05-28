from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


MANUAL_POSITION_OVERRIDES = {
    2138: "ILB",  # Tyrel Dodson
    2139: "ILB",  # Willie Gay Jr.
    2140: "SS",   # Ronnie Harrison Jr.
    2513: "ILB",
    2526: "ILB",
    2529: "ILB",
    2532: "ILB",
    2597: "ILB",
    2608: "EDGE",
    2632: "EDGE",
    2640: "EDGE",
    2663: "ILB",
}

NAME_FIXES = {
    2090: ("Le'Veon", "Moss"),
}

EXPERIENCE_OVERRIDES = {
    ("D'Ernest", "Johnson", "RB"): 7,
    ("Justin", "Osborne", "C"): 0,
    ("Leander", "Wiegand", "OG"): 0,
    ("Michael", "Danna", "EDGE"): 6,
    ("Savion", "Washington", "OT"): 0,
    ("Taybor", "Pepper", "LS"): 8,
    ("Tomon", "Fox", "EDGE"): 3,
}


def safety_position(row: sqlite3.Row) -> str:
    tackle = int(row["tackle"] or 0)
    coverage = int(row["coverage"] or 0)
    weight = int(row["weight_lbs"] or 0)
    # Box-heavy safeties and bigger bodies land at SS; rangier coverage profiles at FS.
    if tackle > coverage + 2 or weight >= 205:
        return "SS"
    return "FS"


def update_position(conn: sqlite3.Connection, player_id: int, position: str) -> None:
    conn.execute("update players set position=? where player_id=?", (position, player_id))


def delete_player_everywhere(conn: sqlite3.Connection, player_id: int) -> None:
    tables = [
        row[0]
        for row in conn.execute(
            "select name from sqlite_master where type='table' order by name"
        )
    ]
    for table in tables:
        cols = [row[1] for row in conn.execute(f"pragma table_info({table})")]
        if "player_id" in cols:
            conn.execute(f"delete from {table} where player_id=?", (player_id,))


def merge_jalon_kilgore(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        select player_id
        from players
        where lower(first_name)='jalon' and lower(last_name)='kilgore' and team_id=1
        order by player_id
        """
    ).fetchall()
    ids = [int(row["player_id"]) for row in rows]
    if 2225 not in ids or 2237 not in ids:
        return 0

    # Keep the SS row because it matches the player's natural roster group and already has
    # the headshot asset; move the stronger rookie-scale contract onto it.
    old_contracts = [row["contract_id"] for row in conn.execute("select contract_id from contracts where player_id=?", (2237,))]
    for contract_id in old_contracts:
        conn.execute("delete from contract_years where contract_id=?", (contract_id,))
        conn.execute("delete from contracts where contract_id=?", (contract_id,))
    conn.execute("update contracts set player_id=? where player_id=?", (2237, 2225))
    conn.execute("update contract_years set player_id=? where player_id=?", (2237, 2225))
    delete_player_everywhere(conn, 2225)
    conn.execute(
        """
        update players
        set position='SS',
            accolades='["167th Pick 2026"]'
        where player_id=2237
        """
    )
    return 1


def repair(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    stats = {
        "name_fixes": 0,
        "position_fixes": 0,
        "experience_fixes": 0,
        "potential_fixes": 0,
        "duplicate_players_merged": 0,
    }
    try:
        with conn:
            for player_id, (first, last) in NAME_FIXES.items():
                before = conn.execute(
                    "select first_name,last_name from players where player_id=?", (player_id,)
                ).fetchone()
                if before and (before["first_name"], before["last_name"]) != (first, last):
                    conn.execute(
                        "update players set first_name=?, last_name=? where player_id=?",
                        (first, last, player_id),
                    )
                    stats["name_fixes"] += 1

            for player_id, position in MANUAL_POSITION_OVERRIDES.items():
                row = conn.execute("select position from players where player_id=?", (player_id,)).fetchone()
                if row and row["position"] != position:
                    update_position(conn, player_id, position)
                    stats["position_fixes"] += 1

            for first, last, position in EXPERIENCE_OVERRIDES:
                row = conn.execute(
                    """
                    select years_exp
                    from players
                    where first_name=? and last_name=? and position=?
                    """,
                    (first, last, position),
                ).fetchone()
                if row and int(row["years_exp"] or 0) != EXPERIENCE_OVERRIDES[(first, last, position)]:
                    conn.execute(
                        """
                        update players
                        set years_exp=?
                        where first_name=? and last_name=? and position=?
                        """,
                        (EXPERIENCE_OVERRIDES[(first, last, position)], first, last, position),
                    )
                    stats["experience_fixes"] += 1

            result = conn.execute(
                """
                update players
                set years_exp=0
                where coalesce(is_rookie, 0)=1
                  and coalesce(years_exp, 0)<>0
                """
            )
            stats["experience_fixes"] += int(result.rowcount or 0)

            for row in conn.execute(
                "select player_id, tackle, coverage, weight_lbs from players where position='S'"
            ).fetchall():
                update_position(conn, int(row["player_id"]), safety_position(row))
                stats["position_fixes"] += 1

            # Keep potential from being lower than current overall; that can break simple
            # progression expectations and was only seen on aging free-agent safety imports.
            result = conn.execute(
                "update players set potential=overall where potential < overall"
            )
            stats["potential_fixes"] += int(result.rowcount or 0)

            stats["duplicate_players_merged"] += merge_jalon_kilgore(conn)
    finally:
        conn.close()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair obvious league-wide player data quality issues.")
    parser.add_argument("db", type=Path)
    args = parser.parse_args()
    stats = repair(args.db)
    print(f"Repaired {args.db}: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
