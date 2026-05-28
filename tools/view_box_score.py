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


def row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() else default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def average(numerator: Any, denominator: Any) -> float:
    denom = as_float(denominator)
    return round(as_float(numerator) / denom, 1) if denom else 0.0


def team_logo_map(con: sqlite3.Connection) -> dict[str, str]:
    if not table_exists(con, "team_graphics_assets"):
        return {}
    rows = con.execute(
        """
        WITH ranked AS (
            SELECT
                t.abbreviation,
                a.local_path,
                ROW_NUMBER() OVER (
                    PARTITION BY t.team_id
                    ORDER BY
                        CASE a.variant
                            WHEN 'scoreboard' THEN 0
                            WHEN 'primary' THEN 1
                            WHEN 'scoreboard_dark' THEN 2
                            WHEN 'dark' THEN 3
                            ELSE 9
                        END,
                        a.asset_id
                ) AS rn
            FROM team_graphics_assets a
            JOIN teams t ON t.team_id = a.team_id
            WHERE a.asset_type = 'logo'
        )
        SELECT abbreviation, local_path
        FROM ranked
        WHERE rn = 1
        """
    ).fetchall()
    return {
        str(row["abbreviation"]): "/" + str(row["local_path"]).replace("\\", "/").lstrip("/")
        for row in rows
        if row["local_path"]
    }


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


