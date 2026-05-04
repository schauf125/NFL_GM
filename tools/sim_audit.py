#!/usr/bin/env python3
"""Run dry-run match-engine batches and report realism guardrails."""

from __future__ import annotations

import argparse
import csv
import json
import random
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import match_engine  # noqa: E402


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    low: float | None = None
    high: float | None = None
    decimals: int = 1
    suffix: str = ""


@dataclass(frozen=True)
class Matchup:
    away_team_id: int
    home_team_id: int
    away_abbr: str
    home_abbr: str
    game_id: int | None = None
    week: int | None = None


METRICS = [
    MetricSpec("points", "Total points", 36.0, 52.0),
    MetricSpec("offensive_plays", "Offensive plays", 120.0, 155.0),
    MetricSpec("play_events", "Play events", 145.0, 205.0),
    MetricSpec("drives", "Drives", 18.0, 30.0),
    MetricSpec("total_yards", "Total yards", 560.0, 790.0),
    MetricSpec("yards_per_play", "Yards/play", 4.4, 6.2, 2),
    MetricSpec("first_downs", "First downs", 32.0, 48.0),
    MetricSpec("pass_attempts", "Pass attempts", 60.0, 88.0),
    MetricSpec("pass_rate", "Pass rate", 48.0, 66.0, suffix="%"),
    MetricSpec("completion_rate", "Completion rate", 56.0, 72.0, suffix="%"),
    MetricSpec("rush_attempts", "Rush attempts", 42.0, 68.0),
    MetricSpec("sacks", "Sacks", 2.5, 6.5),
    MetricSpec("sack_rate", "Sack rate", 3.0, 9.0, suffix="%"),
    MetricSpec("interceptions", "Interceptions", 0.6, 2.2),
    MetricSpec("interception_rate", "INT rate", 0.8, 4.0, suffix="%"),
    MetricSpec("fumbles_lost", "Fumbles lost", 0.15, 1.1),
    MetricSpec("penalties", "Penalties", 5.5, 13.5),
    MetricSpec("penalty_yards", "Penalty yards", 35.0, 115.0),
    MetricSpec("declined_penalties", "Declined penalties"),
    MetricSpec("offsetting_penalties", "Offsetting flags"),
    MetricSpec("penalty_first_downs", "Penalty first downs"),
    MetricSpec("kickoffs", "Kickoffs", 6.0, 13.5),
    MetricSpec("kickoff_returns", "Kickoff returns", 2.0, 10.0),
    MetricSpec("kickoff_return_rate", "Kickoff return rate", 30.0, 90.0, suffix="%"),
    MetricSpec("punts", "Punts", 6.0, 13.5),
    MetricSpec("punt_returns", "Punt returns", 2.0, 11.0),
    MetricSpec("punt_return_rate", "Punt return rate", 25.0, 90.0, suffix="%"),
    MetricSpec("fair_catches", "Fair catches", 0.0, 7.0),
    MetricSpec("field_goal_attempts", "FG attempts", 2.0, 6.5),
    MetricSpec("field_goal_rate", "FG make rate", 65.0, 95.0, suffix="%"),
    MetricSpec("xp_rate", "XP make rate", 82.0, 100.0, suffix="%"),
    MetricSpec("two_point_attempts", "Two-point attempts", 0.02, 0.9, 2),
    MetricSpec("timeouts", "Timeouts used", 0.5, 9.5),
    MetricSpec("return_tds", "Return TDs", 0.0, 0.35, 2),
    MetricSpec("defensive_tds", "Defensive TDs", 0.0, 0.25, 2),
    MetricSpec("special_teams_tds", "Special teams TDs", 0.0, 0.20, 2),
    MetricSpec("blocked_plays", "Blocked kicks/punts", 0.0, 0.35, 2),
    MetricSpec("safeties", "Safeties", 0.0, 0.18, 2),
    MetricSpec("onside_kicks", "Onside kicks", 0.0, 0.9, 2),
    MetricSpec("kneels", "Kneels"),
    MetricSpec("spikes", "Spikes"),
    MetricSpec("ot_games", "OT games", 2.0, 15.0, suffix="%"),
    MetricSpec("ties", "Ties", 0.0, 4.0, suffix="%"),
]


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def team_row(con: sqlite3.Connection, abbreviation: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT team_id, abbreviation FROM teams WHERE abbreviation = ?",
        (abbreviation.upper(),),
    ).fetchone()
    if not row:
        raise ValueError(f"Team not found: {abbreviation}")
    return row


