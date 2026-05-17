"""Reconcile duplicate real-player rows that share the same external identity.

The roster imports can occasionally leave the same NFL player on two teams.
This script is conservative: it only touches duplicate rows that share a GSIS
or PFR id in player_external_ids. Same-name players with different external
ids are left alone.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "database" / "nfl_gm.db"
RECONCILED_STATUS = "Retired"
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


@dataclass(frozen=True)
class PlayerRow:
    player_id: int
    name: str
    position: str
    team: str
    team_id: int | None
    latest_team: str
    age: int
    overall: int
    potential: int
    status: str
    active_contracts: int
    season_stats: int
    game_stats: int


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def duplicate_identity_groups(conn: sqlite3.Connection) -> list[list[PlayerRow]]:
    rows = conn.execute(
        """
        WITH identities AS (
            SELECT
                player_id,
                CASE
                    WHEN COALESCE(gsis_id, '') != '' THEN 'gsis:' || gsis_id
                    WHEN COALESCE(pfr_id, '') != '' THEN 'pfr:' || pfr_id
                    ELSE NULL
                END AS identity
            FROM player_external_ids
        )
        SELECT identity
        FROM identities
        WHERE identity IS NOT NULL
        GROUP BY identity
        HAVING COUNT(DISTINCT player_id) > 1
        ORDER BY identity
        """
    ).fetchall()

    groups: list[list[PlayerRow]] = []
    for row in rows:
        members = conn.execute(
            """
            SELECT
                p.player_id,
                p.first_name || ' ' || p.last_name AS name,
                p.position,
                COALESCE(t.abbreviation, 'FA') AS team,
                p.team_id,
                COALESCE(x.latest_team, '') AS latest_team,
                COALESCE(p.age, 0) AS age,
                COALESCE(p.overall, 0) AS overall,
                COALESCE(p.potential, 0) AS potential,
                COALESCE(p.status, 'Active') AS status,
                (
                    SELECT COUNT(*)
                    FROM contracts c
                    WHERE c.player_id = p.player_id AND COALESCE(c.is_active, 1) = 1
                ) AS active_contracts,
                (
                    SELECT COUNT(*)
                    FROM player_season_stats s
                    WHERE s.player_id = p.player_id
                ) AS season_stats,
                (
                    SELECT COUNT(*)
                    FROM game_player_stats gps
                    WHERE gps.player_id = p.player_id
                ) AS game_stats
            FROM player_external_ids x
            JOIN players p ON p.player_id = x.player_id
            LEFT JOIN teams t ON t.team_id = p.team_id
            WHERE (
                CASE
                    WHEN COALESCE(x.gsis_id, '') != '' THEN 'gsis:' || x.gsis_id
                    WHEN COALESCE(x.pfr_id, '') != '' THEN 'pfr:' || x.pfr_id
                    ELSE NULL
                END
            ) = ?
              AND COALESCE(p.status, 'Active') != ?
            ORDER BY p.player_id
            """,
            (row["identity"], RECONCILED_STATUS),
        ).fetchall()
        players = [PlayerRow(**dict(member)) for member in members]
        if len(players) > 1 and same_real_name_family(players):
            groups.append(players)
    return groups


def name_tokens(name: str) -> list[str]:
    tokens = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return [token for token in tokens if token not in SUFFIXES]


def same_real_name_family(group: list[PlayerRow]) -> bool:
    token_groups = [name_tokens(player.name) for player in group]
    if not all(tokens for tokens in token_groups):
        return False
    last_names = {tokens[-1] for tokens in token_groups}
    if len(last_names) == 1:
        return True

    # Known source aliases for the same DB identity.
    names = {player.name.lower() for player in group}
    return names in (
        {"c.j. gardner-johnson", "chauncey gardner-johnson"},
        {"jermaine johnson", "jermaine johnson ii"},
    )


def choose_canonical(group: list[PlayerRow]) -> PlayerRow:
    """Pick the row that should stay playable.

    Prefer the row matching the external latest_team. If the in-save league has
    moved the player already, prefer active rostered rows over free agents, then
    higher usage/stat history, then higher overall.
    """

    latest_team = next((row.latest_team for row in group if row.latest_team), "")

    def score(row: PlayerRow) -> tuple[int, int, int, int, int, int]:
        rostered = 1 if row.team_id is not None and row.team != "FA" else 0
        latest_match = 1 if rostered and latest_team and row.team == latest_team else 0
        active = 1 if row.status in {"Active", "Practice Squad"} else 0
        has_contract = 1 if rostered and row.active_contracts else 0
        usage = row.game_stats + row.season_stats
        return (latest_match, rostered, active, has_contract, usage, row.overall)

    return max(group, key=score)


def reconcile_group(conn: sqlite3.Connection, group: list[PlayerRow], *, dry_run: bool) -> tuple[PlayerRow, list[PlayerRow]]:
    canonical = choose_canonical(group)
    duplicates = [row for row in group if row.player_id != canonical.player_id]
    if dry_run:
        return canonical, duplicates

    for duplicate in duplicates:
        conn.execute(
            """
            UPDATE players
               SET status = ?,
                   team_id = NULL,
                   jersey_number = NULL
             WHERE player_id = ?
            """,
            (RECONCILED_STATUS, duplicate.player_id),
        )
        if table_exists(conn, "contracts"):
            conn.execute(
                """
                UPDATE contracts
                   SET is_active = 0
                 WHERE player_id = ?
                """,
                (duplicate.player_id,),
            )
        if table_exists(conn, "depth_charts"):
            conn.execute("DELETE FROM depth_charts WHERE player_id = ?", (duplicate.player_id,))
        if table_exists(conn, "practice_squad_moves"):
            columns = table_columns(conn, "practice_squad_moves")
            if "released_date" in columns:
                conn.execute(
                    """
                    UPDATE practice_squad_moves
                       SET released_date = COALESCE(released_date, date('now')),
                           notes = COALESCE(notes || '; ', '') || 'Closed by duplicate player reconciliation.'
                     WHERE player_id = ? AND released_date IS NULL
                    """,
                    (duplicate.player_id,),
                )
            else:
                conn.execute(
                    """
                    UPDATE practice_squad_moves
                       SET notes = COALESCE(notes || '; ', '') || 'Closed by duplicate player reconciliation.'
                     WHERE player_id = ?
                    """,
                    (duplicate.player_id,),
                )
        if table_exists(conn, "active_player_injuries"):
            conn.execute("DELETE FROM active_player_injuries WHERE player_id = ?", (duplicate.player_id,))
    return canonical, duplicates


def same_name_duplicate_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM (
            SELECT LOWER(first_name || ' ' || last_name), position
            FROM players
            WHERE COALESCE(status, 'Active') != ?
            GROUP BY LOWER(first_name || ' ' || last_name), position
            HAVING COUNT(*) > 1
        )
        """,
        (RECONCILED_STATUS,),
    ).fetchone()
    return int(row["count"] if row else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true", help="Apply changes. Default is dry-run.")
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        groups = duplicate_identity_groups(conn)
        print(f"Duplicate identity groups: {len(groups)}")
        total_duplicates = 0
        for group in groups:
            canonical, duplicates = reconcile_group(conn, group, dry_run=not args.apply)
            total_duplicates += len(duplicates)
            duplicate_text = ", ".join(
                f"#{row.player_id} {row.name} {row.team} OVR {row.overall}" for row in duplicates
            )
            print(
                f"KEEP #{canonical.player_id} {canonical.name} {canonical.team} OVR {canonical.overall}; "
                f"retire {duplicate_text}"
            )
        if args.apply:
            conn.commit()
        print(f"Duplicate rows {'retired' if args.apply else 'that would be retired'}: {total_duplicates}")
        print(f"Remaining same-name/position groups: {same_name_duplicate_count(conn)}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
