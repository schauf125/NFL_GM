from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path


PLAYER_IDS = tuple(range(2243, 2252))

SOURCE_DB = Path(
    r"Z:\NFL_GM_SIM_MISC_Files\backup_20260505_064723\database\clean_nfl_gm.db"
)
SOURCE_GRAPHICS = Path(r"Z:\NFL_GM_SIM_MISC_Files\graphics backup\players\MIN\headshots")

RELATED_TABLES = [
    "player_ratings",
    "player_role_scores",
    "player_role_assignments",
    "player_rb_behavior_profiles",
    "player_ol_behavior_profiles",
    "player_idl_behavior_profiles",
    "player_lb_behavior_profiles",
    "player_secondary_behavior_profiles",
    "contracts",
    "contract_years",
    "player_graphics_assets",
]

AUTOINCREMENT_KEYS = {
    "player_graphics_assets": {"asset_id"},
    "player_role_assignments": {"assignment_id"},
}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type='table' and name=?", (table,)
    ).fetchone()
    return row is not None


def table_cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"pragma table_info({table})").fetchall()]


def pk_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        row[1]
        for row in conn.execute(f"pragma table_info({table})").fetchall()
        if row[5]
    }


def rows_for_players(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    if not table_exists(conn, table):
        return []
    placeholders = ",".join("?" for _ in PLAYER_IDS)
    return conn.execute(
        f"select * from {table} where player_id in ({placeholders}) order by player_id",
        PLAYER_IDS,
    ).fetchall()


def delete_target_rows(conn: sqlite3.Connection, table: str) -> None:
    if not table_exists(conn, table) or "player_id" not in table_cols(conn, table):
        return
    placeholders = ",".join("?" for _ in PLAYER_IDS)
    conn.execute(f"delete from {table} where player_id in ({placeholders})", PLAYER_IDS)


def insert_row(
    conn: sqlite3.Connection,
    table: str,
    row: sqlite3.Row,
    *,
    overrides: dict[str, object] | None = None,
    omit_keys: set[str] | None = None,
) -> None:
    target_cols = table_cols(conn, table)
    source_cols = set(row.keys())
    omit_keys = omit_keys or set()
    overrides = overrides or {}

    cols: list[str] = []
    values: list[object] = []
    for col in target_cols:
        if col in omit_keys:
            continue
        if col in overrides:
            cols.append(col)
            values.append(overrides[col])
        elif col in source_cols:
            cols.append(col)
            values.append(row[col])
        elif col == "is_international_pathway":
            cols.append(col)
            values.append(0)

    placeholders = ",".join("?" for _ in cols)
    quoted_cols = ",".join(cols)
    conn.execute(
        f"insert into {table} ({quoted_cols}) values ({placeholders})",
        values,
    )


def copy_player_rows(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    active_save: bool,
) -> None:
    source_rows = rows_for_players(source, "players")
    if len(source_rows) != len(PLAYER_IDS):
        raise RuntimeError(
            f"Expected {len(PLAYER_IDS)} Vikings rookie rows in source, found {len(source_rows)}"
        )

    delete_target_rows(target, "players")
    for row in source_rows:
        overrides = {}
        if active_save:
            overrides = {
                "age": int(row["age"] or 0) + 1,
                "is_rookie": 0,
                "status": "Active",
                "team_id": 24,
            }
        insert_row(target, "players", row, overrides=overrides)


def copy_related_rows(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    for table in RELATED_TABLES:
        if not table_exists(source, table) or not table_exists(target, table):
            continue
        delete_target_rows(target, table)
        omit = AUTOINCREMENT_KEYS.get(table, set())
        for row in rows_for_players(source, table):
            insert_row(target, table, row, omit_keys=omit)


def duplicate_season_rows(target: sqlite3.Connection, season_from: int, season_to: int) -> None:
    seasonal_tables = [
        "player_ratings",
        "player_role_scores",
        "player_role_assignments",
        "player_rb_behavior_profiles",
        "player_ol_behavior_profiles",
        "player_idl_behavior_profiles",
        "player_lb_behavior_profiles",
        "player_secondary_behavior_profiles",
    ]
    placeholders = ",".join("?" for _ in PLAYER_IDS)
    for table in seasonal_tables:
        if not table_exists(target, table):
            continue
        cols = table_cols(target, table)
        if "season" not in cols or "player_id" not in cols:
            continue
        target.execute(
            f"delete from {table} where season=? and player_id in ({placeholders})",
            (season_to, *PLAYER_IDS),
        )
        omit = AUTOINCREMENT_KEYS.get(table, set())
        rows = target.execute(
            f"select * from {table} where season=? and player_id in ({placeholders})",
            (season_from, *PLAYER_IDS),
        ).fetchall()
        for row in rows:
            overrides = {"season": season_to}
            if "source" in cols and table in {"player_ratings", "player_role_scores", "player_role_assignments"}:
                overrides["source"] = "restored_vikings_2026_rookies"
            insert_row(target, table, row, overrides=overrides, omit_keys=omit)


def copy_graphics(repo_root: Path) -> None:
    target_dir = repo_root / "graphics" / "players" / "MIN" / "headshots"
    target_dir.mkdir(parents=True, exist_ok=True)
    for file in SOURCE_GRAPHICS.glob("*.png"):
        prefix = file.name.split("_", 1)[0]
        if prefix.isdigit() and int(prefix) in PLAYER_IDS:
            destination = target_dir / file.name
            if not destination.exists() or destination.stat().st_size == 0:
                shutil.copy2(file, destination)


def restore(target_db: Path, source_db: Path, *, active_save: bool, duplicate_to_2027: bool) -> None:
    if not source_db.exists():
        raise FileNotFoundError(source_db)
    if not target_db.exists():
        raise FileNotFoundError(target_db)

    source = sqlite3.connect(source_db)
    target = sqlite3.connect(target_db)
    source.row_factory = sqlite3.Row
    target.row_factory = sqlite3.Row
    try:
        target.execute("pragma foreign_keys = off")
        with target:
            for table in reversed(RELATED_TABLES):
                delete_target_rows(target, table)
            copy_player_rows(source, target, active_save=active_save)
            copy_related_rows(source, target)
            if duplicate_to_2027:
                duplicate_season_rows(target, 2026, 2027)
    finally:
        source.close()
        target.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore the custom Vikings 2026 rookie class from the May 5 backup."
    )
    parser.add_argument("--target-db", required=True, type=Path)
    parser.add_argument("--source-db", default=SOURCE_DB, type=Path)
    parser.add_argument(
        "--active-save",
        action="store_true",
        help="Age players one year, mark them non-rookies, and duplicate 2026 ratings to 2027.",
    )
    parser.add_argument(
        "--copy-graphics-root",
        type=Path,
        help="Repo root where missing graphics/players/MIN/headshots files should be restored.",
    )
    args = parser.parse_args()

    restore(
        args.target_db,
        args.source_db,
        active_save=args.active_save,
        duplicate_to_2027=args.active_save,
    )
    if args.copy_graphics_root:
        copy_graphics(args.copy_graphics_root)

    print(f"Restored Vikings 2026 rookies into {args.target_db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
