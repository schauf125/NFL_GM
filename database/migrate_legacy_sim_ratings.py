"""Backfill missing normalized sim ratings from the old player columns.

This is a bridge script for the move away from legacy player rating columns.
It preserves existing normalized rows and only inserts missing values, so
manual tuning and current QB work stay intact.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime

from seed_vikings_sim_ratings import (
    SEASON,
    calculate_role_score,
    choose_roles,
    generate_ratings,
)
from setup_sim_ratings import (
    DB_PATH,
    create_schema,
    seed_rating_definitions,
    seed_role_definitions,
    seed_role_weights,
)


SOURCE = "legacy_rating_migration"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def backup_database():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DB_PATH.with_name(f"{DB_PATH.stem}.pre_legacy_rating_migration_{timestamp}{DB_PATH.suffix}")
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def ensure_sim_rating_schema(conn: sqlite3.Connection) -> None:
    create_schema(conn)
    seed_rating_definitions(conn)
    seed_role_definitions(conn)
    seed_role_weights(conn)


def clamp(value, low=0, high=100):
    return max(low, min(high, int(round(value))))


def nz(value, default=50):
    return default if value is None else value


def load_players(conn: sqlite3.Connection, *, include_free_agents: bool):
    team_filter = "" if include_free_agents else "AND team_id IS NOT NULL"
    return conn.execute(
        f"""
        SELECT *
        FROM players
        WHERE COALESCE(status, 'Active') NOT IN ('Retired')
          {team_filter}
        ORDER BY COALESCE(team_id, 999), position, last_name, first_name
        """
    ).fetchall()


def existing_rating_keys(conn: sqlite3.Connection, player_id: int) -> set[str]:
    return {
        row["rating_key"]
        for row in conn.execute(
            """
            SELECT rating_key
            FROM player_ratings
            WHERE player_id = ? AND season = ?
            """,
            (player_id, SEASON),
        )
    }


def load_player_ratings(conn: sqlite3.Connection, player_id: int) -> dict[str, int]:
    return {
        row["rating_key"]: int(row["rating_value"])
        for row in conn.execute(
            """
            SELECT rating_key, rating_value
            FROM player_ratings
            WHERE player_id = ? AND season = ?
            """,
            (player_id, SEASON),
        )
    }


def apply_specialist_migration(player, ratings):
    if player["position"] not in {"K", "P", "LS"}:
        return ratings

    overall = nz(player["overall"], 55)
    if player["position"] == "LS":
        default_power = clamp(overall - 4, 45, 75)
        default_accuracy = clamp(overall, 48, 78)
    else:
        default_power = clamp(overall + 8, 58, 90)
        default_accuracy = clamp(overall + 6, 55, 88)

    ratings["kick_power"] = clamp(nz(player["kick_power"], default_power))
    ratings["kick_accuracy"] = clamp(nz(player["kick_acc"], default_accuracy))
    return ratings


def upsert_missing_ratings(conn: sqlite3.Connection, player, ratings: dict[str, int], missing_keys: set[str], source: str = SOURCE) -> int:
    rows = [
        (
            player["player_id"],
            SEASON,
            rating_key,
            ratings[rating_key],
            "medium" if player["position"] in {"K", "P", "LS"} else "low",
            source,
            "Migrated from legacy player rating columns to complete normalized sim ratings.",
        )
        for rating_key in sorted(missing_keys)
    ]
    conn.executemany(
        """
        INSERT INTO player_ratings (
            player_id, season, rating_key, rating_value, confidence, source, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id, season, rating_key) DO NOTHING
        """,
        rows,
    )
    return len(rows)


def has_role_assignment(conn: sqlite3.Connection, player_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM player_role_assignments
        WHERE player_id = ? AND season = ?
        LIMIT 1
        """,
        (player_id, SEASON),
    ).fetchone()
    return row is not None


def insert_missing_role_assignments(conn: sqlite3.Connection, player, roles, source: str = SOURCE) -> int:
    if has_role_assignment(conn, player["player_id"]):
        return 0

    rows = []
    for priority, role_key in ((1, roles[0]), (2, roles[1])):
        if role_key is None:
            continue
        rows.append(
            (
                player["player_id"],
                SEASON,
                role_key,
                priority,
                source,
                "Initial role assignment migrated while completing normalized sim ratings.",
            )
        )

    conn.executemany(
        """
        INSERT INTO player_role_assignments (
            player_id, season, role_key, priority, source, notes
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id, season, role_key) DO NOTHING
        """,
        rows,
    )
    return len(rows)


