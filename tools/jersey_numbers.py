#!/usr/bin/env python3
"""NFL-style jersey number assignment helpers.

The rules here follow the modern NFL number ranges, then layer football-common
preferences on top so auto-assigned numbers look natural instead of merely legal.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"

UNCONTROLLED_STATUSES = {"Free Agent", "Retired", "Waived"}

NUMBER_RANGES: dict[str, tuple[tuple[int, int], ...]] = {
    "QB": ((0, 19),),
    "RB": ((0, 49), (80, 89)),
    "FB": ((0, 49), (80, 89)),
    "WR": ((0, 49), (80, 89)),
    "TE": ((0, 49), (80, 89)),
    "C": ((50, 79),),
    "OG": ((50, 79),),
    "IOL": ((50, 79),),
    "OT": ((50, 79),),
    "LT": ((50, 79),),
    "RT": ((50, 79),),
    "LG": ((50, 79),),
    "RG": ((50, 79),),
    "IDL": ((50, 79), (90, 99)),
    "DT": ((50, 79), (90, 99)),
    "NT": ((50, 79), (90, 99)),
    "DE": ((50, 79), (90, 99)),
    "EDGE": ((0, 59), (90, 99)),
    "ILB": ((0, 59), (90, 99)),
    "OLB": ((0, 59), (90, 99)),
    "MLB": ((0, 59), (90, 99)),
    "LB": ((0, 59), (90, 99)),
    "CB": ((0, 49),),
    "NB": ((0, 49),),
    "FS": ((0, 49),),
    "SS": ((0, 49),),
    "S": ((0, 49),),
    "K": ((0, 49), (90, 99)),
    "P": ((0, 49), (90, 99)),
    # NFL rule tables do not usually break out long snappers, so use the
    # common football ranges first and keep 90s as a practical fallback.
    "LS": ((40, 49), (50, 79), (90, 99)),
}


def ordered_unique(values: Iterable[int]) -> list[int]:
    seen: set[int] = set()
    output: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def span(start: int, end: int) -> list[int]:
    return list(range(start, end + 1))


PREFERRED_NUMBERS: dict[str, list[int]] = {
    "QB": ordered_unique([*span(1, 19), 0]),
    "RB": ordered_unique([*span(20, 39), *span(0, 9), *span(40, 49), *span(80, 89), *span(10, 19)]),
    "FB": ordered_unique([*span(40, 49), *span(20, 39), *span(80, 89), *span(0, 19)]),
    "WR": ordered_unique([*span(0, 19), *span(80, 89), *span(20, 49)]),
    "TE": ordered_unique([*span(80, 89), *span(40, 49), *span(0, 19), *span(20, 39)]),
    "OL": ordered_unique([*span(60, 79), *span(50, 59)]),
    "IDL": ordered_unique([*span(90, 99), *span(50, 79)]),
    "EDGE": ordered_unique([*span(90, 99), *span(40, 59), *span(0, 19), *span(20, 39)]),
    "LB": ordered_unique([*span(40, 59), *span(0, 19), *span(90, 99), *span(20, 39)]),
    "DB": ordered_unique([*span(20, 39), *span(0, 19), *span(40, 49)]),
    "ST": ordered_unique([*span(1, 19), 0, *span(40, 49), *span(90, 99), *span(20, 39)]),
    "LS": ordered_unique([*span(40, 49), *span(50, 59), *span(60, 79), *span(90, 99)]),
}

POSITION_TO_STYLE = {
    "QB": "QB",
    "RB": "RB",
    "FB": "FB",
    "WR": "WR",
    "SWR": "WR",
    "TE": "TE",
    "C": "OL",
    "OG": "OL",
    "IOL": "OL",
    "OT": "OL",
    "LT": "OL",
    "RT": "OL",
    "LG": "OL",
    "RG": "OL",
    "IDL": "IDL",
    "DT": "IDL",
    "NT": "IDL",
    "DE": "IDL",
    "EDGE": "EDGE",
    "ILB": "LB",
    "OLB": "LB",
    "MLB": "LB",
    "LB": "LB",
    "CB": "DB",
    "NB": "DB",
    "FS": "DB",
    "SS": "DB",
    "S": "DB",
    "K": "ST",
    "P": "ST",
    "LS": "LS",
}


def unit_for_position(position: str | None) -> str:
    normalized = str(position or "").upper()
    if POSITION_TO_STYLE.get(normalized) in {"QB", "RB", "FB", "WR", "TE", "OL"}:
        return "offense"
    if POSITION_TO_STYLE.get(normalized) in {"IDL", "EDGE", "LB", "DB"}:
        return "defense"
    if POSITION_TO_STYLE.get(normalized) in {"ST", "LS"}:
        return "special_teams"
    return "other"


def table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def current_game_date(con: sqlite3.Connection) -> str | None:
    if not table_exists(con, "game_settings"):
        return None
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
    ).fetchone()
    return str(row["setting_value"] or "") if row and row["setting_value"] else None


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS player_jersey_number_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            team_id INTEGER,
            jersey_number INTEGER NOT NULL,
            assigned_date TEXT,
            source TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_player_jersey_number_history_player
            ON player_jersey_number_history(player_id, created_at DESC);
        """
    )
    con.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_player_jersey_number_history_unique
        ON player_jersey_number_history(
            player_id,
            COALESCE(team_id, -1),
            jersey_number,
            COALESCE(assigned_date, '')
        )
        """
    )


def legal_numbers(position: str | None) -> list[int]:
    normalized = str(position or "").upper()
    ranges = NUMBER_RANGES.get(normalized, ((0, 99),))
    numbers: list[int] = []
    for start, end in ranges:
        numbers.extend(range(start, end + 1))
    return ordered_unique(numbers)


def is_legal_number(position: str | None, number: object) -> bool:
    try:
        value = int(number)
    except (TypeError, ValueError):
        return False
    return value in legal_numbers(position)


def preferred_numbers(position: str | None) -> list[int]:
    normalized = str(position or "").upper()
    style = POSITION_TO_STYLE.get(normalized, normalized)
    legal = set(legal_numbers(normalized))
    preferred = [number for number in PREFERRED_NUMBERS.get(style, []) if number in legal]
    return ordered_unique([*preferred, *sorted(legal)])


def held_number_status_sql() -> str:
    placeholders = ", ".join("?" for _ in UNCONTROLLED_STATUSES)
    return f"COALESCE(status, 'Active') NOT IN ({placeholders})"


def used_numbers(
    con: sqlite3.Connection,
    team_id: int,
    *,
    exclude_player_id: int | None = None,
) -> set[int]:
    params: list[object] = [team_id, *sorted(UNCONTROLLED_STATUSES)]
    exclude_sql = ""
    if exclude_player_id is not None:
        exclude_sql = " AND player_id != ?"
        params.append(exclude_player_id)
    rows = con.execute(
        f"""
        SELECT jersey_number
        FROM players
        WHERE team_id = ?
          AND jersey_number IS NOT NULL
          AND {held_number_status_sql()}
          {exclude_sql}
        """,
        params,
    ).fetchall()
    return {int(row["jersey_number"]) for row in rows if row["jersey_number"] is not None}


def number_counts(
    con: sqlite3.Connection,
    team_id: int,
    *,
    exclude_player_id: int | None = None,
) -> dict[int, int]:
    params: list[object] = [team_id, *sorted(UNCONTROLLED_STATUSES)]
    exclude_sql = ""
    if exclude_player_id is not None:
        exclude_sql = " AND player_id != ?"
        params.append(exclude_player_id)
    rows = con.execute(
        f"""
        SELECT jersey_number, COUNT(*) AS count
        FROM players
        WHERE team_id = ?
          AND jersey_number IS NOT NULL
          AND {held_number_status_sql()}
          {exclude_sql}
        GROUP BY jersey_number
        """,
        params,
    ).fetchall()
    return {
        int(row["jersey_number"]): int(row["count"] or 0)
        for row in rows
        if row["jersey_number"] is not None
    }


def number_unit_counts(
    con: sqlite3.Connection,
    team_id: int,
    *,
    exclude_player_id: int | None = None,
) -> dict[int, dict[str, int]]:
    params: list[object] = [team_id, *sorted(UNCONTROLLED_STATUSES)]
    exclude_sql = ""
    if exclude_player_id is not None:
        exclude_sql = " AND player_id != ?"
        params.append(exclude_player_id)
    rows = con.execute(
        f"""
        SELECT player_id, position, jersey_number
        FROM players
        WHERE team_id = ?
          AND jersey_number IS NOT NULL
          AND {held_number_status_sql()}
          {exclude_sql}
        """,
        params,
    ).fetchall()
    output: dict[int, dict[str, int]] = {}
    for row in rows:
        number = int(row["jersey_number"])
        unit = unit_for_position(row["position"])
        output.setdefault(number, {})[unit] = output.setdefault(number, {}).get(unit, 0) + 1
    return output


def record_number(
    con: sqlite3.Connection,
    *,
    player_id: int,
    team_id: int | None,
    number: int,
    source: str,
) -> None:
    ensure_schema(con)
    con.execute(
        """
        INSERT OR IGNORE INTO player_jersey_number_history (
            player_id, team_id, jersey_number, assigned_date, source
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (player_id, team_id, number, current_game_date(con), source),
    )


