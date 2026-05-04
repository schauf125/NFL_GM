#!/usr/bin/env python3
"""Build and sample college/age data for draft prospects."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "draft" / "colleges" / "college_pool.db"
DEFAULT_CACHE_DIR = ROOT / "data" / "draft" / "colleges" / ".cache"
DEFAULT_CONFIG = ROOT / "data" / "draft" / "colleges" / "college_config.json"
NFLVERSE_PLAYERS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
)
USER_AGENT = "NFL-GM-Sim college-pool builder/0.1"


POWER_SCHOOLS = {
    "Alabama", "Georgia", "Ohio State", "LSU", "Michigan", "Clemson", "Texas",
    "Oklahoma", "Notre Dame", "Penn State", "USC", "Oregon", "Florida",
    "Florida State", "Miami", "Tennessee", "Auburn", "Texas A&M", "Washington",
    "Wisconsin", "Iowa", "North Carolina", "South Carolina", "UCLA", "Stanford",
}

COLLEGE_SPLIT_MARKERS = (";", "|", "/")
JUNIOR_COLLEGE_HINTS = (
    " community college",
    " junior college",
    " city college",
    " cc",
    " jc",
    " arizona western",
    " blinn",
    " butte",
    " coffeyville",
    " east mississippi",
    " garden city",
    " hutchinson",
    " iowa western",
    " kilgore",
    " lackawanna",
    " mississippi gulf coast",
    " northwest mississippi",
    " snow college",
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_players(cache_dir: Path, *, refresh: bool) -> tuple[bytes, str]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "nflverse_players.csv"
    meta_path = cache_dir / "nflverse_players.csv.json"
    if cache_path.exists() and meta_path.exists() and not refresh:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return cache_path.read_bytes(), str(meta["used_url"])
    request = urllib.request.Request(NFLVERSE_PLAYERS_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
    except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise RuntimeError(f"Unable to download {NFLVERSE_PLAYERS_URL}: {exc}") from exc
    cache_path.write_bytes(data)
    meta_path.write_text(
        json.dumps({"used_url": NFLVERSE_PLAYERS_URL, "fetched_at": now_utc()}, indent=2),
        encoding="utf-8",
    )
    return data, NFLVERSE_PLAYERS_URL


def clean_college(value: str) -> str:
    value = " ".join((value or "").strip().split())
    value = graduation_college(value)
    aliases = {
        "N.C. State": "NC State",
        "North Carolina State": "NC State",
        "Southern California": "USC",
        "Texas Christian": "TCU",
        "Brigham Young": "BYU",
    }
    return aliases.get(value, value)


def graduation_college(value: str) -> str:
    """Return one school from compound transfer fields."""
    if not value:
        return ""
    for marker in COLLEGE_SPLIT_MARKERS:
        value = value.replace(marker, ";")
    parts = [part.strip() for part in value.split(";")]
    parts = [part for part in parts if part]
    if len(parts) <= 1:
        return parts[0] if parts else ""
    four_year_parts = [
        part
        for part in parts
        if not any(hint in f" {part.lower()}" for hint in JUNIOR_COLLEGE_HINTS)
    ]
    return four_year_parts[0] if four_year_parts else parts[0]


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        DROP VIEW IF EXISTS college_pool_summary_view;
        DROP TABLE IF EXISTS build_metadata;
        DROP TABLE IF EXISTS college_sources;
        DROP TABLE IF EXISTS international_development_sources;
        DROP TABLE IF EXISTS age_weights;
        DROP TABLE IF EXISTS college_pool;

        CREATE TABLE college_sources (
            source_key TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            source_url TEXT NOT NULL,
            used_url TEXT NOT NULL,
            notes TEXT,
            fetched_at TEXT NOT NULL
        );

        CREATE TABLE build_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE college_pool (
            college TEXT PRIMARY KEY,
            player_count INTEGER NOT NULL,
            weight REAL NOT NULL,
            tier TEXT NOT NULL
        );

        CREATE TABLE age_weights (
            bucket TEXT NOT NULL,
            age INTEGER NOT NULL,
            weight REAL NOT NULL,
            PRIMARY KEY (bucket, age)
        );

        CREATE TABLE international_development_sources (
            source_name TEXT PRIMARY KEY,
            weight REAL NOT NULL
        );

        CREATE VIEW college_pool_summary_view AS
        SELECT tier, COUNT(*) AS college_count, ROUND(SUM(weight), 1) AS total_weight
        FROM college_pool
        GROUP BY tier;
        """
    )


