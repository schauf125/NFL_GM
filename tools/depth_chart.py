#!/usr/bin/env python3
"""Depth chart tools for active-save gameplay."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"

OFFENSE_SLOTS = {"QB", "RB", "FB", "LWR", "RWR", "SWR", "TE", "LT", "LG", "C", "RG", "RT"}
DEFENSE_SLOTS = {"LEDGE", "REDGE", "LDL", "RDL", "NT", "WLB", "MLB", "SLB", "LCB", "RCB", "NB", "FS", "SS"}
SPECIAL_SLOTS = {"PK", "P", "KO", "LS", "KR", "PR", "H"}


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def infer_unit(position: str) -> str:
    key = position.upper()
    if key in OFFENSE_SLOTS:
        return "Offense"
    if key in DEFENSE_SLOTS:
        return "Defense"
    if key in SPECIAL_SLOTS:
        return "Special Teams"
    return "Offense"


def get_team(con: sqlite3.Connection, team: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT team_id, abbreviation, city, nickname FROM teams WHERE abbreviation = ?",
        (team.upper(),),
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown team abbreviation: {team}")
    return row


def get_player(con: sqlite3.Connection, player_id: int, team_id: int) -> sqlite3.Row:
    row = con.execute(
        """
        SELECT player_id, first_name, last_name, position, team_id, status
        FROM players
        WHERE player_id = ?
        """,
        (player_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown player_id: {player_id}")
    if int(row["team_id"] or 0) != int(team_id):
        name = f"{row['first_name']} {row['last_name']}".strip()
        raise ValueError(f"{name} is not on this team.")
    if row["status"] == "Retired":
        raise ValueError("Retired players cannot be placed on the depth chart.")
    return row


def depth_row(
    con: sqlite3.Connection,
    *,
    team_id: int,
    position: str,
    rank: int | None = None,
    player_id: int | None = None,
) -> sqlite3.Row | None:
    clauses = ["team_id = ?", "position = ?"]
    params: list[object] = [team_id, position.upper()]
    if rank is not None:
        clauses.append("depth_rank = ?")
        params.append(rank)
    if player_id is not None:
        clauses.append("player_id = ?")
        params.append(player_id)
    sql = f"SELECT * FROM depth_charts WHERE {' AND '.join(clauses)} LIMIT 1"
    return con.execute(sql, params).fetchone()


def swap_ranks(con: sqlite3.Connection, first_id: int, second_id: int, first_rank: int, second_rank: int) -> None:
    temp_rank = -100000 - first_id
    con.execute("UPDATE depth_charts SET depth_rank = ? WHERE depth_chart_id = ?", (temp_rank, first_id))
    con.execute("UPDATE depth_charts SET depth_rank = ? WHERE depth_chart_id = ?", (first_rank, second_id))
    con.execute("UPDATE depth_charts SET depth_rank = ? WHERE depth_chart_id = ?", (second_rank, first_id))


def set_slot(
    con: sqlite3.Connection,
    *,
    team: str,
    position: str,
    rank: int,
    player_id: int,
    unit: str | None,
    apply: bool,
) -> None:
    if rank < 1:
        raise ValueError("Depth rank must be 1 or higher.")
    team_row = get_team(con, team)
    team_id = int(team_row["team_id"])
    player = get_player(con, player_id, team_id)
    slot = position.upper()
    unit_value = unit or infer_unit(slot)
    target = depth_row(con, team_id=team_id, position=slot, rank=rank)
    existing = depth_row(con, team_id=team_id, position=slot, player_id=player_id)

    if existing and int(existing["depth_rank"]) == rank:
        name = f"{player['first_name']} {player['last_name']}".strip()
        print(f"No change: {name} is already {team_row['abbreviation']} {slot} #{rank}.")
        return

    if not apply:
        name = f"{player['first_name']} {player['last_name']}".strip()
        old = f" replacing player_id {target['player_id']}" if target else ""
        print(f"DRY RUN: set {team_row['abbreviation']} {slot} #{rank} to {name}{old}.")
        return

    if existing and target:
        swap_ranks(
            con,
            int(existing["depth_chart_id"]),
            int(target["depth_chart_id"]),
            int(existing["depth_rank"]),
            int(target["depth_rank"]),
        )
    elif existing:
        con.execute(
            "UPDATE depth_charts SET depth_rank = ?, unit = ? WHERE depth_chart_id = ?",
            (rank, unit_value, int(existing["depth_chart_id"])),
        )
    elif target:
        con.execute(
            "UPDATE depth_charts SET player_id = ?, unit = ? WHERE depth_chart_id = ?",
            (player_id, unit_value, int(target["depth_chart_id"])),
        )
    else:
        con.execute(
            """
            INSERT INTO depth_charts (team_id, player_id, position, depth_rank, unit)
            VALUES (?, ?, ?, ?, ?)
            """,
            (team_id, player_id, slot, rank, unit_value),
        )
    con.commit()
    name = f"{player['first_name']} {player['last_name']}".strip()
    print(f"Set {team_row['abbreviation']} {slot} #{rank} to {name}.")


def move_player(
    con: sqlite3.Connection,
    *,
    team: str,
    position: str,
    player_id: int,
    direction: str,
    apply: bool,
) -> None:
    team_row = get_team(con, team)
    team_id = int(team_row["team_id"])
    player = get_player(con, player_id, team_id)
    slot = position.upper()
    current = depth_row(con, team_id=team_id, position=slot, player_id=player_id)
    if not current:
        raise ValueError(f"player_id {player_id} is not listed in {team_row['abbreviation']} {slot}.")
    current_rank = int(current["depth_rank"])
    delta = -1 if direction == "up" else 1
    target_rank = current_rank + delta
    if target_rank < 1:
        raise ValueError("Player is already first at that slot.")
    target = depth_row(con, team_id=team_id, position=slot, rank=target_rank)
    name = f"{player['first_name']} {player['last_name']}".strip()
    if not apply:
        print(f"DRY RUN: move {name} from {slot} #{current_rank} to #{target_rank}.")
        return
    if target:
        swap_ranks(
            con,
            int(current["depth_chart_id"]),
            int(target["depth_chart_id"]),
            current_rank,
            target_rank,
        )
    else:
        con.execute(
            "UPDATE depth_charts SET depth_rank = ? WHERE depth_chart_id = ?",
            (target_rank, int(current["depth_chart_id"])),
        )
    con.commit()
    print(f"Moved {name} to {team_row['abbreviation']} {slot} #{target_rank}.")


def show_team(con: sqlite3.Connection, team: str) -> None:
    team_row = get_team(con, team)
    rows = con.execute(
        """
        SELECT
            dc.unit,
            dc.position,
            dc.depth_rank,
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position AS listed_position,
            p.age
        FROM depth_charts dc
        JOIN players p ON p.player_id = dc.player_id
        WHERE dc.team_id = ?
        ORDER BY dc.unit, dc.position, dc.depth_rank
        """,
        (int(team_row["team_id"]),),
    ).fetchall()
    print(f"{team_row['abbreviation']} depth chart")
    for row in rows:
        print(
            f"{row['unit']:<14} {row['position']:<6} #{row['depth_rank']:<2} "
            f"{row['player_name']} ({row['listed_position']}, age {row['age']}) "
            f"player_id={row['player_id']}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Edit a team's depth chart.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    show_parser = subparsers.add_parser("show", help="Show a team depth chart.")
    show_parser.add_argument("--team", required=True)

    set_parser = subparsers.add_parser("set", help="Set one depth chart slot/rank.")
    set_parser.add_argument("--team", required=True)
    set_parser.add_argument("--position", required=True)
    set_parser.add_argument("--rank", type=int, required=True)
    set_parser.add_argument("--player-id", type=int, required=True)
    set_parser.add_argument("--unit")
    set_parser.add_argument("--apply", action="store_true")

    move_parser = subparsers.add_parser("move", help="Move a player up/down within one slot.")
    move_parser.add_argument("--team", required=True)
    move_parser.add_argument("--position", required=True)
    move_parser.add_argument("--player-id", type=int, required=True)
    move_parser.add_argument("--direction", choices=["up", "down"], required=True)
    move_parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    con = connect(args.db)
    try:
        if args.command == "show":
            show_team(con, args.team)
        elif args.command == "set":
            set_slot(
                con,
                team=args.team,
                position=args.position,
                rank=args.rank,
                player_id=args.player_id,
                unit=args.unit,
                apply=args.apply,
            )
        elif args.command == "move":
            move_player(
                con,
                team=args.team,
                position=args.position,
                player_id=args.player_id,
                direction=args.direction,
                apply=args.apply,
            )
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
