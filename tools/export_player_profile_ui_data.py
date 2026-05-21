"""Export data for the FM-style player profile UI."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import pro_player_fog


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "database" / "nfl_gm.db"
DEFAULT_OUTPUT = PROJECT_ROOT / "ui" / "player_profile" / "player-profile-data.js"
CURRENT_SEASON = 2026

SEASON_STAT_KEYS = [
    "season", "stat_team", "stat_position", "games", "completions", "passing_attempts",
    "passing_yards", "passing_tds", "passing_interceptions", "sacks_suffered",
    "sack_yards_lost", "carries", "rushing_yards", "rushing_tds", "rushing_fumbles",
    "rushing_fumbles_lost", "receptions", "targets", "receiving_yards", "receiving_tds",
    "receiving_fumbles", "receiving_fumbles_lost", "def_tackles_solo",
    "def_tackles_with_assist", "def_tackle_assists", "def_tackles_for_loss",
    "def_fumbles_forced", "def_sacks", "def_qb_hits", "def_interceptions",
    "def_interception_yards", "def_pass_defended", "def_tds", "def_safeties",
    "punt_returns", "punt_return_yards", "kickoff_returns", "kickoff_return_yards",
    "fg_made", "fg_att", "fg_long", "fg_pct", "pat_made", "pat_att", "pat_pct",
    "fantasy_points", "fantasy_points_ppr", "source",
]

SUM_CAREER_FIELDS = [
    "games", "completions", "passing_attempts", "passing_yards", "passing_tds",
    "passing_interceptions", "sacks_suffered", "sack_yards_lost", "carries",
    "rushing_yards", "rushing_tds", "rushing_fumbles", "rushing_fumbles_lost",
    "receptions", "targets", "receiving_yards", "receiving_tds", "receiving_fumbles",
    "receiving_fumbles_lost", "def_tackles_solo", "def_tackles_with_assist",
    "def_tackle_assists", "def_tackles_for_loss", "def_fumbles_forced", "def_sacks",
    "def_qb_hits", "def_interceptions", "def_interception_yards", "def_pass_defended",
    "def_tds", "def_safeties", "punt_returns", "punt_return_yards",
    "kickoff_returns", "kickoff_return_yards", "fg_made", "fg_att", "pat_made",
    "pat_att", "fantasy_points", "fantasy_points_ppr",
]

POSITION_LABELS = {
    "QB": "Quarterback",
    "RB": "Running Back",
    "FB": "Fullback",
    "WR": "Wide Receiver",
    "TE": "Tight End",
    "OT": "Offensive Tackle",
    "OG": "Guard",
    "C": "Center",
    "IDL": "Interior Defensive Line",
    "EDGE": "Edge Defender",
    "LB": "Linebacker",
    "CB": "Cornerback",
    "S": "Safety",
    "K": "Kicker",
    "P": "Punter",
    "LS": "Long Snapper",
}

GROUP_LABELS = {
    "universal": "Physical / Mental",
    "passer": "Passing",
    "ball_carrier": "Ball Carrying",
    "receiver": "Receiving",
    "blocker": "Blocking",
    "pass_rusher": "Pass Rush",
    "run_defender": "Run Defense",
    "coverage": "Coverage",
    "tackler": "Tackling",
    "specialist": "Special Teams",
}

GROUP_ORDER = {
    "universal": 1,
    "passer": 2,
    "ball_carrier": 3,
    "receiver": 4,
    "blocker": 5,
    "pass_rusher": 6,
    "run_defender": 7,
    "coverage": 8,
    "tackler": 9,
    "specialist": 10,
}

ROLE_REPLACEMENTS = {
    "qb": "QB",
    "wr": "WR",
    "rb": "RB",
    "ot": "OT",
    "te": "TE",
    "idl": "IDL",
    "cb": "CB",
}


def clean_hex(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    value = value.strip().lstrip("#")
    if len(value) in {3, 6} and all(char in "0123456789abcdefABCDEF" for char in value):
        return f"#{value}"
    return fallback


def grade_label(value: float | None) -> str:
    if value is None:
        return "Unknown"
    if value >= 94:
        return "Elite"
    if value >= 88:
        return "Excellent"
    if value >= 82:
        return "Very Good"
    if value >= 74:
        return "Strong"
    if value >= 66:
        return "Solid"
    if value >= 58:
        return "Developing"
    if value >= 50:
        return "Raw"
    return "Concern"


def role_label(role_key: str | None) -> str:
    if not role_key:
        return "Depth Role"
    words = []
    for part in role_key.split("_"):
        words.append(ROLE_REPLACEMENTS.get(part, part.title()))
    return " ".join(words)


def height_label(height_in: int | None) -> str:
    if not height_in:
        return "--"
    feet, inches = divmod(int(height_in), 12)
    return f"{feet}'{inches}\""


def years_label(years_exp: int | None, is_rookie: int | None) -> str:
    if is_rookie or years_exp == 0:
        return "Rookie"
    if years_exp is None:
        return "--"
    if years_exp == 1:
        return "1 year"
    return f"{years_exp} years"


def relative_ui_path(local_path: str | None) -> str | None:
    if not local_path:
        return None
    return "../../" + local_path.replace("\\", "/").lstrip("/")


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def money(value: int | None) -> int:
    return int(value or 0)


def team_assets(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT t.team_id, t.abbreviation, t.city, t.nickname, t.conference, t.division,
               g.local_path, g.color, g.alternate_color
        FROM teams t
        LEFT JOIN team_graphics_assets g
          ON g.team_id = t.team_id
         AND g.variant = 'primary'
         AND g.asset_type = 'logo'
        """
    ).fetchall()
    return {
        int(row["team_id"]): {
            "id": int(row["team_id"]),
            "abbr": row["abbreviation"],
            "name": f"{row['city']} {row['nickname']}",
            "conference": row["conference"],
            "division": row["division"],
            "logo": relative_ui_path(row["local_path"]),
            "primary": clean_hex(row["color"], "#75808f"),
            "secondary": clean_hex(row["alternate_color"], "#d6dde6"),
        }
        for row in rows
    }


