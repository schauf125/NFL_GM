#!/usr/bin/env python3
"""Depth chart tools for active-save gameplay."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import depth_packages, match_engine  # noqa: E402

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
    key = depth_packages.canonical_slot(position)
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


def player_flex_positions(con: sqlite3.Connection, player_id: int) -> set[str]:
    if not table_exists(con, "player_position_flex"):
        return set()
    rows = con.execute(
        """
        SELECT position
        FROM player_position_flex
        WHERE player_id = ?
          AND COALESCE(experience, 0) > 0
        """,
        (int(player_id),),
    ).fetchall()
    return {str(row["position"] or "").upper() for row in rows if row["position"]}


def validate_player_slot(con: sqlite3.Connection, player: sqlite3.Row, slot: str) -> None:
    canonical = depth_packages.canonical_slot(slot)
    player_position = str(player["position"] or "").upper()
    legal_positions = match_engine.slot_eligible_positions(canonical)
    if player_position in legal_positions:
        return
    if player_flex_positions(con, int(player["player_id"])) & legal_positions:
        return
    name = f"{player['first_name']} {player['last_name']}".strip()
    allowed = ", ".join(sorted(legal_positions))
    raise ValueError(f"{name} ({player_position}) cannot be placed at {canonical}. Allowed positions: {allowed}.")


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


OFFENSE_CORE_PACKAGE_SLOTS = ["QB", "RB", "LT", "LG", "C", "RG", "RT"]


def ranked_package_entries(slots: list[str]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    entries: list[tuple[str, int]] = []
    for slot in slots:
        key = depth_packages.canonical_slot(slot)
        counts[key] = counts.get(key, 0) + 1
        entries.append((key, counts[key]))
    return entries


def package_entries_for_target(slot: str, rank: int) -> list[tuple[str, str, list[tuple[str, int]]]]:
    target_slot = str(slot or "").upper()
    target_rank = int(rank or 1)
    packages: list[tuple[str, str, list[tuple[str, int]]]] = []
    if target_slot in SPECIAL_SLOTS:
        return packages

    if depth_packages.canonical_slot(target_slot) == target_slot:
        for package in depth_packages.OFFENSE_PACKAGE_ORDER:
            entries = ranked_package_entries(
                OFFENSE_CORE_PACKAGE_SLOTS + list(depth_packages.OFFENSE_PACKAGE_SNAP_SLOTS.get(package, []))
            )
            if (target_slot, target_rank) in entries:
                packages.append(("offense", package, entries))
    else:
        for package in depth_packages.DEFENSE_PACKAGE_ORDER:
            entries = [(entry_slot, 1) for entry_slot in depth_packages.DEFENSE_PACKAGE_SNAP_SLOTS.get(package, [])]
            if (target_slot, target_rank) in entries:
                packages.append(("defense", package, entries))
    return packages


def package_label(side: str, package: str) -> str:
    if side == "offense":
        return f"{package} personnel"
    labels = {"nickel": "Nickel", "base34": "3-4", "base43": "4-3"}
    return labels.get(package, package)


def ensure_no_package_duplicate(
    con: sqlite3.Connection,
    *,
    team_id: int,
    player_id: int,
    target_slot: str,
    target_rank: int,
    ignore_depth_chart_ids: set[int] | None = None,
) -> None:
    ignored = ignore_depth_chart_ids or set()
    for side, package, entries in package_entries_for_target(target_slot, target_rank):
        for slot, rank in entries:
            if slot == target_slot and rank == target_rank:
                continue
            row = depth_row(con, team_id=team_id, position=slot, rank=rank)
            if not row or int(row["depth_chart_id"]) in ignored:
                continue
            if int(row["player_id"]) != int(player_id):
                continue
            raise ValueError(
                f"That player is already assigned at {slot} #{rank} in the {package_label(side, package)} package."
            )


def ensure_package_row_for_rank(
    con: sqlite3.Connection,
    *,
    team_id: int,
    slot: str,
    rank: int,
    unit: str,
) -> None:
    target_slot = slot.upper()
    if depth_packages.canonical_slot(target_slot) == target_slot:
        return
    if depth_row(con, team_id=team_id, position=target_slot, rank=rank):
        return
    fallback_slot = depth_packages.canonical_slot(target_slot)
    source = depth_row(con, team_id=team_id, position=fallback_slot, rank=rank)
    if not source:
        return
    if depth_row(con, team_id=team_id, position=target_slot, player_id=int(source["player_id"])):
        return
    con.execute(
        """
        INSERT INTO depth_charts (team_id, player_id, position, depth_rank, unit)
        VALUES (?, ?, ?, ?, ?)
        """,
        (team_id, int(source["player_id"]), target_slot, int(rank), unit),
    )


def seed_missing_package_lower_ranks(
    con: sqlite3.Connection,
    *,
    team_id: int,
    slot: str,
    rank: int,
    unit: str,
    target_player_id: int,
) -> None:
    canonical = depth_packages.canonical_slot(slot)
    if canonical == slot or rank <= 1:
        return
    for lower_rank in range(1, rank):
        if depth_row(con, team_id=team_id, position=slot, rank=lower_rank):
            continue
        source = depth_row(con, team_id=team_id, position=canonical, rank=lower_rank)
        if not source:
            continue
        source_player_id = int(source["player_id"])
        if source_player_id == int(target_player_id):
            continue
        con.execute(
            """
            INSERT INTO depth_charts (team_id, player_id, position, depth_rank, unit)
            VALUES (?, ?, ?, ?, ?)
            """,
            (team_id, source_player_id, slot, lower_rank, unit),
        )


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
    validate_player_slot(con, player, slot)
    unit_value = unit or infer_unit(slot)
    target = depth_row(con, team_id=team_id, position=slot, rank=rank)
    existing = depth_row(con, team_id=team_id, position=slot, player_id=player_id)

    if existing and int(existing["depth_rank"]) == rank:
        name = f"{player['first_name']} {player['last_name']}".strip()
        print(f"No change: {name} is already {team_row['abbreviation']} {slot} #{rank}.")
        return

    ignored_ids = {int(existing["depth_chart_id"])} if existing else set()
    ensure_no_package_duplicate(
        con,
        team_id=team_id,
        player_id=player_id,
        target_slot=slot,
        target_rank=rank,
        ignore_depth_chart_ids=ignored_ids,
    )

    if not apply:
        name = f"{player['first_name']} {player['last_name']}".strip()
        old = f" replacing player_id {target['player_id']}" if target else ""
        print(f"DRY RUN: set {team_row['abbreviation']} {slot} #{rank} to {name}{old}.")
        return

    seed_missing_package_lower_ranks(
        con,
        team_id=team_id,
        slot=slot,
        rank=rank,
        unit=unit_value,
        target_player_id=player_id,
    )
    target = depth_row(con, team_id=team_id, position=slot, rank=rank)

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


def swap_slots(
    con: sqlite3.Connection,
    *,
    team: str,
    first_position: str,
    first_rank: int,
    second_position: str,
    second_rank: int,
    apply: bool,
) -> None:
    if first_rank < 1 or second_rank < 1:
        raise ValueError("Depth ranks must be 1 or higher.")
    team_row = get_team(con, team)
    team_id = int(team_row["team_id"])
    first_slot = first_position.upper()
    second_slot = second_position.upper()
    if apply:
        ensure_package_row_for_rank(
            con,
            team_id=team_id,
            slot=first_slot,
            rank=first_rank,
            unit=infer_unit(first_slot),
        )
        ensure_package_row_for_rank(
            con,
            team_id=team_id,
            slot=second_slot,
            rank=second_rank,
            unit=infer_unit(second_slot),
        )
    first = depth_row(con, team_id=team_id, position=first_slot, rank=first_rank)
    second = depth_row(con, team_id=team_id, position=second_slot, rank=second_rank)
    if not first:
        raise ValueError(f"No player is listed at {team_row['abbreviation']} {first_slot} #{first_rank}.")
    if not second:
        raise ValueError(f"No player is listed at {team_row['abbreviation']} {second_slot} #{second_rank}.")
    if int(first["depth_chart_id"]) == int(second["depth_chart_id"]):
        print("No change: same depth chart slot.")
        return
    first_player = get_player(con, int(first["player_id"]), team_id)
    second_player = get_player(con, int(second["player_id"]), team_id)
    validate_player_slot(con, first_player, second_slot)
    validate_player_slot(con, second_player, first_slot)
    ignored_ids = {int(first["depth_chart_id"]), int(second["depth_chart_id"])}
    ensure_no_package_duplicate(
        con,
        team_id=team_id,
        player_id=int(first["player_id"]),
        target_slot=second_slot,
        target_rank=second_rank,
        ignore_depth_chart_ids=ignored_ids,
    )
    ensure_no_package_duplicate(
        con,
        team_id=team_id,
        player_id=int(second["player_id"]),
        target_slot=first_slot,
        target_rank=first_rank,
        ignore_depth_chart_ids=ignored_ids,
    )
    first_name = f"{first_player['first_name']} {first_player['last_name']}".strip()
    second_name = f"{second_player['first_name']} {second_player['last_name']}".strip()
    if not apply:
        print(
            f"DRY RUN: swap {first_name} ({first_slot} #{first_rank}) "
            f"with {second_name} ({second_slot} #{second_rank})."
        )
        return
    con.execute(
        "UPDATE depth_charts SET player_id = ? WHERE depth_chart_id = ?",
        (int(second["player_id"]), int(first["depth_chart_id"])),
    )
    con.execute(
        "UPDATE depth_charts SET player_id = ? WHERE depth_chart_id = ?",
        (int(first["player_id"]), int(second["depth_chart_id"])),
    )
    con.commit()
    print(
        f"Swapped {first_name} ({first_slot} #{first_rank}) "
        f"with {second_name} ({second_slot} #{second_rank})."
    )


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

    swap_parser = subparsers.add_parser("swap", help="Swap players between two depth chart slots/ranks.")
    swap_parser.add_argument("--team", required=True)
    swap_parser.add_argument("--first-position", required=True)
    swap_parser.add_argument("--first-rank", type=int, required=True)
    swap_parser.add_argument("--second-position", required=True)
    swap_parser.add_argument("--second-rank", type=int, required=True)
    swap_parser.add_argument("--apply", action="store_true")
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
        elif args.command == "swap":
            swap_slots(
                con,
                team=args.team,
                first_position=args.first_position,
                first_rank=args.first_rank,
                second_position=args.second_position,
                second_rank=args.second_rank,
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