def cmd_build(args: argparse.Namespace) -> None:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    data, used_url = fetch_players(args.cache_dir, refresh=args.refresh)
    counts: Counter[str] = Counter()
    text = io.StringIO(data.decode("utf-8-sig"))
    for row in csv.DictReader(text):
        college = clean_college(row.get("college_name", ""))
        if college:
            counts[college] += 1

    args.db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.db) as con:
        create_schema(con)
        con.execute(
            """
            INSERT INTO college_sources (
                source_key, source_name, source_url, used_url, notes, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "nflverse_players",
                "nflverse player information",
                NFLVERSE_PLAYERS_URL,
                used_url,
                "College frequency weights from historical/current NFL players.",
                now_utc(),
            ),
        )
        rows = []
        for college, count in counts.most_common(args.max_colleges):
            if count >= args.power_threshold or college in POWER_SCHOOLS:
                tier = "Power"
                weight = count * float(config["power_school_weight_multiplier"])
            elif count <= args.small_threshold:
                tier = "Small"
                weight = count * float(config["small_school_weight_multiplier"])
            else:
                tier = "Regular"
                weight = float(count)
            rows.append((college, count, weight, tier))
        con.executemany(
            "INSERT INTO college_pool (college, player_count, weight, tier) VALUES (?, ?, ?, ?)",
            rows,
        )
        age_rows = []
        for bucket, weights in config["age_bucket_weights"].items():
            for age_text, weight in weights.items():
                age_rows.append((bucket, int(age_text), float(weight)))
        con.executemany(
            """
            INSERT INTO age_weights (bucket, age, weight)
            VALUES (?, ?, ?)
            """,
            age_rows,
        )
        con.executemany(
            "INSERT INTO international_development_sources (source_name, weight) VALUES (?, ?)",
            [
                (source_name, float(weight))
                for source_name, weight in config["international_development_sources"].items()
            ],
        )
        con.executemany(
            "INSERT INTO build_metadata (key, value) VALUES (?, ?)",
            [
                ("built_at", now_utc()),
                ("config", str(args.config)),
                ("max_colleges", str(args.max_colleges)),
            ],
        )
        con.commit()
    print(f"Built college pool: {args.db}")
    print_summary(args.db)


def print_summary(db_path: Path) -> None:
    with sqlite3.connect(db_path) as con:
        for tier, college_count, total_weight in con.execute(
            "SELECT tier, college_count, total_weight FROM college_pool_summary_view ORDER BY total_weight DESC"
        ):
            print(f"{tier}: {college_count} colleges, weight {total_weight}")


def cmd_summary(args: argparse.Namespace) -> None:
    print_summary(args.db)


def cmd_sample(args: argparse.Namespace) -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from engine.draft.college import CollegeGenerator

    generator = CollegeGenerator(args.db, seed=args.seed)
    for rank in range(1, args.count + 1):
        result = generator.generate(rank=rank, is_international=False)
        print(f"{rank:3} age {result.age} {result.college} ({result.college_tier})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build college pool database")
    build_parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    build_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    build_parser.add_argument("--refresh", action="store_true")
    build_parser.add_argument("--max-colleges", type=int, default=800)
    build_parser.add_argument("--power-threshold", type=int, default=160)
    build_parser.add_argument("--small-threshold", type=int, default=8)
    build_parser.set_defaults(func=cmd_build)

    summary_parser = subparsers.add_parser("summary", help="Show college pool summary")
    summary_parser.set_defaults(func=cmd_summary)

    sample_parser = subparsers.add_parser("sample", help="Sample college/age rows")
    sample_parser.add_argument("--count", type=int, default=20)
    sample_parser.add_argument("--seed")
    sample_parser.set_defaults(func=cmd_sample)
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
