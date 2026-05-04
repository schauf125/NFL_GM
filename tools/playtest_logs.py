#!/usr/bin/env python3
"""Inspect and bundle manual playtest logs."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_ROOT = ROOT / "logs" / "playtests"


def sessions(log_root: Path) -> list[Path]:
    if not log_root.exists():
        return []
    return sorted(
        [path for path in log_root.iterdir() if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def latest_session(log_root: Path) -> Path:
    rows = sessions(log_root)
    if not rows:
        raise FileNotFoundError(f"No playtest logs found in {log_root}")
    return rows[0]


def action_list(args: argparse.Namespace) -> None:
    rows = sessions(Path(args.log_root))[: args.limit]
    if not rows:
        print(f"No playtest logs found in {args.log_root}")
        return
    for path in rows:
        print(path)


def action_latest(args: argparse.Namespace) -> None:
    path = latest_session(Path(args.log_root))
    print(f"Latest playtest log: {path}")
    for name in ["ISSUE_TEMPLATE.md", "play_by_play.log", "events.jsonl", "box_score.txt", "result.json", "crash_report.txt"]:
        candidate = path / name
        if candidate.exists():
            print(f"  {name}: {candidate}")


def action_bundle(args: argparse.Namespace) -> None:
    source = latest_session(Path(args.log_root)) if args.latest else Path(args.session)
    if not source.exists() or not source.is_dir():
        raise FileNotFoundError(source)
    output_base = Path(args.output) if args.output else source
    if output_base.suffix.lower() == ".zip":
        output_base = output_base.with_suffix("")
    archive = shutil.make_archive(str(output_base), "zip", root_dir=source)
    print(f"Bundled playtest logs: {archive}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect manual playtest logs.")
    parser.add_argument("--log-root", default=str(LOG_ROOT))
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List recent playtest sessions.")
    list_parser.add_argument("--limit", type=int, default=10)
    list_parser.set_defaults(func=action_list)

    latest_parser = subparsers.add_parser("latest", help="Show the latest playtest session and key files.")
    latest_parser.set_defaults(func=action_latest)

    bundle_parser = subparsers.add_parser("bundle", help="Create a zip bundle for a playtest session.")
    bundle_parser.add_argument("--latest", action="store_true", help="Bundle the latest session.")
    bundle_parser.add_argument("--session", help="Specific session folder to bundle.")
    bundle_parser.add_argument("--output", help="Optional output .zip path.")
    bundle_parser.set_defaults(func=action_bundle)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "bundle" and not args.latest and not args.session:
        parser.error("bundle requires --latest or --session")
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