def headshots(conn: sqlite3.Connection) -> dict[int, str]:
    if not table_exists(conn, "player_graphics_assets"):
        return {}
    rows = conn.execute(
        """
        SELECT player_id, local_path
        FROM player_graphics_assets
        WHERE asset_key = 'headshot_espn_full'
        """
    ).fetchall()
    return {int(row["player_id"]): relative_ui_path(row["local_path"]) for row in rows}


def fetch_players(conn: sqlite3.Connection, limit: int | None, player_id: int | None = None) -> list[sqlite3.Row]:
    params: list[Any] = []
    player_filter = ""
    if player_id is not None:
        player_filter = " AND p.player_id = ?"
        params.append(player_id)
    sql = """
        SELECT p.*, t.abbreviation AS team_abbr
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        WHERE COALESCE(p.status, 'Active') != 'Retired'
        {player_filter}
        ORDER BY
            CASE WHEN p.team_id IS NULL THEN 1 ELSE 0 END,
            t.abbreviation,
            p.last_name,
            p.first_name
    """.format(player_filter=player_filter)
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def ratings_by_player(
    conn: sqlite3.Connection,
    season: int,
    player_ids: list[int],
    evaluations: dict[int, dict[str, Any]] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        WITH latest AS (
            SELECT player_id, MAX(season) AS season
            FROM player_sim_ratings_view
            WHERE season <= ?
              AND player_id IN ({placeholders})
            GROUP BY player_id
        )
        SELECT r.player_id, r.season, r.rating_group, r.rating_key, r.display_name, r.rating_value, r.confidence, r.source
        FROM player_sim_ratings_view r
        JOIN latest l
          ON l.player_id = r.player_id
         AND l.season = r.season
        ORDER BY r.player_id, r.rating_group, r.display_name
        """,
        [season, *player_ids],
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        evaluation = (evaluations or {}).get(int(row["player_id"]))
        true_value = float(row["rating_value"])
        value = pro_player_fog.fog_rating_value(
            evaluation,
            str(row["rating_key"]),
            true_value,
        )
        grouped.setdefault(int(row["player_id"]), []).append({
            "group": row["rating_group"],
            "groupLabel": GROUP_LABELS.get(row["rating_group"], row["rating_group"].replace("_", " ").title()),
            "groupOrder": GROUP_ORDER.get(row["rating_group"], 99),
            "key": row["rating_key"],
            "label": row["display_name"],
            "value": round(max(0, min(100, value)), 1),
            "grade": grade_label(value),
            "confidence": evaluation.get("confidenceLabel") if evaluation else (row["confidence"] or "medium"),
            "source": row["source"] or "",
            "season": int(row["season"]),
        })
    return grouped


def roles_by_player(
    conn: sqlite3.Connection,
    season: int,
    player_ids: list[int],
    evaluations: dict[int, dict[str, Any]] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        WITH latest AS (
            SELECT player_id, MAX(season) AS season
            FROM player_role_scores
            WHERE season <= ?
              AND scheme_key = 'default'
              AND player_id IN ({placeholders})
            GROUP BY player_id
        )
        SELECT r.player_id, r.season, r.role_key, r.role_score, r.source
        FROM player_role_scores r
        JOIN latest l
          ON l.player_id = r.player_id
         AND l.season = r.season
        WHERE r.scheme_key = 'default'
        ORDER BY r.player_id, r.role_score DESC
        """,
        [season, *player_ids],
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        value = pro_player_fog.fog_role_value(
            (evaluations or {}).get(int(row["player_id"])),
            row["role_key"],
            float(row["role_score"]),
        )
        items = grouped.setdefault(int(row["player_id"]), [])
        if len(items) < 8:
            items.append({
                "key": row["role_key"],
                "label": role_label(row["role_key"]),
                "value": round(max(0, min(100, value)), 1),
                "grade": grade_label(value),
                "source": row["source"] or "",
                "season": int(row["season"]),
            })
    return grouped


def flex_by_player(conn: sqlite3.Connection, player_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT player_id, position, experience, potential, is_primary, source, notes
        FROM player_position_flex
        WHERE player_id IN ({placeholders})
        ORDER BY player_id, is_primary DESC, experience DESC, potential DESC, position
        """,
        player_ids,
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["player_id"]), []).append({
            "position": row["position"],
            "current": int(row["experience"]),
            "potential": int(row["potential"]),
            "primary": bool(row["is_primary"]),
            "source": row["source"] or "",
            "notes": row["notes"] or "",
        })
    return grouped


def career_by_player(conn: sqlite3.Connection, player_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM player_career_stats
        WHERE player_id IN ({placeholders})
        """,
        player_ids,
    ).fetchall()
    return {int(row["player_id"]): dict(row) for row in rows}


def pct_value(made: float, attempts: float) -> float | None:
    if attempts <= 0:
        return None
    return round(made * 100.0 / attempts, 1)


def number(row: sqlite3.Row | dict[str, Any], key: str) -> float:
    value = row[key] if isinstance(row, sqlite3.Row) else row.get(key)
    return float(value or 0)


def fantasy_ppr(row: dict[str, Any]) -> float:
    return round(
        number(row, "passing_yards") / 25.0
        + number(row, "passing_tds") * 4.0
        - number(row, "passing_interceptions") * 2.0
        + number(row, "rushing_yards") / 10.0
        + number(row, "rushing_tds") * 6.0
        + number(row, "receptions")
        + number(row, "receiving_yards") / 10.0
        + number(row, "receiving_tds") * 6.0
        - (number(row, "rushing_fumbles_lost") + number(row, "receiving_fumbles_lost")) * 2.0
        + number(row, "fg_made") * 3.0
        + number(row, "pat_made"),
        1,
    )


def normalized_historical_season_rows(conn: sqlite3.Connection, player_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not player_ids or not table_exists(conn, "player_season_stats"):
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT
            player_id,
            season,
            team AS stat_team,
            position AS stat_position,
            games,
            completions,
            passing_attempts,
            passing_yards,
            passing_tds,
            passing_interceptions,
            sacks_suffered,
            sack_yards_lost,
            carries,
            rushing_yards,
            rushing_tds,
            rushing_fumbles,
            rushing_fumbles_lost,
            receptions,
            targets,
            receiving_yards,
            receiving_tds,
            receiving_fumbles,
            receiving_fumbles_lost,
            def_tackles_solo,
            def_tackles_with_assist,
            def_tackle_assists,
            def_tackles_for_loss,
            def_fumbles_forced,
            def_sacks,
            def_qb_hits,
            def_interceptions,
            def_interception_yards,
            def_pass_defended,
            def_tds,
            def_safeties,
            punt_returns,
            punt_return_yards,
            kickoff_returns,
            kickoff_return_yards,
            fg_made,
            fg_att,
            fg_long,
            fg_pct,
            pat_made,
            pat_att,
            pat_pct,
            fantasy_points,
            fantasy_points_ppr,
            source
        FROM player_season_stats
        WHERE player_id IN ({placeholders})
        """,
        player_ids,
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["player_id"]), []).append({key: row[key] for key in SEASON_STAT_KEYS})
    return grouped


def normalized_simulated_season_rows(conn: sqlite3.Connection, player_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    required_tables = ["season_player_stats", "game_player_stats", "game_sim_runs", "season_games", "teams", "players"]
    if not player_ids or any(not table_exists(conn, table) for table in required_tables):
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        WITH pivot AS (
            SELECT
                season,
                player_id,
                team_id,
                SUM(CASE WHEN stat_key = 'pass_completions' THEN stat_value ELSE 0 END) AS completions,
                SUM(CASE WHEN stat_key = 'pass_attempts' THEN stat_value ELSE 0 END) AS passing_attempts,
                SUM(CASE WHEN stat_key = 'pass_yards' THEN stat_value ELSE 0 END) AS passing_yards,
                SUM(CASE WHEN stat_key = 'pass_tds' THEN stat_value ELSE 0 END) AS passing_tds,
                SUM(CASE WHEN stat_key = 'interceptions_thrown' THEN stat_value ELSE 0 END) AS passing_interceptions,
                SUM(CASE WHEN stat_key = 'sacks_taken' THEN stat_value ELSE 0 END) AS sacks_suffered,
                SUM(CASE WHEN stat_key = 'rush_attempts' THEN stat_value ELSE 0 END) AS carries,
                SUM(CASE WHEN stat_key = 'rush_yards' THEN stat_value ELSE 0 END) AS rushing_yards,
                SUM(CASE WHEN stat_key = 'rush_tds' THEN stat_value ELSE 0 END) AS rushing_tds,
                SUM(CASE WHEN stat_key = 'fumbles' THEN stat_value ELSE 0 END) AS fumbles,
                SUM(CASE WHEN stat_key = 'fumbles_lost' THEN stat_value ELSE 0 END) AS fumbles_lost,
                SUM(CASE WHEN stat_key = 'receptions' THEN stat_value ELSE 0 END) AS receptions,
                SUM(CASE WHEN stat_key = 'targets' THEN stat_value ELSE 0 END) AS targets,
                SUM(CASE WHEN stat_key = 'receiving_yards' THEN stat_value ELSE 0 END) AS receiving_yards,
                SUM(CASE WHEN stat_key = 'receiving_tds' THEN stat_value ELSE 0 END) AS receiving_tds,
                SUM(CASE WHEN stat_key = 'solo_tackles' THEN stat_value ELSE 0 END) AS def_tackles_solo,
                SUM(CASE WHEN stat_key = 'assisted_tackles' THEN stat_value ELSE 0 END) AS def_tackles_with_assist,
                SUM(CASE WHEN stat_key = 'forced_fumbles' THEN stat_value ELSE 0 END) AS def_fumbles_forced,
                SUM(CASE WHEN stat_key = 'sacks' THEN stat_value ELSE 0 END) AS def_sacks,
                SUM(CASE WHEN stat_key = 'interceptions' THEN stat_value ELSE 0 END) AS def_interceptions,
                SUM(CASE WHEN stat_key = 'interception_return_yards' THEN stat_value ELSE 0 END) AS def_interception_yards,
                SUM(CASE WHEN stat_key = 'pass_deflections' THEN stat_value ELSE 0 END) AS def_pass_defended,
                SUM(CASE WHEN stat_key = 'defensive_tds' THEN stat_value ELSE 0 END) AS def_tds,
                SUM(CASE WHEN stat_key = 'punt_returns' THEN stat_value ELSE 0 END) AS punt_returns,
                SUM(CASE WHEN stat_key = 'punt_return_yards' THEN stat_value ELSE 0 END) AS punt_return_yards,
                SUM(CASE WHEN stat_key = 'kickoff_returns' THEN stat_value ELSE 0 END) AS kickoff_returns,
                SUM(CASE WHEN stat_key = 'kickoff_return_yards' THEN stat_value ELSE 0 END) AS kickoff_return_yards,
                SUM(CASE WHEN stat_key = 'fg_made' THEN stat_value ELSE 0 END) AS fg_made,
                SUM(CASE WHEN stat_key = 'fg_attempts' THEN stat_value ELSE 0 END) AS fg_att,
                MAX(CASE WHEN stat_key = 'long_fg' THEN stat_value ELSE 0 END) AS fg_long,
                SUM(CASE WHEN stat_key = 'xp_made' THEN stat_value ELSE 0 END) AS pat_made,
                SUM(CASE WHEN stat_key = 'xp_attempts' THEN stat_value ELSE 0 END) AS pat_att
            FROM season_player_stats
            WHERE player_id IN ({placeholders})
            GROUP BY season, player_id, team_id
        ),
        games AS (
            SELECT
                r.season,
                gps.player_id,
                gps.team_id,
                COUNT(DISTINCT gps.run_id) AS games
            FROM game_player_stats gps
            JOIN game_sim_runs r ON r.run_id = gps.run_id
            LEFT JOIN season_games sg ON sg.game_id = r.schedule_game_id
            WHERE gps.player_id IN ({placeholders})
              AND COALESCE(r.counts_for_stats, 1) = 1
              AND COALESCE(r.status, 'final') = 'final'
              AND (sg.game_id IS NULL OR sg.game_type = 'REG')
            GROUP BY r.season, gps.player_id, gps.team_id
        )
        SELECT
            pivot.*,
            COALESCE(games.games, 0) AS games,
            p.position AS stat_position,
            t.abbreviation AS stat_team
        FROM pivot
        JOIN players p ON p.player_id = pivot.player_id
        JOIN teams t ON t.team_id = pivot.team_id
        LEFT JOIN games
          ON games.season = pivot.season
         AND games.player_id = pivot.player_id
         AND games.team_id = pivot.team_id
        """,
        [*player_ids, *player_ids],
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        item = {
            "season": int(row["season"]),
            "stat_team": row["stat_team"],
            "stat_position": row["stat_position"],
            "games": int(row["games"] or 0),
            "completions": int(row["completions"] or 0),
            "passing_attempts": int(row["passing_attempts"] or 0),
            "passing_yards": int(row["passing_yards"] or 0),
            "passing_tds": int(row["passing_tds"] or 0),
            "passing_interceptions": int(row["passing_interceptions"] or 0),
            "sacks_suffered": int(row["sacks_suffered"] or 0),
            "sack_yards_lost": 0,
            "carries": int(row["carries"] or 0),
            "rushing_yards": int(row["rushing_yards"] or 0),
            "rushing_tds": int(row["rushing_tds"] or 0),
            "rushing_fumbles": int(row["fumbles"] or 0),
            "rushing_fumbles_lost": int(row["fumbles_lost"] or 0),
            "receptions": int(row["receptions"] or 0),
            "targets": int(row["targets"] or 0),
            "receiving_yards": int(row["receiving_yards"] or 0),
            "receiving_tds": int(row["receiving_tds"] or 0),
            "receiving_fumbles": 0,
            "receiving_fumbles_lost": 0,
            "def_tackles_solo": int(row["def_tackles_solo"] or 0),
            "def_tackles_with_assist": int(row["def_tackles_with_assist"] or 0),
            "def_tackle_assists": int(row["def_tackles_with_assist"] or 0),
            "def_tackles_for_loss": 0,
            "def_fumbles_forced": int(row["def_fumbles_forced"] or 0),
            "def_sacks": float(row["def_sacks"] or 0),
            "def_qb_hits": 0,
            "def_interceptions": int(row["def_interceptions"] or 0),
            "def_interception_yards": int(row["def_interception_yards"] or 0),
            "def_pass_defended": int(row["def_pass_defended"] or 0),
            "def_tds": int(row["def_tds"] or 0),
            "def_safeties": 0,
            "punt_returns": int(row["punt_returns"] or 0),
            "punt_return_yards": int(row["punt_return_yards"] or 0),
            "kickoff_returns": int(row["kickoff_returns"] or 0),
            "kickoff_return_yards": int(row["kickoff_return_yards"] or 0),
            "fg_made": int(row["fg_made"] or 0),
            "fg_att": int(row["fg_att"] or 0),
            "fg_long": int(row["fg_long"] or 0),
            "fg_pct": pct_value(float(row["fg_made"] or 0), float(row["fg_att"] or 0)),
            "pat_made": int(row["pat_made"] or 0),
            "pat_att": int(row["pat_att"] or 0),
            "pat_pct": pct_value(float(row["pat_made"] or 0), float(row["pat_att"] or 0)),
            "source": "nfl_gm_sim_engine",
        }
        item["fantasy_points"] = fantasy_ppr(item)
        item["fantasy_points_ppr"] = item["fantasy_points"]
        grouped.setdefault(int(row["player_id"]), []).append(item)
    return grouped


def season_stats_by_player(conn: sqlite3.Connection, player_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not player_ids:
        return {}
    historical = normalized_historical_season_rows(conn, player_ids)
    simulated = normalized_simulated_season_rows(conn, player_ids)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for player_id in player_ids:
        simulated_seasons = {int(row["season"]) for row in simulated.get(player_id, [])}
        rows = [
            row for row in historical.get(player_id, [])
            if int(row["season"]) not in simulated_seasons
        ]
        rows.extend(simulated.get(player_id, []))
        rows.sort(key=lambda row: (-int(row["season"]), str(row.get("stat_team") or "")))
        if rows:
            grouped[player_id] = rows
    return grouped


def career_from_season_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    career: dict[str, Any] = {key: 0 for key in SUM_CAREER_FIELDS}
    seasons = sorted({int(row["season"]) for row in rows if number(row, "games") > 0})
    teams = sorted({str(row.get("stat_team") or "") for row in rows if row.get("stat_team")})
    for row in rows:
        for key in SUM_CAREER_FIELDS:
            career[key] += number(row, key)
    career["player_id"] = None
    career["seasons_played"] = len(seasons)
    career["first_season"] = seasons[0] if seasons else None
    career["last_season"] = seasons[-1] if seasons else None
    career["teams_played_for"] = ",".join(teams)
    career["career_games"] = int(career.pop("games", 0))
    career["scrimmage_yards"] = career["rushing_yards"] + career["receiving_yards"]
    career["offensive_tds"] = career["rushing_tds"] + career["receiving_tds"]
    career["total_tds"] = career["passing_tds"] + career["rushing_tds"] + career["receiving_tds"] + career["def_tds"]
    career["fumbles_lost"] = career["rushing_fumbles_lost"] + career["receiving_fumbles_lost"]
    career["def_tackles_combined"] = career["def_tackles_solo"] + career["def_tackles_with_assist"]
    career["fg_long"] = max((int(number(row, "fg_long")) for row in rows), default=0)
    career["fg_pct"] = pct_value(career["fg_made"], career["fg_att"])
    career["pat_pct"] = pct_value(career["pat_made"], career["pat_att"])
    for key, value in list(career.items()):
        if isinstance(value, float) and value.is_integer():
            career[key] = int(value)
    return career


def career_totals_by_player(season_stats: dict[int, list[dict[str, Any]]], fallback: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    career = dict(fallback)
    for player_id, rows in season_stats.items():
        if rows:
            item = career_from_season_rows(rows)
            item["player_id"] = player_id
            career[player_id] = item
    return career


def contracts_by_player(conn: sqlite3.Connection, player_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM current_contract_years_view
        WHERE player_id IN ({placeholders})
        """,
        player_ids,
    ).fetchall()
    contracts: dict[int, dict[str, Any]] = {}
    contract_ids: list[int] = []
    for row in rows:
        contract_id = int(row["contract_id"])
        contract_ids.append(contract_id)
        contracts[int(row["player_id"])] = {
            "contractId": contract_id,
            "team": row["team"],
            "season": row["season"],
            "yearNumber": row["contract_year_number"],
            "startYear": row["start_year"],
            "endYear": row["end_year"],
            "type": row["contract_type"],
            "baseSalary": money(row["base_salary"]),
            "signingBonusProration": money(row["signing_bonus_proration"]),
            "rosterBonus": money(row["roster_bonus"]),
            "workoutBonus": money(row["workout_bonus"]),
            "guaranteedSalary": money(row["guaranteed_salary"]),
            "capHit": money(row["cap_hit"]),
            "cashDue": money(row["cash_due"]),
            "deadPreJune1": money(row["dead_cap_if_cut_pre_june1"]),
            "deadPostJune1Current": money(row["dead_cap_if_cut_post_june1_current"]),
            "deadPostJune1Next": money(row["dead_cap_if_cut_post_june1_next"]),
            "totalValue": money(row["total_value"]),
            "aav": money(row["aav"]),
            "optionYear": bool(row["is_option_year"]),
            "voidYear": bool(row["is_void_year"]),
            "source": row["source"] or "",
            "notes": row["notes"] or "",
            "years": [],
        }
    if contract_ids:
        contract_placeholders = ",".join("?" for _ in contract_ids)
        year_rows = conn.execute(
            f"""
            SELECT
                cy.contract_id,
                cy.season,
                cy.contract_year_number,
                cy.base_salary,
                cy.signing_bonus_proration,
                cy.roster_bonus,
                cy.workout_bonus,
                cy.option_bonus_proration,
                cy.other_bonus,
                cy.guaranteed_salary,
                cy.cap_hit,
                cy.cash_due,
                cy.dead_cap_if_cut_pre_june1,
                cy.dead_cap_if_cut_post_june1_current,
                cy.dead_cap_if_cut_post_june1_next,
                cy.is_option_year,
                cy.option_exercised,
                cy.is_void_year,
                cy.is_active
            FROM contract_years cy
            WHERE cy.contract_id IN ({contract_placeholders})
            ORDER BY cy.contract_id, cy.season, cy.contract_year_number
            """,
            contract_ids,
        ).fetchall()
        contracts_by_id = {
            int(contract["contractId"]): contract
            for contract in contracts.values()
            if contract.get("contractId") is not None
        }
        for row in year_rows:
            contract = contracts_by_id.get(int(row["contract_id"]))
            if not contract:
                continue
            contract["years"].append({
                "season": row["season"],
                "yearNumber": row["contract_year_number"],
                "baseSalary": money(row["base_salary"]),
                "signingBonusProration": money(row["signing_bonus_proration"]),
                "rosterBonus": money(row["roster_bonus"]),
                "workoutBonus": money(row["workout_bonus"]),
                "optionBonusProration": money(row["option_bonus_proration"]),
                "otherBonus": money(row["other_bonus"]),
                "guaranteedSalary": money(row["guaranteed_salary"]),
                "capHit": money(row["cap_hit"]),
                "cashDue": money(row["cash_due"]),
                "deadPreJune1": money(row["dead_cap_if_cut_pre_june1"]),
                "deadPostJune1Current": money(row["dead_cap_if_cut_post_june1_current"]),
                "deadPostJune1Next": money(row["dead_cap_if_cut_post_june1_next"]),
                "optionYear": bool(row["is_option_year"]),
                "optionExercised": bool(row["option_exercised"]),
                "voidYear": bool(row["is_void_year"]),
                "active": bool(row["is_active"]),
            })
    return contracts


def free_agent_profiles(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    if not table_exists(conn, "free_agent_pool_view"):
        return {}
    rows = conn.execute("SELECT * FROM free_agent_pool_view").fetchall()
    return {
        int(row["player_id"]): {
            "marketTier": row["market_tier"],
            "askingAav": money(row["asking_aav"]),
            "minimumAav": money(row["minimum_aav"]),
            "preferredYears": row["preferred_years"],
            "guaranteePct": row["guarantee_pct"],
            "previousTeam": row["previous_team"],
            "preferredTeams": row["preferred_teams"],
            "hometownTeams": row["hometown_teams"],
            "motivation": row["motivation"] or "",
            "notes": row["signing_notes"] or "",
        }
        for row in rows
    }


def transactions_by_player(conn: sqlite3.Connection, player_ids: list[int], limit_each: int = 14) -> dict[int, list[dict[str, Any]]]:
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM player_transaction_history_view
        WHERE player_id IN ({placeholders})
        ORDER BY player_id, transaction_id DESC
        """,
        player_ids,
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        items = grouped.setdefault(int(row["player_id"]), [])
        if len(items) >= limit_each:
            continue
        items.append({
            "id": int(row["transaction_id"]),
            "date": row["transaction_date"],
            "type": row["transaction_type"],
            "category": row["transaction_category"],
            "team": row["team"],
            "fromTeam": row["from_team"],
            "toTeam": row["to_team"],
            "oldStatus": row["old_status"],
            "newStatus": row["new_status"],
            "capDeltaCurrent": money(row["cap_delta_current"]),
            "cashDelta": money(row["cash_delta"]),
            "description": row["description"] or "",
        })
    return grouped


def medical_by_player(conn: sqlite3.Connection, player_ids: list[int], limit_history: int = 18) -> dict[int, dict[str, Any]]:
    medical = {player_id: {"active": [], "history": [], "bodyRisk": []} for player_id in player_ids}
    if not player_ids:
        return medical
    placeholders = ",".join("?" for _ in player_ids)
    if table_exists(conn, "active_player_injuries"):
        rows = conn.execute(
            f"""
            SELECT *
            FROM active_player_injuries
            WHERE player_id IN ({placeholders})
              AND resolved_at IS NULL
            ORDER BY player_id, return_earliest_date, active_injury_id
            """,
            player_ids,
        ).fetchall()
        for row in rows:
            medical.setdefault(int(row["player_id"]), {"active": [], "history": [], "bodyRisk": []})["active"].append({
                "injury": row["injury_label"],
                "bodyRegion": row["body_region"],
                "bodyPart": row["body_part"],
                "severity": row["severity"],
                "status": row["status"],
                "startDate": row["start_date"],
                "returnEarliestDate": row["return_earliest_date"],
                "expectedDays": int(row["expected_days"] or 0),
                "expectedGames": int(row["expected_games"] or 0),
                "notes": row["notes"] or "",
            })
    if table_exists(conn, "player_injury_history"):
        rows = conn.execute(
            f"""
            SELECT *
            FROM player_injury_history
            WHERE player_id IN ({placeholders})
            ORDER BY player_id, date(start_date) DESC, injury_history_id DESC
            """,
            player_ids,
        ).fetchall()
        counts: dict[int, int] = {}
        for row in rows:
            player_id = int(row["player_id"])
            if counts.get(player_id, 0) >= limit_history:
                continue
            counts[player_id] = counts.get(player_id, 0) + 1
            medical.setdefault(player_id, {"active": [], "history": [], "bodyRisk": []})["history"].append({
                "injury": row["injury_label"],
                "bodyRegion": row["body_region"],
                "bodyPart": row["body_part"],
                "severity": row["severity"],
                "startDate": row["start_date"],
                "resolvedDate": row["resolved_date"],
                "expectedDays": int(row["expected_days"] or 0),
                "gamesMissed": int(row["games_missed"] or 0),
                "recurrenceRisk": round(float(row["recurrence_risk"] or 0.0) * 100, 1),
                "source": row["source"] or "",
                "notes": row["notes"] or "",
            })
    if table_exists(conn, "player_injury_risk_view"):
        rows = conn.execute(
            f"""
            SELECT *
            FROM player_injury_risk_view
            WHERE player_id IN ({placeholders})
            ORDER BY player_id, active_status IS NULL, max_recurrence_risk DESC, games_missed DESC
            """,
            player_ids,
        ).fetchall()
        for row in rows:
            player_id = int(row["player_id"])
            medical.setdefault(player_id, {"active": [], "history": [], "bodyRisk": []})["bodyRisk"].append({
                "bodyRegion": row["body_region"],
                "bodyPart": row["body_part"],
                "injuryCount": int(row["injury_count"] or 0),
                "majorCount": int(row["major_count"] or 0),
                "gamesMissed": int(row["games_missed"] or 0),
                "lastInjuryDate": row["last_injury_date"],
                "recurrenceRisk": round(float(row["max_recurrence_risk"] or 0.0) * 100, 1),
                "activeStatus": row["active_status"],
                "activeReturnDate": row["active_return_date"],
            })
    return medical


def build_summary(player: sqlite3.Row, role: dict[str, Any] | None, ratings: list[dict[str, Any]], career: dict[str, Any] | None) -> str:
    name = f"{player['first_name']} {player['last_name']}".strip()
    position = POSITION_LABELS.get(player["position"], player["position"])
    top_traits = sorted(ratings, key=lambda item: item["value"], reverse=True)[:3]
    trait_text = ", ".join(item["label"].lower() for item in top_traits) or "baseline traits"
    role_text = role["label"].lower() if role else "depth role"
    games = int((career or {}).get("career_games") or 0)
    experience = f"{games} career games" if games else "limited regular-season production"
    return f"{name} profiles as a {role_text} at {position}. The strongest visible indicators are {trait_text}. Current production file shows {experience}, with the rest of the page showing the hard data behind the scouting read."


def build_payload(db_path: Path, season: int, limit: int | None = None, player_id: int | None = None) -> dict[str, Any]:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")

    players_rows = fetch_players(conn, limit, player_id)
    player_ids = [int(row["player_id"]) for row in players_rows]
    teams = team_assets(conn)
    shots = headshots(conn)
    game_id = pro_player_fog.active_game_id(conn)
    player_team_ids = {
        int(row["player_id"]): int(row["team_id"]) if row["team_id"] is not None else None
        for row in players_rows
    }
    evaluations, created_evaluations = pro_player_fog.evaluations_for_players(
        conn,
        game_id=game_id,
        season=season,
        player_team_ids=player_team_ids,
        create_missing=True,
    )
    if created_evaluations:
        conn.commit()
    ratings = ratings_by_player(conn, season, player_ids, evaluations)
    roles = roles_by_player(conn, season, player_ids, evaluations)
    flex = flex_by_player(conn, player_ids)
    season_stats = season_stats_by_player(conn, player_ids)
    career = career_totals_by_player(season_stats, career_by_player(conn, player_ids))
    contracts = contracts_by_player(conn, player_ids)
    free_agents = free_agent_profiles(conn)
    transactions = transactions_by_player(conn, player_ids)
    medical = medical_by_player(conn, player_ids)

    players: list[dict[str, Any]] = []
    for row in players_rows:
        player_id = int(row["player_id"])
        name = f"{row['first_name']} {row['last_name']}".strip()
        player_ratings = ratings.get(player_id, [])
        player_roles = roles.get(player_id, [])
        primary_role = player_roles[0] if player_roles else None
        team = teams.get(int(row["team_id"])) if row["team_id"] is not None else None
        career_row = career.get(player_id, {})
        players.append({
            "id": player_id,
            "name": name,
            "initials": "".join(part[:1] for part in name.split()[:2]).upper(),
            "position": row["position"],
            "positionLabel": POSITION_LABELS.get(row["position"], row["position"]),
            "team": team or {
                "id": None,
                "abbr": "FA",
                "name": "Free Agent",
                "conference": "",
                "division": "",
                "logo": None,
                "primary": "#75808f",
                "secondary": "#d6dde6",
            },
            "headshot": shots.get(player_id),
            "profile": {
                "firstName": row["first_name"],
                "lastName": row["last_name"],
                "age": row["age"] if row["age"] is not None else "--",
                "experience": years_label(row["years_exp"], row["is_rookie"]),
                "college": row["college"] or "--",
                "height": height_label(row["height_in"]),
                "weight": f"{row['weight_lbs']} lbs" if row["weight_lbs"] else "--",
                "jersey": f"#{row['jersey_number']}" if row["jersey_number"] is not None else "--",
                "status": row["status"] or "Active",
                "devTrait": row["dev_trait"] or "Normal",
                "isRookie": bool(row["is_rookie"]),
            },
            "roles": player_roles,
            "ratings": player_ratings,
            "evaluation": evaluations.get(player_id),
            "flex": flex.get(player_id, []),
            "career": career_row,
            "seasonStats": season_stats.get(player_id, []),
            "contract": contracts.get(player_id),
            "freeAgency": free_agents.get(player_id),
            "transactions": transactions.get(player_id, []),
            "medical": medical.get(player_id, {"active": [], "history": [], "bodyRisk": []}),
            "summary": build_summary(row, primary_role, player_ratings, career_row),
        })

    payload = {
        "season": season,
        "ratingGroups": [
            {"key": key, "label": GROUP_LABELS[key], "order": GROUP_ORDER[key]}
            for key in sorted(GROUP_LABELS, key=lambda group: GROUP_ORDER[group])
        ],
        "players": players,
    }
    conn.close()
    return payload


def export(db_path: Path, output_path: Path, season: int, limit: int | None = None) -> int:
    payload = build_payload(db_path, season, limit)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "/* Generated by tools/export_player_profile_ui_data.py. */\n"
        "window.PLAYER_PROFILE_DATA = "
        + json.dumps(payload, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    return len(payload.get("players", []))


def main() -> None:
    parser = argparse.ArgumentParser(description="Export static data for the player profile UI.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to nfl_gm.db")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JS file")
    parser.add_argument("--season", type=int, default=CURRENT_SEASON, help="Ratings/stat season")
    parser.add_argument("--limit", type=int, default=None, help="Optional player limit for quick previews")
    args = parser.parse_args()

    count = export(Path(args.db), Path(args.output), args.season, args.limit)
    print(f"Exported {count} player profiles to {Path(args.output)}")


if __name__ == "__main__":
    main()