def scheduled_matchups(
    con: sqlite3.Connection,
    *,
    season: int,
    team: str | None = None,
    week: int | None = None,
) -> list[Matchup]:
    filters = ["sg.season = ?", "sg.game_type = 'REG'"]
    params: list[Any] = [season]
    if team:
        filters.append("(away.abbreviation = ? OR home.abbreviation = ?)")
        params.extend([team.upper(), team.upper()])
    if week is not None:
        filters.append("sg.week = ?")
        params.append(week)
    rows = con.execute(
        f"""
        SELECT
            sg.game_id,
            sg.week,
            sg.away_team_id,
            sg.home_team_id,
            away.abbreviation AS away_abbr,
            home.abbreviation AS home_abbr
        FROM season_games sg
        JOIN teams away ON away.team_id = sg.away_team_id
        JOIN teams home ON home.team_id = sg.home_team_id
        WHERE {' AND '.join(filters)}
        ORDER BY sg.week, sg.week_game_number, sg.game_id
        """,
        params,
    ).fetchall()
    return [
        Matchup(
            away_team_id=int(row["away_team_id"]),
            home_team_id=int(row["home_team_id"]),
            away_abbr=str(row["away_abbr"]),
            home_abbr=str(row["home_abbr"]),
            game_id=int(row["game_id"]),
            week=int(row["week"]) if row["week"] is not None else None,
        )
        for row in rows
    ]


def explicit_matchup(con: sqlite3.Connection, away: str, home: str) -> list[Matchup]:
    away_row = team_row(con, away)
    home_row = team_row(con, home)
    return [
        Matchup(
            away_team_id=int(away_row["team_id"]),
            home_team_id=int(home_row["team_id"]),
            away_abbr=str(away_row["abbreviation"]),
            home_abbr=str(home_row["abbreviation"]),
        )
    ]


def cycle_matchups(rows: list[Matchup], games: int, rng: random.Random) -> list[Matchup]:
    selected: list[Matchup] = []
    deck: list[Matchup] = []
    while len(selected) < games:
        if not deck:
            deck = list(rows)
            rng.shuffle(deck)
        selected.append(deck.pop())
    return selected