def build_box_score_payload(con: sqlite3.Connection, schedule_game_id: int, show_plays: int = 16) -> dict[str, Any]:
    game = game_row(con, schedule_game_id)
    run = latest_run(con, schedule_game_id)
    logos = team_logo_map(con)

    away_score = as_int(row_value(game, "away_score"))
    home_score = as_int(row_value(game, "home_score"))
    if run:
        away_score = as_int(run["away_score"])
        home_score = as_int(run["home_score"])

    team_stat_rows = []
    team_stats: dict[int, dict[str, float]] = {}
    run_id = int(run["run_id"]) if run else None
    if run_id and table_exists(con, "game_team_stats"):
        team_stat_rows = con.execute(
            """
            SELECT team_id, stat_key, stat_value
            FROM game_team_stats
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchall()
        team_stats = stat_maps(team_stat_rows, "team_id")

    away_id = int(game["away_team_id"])
    home_id = int(game["home_team_id"])
    away_stats = team_stats.get(away_id, {})
    home_stats = team_stats.get(home_id, {})

    def team_payload(side: str, team_id: int, abbr_key: str, name_key: str, score: int, stats: dict[str, float]) -> dict[str, Any]:
        opponent_score = home_score if side == "away" else away_score
        if not run:
            result = "Scheduled" if not as_int(row_value(game, "played")) else "Final"
        elif score > opponent_score:
            result = "Win"
        elif score < opponent_score:
            result = "Loss"
        else:
            result = "Tie"
        abbr = str(game[abbr_key])
        return {
            "side": side,
            "teamId": team_id,
            "abbr": abbr,
            "name": str(game[name_key]),
            "logo": logos.get(abbr),
            "score": score,
            "result": result,
            "stats": {key: as_int(value) for key, value in stats.items()},
        }

    teams = [
        team_payload("away", away_id, "away_team", "away_team_name", away_score, away_stats),
        team_payload("home", home_id, "home_team", "home_team_name", home_score, home_stats),
    ]

    comparison_keys = [
        ("first_downs", "First Downs"),
        ("total_yards", "Total Yards"),
        ("pass_yards", "Passing Yards"),
        ("rush_yards", "Rushing Yards"),
        ("turnovers", "Turnovers"),
        ("sacks_allowed", "Sacks Allowed"),
        ("penalties", "Penalties"),
        ("penalty_yards", "Penalty Yards"),
        ("fg_made", "Field Goals"),
        ("punts", "Punts"),
    ]
    comparison = [
        {
            "key": key,
            "label": label,
            "away": as_int(away_stats.get(key, 0)),
            "home": as_int(home_stats.get(key, 0)),
        }
        for key, label in comparison_keys
    ]

    empty_sections = {"passing": [], "rushing": [], "receiving": [], "kicking": [], "punting": [], "defense": []}
    players_by_team: dict[int, dict[str, list[dict[str, Any]]]] = {
        away_id: {key: [] for key in empty_sections},
        home_id: {key: [] for key in empty_sections},
    }
    if run_id:
        rows = player_stats(con, run_id)
        player_team: dict[int, int] = {}
        player_name: dict[int, str] = {}
        player_position: dict[int, str] = {}
        player_stat_map: dict[int, dict[str, float]] = {}
        for row in rows:
            player_id = int(row["player_id"])
            player_team[player_id] = int(row["team_id"])
            player_name[player_id] = str(row["player_name"])
            player_position[player_id] = str(row["position"] or "")
            player_stat_map.setdefault(player_id, {})[str(row["stat_key"])] = float(row["stat_value"] or 0)

        for player_id, stats in player_stat_map.items():
            team_id = player_team.get(player_id)
            if team_id not in players_by_team:
                continue
            base = {
                "playerId": player_id,
                "name": player_name.get(player_id, f"Player {player_id}"),
                "position": player_position.get(player_id, ""),
            }
            if stats.get("pass_attempts", 0):
                interceptions = stats.get("interceptions_thrown", stats.get("interceptions", 0))
                players_by_team[team_id]["passing"].append({
                    **base,
                    "completions": as_int(stats.get("pass_completions")),
                    "attempts": as_int(stats.get("pass_attempts")),
                    "yards": as_int(stats.get("pass_yards")),
                    "td": as_int(stats.get("pass_tds")),
                    "int": as_int(interceptions),
                    "sacks": as_int(stats.get("sacks_taken")),
                    "sort": as_int(stats.get("pass_yards")),
                })
            if stats.get("rush_attempts", 0):
                players_by_team[team_id]["rushing"].append({
                    **base,
                    "attempts": as_int(stats.get("rush_attempts")),
                    "yards": as_int(stats.get("rush_yards")),
                    "avg": average(stats.get("rush_yards"), stats.get("rush_attempts")),
                    "td": as_int(stats.get("rush_tds")),
                    "sort": as_int(stats.get("rush_yards")),
                })
            if stats.get("targets", 0) or stats.get("receptions", 0):
                players_by_team[team_id]["receiving"].append({
                    **base,
                    "receptions": as_int(stats.get("receptions")),
                    "targets": as_int(stats.get("targets")),
                    "yards": as_int(stats.get("receiving_yards")),
                    "avg": average(stats.get("receiving_yards"), stats.get("receptions")),
                    "td": as_int(stats.get("receiving_tds")),
                    "sort": as_int(stats.get("receiving_yards")),
                })
            if stats.get("fg_attempts", 0) or stats.get("xp_attempts", 0):
                players_by_team[team_id]["kicking"].append({
                    **base,
                    "fgMade": as_int(stats.get("fg_made")),
                    "fgAttempts": as_int(stats.get("fg_attempts")),
                    "xpMade": as_int(stats.get("xp_made")),
                    "xpAttempts": as_int(stats.get("xp_attempts")),
                    "long": as_int(stats.get("long_fg")),
                    "sort": as_int(stats.get("fg_made")),
                })
            if stats.get("punts", 0):
                players_by_team[team_id]["punting"].append({
                    **base,
                    "punts": as_int(stats.get("punts")),
                    "yards": as_int(stats.get("punt_yards")),
                    "avg": average(stats.get("punt_yards"), stats.get("punts")),
                    "sort": as_int(stats.get("punt_yards")),
                })

            defense_total = sum(
                as_int(stats.get(key, 0))
                for key in (
                    "tackles",
                    "solo_tackles",
                    "assisted_tackles",
                    "sacks",
                    "interceptions",
                    "pass_deflections",
                    "forced_fumbles",
                    "fumble_recoveries",
                )
            )
            if defense_total:
                tackles = as_int(stats.get("tackles"))
                players_by_team[team_id]["defense"].append({
                    **base,
                    "tackles": tackles,
                    "solo": as_int(stats.get("solo_tackles", tackles if not stats.get("assisted_tackles", 0) else 0)),
                    "assists": as_int(stats.get("assisted_tackles")),
                    "sacks": as_float(stats.get("sacks")),
                    "int": as_int(stats.get("interceptions")),
                    "pd": as_int(stats.get("pass_deflections")),
                    "ff": as_int(stats.get("forced_fumbles")),
                    "fr": as_int(stats.get("fumble_recoveries")),
                    "sort": tackles,
                })

        for sections in players_by_team.values():
            for section, rows in sections.items():
                if section == "passing":
                    rows.sort(key=lambda item: (item.get("attempts", 0), item.get("yards", 0)), reverse=True)
                elif section == "defense":
                    rows.sort(key=lambda item: (item.get("sort", 0), item.get("sacks", 0), item.get("int", 0)), reverse=True)
                else:
                    rows.sort(key=lambda item: item.get("sort", 0), reverse=True)

    drives: list[dict[str, Any]] = []
    if run_id and table_exists(con, "game_sim_drives"):
        drive_rows = con.execute(
            """
            SELECT d.*, t.abbreviation AS offense
            FROM game_sim_drives d
            JOIN teams t ON t.team_id = d.offense_team_id
            WHERE d.run_id = ?
            ORDER BY d.drive_number
            """,
            (run_id,),
        ).fetchall()
        drives = [
            {
                "driveNumber": as_int(row["drive_number"]),
                "offense": str(row["offense"]),
                "quarter": as_int(row["start_quarter"]),
                "clock": clock_string(row["start_clock_tenths"]),
                "result": str(row["result"] or ""),
                "plays": as_int(row["plays"]),
                "yards": as_int(row["yards"]),
                "points": as_int(row_value(row, "points")),
            }
            for row in drive_rows
        ]

    plays: list[dict[str, Any]] = []
    if run_id and show_plays and table_exists(con, "game_sim_plays"):
        play_rows = con.execute(
            """
            SELECT gp.*, t.abbreviation AS offense
            FROM game_sim_plays gp
            JOIN teams t ON t.team_id = gp.offense_team_id
            WHERE gp.run_id = ?
            ORDER BY gp.play_number DESC
            LIMIT ?
            """,
            (run_id, int(show_plays)),
        ).fetchall()
        plays = [
            {
                "playNumber": as_int(row["play_number"]),
                "quarter": as_int(row["quarter"]),
                "clock": clock_string(row["clock_tenths"]),
                "offense": str(row["offense"]),
                "down": as_int(row["down"]),
                "distance": as_int(row["distance"]),
                "yards": as_int(row["yards_gained"]),
                "description": str(row["description"] or ""),
                "touchdown": bool(row["is_touchdown"]),
                "turnover": bool(row["is_turnover"]),
            }
            for row in reversed(play_rows)
        ]

    players = {
        team["abbr"]: players_by_team.get(int(team["teamId"]), {key: [] for key in empty_sections})
        for team in teams
    }
    return {
        "gameId": int(schedule_game_id),
        "status": "final" if run else ("played" if as_int(row_value(game, "played")) else "scheduled"),
        "season": as_int(row_value(game, "season")),
        "week": row_value(game, "week"),
        "gameType": row_value(game, "game_type"),
        "gameDate": row_value(game, "game_date"),
        "matchup": f"{game['away_team']} at {game['home_team']}",
        "run": {
            "runId": run_id,
            "engineVersion": row_value(run, "engine_version"),
            "seed": row_value(run, "seed"),
            "totalPlays": row_value(run, "total_plays"),
            "totalDrives": row_value(run, "total_drives"),
        } if run else None,
        "teams": teams,
        "comparison": comparison,
        "players": players,
        "drives": drives,
        "plays": plays,
    }


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
                for key in (
                    "tackles",
                    "solo_tackles",
                    "assisted_tackles",
                    "sacks",
                    "interceptions",
                    "pass_deflections",
                    "forced_fumbles",
                    "fumble_recoveries",
                )
            )
            if defense_total:
                tackles = int(stats.get("tackles", 0))
                solo = int(stats.get("solo_tackles", tackles if not stats.get("assisted_tackles", 0) else 0))
                assisted = int(stats.get("assisted_tackles", 0))
                defense.append(
                    (
                        tackles,
                        name,
                        f"  {name}: {tackles} TKL ({solo} solo, {assisted} ast), {int(stats.get('sacks', 0))} SK, "
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
