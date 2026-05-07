#!/usr/bin/env python3
"""Seed stored secondary behavior profiles for current players and draft prospects."""

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
    DraftProspectSecondaryBehaviorProfile,
    replace_prospect_secondary_behavior_profile,
)
from engine.draft.schema import ensure_schema as ensure_draft_schema  # noqa: E402
from engine.match_engine import DEFAULT_SEASON, PlayerSnapshot  # noqa: E402
from engine.secondary_behavior import (  # noqa: E402
    SECONDARY_STYLE_OVERRIDES,
    ensure_player_secondary_behavior_schema,
    generated_secondary_behavior_profile,
    normalize_name,
    profile_to_db_tuple,
    secondary_behavior_profile,
)


SECONDARY_POSITIONS = ("CB", "NB", "FS", "SS", "S")


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def load_secondaries(con: sqlite3.Connection, season: int) -> list[sqlite3.Row]:
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
            WHERE p.position IN ('CB', 'NB', 'FS', 'SS', 'S')
              AND EXISTS (
                  SELECT 1
                  FROM player_ratings pr
                  WHERE pr.player_id = p.player_id
                    AND pr.season = ?
              )
            ORDER BY CASE WHEN t.abbreviation IS NULL THEN 1 ELSE 0 END,
                     t.abbreviation,
                     CASE p.position
                         WHEN 'CB' THEN 1
                         WHEN 'NB' THEN 2
                         WHEN 'FS' THEN 3
                         WHEN 'SS' THEN 4
                         ELSE 5
                     END,
                     p.overall DESC,
                     p.player_id
            """,
            (season,),
        )
    )


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
    ensure_player_secondary_behavior_schema(con)
    counts: Counter[str] = Counter()
    for row in load_secondaries(con, season):
        defender = player_snapshot(con, row, season)
        profile = secondary_behavior_profile(defender)
        source_kind = "named" if normalize_name(defender.name) in SECONDARY_STYLE_OVERRIDES else "inferred"
        source = f"secondary_behavior_{source_kind}_seed"
        counts[source_kind] += 1
        if apply:
            con.execute(
                """
                INSERT INTO player_secondary_behavior_profiles (
                    player_id, season, label, press_timing, man_mirror,
                    zone_eye_discipline, break_trigger, deep_range,
                    ball_play_timing, catch_point_compete, slot_traffic,
                    run_support_fit, tackle_finish, penalty_control,
                    source, notes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(player_id, season) DO UPDATE SET
                    label = excluded.label,
                    press_timing = excluded.press_timing,
                    man_mirror = excluded.man_mirror,
                    zone_eye_discipline = excluded.zone_eye_discipline,
                    break_trigger = excluded.break_trigger,
                    deep_range = excluded.deep_range,
                    ball_play_timing = excluded.ball_play_timing,
                    catch_point_compete = excluded.catch_point_compete,
                    slot_traffic = excluded.slot_traffic,
                    run_support_fit = excluded.run_support_fit,
                    tackle_finish = excluded.tackle_finish,
                    penalty_control = excluded.penalty_control,
                    source = excluded.source,
                    notes = excluded.notes,
                    updated_at = datetime('now')
                """,
                profile_to_db_tuple(defender.player_id, season, profile, source),
            )
    return counts


def latest_draft_year(con: sqlite3.Connection) -> int | None:
    row = con.execute(
        """
        SELECT MAX(dc.draft_year) AS draft_year
        FROM draft_classes dc
        JOIN draft_prospects dp ON dp.draft_class_id = dc.draft_class_id
        WHERE dp.position IN ('CB', 'NB', 'FS', 'SS', 'S')
        """
    ).fetchone()
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
          AND dp.position IN ('CB', 'NB', 'FS', 'SS', 'S')
          AND EXISTS (
              SELECT 1
              FROM draft_prospect_ratings dpr
              WHERE dpr.prospect_id = dp.prospect_id
          )
        ORDER BY dp.position, dp.true_rank, dp.prospect_id
        """,
        (draft_year,),
    ).fetchall()
    for row in rows:
        profile = generated_secondary_behavior_profile(
            str(row["archetype"] or ""),
            draft_ratings(con, int(row["prospect_id"])),
            position=str(row["position"] or "CB"),
        )
        counts[str(row["position"])] += 1
        if apply:
            replace_prospect_secondary_behavior_profile(
                con,
                int(row["prospect_id"]),
                DraftProspectSecondaryBehaviorProfile(
                    label=profile.label,
                    press_timing=int(round(profile.press_timing)),
                    man_mirror=int(round(profile.man_mirror)),
                    zone_eye_discipline=int(round(profile.zone_eye_discipline)),
                    break_trigger=int(round(profile.break_trigger)),
                    deep_range=int(round(profile.deep_range)),
                    ball_play_timing=int(round(profile.ball_play_timing)),
                    catch_point_compete=int(round(profile.catch_point_compete)),
                    slot_traffic=int(round(profile.slot_traffic)),
                    run_support_fit=int(round(profile.run_support_fit)),
                    tackle_finish=int(round(profile.tackle_finish)),
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
        print("Dry run only. Add --apply to write secondary behavior profiles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