def historical_number_candidates(
    con: sqlite3.Connection,
    *,
    player_id: int,
    position: str | None,
    current_number: object,
) -> list[int]:
    candidates: list[int] = []
    try:
        current = int(current_number)
        candidates.append(current)
    except (TypeError, ValueError):
        pass
    if table_exists(con, "player_jersey_number_history"):
        rows = con.execute(
            """
            SELECT jersey_number
            FROM player_jersey_number_history
            WHERE player_id = ?
            ORDER BY history_id DESC
            """,
            (player_id,),
        ).fetchall()
        candidates.extend(int(row["jersey_number"]) for row in rows)
    return [number for number in ordered_unique(candidates) if is_legal_number(position, number)]


def choose_number(
    con: sqlite3.Connection,
    *,
    player_id: int,
    team_id: int,
    position: str | None,
    current_number: object = None,
    used: set[int] | None = None,
    counts: dict[int, int] | None = None,
    unit_counts: dict[int, dict[str, int]] | None = None,
) -> int | None:
    blocked = set(used if used is not None else used_numbers(con, team_id, exclude_player_id=player_id))
    for number in historical_number_candidates(
        con,
        player_id=player_id,
        position=position,
        current_number=current_number,
    ):
        if number not in blocked:
            return number
    for number in preferred_numbers(position):
        if number not in blocked:
            return number
    usage = counts if counts is not None else number_counts(con, team_id, exclude_player_id=player_id)
    unit_usage = (
        unit_counts
        if unit_counts is not None
        else number_unit_counts(con, team_id, exclude_player_id=player_id)
    )
    legal_preferences = preferred_numbers(position)
    if legal_preferences:
        # Preseason 90-man rosters can exhaust a legal position pool. In that
        # case, duplicate the least-used legal number instead of giving the
        # player an illegal number or leaving them blank. Prefer duplicate
        # numbers that are not already used on the same side of the ball.
        unit = unit_for_position(position)
        return min(
            legal_preferences,
            key=lambda item: (
                int(unit_usage.get(item, {}).get(unit, 0)),
                int(usage.get(item, 0)),
                item,
            ),
        )
    return None


