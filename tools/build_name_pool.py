#!/usr/bin/env python3
"""Build and sample the draft name-pool database.

The compiled database blends:
- U.S. first-name usage from Social Security Administration baby-name data.
- U.S. surname usage from the 2010 Census surname file.
- First and last name components from nflverse NFL player data.

The builder stores source metadata in SQLite so a generated draft class can be
traced back to the inputs that shaped it.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
import sys
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "draft" / "names" / "name_pool.db"
DEFAULT_CACHE_DIR = ROOT / "data" / "draft" / "names" / ".cache"
DEFAULT_DIVERSITY_CONFIG = ROOT / "data" / "draft" / "names" / "ethnicity_origins.json"

SSA_NAMES_URL = "https://www.ssa.gov/oact/babynames/names.zip"
SSA_MIRROR_URL = (
    "https://raw.githubusercontent.com/hackerb9/ssa-baby-names/master/alldata.txt"
)
CENSUS_SURNAMES_URL = (
    "https://www2.census.gov/topics/genealogy/2010surnames/names.zip"
)
NFLVERSE_PLAYERS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
)

USER_AGENT = "NFL-GM-Sim name-pool builder/0.1"


@dataclass
class SourceFile:
    source_key: str
    source_name: str
    source_url: str
    used_url: str
    notes: str


@dataclass
class FirstNameStats:
    name: str
    gender: str
    ssa_births: int = 0
    ssa_recent_births: int = 0
    first_year: int | None = None
    last_year: int | None = None
    football_count: int = 0
    football_active_count: int = 0


@dataclass
class LastNameStats:
    name: str
    census_count: int = 0
    census_rank: int | None = None
    football_count: int = 0
    football_active_count: int = 0


@dataclass
class EthnicityProfile:
    key: str
    label: str
    target_pct: float
    sigma_pct: float
    min_pct: float
    max_pct: float
    first_styles: dict[str, int]
    last_styles: dict[str, int]


@dataclass
class OriginCountry:
    country: str
    weight: int
    ethnicity_key: str
    common_positions: str
    first_styles: dict[str, int]
    last_styles: dict[str, int]


@dataclass
class StyleComponent:
    style_key: str
    component_type: str
    name: str
    weight: int


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_name_key(value: str) -> str:
    import unicodedata

    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return "".join(ch for ch in ascii_value.upper() if "A" <= ch <= "Z")


def display_case_name(value: str) -> str:
    value = " ".join(value.strip().split())
    if not value:
        return value
    if any(ch.islower() for ch in value) and any(ch.isupper() for ch in value[1:]):
        return value
    if "." in value and len(value) <= 6:
        return value.upper()

    def fix_piece(piece: str) -> str:
        if not piece:
            return piece
        lower = piece.lower()
        fixed = lower[0].upper() + lower[1:]
        if lower.startswith("mc") and len(lower) > 2:
            fixed = "Mc" + lower[2].upper() + lower[3:]
        return fixed

    words = []
    for word in value.replace("_", " ").split(" "):
        hyphen_parts = []
        for hyphen_part in word.split("-"):
            apostrophe_parts = [fix_piece(part) for part in hyphen_part.split("'")]
            hyphen_parts.append("'".join(apostrophe_parts))
        words.append("-".join(hyphen_parts))
    return " ".join(words)


def usable_name(value: str) -> bool:
    key = normalize_name_key(value)
    if key in {"ALLOTHERNAMES", "UNKNOWN", "NONAME", "NULL", "NONE"}:
        return False
    if len(key) < 2:
        return False
    if len(key) > 24:
        return False
    return any(ch.isalpha() for ch in value)


def fetch_bytes(
    *,
    cache_dir: Path,
    cache_name: str,
    urls: list[str],
    refresh: bool,
) -> tuple[bytes, str]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / cache_name
    meta_path = cache_dir / f"{cache_name}.json"
    if cache_path.exists() and meta_path.exists() and not refresh:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return cache_path.read_bytes(), str(meta["used_url"])

    errors = []
    for url in urls:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
            cache_path.write_bytes(data)
            meta_path.write_text(
                json.dumps({"used_url": url, "fetched_at": now_utc()}, indent=2),
                encoding="utf-8",
            )
            return data, url
        except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("Unable to download source:\n" + "\n".join(errors))


def read_ssa_names(data: bytes, used_url: str) -> list[tuple[str, str, int, int]]:
    rows: list[tuple[str, str, int, int]] = []
    if used_url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if not info.filename.startswith("yob") or not info.filename.endswith(".txt"):
                    continue
                year = int(info.filename[3:7])
                with archive.open(info) as handle:
                    text = io.TextIOWrapper(handle, encoding="utf-8")
                    for raw_name, gender, count in csv.reader(text):
                        rows.append((raw_name, gender, int(count), year))
    else:
        text = io.StringIO(data.decode("utf-8"))
        for raw_name, gender, count, year in csv.reader(text):
            rows.append((raw_name, gender, int(count), int(year)))
    return rows


def load_first_names(args: argparse.Namespace) -> tuple[dict[tuple[str, str], FirstNameStats], SourceFile]:
    data, used_url = fetch_bytes(
        cache_dir=args.cache_dir,
        cache_name="ssa_first_names",
        urls=[SSA_NAMES_URL, SSA_MIRROR_URL],
        refresh=args.refresh,
    )
    source = SourceFile(
        source_key="ssa_first_names",
        source_name="Social Security Administration national baby names",
        source_url=SSA_NAMES_URL,
        used_url=used_url,
        notes=(
            "Counts are aggregated from national baby-name files. If ssa.gov "
            "blocks automated download, the builder falls back to a mirror of "
            "the same SSA files."
        ),
    )
    stats: dict[tuple[str, str], FirstNameStats] = {}
    end_year = args.first_end_year
    for raw_name, gender, count, year in read_ssa_names(data, used_url):
        if year < args.first_start_year:
            continue
        if end_year and year > end_year:
            continue
        name = display_case_name(raw_name)
        if not usable_name(name):
            continue
        gender = gender.upper()
        key = (normalize_name_key(name), gender)
        entry = stats.setdefault(key, FirstNameStats(name=name, gender=gender))
        entry.ssa_births += count
        if year >= args.recent_start_year:
            entry.ssa_recent_births += count
        entry.first_year = year if entry.first_year is None else min(entry.first_year, year)
        entry.last_year = year if entry.last_year is None else max(entry.last_year, year)
    return stats, source


def load_last_names(args: argparse.Namespace) -> tuple[dict[str, LastNameStats], SourceFile]:
    data, used_url = fetch_bytes(
        cache_dir=args.cache_dir,
        cache_name="census_surnames.zip",
        urls=[CENSUS_SURNAMES_URL],
        refresh=args.refresh,
    )
    source = SourceFile(
        source_key="census_surnames",
        source_name="U.S. Census 2010 frequently occurring surnames",
        source_url=CENSUS_SURNAMES_URL,
        used_url=used_url,
        notes="Includes surnames occurring at least 100 times in the 2010 Census.",
    )
    stats: dict[str, LastNameStats] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        csv_name = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
        with archive.open(csv_name) as handle:
            text = io.TextIOWrapper(handle, encoding="utf-8-sig")
            for row in csv.DictReader(text):
                name = display_case_name(row["name"])
                if not usable_name(name):
                    continue
                key = normalize_name_key(name)
                stats[key] = LastNameStats(
                    name=name,
                    census_count=int(row["count"]),
                    census_rank=int(row["rank"]),
                )
    return stats, source


def load_football_names(
    args: argparse.Namespace,
    first_names: dict[tuple[str, str], FirstNameStats],
    last_names: dict[str, LastNameStats],
) -> tuple[list[dict[str, object]], SourceFile]:
    data, used_url = fetch_bytes(
        cache_dir=args.cache_dir,
        cache_name="nflverse_players.csv",
        urls=[NFLVERSE_PLAYERS_URL],
        refresh=args.refresh,
    )
    source = SourceFile(
        source_key="nflverse_players",
        source_name="nflverse player information",
        source_url=NFLVERSE_PLAYERS_URL,
        used_url=used_url,
        notes="Used for football-flavored first and last name components.",
    )
    rows: list[dict[str, object]] = []
    text = io.StringIO(data.decode("utf-8-sig"))
    for row in csv.DictReader(text):
        first = display_case_name(row.get("common_first_name") or row.get("first_name") or "")
        last = display_case_name(row.get("last_name") or "")
        if not usable_name(first) or not usable_name(last):
            continue
        try:
            last_season = int(row["last_season"]) if row.get("last_season") else None
        except ValueError:
            last_season = None
        is_active = int(last_season is not None and last_season >= args.football_active_since)

        first_key = (normalize_name_key(first), "M")
        first_entry = first_names.setdefault(
            first_key,
            FirstNameStats(name=first, gender="M"),
        )
        first_entry.football_count += 1
        first_entry.football_active_count += is_active

        last_key = normalize_name_key(last)
        last_entry = last_names.setdefault(last_key, LastNameStats(name=last))
        last_entry.football_count += 1
        last_entry.football_active_count += is_active

        rows.append(
            {
                "display_name": row.get("display_name") or f"{first} {last}",
                "first_name": first,
                "last_name": last,
                "normalized_full_name": normalize_name_key(f"{first} {last}"),
                "position": row.get("position") or None,
                "position_group": row.get("position_group") or None,
                "rookie_season": row.get("rookie_season") or None,
                "last_season": last_season,
                "latest_team": row.get("latest_team") or None,
                "status": row.get("status") or None,
                "is_active": is_active,
            }
        )
    return rows, source


def load_diversity_config(
    config_path: Path,
) -> tuple[list[EthnicityProfile], list[OriginCountry], list[StyleComponent], SourceFile]:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    ethnicity_profiles = [
        EthnicityProfile(
            key=row["key"],
            label=row["label"],
            target_pct=float(row["target_pct"]),
            sigma_pct=float(row["sigma_pct"]),
            min_pct=float(row["min_pct"]),
            max_pct=float(row["max_pct"]),
            first_styles={str(k): int(v) for k, v in row["first_styles"].items()},
            last_styles={str(k): int(v) for k, v in row["last_styles"].items()},
        )
        for row in data["ethnicity_profiles"]
    ]
    origin_countries = [
        OriginCountry(
            country=row["country"],
            weight=int(row["weight"]),
            ethnicity_key=row["ethnicity_key"],
            common_positions=row.get("common_positions", ""),
            first_styles={str(k): int(v) for k, v in row["first_styles"].items()},
            last_styles={str(k): int(v) for k, v in row["last_styles"].items()},
        )
        for row in data["international_origins"]
    ]
    style_components: list[StyleComponent] = []
    for style_key, style_data in data["styles"].items():
        for component_type in ("first", "last"):
            for name, weight in style_data.get(component_type, {}).items():
                display_name = display_case_name(name)
                if usable_name(display_name):
                    style_components.append(
                        StyleComponent(
                            style_key=style_key,
                            component_type=component_type,
                            name=display_name,
                            weight=int(weight),
                        )
                    )
    source = SourceFile(
        source_key="draft_name_diversity_config",
        source_name="NFL GM Sim draft ethnicity and origin name configuration",
        source_url=str(config_path),
        used_url=str(config_path),
        notes=(
            "Curated gameplay configuration for fictional prospect ethnicity, "
            "international origin, and cultural name-style sampling."
        ),
    )
    return ethnicity_profiles, origin_countries, style_components, source


def first_name_weight(row: FirstNameStats) -> int:
    return max(
        1,
        int(row.ssa_recent_births + row.ssa_births * 0.15)
        + row.football_count * 500
        + row.football_active_count * 1000,
    )


def last_name_weight(row: LastNameStats) -> int:
    return max(
        1,
        row.census_count
        + row.football_count * 2500
        + row.football_active_count * 5000,
    )


def football_weight(count: int, active_count: int) -> int:
    return max(0, count * 100 + active_count * 250)


def source_flags(*, has_us: bool, football_count: int) -> str:
    flags = []
    if has_us:
        flags.append("us")
    if football_count:
        flags.append("football")
    return ",".join(flags) if flags else "unknown"


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        PRAGMA foreign_keys = ON;

        DROP VIEW IF EXISTS name_pool_summary_view;
        DROP VIEW IF EXISTS football_name_component_view;
        DROP VIEW IF EXISTS ethnicity_mix_target_view;
        DROP TABLE IF EXISTS build_metadata;
        DROP TABLE IF EXISTS name_sources;
        DROP TABLE IF EXISTS ethnicity_name_style_weights;
        DROP TABLE IF EXISTS country_name_style_weights;
        DROP TABLE IF EXISTS name_style_components;
        DROP TABLE IF EXISTS international_origin_countries;
        DROP TABLE IF EXISTS ethnicity_profiles;
        DROP TABLE IF EXISTS first_names;
        DROP TABLE IF EXISTS last_names;
        DROP TABLE IF EXISTS football_player_names;

        CREATE TABLE name_sources (
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

        CREATE TABLE first_names (
            name_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            gender TEXT NOT NULL,
            ssa_births INTEGER NOT NULL DEFAULT 0,
            ssa_recent_births INTEGER NOT NULL DEFAULT 0,
            first_year INTEGER,
            last_year INTEGER,
            football_count INTEGER NOT NULL DEFAULT 0,
            football_active_count INTEGER NOT NULL DEFAULT 0,
            weight INTEGER NOT NULL,
            football_weight INTEGER NOT NULL,
            source_flags TEXT NOT NULL,
            UNIQUE(normalized_name, gender)
        );

        CREATE TABLE last_names (
            name_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL UNIQUE,
            census_count INTEGER NOT NULL DEFAULT 0,
            census_rank INTEGER,
            football_count INTEGER NOT NULL DEFAULT 0,
            football_active_count INTEGER NOT NULL DEFAULT 0,
            weight INTEGER NOT NULL,
            football_weight INTEGER NOT NULL,
            source_flags TEXT NOT NULL
        );

        CREATE TABLE football_player_names (
            football_name_id INTEGER PRIMARY KEY AUTOINCREMENT,
            display_name TEXT NOT NULL,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            normalized_full_name TEXT NOT NULL,
            position TEXT,
            position_group TEXT,
            rookie_season INTEGER,
            last_season INTEGER,
            latest_team TEXT,
            status TEXT,
            is_active INTEGER NOT NULL DEFAULT 0,
            UNIQUE(normalized_full_name, rookie_season, last_season, position)
        );

        CREATE TABLE ethnicity_profiles (
            ethnicity_key TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            target_pct REAL NOT NULL,
            sigma_pct REAL NOT NULL,
            min_pct REAL NOT NULL,
            max_pct REAL NOT NULL
        );

        CREATE TABLE international_origin_countries (
            country TEXT PRIMARY KEY,
            weight INTEGER NOT NULL,
            ethnicity_key TEXT NOT NULL REFERENCES ethnicity_profiles(ethnicity_key),
            common_positions TEXT
        );

        CREATE TABLE ethnicity_name_style_weights (
            ethnicity_key TEXT NOT NULL REFERENCES ethnicity_profiles(ethnicity_key),
            component_type TEXT NOT NULL CHECK (component_type IN ('first', 'last')),
            style_key TEXT NOT NULL,
            weight INTEGER NOT NULL,
            PRIMARY KEY (ethnicity_key, component_type, style_key)
        );

        CREATE TABLE country_name_style_weights (
            country TEXT NOT NULL REFERENCES international_origin_countries(country),
            component_type TEXT NOT NULL CHECK (component_type IN ('first', 'last')),
            style_key TEXT NOT NULL,
            weight INTEGER NOT NULL,
            PRIMARY KEY (country, component_type, style_key)
        );

        CREATE TABLE name_style_components (
            component_id INTEGER PRIMARY KEY AUTOINCREMENT,
            style_key TEXT NOT NULL,
            component_type TEXT NOT NULL CHECK (component_type IN ('first', 'last')),
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            weight INTEGER NOT NULL,
            UNIQUE(style_key, component_type, normalized_name)
        );

        CREATE INDEX idx_first_names_weight
            ON first_names(gender, weight DESC);

        CREATE INDEX idx_first_names_football
            ON first_names(gender, football_weight DESC);

        CREATE INDEX idx_last_names_weight
            ON last_names(weight DESC);

        CREATE INDEX idx_last_names_football
            ON last_names(football_weight DESC);

        CREATE INDEX idx_style_components
            ON name_style_components(style_key, component_type, weight DESC);

        CREATE INDEX idx_origin_countries_weight
            ON international_origin_countries(weight DESC);

        CREATE VIEW name_pool_summary_view AS
        SELECT 'first_names' AS category, COUNT(*) AS row_count,
               SUM(CASE WHEN source_flags LIKE '%football%' THEN 1 ELSE 0 END) AS football_component_count
        FROM first_names
        UNION ALL
        SELECT 'last_names', COUNT(*),
               SUM(CASE WHEN source_flags LIKE '%football%' THEN 1 ELSE 0 END)
        FROM last_names
        UNION ALL
        SELECT 'football_player_names', COUNT(*), SUM(is_active)
        FROM football_player_names
        UNION ALL
        SELECT 'ethnicity_profiles', COUNT(*), NULL
        FROM ethnicity_profiles
        UNION ALL
        SELECT 'international_origin_countries', COUNT(*), NULL
        FROM international_origin_countries
        UNION ALL
        SELECT 'name_style_components', COUNT(*), NULL
        FROM name_style_components;

        CREATE VIEW football_name_component_view AS
        SELECT 'first' AS component, name, football_count, football_active_count
        FROM first_names
        WHERE football_count > 0
        UNION ALL
        SELECT 'last', name, football_count, football_active_count
        FROM last_names
        WHERE football_count > 0;

        CREATE VIEW ethnicity_mix_target_view AS
        SELECT
            ethnicity_key,
            label,
            target_pct,
            sigma_pct,
            min_pct,
            max_pct
        FROM ethnicity_profiles
        ORDER BY target_pct DESC;
        """
    )