def safe_rate(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator * 100.0


def team_sum(result: match_engine.GameResult, key: str) -> float:
    return float(sum(stats.get(key, 0) for stats in result.team_stats.values()))


def count_concept(result: match_engine.GameResult, concept: str) -> float:
    return float(sum(1 for play in result.plays if play.concept == concept))


def game_metrics(result: match_engine.GameResult) -> dict[str, float | None]:
    offensive_plays = team_sum(result, "plays")
    pass_attempts = team_sum(result, "pass_attempts")
    pass_completions = team_sum(result, "pass_completions")
    rush_attempts = team_sum(result, "rush_attempts")
    total_yards = team_sum(result, "total_yards")
    sacks = team_sum(result, "sacks")
    interceptions = team_sum(result, "interceptions")
    kickoffs = team_sum(result, "kickoffs")
    kickoff_returns = team_sum(result, "kickoff_returns")
    punts = team_sum(result, "punts")
    punt_returns = team_sum(result, "punt_returns")
    fg_attempts = team_sum(result, "fg_attempts")
    xp_attempts = team_sum(result, "xp_attempts")
    two_point_attempts = team_sum(result, "two_point_attempts")
    defensive_tds = team_sum(result, "defensive_tds")
    special_teams_tds = team_sum(result, "special_teams_tds")
    blocked_plays = team_sum(result, "blocked_kicks") + team_sum(result, "blocked_punts")
    dropbacks = pass_attempts + sacks

    return {
        "points": float(result.away_score + result.home_score),
        "offensive_plays": offensive_plays,
        "play_events": float(len(result.plays)),
        "drives": float(len(result.drives)),
        "total_yards": total_yards,
        "yards_per_play": safe_rate(total_yards, offensive_plays * 100.0),
        "first_downs": team_sum(result, "first_downs"),
        "pass_attempts": pass_attempts,
        "pass_rate": safe_rate(pass_attempts, offensive_plays),
        "completion_rate": safe_rate(pass_completions, pass_attempts),
        "rush_attempts": rush_attempts,
        "sacks": sacks,
        "sack_rate": safe_rate(sacks, dropbacks),
        "interceptions": interceptions,
        "interception_rate": safe_rate(interceptions, pass_attempts),
        "fumbles_lost": team_sum(result, "fumbles_lost"),
        "penalties": team_sum(result, "penalties"),
        "penalty_yards": team_sum(result, "penalty_yards"),
        "declined_penalties": team_sum(result, "declined_penalties"),
        "offsetting_penalties": team_sum(result, "offsetting_penalties"),
        "penalty_first_downs": team_sum(result, "penalty_first_downs"),
        "kickoffs": kickoffs,
        "kickoff_returns": kickoff_returns,
        "kickoff_return_rate": safe_rate(kickoff_returns, kickoffs),
        "punts": punts,
        "punt_returns": punt_returns,
        "punt_return_rate": safe_rate(punt_returns, punts),
        "fair_catches": team_sum(result, "fair_catches"),
        "field_goal_attempts": fg_attempts,
        "field_goal_rate": safe_rate(team_sum(result, "fg_made"), fg_attempts),
        "xp_rate": safe_rate(team_sum(result, "xp_made"), xp_attempts),
        "two_point_attempts": two_point_attempts,
        "two_point_rate": safe_rate(team_sum(result, "two_point_made"), two_point_attempts),
        "timeouts": team_sum(result, "timeouts_used"),
        "return_tds": defensive_tds + special_teams_tds,
        "defensive_tds": defensive_tds,
        "special_teams_tds": special_teams_tds,
        "blocked_plays": blocked_plays,
        "safeties": team_sum(result, "safeties"),
        "onside_kicks": team_sum(result, "onside_kicks"),
        "kneels": count_concept(result, "kneel"),
        "spikes": count_concept(result, "spike"),
        "ot_games": 100.0 if any(play.quarter == 5 for play in result.plays) else 0.0,
        "ties": 100.0 if result.away_score == result.home_score else 0.0,
    }


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * pct
    lower = int(pos)
    upper = min(lower + 1, len(ordered) - 1)
    weight = pos - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize(values: list[float | None]) -> dict[str, float | int | None]:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return {
            "samples": 0,
            "avg": None,
            "min": None,
            "p10": None,
            "median": None,
            "p90": None,
            "max": None,
        }
    return {
        "samples": len(clean),
        "avg": sum(clean) / len(clean),
        "min": min(clean),
        "p10": percentile(clean, 0.10),
        "median": percentile(clean, 0.50),
        "p90": percentile(clean, 0.90),
        "max": max(clean),
    }


def target_status(spec: MetricSpec, avg: float | None) -> str:
    if avg is None or spec.low is None or spec.high is None:
        return ""
    if avg < spec.low:
        return "LOW"
    if avg > spec.high:
        return "HIGH"
    return "OK"


def format_value(value: float | int | None, spec: MetricSpec) -> str:
    if value is None:
        return "-"
    return f"{float(value):.{spec.decimals}f}{spec.suffix}"


def print_summary(
    *,
    summaries: dict[str, dict[str, float | int | None]],
    args: argparse.Namespace,
    pool_label: str,
    failures: list[str],
) -> None:
    print("Sim Audit")
    print(f"Engine: {match_engine.ENGINE_VERSION}")
    print(f"DB: {args.db}")
    print(f"Season: {args.season}")
    print(f"Games: {args.games}")
    print(f"Seed: {args.seed}")
    print(f"Pool: {pool_label}")
    print("")
    print("Guardrails are starter tuning ranges, not immutable league facts.")
    print("")
    header = f"{'Metric':<24} {'Avg':>9} {'P10':>9} {'Med':>9} {'P90':>9} {'Min':>9} {'Max':>9} {'Target':>15} {'Status':>7}"
    print(header)
    print("-" * len(header))
    for spec in METRICS:
        row = summaries[spec.key]
        target = ""
        if spec.low is not None and spec.high is not None:
            target = f"{format_value(spec.low, spec)}-{format_value(spec.high, spec)}"
        status = target_status(spec, row["avg"] if isinstance(row["avg"], float) else None)
        print(
            f"{spec.label:<24} "
            f"{format_value(row['avg'], spec):>9} "
            f"{format_value(row['p10'], spec):>9} "
            f"{format_value(row['median'], spec):>9} "
            f"{format_value(row['p90'], spec):>9} "
            f"{format_value(row['min'], spec):>9} "
            f"{format_value(row['max'], spec):>9} "
            f"{target:>15} "
            f"{status:>7}"
        )
    if failures:
        print("")
        print("Guardrail flags:")
        for failure in failures:
            print(f"  {failure}")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_csv(path: Path, summaries: dict[str, dict[str, float | int | None]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "label", "samples", "avg", "min", "p10", "median", "p90", "max", "target_low", "target_high", "status"])
        for spec in METRICS:
            row = summaries[spec.key]
            avg = row["avg"] if isinstance(row["avg"], float) else None
            writer.writerow(
                [
                    spec.key,
                    spec.label,
                    row["samples"],
                    row["avg"],
                    row["min"],
                    row["p10"],
                    row["median"],
                    row["p90"],
                    row["max"],
                    spec.low,
                    spec.high,
                    target_status(spec, avg),
                ]
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit match-engine outputs across dry-run simulation batches.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--season", type=int, default=match_engine.DEFAULT_SEASON)
    parser.add_argument("--games", type=int, default=100, help="Number of dry-run games to simulate.")
    parser.add_argument("--seed", type=int, default=3000, help="Base seed for matchup sampling and game seeds.")
    parser.add_argument("--team", help="Sample scheduled games involving this team abbreviation.")
    parser.add_argument("--week", type=int, help="Sample scheduled games from one regular-season week.")
    parser.add_argument("--matchup", nargs=2, metavar=("AWAY", "HOME"), help="Repeat one explicit matchup instead of sampling the schedule.")
    parser.add_argument("--progress-every", type=int, default=0, help="Print progress every N games.")
    parser.add_argument("--json", type=Path, help="Write summary payload to JSON.")
    parser.add_argument("--csv", type=Path, help="Write summary rows to CSV.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when any metric average is outside its guardrail.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.games <= 0:
        raise ValueError("--games must be positive.")

    rng = random.Random(args.seed)
    samples: dict[str, list[float | None]] = {spec.key: [] for spec in METRICS}
    game_rows: list[dict[str, Any]] = []
    with connect(args.db) as con:
        if args.matchup:
            matchups = explicit_matchup(con, args.matchup[0], args.matchup[1])
            pool_label = f"{args.matchup[0].upper()} at {args.matchup[1].upper()} repeated"
        else:
            matchups = scheduled_matchups(con, season=args.season, team=args.team, week=args.week)
            label_bits = [f"{len(matchups)} scheduled regular-season matchup(s)"]
            if args.team:
                label_bits.append(f"team={args.team.upper()}")
            if args.week is not None:
                label_bits.append(f"week={args.week}")
            pool_label = ", ".join(label_bits)
        if not matchups:
            raise ValueError("No matchup pool found for the requested filters.")

        selected = cycle_matchups(matchups, args.games, rng)
        team_cache: dict[int, match_engine.TeamSnapshot] = {}

        def load(team_id: int) -> match_engine.TeamSnapshot:
            if team_id not in team_cache:
                team_cache[team_id] = match_engine.load_team(con, team_id, args.season)
            return team_cache[team_id]

        for idx, matchup in enumerate(selected, start=1):
            result = match_engine.MatchEngine(
                away=load(matchup.away_team_id),
                home=load(matchup.home_team_id),
                season=args.season,
                week=matchup.week,
                schedule_game_id=matchup.game_id,
                seed=args.seed + idx - 1,
            ).simulate()
            metrics = game_metrics(result)
            for spec in METRICS:
                samples[spec.key].append(metrics.get(spec.key))
            game_rows.append(
                {
                    "idx": idx,
                    "seed": args.seed + idx - 1,
                    "away": matchup.away_abbr,
                    "home": matchup.home_abbr,
                    "away_score": result.away_score,
                    "home_score": result.home_score,
                    "metrics": metrics,
                }
            )
            if args.progress_every and idx % args.progress_every == 0:
                print(f"Simulated {idx}/{args.games} game(s)...")

    summaries = {key: summarize(values) for key, values in samples.items()}
    failures = []
    for spec in METRICS:
        row = summaries[spec.key]
        avg = row["avg"] if isinstance(row["avg"], float) else None
        status = target_status(spec, avg)
        if status in {"LOW", "HIGH"}:
            failures.append(f"{spec.label}: {format_value(avg, spec)} is {status} vs guardrail")

    print_summary(summaries=summaries, args=args, pool_label=pool_label, failures=failures)

    payload = {
        "engine_version": match_engine.ENGINE_VERSION,
        "season": args.season,
        "games": args.games,
        "seed": args.seed,
        "pool": pool_label,
        "metrics": summaries,
        "failures": failures,
        "games_sampled": game_rows,
    }
    if args.json:
        write_json(args.json, payload)
        print(f"\nWrote JSON: {args.json}")
    if args.csv:
        write_csv(args.csv, summaries)
        print(f"Wrote CSV: {args.csv}")

    return 1 if args.strict and failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
