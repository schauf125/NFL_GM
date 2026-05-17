from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


SOURCE_DB = Path(r"Z:\NFL_GM_SIM_MISC_Files\backup_20260505_064723\database\clean_nfl_gm.db")

# Players briefly removed while testing strict jersey-number enforcement. Offseason camp
# bodies are allowed to be unnumbered until cutdown, so these should stay on their clubs.
PLAYER_IDS = [
    2009, 2010, 2064,
    2527, 2567, 2573, 2596,
    2609, 2614,
    3104,
    2432, 2433, 2434, 2496,
    2701, 2707, 2708, 2716,
    1336, 1339, 1340,
]


def cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"pragma table_info({table})")]


def insert_row(
    conn: sqlite3.Connection,
    table: str,
    row: sqlite3.Row,
    *,
    omit: set[str] | None = None,
    overrides: dict[str, object] | None = None,
) -> int:
    omit = omit or set()
    overrides = overrides or {}
    target_cols = cols(conn, table)
    source_cols = set(row.keys())
    names: list[str] = []
    values: list[object] = []
    for col in target_cols:
        if col in omit:
            continue
        if col in overrides:
            names.append(col)
            values.append(overrides[col])
        elif col in source_cols:
            names.append(col)
            values.append(row[col])
    conn.execute(
        f"insert into {table} ({','.join(names)}) values ({','.join('?' for _ in names)})",
        values,
    )
    return int(conn.execute("select last_insert_rowid()").fetchone()[0])


def restore(target_db: Path, source_db: Path) -> int:
    source = sqlite3.connect(source_db)
    target = sqlite3.connect(target_db)
    source.row_factory = sqlite3.Row
    target.row_factory = sqlite3.Row
    restored = 0
    try:
        with target:
            for player_id in PLAYER_IDS:
                player = source.execute(
                    "select team_id, status, jersey_number from players where player_id=?",
                    (player_id,),
                ).fetchone()
                if not player:
                    continue
                target.execute(
                    """
                    update players
                    set team_id=?, status=?, jersey_number=?
                    where player_id=?
                    """,
                    (player["team_id"], player["status"], player["jersey_number"], player_id),
                )
                target.execute("delete from contract_years where player_id=?", (player_id,))
                target.execute("delete from contracts where player_id=?", (player_id,))
                old_to_new: dict[int, int] = {}
                for contract in source.execute(
                    "select * from contracts where player_id=? order by contract_id",
                    (player_id,),
                ).fetchall():
                    new_id = insert_row(target, "contracts", contract, omit={"contract_id"})
                    old_to_new[int(contract["contract_id"])] = new_id
                for year in source.execute(
                    "select * from contract_years where player_id=? order by contract_year_id",
                    (player_id,),
                ).fetchall():
                    old_contract = int(year["contract_id"])
                    if old_contract in old_to_new:
                        insert_row(
                            target,
                            "contract_years",
                            year,
                            omit={"contract_year_id"},
                            overrides={"contract_id": old_to_new[old_contract]},
                        )
                restored += 1
    finally:
        source.close()
        target.close()
    return restored


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore camp bodies removed during strict jersey number testing.")
    parser.add_argument("target_db", type=Path)
    parser.add_argument("--source-db", type=Path, default=SOURCE_DB)
    args = parser.parse_args()
    count = restore(args.target_db, args.source_db)
    print(f"Restored {count} camp bodies in {args.target_db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
