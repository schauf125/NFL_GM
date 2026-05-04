#!/usr/bin/env python3
"""Print specialist and special-teams behavior profiles used by the sim engine."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.match_engine import DEFAULT_SEASON, PlayerSnapshot  # noqa: E402
from engine.specialist_behavior import (  # noqa: E402
    player_specialist_behavior_table_exists,
    profile_from_mapping,
    specialist_behavior_profile,
    specialist_behavior_source,
)


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def player_snapshot(con: sqlite3.Connection, row: sqlite3.Row, season: int) -> PlayerSnapshot:
    ratings = {
        rating["rating_key"]: int(rating["rating_value"])
        for rating in con.execute(
            """
            SELECT rating_key, rating_value
            FROM player_ratings
            WHERE player_id = ?
              AND season = ?
            """,
            (row["player_id"], season),
        )
    }
    role_scores = {
        role["role_key"]: float(role["role_score"])
        for role in con.execute(
            """
            SELECT role_key, role_score
            FROM player_role_scores
            WHERE player_id = ?
              AND season = ?
            """,
            (row["player_id"], season),
        )
    }
    metadata = {}
    if player_specialist_behavior_table_exists(con):
        profile_row = con.execute(
            """
            SELECT *
            FROM player_specialist_behavior_profiles
            WHERE player_id = ?
              AND season = ?
            """,
            (row["player_id"], season),
        ).fetchone()
        if profile_row:
            metadata["specialist_behavior_profile"] = profile_from_mapping(dict(profile_row))
            metadata["specialist_behavior_source"] = str(profile_row["source"])
    return PlayerSnapshot(
        player_id=int(row["player_id"]),
        name=str(row["name"]),
        position=str(row["position"]),
        ratings=ratings,
        role_scores=role_scores,
        metadata=metadata,
    )


def load_profiles(con: sqlite3.Connection, *, season: int, team: str | None, all_players: bool) -> list[sqlite3.Row]:
    params: list[Any] = [season]
    team_filter = ""
    if team:
        team_filter = "AND t.abbreviation = ?"
        params.append(team.upper())
    profile_filter = ""
    if not all_players:
        profile_filter = """
        AND (
            p.position IN ('K', 'P', 'LS')
            OR EXISTS (
                SELECT 1
                FROM player_specialist_behavior_profiles sbp
                WHERE sbp.player_id = p.player_id
                  AND sbp.season = ?
            )
        )
        """
        params.append(season)
    return list(
        con.execute(
            f"""
            SELECT p.player_id,
                   p.first_name || ' ' || p.last_name AS name,
                   p.position,
                   p.overall,
                   COALESCE(t.abbreviation, 'FA') AS team
            FROM players p
            LEFT JOIN teams t ON t.team_id = p.team_id
            WHERE EXISTS (
                  SELECT 1
                  FROM player_ratings pr
                  WHERE pr.player_id = p.player_id
                    AND pr.season = ?
              )
              {team_filter}
              {profile_filter}
            ORDER BY CASE WHEN t.abbreviation IS NULL THEN 1 ELSE 0 END,
                     t.abbreviation,
                     CASE p.position WHEN 'K' THEN 1 WHEN 'P' THEN 2 WHEN 'LS' THEN 3 ELSE 4 END,
                     p.overall DESC,
                     p.player_id
            """,
            params,
        )
    )


def row_payload(con: sqlite3.Connection, row: sqlite3.Row, season: int) -> dict[str, Any]:
    player = player_snapshot(con, row, season)
    profile = specialist_behavior_profile(player)
    payload = {
        "team": row["team"],
        "name": row["name"],
        "position": row["position"],
        "overall": row["overall"],
        "style": profile.label,
        "source": specialist_behavior_source(player),
    }
    payload.update(profile.as_dict())
    return payload


def draft_rows(con: sqlite3.Connection, draft_year: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in con.execute(
            """
            SELECT dp.position,
                   dp.first_name || ' ' || dp.last_name AS name,
                   dp.college,
                   dp.archetype,
                   dp.overall,
                   sbp.*
            FROM draft_prospects dp
            JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
            JOIN draft_prospect_specialist_behavior_profiles sbp ON sbp.prospect_id = dp.prospect_id
            WHERE dc.draft_year = ?
            ORDER BY dp.true_rank, dp.prospect_id
            """,
            (draft_year,),
        )
    ]


def print_rows(rows: list[dict[str, Any]]) -> None:
    print(
        f"{'Tm':<4} {'Pos':<4} {'Player':<24} {'OVR':>3} {'Src':<8} {'Style':<28} "
        f"{'KOp':>3} {'KOf':>3} {'Han':>3} {'Plc':>3} {'Snp':>3} {'Rel':>3} "
        f"{'Gun':>3} {'Ret':>3} {'Blk':>3} {'Tkl':>3} {'Pen':>3}"
    )
    for row in rows:
        print(
            f"{row['team']:<4} {row['position']:<4} {row['name']:<24} {int(row['overall']):>3} {row['source']:<8} "
            f"{row['style']:<28} {row['kick_operation']:>3.0f} {row['kickoff_control']:>3.0f} "
            f"{row['punt_hang_time']:>3.0f} {row['punt_placement']:>3.0f} {row['snap_accuracy']:>3.0f} "
            f"{row['lane_release']:>3.0f} {row['gunner_speed']:>3.0f} {row['return_lane_vision']:>3.0f} "
            f"{row['block_timing']:>3.0f} {row['coverage_tackle']:>3.0f} {row['penalty_control']:>3.0f}"
        )


def print_draft_rows(rows: list[dict[str, Any]], limit: int) -> None:
    print(
        f"{'Pos':<4} {'Prospect':<24} {'OVR':>3} {'Archetype':<20} {'Style':<28} "
        f"{'KOp':>3} {'KOf':>3} {'Han':>3} {'Plc':>3} {'Snp':>3} {'Rel':>3} "
        f"{'Gun':>3} {'Ret':>3} {'Blk':>3} {'Tkl':>3} {'Pen':>3}"
    )
    for row in rows[:limit]:
        print(
            f"{row['position']:<4} {row['name']:<24} {int(row['overall']):>3} {str(row['archetype'] or ''):<20} "
            f"{row['label']:<28} {int(row['kick_operation']):>3} {int(row['kickoff_control']):>3} "
            f"{int(row['punt_hang_time']):>3} {int(row['punt_placement']):>3} {int(row['snap_accuracy']):>3} "
            f"{int(row['lane_release']):>3} {int(row['gunner_speed']):>3} {int(row['return_lane_vision']):>3} "
            f"{int(row['block_timing']):>3} {int(row['coverage_tackle']):>3} {int(row['penalty_control']):>3}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show specialist behavior profiles used by the sim engine.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    parser.add_argument("--team", help="Filter by team abbreviation.")
    parser.add_argument("--all", action="store_true", help="Show all players with inferred special-teams profiles.")
    parser.add_argument("--draft-year", type=int, help="Show generated draft special-teams profiles for this class.")
    parser.add_argument("--limit", type=int, default=80, help="Rows to print for draft reports.")
    parser.add_argument("--json", type=Path, help="Write full payload to JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with connect(args.db) as con:
        if args.draft_year:
            rows = draft_rows(con, args.draft_year)
            print_draft_rows(rows, args.limit)
        else:
            rows = [
                row_payload(con, row, args.season)
                for row in load_profiles(con, season=args.season, team=args.team, all_players=args.all)
            ]
            print_rows(rows)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nWrote JSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
