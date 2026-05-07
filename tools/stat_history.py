#!/usr/bin/env python3
"""Season stat/history utilities for saved match-engine results."""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import match_engine  # noqa: E402


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def normalize_name(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", value.lower()))


def current_season(con: sqlite3.Connection) -> int:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_season'"
    ).fetchone()
    return int(row["setting_value"]) if row else match_engine.DEFAULT_SEASON


def fmt(value: float | int | None) -> str:
    if value is None:
        return "-"
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}"


def find_player(con: sqlite3.Connection, name: str) -> sqlite3.Row:
    rows = con.execute(
        """
        SELECT p.*, t.abbreviation AS team
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        ORDER BY p.last_name, p.first_name
        """
    ).fetchall()
    needle = normalize_name(name)
    matches = [
        row
        for row in rows
        if needle in normalize_name(f"{row['first_name']} {row['last_name']}")
    ]
    if not matches:
        raise ValueError(f"Player not found: {name}")
    if len(matches) > 1:
        examples = ", ".join(
            f"{row['first_name']} {row['last_name']} ({row['position']}, {row['team'] or row['status']})"
            for row in matches[:8]
        )
        raise ValueError(f"Player search matched {len(matches)} players. Be more specific: {examples}")
    return matches[0]


def find_team(con: sqlite3.Connection, abbreviation: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM teams WHERE abbreviation = ?",
        (abbreviation.upper(),),
    ).fetchone()
    if not row:
        raise ValueError(f"Team not found: {abbreviation}")
    return row


def action_setup(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        match_engine.ensure_schema(con)
        con.commit()
    print("Season history schema ready.")


def action_rebuild(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        season = args.season if args.season is not None else current_season(con)
        match_engine.rebuild_season_history(con, season)
        con.commit()
        counted_runs = con.execute(
            """
            SELECT COUNT(*)
            FROM game_sim_runs
            WHERE season = ? AND status = 'final' AND counts_for_stats = 1
            """,
            (season,),
        ).fetchone()[0]
    print(f"Rebuilt {season} history from {counted_runs} counted game run(s).")


def action_standings(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        match_engine.ensure_schema(con)
        season = args.season if args.season is not None else current_season(con)
        filters = ["season = ?"]
        params: list[object] = [season]
        if args.conference:
            filters.append("conference = ?")
            params.append(args.conference.upper())
        if args.division:
            filters.append("division = ?")
            params.append(args.division)
        rows = con.execute(
            f"""
            SELECT *
            FROM season_standings_view
            WHERE {' AND '.join(filters)}
            ORDER BY conference, division, win_pct DESC, wins DESC, point_diff DESC, abbreviation
            """,
            params,
        ).fetchall()
    if not rows:
        print(f"No standings rows for {season}. Run `stat_history.py rebuild --season {season}` after saving games.")
        return
    current_group = None
    for row in rows:
        group = f"{row['conference']} {row['division']}"
        if group != current_group:
            current_group = group
            print(group)
        record = f"{row['wins']}-{row['losses']}-{row['ties']}"
        print(
            f"  {row['abbreviation']:<3} {record:>7} "
            f"{row['win_pct']:.3f}  PF {row['points_for']:>3}  PA {row['points_against']:>3}  Diff {row['point_diff']:>4}"
        )


def action_leaders(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        match_engine.ensure_schema(con)
        season = args.season if args.season is not None else current_season(con)
        rows = con.execute(
            """
            SELECT *
            FROM season_player_stats_view
            WHERE season = ? AND stat_key = ?
            ORDER BY stat_value DESC, player_name
            LIMIT ?
            """,
            (season, args.stat, args.limit),
        ).fetchall()
    if not rows:
        print(f"No leaders found for {season} stat `{args.stat}`.")
        return
    print(f"{season} {args.stat} leaders")
    for idx, row in enumerate(rows, start=1):
        print(f"  {idx:>2}. {row['player_name']} {row['team']} {row['position']}: {fmt(row['stat_value'])}")


def action_player(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        match_engine.ensure_schema(con)
        season = args.season if args.season is not None else current_season(con)
        player = find_player(con, args.player)
        rows = con.execute(
            """
            SELECT *
            FROM season_player_stats_view
            WHERE season = ? AND player_id = ?
            ORDER BY team, stat_key
            """,
            (season, player["player_id"]),
        ).fetchall()
    print(f"{player['first_name']} {player['last_name']} ({player['position']}) - {season}")
    if not rows:
        print("  No saved season stats yet.")
        return
    for row in rows:
        print(f"  {row['team']:<3} {row['stat_key']:<24} {fmt(row['stat_value'])}")


def action_team(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        match_engine.ensure_schema(con)
        season = args.season if args.season is not None else current_season(con)
        team = find_team(con, args.team)
        rows = con.execute(
            """
            SELECT *
            FROM season_team_stats_view
            WHERE season = ? AND team_id = ?
            ORDER BY stat_key
            """,
            (season, team["team_id"]),
        ).fetchall()
    print(f"{team['abbreviation']} team stats - {season}")
    if not rows:
        print("  No saved season stats yet.")
        return
    for row in rows:
        print(f"  {row['stat_key']:<24} {fmt(row['stat_value'])}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="View and rebuild season history.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Create season history tables/views.")
    setup_parser.set_defaults(func=action_setup)

    rebuild_parser = subparsers.add_parser("rebuild", help="Rebuild season records and stat aggregates.")
    rebuild_parser.add_argument("--season", type=int)
    rebuild_parser.set_defaults(func=action_rebuild)

    standings_parser = subparsers.add_parser("standings", help="Show season standings.")
    standings_parser.add_argument("--season", type=int)
    standings_parser.add_argument("--conference", choices=["AFC", "NFC"])
    standings_parser.add_argument("--division")
    standings_parser.set_defaults(func=action_standings)

    leaders_parser = subparsers.add_parser("leaders", help="Show player stat leaders.")
    leaders_parser.add_argument("--season", type=int)
    leaders_parser.add_argument("--stat", required=True)
    leaders_parser.add_argument("--limit", type=int, default=15)
    leaders_parser.set_defaults(func=action_leaders)

    player_parser = subparsers.add_parser("player", help="Show one player's season stat rows.")
    player_parser.add_argument("player")
    player_parser.add_argument("--season", type=int)
    player_parser.set_defaults(func=action_player)

    team_parser = subparsers.add_parser("team", help="Show one team's season stat rows.")
    team_parser.add_argument("team")
    team_parser.add_argument("--season", type=int)
    team_parser.set_defaults(func=action_team)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