def insert_sources(con: sqlite3.Connection, sources: list[SourceFile]) -> None:
    con.executemany(
        """
        INSERT INTO name_sources (
            source_key, source_name, source_url, used_url, notes, fetched_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                source.source_key,
                source.source_name,
                source.source_url,
                source.used_url,
                source.notes,
                now_utc(),
            )
            for source in sources
        ],
    )


def insert_diversity_config(
    con: sqlite3.Connection,
    ethnicity_profiles: list[EthnicityProfile],
    origin_countries: list[OriginCountry],
    style_components: list[StyleComponent],
) -> None:
    con.executemany(
        """
        INSERT INTO ethnicity_profiles (
            ethnicity_key, label, target_pct, sigma_pct, min_pct, max_pct
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row.key,
                row.label,
                row.target_pct,
                row.sigma_pct,
                row.min_pct,
                row.max_pct,
            )
            for row in ethnicity_profiles
        ],
    )
    con.executemany(
        """
        INSERT INTO international_origin_countries (
            country, weight, ethnicity_key, common_positions
        )
        VALUES (?, ?, ?, ?)
        """,
        [
            (
                row.country,
                row.weight,
                row.ethnicity_key,
                row.common_positions,
            )
            for row in origin_countries
        ],
    )
    con.executemany(
        """
        INSERT INTO ethnicity_name_style_weights (
            ethnicity_key, component_type, style_key, weight
        )
        VALUES (?, ?, ?, ?)
        """,
        [
            (row.key, component_type, style_key, weight)
            for row in ethnicity_profiles
            for component_type, styles in (
                ("first", row.first_styles),
                ("last", row.last_styles),
            )
            for style_key, weight in styles.items()
        ],
    )
    con.executemany(
        """
        INSERT INTO country_name_style_weights (
            country, component_type, style_key, weight
        )
        VALUES (?, ?, ?, ?)
        """,
        [
            (row.country, component_type, style_key, weight)
            for row in origin_countries
            for component_type, styles in (
                ("first", row.first_styles),
                ("last", row.last_styles),
            )
            for style_key, weight in styles.items()
        ],
    )
    con.executemany(
        """
        INSERT INTO name_style_components (
            style_key, component_type, name, normalized_name, weight
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                row.style_key,
                row.component_type,
                row.name,
                normalize_name_key(row.name),
                row.weight,
            )
            for row in style_components
        ],
    )


def insert_names(
    con: sqlite3.Connection,
    args: argparse.Namespace,
    first_names: dict[tuple[str, str], FirstNameStats],
    last_names: dict[str, LastNameStats],
    football_rows: list[dict[str, object]],
) -> None:
    first_rows = sorted(
        first_names.values(),
        key=lambda row: (row.football_count > 0, first_name_weight(row)),
        reverse=True,
    )
    first_rows = [
        row
        for row in first_rows[: args.max_first_names]
        if row.ssa_births > 0 or row.football_count > 0
    ]
    last_rows = sorted(
        last_names.values(),
        key=lambda row: (row.football_count > 0, last_name_weight(row)),
        reverse=True,
    )
    last_rows = [
        row
        for row in last_rows[: args.max_last_names]
        if row.census_count > 0 or row.football_count > 0
    ]

    con.executemany(
        """
        INSERT INTO first_names (
            name, normalized_name, gender, ssa_births, ssa_recent_births,
            first_year, last_year, football_count, football_active_count,
            weight, football_weight, source_flags
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row.name,
                normalize_name_key(row.name),
                row.gender,
                row.ssa_births,
                row.ssa_recent_births,
                row.first_year,
                row.last_year,
                row.football_count,
                row.football_active_count,
                first_name_weight(row),
                football_weight(row.football_count, row.football_active_count),
                source_flags(has_us=row.ssa_births > 0, football_count=row.football_count),
            )
            for row in first_rows
        ],
    )
    con.executemany(
        """
        INSERT INTO last_names (
            name, normalized_name, census_count, census_rank, football_count,
            football_active_count, weight, football_weight, source_flags
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row.name,
                normalize_name_key(row.name),
                row.census_count,
                row.census_rank,
                row.football_count,
                row.football_active_count,
                last_name_weight(row),
                football_weight(row.football_count, row.football_active_count),
                source_flags(has_us=row.census_count > 0, football_count=row.football_count),
            )
            for row in last_rows
        ],
    )
    con.executemany(
        """
        INSERT OR IGNORE INTO football_player_names (
            display_name, first_name, last_name, normalized_full_name,
            position, position_group, rookie_season, last_season,
            latest_team, status, is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["display_name"],
                row["first_name"],
                row["last_name"],
                row["normalized_full_name"],
                row["position"],
                row["position_group"],
                row["rookie_season"],
                row["last_season"],
                row["latest_team"],
                row["status"],
                row["is_active"],
            )
            for row in football_rows
        ],
    )
    con.executemany(
        "INSERT INTO build_metadata (key, value) VALUES (?, ?)",
        [
            ("built_at", now_utc()),
            ("first_start_year", str(args.first_start_year)),
            ("recent_start_year", str(args.recent_start_year)),
            ("first_end_year", str(args.first_end_year or "")),
            ("football_active_since", str(args.football_active_since)),
            ("max_first_names", str(args.max_first_names)),
            ("max_last_names", str(args.max_last_names)),
            ("diversity_config", str(args.diversity_config)),
        ],
    )


