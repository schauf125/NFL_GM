#!/usr/bin/env python3
"""Set up and inspect draft-class tables.

This creates the schema layer for generated draft classes and prospects. It does
not generate prospects yet; it gives the game a safe holding area before a
prospect becomes a real player through the draft.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.draft.repository import DraftClass, create_draft_class, list_draft_classes
from engine.draft.schema import ensure_schema


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found: {db_path}. Pass --db or add database/nfl_gm.db first."
        )
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def cmd_apply(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_schema(con)
        con.commit()
    print(f"Draft-class schema ready: {args.db}")


def cmd_create_class(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        class_id = create_draft_class(
            con,
            DraftClass(
                draft_year=args.year,
                class_name=args.name or f"{args.year} NFL Draft Class",
                class_strength=args.strength,
                generation_seed=args.seed,
                notes=args.notes,
            ),
        )
        con.commit()
    print(f"Draft class {args.year} ready with id {class_id}")


def cmd_list(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        rows = list_draft_classes(con)
    if not rows:
        print("No draft classes found.")
        return
    for row in rows:
        print(
            f"{row['draft_year']} | {row['class_name']} | "
            f"strength {row['class_strength']} | prospects {row['prospect_count']} | "
            f"status {row['status']}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"SQLite DB path. Default: {DB_PATH}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    apply_parser = subparsers.add_parser("apply", help="Create draft schema tables/views")
    apply_parser.set_defaults(func=cmd_apply)

    create_parser = subparsers.add_parser("create-class", help="Create or update a draft class")
    create_parser.add_argument("year", type=int)
    create_parser.add_argument("--name")
    create_parser.add_argument("--strength", type=int, default=50)
    create_parser.add_argument("--seed")
    create_parser.add_argument("--notes")
    create_parser.set_defaults(func=cmd_create_class)

    list_parser = subparsers.add_parser("list", help="List draft classes")
    list_parser.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

