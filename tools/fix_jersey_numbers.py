from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


NUMBER_RANGES = {
    "QB": [(0, 19)],
    "RB": [(0, 49)],
    "FB": [(0, 49)],
    "WR": [(0, 49), (80, 89)],
    "TE": [(0, 49), (80, 89)],
    "C": [(50, 79)],
    "OG": [(50, 79)],
    "OT": [(50, 79)],
    "IDL": [(50, 79), (90, 99)],
    "EDGE": [(0, 59), (90, 99)],
    "ILB": [(0, 59), (90, 99)],
    "LB": [(0, 59), (90, 99)],
    "CB": [(0, 49)],
    "NB": [(0, 49)],
    "FS": [(0, 49)],
    "SS": [(0, 49)],
    "K": [(0, 49)],
    "P": [(0, 49)],
    "LS": [(40, 49), (50, 79)],
}


def expanded(position: str) -> list[int]:
    ranges = NUMBER_RANGES.get(position, [(0, 99)])
    numbers: list[int] = []
    for start, end in ranges:
        numbers.extend(range(start, end + 1))
    return numbers


def valid(position: str, number: object) -> bool:
    try:
        return int(number) in expanded(position)
    except (TypeError, ValueError):
        return False


def priority(row: sqlite3.Row, protected: set[int] | None = None) -> tuple[int, int, int, int, int]:
    # Keep established/high-impact players' numbers before changing rookies or fringe players.
    protected = protected or set()
    return (
        0 if row["player_id"] in protected else 1,
        int(row["is_rookie"] or 0),
        -int(row["years_exp"] or 0),
        -int(row["overall"] or 0),
        int(row["player_id"]),
    )


def choose_number(position: str, used: set[int]) -> int | None:
    for candidate in expanded(position):
        if candidate not in used:
            return candidate
    return None


def fix_db(db_path: Path, protected: set[int] | None = None) -> tuple[int, int]:
    protected = protected or set()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    changed = 0
    unresolved = 0
    try:
        teams = [
            row[0]
            for row in conn.execute(
                "select distinct team_id from players where team_id is not null order by team_id"
            )
        ]
        with conn:
            for team_id in teams:
                rows = conn.execute(
                    """
                    select player_id, first_name, last_name, position, team_id, jersey_number,
                           is_rookie, years_exp, overall
                    from players
                    where team_id=?
                    order by player_id
                    """,
                    (team_id,),
                ).fetchall()
                used: set[int] = set()
                for row in sorted(rows, key=lambda item: priority(item, protected)):
                    current = row["jersey_number"]
                    if valid(row["position"], current) and int(current) not in used:
                        number = int(current)
                    else:
                        number = choose_number(row["position"], used)
                    if number is None:
                        if row["jersey_number"] is not None:
                            conn.execute(
                                "update players set jersey_number=null where player_id=?",
                                (row["player_id"],),
                            )
                            changed += 1
                        continue
                    used.add(number)
                    if row["jersey_number"] != number:
                        conn.execute(
                            "update players set jersey_number=? where player_id=?",
                            (number, row["player_id"]),
                        )
                        changed += 1
        return changed, unresolved
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Assign valid unique jersey numbers by team.")
    parser.add_argument("db", type=Path)
    parser.add_argument(
        "--protect-player",
        action="append",
        default=[],
        type=int,
        help="Player ID whose existing jersey number should win conflicts.",
    )
    args = parser.parse_args()
    changed, unresolved = fix_db(args.db, set(args.protect_player))
    print(f"Updated {changed} jersey numbers in {args.db}; unresolved={unresolved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
