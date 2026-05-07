"""Repair generated rookie player names that collide with established players.

The base roster can contain legitimate duplicate-looking veteran rows while
contracts and free agency are being normalized. This repair is deliberately
narrow: when an is_rookie player shares an exact first/last name with an
established non-rookie player, rename the rookie to a unique generated name.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.draft.names import NameGenerator, normalize_name_key  # noqa: E402

DEFAULT_DB = ROOT / "database" / "nfl_gm.db"


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def player_name_key(row: sqlite3.Row) -> str:
    return normalize_name_key(f"{row['first_name']} {row['last_name']}")


def existing_player_name_keys(con: sqlite3.Connection) -> set[str]:
    return {
        normalize_name_key(f"{row['first_name']} {row['last_name']}")
        for row in con.execute("SELECT first_name, last_name FROM players")
    }


def collision_rookies(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        """
        WITH established_names AS (
            SELECT lower(first_name) AS first_key, lower(last_name) AS last_key
            FROM players
            WHERE COALESCE(is_rookie, 0) = 0
            GROUP BY lower(first_name), lower(last_name)
        )
        SELECT p.player_id, p.first_name, p.last_name, p.position, p.college, p.team_id
        FROM players p
        JOIN established_names n
          ON n.first_key = lower(p.first_name)
         AND n.last_key = lower(p.last_name)
        WHERE COALESCE(p.is_rookie, 0) = 1
        ORDER BY p.player_id
        """
    ).fetchall()


def unique_generated_name(generator: NameGenerator, used: set[str], *, seed_hint: str) -> tuple[str, str]:
    for attempt in range(100):
        name = generator.generate(
            football_bias=0.2,
            cultural_bias=0.68,
            avoid_real_player_names=True,
        )
        key = normalize_name_key(name.full_name)
        if key not in used:
            used.add(key)
            return name.first_name, name.last_name

    # Extremely defensive fallback. It keeps a plausible display while forcing
    # the normalized full-name key away from real-player/name-pool collisions.
    fallback_first = "Jalen"
    fallback_last = f"Cross{abs(hash(seed_hint)) % 10000}"
    key = normalize_name_key(f"{fallback_first} {fallback_last}")
    suffix = 1
    while key in used:
        suffix += 1
        fallback_last = f"Cross{abs(hash(seed_hint)) % 10000}{suffix}"
        key = normalize_name_key(f"{fallback_first} {fallback_last}")
    used.add(key)
    return fallback_first, fallback_last


def update_transaction_text(
    con: sqlite3.Connection,
    *,
    player_id: int,
    old_name: str,
    new_name: str,
) -> None:
    if not table_exists(con, "transaction_log"):
        return
    con.execute(
        """
        UPDATE transaction_log
        SET description = replace(description, ?, ?)
        WHERE player_id = ?
          AND description LIKE ?
        """,
        (old_name, new_name, player_id, f"%{old_name}%"),
    )


def repair(con: sqlite3.Connection, *, apply: bool) -> list[dict[str, object]]:
    con.row_factory = sqlite3.Row
    generator = NameGenerator(seed="repair_duplicate_player_names")
    used = existing_player_name_keys(con)
    changes: list[dict[str, object]] = []

    for row in collision_rookies(con):
        old_name = f"{row['first_name']} {row['last_name']}"
        first_name, last_name = unique_generated_name(
            generator,
            used,
            seed_hint=f"{row['player_id']}:{old_name}:{row['position']}:{row['college']}",
        )
        new_name = f"{first_name} {last_name}"
        change = {
            "player_id": int(row["player_id"]),
            "old_name": old_name,
            "new_name": new_name,
            "position": row["position"],
            "college": row["college"],
        }
        changes.append(change)
        if apply:
            con.execute(
                """
                UPDATE players
                SET first_name = ?,
                    last_name = ?
                WHERE player_id = ?
                """,
                (first_name, last_name, int(row["player_id"])),
            )
            update_transaction_text(
                con,
                player_id=int(row["player_id"]),
                old_name=old_name,
                new_name=new_name,
            )

    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    try:
        changes = repair(con, apply=args.apply)
        if args.apply:
            con.commit()
        else:
            con.rollback()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    mode = "Applied" if args.apply else "Would repair"
    print(f"{mode} {len(changes)} rookie duplicate name collision(s).")
    for item in changes:
        print(
            f"  {item['player_id']}: {item['old_name']} -> {item['new_name']} "
            f"({item['position']}, {item['college']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