def cmd_build(args: argparse.Namespace) -> None:
    args.db.parent.mkdir(parents=True, exist_ok=True)
    first_names, first_source = load_first_names(args)
    last_names, last_source = load_last_names(args)
    football_rows, football_source = load_football_names(args, first_names, last_names)
    diversity_profiles, origin_countries, style_components, diversity_source = (
        load_diversity_config(args.diversity_config)
    )

    with sqlite3.connect(args.db) as con:
        create_schema(con)
        insert_sources(con, [first_source, last_source, football_source, diversity_source])
        insert_diversity_config(
            con,
            diversity_profiles,
            origin_countries,
            style_components,
        )
        insert_names(con, args, first_names, last_names, football_rows)
        con.commit()
    print(f"Built name pool: {args.db}")
    print_summary(args.db)


def print_summary(db_path: Path) -> None:
    with sqlite3.connect(db_path) as con:
        for category, row_count, football_count in con.execute(
            "SELECT category, row_count, football_component_count FROM name_pool_summary_view"
        ):
            if football_count is None:
                print(f"{category}: {row_count} rows")
            else:
                print(f"{category}: {row_count} rows ({football_count or 0} football-linked)")


def cmd_summary(args: argparse.Namespace) -> None:
    print_summary(args.db)


def cmd_sample(args: argparse.Namespace) -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from engine.draft.names import NameGenerator

    generator = NameGenerator(args.db, seed=args.seed)
    for _ in range(args.count):
        generated = generator.generate(
            football_bias=args.football_bias,
            international_chance=args.international_chance,
        )
        if args.show_meta:
            origin = generated.country if generated.is_international else "United States"
            print(f"{generated.full_name} | {generated.ethnicity_label} | {origin}")
        else:
            print(generated.full_name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build or rebuild the name pool")
    build_parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    build_parser.add_argument("--refresh", action="store_true")
    build_parser.add_argument("--diversity-config", type=Path, default=DEFAULT_DIVERSITY_CONFIG)
    build_parser.add_argument("--first-start-year", type=int, default=1960)
    build_parser.add_argument("--first-end-year", type=int)
    build_parser.add_argument("--recent-start-year", type=int, default=1995)
    build_parser.add_argument("--football-active-since", type=int, default=2024)
    build_parser.add_argument("--max-first-names", type=int, default=12000)
    build_parser.add_argument("--max-last-names", type=int, default=25000)
    build_parser.set_defaults(func=cmd_build)

    summary_parser = subparsers.add_parser("summary", help="Show name-pool summary")
    summary_parser.set_defaults(func=cmd_summary)

    sample_parser = subparsers.add_parser("sample", help="Generate sample names")
    sample_parser.add_argument("--count", type=int, default=20)
    sample_parser.add_argument("--seed")
    sample_parser.add_argument("--football-bias", type=float, default=0.35)
    sample_parser.add_argument("--international-chance", type=float, default=0.035)
    sample_parser.add_argument("--show-meta", action="store_true")
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
