#!/usr/bin/env python3
"""Print WR/TE behavior profiles used by the sim engine."""

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
from engine.receiver_behavior import (  # noqa: E402
    player_receiver_behavior_table_exists,
    profile_from_mapping,
    receiver_behavior_profile,
    receiver_behavior_source,
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
    if player_receiver_behavior_table_exists(con):
        profile_row = con.execute(
            """
            SELECT *
            FROM player_receiver_behavior_profiles
            WHERE player_id = ?
              AND season = ?
            """,
            (row["player_id"], season),
        ).fetchone()
        if profile_row:
            metadata["receiver_behavior_profile"] = profile_from_mapping(dict(profile_row))
            metadata["receiver_behavior_source"] = str(profile_row["source"])
    return PlayerSnapshot(
        player_id=int(row["player_id"]),
        name=str(row["name"]),
        position=str(row["position"]),
        ratings=ratings,
        role_scores=role_scores,
        metadata=metadata,
    )


def load_receivers(con: sqlite3.Connection, *, season: int, team: str | None, starters_only: bool) -> list[sqlite3.Row]:
    params: list[Any] = [season]
    team_filter = ""
    if team:
        team_filter = "AND t.abbreviation = ?"
        params.append(team.upper())
    starter_filter = ""
    if starters_only:
        starter_filter = """
        AND EXISTS (
            SELECT 1
            FROM depth_charts dc
            WHERE dc.player_id = p.player_id
              AND dc.position IN ('LWR', 'RWR', 'SWR', 'TE')
              AND dc.depth_rank = 1
        )
        """
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
            WHERE p.position IN ('WR', 'TE')
              AND EXISTS (
                  SELECT 1
                  FROM player_ratings pr
                  WHERE pr.player_id = p.player_id
                    AND pr.season = ?
              )
              {team_filter}
              {starter_filter}
            ORDER BY CASE WHEN t.abbreviation IS NULL THEN 1 ELSE 0 END,
                     t.abbreviation,
                     CASE p.position WHEN 'WR' THEN 1 ELSE 2 END,
                     p.overall DESC,
                     p.player_id
            """,
            params,
        )
    )


def row_payload(con: sqlite3.Connection, row: sqlite3.Row, season: int) -> dict[str, Any]:
    receiver = player_snapshot(con, row, season)
    profile = receiver_behavior_profile(receiver)
    payload = {
        "team": row["team"],
        "name": row["name"],
        "position": row["position"],
        "overall": row["overall"],
        "style": profile.label,
        "source": receiver_behavior_source(receiver),
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
                   rbp.*
            FROM draft_prospects dp
            JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
            JOIN draft_prospect_receiver_behavior_profiles rbp ON rbp.prospect_id = dp.prospect_id
            WHERE dc.draft_year = ?
              AND dp.position IN ('WR', 'TE')
            ORDER BY dp.position, dp.true_rank, dp.prospect_id
            """,
            (draft_year,),
        )
    ]


def print_rows(rows: list[dict[str, Any]]) -> None:
    print(f"{'Tm':<4} {'Pos':<3} {'Receiver':<24} {'OVR':>3} {'Src':<8} {'Style':<28} {'Tgt':>3} {'Rel':>3} {'Rte':>3} {'Vrt':>3} {'Mid':>3} {'Cnt':>3} {'YAC':>3} {'Sec':>3}")
    for row in rows:
        print(
            f"{row['team']:<4} {row['position']:<3} {row['name']:<24} {int(row['overall']):>3} {row['source']:<8} "
            f"{row['style']:<28} {row['target_gravity']:>3.0f} {row['release_urgency']:>3.0f} "
            f"{row['route_pacing']:>3.0f} {row['vertical_intent']:>3.0f} {row['middle_comfort']:>3.0f} "
            f"{row['contested_alpha']:>3.0f} {row['yac_intent']:>3.0f} {row['catch_security']:>3.0f}"
        )


def print_draft_rows(rows: list[dict[str, Any]]) -> None:
    print(f"{'Pos':<3} {'Prospect':<24} {'OVR':>3} {'Archetype':<22} {'Style':<28} {'Tgt':>3} {'Rel':>3} {'Rte':>3} {'Vrt':>3} {'Mid':>3} {'Cnt':>3} {'YAC':>3} {'Sec':>3}")
    for row in rows:
        print(
            f"{row['position']:<3} {row['name']:<24} {int(row['overall']):>3} {str(row['archetype'] or ''):<22} "
            f"{row['label']:<28} {int(row['target_gravity']):>3} {int(row['release_urgency']):>3} "
            f"{int(row['route_pacing']):>3} {int(row['vertical_intent']):>3} {int(row['middle_comfort']):>3} "
            f"{int(row['contested_alpha']):>3} {int(row['yac_intent']):>3} {int(row['catch_security']):>3}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show WR/TE behavior profiles used by the sim engine.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    parser.add_argument("--team", help="Filter by team abbreviation.")
    parser.add_argument("--all", action="store_true", help="Show all receivers. Default is listed starters only.")
    parser.add_argument("--draft-year", type=int, help="Show generated draft WR/TE profiles for this class.")
    parser.add_argument("--json", type=Path, help="Write full payload to JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with connect(args.db) as con:
        if args.draft_year:
            rows = draft_rows(con, args.draft_year)
            print_draft_rows(rows)
        else:
            rows = [
                row_payload(con, row, args.season)
                for row in load_receivers(con, season=args.season, team=args.team, starters_only=not args.all)
            ]
            print_rows(rows)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nWrote JSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
