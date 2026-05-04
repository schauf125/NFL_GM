#!/usr/bin/env python3
"""Print LB behavior profiles used by the sim engine."""

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
from engine.lb_behavior import (  # noqa: E402
    player_lb_behavior_table_exists,
    profile_from_mapping,
    lb_behavior_profile,
    lb_behavior_source,
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
    if player_lb_behavior_table_exists(con):
        profile_row = con.execute(
            """
            SELECT *
            FROM player_lb_behavior_profiles
            WHERE player_id = ?
              AND season = ?
            """,
            (row["player_id"], season),
        ).fetchone()
        if profile_row:
            metadata["lb_behavior_profile"] = profile_from_mapping(dict(profile_row))
            metadata["lb_behavior_source"] = str(profile_row["source"])
    return PlayerSnapshot(
        player_id=int(row["player_id"]),
        name=str(row["name"]),
        position=str(row["position"]),
        ratings=ratings,
        role_scores=role_scores,
        metadata=metadata,
    )


def load_linebackers(con: sqlite3.Connection, *, season: int, team: str | None, starters_only: bool) -> list[sqlite3.Row]:
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
              AND dc.position IN ('MLB', 'WLB', 'SLB')
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
            WHERE p.position IN ('ILB', 'LB', 'OLB')
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
                     CASE p.position WHEN 'ILB' THEN 1 WHEN 'LB' THEN 2 ELSE 3 END,
                     p.overall DESC,
                     p.player_id
            """,
            params,
        )
    )


def row_payload(con: sqlite3.Connection, row: sqlite3.Row, season: int) -> dict[str, Any]:
    linebacker = player_snapshot(con, row, season)
    profile = lb_behavior_profile(linebacker)
    payload = {
        "team": row["team"],
        "name": row["name"],
        "position": row["position"],
        "overall": row["overall"],
        "style": profile.label,
        "source": lb_behavior_source(linebacker),
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
                   lbp.*
            FROM draft_prospects dp
            JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
            JOIN draft_prospect_lb_behavior_profiles lbp ON lbp.prospect_id = dp.prospect_id
            WHERE dc.draft_year = ?
              AND dp.position IN ('ILB', 'LB', 'OLB')
            ORDER BY dp.position, dp.true_rank, dp.prospect_id
            """,
            (draft_year,),
        )
    ]


def print_rows(rows: list[dict[str, Any]]) -> None:
    print(f"{'Tm':<4} {'Pos':<3} {'Linebacker':<24} {'OVR':>3} {'Src':<8} {'Style':<28} {'Trg':>3} {'Gap':>3} {'Scr':>3} {'Tra':>3} {'Zon':>3} {'Man':>3} {'Blz':>3} {'Tkl':>3} {'Ral':>3} {'Pen':>3}")
    for row in rows:
        print(
            f"{row['team']:<4} {row['position']:<3} {row['name']:<24} {int(row['overall']):>3} {row['source']:<8} "
            f"{row['style']:<28} {row['trigger_quickness']:>3.0f} {row['gap_fit_discipline']:>3.0f} "
            f"{row['scrape_range']:>3.0f} {row['traffic_navigation']:>3.0f} "
            f"{row['zone_landmark_depth']:>3.0f} {row['man_match_carry']:>3.0f} "
            f"{row['blitz_timing']:>3.0f} {row['tackle_finish']:>3.0f} "
            f"{row['rally_support']:>3.0f} {row['penalty_control']:>3.0f}"
        )


def print_draft_rows(rows: list[dict[str, Any]]) -> None:
    print(f"{'Pos':<3} {'Prospect':<24} {'OVR':>3} {'Archetype':<18} {'Style':<28} {'Trg':>3} {'Gap':>3} {'Scr':>3} {'Tra':>3} {'Zon':>3} {'Man':>3} {'Blz':>3} {'Tkl':>3} {'Ral':>3} {'Pen':>3}")
    for row in rows:
        print(
            f"{row['position']:<3} {row['name']:<24} {int(row['overall']):>3} {str(row['archetype'] or ''):<18} "
            f"{row['label']:<28} {int(row['trigger_quickness']):>3} {int(row['gap_fit_discipline']):>3} "
            f"{int(row['scrape_range']):>3} {int(row['traffic_navigation']):>3} "
            f"{int(row['zone_landmark_depth']):>3} {int(row['man_match_carry']):>3} "
            f"{int(row['blitz_timing']):>3} {int(row['tackle_finish']):>3} "
            f"{int(row['rally_support']):>3} {int(row['penalty_control']):>3}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Show LB behavior profiles used by the sim engine.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    parser.add_argument("--team", help="Filter by team abbreviation.")
    parser.add_argument("--all", action="store_true", help="Show all linebackers. Default is listed starters only.")
    parser.add_argument("--draft-year", type=int, help="Show generated draft LB profiles for this class.")
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
                for row in load_linebackers(con, season=args.season, team=args.team, starters_only=not args.all)
            ]
            print_rows(rows)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nWrote JSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
