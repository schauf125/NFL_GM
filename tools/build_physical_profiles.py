#!/usr/bin/env python3
"""Build and sample position-specific draft physical profiles."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "draft" / "physical" / "physical_profiles.db"
DEFAULT_CACHE_DIR = ROOT / "data" / "draft" / "physical" / ".cache"
DEFAULT_POSITION_MAP = ROOT / "data" / "draft" / "physical" / "position_mapping.json"
NFLVERSE_PLAYERS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
)
COMBINE_MEASUREMENTS_URL = (
    "https://raw.githubusercontent.com/array-carpenter/nfl-draft-data/master/data/combine_pro_day.csv"
)
USER_AGENT = "NFL-GM-Sim physical-profile builder/0.1"


@dataclass(frozen=True)
class PositionProfile:
    position: str
    source_positions: str
    sample_size: int
    height_mean: float
    height_sd: float
    height_min: int
    height_p01: int
    height_p05: int
    height_p50: int
    height_p95: int
    height_p99: int
    height_max: int
    weight_mean: float
    weight_sd: float
    weight_min: int
    weight_p01: int
    weight_p05: int
    weight_p50: int
    weight_p95: int
    weight_p99: int
    weight_max: int
    height_weight_corr: float
    measurement_sample_size: int
    arm_length_mean: float
    arm_length_sd: float
    arm_length_min: float
    arm_length_p01: float
    arm_length_p05: float
    arm_length_p50: float
    arm_length_p95: float
    arm_length_p99: float
    arm_length_max: float
    hand_size_mean: float
    hand_size_sd: float
    hand_size_min: float
    hand_size_p01: float
    hand_size_p05: float
    hand_size_p50: float
    hand_size_p95: float
    hand_size_p99: float
    hand_size_max: float
    arm_height_corr: float
    arm_weight_corr: float
    hand_height_corr: float
    hand_weight_corr: float
    gen_height_min: int
    gen_height_max: int
    gen_weight_min: int
    gen_weight_max: int
    gen_arm_length_min: float
    gen_arm_length_max: float
    gen_hand_size_min: float
    gen_hand_size_max: float


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_bytes(
    *,
    cache_dir: Path,
    cache_name: str,
    url: str,
    refresh: bool,
) -> tuple[bytes, str]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / cache_name
    meta_path = cache_dir / f"{cache_name}.json"
    if cache_path.exists() and meta_path.exists() and not refresh:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return cache_path.read_bytes(), str(meta["used_url"])

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
    except (OSError, urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise RuntimeError(f"Unable to download {url}: {exc}") from exc
    cache_path.write_bytes(data)
    meta_path.write_text(
        json.dumps({"used_url": url, "fetched_at": now_utc()}, indent=2),
        encoding="utf-8",
    )
    return data, url


def percentile(values: list[int], pct: float) -> int:
    if not values:
        raise ValueError("Cannot compute percentile of an empty list")
    ordered = sorted(values)
    index = round((len(ordered) - 1) * pct)
    return int(ordered[index])


def percentile_float(values: list[float], pct: float) -> float:
    if not values:
        raise ValueError("Cannot compute percentile of an empty list")
    ordered = sorted(values)
    index = round((len(ordered) - 1) * pct)
    return float(ordered[index])


def mean(values: list[int] | list[float]) -> float:
    return sum(values) / len(values)


def population_sd(values: list[int] | list[float], avg: float) -> float:
    if len(values) < 2:
        return 1.0
    variance = sum((value - avg) ** 2 for value in values) / len(values)
    return max(0.1, math.sqrt(variance))


def correlation(
    xs: list[int] | list[float],
    ys: list[int] | list[float],
    x_avg: float,
    y_avg: float,
) -> float:
    if len(xs) < 2:
        return 0.0
    x_sd = population_sd(xs, x_avg)
    y_sd = population_sd(ys, y_avg)
    if x_sd <= 0 or y_sd <= 0:
        return 0.0
    cov = sum((x - x_avg) * (y - y_avg) for x, y in zip(xs, ys)) / len(xs)
    return max(-0.95, min(0.95, cov / (x_sd * y_sd)))


def padded_bounds(
    values: list[float],
    *,
    low_floor: float,
    high_ceiling: float,
    pad: float,
) -> tuple[float, float]:
    low = percentile_float(values, 0.01) - pad
    high = percentile_float(values, 0.99) + pad
    return max(low_floor, low), min(high_ceiling, high)


def load_mapping(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_player_measurements(args: argparse.Namespace) -> tuple[list[dict[str, object]], str]:
    data, used_url = fetch_bytes(
        cache_dir=args.cache_dir,
        cache_name="nflverse_players.csv",
        url=NFLVERSE_PLAYERS_URL,
        refresh=args.refresh,
    )
    rows: list[dict[str, object]] = []
    text = io.StringIO(data.decode("utf-8-sig"))
    for row in csv.DictReader(text):
        try:
            height = int(float(row["height"]))
            weight = int(float(row["weight"]))
            last_season = int(row["last_season"]) if row.get("last_season") else 0
        except (TypeError, ValueError):
            continue
        if height <= 0 or weight <= 0:
            continue
        if last_season < args.min_last_season:
            continue
        rows.append(
            {
                "position": row.get("position") or "",
                "height": height,
                "weight": weight,
                "last_season": last_season,
            }
        )
    return rows, used_url


def load_combine_measurements(args: argparse.Namespace) -> tuple[list[dict[str, object]], str]:
    data, used_url = fetch_bytes(
        cache_dir=args.cache_dir,
        cache_name="combine_pro_day.csv",
        url=COMBINE_MEASUREMENTS_URL,
        refresh=args.refresh,
    )
    rows: list[dict[str, object]] = []
    text = io.StringIO(data.decode("utf-8-sig"))
    for row in csv.DictReader(text):
        try:
            year = int(row["Year"])
            height = float(row["Height (in)"])
            weight = float(row["Weight (lbs)"])
            arm_length = float(row["Arm Length (in)"])
            hand_size = float(row["Hand Size (in)"])
        except (TypeError, ValueError):
            continue
        if year < args.min_measurement_year:
            continue
        if not (60 <= height <= 85 and 150 <= weight <= 400):
            continue
        if not (25 <= arm_length <= 39 and 6.5 <= hand_size <= 13):
            continue
        rows.append(
            {
                "position": row.get("POS") or "",
                "position_group": row.get("POS_GP") or "",
                "height": height,
                "weight": weight,
                "arm_length": arm_length,
                "hand_size": hand_size,
                "year": year,
            }
        )
    return rows, used_url


def build_profiles(
    measurements: list[dict[str, object]],
    combine_measurements: list[dict[str, object]],
    mapping: dict[str, object],
    *,
    min_sample_size: int,
    min_measurement_sample_size: int,
) -> list[PositionProfile]:
    profiles: list[PositionProfile] = []
    position_map = mapping["positions"]
    for position, config in position_map.items():
        source_positions = set(config["source_positions"])
        rows = [
            row
            for row in measurements
            if row["position"] in source_positions
        ]
        if len(rows) < min_sample_size:
            raise ValueError(
                f"Not enough measurements for {position}: {len(rows)} < {min_sample_size}"
            )
        combine_rows = [
            row
            for row in combine_measurements
            if row["position"] in source_positions
            or row["position_group"] in source_positions
        ]
        if len(combine_rows) < min_measurement_sample_size:
            raise ValueError(
                f"Not enough arm/hand measurements for {position}: "
                f"{len(combine_rows)} < {min_measurement_sample_size}"
            )
        heights = [int(row["height"]) for row in rows]
        weights = [int(row["weight"]) for row in rows]
        measured_heights = [float(row["height"]) for row in combine_rows]
        measured_weights = [float(row["weight"]) for row in combine_rows]
        arms = [float(row["arm_length"]) for row in combine_rows]
        hands = [float(row["hand_size"]) for row in combine_rows]
        height_avg = mean(heights)
        weight_avg = mean(weights)
        height_sd = population_sd(heights, height_avg)
        weight_sd = population_sd(weights, weight_avg)
        measured_height_avg = mean(measured_heights)
        measured_weight_avg = mean(measured_weights)
        arm_avg = mean(arms)
        hand_avg = mean(hands)
        arm_sd = population_sd(arms, arm_avg)
        hand_sd = population_sd(hands, hand_avg)
        height_bounds = config["height_bounds"]
        weight_bounds = config["weight_bounds"]
        arm_bounds = padded_bounds(arms, low_floor=27.0, high_ceiling=38.5, pad=0.75)
        hand_bounds = padded_bounds(hands, low_floor=7.0, high_ceiling=12.25, pad=0.375)
        profiles.append(
            PositionProfile(
                position=position,
                source_positions=",".join(config["source_positions"]),
                sample_size=len(rows),
                height_mean=height_avg,
                height_sd=height_sd,
                height_min=min(heights),
                height_p01=percentile(heights, 0.01),
                height_p05=percentile(heights, 0.05),
                height_p50=percentile(heights, 0.50),
                height_p95=percentile(heights, 0.95),
                height_p99=percentile(heights, 0.99),
                height_max=max(heights),
                weight_mean=weight_avg,
                weight_sd=weight_sd,
                weight_min=min(weights),
                weight_p01=percentile(weights, 0.01),
                weight_p05=percentile(weights, 0.05),
                weight_p50=percentile(weights, 0.50),
                weight_p95=percentile(weights, 0.95),
                weight_p99=percentile(weights, 0.99),
                weight_max=max(weights),
                height_weight_corr=correlation(heights, weights, height_avg, weight_avg),
                measurement_sample_size=len(combine_rows),
                arm_length_mean=arm_avg,
                arm_length_sd=arm_sd,
                arm_length_min=min(arms),
                arm_length_p01=percentile_float(arms, 0.01),
                arm_length_p05=percentile_float(arms, 0.05),
                arm_length_p50=percentile_float(arms, 0.50),
                arm_length_p95=percentile_float(arms, 0.95),
                arm_length_p99=percentile_float(arms, 0.99),
                arm_length_max=max(arms),
                hand_size_mean=hand_avg,
                hand_size_sd=hand_sd,
                hand_size_min=min(hands),
                hand_size_p01=percentile_float(hands, 0.01),
                hand_size_p05=percentile_float(hands, 0.05),
                hand_size_p50=percentile_float(hands, 0.50),
                hand_size_p95=percentile_float(hands, 0.95),
                hand_size_p99=percentile_float(hands, 0.99),
                hand_size_max=max(hands),
                arm_height_corr=correlation(
                    arms,
                    measured_heights,
                    arm_avg,
                    measured_height_avg,
                ),
                arm_weight_corr=correlation(
                    arms,
                    measured_weights,
                    arm_avg,
                    measured_weight_avg,
                ),
                hand_height_corr=correlation(
                    hands,
                    measured_heights,
                    hand_avg,
                    measured_height_avg,
                ),
                hand_weight_corr=correlation(
                    hands,
                    measured_weights,
                    hand_avg,
                    measured_weight_avg,
                ),
                gen_height_min=int(height_bounds[0]),
                gen_height_max=int(height_bounds[1]),
                gen_weight_min=int(weight_bounds[0]),
                gen_weight_max=int(weight_bounds[1]),
                gen_arm_length_min=arm_bounds[0],
                gen_arm_length_max=arm_bounds[1],
                gen_hand_size_min=hand_bounds[0],
                gen_hand_size_max=hand_bounds[1],
            )
        )
    return profiles


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        DROP VIEW IF EXISTS physical_profile_summary_view;
        DROP TABLE IF EXISTS physical_profile_sources;
        DROP TABLE IF EXISTS physical_profiles;
        DROP TABLE IF EXISTS build_metadata;

        CREATE TABLE physical_profile_sources (
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

        CREATE TABLE physical_profiles (
            position TEXT PRIMARY KEY,
            source_positions TEXT NOT NULL,
            sample_size INTEGER NOT NULL,
            height_mean REAL NOT NULL,
            height_sd REAL NOT NULL,
            height_min INTEGER NOT NULL,
            height_p01 INTEGER NOT NULL,
            height_p05 INTEGER NOT NULL,
            height_p50 INTEGER NOT NULL,
            height_p95 INTEGER NOT NULL,
            height_p99 INTEGER NOT NULL,
            height_max INTEGER NOT NULL,
            weight_mean REAL NOT NULL,
            weight_sd REAL NOT NULL,
            weight_min INTEGER NOT NULL,
            weight_p01 INTEGER NOT NULL,
            weight_p05 INTEGER NOT NULL,
            weight_p50 INTEGER NOT NULL,
            weight_p95 INTEGER NOT NULL,
            weight_p99 INTEGER NOT NULL,
            weight_max INTEGER NOT NULL,
            height_weight_corr REAL NOT NULL,
            measurement_sample_size INTEGER NOT NULL,
            arm_length_mean REAL NOT NULL,
            arm_length_sd REAL NOT NULL,
            arm_length_min REAL NOT NULL,
            arm_length_p01 REAL NOT NULL,
            arm_length_p05 REAL NOT NULL,
            arm_length_p50 REAL NOT NULL,
            arm_length_p95 REAL NOT NULL,
            arm_length_p99 REAL NOT NULL,
            arm_length_max REAL NOT NULL,
            hand_size_mean REAL NOT NULL,
            hand_size_sd REAL NOT NULL,
            hand_size_min REAL NOT NULL,
            hand_size_p01 REAL NOT NULL,
            hand_size_p05 REAL NOT NULL,
            hand_size_p50 REAL NOT NULL,
            hand_size_p95 REAL NOT NULL,
            hand_size_p99 REAL NOT NULL,
            hand_size_max REAL NOT NULL,
            arm_height_corr REAL NOT NULL,
            arm_weight_corr REAL NOT NULL,
            hand_height_corr REAL NOT NULL,
            hand_weight_corr REAL NOT NULL,
            gen_height_min INTEGER NOT NULL,
            gen_height_max INTEGER NOT NULL,
            gen_weight_min INTEGER NOT NULL,
            gen_weight_max INTEGER NOT NULL,
            gen_arm_length_min REAL NOT NULL,
            gen_arm_length_max REAL NOT NULL,
            gen_hand_size_min REAL NOT NULL,
            gen_hand_size_max REAL NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE VIEW physical_profile_summary_view AS
        SELECT
            position,
            source_positions,
            sample_size,
            ROUND(height_mean, 1) AS height_mean,
            ROUND(height_sd, 2) AS height_sd,
            height_p05,
            height_p50,
            height_p95,
            ROUND(weight_mean, 1) AS weight_mean,
            ROUND(weight_sd, 2) AS weight_sd,
            weight_p05,
            weight_p50,
            weight_p95,
            ROUND(height_weight_corr, 3) AS height_weight_corr,
            measurement_sample_size,
            ROUND(arm_length_mean, 2) AS arm_length_mean,
            ROUND(arm_length_sd, 2) AS arm_length_sd,
            ROUND(arm_length_p05, 2) AS arm_length_p05,
            ROUND(arm_length_p95, 2) AS arm_length_p95,
            ROUND(hand_size_mean, 2) AS hand_size_mean,
            ROUND(hand_size_sd, 2) AS hand_size_sd,
            ROUND(hand_size_p05, 2) AS hand_size_p05,
            ROUND(hand_size_p95, 2) AS hand_size_p95,
            ROUND(arm_height_corr, 3) AS arm_height_corr,
            ROUND(hand_height_corr, 3) AS hand_height_corr
        FROM physical_profiles
        ORDER BY
            CASE position
                WHEN 'QB' THEN 1 WHEN 'RB' THEN 2 WHEN 'FB' THEN 3
                WHEN 'WR' THEN 4 WHEN 'TE' THEN 5
                WHEN 'OT' THEN 6 WHEN 'OG' THEN 7 WHEN 'C' THEN 8
                WHEN 'IDL' THEN 9 WHEN 'EDGE' THEN 10 WHEN 'ILB' THEN 11
                WHEN 'CB' THEN 12 WHEN 'NB' THEN 13 WHEN 'FS' THEN 14 WHEN 'SS' THEN 15
                WHEN 'K' THEN 16 WHEN 'P' THEN 17 WHEN 'LS' THEN 18
                ELSE 99
            END;
        """
    )


