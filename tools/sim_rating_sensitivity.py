#!/usr/bin/env python3
"""Measure how much ratings and role scores move simulated outcomes.

This is a dry-run calibration harness. It never writes to the database; it
loads teams, creates in-memory boosted copies, and compares each boosted sim to
the same matchup/seed baseline.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import match_engine  # noqa: E402
from tools.sim_audit import (  # noqa: E402
    Matchup,
    connect,
    cycle_matchups,
    explicit_matchup,
    scheduled_matchups,
)


OFFENSE_POSITIONS = {"QB", "RB", "FB", "WR", "TE", "OT", "OG", "C", "OL"}
DEFENSE_POSITIONS = {"EDGE", "OLB", "DE", "IDL", "DT", "NT", "ILB", "LB", "CB", "FS", "SS", "S"}


@dataclass(frozen=True)
class GroupSpec:
    label: str
    positions: frozenset[str]
    rating_keys: tuple[str, ...]
    role_keys: tuple[str, ...] = ()


GROUPS: dict[str, GroupSpec] = {
    "qb": GroupSpec(
        "QB passing/rushing",
        frozenset({"QB"}),
        tuple(
            dict.fromkeys(
                [
                    *match_engine.QB_PASS_WEIGHTS.keys(),
                    *match_engine.QB_SCRAMBLE_WEIGHTS.keys(),
                    "throw_power",
                    "throw_release",
                    "ball_security",
                ]
            )
        ),
        ("pocket_qb", "scrambling_qb"),
    ),
    "rb": GroupSpec(
        "RB/FB ball carrying",
        frozenset({"RB", "FB"}),
        tuple(dict.fromkeys([*match_engine.RB_RUN_WEIGHTS.keys(), *match_engine.YAC_WEIGHTS.keys(), "forced_fumble"])),
        ("power_rb", "elusive_rb", "receiving_back"),
    ),
    "receiver": GroupSpec(
        "WR/TE receiving",
        frozenset({"WR", "TE"}),
        tuple(dict.fromkeys([*match_engine.RECEIVER_WEIGHTS.keys(), *match_engine.YAC_WEIGHTS.keys(), "ball_security"])),
        ("boundary_wr", "slot_wr", "inline_te", "move_te"),
    ),
    "ol": GroupSpec(
        "Offensive line blocking",
        frozenset({"OT", "OG", "C", "OL"}),
        tuple(dict.fromkeys([*match_engine.RUN_BLOCK_WEIGHTS.keys(), *match_engine.PASS_BLOCK_WEIGHTS.keys()])),
        ("pass_protecting_ot", "interior_run_blocker"),
    ),
    "pass_rush": GroupSpec(
        "Front pass rush",
        frozenset({"EDGE", "OLB", "DE", "IDL", "DT", "NT"}),
        tuple(dict.fromkeys([*match_engine.PASS_RUSH_WEIGHTS.keys(), *match_engine.SACK_CREDIT_WEIGHTS.keys()])),
        ("speed_edge", "power_edge", "interior_rusher"),
    ),
    "run_defense": GroupSpec(
        "Front/LB run defense",
        frozenset({"EDGE", "OLB", "DE", "IDL", "DT", "NT", "ILB", "LB"}),
        tuple(dict.fromkeys([*match_engine.RUN_DEF_WEIGHTS.keys(), *match_engine.TACKLE_WEIGHTS.keys(), *match_engine.ASSIST_TACKLE_WEIGHTS.keys()])),
        ("nose_run_stopping_dt", "box_lb", "power_edge"),
    ),
    "coverage": GroupSpec(
        "Coverage defenders",
        frozenset({"CB", "FS", "SS", "S", "ILB", "LB", "OLB"}),
        tuple(dict.fromkeys([*match_engine.COVERAGE_WEIGHTS.keys(), *match_engine.TACKLE_WEIGHTS.keys(), "forced_fumble"])),
        ("man_cb", "zone_cb", "deep_safety", "box_safety", "coverage_lb"),
    ),
    "specialist": GroupSpec(
        "K/P/LS specialists",
        frozenset({"K", "P", "LS"}),
        tuple(dict.fromkeys([*match_engine.KICK_WEIGHTS.keys(), *match_engine.PUNT_WEIGHTS.keys(), "discipline", "stamina"])),
        (),
    ),
}


@dataclass
class Aggregate:
    count: int = 0
    sums: Counter = field(default_factory=Counter)

    def add(self, values: dict[str, float]) -> None:
        self.count += 1
        for key, value in values.items():
            self.sums[key] += float(value)

    def avg(self, key: str) -> float:
        if self.count <= 0:
            return 0.0
        return float(self.sums.get(key, 0.0)) / self.count


def clamp_rating(value: float) -> int:
    return int(round(match_engine.clamp(value, 1, 99)))


def clone_player(
    player: match_engine.PlayerSnapshot,
    *,
    group: GroupSpec | None = None,
    delta: int = 0,
    mode: str = "selected",
) -> match_engine.PlayerSnapshot:
    ratings = dict(player.ratings)
    role_scores = dict(player.role_scores)
    if group and player.position in group.positions and delta:
        if mode == "roles":
            keys = group.role_keys or tuple(role_scores.keys())
            for key in keys:
                if key in role_scores:
                    role_scores[key] = float(match_engine.clamp(role_scores[key] + delta, 1, 99))
        else:
            keys: Iterable[str] = ratings.keys() if mode == "all" else group.rating_keys
            for key in keys:
                if key in ratings:
                    ratings[key] = clamp_rating(ratings[key] + delta)
    return match_engine.PlayerSnapshot(
        player_id=player.player_id,
        name=player.name,
        position=player.position,
        ratings=ratings,
        role_scores=role_scores,
        metadata=dict(player.metadata),
    )


def clone_team(
    team: match_engine.TeamSnapshot,
    *,
    group: GroupSpec | None = None,
    delta: int = 0,
    mode: str = "selected",
) -> match_engine.TeamSnapshot:
    roster = [clone_player(player, group=group, delta=delta, mode=mode) for player in team.roster]
    by_id = {player.player_id: player for player in roster}
    depth = {
        slot: [by_id[player.player_id] for player in players if player.player_id in by_id]
        for slot, players in team.depth.items()
    }
    return match_engine.TeamSnapshot(
        team_id=team.team_id,
        abbreviation=team.abbreviation,
        city=team.city,
        nickname=team.nickname,
        conference=team.conference,
        division=team.division,
        roster=roster,
        depth=depth,
    )


def team_stat(result: match_engine.GameResult, team_id: int, key: str) -> float:
    return float(result.team_stats.get(team_id, Counter()).get(key, 0.0))


def side_metrics(result: match_engine.GameResult) -> dict[str, float]:
    away_id = result.away.team_id
    home_id = result.home.team_id
    away_plays = team_stat(result, away_id, "plays")
    away_pass = team_stat(result, away_id, "pass_attempts")
    away_rush = team_stat(result, away_id, "rush_attempts")
    home_plays = team_stat(result, home_id, "plays")
    return {
        "away_margin": float(result.away_score - result.home_score),
        "away_win": 1.0 if result.away_score > result.home_score else 0.0,
        "away_points": float(result.away_score),
        "home_points": float(result.home_score),
        "away_yards": team_stat(result, away_id, "total_yards"),
        "home_yards": team_stat(result, home_id, "total_yards"),
        "away_ypp": team_stat(result, away_id, "total_yards") / away_plays if away_plays else 0.0,
        "home_ypp": team_stat(result, home_id, "total_yards") / home_plays if home_plays else 0.0,
        "away_rush_yards": team_stat(result, away_id, "rush_yards"),
        "away_pass_yards": team_stat(result, away_id, "pass_yards"),
        "away_rush_att": away_rush,
        "away_pass_att": away_pass,
        "away_sacks_taken": team_stat(result, away_id, "sacks_allowed"),
        "home_sacks": team_stat(result, home_id, "sacks"),
        "away_turnovers": team_stat(result, away_id, "turnovers"),
        "home_turnovers": team_stat(result, home_id, "turnovers"),
        "away_int_thrown": team_stat(result, away_id, "interceptions_thrown"),
        "home_ints": team_stat(result, home_id, "interceptions"),
    }


def diff_metrics(base: match_engine.GameResult, variant: match_engine.GameResult) -> dict[str, float]:
    base_metrics = side_metrics(base)
    variant_metrics = side_metrics(variant)
    return {f"{key}_delta": variant_metrics[key] - base_metrics[key] for key in base_metrics}


def load_matchups(
    con: sqlite3.Connection,
    *,
    season: int,
    games: int,
    seed: int,
    team: str | None,
    week: int | None,
    matchup: list[str] | None,
) -> tuple[str, list[Matchup]]:
    rng = random.Random(seed)
    if matchup:
        rows = explicit_matchup(con, matchup[0], matchup[1])
        label = f"{matchup[0].upper()} at {matchup[1].upper()} repeated"
    else:
        rows = scheduled_matchups(con, season=season, team=team, week=week)
        label_bits = [f"{len(rows)} scheduled regular-season matchup(s)"]
        if team:
            label_bits.append(f"team={team.upper()}")
        if week is not None:
            label_bits.append(f"week={week}")
        label = ", ".join(label_bits)
    if not rows:
        raise ValueError("No matchup pool found for the requested filters.")
    return label, cycle_matchups(rows, games, rng)


def run_game(
    *,
    away: match_engine.TeamSnapshot,
    home: match_engine.TeamSnapshot,
    season: int,
    matchup: Matchup,
    seed: int,
) -> match_engine.GameResult:
    return match_engine.MatchEngine(
        away=away,
        home=home,
        season=season,
        week=matchup.week,
        schedule_game_id=matchup.game_id,
        seed=seed,
    ).simulate()


def run_sensitivity(args: argparse.Namespace) -> dict[str, Aggregate]:
    groups = args.groups or list(GROUPS)
    unknown = [group for group in groups if group not in GROUPS]
    if unknown:
        raise ValueError(f"Unknown group(s): {', '.join(unknown)}")

    aggregates = {group: Aggregate() for group in groups}
    with connect(args.db) as con:
        pool_label, selected = load_matchups(
            con,
            season=args.season,
            games=args.games,
            seed=args.seed,
            team=args.team,
            week=args.week,
            matchup=args.matchup,
        )
        team_cache: dict[int, match_engine.TeamSnapshot] = {}

        def load(team_id: int) -> match_engine.TeamSnapshot:
            if team_id not in team_cache:
                team_cache[team_id] = match_engine.load_team(con, team_id, args.season)
            return team_cache[team_id]

        for idx, matchup in enumerate(selected, start=1):
            seed = args.seed + idx - 1
            away = load(matchup.away_team_id)
            home = load(matchup.home_team_id)
            base = run_game(away=clone_team(away), home=clone_team(home), season=args.season, matchup=matchup, seed=seed)
            for group_key in groups:
                boosted_away = clone_team(away, group=GROUPS[group_key], delta=args.delta, mode=args.mode)
                variant = run_game(
                    away=boosted_away,
                    home=clone_team(home),
                    season=args.season,
                    matchup=matchup,
                    seed=seed,
                )
                aggregates[group_key].add(diff_metrics(base, variant))
            if args.progress_every and idx % args.progress_every == 0:
                print(f"Sensitivity simulated {idx}/{args.games} baseline matchup(s)...", flush=True)

    print_sensitivity(args=args, pool_label=pool_label, aggregates=aggregates)
    return aggregates


def print_sensitivity(*, args: argparse.Namespace, pool_label: str, aggregates: dict[str, Aggregate]) -> None:
    print("Rating Sensitivity")
    print(f"Engine: {match_engine.ENGINE_VERSION}")
    print(f"DB: {args.db}")
    print(f"Season: {args.season}")
    print(f"Games per group: {args.games}")
    print(f"Seed: {args.seed}")
    print(f"Pool: {pool_label}")
    print(f"Boost: +{args.delta} ({args.mode}) on away-team group only")
    print("")
    print("Positive margin/win deltas mean the boosted away team improved.")
    print("")
    header = (
        f"{'Group':<18} {'Margin':>8} {'Win%':>8} {'Pts For':>8} {'Pts Ag':>8} "
        f"{'Yds For':>8} {'Yds Ag':>8} {'YPP':>7} {'Opp YPP':>8} {'Sacks':>7} {'TO For':>7} {'TO Ag':>7}"
    )
    print(header)
    print("-" * len(header))
    for group_key, agg in aggregates.items():
        print(
            f"{group_key:<18} "
            f"{agg.avg('away_margin_delta'):>8.2f} "
            f"{agg.avg('away_win_delta') * 100.0:>7.1f}% "
            f"{agg.avg('away_points_delta'):>8.2f} "
            f"{agg.avg('home_points_delta'):>8.2f} "
            f"{agg.avg('away_yards_delta'):>8.1f} "
            f"{agg.avg('home_yards_delta'):>8.1f} "
            f"{agg.avg('away_ypp_delta'):>7.2f} "
            f"{agg.avg('home_ypp_delta'):>8.2f} "
            f"{agg.avg('home_sacks_delta'):>7.2f} "
            f"{agg.avg('home_turnovers_delta'):>7.2f} "
            f"{agg.avg('away_turnovers_delta'):>7.2f}"
        )


def success(play: match_engine.PlayEvent) -> bool:
    if play.is_touchdown:
        return True
    needed = max(1, play.distance)
    gained = play.yards_gained
    if play.down == 1:
        return gained >= needed * 0.45
    if play.down == 2:
        return gained >= needed * 0.60
    return gained >= needed


def bucket_label(value: float) -> str:
    if value <= -10:
        return "<= -10"
    if value <= -5:
        return "-10..-5"
    if value < 5:
        return "-5..+5"
    if value < 10:
        return "+5..+10"
    return ">= +10"


def player_map(team: match_engine.TeamSnapshot) -> dict[int, match_engine.PlayerSnapshot]:
    return {player.player_id: player for player in team.roster}


def add_play_bucket(bucket: Aggregate, play: match_engine.PlayEvent) -> None:
    bucket.add(
        {
            "yards": float(play.yards_gained),
            "success": 1.0 if success(play) else 0.0,
            "touchdown": float(play.is_touchdown),
            "turnover": float(play.is_turnover),
            "sack": 1.0 if is_sack(play) else 0.0,
        }
    )


def run_gap(offense: match_engine.TeamSnapshot, defense: match_engine.TeamSnapshot, runner: match_engine.PlayerSnapshot | None) -> float:
    runner_score = match_engine.weighted_average(runner, match_engine.RB_RUN_WEIGHTS) if runner else 50.0
    return (offense.run_block_score() + runner_score) * 0.5 - (defense.run_defense_score() + defense.tackling_score()) * 0.5


def pass_gap(
    offense: match_engine.TeamSnapshot,
    defense: match_engine.TeamSnapshot,
    target: match_engine.PlayerSnapshot | None,
    defender: match_engine.PlayerSnapshot | None,
) -> float:
    qb = offense.starter("QB")
    qb_score = match_engine.weighted_average(qb, match_engine.QB_PASS_WEIGHTS)
    target_score = match_engine.weighted_average(target, match_engine.RECEIVER_WEIGHTS) if target else 50.0
    defender_score = match_engine.weighted_average(defender, match_engine.COVERAGE_WEIGHTS) if defender else defense.coverage_score()
    return (qb_score + target_score) * 0.5 - defender_score


def protection_gap(offense: match_engine.TeamSnapshot, defense: match_engine.TeamSnapshot) -> float:
    return offense.pass_block_score() - defense.pass_rush_score()


def is_sack(play: match_engine.PlayEvent) -> bool:
    return (
        play.play_type == "pass"
        and play.offense_player_id is not None
        and play.target_player_id == play.offense_player_id
        and "sacks" in play.description.lower()
    )


def run_play_level(args: argparse.Namespace) -> dict[str, Aggregate]:
    buckets: dict[str, Aggregate] = defaultdict(Aggregate)
    with connect(args.db) as con:
        pool_label, selected = load_matchups(
            con,
            season=args.season,
            games=args.play_games,
            seed=args.play_seed,
            team=args.team,
            week=args.week,
            matchup=args.matchup,
        )
        team_cache: dict[int, match_engine.TeamSnapshot] = {}

        def load(team_id: int) -> match_engine.TeamSnapshot:
            if team_id not in team_cache:
                team_cache[team_id] = match_engine.load_team(con, team_id, args.season)
            return team_cache[team_id]

        for idx, matchup in enumerate(selected, start=1):
            away = clone_team(load(matchup.away_team_id))
            home = clone_team(load(matchup.home_team_id))
            result = run_game(away=away, home=home, season=args.season, matchup=matchup, seed=args.play_seed + idx - 1)
            teams = {away.team_id: away, home.team_id: home}
            players = {away.team_id: player_map(away), home.team_id: player_map(home)}
            for play in result.plays:
                if play.play_type not in {"run", "pass"}:
                    continue
                offense = teams[play.offense_team_id]
                defense = teams[play.defense_team_id]
                offense_players = players[play.offense_team_id]
                defense_players = players[play.defense_team_id]
                if play.play_type == "run":
                    runner = offense_players.get(play.offense_player_id or -1)
                    gap = run_gap(offense, defense, runner)
                    add_play_bucket(buckets[f"run {bucket_label(gap)}"], play)
                else:
                    if play.concept == "spike":
                        continue
                    protect_gap = protection_gap(offense, defense)
                    add_play_bucket(buckets[f"protect {bucket_label(protect_gap)}"], play)
                    if is_sack(play):
                        continue
                    target = offense_players.get(play.target_player_id or -1)
                    defender = defense_players.get(play.defense_player_id or -1)
                    gap = pass_gap(offense, defense, target, defender)
                    add_play_bucket(buckets[f"route {bucket_label(gap)}"], play)
            if args.progress_every and idx % args.progress_every == 0:
                print(f"Play-level simulated {idx}/{args.play_games} game(s)...", flush=True)

    print_play_level(args=args, pool_label=pool_label, buckets=buckets)
    return buckets


def print_play_level(*, args: argparse.Namespace, pool_label: str, buckets: dict[str, Aggregate]) -> None:
    print("")
    print("Play-Level Rating Buckets")
    print(f"Games: {args.play_games}")
    print(f"Seed: {args.play_seed}")
    print(f"Pool: {pool_label}")
    print("")
    header = f"{'Bucket':<16} {'Plays':>7} {'Yds/Play':>9} {'Success':>9} {'Sack':>7} {'TD':>7} {'TO':>7}"
    print(header)
    print("-" * len(header))
    order = [
        "run <= -10",
        "run -10..-5",
        "run -5..+5",
        "run +5..+10",
        "run >= +10",
        "protect <= -10",
        "protect -10..-5",
        "protect -5..+5",
        "protect +5..+10",
        "protect >= +10",
        "route <= -10",
        "route -10..-5",
        "route -5..+5",
        "route +5..+10",
        "route >= +10",
    ]
    for key in order:
        bucket = buckets.get(key)
        if not bucket or not bucket.count:
            continue
        print(
            f"{key:<16} "
            f"{bucket.count:>7} "
            f"{bucket.avg('yards'):>9.2f} "
            f"{bucket.avg('success') * 100.0:>8.1f}% "
            f"{bucket.avg('sack') * 100.0:>6.2f}% "
            f"{bucket.avg('touchdown') * 100.0:>6.2f}% "
            f"{bucket.avg('turnover') * 100.0:>6.2f}%"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run rating/role-score sensitivity checks for MatchEngine.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--season", type=int, default=match_engine.DEFAULT_SEASON)
    parser.add_argument("--games", type=int, default=32, help="Baseline matchup count per sensitivity group.")
    parser.add_argument("--seed", type=int, default=72000)
    parser.add_argument("--team", help="Sample scheduled games involving this team abbreviation.")
    parser.add_argument("--week", type=int, help="Sample scheduled games from one regular-season week.")
    parser.add_argument("--matchup", nargs=2, metavar=("AWAY", "HOME"), help="Repeat one explicit matchup.")
    parser.add_argument("--groups", nargs="+", choices=sorted(GROUPS), help="Position groups to boost. Default: all.")
    parser.add_argument("--delta", type=int, default=10, help="Rating/role-score boost amount.")
    parser.add_argument(
        "--mode",
        choices=("selected", "all", "roles"),
        default="selected",
        help="selected=group engine keys, all=all ratings on matching players, roles=role score proxy for overall/depth.",
    )
    parser.add_argument("--play-level", action="store_true", help="Also bucket baseline run/pass plays by rating advantage.")
    parser.add_argument("--play-games", type=int, default=64, help="Game count for play-level bucket pass.")
    parser.add_argument("--play-seed", type=int, default=73000)
    parser.add_argument("--progress-every", type=int, default=0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.games <= 0:
        raise ValueError("--games must be positive.")
    if args.play_games <= 0:
        raise ValueError("--play-games must be positive.")
    run_sensitivity(args)
    if args.play_level:
        run_play_level(args)
    print("")
    print("Note: legacy players.overall is not read by MatchEngine; use --mode roles to test the role-score/depth proxy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
