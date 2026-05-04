#!/usr/bin/env python3
"""Validate generated draft classes from a preview CSV or persisted SQLite class."""

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

from engine.draft.class_preview import DEFAULT_OUTPUT_DIR
from engine.draft.schema import ensure_schema
from engine.draft.validation import build_preview_report, read_preview_csv


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview_parser = subparsers.add_parser("preview", help="Validate a generated preview CSV.")
    preview_parser.add_argument("--csv", type=Path, default=DEFAULT_OUTPUT_DIR / "2027_draft_class.csv")
    preview_parser.set_defaults(func=action_preview)

    db_parser = subparsers.add_parser("db", help="Validate a persisted draft class.")
    db_parser.add_argument("--draft-year", type=int, required=True)
    db_parser.set_defaults(func=action_db)
    return parser


def action_preview(args: argparse.Namespace) -> None:
    if not args.csv.exists():
        raise FileNotFoundError(args.csv)
    print(build_preview_report(read_preview_csv(args.csv)), end="")


def action_db(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_schema(con)
        class_row = con.execute(
            "SELECT * FROM draft_classes WHERE draft_year = ?",
            (args.draft_year,),
        ).fetchone()
        if not class_row:
            raise ValueError(f"No draft class found for {args.draft_year}.")
        rows = persisted_rows(con, int(class_row["draft_class_id"]))
        integrity = integrity_lines(con, int(class_row["draft_class_id"]), len(rows))
    print(build_preview_report(rows), end="")
    print("Persisted Table Integrity")
    print("=========================")
    for line in integrity:
        print(line)


def persisted_rows(con: sqlite3.Connection, draft_class_id: int) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT
            dp.prospect_id,
            COALESCE(dp.public_board_rank, dp.scouting_rank) AS rank,
            dp.true_rank,
            dp.generation_version,
            dp.position,
            dp.position_group,
            dp.age,
            dp.college,
            dp.college_tier,
            dp.height_in,
            dp.weight_lbs,
            dp.arm_length_in,
            dp.hand_size_in,
            dp.ethnicity_key,
            dp.birth_country,
            dp.handedness,
            dp.primary_role,
            dp.archetype,
            dp.original_archetype,
            dp.archetype_identity_status,
            dp.archetype_identity_note,
            dp.true_grade,
            dp.ceiling_grade,
            dp.dev_trait,
            dp.risk_level,
            dp.scout_lens,
            dp.scout_confidence,
            dp.scout_grade,
            dp.scout_ceiling,
            dp.scout_risk,
            dp.eye_color,
            dp.hair_color,
            dp.hairstyle,
            dp.facial_hair_style AS facial_hair,
            dp.secondary_ethnicity_label AS secondary_ethnicity,
            dp.hairstyle_outlier,
            dp.combine_status,
            dp.combine_grade,
            dp.combine_athletic_score AS athletic_score,
            dp.combine_drills_completed AS drills_completed,
            dpc.workout_variance,
            dp.combine_injured,
            dp.combine_top_skip,
            dp.pro_day_status,
            dp.pro_day_grade,
            dp.pro_day_athletic_score,
            dp.pro_day_drills_completed,
            dpd.workout_variance AS pro_day_workout_variance,
            dp.pro_day_improved_from_combine,
            dp.pro_day_medical_recheck
        FROM draft_internal_board_view dp
        LEFT JOIN draft_prospect_combine_results dpc ON dpc.prospect_id = dp.prospect_id
        LEFT JOIN draft_prospect_pro_day_results dpd ON dpd.prospect_id = dp.prospect_id
        WHERE dp.draft_class_id = ?
        ORDER BY
            CASE WHEN COALESCE(dp.public_board_rank, dp.scouting_rank) IS NULL THEN 1 ELSE 0 END,
            COALESCE(dp.public_board_rank, dp.scouting_rank),
            dp.prospect_id
        """,
        (draft_class_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def integrity_lines(con: sqlite3.Connection, draft_class_id: int, prospect_count: int) -> list[str]:
    lines: list[str] = []
    counts = {
        "prospects": prospect_count,
        "rating rows": con.execute(
            """
            SELECT COUNT(*)
            FROM draft_prospect_ratings dpr
            JOIN draft_prospects dp ON dp.prospect_id = dpr.prospect_id
            WHERE dp.draft_class_id = ?
            """,
            (draft_class_id,),
        ).fetchone()[0],
        "role score rows": con.execute(
            """
            SELECT COUNT(*)
            FROM draft_prospect_role_scores dprs
            JOIN draft_prospects dp ON dp.prospect_id = dprs.prospect_id
            WHERE dp.draft_class_id = ?
            """,
            (draft_class_id,),
        ).fetchone()[0],
        "combine rows": _count_side_table(con, draft_class_id, "draft_prospect_combine_results"),
        "pro-day rows": _count_side_table(con, draft_class_id, "draft_prospect_pro_day_results"),
        "private workout rows": _count_side_table(con, draft_class_id, "draft_prospect_private_workouts"),
        "personality rows": _count_side_table(con, draft_class_id, "draft_prospect_personalities"),
    }
    for label, count in counts.items():
        lines.append(f"{label}: {count}")
    duplicate_public = con.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT public_board_rank
            FROM draft_prospects
            WHERE draft_class_id = ? AND public_board_rank IS NOT NULL
            GROUP BY public_board_rank
            HAVING COUNT(*) > 1
        )
        """,
        (draft_class_id,),
    ).fetchone()[0]
    missing_ratings = con.execute(
        """
        SELECT COUNT(*)
        FROM draft_prospects dp
        WHERE dp.draft_class_id = ?
          AND NOT EXISTS (
              SELECT 1 FROM draft_prospect_ratings dpr WHERE dpr.prospect_id = dp.prospect_id
          )
        """,
        (draft_class_id,),
    ).fetchone()[0]
    lines.append(f"duplicate public ranks: {duplicate_public}")
    lines.append(f"prospects missing hidden ratings: {missing_ratings}")
    return lines


def _count_side_table(con: sqlite3.Connection, draft_class_id: int, table: str) -> int:
    return con.execute(
        f"""
        SELECT COUNT(*)
        FROM {table} side
        JOIN draft_prospects dp ON dp.prospect_id = side.prospect_id
        WHERE dp.draft_class_id = ?
        """,
        (draft_class_id,),
    ).fetchone()[0]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
