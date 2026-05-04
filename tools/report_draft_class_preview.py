#!/usr/bin/env python3
"""Print a validation report for a generated draft-class preview CSV."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.draft.class_preview import DEFAULT_OUTPUT_DIR
from engine.draft.validation import build_preview_report, read_preview_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "2027_draft_class_preview.csv",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.csv.exists():
        print(f"Preview CSV not found: {args.csv}", file=sys.stderr)
        return 1
    print(build_preview_report(read_preview_csv(args.csv)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
