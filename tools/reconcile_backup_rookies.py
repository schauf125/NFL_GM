from __future__ import annotations

import argparse
import shutil
import sqlite3
from collections.abc import Iterable
from pathlib import Path


DEFAULT_SOURCE_DB = Path(
    r"Z:\NFL_GM_SIM_MISC_Files\backup_20260505_064723\database\clean_nfl_gm.db"
)
DEFAULT_SOURCE_GRAPHICS = Path(r"Z:\NFL_GM_SIM_MISC_Files\graphics backup\players")

RELATED_TABLES = [
    "player_ratings",
    "player_role_scores",
    "player_role_assignments",
    "player_qb_behavior_profiles",
    "player_rb_behavior_profiles",
    "player_receiver_behavior_profiles",
    "player_te_behavior_profiles",
    "player_ol_behavior_profiles",
    "player_idl_behavior_profiles",
    "player_edge_behavior_profiles",
    "player_lb_behavior_profiles",
    "player_secondary_behavior_profiles",
    "contracts",
    "contract_years",
    "player_graphics_assets",
]

AUTOINCREMENT_KEYS = {
    "player_graphics_assets": {"asset_id"},
    "player_role_assignments": {"assignment_id"},
    "contracts": {"contract_id"},
    "contract_years": {"contract_year_id"},
}

CONTRACT_TABLES = {"contracts", "contract_years"}

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


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "select 1 from sqlite_master where type='table' and name=?", (table,)
        ).fetchone()
        is not None
    )


def table_cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"pragma table_info({table})")]


def expanded_numbers(position: str) -> list[int]:
    ranges = NUMBER_RANGES.get(position, [(0, 99)])
    numbers: list[int] = []
    for start, end in ranges:
        numbers.extend(range(start, end + 1))
    return numbers


def valid_number(position: str, number: object) -> bool:
    try:
        parsed = int(number)
    except (TypeError, ValueError):
        return False
    return parsed in expanded_numbers(position)


def used_numbers(conn: sqlite3.Connection, team_id: int, restored_ids: set[int]) -> set[int]:
    rows = conn.execute(
        """
        select player_id, jersey_number
        from players
        where team_id=? and jersey_number is not null
        """,
        (team_id,),
    ).fetchall()
    used: set[int] = set()
    for row in rows:
        if row["player_id"] in restored_ids:
            continue
        try:
            used.add(int(row["jersey_number"]))
        except (TypeError, ValueError):
            continue
    return used


def assign_numbers(
    conn: sqlite3.Connection,
    restored_ids: Iterable[int],
) -> dict[int, int | None]:
    restored_ids = set(restored_ids)
    rows = conn.execute(
        f"""
        select player_id, team_id, position, jersey_number
        from players
        where player_id in ({",".join("?" for _ in restored_ids)})
        order by team_id, position, player_id
        """,
        tuple(restored_ids),
    ).fetchall()
    assigned: dict[int, int | None] = {}
    team_used_cache: dict[int, set[int]] = {}
    for row in rows:
        team_id = row["team_id"]
        if team_id is None:
            assigned[row["player_id"]] = row["jersey_number"]
            continue
        if team_id not in team_used_cache:
            team_used_cache[team_id] = used_numbers(conn, team_id, restored_ids)
        used = team_used_cache[team_id]
        current = row["jersey_number"]
        if current is not None and valid_number(row["position"], current) and int(current) not in used:
            number = int(current)
        else:
            number = next((candidate for candidate in expanded_numbers(row["position"]) if candidate not in used), None)
        if number is not None:
            used.add(number)
        assigned[row["player_id"]] = number
    return assigned


def insert_row(
    conn: sqlite3.Connection,
    table: str,
    row: sqlite3.Row,
    *,
    overrides: dict[str, object] | None = None,
    omit: set[str] | None = None,
) -> int | None:
    overrides = overrides or {}
    omit = omit or set()
    target_cols = table_cols(conn, table)
    source_cols = set(row.keys())
    cols: list[str] = []
    vals: list[object] = []
    for col in target_cols:
        if col in omit:
            continue
        if col in overrides:
            cols.append(col)
            vals.append(overrides[col])
        elif col in source_cols:
            cols.append(col)
            vals.append(row[col])
        elif col == "is_international_pathway":
            cols.append(col)
            vals.append(0)
    conn.execute(
        f"insert into {table} ({','.join(cols)}) values ({','.join('?' for _ in cols)})",
        vals,
    )
    return conn.execute("select last_insert_rowid()").fetchone()[0]


def missing_rookies(source: sqlite3.Connection, target: sqlite3.Connection) -> list[sqlite3.Row]:
    current_ids = {row[0] for row in target.execute("select player_id from players")}
    rows = source.execute(
        """
        select *
        from players
        where is_rookie=1
        order by team_id, player_id
        """
    ).fetchall()
    return [row for row in rows if row["player_id"] not in current_ids]


def rows_for_players(
    conn: sqlite3.Connection, table: str, player_ids: list[int]
) -> list[sqlite3.Row]:
    if not table_exists(conn, table) or not player_ids:
        return []
    placeholders = ",".join("?" for _ in player_ids)
    return conn.execute(
        f"select * from {table} where player_id in ({placeholders}) order by player_id",
        player_ids,
    ).fetchall()


