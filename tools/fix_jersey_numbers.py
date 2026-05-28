from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import jersey_numbers


def fix_db(db_path: Path, protected: set[int] | None = None) -> tuple[int, int]:
    # Backward-compatible entry point for older maintenance commands.
    # The shared helper now owns both rules and assignment preferences.
    protected = protected or set()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        result = jersey_numbers.assign_missing_numbers(conn, source="fix_jersey_numbers")
        conn.commit()
    return int(result["changed"]), int(result["unresolved"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Assign valid unique jersey numbers by team.")
    parser.add_argument("db", type=Path)
    parser.add_argument(
        "--protect-player",
        action="append",
        default=[],
        type=int,
        help="Retained for compatibility; established legal numbers already win conflicts.",
    )
    args = parser.parse_args()
    changed, unresolved = fix_db(args.db, set(args.protect_player))
    print(f"Updated {changed} jersey numbers in {args.db}; unresolved={unresolved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