def upsert_missing_role_scores(conn: sqlite3.Connection, player, ratings: dict[str, int], roles, source: str = SOURCE) -> int:
    rows = []
    for role_key in roles:
        if role_key is None:
            continue
        existing = conn.execute(
            """
            SELECT 1
            FROM player_role_scores
            WHERE player_id = ?
              AND season = ?
              AND role_key = ?
              AND scheme_key = 'default'
            """,
            (player["player_id"], SEASON, role_key),
        ).fetchone()
        if existing:
            continue
        rows.append(
            (
                player["player_id"],
                SEASON,
                role_key,
                "default",
                calculate_role_score(player, ratings, role_key),
                source,
            )
        )

    conn.executemany(
        """
        INSERT INTO player_role_scores (
            player_id, season, role_key, scheme_key, role_score, source
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id, season, role_key, scheme_key) DO NOTHING
        """,
        rows,
    )
    return len(rows)


def ensure_player_normalized_ratings(
    conn: sqlite3.Connection,
    player_id: int,
    *,
    source: str = SOURCE,
    schema_ready: bool = False,
) -> dict[str, int]:
    if not schema_ready:
        ensure_sim_rating_schema(conn)

    rating_keys = {
        row["rating_key"]
        for row in conn.execute("SELECT rating_key FROM rating_definitions")
    }
    player = conn.execute(
        "SELECT * FROM players WHERE player_id = ?",
        (player_id,),
    ).fetchone()
    if player is None:
        raise ValueError(f"Player not found: {player_id}")

    roles = choose_roles(player)
    stored_ratings = load_player_ratings(conn, player_id)
    missing_keys = rating_keys - set(stored_ratings)
    generated_ratings = generate_ratings(player, roles)
    generated_ratings = apply_specialist_migration(player, generated_ratings)
    complete_ratings = {**generated_ratings, **stored_ratings}

    rating_rows = upsert_missing_ratings(conn, player, generated_ratings, missing_keys, source=source)
    role_assignment_rows = insert_missing_role_assignments(conn, player, roles, source=source)
    role_score_rows = upsert_missing_role_scores(conn, player, complete_ratings, roles, source=source)

    return {
        "rating_rows": rating_rows,
        "role_assignment_rows": role_assignment_rows,
        "role_score_rows": role_score_rows,
    }


def migrate(conn: sqlite3.Connection, *, include_free_agents: bool):
    players = load_players(conn, include_free_agents=include_free_agents)
    players_touched = 0
    rating_rows = 0
    role_assignment_rows = 0
    role_score_rows = 0
    fully_complete = 0

    for player in players:
        result = ensure_player_normalized_ratings(conn, player["player_id"], schema_ready=True)
        rating_rows += result["rating_rows"]
        role_assignment_rows += result["role_assignment_rows"]
        role_score_rows += result["role_score_rows"]
        if any(result.values()):
            players_touched += 1
        else:
            fully_complete += 1

    return {
        "players_checked": len(players),
        "players_touched": players_touched,
        "already_complete": fully_complete,
        "rating_rows": rating_rows,
        "role_assignment_rows": role_assignment_rows,
        "role_score_rows": role_score_rows,
    }


def main():
    parser = argparse.ArgumentParser(description="Backfill missing normalized sim rating rows.")
    parser.add_argument("--no-backup", action="store_true", help="Skip timestamped database backup.")
    parser.add_argument(
        "--exclude-free-agents",
        action="store_true",
        help="Only backfill players currently assigned to a team.",
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)

    if not args.no_backup:
        backup_path = backup_database()
        print(f"Backup created: {backup_path}")

    conn = get_connection()
    try:
        with conn:
            ensure_sim_rating_schema(conn)
            result = migrate(conn, include_free_agents=not args.exclude_free_agents)

        print("Legacy sim rating migration complete.")
        for key, value in result.items():
            print(f"  {key}: {value}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