def assignment_priority(row: sqlite3.Row) -> tuple[int, int, int, int, int]:
    status = str(row["status"] or "Active")
    protected_status = 0 if status in {"Active", "Questionable", "Doubtful", "Out", "IR", "PUP", "Suspended"} else 1
    return (
        protected_status,
        int(row["is_rookie"] or 0),
        -int(row["years_exp"] or 0),
        -int(row["overall"] or 0),
        int(row["player_id"]),
    )


def assign_player_number(
    con: sqlite3.Connection,
    player_id: int,
    *,
    team_id: int | None = None,
    source: str = "auto",
) -> int | None:
    ensure_schema(con)
    player = con.execute(
        """
        SELECT player_id, position, team_id, jersey_number, status
        FROM players
        WHERE player_id = ?
        """,
        (player_id,),
    ).fetchone()
    if not player:
        return None
    resolved_team_id = team_id if team_id is not None else player["team_id"]
    if resolved_team_id is None:
        return None
    if str(player["status"] or "Active") in UNCONTROLLED_STATUSES:
        return None

    used = used_numbers(con, int(resolved_team_id), exclude_player_id=int(player_id))
    current = player["jersey_number"]
    if is_legal_number(player["position"], current) and int(current) not in used:
        record_number(
            con,
            player_id=int(player_id),
            team_id=int(resolved_team_id),
            number=int(current),
            source=f"{source}:kept",
        )
        return int(current)

    number = choose_number(
        con,
        player_id=int(player_id),
        team_id=int(resolved_team_id),
        position=str(player["position"] or ""),
        current_number=current,
        used=used,
        counts=number_counts(con, int(resolved_team_id), exclude_player_id=int(player_id)),
        unit_counts=number_unit_counts(con, int(resolved_team_id), exclude_player_id=int(player_id)),
    )
    if number is None:
        con.execute("UPDATE players SET jersey_number = NULL WHERE player_id = ?", (player_id,))
        return None
    con.execute("UPDATE players SET jersey_number = ? WHERE player_id = ?", (number, player_id))
    record_number(
        con,
        player_id=int(player_id),
        team_id=int(resolved_team_id),
        number=number,
        source=source,
    )
    return number


