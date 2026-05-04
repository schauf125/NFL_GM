#!/usr/bin/env python3
"""Dry-run one prototype tick-resolved passing play."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import match_engine, tick_engine  # noqa: E402


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def team_id(con: sqlite3.Connection, abbreviation: str) -> int:
    row = con.execute("SELECT team_id FROM teams WHERE abbreviation = ?", (abbreviation.upper(),)).fetchone()
    if not row:
        raise ValueError(f"Team not found: {abbreviation}")
    return int(row["team_id"])


def route_row(route: tick_engine.RouteTickState) -> dict[str, Any]:
    return {
        "receiver": route.receiver.name,
        "defender": route.defender.name,
        "slot": route.route_slot,
        "read_rank": route.read_rank,
        "role": route.route_role,
        "depth": route.depth,
        "break_tick": route.break_tick,
        "separation": round(route.separation, 2),
        "open_tick": route.open_tick,
        "open_score": round(route.final_open_score, 2),
        "priority": round(route.target_priority, 2),
    }


def result_payload(result: tick_engine.TickPassResult) -> dict[str, Any]:
    return {
        "concept": result.concept,
        "outcome": result.outcome,
        "yards": result.yards,
        "air_yards": result.air_yards,
        "yac_yards": result.yac_yards,
        "ticks_elapsed": result.ticks_elapsed,
        "time_elapsed_seconds": result.time_elapsed_seconds,
        "quarterback": result.quarterback.name,
        "qb_profile": result.qb_profile.as_dict(),
        "target": result.target.name if result.target else None,
        "defender": result.defender.name if result.defender else None,
        "rusher": result.rusher.name if result.rusher else None,
        "throw_tick": result.throw_tick,
        "pressure_tick": result.pressure_tick,
        "sack_tick": result.sack_tick,
        "completion_probability": round(result.completion_probability, 4),
        "interception_probability": round(result.interception_probability, 4),
        "pressure_score": round(result.pressure_score, 2),
        "best_open_score": round(result.best_open_score, 2),
        "description": result.description,
        "routes": [route_row(route) for route in result.routes],
        "events": [
            {
                "tick": event.tick,
                "time_seconds": event.time_seconds,
                "kind": event.kind,
                "description": event.description,
                "data": event.data,
            }
            for event in result.events
        ],
    }


def print_result(result: tick_engine.TickPassResult, *, show_events: bool, show_routes: bool) -> None:
    print("Tick Pass Play")
    print(f"Concept: {result.concept}")
    print(f"Outcome: {result.outcome}")
    print(f"Result: {result.description}")
    print(
        f"Time: {result.ticks_elapsed} ticks / {result.time_elapsed_seconds:.1f}s | "
        f"Yards: {result.yards} ({result.air_yards} air, {result.yac_yards} YAC)"
    )
    print(
        f"QB: {result.quarterback.name} | "
        f"Style: {result.qb_profile.label} | "
        f"Target: {result.target.name if result.target else '-'} | "
        f"Defender: {result.defender.name if result.defender else '-'} | "
        f"Rusher: {result.rusher.name if result.rusher else '-'}"
    )
    print(
        f"QB traits: rhythm {result.qb_profile.rhythm:.0f}, drift {result.qb_profile.pocket_drift:.0f}, "
        f"escape {result.qb_profile.pressure_escape:.0f}, broken-play {result.qb_profile.broken_play_creation:.0f}, "
        f"sack-risk {result.qb_profile.sack_risk:.0f}"
    )
    print(
        f"Throw tick: {result.throw_tick or '-'} | "
        f"Pressure tick: {result.pressure_tick or '-'} | "
        f"Best open score: {result.best_open_score:.2f}"
    )
    if result.completion_probability or result.interception_probability:
        print(
            f"Completion probability: {result.completion_probability:.1%} | "
            f"Interception probability: {result.interception_probability:.1%}"
        )
    if show_routes:
        print("")
        print("Routes")
        for route in sorted(result.routes, key=lambda item: item.target_priority, reverse=True):
            print(
                f"  {route.receiver.name:<24} vs {route.defender.name:<24} "
                f"{route.route_slot:<3} {route.route_role:<9} R{route.read_rank + 1} "
                f"depth {route.depth:>2}, break T{route.break_tick:>2}, "
                f"sep {route.separation:.2f}, open T{route.open_tick or '-'}"
            )
    if show_events:
        print("")
        print("Events")
        for event in result.events:
            print(f"  T{event.tick:>2} {event.time_seconds:>4.1f}s [{event.kind}] {event.description}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run one tick-resolved passing play.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("away")
    parser.add_argument("home")
    parser.add_argument("--season", type=int, default=match_engine.DEFAULT_SEASON)
    parser.add_argument("--down", type=int, default=1)
    parser.add_argument("--distance", type=int, default=10)
    parser.add_argument("--field-pos", type=int, default=25, help="Offense field position from own goal, 1-99.")
    parser.add_argument("--concept", choices=tick_engine.PASS_CONCEPTS)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--debug-ticks", action="store_true", help="Include every tick's route state in the event log.")
    parser.add_argument("--events", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--routes", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--json", type=Path, help="Write full result payload to JSON.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    with connect(args.db) as con:
        offense = match_engine.load_team(con, team_id(con, args.away), args.season)
        defense = match_engine.load_team(con, team_id(con, args.home), args.season)
    config = tick_engine.TickConfig(debug_ticks=args.debug_ticks)
    result = tick_engine.resolve_pass_tick(
        offense,
        defense,
        down=args.down,
        distance=args.distance,
        field_pos=args.field_pos,
        concept=args.concept,
        seed=args.seed,
        config=config,
    )
    print_result(result, show_events=args.events, show_routes=args.routes)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result_payload(result), indent=2), encoding="utf-8")
        print(f"\nWrote JSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
