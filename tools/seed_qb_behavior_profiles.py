#!/usr/bin/env python3
"""Seed stored QB behavior profiles for current players."""

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

from engine.match_engine import DEFAULT_SEASON, PlayerSnapshot  # noqa: E402
from engine.qb_behavior import (  # noqa: E402
    QB_STYLE_OVERRIDES,
    ensure_player_qb_behavior_schema,
    normalize_name,
    profile_to_db_tuple,
    qb_behavior_profile,
)


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def load_qbs(con: sqlite3.Connection, season: int) -> list[sqlite3.Row]:
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
            WHERE p.position = 'QB'
              AND EXISTS (
                  SELECT 1
                  FROM player_ratings pr
                  WHERE pr.player_id = p.player_id
                    AND pr.season = ?
              )
            ORDER BY CASE WHEN t.abbreviation IS NULL THEN 1 ELSE 0 END,
                     t.abbreviation,
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


def seed_profiles(con: sqlite3.Connection, *, season: int, apply: bool) -> Counter[str]:
    ensure_player_qb_behavior_schema(con)
    counts: Counter[str] = Counter()
    for row in load_qbs(con, season):
        qb = player_snapshot(con, row, season)
        profile = qb_behavior_profile(qb)
        source_kind = "named" if normalize_name(qb.name) in QB_STYLE_OVERRIDES else "inferred"
        source = f"qb_behavior_{source_kind}_seed"
        counts[source_kind] += 1
        if apply:
            con.execute(
                """
                INSERT INTO player_qb_behavior_profiles (
                    player_id, season, label, rhythm, pocket_discipline, pocket_drift,
                    checkdown_willingness, deep_aggression, pressure_escape,
                    broken_play_creation, scramble_trigger, sack_risk,
                    throwaway_discipline, source, notes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(player_id, season) DO UPDATE SET
                    label = excluded.label,
                    rhythm = excluded.rhythm,
                    pocket_discipline = excluded.pocket_discipline,
                    pocket_drift = excluded.pocket_drift,
                    checkdown_willingness = excluded.checkdown_willingness,
                    deep_aggression = excluded.deep_aggression,
                    pressure_escape = excluded.pressure_escape,
                    broken_play_creation = excluded.broken_play_creation,
                    scramble_trigger = excluded.scramble_trigger,
                    sack_risk = excluded.sack_risk,
                    throwaway_discipline = excluded.throwaway_discipline,
                    source = excluded.source,
                    notes = excluded.notes,
                    updated_at = datetime('now')
                """,
                profile_to_db_tuple(qb.player_id, season, profile, source),
            )
    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    parser.add_argument("--apply", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with connect(args.db) as con:
        counts = seed_profiles(con, season=args.season, apply=args.apply)
        if args.apply:
            con.commit()
        else:
            con.rollback()
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"Season: {args.season}")
    print(f"Profiles: {sum(counts.values())}")
    for source, count in sorted(counts.items()):
        print(f"{source}: {count}")
    if not args.apply:
        print("Dry run only. Add --apply to write player_qb_behavior_profiles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
