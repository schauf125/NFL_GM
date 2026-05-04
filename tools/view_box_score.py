#!/usr/bin/env python3
"""Show a stored box score for a simulated schedule game."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MASTER_DB = ROOT / "database" / "nfl_gm.db"
SAVE_REGISTRY = ROOT / "saves" / "save_registry.json"


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def default_db() -> Path:
    registry = read_json(SAVE_REGISTRY, {"active_game_id": None, "saves": {}})
    active_id = registry.get("active_game_id")
    if active_id:
        record = registry.get("saves", {}).get(active_id)
        if record and record.get("db_path"):
            path = ROOT / record["db_path"]
            if path.exists():
                return path
    return MASTER_DB


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone() is not None


def clock_string(tenths: int | float | None) -> str:
    total_seconds = max(0, int((tenths or 0) // 10))
    return f"{total_seconds // 60:02d}:{total_seconds % 60:02d}"


def stat_maps(rows: list[sqlite3.Row], key_field: str) -> dict[int, dict[str, float]]:
    output: dict[int, dict[str, float]] = {}
    for row in rows:
        entity_id = int(row[key_field])
        output.setdefault(entity_id, {})[str(row["stat_key"])] = float(row["stat_value"] or 0)
    return output


def latest_run(con: sqlite3.Connection, schedule_game_id: int) -> sqlite3.Row | None:
    if not table_exists(con, "game_sim_runs"):
        return None
    return con.execute(
        """
        SELECT *
        FROM game_sim_runs
        WHERE schedule_game_id = ?
          AND status = 'final'
        ORDER BY run_id DESC
        LIMIT 1
        """,
        (schedule_game_id,),
    ).fetchone()


def game_row(con: sqlite3.Connection, schedule_game_id: int) -> sqlite3.Row:
    row = con.execute(
        """
        SELECT
            sg.*,
            away.abbreviation AS away_team,
            away.city || ' ' || away.nickname AS away_team_name,
            home.abbreviation AS home_team,
            home.city || ' ' || home.nickname AS home_team_name
        FROM season_games sg
        JOIN teams away ON away.team_id = sg.away_team_id
        JOIN teams home ON home.team_id = sg.home_team_id
        WHERE sg.game_id = ?
        """,
        (schedule_game_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Schedule game not found: {schedule_game_id}")
    return row


def print_team_line(team: str, score: Any, stats: dict[str, float]) -> None:
    print(
        f"  {team} {int(score or 0)}: "
        f"{int(stats.get('total_yards', 0))} yds, "
        f"{int(stats.get('first_downs', 0))} 1st downs, "
        f"{int(stats.get('turnovers', 0))} TO, "
        f"{int(stats.get('penalty_yards', 0))} penalty yds"
    )


def print_player_section(label: str, lines: list[str]) -> None:
    if not lines:
        return
    print(label)
    for line in lines:
        print(line)


def player_stats(con: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    if not table_exists(con, "game_player_stats"):
        return []
    return con.execute(
        """
        SELECT
            gps.player_id,
            gps.team_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            gps.stat_key,
            gps.stat_value
        FROM game_player_stats gps
        JOIN players p ON p.player_id = gps.player_id
        WHERE gps.run_id = ?
        ORDER BY gps.team_id, p.last_name, p.first_name, gps.stat_key
        """,
        (run_id,),
    ).fetchall()


def print_box_score(con: sqlite3.Connection, schedule_game_id: int, show_plays: int) -> None:
    game = game_row(con, schedule_game_id)
    run = latest_run(con, schedule_game_id)

    print(f"{game['away_team']} at {game['home_team']}")
    print(f"Season {game['season']} | Week {game['week'] or '-'} | {game['game_type']} | {game['game_date']}")
    if not run:
        status = "final score is present" if int(game["played"] or 0) else "not played yet"
        print(f"\nNo stored box score found for schedule game {schedule_game_id} ({status}).")
        return

    run_id = int(run["run_id"])
    print(f"Final: {game['away_team']} {run['away_score']} - {game['home_team']} {run['home_score']}")
    print(f"Run {run_id} | Engine {run['engine_version']} | Seed {run['seed']}")

    team_stat_rows = con.execute(
        """
        SELECT team_id, stat_key, stat_value
        FROM game_team_stats
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchall() if table_exists(con, "game_team_stats") else []
    teams = {
        int(game["away_team_id"]): game["away_team"],
        int(game["home_team_id"]): game["home_team"],
    }
    team_stats = stat_maps(team_stat_rows, "team_id")

    print("\nTeam")
    print_team_line(game["away_team"], run["away_score"], team_stats.get(int(game["away_team_id"]), {}))
    print_team_line(game["home_team"], run["home_score"], team_stats.get(int(game["home_team_id"]), {}))

    rows = player_stats(con, run_id)
    player_team: dict[int, int] = {}
    player_name: dict[int, str] = {}
    player_stat_map: dict[int, dict[str, float]] = {}
    for row in rows:
        player_id = int(row["player_id"])
        player_team[player_id] = int(row["team_id"])
        player_name[player_id] = row["player_name"]
        player_stat_map.setdefault(player_id, {})[str(row["stat_key"])] = float(row["stat_value"] or 0)

    for team_id, team in teams.items():
        print(f"\n{team}")
        passing: list[str] = []
        rushing: list[tuple[float, str]] = []
        receiving: list[tuple[float, str]] = []
        kicking_punting: list[str] = []
        defense: list[tuple[float, str, str]] = []

        for player_id, stats in player_stat_map.items():
            if player_team.get(player_id) != team_id:
                continue
            name = player_name.get(player_id, f"Player {player_id}")
            if stats.get("pass_attempts", 0):
                interceptions_thrown = stats.get("interceptions_thrown", stats.get("interceptions", 0))
                passing.append(
                    f"  {name}: {int(stats.get('pass_completions', 0))}/{int(stats.get('pass_attempts', 0))}, "
                    f"{int(stats.get('pass_yards', 0))} yds, {int(stats.get('pass_tds', 0))} TD, "
                    f"{int(interceptions_thrown)} INT, {int(stats.get('sacks_taken', 0))} sacks"
                )
            if stats.get("rush_attempts", 0):
                attempts = int(stats.get("rush_attempts", 0))
                yards = int(stats.get("rush_yards", 0))
                rushing.append(
                    (
                        yards,
                        f"  {name}: {attempts} car, {yards} yds, {(yards / attempts if attempts else 0):.1f} avg, "
                        f"{int(stats.get('rush_tds', 0))} TD",
                    )
                )
            if stats.get("targets", 0) or stats.get("receptions", 0):
                receptions = int(stats.get("receptions", 0))
                targets = int(stats.get("targets", 0))
                yards = int(stats.get("receiving_yards", 0))
                receiving.append(
                    (
                        yards,
                        f"  {name}: {receptions}/{targets}, {yards} yds, {(yards / receptions if receptions else 0):.1f} avg, "
                        f"{int(stats.get('receiving_tds', 0))} TD",
                    )
                )
            if stats.get("fg_attempts", 0) or stats.get("xp_attempts", 0):
                kicking_punting.append(
                    f"  {name}: FG {int(stats.get('fg_made', 0))}/{int(stats.get('fg_attempts', 0))}, "
                    f"XP {int(stats.get('xp_made', 0))}/{int(stats.get('xp_attempts', 0))}, "
                    f"long {int(stats.get('long_fg', 0))}"
                )
            if stats.get("punts", 0):
                punts = int(stats.get("punts", 0))
                yards = int(stats.get("punt_yards", 0))
                kicking_punting.append(f"  {name}: {punts} punts, {yards} yds, {(yards / punts if punts else 0):.1f} avg")

            defense_total = sum(
                int(stats.get(key, 0))
                for key in ("tackles", "sacks", "interceptions", "pass_deflections", "forced_fumbles", "fumble_recoveries")
            )
            if defense_total:
                defense.append(
                    (
                        stats.get("tackles", 0),
                        name,
                        f"  {name}: {int(stats.get('tackles', 0))} TKL, {int(stats.get('sacks', 0))} SK, "
                        f"{int(stats.get('interceptions', 0))} INT, {int(stats.get('pass_deflections', 0))} PD, "
                        f"{int(stats.get('forced_fumbles', 0))} FF, {int(stats.get('fumble_recoveries', 0))} FR",
                    )
                )

        print_player_section("Passing", passing)
        print_player_section("Rushing", [line for _yards, line in sorted(rushing, reverse=True)])
        print_player_section("Receiving", [line for _yards, line in sorted(receiving, reverse=True)])
        print_player_section("Kicking/Punting", kicking_punting)
        print_player_section("Defense", [line for _tackles, _name, line in sorted(defense, reverse=True)[:10]])

    if table_exists(con, "game_sim_drives"):
        drives = con.execute(
            """
            SELECT d.*, t.abbreviation AS offense
            FROM game_sim_drives d
            JOIN teams t ON t.team_id = d.offense_team_id
            WHERE d.run_id = ?
            ORDER BY d.drive_number
            """,
            (run_id,),
        ).fetchall()
        if drives:
            print("\nDrive Summary")
            for drive in drives:
                print(
                    f"  {int(drive['drive_number']):>2}. {drive['offense']} "
                    f"Q{drive['start_quarter']} {clock_string(drive['start_clock_tenths'])}: "
                    f"{drive['result']}, {drive['plays']} plays, {int(drive['yards'] or 0)} yds"
                )

    if show_plays and table_exists(con, "game_sim_plays"):
        plays = con.execute(
            """
            SELECT gp.*, t.abbreviation AS offense
            FROM game_sim_plays gp
            JOIN teams t ON t.team_id = gp.offense_team_id
            WHERE gp.run_id = ?
            ORDER BY gp.play_number DESC
            LIMIT ?
            """,
            (run_id, show_plays),
        ).fetchall()
        if plays:
            print(f"\nLast {len(plays)} Plays")
            for play in reversed(plays):
                print(
                    f"  Q{play['quarter']} {clock_string(play['clock_tenths'])} "
                    f"{play['offense']} {play['down']}&{play['distance']}: {play['description']}"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Show a stored box score for a simulated game.")
    parser.add_argument("--db", type=Path, default=default_db())
    parser.add_argument("--game-id", "--schedule-game-id", dest="schedule_game_id", type=int, required=True)
    parser.add_argument("--show-plays", type=int, default=16)
    args = parser.parse_args()

    with connect(args.db) as con:
        print_box_score(con, args.schedule_game_id, args.show_plays)


if __name__ == "__main__":
    main()
