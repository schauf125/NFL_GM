#!/usr/bin/env python3
"""Seed special-teams flex ratings for players and draft prospects."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import special_teams_flex  # noqa: E402
from engine.draft.schema import ensure_schema as ensure_draft_schema  # noqa: E402


SOURCE = "special_teams_flex_seed"
PLAYER_ROLE_KEYS = set(special_teams_flex.SPECIAL_TEAMS_FLEX_ROLES)


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone() is not None


def current_season(con: sqlite3.Connection) -> int:
    if table_exists(con, "game_settings"):
        row = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key IN ('current_season', 'current_league_year') ORDER BY setting_key LIMIT 1"
        ).fetchone()
        if row and row["setting_value"]:
            try:
                return int(row["setting_value"])
            except ValueError:
                pass
    row = con.execute(
        """
        SELECT MAX(season) AS season
        FROM player_role_scores
        WHERE scheme_key = 'default'
        """
    ).fetchone() if table_exists(con, "player_role_scores") else None
    return int(row["season"] or 2026) if row else 2026


def player_rating_maps(con: sqlite3.Connection, season: int) -> dict[int, dict[str, Any]]:
    ratings: dict[int, dict[str, Any]] = {}
    if table_exists(con, "player_ratings"):
        rows = con.execute(
            """
            SELECT player_id, rating_key, rating_value
            FROM player_ratings
            WHERE season = ?
            """,
            (season,),
        ).fetchall()
        for row in rows:
            ratings.setdefault(int(row["player_id"]), {})[str(row["rating_key"])] = int(row["rating_value"])
    return ratings


def player_role_score_maps(con: sqlite3.Connection, season: int) -> dict[int, dict[str, Any]]:
    roles: dict[int, dict[str, Any]] = {}
    if not table_exists(con, "player_role_scores"):
        return roles
    rows = con.execute(
        """
        SELECT player_id, role_key, role_score
        FROM player_role_scores
        WHERE season = ? AND scheme_key = 'default'
        """,
        (season,),
    ).fetchall()
    for row in rows:
        roles.setdefault(int(row["player_id"]), {})[str(row["role_key"])] = float(row["role_score"])
    return roles


def player_specialist_profiles(con: sqlite3.Connection, season: int) -> dict[int, dict[str, Any]]:
    profiles: dict[int, dict[str, Any]] = {}
    if not table_exists(con, "player_specialist_behavior_profiles"):
        return profiles
    rows = con.execute(
        """
        SELECT *
        FROM player_specialist_behavior_profiles
        WHERE season = ?
        """,
        (season,),
    ).fetchall()
    for row in rows:
        profiles[int(row["player_id"])] = dict(row)
    return profiles


def player_return_stats(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    stats: dict[int, dict[str, Any]] = {}
    if table_exists(con, "player_season_stats"):
        rows = con.execute(
            """
            SELECT
                player_id,
                COALESCE(SUM(punt_returns), 0) AS punt_returns,
                COALESCE(SUM(punt_return_yards), 0) AS punt_return_yards,
                COALESCE(SUM(kickoff_returns), 0) AS kickoff_returns,
                COALESCE(SUM(kickoff_return_yards), 0) AS kickoff_return_yards
            FROM player_season_stats
            GROUP BY player_id
            """
        ).fetchall()
        for row in rows:
            stats[int(row["player_id"])] = dict(row)
    if table_exists(con, "season_player_stats"):
        rows = con.execute(
            """
            SELECT
                player_id,
                SUM(CASE WHEN stat_key = 'punt_returns' THEN stat_value ELSE 0 END) AS punt_returns,
                SUM(CASE WHEN stat_key = 'punt_return_yards' THEN stat_value ELSE 0 END) AS punt_return_yards,
                SUM(CASE WHEN stat_key = 'kickoff_returns' THEN stat_value ELSE 0 END) AS kickoff_returns,
                SUM(CASE WHEN stat_key = 'kickoff_return_yards' THEN stat_value ELSE 0 END) AS kickoff_return_yards
            FROM season_player_stats
            GROUP BY player_id
            """
        ).fetchall()
        for row in rows:
            item = stats.setdefault(int(row["player_id"]), {})
            for key in ("punt_returns", "punt_return_yards", "kickoff_returns", "kickoff_return_yards"):
                item[key] = float(item.get(key) or 0) + float(row[key] or 0)
    return stats


def seed_player_flex(con: sqlite3.Connection, *, season: int | None = None, apply: bool = False) -> int:
    target_season = int(season or current_season(con))
    ratings_by_player = player_rating_maps(con, target_season)
    roles_by_player = player_role_score_maps(con, target_season)
    specialist_by_player = player_specialist_profiles(con, target_season)
    stats_by_player = player_return_stats(con)
    players = con.execute(
        """
        SELECT
            player_id,
            first_name || ' ' || last_name AS player_name,
            position,
            age,
            years_exp,
            overall,
            potential,
            is_rookie,
            speed,
            strength,
            agility,
            awareness
        FROM players
        WHERE COALESCE(status, 'Active') != 'Retired'
        """
    ).fetchall()
    rows: list[tuple[int, str, int, int, str]] = []
    for player in players:
        player_id = int(player["player_id"])
        ratings = dict(ratings_by_player.get(player_id, {}))
        for key in ("speed", "strength", "agility", "awareness"):
            if key not in ratings and player[key] is not None:
                ratings[key] = int(player[key])
        grades = special_teams_flex.special_teams_flex_for_profile(
            position=str(player["position"] or ""),
            ratings=ratings,
            specialist_profile=specialist_by_player.get(player_id, {}),
            role_scores=roles_by_player.get(player_id, {}),
            overall=player["overall"],
            potential_overall=player["potential"],
            age=int(player["age"] or 0) or None,
            years_exp=int(player["years_exp"] or 0),
            is_rookie=bool(player["is_rookie"]),
            stats=stats_by_player.get(player_id, {}),
            seed_key=f"player:{player_id}:{target_season}:special-teams-flex",
        )
        for role, grade in grades.items():
            rows.append((player_id, role, grade.current, grade.potential, grade.notes))
    if apply:
        placeholders = ",".join("?" for _ in PLAYER_ROLE_KEYS)
        con.execute(
            f"DELETE FROM player_position_flex WHERE position IN ({placeholders})",
            tuple(sorted(PLAYER_ROLE_KEYS)),
        )
        con.executemany(
            """
            INSERT INTO player_position_flex (
                player_id, position, experience, potential, is_primary, source, notes
            )
            VALUES (?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(player_id, position) DO UPDATE SET
                experience = excluded.experience,
                potential = excluded.potential,
                is_primary = CASE WHEN player_position_flex.is_primary = 1 THEN 1 ELSE 0 END,
                source = excluded.source,
                notes = excluded.notes
            """,
            [(player_id, role, current, potential, SOURCE, notes) for player_id, role, current, potential, notes in rows],
        )
    return len(rows)


def draft_rating_maps(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    ratings: dict[int, dict[str, Any]] = {}
    if not table_exists(con, "draft_prospect_ratings"):
        return ratings
    rows = con.execute("SELECT prospect_id, rating_key, rating_value FROM draft_prospect_ratings").fetchall()
    for row in rows:
        ratings.setdefault(int(row["prospect_id"]), {})[str(row["rating_key"])] = int(row["rating_value"])
    return ratings


def draft_role_score_maps(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    roles: dict[int, dict[str, Any]] = {}
    if not table_exists(con, "draft_prospect_role_scores"):
        return roles
    rows = con.execute("SELECT prospect_id, role_key, role_score FROM draft_prospect_role_scores").fetchall()
    for row in rows:
        roles.setdefault(int(row["prospect_id"]), {})[str(row["role_key"])] = float(row["role_score"])
    return roles


def draft_specialist_profiles(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    profiles: dict[int, dict[str, Any]] = {}
    if not table_exists(con, "draft_prospect_specialist_behavior_profiles"):
        return profiles
    rows = con.execute("SELECT * FROM draft_prospect_specialist_behavior_profiles").fetchall()
    for row in rows:
        profiles[int(row["prospect_id"])] = dict(row)
    return profiles


def seed_draft_prospect_flex(con: sqlite3.Connection, *, apply: bool = False) -> int:
    ensure_draft_schema(con)
    if not table_exists(con, "draft_prospects"):
        return 0
    ratings_by_prospect = draft_rating_maps(con)
    roles_by_prospect = draft_role_score_maps(con)
    specialist_by_prospect = draft_specialist_profiles(con)
    prospects = con.execute(
        """
        SELECT prospect_id, draft_class_id, first_name || ' ' || last_name AS player_name,
               position, age, true_grade, ceiling_grade, overall, potential,
               COALESCE(public_board_rank, scouting_rank, true_rank) AS draft_rank,
               college_tier,
               COALESCE(discovery_status, public_board_status) AS discovery_profile
        FROM draft_prospects
        WHERE COALESCE(status, 'Available') IN ('Available', 'Drafted', 'Archived')
        """
    ).fetchall()
    rows: list[tuple[int, str, int, int, str]] = []
    for prospect in prospects:
        prospect_id = int(prospect["prospect_id"])
        grades = special_teams_flex.special_teams_flex_for_profile(
            position=str(prospect["position"] or ""),
            ratings=ratings_by_prospect.get(prospect_id, {}),
            specialist_profile=specialist_by_prospect.get(prospect_id, {}),
            role_scores=roles_by_prospect.get(prospect_id, {}),
            overall=prospect["true_grade"] or prospect["overall"],
            potential_overall=prospect["ceiling_grade"] or prospect["potential"],
            age=int(prospect["age"] or 0) or None,
            is_rookie=True,
            draft_rank=prospect["draft_rank"],
            college_tier=prospect["college_tier"],
            discovery_profile=prospect["discovery_profile"],
            seed_key=f"prospect:{prospect_id}:{prospect['draft_class_id']}:special-teams-flex",
        )
        for role, grade in grades.items():
            rows.append((prospect_id, role, grade.current, grade.potential, grade.notes))
    if apply:
        con.execute("DELETE FROM draft_prospect_special_teams_flex")
        con.executemany(
            """
            INSERT INTO draft_prospect_special_teams_flex (
                prospect_id, role_key, experience, potential, source, notes, updated_at
            )
            VALUES (?, ?, ?, ?, 'draft_generator', ?, datetime('now'))
            ON CONFLICT(prospect_id, role_key) DO UPDATE SET
                experience = excluded.experience,
                potential = excluded.potential,
                source = excluded.source,
                notes = excluded.notes,
                updated_at = datetime('now')
            """,
            rows,
        )
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed special-teams flex roles.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--season", type=int)
    parser.add_argument("--players-only", action="store_true")
    parser.add_argument("--draft-only", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    with connect(args.db) as con:
        player_rows = 0 if args.draft_only else seed_player_flex(con, season=args.season, apply=args.apply)
        prospect_rows = 0 if args.players_only else seed_draft_prospect_flex(con, apply=args.apply)
        if args.apply:
            con.commit()
    print(f"Player special-teams flex rows: {player_rows}")
    print(f"Draft prospect special-teams flex rows: {prospect_rows}")
    if not args.apply:
        print("Dry run only. Add --apply to write changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
