#!/usr/bin/env python3
"""Seed stored specialist and special-teams behavior profiles."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.draft.repository import (  # noqa: E402
    DraftProspectSpecialistBehaviorProfile,
    replace_prospect_specialist_behavior_profile,
)
from engine.draft.schema import ensure_schema as ensure_draft_schema  # noqa: E402
from engine.match_engine import DEFAULT_SEASON, PlayerSnapshot  # noqa: E402
from engine.specialist_behavior import (  # noqa: E402
    SPECIALIST_POSITIONS,
    SPECIALIST_STYLE_OVERRIDES,
    ensure_player_specialist_behavior_schema,
    generated_specialist_behavior_profile,
    normalize_name,
    profile_to_db_tuple,
    specialist_behavior_profile,
)


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def load_players(con: sqlite3.Connection, season: int) -> list[sqlite3.Row]:
    return list(
        con.execute(
            """
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
            ORDER BY CASE WHEN t.abbreviation IS NULL THEN 1 ELSE 0 END,
                     t.abbreviation,
                     CASE p.position WHEN 'K' THEN 1 WHEN 'P' THEN 2 WHEN 'LS' THEN 3 ELSE 4 END,
                     p.overall DESC,
                     p.player_id
            """,
            (season,),
        )
    )


def should_seed_player(row: sqlite3.Row) -> bool:
    return str(row["position"]).upper() in SPECIALIST_POSITIONS or normalize_name(str(row["name"])) in SPECIALIST_STYLE_OVERRIDES


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
    return PlayerSnapshot(
        player_id=int(row["player_id"]),
        name=str(row["name"]),
        position=str(row["position"]),
        ratings=ratings,
        role_scores=role_scores,
    )


def seed_player_profiles(con: sqlite3.Connection, *, season: int, apply: bool) -> Counter[str]:
    ensure_player_specialist_behavior_schema(con)
    counts: Counter[str] = Counter()
    for row in load_players(con, season):
        if not should_seed_player(row):
            continue
        player = player_snapshot(con, row, season)
        profile = specialist_behavior_profile(player)
        source_kind = "named" if normalize_name(player.name) in SPECIALIST_STYLE_OVERRIDES else "inferred"
        source = f"specialist_behavior_{source_kind}_seed"
        counts[source_kind] += 1
        if apply:
            con.execute(
                """
                INSERT INTO player_specialist_behavior_profiles (
                    player_id, season, label, kick_operation, kickoff_control,
                    punt_hang_time, punt_placement, snap_accuracy,
                    lane_release, gunner_speed, return_lane_vision,
                    block_timing, coverage_tackle, penalty_control,
                    source, notes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(player_id, season) DO UPDATE SET
                    label = excluded.label,
                    kick_operation = excluded.kick_operation,
                    kickoff_control = excluded.kickoff_control,
                    punt_hang_time = excluded.punt_hang_time,
                    punt_placement = excluded.punt_placement,
                    snap_accuracy = excluded.snap_accuracy,
                    lane_release = excluded.lane_release,
                    gunner_speed = excluded.gunner_speed,
                    return_lane_vision = excluded.return_lane_vision,
                    block_timing = excluded.block_timing,
                    coverage_tackle = excluded.coverage_tackle,
                    penalty_control = excluded.penalty_control,
                    source = excluded.source,
                    notes = excluded.notes,
                    updated_at = datetime('now')
                """,
                profile_to_db_tuple(player.player_id, season, profile, source),
            )
    return counts


def latest_draft_year(con: sqlite3.Connection) -> int | None:
    row = con.execute("SELECT MAX(draft_year) AS draft_year FROM draft_classes").fetchone()
    return int(row["draft_year"]) if row and row["draft_year"] is not None else None


def draft_ratings(con: sqlite3.Connection, prospect_id: int) -> dict[str, int]:
    return {
        row["rating_key"]: int(row["rating_value"])
        for row in con.execute(
            """
            SELECT rating_key, rating_value
            FROM draft_prospect_ratings
            WHERE prospect_id = ?
            """,
            (prospect_id,),
        )
    }


def seed_draft_profiles(
    con: sqlite3.Connection,
    *,
    draft_year: int | None,
    apply: bool,
) -> Counter[str]:
    ensure_draft_schema(con)
    counts: Counter[str] = Counter()
    if draft_year is None:
        draft_year = latest_draft_year(con)
    if draft_year is None:
        return counts
    rows = con.execute(
        """
        SELECT dp.prospect_id,
               dp.position,
               dp.archetype,
               dp.first_name || ' ' || dp.last_name AS name
        FROM draft_prospects dp
        JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
        WHERE dc.draft_year = ?
          AND EXISTS (
              SELECT 1
              FROM draft_prospect_ratings dpr
              WHERE dpr.prospect_id = dp.prospect_id
          )
        ORDER BY dp.true_rank, dp.prospect_id
        """,
        (draft_year,),
    ).fetchall()
    for row in rows:
        profile = generated_specialist_behavior_profile(
            str(row["archetype"] or ""),
            draft_ratings(con, int(row["prospect_id"])),
            position=str(row["position"] or ""),
        )
        counts[str(row["position"])] += 1
        if apply:
            replace_prospect_specialist_behavior_profile(
                con,
                int(row["prospect_id"]),
                DraftProspectSpecialistBehaviorProfile(
                    label=profile.label,
                    kick_operation=int(round(profile.kick_operation)),
                    kickoff_control=int(round(profile.kickoff_control)),
                    punt_hang_time=int(round(profile.punt_hang_time)),
                    punt_placement=int(round(profile.punt_placement)),
                    snap_accuracy=int(round(profile.snap_accuracy)),
                    lane_release=int(round(profile.lane_release)),
                    gunner_speed=int(round(profile.gunner_speed)),
                    return_lane_vision=int(round(profile.return_lane_vision)),
                    block_timing=int(round(profile.block_timing)),
                    coverage_tackle=int(round(profile.coverage_tackle)),
                    penalty_control=int(round(profile.penalty_control)),
                    notes=profile.notes,
                ),
                ensure=False,
            )
    if rows:
        counts[f"draft_year_{draft_year}"] = len(rows)
    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    parser.add_argument("--draft-year", type=int, help="Draft class year to seed. Default is latest class.")
    parser.add_argument("--no-draft", action="store_true", help="Only seed current player profiles.")
    parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with connect(args.db) as con:
        player_counts = seed_player_profiles(con, season=args.season, apply=args.apply)
        draft_counts = Counter()
        if not args.no_draft:
            draft_counts = seed_draft_profiles(con, draft_year=args.draft_year, apply=args.apply)
        if args.apply:
            con.commit()
        else:
            con.rollback()
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"Season: {args.season}")
    print(f"Player profiles: {sum(player_counts.values())}")
    for source, count in sorted(player_counts.items()):
        print(f"{source}: {count}")
    print(f"Draft profiles: {sum(count for key, count in draft_counts.items() if not key.startswith('draft_year_'))}")
    for source, count in sorted(draft_counts.items()):
        print(f"{source}: {count}")
    if not args.apply:
        print("Dry run only. Add --apply to write specialist behavior profiles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
