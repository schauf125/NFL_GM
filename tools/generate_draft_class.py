#!/usr/bin/env python3
"""Generate a draft class and optionally persist it into the game database."""

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

from engine.draft.class_preview import (
    DEFAULT_OUTPUT_DIR,
    DraftClassPreviewGenerator,
    write_csv,
    write_html,
)
from engine.draft.persistence import persist_draft_class
from engine.draft.schema import ensure_schema
from engine.draft.validation import write_preview_report


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--year", type=int, default=2027)
    parser.add_argument("--count", type=int, default=310)
    parser.add_argument("--hidden-count", type=int, help="Exact number of off-public-board prospects to add.")
    parser.add_argument("--hidden-min", type=int, default=36)
    parser.add_argument("--hidden-max", type=int, default=44)
    parser.add_argument("--no-hidden-prospects", action="store_true", help="Generate only the public board.")
    parser.add_argument("--seed", default=None)
    parser.add_argument("--class-strength", type=int, default=50)
    parser.add_argument("--class-name")
    parser.add_argument("--notes", default="Generated fictional draft class.")
    parser.add_argument("--international-chance", type=float, default=0.04)
    parser.add_argument("--physical-outlier-chance", type=float, default=0.045)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--apply", action="store_true", help="Persist the generated class. Omit for dry run.")
    parser.add_argument("--force", action="store_true", help="Replace an existing generated class for this year.")
    parser.add_argument(
        "--include-hidden-preview",
        action="store_true",
        help="Include true grades/dev traits/private workouts in preview CSV and HTML.",
    )
    parser.add_argument("--no-preview", action="store_true", help="Skip writing preview CSV/HTML/report files.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    seed = str(args.seed if args.seed is not None else f"{args.year}-draft-class")
    generator = DraftClassPreviewGenerator(seed=seed)
    rows = generator.generate(
        draft_year=args.year,
        count=args.count,
        hidden_count=0 if args.no_hidden_prospects else args.hidden_count,
        hidden_min=args.hidden_min,
        hidden_max=args.hidden_max,
        international_chance=args.international_chance,
        physical_outlier_chance=args.physical_outlier_chance,
        class_strength=args.class_strength,
    )

    if not args.no_preview:
        stem = f"{args.year}_draft_class"
        csv_path = args.out_dir / f"{stem}.csv"
        html_path = args.out_dir / f"{stem}.html"
        report_path = args.out_dir / f"{stem}_validation.txt"
        write_csv(rows, csv_path, include_hidden=args.include_hidden_preview)
        write_html(rows, html_path, include_hidden=args.include_hidden_preview)
        write_preview_report(rows, report_path)
        print(f"CSV:  {csv_path}")
        print(f"HTML: {html_path}")
        print(f"Report: {report_path}")

    off_board_count = sum(row.public_board_status == "off_public_board" for row in rows)
    public_count = len(rows) - off_board_count
    print(
        f"Generated {len(rows)} prospects for {args.year} "
        f"({public_count} public board, {off_board_count} off-board)"
    )
    print(f"Seed: {seed}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print("Top public board")
    for row in [row for row in rows if row.public_board_rank is not None][:10]:
        projected = "UDFA" if row.projected_round is None else f"R{row.projected_round}"
        print(
            f"{row.rank:>3}. {row.full_name:<24} {row.position:<4} {projected:<4} "
            f"{row.college:<18} scout {row.scout_grade}/{row.scout_ceiling} risk {row.scout_risk}"
        )
    print("")
    print("Positions")
    for position, count in Counter(row.position for row in rows).most_common():
        print(f"{position}: {count}")

    if args.apply:
        with connect(args.db) as con:
            ensure_schema(con)
            result = persist_draft_class(
                con,
                rows,
                draft_year=args.year,
                class_strength=args.class_strength,
                generation_seed=seed,
                class_name=args.class_name,
                notes=args.notes,
                force=args.force,
            )
            con.commit()
        action = "Replaced" if result.replaced_existing else "Persisted"
        print("")
        print(f"{action} draft class {result.draft_year} in {args.db}")
        print(f"Draft class id: {result.draft_class_id}")
        print(f"Prospects: {result.prospect_count}")
    else:
        print("")
        print("Dry run only. Add --apply to persist this class into SQLite.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