def delete_rows(conn: sqlite3.Connection, table: str, player_ids: list[int]) -> None:
    if not table_exists(conn, table) or "player_id" not in table_cols(conn, table) or not player_ids:
        return
    placeholders = ",".join("?" for _ in player_ids)
    conn.execute(f"delete from {table} where player_id in ({placeholders})", player_ids)


def copy_contract_rows(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    player_ids: list[int],
) -> None:
    if not table_exists(source, "contracts") or not table_exists(target, "contracts"):
        return
    old_to_new: dict[int, int] = {}
    for row in rows_for_players(source, "contracts", player_ids):
        old_id = row["contract_id"]
        new_id = insert_row(target, "contracts", row, omit={"contract_id"})
        if new_id is not None:
            old_to_new[int(old_id)] = int(new_id)
    if not old_to_new or not table_exists(source, "contract_years") or not table_exists(target, "contract_years"):
        return
    for row in rows_for_players(source, "contract_years", player_ids):
        old_contract_id = int(row["contract_id"])
        if old_contract_id not in old_to_new:
            continue
        insert_row(
            target,
            "contract_years",
            row,
            overrides={"contract_id": old_to_new[old_contract_id]},
            omit={"contract_year_id"},
        )


def duplicate_season_rows(target: sqlite3.Connection, player_ids: list[int], season_from: int, season_to: int) -> None:
    if not player_ids:
        return
    placeholders = ",".join("?" for _ in player_ids)
    for table in RELATED_TABLES:
        if table in CONTRACT_TABLES or not table_exists(target, table):
            continue
        cols = table_cols(target, table)
        if "season" not in cols or "player_id" not in cols:
            continue
        target.execute(
            f"delete from {table} where season=? and player_id in ({placeholders})",
            (season_to, *player_ids),
        )
        rows = target.execute(
            f"select * from {table} where season=? and player_id in ({placeholders})",
            (season_from, *player_ids),
        ).fetchall()
        for row in rows:
            overrides = {"season": season_to}
            if "source" in cols:
                overrides["source"] = "reconciled_backup_rookie"
            insert_row(target, table, row, overrides=overrides, omit=AUTOINCREMENT_KEYS.get(table, set()))


def copy_graphics(repo_root: Path, player_ids: set[int], source_graphics: Path) -> None:
    if not source_graphics.exists():
        return
    for file in source_graphics.glob("*\\headshots\\*.png"):
        prefix = file.name.split("_", 1)[0]
        if not prefix.isdigit() or int(prefix) not in player_ids:
            continue
        team = file.parent.parent.name
        target_dir = repo_root / "graphics" / "players" / team / "headshots"
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / file.name
        if not dest.exists() or dest.stat().st_size == 0:
            shutil.copy2(file, dest)


def reconcile(
    target_db: Path,
    source_db: Path,
    *,
    active_save: bool,
    copy_graphics_root: Path | None,
    source_graphics: Path,
) -> list[int]:
    source = sqlite3.connect(source_db)
    target = sqlite3.connect(target_db)
    source.row_factory = sqlite3.Row
    target.row_factory = sqlite3.Row
    try:
        rows = missing_rookies(source, target)
        player_ids = [int(row["player_id"]) for row in rows]
        if not player_ids:
            return []
        target.execute("pragma foreign_keys=off")
        with target:
            for table in reversed(RELATED_TABLES):
                delete_rows(target, table, player_ids)
            for row in rows:
                overrides: dict[str, object] = {}
                if active_save:
                    overrides = {
                        "age": int(row["age"] or 0) + 1,
                        "years_exp": max(1, int(row["years_exp"] or 0)),
                        "is_rookie": 0,
                        "status": "Active",
                    }
                insert_row(target, "players", row, overrides=overrides)

            number_assignments = assign_numbers(target, player_ids)
            for player_id, number in number_assignments.items():
                target.execute(
                    "update players set jersey_number=? where player_id=?",
                    (number, player_id),
                )

            for table in RELATED_TABLES:
                if table in CONTRACT_TABLES or not table_exists(source, table) or not table_exists(target, table):
                    continue
                for row in rows_for_players(source, table, player_ids):
                    insert_row(
                        target,
                        table,
                        row,
                        omit=AUTOINCREMENT_KEYS.get(table, set()),
                    )
            copy_contract_rows(source, target, player_ids)
            if active_save:
                duplicate_season_rows(target, player_ids, 2026, 2027)
        if copy_graphics_root:
            copy_graphics(copy_graphics_root, set(player_ids), source_graphics)
        return player_ids
    finally:
        source.close()
        target.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore missing backup rookie rows into a current DB.")
    parser.add_argument("--target-db", required=True, type=Path)
    parser.add_argument("--source-db", default=DEFAULT_SOURCE_DB, type=Path)
    parser.add_argument("--active-save", action="store_true")
    parser.add_argument("--copy-graphics-root", type=Path)
    parser.add_argument("--source-graphics", default=DEFAULT_SOURCE_GRAPHICS, type=Path)
    args = parser.parse_args()
    player_ids = reconcile(
        args.target_db,
        args.source_db,
        active_save=args.active_save,
        copy_graphics_root=args.copy_graphics_root,
        source_graphics=args.source_graphics,
    )
    print(f"Reconciled {len(player_ids)} missing backup rookies into {args.target_db}")
    if player_ids:
        print("Player IDs:", ",".join(str(pid) for pid in player_ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