def insert_profiles(
    con: sqlite3.Connection,
    *,
    profiles: list[PositionProfile],
    source_url: str,
    args: argparse.Namespace,
) -> None:
    con.execute(
        """
        INSERT INTO physical_profile_sources (
            source_key, source_name, source_url, used_url, notes, fetched_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "nflverse_players",
            "nflverse player information",
            NFLVERSE_PLAYERS_URL,
            source_url,
            "Height and weight distributions by mapped position.",
            now_utc(),
        ),
    )
    con.execute(
        """
        INSERT INTO physical_profile_sources (
            source_key, source_name, source_url, used_url, notes, fetched_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "draft_combine_pro_day_measurements",
            "NFL draft combine and pro day measurements",
            COMBINE_MEASUREMENTS_URL,
            args.combine_source_url,
            "Arm length and hand size distributions by mapped position.",
            now_utc(),
        ),
    )
    con.executemany(
        """
        INSERT INTO physical_profiles (
            position, source_positions, sample_size,
            height_mean, height_sd, height_min, height_p01, height_p05, height_p50,
            height_p95, height_p99, height_max,
            weight_mean, weight_sd, weight_min, weight_p01, weight_p05, weight_p50,
            weight_p95, weight_p99, weight_max,
            height_weight_corr,
            measurement_sample_size,
            arm_length_mean, arm_length_sd, arm_length_min, arm_length_p01,
            arm_length_p05, arm_length_p50, arm_length_p95, arm_length_p99,
            arm_length_max,
            hand_size_mean, hand_size_sd, hand_size_min, hand_size_p01,
            hand_size_p05, hand_size_p50, hand_size_p95, hand_size_p99,
            hand_size_max,
            arm_height_corr, arm_weight_corr, hand_height_corr, hand_weight_corr,
            gen_height_min, gen_height_max, gen_weight_min, gen_weight_max,
            gen_arm_length_min, gen_arm_length_max, gen_hand_size_min, gen_hand_size_max
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            (
                profile.position,
                profile.source_positions,
                profile.sample_size,
                profile.height_mean,
                profile.height_sd,
                profile.height_min,
                profile.height_p01,
                profile.height_p05,
                profile.height_p50,
                profile.height_p95,
                profile.height_p99,
                profile.height_max,
                profile.weight_mean,
                profile.weight_sd,
                profile.weight_min,
                profile.weight_p01,
                profile.weight_p05,
                profile.weight_p50,
                profile.weight_p95,
                profile.weight_p99,
                profile.weight_max,
                profile.height_weight_corr,
                profile.measurement_sample_size,
                profile.arm_length_mean,
                profile.arm_length_sd,
                profile.arm_length_min,
                profile.arm_length_p01,
                profile.arm_length_p05,
                profile.arm_length_p50,
                profile.arm_length_p95,
                profile.arm_length_p99,
                profile.arm_length_max,
                profile.hand_size_mean,
                profile.hand_size_sd,
                profile.hand_size_min,
                profile.hand_size_p01,
                profile.hand_size_p05,
                profile.hand_size_p50,
                profile.hand_size_p95,
                profile.hand_size_p99,
                profile.hand_size_max,
                profile.arm_height_corr,
                profile.arm_weight_corr,
                profile.hand_height_corr,
                profile.hand_weight_corr,
                profile.gen_height_min,
                profile.gen_height_max,
                profile.gen_weight_min,
                profile.gen_weight_max,
                profile.gen_arm_length_min,
                profile.gen_arm_length_max,
                profile.gen_hand_size_min,
                profile.gen_hand_size_max,
            )
            for profile in profiles
        ],
    )
    con.executemany(
        "INSERT INTO build_metadata (key, value) VALUES (?, ?)",
        [
            ("built_at", now_utc()),
            ("min_last_season", str(args.min_last_season)),
            ("min_measurement_year", str(args.min_measurement_year)),
            ("min_sample_size", str(args.min_sample_size)),
            ("min_measurement_sample_size", str(args.min_measurement_sample_size)),
            ("position_map", str(args.position_map)),
        ],
    )


def cmd_build(args: argparse.Namespace) -> None:
    args.db.parent.mkdir(parents=True, exist_ok=True)
    mapping = load_mapping(args.position_map)
    if args.min_last_season is None:
        args.min_last_season = int(mapping.get("default_min_last_season", 2015))
    measurements, used_url = load_player_measurements(args)
    combine_measurements, combine_used_url = load_combine_measurements(args)
    args.combine_source_url = combine_used_url
    profiles = build_profiles(
        measurements,
        combine_measurements,
        mapping,
        min_sample_size=args.min_sample_size,
        min_measurement_sample_size=args.min_measurement_sample_size,
    )
    with sqlite3.connect(args.db) as con:
        create_schema(con)
        insert_profiles(con, profiles=profiles, source_url=used_url, args=args)
        con.commit()
    print(f"Built physical profiles: {args.db}")
    print_summary(args.db)


def print_summary(db_path: Path) -> None:
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            """
            SELECT position, sample_size, height_mean, height_sd, height_p05, height_p95,
                   weight_mean, weight_sd, weight_p05, weight_p95, height_weight_corr,
                   measurement_sample_size, arm_length_mean, arm_length_sd,
                   arm_length_p05, arm_length_p95, hand_size_mean, hand_size_sd,
                   hand_size_p05, hand_size_p95
            FROM physical_profile_summary_view
            """
        ).fetchall()
    for row in rows:
        print(
            f"{row[0]:4} n={row[1]:4} "
            f"h={row[2]:4.1f}+/-{row[3]:3.1f} p05-p95={row[4]}-{row[5]} "
            f"w={row[6]:5.1f}+/-{row[7]:4.1f} p05-p95={row[8]}-{row[9]} "
            f"arm={row[12]:5.2f}+/-{row[13]:4.2f} p05-p95={row[14]:.2f}-{row[15]:.2f} "
            f"hand={row[16]:4.2f}+/-{row[17]:4.2f} p05-p95={row[18]:.2f}-{row[19]:.2f} "
            f"meas_n={row[11]:4} corr={row[10]:.2f}"
        )


def cmd_summary(args: argparse.Namespace) -> None:
    print_summary(args.db)


def cmd_sample(args: argparse.Namespace) -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from engine.draft.physical import (
        PhysicalProfileGenerator,
        format_height,
        format_measurement,
    )

    generator = PhysicalProfileGenerator(args.db, seed=args.seed)
    for _ in range(args.count):
        traits = generator.generate(args.position, outlier_chance=args.outlier_chance)
        marker = " outlier" if traits.is_outlier else ""
        print(
            f"{args.position.upper():4} {format_height(traits.height_in):>4} "
            f"{traits.weight_lbs:3} lbs "
            f"arm {format_measurement(traits.arm_length_in):>7} "
            f"hand {format_measurement(traits.hand_size_in):>6}{marker}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build physical profile database")
    build_parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    build_parser.add_argument("--position-map", type=Path, default=DEFAULT_POSITION_MAP)
    build_parser.add_argument("--refresh", action="store_true")
    build_parser.add_argument("--min-last-season", type=int)
    build_parser.add_argument("--min-measurement-year", type=int, default=2015)
    build_parser.add_argument("--min-sample-size", type=int, default=40)
    build_parser.add_argument("--min-measurement-sample-size", type=int, default=10)
    build_parser.set_defaults(func=cmd_build)

    summary_parser = subparsers.add_parser("summary", help="Show physical profile summary")
    summary_parser.set_defaults(func=cmd_summary)

    sample_parser = subparsers.add_parser("sample", help="Generate sample traits")
    sample_parser.add_argument("position")
    sample_parser.add_argument("--count", type=int, default=20)
    sample_parser.add_argument("--seed")
    sample_parser.add_argument("--outlier-chance", type=float, default=0.045)
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
