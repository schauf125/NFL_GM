from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


# Ourlads Minnesota roster, checked 2026-05-17:
# https://www.ourlads.com/nfldepthcharts/roster/MIN
OURLADS_VIKINGS_NUMBERS = {
    2243: 95,  # Caleb Banks
    2244: 41,  # Jake Golday
    2245: 97,  # Domonique Orange
    2246: 78,  # Caleb Tiernan
    2247: 8,   # Jakobe Thomas
    2248: 45,  # Max Bredeson
    2249: 20,  # Charles Demmings
    2250: 21,  # Demond Claiborne
    2251: 58,  # Gavin Gerhardt
}


def apply_numbers(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            for player_id, jersey_number in OURLADS_VIKINGS_NUMBERS.items():
                conn.execute(
                    "update players set jersey_number=? where player_id=? and team_id=24",
                    (jersey_number, player_id),
                )
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Ourlads jersey numbers for restored Vikings 2026 rookies.")
    parser.add_argument("db", type=Path)
    args = parser.parse_args()
    apply_numbers(args.db)
    print(f"Applied Ourlads Vikings rookie jersey numbers to {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
