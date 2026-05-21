#!/usr/bin/env python3
"""Generate a viewable draft-class preview with normalized sim-rating summaries."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.draft.class_preview import (
    DEFAULT_OUTPUT_DIR,
    DraftClassPreviewGenerator,
    write_csv,
    write_html,
)
from engine.draft.validation import write_preview_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2027)
    parser.add_argument("--count", type=int, default=310)
    parser.add_argument("--hidden-count", type=int, help="Exact number of off-public-board prospects to add.")
    parser.add_argument("--hidden-min", type=int, default=36)
    parser.add_argument("--hidden-max", type=int, default=44)
    parser.add_argument("--no-hidden-prospects", action="store_true", help="Generate only the public board.")
    parser.add_argument("--seed", default="2027-preview")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--international-chance", type=float, default=0.04)
    parser.add_argument("--physical-outlier-chance", type=float, default=0.045)
    parser.add_argument("--class-strength", type=int, default=50)
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include true grades/dev traits/private workouts in preview CSV and HTML.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generator = DraftClassPreviewGenerator(seed=args.seed)
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
    stem = f"{args.year}_draft_class_preview"
    csv_path = args.out_dir / f"{stem}.csv"
    html_path = args.out_dir / f"{stem}.html"
    report_path = args.out_dir / f"{stem}_report.txt"
    write_csv(rows, csv_path, include_hidden=args.include_hidden)
    write_html(rows, html_path, include_hidden=args.include_hidden)
    write_preview_report(rows, report_path)

    off_board_count = sum(row.public_board_status == "off_public_board" for row in rows)
    print(
        f"Generated {len(rows)} prospects "
        f"({len(rows) - off_board_count} public board, {off_board_count} off-board)"
    )
    print(f"CSV:  {csv_path}")
    print(f"HTML: {html_path}")
    print(f"Report: {report_path}")
    print("\nPosition counts")
    for position, count in Counter(row.position for row in rows).most_common():
        print(f"{position}: {count}")
    print("\nAppearance notes")
    print(f"Two-ethnicity prospects: {sum(bool(row.secondary_ethnicity) for row in rows)}")
    print(f"Hairstyle outliers: {sum(row.hairstyle_outlier for row in rows)}")
    print(f"Mustache-only prospects: {sum(row.facial_hair == 'Mustache only' for row in rows)}")
    print(f"Physical outliers: {sum(row.physical_outlier for row in rows)}")
    print("\nDev traits")
    for trait, count in Counter(row.dev_trait for row in rows).most_common():
        print(f"{trait}: {count}")
    print(f"Average true grade: {sum(row.true_grade for row in rows) / len(rows):.1f}")
    print(f"Average ceiling grade: {sum(row.ceiling_grade for row in rows) / len(rows):.1f}")
    print(f"Average scout grade: {sum(row.scout_grade for row in rows) / len(rows):.1f}")
    print("\nCombine")
    combine_rows = [row for row in rows if row.combine_grade is not None]
    if combine_rows:
        print(f"Average combine grade: {sum(row.combine_grade or 0 for row in combine_rows) / len(combine_rows):.1f}")
    for status, count in Counter(row.combine_status for row in rows).most_common():
        print(f"{status}: {count}")
    print(f"No workout drills: {sum(row.drills_completed == 0 for row in rows)}")
    print(f"Injury-limited or DNP: {sum(row.combine_injured for row in rows)}")
    print(f"Strategic top/pro-day skips: {sum(row.combine_top_skip for row in rows)}")
    print("\nPro days")
    pro_day_rows = [row for row in rows if row.pro_day_grade is not None]
    if pro_day_rows:
        print(f"Average pro-day grade: {sum(row.pro_day_grade or 0 for row in pro_day_rows) / len(pro_day_rows):.1f}")
    for status, count in Counter(row.pro_day_status for row in rows).most_common():
        print(f"{status}: {count}")
    print(f"Pro-day improvers: {sum(row.pro_day_improved_from_combine for row in rows)}")
    print(f"Medical rechecks: {sum(row.pro_day_medical_recheck for row in rows)}")
    print("\nScout lenses")
    for lens, count in Counter(row.scout_lens for row in rows).most_common():
        print(f"{lens}: {count}")
    print("Scout risk")
    for risk, count in Counter(row.scout_risk for row in rows).most_common():
        print(f"{risk}: {count}")
    print("\nAge counts")
    for age, count in sorted(Counter(row.age for row in rows).items()):
        print(f"{age}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