def assign_missing_numbers(
    con: sqlite3.Connection,
    *,
    team_ids: Iterable[int] | None = None,
    source: str = "auto",
) -> dict[str, int]:
    ensure_schema(con)
    if team_ids is None:
        teams = [
            int(row["team_id"])
            for row in con.execute(
                """
                SELECT DISTINCT team_id
                FROM players
                WHERE team_id IS NOT NULL
                ORDER BY team_id
                """
            ).fetchall()
        ]
    else:
        teams = sorted({int(team_id) for team_id in team_ids})

    changed = 0
    unresolved = 0
    checked = 0
    for team_id in teams:
        rows = con.execute(
            f"""
            SELECT player_id, position, team_id, jersey_number, status,
                   is_rookie, years_exp, overall
            FROM players
            WHERE team_id = ?
              AND {held_number_status_sql()}
            ORDER BY player_id
            """,
            (team_id, *sorted(UNCONTROLLED_STATUSES)),
        ).fetchall()
        used: set[int] = set()
        counts: dict[int, int] = {}
        unit_counts: dict[int, dict[str, int]] = {}
        for row in sorted(rows, key=assignment_priority):
            checked += 1
            current = row["jersey_number"]
            if is_legal_number(row["position"], current) and int(current) not in used:
                number = int(current)
            else:
                number = choose_number(
                    con,
                    player_id=int(row["player_id"]),
                    team_id=team_id,
                    position=str(row["position"] or ""),
                    current_number=current,
                    used=used,
                    counts=counts,
                    unit_counts=unit_counts,
                )
            if number is None:
                unresolved += 1
                if current is not None:
                    con.execute("UPDATE players SET jersey_number = NULL WHERE player_id = ?", (row["player_id"],))
                    changed += 1
                continue
            used.add(number)
            counts[number] = counts.get(number, 0) + 1
            unit = unit_for_position(row["position"])
            unit_counts.setdefault(number, {})[unit] = unit_counts.setdefault(number, {}).get(unit, 0) + 1
            if current != number:
                con.execute(
                    "UPDATE players SET jersey_number = ? WHERE player_id = ?",
                    (number, row["player_id"]),
                )
                changed += 1
            record_number(
                con,
                player_id=int(row["player_id"]),
                team_id=team_id,
                number=number,
                source=source if current != number else f"{source}:kept",
            )
    return {"teams": len(teams), "checked": checked, "changed": changed, "unresolved": unresolved}


def audit_numbers(con: sqlite3.Connection) -> dict[str, int]:
    missing = con.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM players
        WHERE team_id IS NOT NULL
          AND jersey_number IS NULL
          AND {held_number_status_sql()}
        """,
        tuple(sorted(UNCONTROLLED_STATUSES)),
    ).fetchone()["count"]
    duplicates = con.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM (
            SELECT team_id, jersey_number
            FROM players
            WHERE team_id IS NOT NULL
              AND jersey_number IS NOT NULL
              AND {held_number_status_sql()}
            GROUP BY team_id, jersey_number
            HAVING COUNT(*) > 1
        )
        """,
        tuple(sorted(UNCONTROLLED_STATUSES)),
    ).fetchone()["count"]
    illegal = 0
    rows = con.execute(
        f"""
        SELECT position, jersey_number
        FROM players
        WHERE team_id IS NOT NULL
          AND jersey_number IS NOT NULL
          AND {held_number_status_sql()}
        """,
        tuple(sorted(UNCONTROLLED_STATUSES)),
    ).fetchall()
    for row in rows:
        if not is_legal_number(row["position"], row["jersey_number"]):
            illegal += 1
    return {"missing": int(missing or 0), "duplicates": int(duplicates or 0), "illegal": illegal}


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def main() -> int:
    parser = argparse.ArgumentParser(description="Assign and audit NFL-style jersey numbers.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    assign_parser = subparsers.add_parser("assign-missing", help="Assign missing, duplicate, or illegal numbers.")
    assign_parser.add_argument("--team-id", action="append", type=int, default=[])
    assign_parser.add_argument("--source", default="manual_cleanup")

    one_parser = subparsers.add_parser("assign-player", help="Assign one player a legal number.")
    one_parser.add_argument("--player-id", type=int, required=True)
    one_parser.add_argument("--team-id", type=int)
    one_parser.add_argument("--source", default="manual_player")

    subparsers.add_parser("audit", help="Report missing, duplicate, and illegal numbers.")

    args = parser.parse_args()
    with connect(args.db) as con:
        if args.command == "assign-missing":
            result = assign_missing_numbers(
                con,
                team_ids=args.team_id or None,
                source=args.source,
            )
            con.commit()
            print(
                "Jersey numbers assigned: "
                f"{result['changed']} changed, {result['unresolved']} unresolved, "
                f"{result['checked']} checked across {result['teams']} team(s)."
            )
        elif args.command == "assign-player":
            number = assign_player_number(
                con,
                args.player_id,
                team_id=args.team_id,
                source=args.source,
            )
            con.commit()
            print(f"Player {args.player_id} assigned #{number if number is not None else '--'}.")
        elif args.command == "audit":
            result = audit_numbers(con)
            print(
                "Jersey number audit: "
                f"{result['missing']} missing, {result['duplicates']} duplicate team-number pairs, "
                f"{result['illegal']} illegal by position."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
