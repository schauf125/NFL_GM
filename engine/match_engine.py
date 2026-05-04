"""Attribute-driven football match engine for NFL GM Sim.

This is intentionally a first playable engine, not the final physics model.
It uses the normalized 0-100 player_ratings table, depth charts, and a
tenths-of-a-second game clock. The design keeps the surface stable so future
work can swap in richer playbooks, coaching tendencies, injuries, weather,
and local LLM GM/coach logic without changing schedule/result storage.
"""

from __future__ import annotations

import math
import random
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


ENGINE_VERSION = "0.1.2"
TENTHS_PER_SECOND = 10
REGULATION_QUARTER_TENTHS = 15 * 60 * TENTHS_PER_SECOND
OVERTIME_TENTHS = 10 * 60 * TENTHS_PER_SECOND
DEFAULT_SEASON = 2026


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def weighted_average(player: "PlayerSnapshot", weights: dict[str, float], default: float = 50.0) -> float:
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return default
    return sum(player.rating(key, default) * weight for key, weight in weights.items()) / total_weight


def average(values: Iterable[float], default: float = 50.0) -> float:
    values = list(values)
    if not values:
        return default
    return sum(values) / len(values)


def clock_string(tenths: int) -> str:
    tenths = max(0, int(tenths))
    minutes = tenths // 600
    seconds = (tenths % 600) // 10
    tenth = tenths % 10
    return f"{minutes:02d}:{seconds:02d}.{tenth}"


def format_yardline(field_pos: int) -> str:
    field_pos = int(clamp(field_pos, 0, 100))
    if field_pos == 50:
        return "50"
    if field_pos < 50:
        return f"own {field_pos}"
    return f"opp {100 - field_pos}"


def weighted_choice(rng: random.Random, items: list[tuple[object, float]]):
    clean = [(item, max(0.01, weight)) for item, weight in items]
    total = sum(weight for _item, weight in clean)
    roll = rng.random() * total
    cursor = 0.0
    for item, weight in clean:
        cursor += weight
        if roll <= cursor:
            return item
    return clean[-1][0]


QB_PASS_WEIGHTS = {
    "pass_accuracy_short": 12,
    "pass_accuracy_mid": 16,
    "pass_accuracy_deep": 10,
    "throw_power": 8,
    "throw_release": 10,
    "platform_control": 9,
    "processing_speed": 14,
    "play_recognition": 12,
    "composure": 8,
    "discipline": 5,
}
QB_SCRAMBLE_WEIGHTS = {
    "speed": 12,
    "acceleration": 12,
    "agility": 12,
    "elusiveness": 12,
    "carry_vision": 8,
    "ball_security": 8,
    "platform_control": 6,
}
RB_RUN_WEIGHTS = {
    "carry_vision": 14,
    "run_patience": 12,
    "elusiveness": 10,
    "contact_power": 10,
    "balance": 10,
    "speed": 8,
    "acceleration": 10,
    "agility": 8,
    "ball_security": 8,
}
RECEIVER_WEIGHTS = {
    "route_timing": 14,
    "route_snap": 12,
    "release_vs_press": 8,
    "hands": 12,
    "catch_in_traffic": 8,
    "contested_catch": 6,
    "speed": 8,
    "acceleration": 8,
    "agility": 6,
    "composure": 4,
}
YAC_WEIGHTS = {
    "speed": 10,
    "acceleration": 10,
    "agility": 10,
    "elusiveness": 10,
    "balance": 8,
    "contact_power": 6,
    "ball_security": 4,
}
RUN_BLOCK_WEIGHTS = {
    "run_block_drive": 14,
    "reach_block": 10,
    "block_sustain": 12,
    "lead_block": 4,
    "strength": 10,
    "discipline": 4,
    "stamina": 3,
}
PASS_BLOCK_WEIGHTS = {
    "pass_block_speed": 12,
    "pass_block_power": 12,
    "pass_block_finesse": 12,
    "strength": 8,
    "processing_speed": 5,
    "discipline": 5,
    "stamina": 3,
}
PASS_RUSH_WEIGHTS = {
    "speed_rush": 10,
    "power_rush": 10,
    "finesse_rush": 8,
    "rush_plan": 8,
    "sack_finish": 8,
    "acceleration": 6,
    "strength": 6,
    "stamina": 3,
}
RUN_DEF_WEIGHTS = {
    "gap_integrity": 12,
    "run_diagnostics": 12,
    "block_shedding": 10,
    "double_team_takeon": 8,
    "edge_contain": 6,
    "strength": 8,
    "tackle_wrap": 8,
    "solo_tackle": 6,
}
COVERAGE_WEIGHTS = {
    "man_coverage": 11,
    "zone_coverage": 11,
    "press_coverage": 6,
    "coverage_communication": 6,
    "play_recognition": 8,
    "processing_speed": 6,
    "speed": 8,
    "agility": 8,
    "ball_skills": 6,
}
TACKLE_WEIGHTS = {
    "solo_tackle": 10,
    "assist_tackle": 4,
    "tackle_wrap": 10,
    "open_field_tackle": 10,
    "pursuit_angle": 6,
    "hit_power": 5,
    "strength": 4,
}
KICK_WEIGHTS = {
    "kick_power": 9,
    "kick_accuracy": 12,
    "composure": 4,
}
PUNT_WEIGHTS = {
    "kick_power": 12,
    "kick_accuracy": 8,
    "composure": 3,
}


SLOT_POSITION_FALLBACKS = {
    "QB": ["QB"],
    "RB": ["RB"],
    "FB": ["FB", "TE", "RB"],
    "LWR": ["WR"],
    "RWR": ["WR"],
    "SWR": ["WR"],
    "TE": ["TE"],
    "LT": ["OT", "OL"],
    "LG": ["OG", "C", "OL"],
    "C": ["C", "OG", "OL"],
    "RG": ["OG", "C", "OL"],
    "RT": ["OT", "OL"],
    "LEDGE": ["EDGE", "OLB", "DE"],
    "REDGE": ["EDGE", "OLB", "DE"],
    "LDL": ["IDL", "DT", "DE"],
    "NT": ["IDL", "DT", "NT"],
    "RDL": ["IDL", "DT", "DE"],
    "MLB": ["ILB", "LB"],
    "WLB": ["ILB", "LB", "OLB"],
    "SLB": ["ILB", "LB", "OLB", "EDGE"],
    "LCB": ["CB"],
    "RCB": ["CB"],
    "NB": ["CB", "SS", "FS"],
    "FS": ["FS", "SS", "S"],
    "SS": ["SS", "FS", "S"],
    "PK": ["K"],
    "K": ["K"],
    "KO": ["K"],
    "PT": ["P"],
    "P": ["P"],
}


@dataclass
class PlayerSnapshot:
    player_id: int
    name: str
    position: str
    ratings: dict[str, int]
    role_scores: dict[str, float] = field(default_factory=dict)

    def rating(self, key: str, default: float = 50.0) -> float:
        return float(self.ratings.get(key, default))

    def role(self, key: str, default: float = 50.0) -> float:
        return float(self.role_scores.get(key, default))

    def general_score(self) -> float:
        role_anchor = max(self.role_scores.values(), default=0.0)
        universal = average(
            self.rating(key)
            for key in (
                "play_recognition",
                "processing_speed",
                "discipline",
                "composure",
                "consistency",
            )
        )
        athletic = average(
            self.rating(key)
            for key in ("speed", "acceleration", "agility", "strength", "stamina")
        )
        if role_anchor:
            return role_anchor * 0.70 + universal * 0.20 + athletic * 0.10
        return universal * 0.60 + athletic * 0.40


@dataclass
class TeamSnapshot:
    team_id: int
    abbreviation: str
    city: str
    nickname: str
    conference: str
    division: str
    roster: list[PlayerSnapshot]
    depth: dict[str, list[PlayerSnapshot]]

    @property
    def display_name(self) -> str:
        return f"{self.city} {self.nickname}"

    def candidates(self, slot: str) -> list[PlayerSnapshot]:
        slot = slot.upper()
        if self.depth.get(slot):
            return self.depth[slot]
        fallback_positions = SLOT_POSITION_FALLBACKS.get(slot, [slot])
        players = [p for p in self.roster if p.position in fallback_positions]
        return sorted(players, key=lambda p: self.score_for_slot(p, slot), reverse=True)

    def starter(self, slot: str) -> PlayerSnapshot:
        candidates = self.candidates(slot)
        if candidates:
            return candidates[0]
        if self.roster:
            return max(self.roster, key=lambda p: p.general_score())
        raise ValueError(f"{self.abbreviation} has no roster players available for {slot}.")

    def unique_starters(self, slots: list[str]) -> list[PlayerSnapshot]:
        selected = []
        used = set()
        for slot in slots:
            for player in self.candidates(slot):
                if player.player_id not in used:
                    selected.append(player)
                    used.add(player.player_id)
                    break
        return selected

    def score_for_slot(self, player: PlayerSnapshot, slot: str) -> float:
        slot = slot.upper()
        if slot == "QB":
            return max(player.role("pocket_qb"), player.role("scrambling_qb"), weighted_average(player, QB_PASS_WEIGHTS))
        if slot == "RB":
            return max(player.role("power_rb"), player.role("elusive_rb"), weighted_average(player, RB_RUN_WEIGHTS))
        if slot in {"LWR", "RWR", "SWR"}:
            return max(player.role("boundary_wr"), player.role("slot_wr"), weighted_average(player, RECEIVER_WEIGHTS))
        if slot == "TE":
            return max(player.role("inline_te"), player.role("move_te"), weighted_average(player, RECEIVER_WEIGHTS))
        if slot in {"LT", "RT"}:
            return max(player.role("pass_protecting_ot"), weighted_average(player, PASS_BLOCK_WEIGHTS))
        if slot in {"LG", "C", "RG"}:
            return max(player.role("interior_run_blocker"), weighted_average(player, RUN_BLOCK_WEIGHTS))
        if slot in {"LEDGE", "REDGE"}:
            return max(player.role("speed_edge"), player.role("power_edge"), weighted_average(player, PASS_RUSH_WEIGHTS))
        if slot in {"LDL", "NT", "RDL"}:
            return max(player.role("interior_rusher"), player.role("nose_run_stopping_dt"), weighted_average(player, RUN_DEF_WEIGHTS))
        if slot in {"MLB", "WLB", "SLB"}:
            return max(player.role("box_lb"), player.role("coverage_lb"), weighted_average(player, TACKLE_WEIGHTS))
        if slot in {"LCB", "RCB", "NB"}:
            return max(player.role("man_cb"), player.role("zone_cb"), weighted_average(player, COVERAGE_WEIGHTS))
        if slot in {"FS", "SS"}:
            return max(player.role("deep_safety"), player.role("box_safety"), weighted_average(player, COVERAGE_WEIGHTS))
        if slot in {"PK", "K", "KO"}:
            return weighted_average(player, KICK_WEIGHTS)
        if slot in {"PT", "P"}:
            return weighted_average(player, PUNT_WEIGHTS)
        return player.general_score()

    def offensive_line(self) -> list[PlayerSnapshot]:
        return self.unique_starters(["LT", "LG", "C", "RG", "RT"])

    def receiving_options(self) -> list[PlayerSnapshot]:
        options = self.unique_starters(["LWR", "RWR", "SWR", "TE", "RB"])
        return options or self.roster[:5]

    def defensive_front(self) -> list[PlayerSnapshot]:
        return self.unique_starters(["LEDGE", "LDL", "NT", "RDL", "REDGE"])

    def linebackers(self) -> list[PlayerSnapshot]:
        return self.unique_starters(["MLB", "WLB", "SLB"])

    def secondary(self) -> list[PlayerSnapshot]:
        return self.unique_starters(["LCB", "RCB", "NB", "FS", "SS"])

    def run_block_score(self) -> float:
        blockers = self.offensive_line() + self.unique_starters(["TE", "FB"])
        return average(weighted_average(p, RUN_BLOCK_WEIGHTS) for p in blockers)

    def pass_block_score(self) -> float:
        blockers = self.offensive_line() + self.unique_starters(["TE"])
        return average(weighted_average(p, PASS_BLOCK_WEIGHTS) for p in blockers)

    def run_defense_score(self) -> float:
        defenders = self.defensive_front() + self.linebackers()
        return average(weighted_average(p, RUN_DEF_WEIGHTS) for p in defenders)

    def pass_rush_score(self) -> float:
        rushers = self.defensive_front()
        return average(weighted_average(p, PASS_RUSH_WEIGHTS) for p in rushers)

    def coverage_score(self) -> float:
        defenders = self.secondary() + self.linebackers()
        return average(weighted_average(p, COVERAGE_WEIGHTS) for p in defenders)

    def tackling_score(self) -> float:
        defenders = self.defensive_front() + self.linebackers() + self.secondary()
        return average(weighted_average(p, TACKLE_WEIGHTS) for p in defenders)

    def discipline_score(self) -> float:
        starters = (
            self.offensive_line()
            + self.receiving_options()
            + self.defensive_front()
            + self.linebackers()
            + self.secondary()
        )
        return average(p.rating("discipline") for p in starters)


@dataclass
class PlayEvent:
    play_number: int
    drive_number: int
    quarter: int
    clock_tenths: int
    offense_team_id: int
    defense_team_id: int
    down: int
    distance: int
    yardline: int
    play_type: str
    concept: str
    yards_gained: int = 0
    offense_player_id: int | None = None
    target_player_id: int | None = None
    defense_player_id: int | None = None
    is_touchdown: int = 0
    is_turnover: int = 0
    clock_elapsed_tenths: int = 0
    runoff_tenths: int = 0
    description: str = ""


@dataclass
class DriveRecord:
    drive_number: int
    offense_team_id: int
    defense_team_id: int
    start_quarter: int
    start_clock_tenths: int
    start_yardline: int
    end_quarter: int = 0
    end_clock_tenths: int = 0
    end_yardline: int = 0
    result: str = ""
    plays: int = 0
    yards: int = 0
    points: int = 0
    time_elapsed_tenths: int = 0


@dataclass
class GameResult:
    schedule_game_id: int | None
    season: int
    week: int | None
    away: TeamSnapshot
    home: TeamSnapshot
    away_score: int
    home_score: int
    seed: int
    plays: list[PlayEvent]
    drives: list[DriveRecord]
    team_stats: dict[int, Counter]
    player_stats: dict[int, Counter]
    status: str = "final"


def table_columns(con: sqlite3.Connection, table_name: str) -> set[str]:
    rows = con.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] if isinstance(row, sqlite3.Row) else row[1] for row in rows}


def ensure_column(con: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    if column_name not in table_columns(con, table_name):
        con.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS game_sim_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_game_id INTEGER REFERENCES season_games(game_id) ON DELETE SET NULL,
            season INTEGER NOT NULL,
            week INTEGER,
            away_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            home_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            seed INTEGER NOT NULL,
            engine_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'final',
            away_score INTEGER NOT NULL,
            home_score INTEGER NOT NULL,
            total_plays INTEGER NOT NULL,
            total_drives INTEGER NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS game_sim_drives (
            run_id INTEGER NOT NULL REFERENCES game_sim_runs(run_id) ON DELETE CASCADE,
            drive_number INTEGER NOT NULL,
            offense_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            defense_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            start_quarter INTEGER NOT NULL,
            start_clock_tenths INTEGER NOT NULL,
            end_quarter INTEGER NOT NULL,
            end_clock_tenths INTEGER NOT NULL,
            start_yardline INTEGER NOT NULL,
            end_yardline INTEGER NOT NULL,
            result TEXT NOT NULL,
            plays INTEGER NOT NULL,
            yards INTEGER NOT NULL,
            points INTEGER NOT NULL,
            time_elapsed_tenths INTEGER NOT NULL,
            PRIMARY KEY(run_id, drive_number)
        );

        CREATE TABLE IF NOT EXISTS game_sim_plays (
            run_id INTEGER NOT NULL REFERENCES game_sim_runs(run_id) ON DELETE CASCADE,
            play_number INTEGER NOT NULL,
            drive_number INTEGER NOT NULL,
            quarter INTEGER NOT NULL,
            clock_tenths INTEGER NOT NULL,
            offense_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            defense_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            down INTEGER NOT NULL,
            distance INTEGER NOT NULL,
            yardline INTEGER NOT NULL,
            play_type TEXT NOT NULL,
            concept TEXT,
            offense_player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            target_player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            defense_player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            yards_gained INTEGER NOT NULL DEFAULT 0,
            is_touchdown INTEGER NOT NULL DEFAULT 0,
            is_turnover INTEGER NOT NULL DEFAULT 0,
            clock_elapsed_tenths INTEGER NOT NULL DEFAULT 0,
            runoff_tenths INTEGER NOT NULL DEFAULT 0,
            description TEXT,
            PRIMARY KEY(run_id, play_number)
        );

        CREATE INDEX IF NOT EXISTS idx_game_sim_plays_run_drive
            ON game_sim_plays(run_id, drive_number, play_number);

        CREATE TABLE IF NOT EXISTS game_team_stats (
            run_id INTEGER NOT NULL REFERENCES game_sim_runs(run_id) ON DELETE CASCADE,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            stat_key TEXT NOT NULL,
            stat_value REAL NOT NULL,
            PRIMARY KEY(run_id, team_id, stat_key)
        );

        CREATE TABLE IF NOT EXISTS game_player_stats (
            run_id INTEGER NOT NULL REFERENCES game_sim_runs(run_id) ON DELETE CASCADE,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            stat_key TEXT NOT NULL,
            stat_value REAL NOT NULL,
            PRIMARY KEY(run_id, player_id, stat_key)
        );

        CREATE TABLE IF NOT EXISTS season_team_records (
            season INTEGER NOT NULL,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            ties INTEGER NOT NULL DEFAULT 0,
            points_for INTEGER NOT NULL DEFAULT 0,
            points_against INTEGER NOT NULL DEFAULT 0,
            conference_wins INTEGER NOT NULL DEFAULT 0,
            conference_losses INTEGER NOT NULL DEFAULT 0,
            conference_ties INTEGER NOT NULL DEFAULT 0,
            division_wins INTEGER NOT NULL DEFAULT 0,
            division_losses INTEGER NOT NULL DEFAULT 0,
            division_ties INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(season, team_id)
        );

        CREATE TABLE IF NOT EXISTS season_team_stats (
            season INTEGER NOT NULL,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            stat_key TEXT NOT NULL,
            stat_value REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(season, team_id, stat_key)
        );

        CREATE TABLE IF NOT EXISTS season_player_stats (
            season INTEGER NOT NULL,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            stat_key TEXT NOT NULL,
            stat_value REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(season, player_id, team_id, stat_key)
        );

        CREATE INDEX IF NOT EXISTS idx_season_player_stats_leaders
            ON season_player_stats(season, stat_key, stat_value DESC);

        DROP VIEW IF EXISTS season_standings_view;
        CREATE VIEW season_standings_view AS
        SELECT
            str.season,
            t.team_id,
            t.abbreviation,
            t.city,
            t.nickname,
            t.conference,
            t.division,
            COALESCE(str.wins, 0) AS wins,
            COALESCE(str.losses, 0) AS losses,
            COALESCE(str.ties, 0) AS ties,
            COALESCE(str.points_for, 0) AS points_for,
            COALESCE(str.points_against, 0) AS points_against,
            COALESCE(str.points_for, 0) - COALESCE(str.points_against, 0) AS point_diff,
            COALESCE(str.conference_wins, 0) AS conference_wins,
            COALESCE(str.conference_losses, 0) AS conference_losses,
            COALESCE(str.conference_ties, 0) AS conference_ties,
            COALESCE(str.division_wins, 0) AS division_wins,
            COALESCE(str.division_losses, 0) AS division_losses,
            COALESCE(str.division_ties, 0) AS division_ties,
            CASE
                WHEN COALESCE(str.wins, 0) + COALESCE(str.losses, 0) + COALESCE(str.ties, 0) = 0
                THEN 0.0
                ELSE (COALESCE(str.wins, 0) + COALESCE(str.ties, 0) * 0.5)
                     / (COALESCE(str.wins, 0) + COALESCE(str.losses, 0) + COALESCE(str.ties, 0))
            END AS win_pct,
            str.updated_at
        FROM teams t
        LEFT JOIN season_team_records str ON str.team_id = t.team_id;

        DROP VIEW IF EXISTS season_player_stats_view;
        CREATE VIEW season_player_stats_view AS
        SELECT
            sps.season,
            sps.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            sps.team_id,
            t.abbreviation AS team,
            sps.stat_key,
            sps.stat_value,
            sps.updated_at
        FROM season_player_stats sps
        JOIN players p ON p.player_id = sps.player_id
        JOIN teams t ON t.team_id = sps.team_id;

        DROP VIEW IF EXISTS season_team_stats_view;
        CREATE VIEW season_team_stats_view AS
        SELECT
            sts.season,
            sts.team_id,
            t.abbreviation AS team,
            t.city,
            t.nickname,
            sts.stat_key,
            sts.stat_value,
            sts.updated_at
        FROM season_team_stats sts
        JOIN teams t ON t.team_id = sts.team_id;
        """
    )
    ensure_column(con, "game_sim_runs", "counts_for_stats", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(con, "game_sim_runs", "counts_for_standings", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(con, "game_sim_runs", "superseded_by_run_id", "INTEGER")
    con.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_game_sim_runs_schedule
            ON game_sim_runs(schedule_game_id, counts_for_stats, counts_for_standings);
        """
    )


def load_team(con: sqlite3.Connection, team_id: int, season: int) -> TeamSnapshot:
    team_row = con.execute("SELECT * FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    if not team_row:
        raise ValueError(f"Team id not found: {team_id}")

    player_rows = con.execute(
        """
        SELECT p.*
        FROM players p
        LEFT JOIN roster_status_types rst ON rst.status_code = p.status
        WHERE p.team_id = ?
          AND COALESCE(
                rst.counts_against_roster_limit,
                CASE WHEN COALESCE(p.status, 'Active') NOT IN ('Retired', 'Free Agent') THEN 1 ELSE 0 END
              ) = 1
        """,
        (team_id,),
    ).fetchall()
    player_ids = [int(row["player_id"]) for row in player_rows]
    if not player_ids:
        raise ValueError(f"{team_row['abbreviation']} has no roster players.")

    placeholders = ",".join("?" for _ in player_ids)
    rating_rows = con.execute(
        f"""
        SELECT player_id, rating_key, rating_value
        FROM player_ratings
        WHERE season = ? AND player_id IN ({placeholders})
        """,
        (season, *player_ids),
    ).fetchall()
    ratings_by_player: dict[int, dict[str, int]] = defaultdict(dict)
    for row in rating_rows:
        ratings_by_player[int(row["player_id"])][row["rating_key"]] = int(row["rating_value"])

    role_rows = con.execute(
        f"""
        SELECT player_id, role_key, role_score
        FROM player_role_scores
        WHERE season = ? AND player_id IN ({placeholders})
        """,
        (season, *player_ids),
    ).fetchall()
    role_by_player: dict[int, dict[str, float]] = defaultdict(dict)
    for row in role_rows:
        role_by_player[int(row["player_id"])][row["role_key"]] = float(row["role_score"])

    players_by_id = {}
    roster = []
    for row in player_rows:
        name = f"{row['first_name']} {row['last_name']}"
        player = PlayerSnapshot(
            player_id=int(row["player_id"]),
            name=name,
            position=row["position"],
            ratings=ratings_by_player[int(row["player_id"])],
            role_scores=role_by_player[int(row["player_id"])],
        )
        players_by_id[player.player_id] = player
        roster.append(player)

    depth: dict[str, list[PlayerSnapshot]] = defaultdict(list)
    depth_rows = con.execute(
        """
        SELECT *
        FROM depth_charts
        WHERE team_id = ?
        ORDER BY position, depth_rank
        """,
        (team_id,),
    ).fetchall()
    for row in depth_rows:
        player = players_by_id.get(int(row["player_id"]))
        if player:
            depth[row["position"].upper()].append(player)

    return TeamSnapshot(
        team_id=int(team_row["team_id"]),
        abbreviation=team_row["abbreviation"],
        city=team_row["city"],
        nickname=team_row["nickname"],
        conference=team_row["conference"],
        division=team_row["division"],
        roster=roster,
        depth=dict(depth),
    )


class MatchEngine:
    def __init__(
        self,
        *,
        away: TeamSnapshot,
        home: TeamSnapshot,
        season: int,
        week: int | None,
        schedule_game_id: int | None,
        seed: int | None = None,
    ) -> None:
        self.away = away
        self.home = home
        self.season = season
        self.week = week
        self.schedule_game_id = schedule_game_id
        self.seed = int(seed if seed is not None else random.randrange(1, 2**31))
        self.rng = random.Random(self.seed)
        self.score = {away.team_id: 0, home.team_id: 0}
        self.team_stats: dict[int, Counter] = defaultdict(Counter)
        self.player_stats: dict[int, Counter] = defaultdict(Counter)
        self.plays: list[PlayEvent] = []
        self.drives: list[DriveRecord] = []
        self.quarter = 1
        self.clock_tenths = REGULATION_QUARTER_TENTHS
        self.play_number = 0
        self.drive_number = 0
        self.first_half_receiver = self.rng.choice([away, home])
        self.second_half_receiver = home if self.first_half_receiver.team_id == away.team_id else away
        self.ot_first_drive_team_id: int | None = None
        self.ot_possessions: set[int] = set()
        self._last_play_concept: str | None = None

    def opponent(self, team: TeamSnapshot) -> TeamSnapshot:
        return self.home if team.team_id == self.away.team_id else self.away

    def current_score_diff(self, offense: TeamSnapshot) -> int:
        return self.score[offense.team_id] - self.score[self.opponent(offense).team_id]

    def add_score(self, team: TeamSnapshot, points: int) -> None:
        self.score[team.team_id] += points
        self.team_stats[team.team_id]["points"] += points

    def add_play_event(self, event: PlayEvent) -> None:
        self.plays.append(event)

    def add_snap(self, player: PlayerSnapshot | None, snap_key: str) -> None:
        if not player:
            return
        self.player_stats[player.player_id][snap_key] += 1
        self.player_stats[player.player_id]["total_snaps"] += 1

    def add_snaps(self, players: list[PlayerSnapshot], snap_key: str) -> None:
        seen: set[int] = set()
        for player in players:
            if player.player_id in seen:
                continue
            seen.add(player.player_id)
            self.add_snap(player, snap_key)

    def offensive_snap_players(self, offense: TeamSnapshot, concept: str) -> list[PlayerSnapshot]:
        slots = ["QB", "RB", "LT", "LG", "C", "RG", "RT", "TE", "LWR", "RWR"]
        fullback = offense.starter("FB")
        if concept in {"inside_zone", "power"} and fullback and fullback.position == "FB":
            slots.append("FB")
        else:
            slots.append("SWR")
        return offense.unique_starters(slots)

    def defensive_snap_players(self, defense: TeamSnapshot, play_type: str, concept: str) -> list[PlayerSnapshot]:
        if play_type == "pass":
            slots = ["LEDGE", "LDL", "RDL", "REDGE", "MLB", "WLB", "LCB", "RCB", "NB", "FS", "SS"]
        elif concept in {"inside_zone", "power"}:
            slots = ["LEDGE", "LDL", "NT", "RDL", "REDGE", "MLB", "WLB", "SLB", "LCB", "RCB", "SS"]
        else:
            slots = ["LEDGE", "LDL", "NT", "REDGE", "MLB", "WLB", "LCB", "RCB", "NB", "FS", "SS"]
        return defense.unique_starters(slots)

    def special_teams_snap_players(self, team: TeamSnapshot, play_type: str) -> list[PlayerSnapshot]:
        if play_type in {"field_goal", "extra_point"}:
            return team.unique_starters(["PK", "LS", "PT"])
        if play_type == "punt":
            return team.unique_starters(["PT", "LS"])
        return []

    def count_scrimmage_snap(self, offense: TeamSnapshot, defense: TeamSnapshot, play_type: str, concept: str) -> None:
        self.add_snaps(self.offensive_snap_players(offense, concept), "offensive_snaps")
        self.add_snaps(self.defensive_snap_players(defense, play_type, concept), "defensive_snaps")

    def count_special_teams_snap(self, team: TeamSnapshot, play_type: str) -> None:
        self.add_snaps(self.special_teams_snap_players(team, play_type), "special_teams_snaps")

    def consume_clock(self, live_tenths: int, runoff_tenths: int) -> tuple[int, int]:
        live_tenths = max(1, int(live_tenths))
        runoff_tenths = max(0, int(runoff_tenths))
        total = live_tenths + runoff_tenths
        consumed = min(self.clock_tenths, total)
        self.clock_tenths -= consumed
        consumed_runoff = max(0, consumed - live_tenths)
        return consumed, consumed_runoff

    def advance_dead_quarter_if_needed(self, offense: TeamSnapshot, field_pos: int, down: int, distance: int) -> tuple[bool, TeamSnapshot, int, int, int]:
        if self.clock_tenths > 0:
            return False, offense, field_pos, down, distance
        if self.quarter in (1, 3):
            self.quarter += 1
            self.clock_tenths = REGULATION_QUARTER_TENTHS
            return False, offense, field_pos, down, distance
        if self.quarter == 2:
            self.quarter = 3
            self.clock_tenths = REGULATION_QUARTER_TENTHS
            return True, self.second_half_receiver, 25, 1, 10
        return True, offense, field_pos, down, distance

    def play_call_is_pass(self, offense: TeamSnapshot, defense: TeamSnapshot, down: int, distance: int, field_pos: int) -> bool:
        pass_rate = 0.56
        if down == 1:
            pass_rate -= 0.04
        elif down == 2 and distance >= 8:
            pass_rate += 0.09
        elif down == 3 and distance >= 7:
            pass_rate += 0.24
        elif down == 3 and distance <= 3:
            pass_rate -= 0.12
        if field_pos >= 80:
            pass_rate -= 0.05
        score_diff = self.current_score_diff(offense)
        late = self.quarter >= 4 or self.quarter == 5
        if late and score_diff < 0:
            pass_rate += min(0.20, abs(score_diff) * 0.012)
        elif late and score_diff > 0:
            pass_rate -= min(0.18, score_diff * 0.010)

        qb = offense.starter("QB")
        pass_identity = weighted_average(qb, QB_PASS_WEIGHTS)
        rb = offense.starter("RB")
        run_identity = weighted_average(rb, RB_RUN_WEIGHTS)
        pass_rate += (pass_identity - run_identity) * 0.002
        return self.rng.random() < clamp(pass_rate, 0.25, 0.86)

    def fourth_down_decision(self, offense: TeamSnapshot, defense: TeamSnapshot, down: int, distance: int, field_pos: int) -> str:
        fg_distance = 100 - field_pos + 17
        score_diff = self.current_score_diff(offense)
        late_trailing = self.quarter >= 4 and score_diff < 0
        if field_pos >= 63 and fg_distance <= 57 and not (late_trailing and distance <= 4 and field_pos >= 55):
            return "field_goal"
        go_prob = 0.04
        if field_pos >= 45 and distance <= 1:
            go_prob = 0.62
        elif field_pos >= 50 and distance <= 3:
            go_prob = 0.38
        elif late_trailing and field_pos >= 45 and distance <= 6:
            go_prob = 0.55
        if self.rng.random() < go_prob:
            return "go"
        return "punt"

    def choose_run_concept(self, offense: TeamSnapshot, defense: TeamSnapshot, down: int, distance: int, field_pos: int) -> str:
        return self.rng.choice(["inside_zone", "outside_zone", "power", "draw"])

    def choose_pass_concept(self, offense: TeamSnapshot, defense: TeamSnapshot, down: int, distance: int, field_pos: int) -> str:
        return weighted_choice(
            self.rng,
            [
                ("quick", 1.25 if distance <= 5 else 0.85),
                ("short", 1.15),
                ("intermediate", 1.00),
                ("deep", 0.45 if down < 3 else 0.70),
                ("screen", 0.45),
            ],
        )

    def maybe_penalty(self, offense: TeamSnapshot, defense: TeamSnapshot, play_type: str) -> tuple[int, str, bool] | None:
        offense_discipline = offense.discipline_score()
        defense_discipline = defense.discipline_score()
        base = 0.041
        penalty_chance = base + max(0, 60 - offense_discipline) * 0.00045 + max(0, 60 - defense_discipline) * 0.00025
        if self.rng.random() >= penalty_chance:
            return None
        if self.rng.random() < 0.62:
            yards = -10 if play_type == "pass" and self.rng.random() < 0.55 else -5
            label = "offensive holding" if yards == -10 else "false start"
            self.team_stats[offense.team_id]["penalties"] += 1
            self.team_stats[offense.team_id]["penalty_yards"] += abs(yards)
            return yards, label, False
        if self.rng.random() < 0.70:
            yards = 5
            label = "defensive offside"
            automatic_first_down = False
        else:
            yards = 5
            label = "defensive holding"
            automatic_first_down = True
        self.team_stats[defense.team_id]["penalties"] += 1
        self.team_stats[defense.team_id]["penalty_yards"] += yards
        return yards, label, automatic_first_down

    def run_play(
        self,
        offense: TeamSnapshot,
        defense: TeamSnapshot,
        down: int,
        distance: int,
        field_pos: int,
    ) -> tuple[str, int, int, int, str, TeamSnapshot, int, int, int, PlayerSnapshot | None, PlayerSnapshot | None]:
        qb = offense.starter("QB")
        rb_candidates = offense.candidates("RB")[:3]
        if not rb_candidates:
            rb_candidates = [offense.starter("RB")]
        qb_scramble_score = weighted_average(qb, QB_SCRAMBLE_WEIGHTS)
        designed_qb_run = qb_scramble_score >= 76 and self.rng.random() < 0.075
        runner = qb if designed_qb_run else weighted_choice(
            self.rng,
            [(player, 1.0 / (idx + 1.4)) for idx, player in enumerate(rb_candidates)],
        )
        concept = self.choose_run_concept(offense, defense, down, distance, field_pos)
        self._last_play_concept = concept
        run_block = offense.run_block_score()
        run_def = defense.run_defense_score()
        runner_score = weighted_average(runner, RB_RUN_WEIGHTS if runner.player_id != qb.player_id else QB_SCRAMBLE_WEIGHTS)
        tackling = defense.tackling_score()
        trench_advantage = run_block - run_def
        runner_advantage = runner_score - tackling

        stuff_chance = clamp(0.155 - trench_advantage * 0.0022 - runner_advantage * 0.0007, 0.055, 0.285)
        explosive_chance = clamp(0.030 + (runner.rating("speed") - 70) * 0.0018 + (runner.rating("elusiveness") - tackling) * 0.0012, 0.010, 0.115)

        if self.rng.random() < stuff_chance:
            yards = int(round(self.rng.gauss(-1.2, 1.5)))
        else:
            mean = 4.05 + trench_advantage * 0.045 + runner_advantage * 0.020
            yards = int(round(self.rng.gauss(mean, 3.0)))
            if self.rng.random() < explosive_chance:
                yards += int(round(self.rng.lognormvariate(2.15, 0.42)))
        yards = int(clamp(yards, -8, 80))
        if field_pos + yards >= 100:
            yards = max(0, 100 - field_pos)

        fumble_chance = clamp(0.010 + (58 - runner.rating("ball_security")) * 0.00038 + (defense.tackling_score() - 65) * 0.00008, 0.002, 0.035)
        if self.rng.random() < fumble_chance:
            recovery_spot = int(clamp(field_pos + yards, 1, 99))
            return_yards = max(0, int(round(self.rng.gauss(7, 6))))
            new_field = int(clamp(100 - recovery_spot + return_yards, 1, 99))
            defender = self.select_tackler(defense, yards)
            self.team_stats[offense.team_id]["plays"] += 1
            self.team_stats[offense.team_id]["rush_attempts"] += 1
            self.team_stats[offense.team_id]["rush_yards"] += yards
            self.team_stats[offense.team_id]["total_yards"] += yards
            self.team_stats[offense.team_id]["turnovers"] += 1
            self.team_stats[offense.team_id]["fumbles_lost"] += 1
            self.player_stats[runner.player_id]["rush_attempts"] += 1
            self.player_stats[runner.player_id]["rush_yards"] += yards
            self.player_stats[runner.player_id]["fumbles"] += 1
            self.player_stats[defender.player_id]["tackles"] += 1
            self.player_stats[defender.player_id]["fumble_recoveries"] += 1
            self.player_stats[defender.player_id]["forced_fumbles"] += 1
            desc = f"{runner.name} runs for {yards} but fumbles. {defender.name} recovers."
            return "turnover", yards, 0, 0, desc, defense, new_field, 1, 10, runner, defender

        new_field = field_pos + yards
        touchdown = new_field >= 100
        tackler = None if touchdown else self.select_tackler(defense, yards)
        self.team_stats[offense.team_id]["plays"] += 1
        self.team_stats[offense.team_id]["rush_attempts"] += 1
        self.team_stats[offense.team_id]["rush_yards"] += yards
        self.team_stats[offense.team_id]["total_yards"] += yards
        self.player_stats[runner.player_id]["rush_attempts"] += 1
        self.player_stats[runner.player_id]["rush_yards"] += yards
        if tackler:
            self.player_stats[tackler.player_id]["tackles"] += 1
        if touchdown:
            self.add_score(offense, 6)
            self.player_stats[runner.player_id]["rush_tds"] += 1
            xp = self.extra_point(offense)
            desc = f"{runner.name} scores on a {max(0, 100 - field_pos)} yard run. {xp}"
            return "touchdown", yards, 6, 0, desc, self.opponent(offense), 25, 1, 10, runner, None

        desc = f"{runner.name} runs {yards} yards"
        if tackler:
            desc += f", tackled by {tackler.name}"
        return "normal", yards, 0, 0, desc + ".", offense, int(clamp(new_field, 1, 99)), down, distance, runner, tackler

    def pass_play(self, offense: TeamSnapshot, defense: TeamSnapshot, down: int, distance: int, field_pos: int) -> tuple[str, int, int, int, str, TeamSnapshot, int, int, int, PlayerSnapshot | None, PlayerSnapshot | None]:
        qb = offense.starter("QB")
        concept = self.choose_pass_concept(offense, defense, down, distance, field_pos)
        self._last_play_concept = concept
        depth_profile = {
            "quick": (3, 2),
            "short": (6, 3),
            "intermediate": (11, 4),
            "deep": (21, 7),
            "screen": (0, 2),
        }[concept]
        pass_block = offense.pass_block_score()
        pass_rush = defense.pass_rush_score()
        qb_processing = average([qb.rating("processing_speed"), qb.rating("play_recognition"), qb.rating("throw_release")])
        pressure_chance = clamp(0.270 + (pass_rush - pass_block) * 0.0062 - (qb_processing - 65) * 0.0013, 0.080, 0.58)
        pressured = self.rng.random() < pressure_chance
        concept_sack_modifier = {
            "screen": -0.095,
            "quick": -0.075,
            "short": -0.035,
            "intermediate": 0.020,
            "deep": 0.065,
        }[concept]
        escape_score = average([qb.rating("speed"), qb.rating("acceleration"), qb.rating("agility"), qb.rating("processing_speed")])
        sack_chance = clamp(
            0.300
            + concept_sack_modifier
            + (pass_rush - pass_block) * 0.0060
            - (qb.rating("throw_release") - 65) * 0.0020
            - (escape_score - 65) * 0.0011,
            0.075,
            0.500,
        )
        if pressured and self.rng.random() < sack_chance:
            rusher = self.select_pass_rusher(defense)
            loss = int(clamp(round(self.rng.gauss(6.3 + (pass_rush - pass_block) * 0.035, 2.2)), 1, 15))
            self.team_stats[offense.team_id]["plays"] += 1
            self.team_stats[offense.team_id]["sacks_allowed"] += 1
            self.team_stats[offense.team_id]["pass_yards"] -= loss
            self.team_stats[offense.team_id]["total_yards"] -= loss
            self.team_stats[defense.team_id]["sacks"] += 1
            self.player_stats[qb.player_id]["sacks_taken"] += 1
            self.player_stats[rusher.player_id]["sacks"] += 1
            self.player_stats[rusher.player_id]["tackles"] += 1
            desc = f"{rusher.name} sacks {qb.name} for a loss of {loss}."
            return "normal", -loss, 0, 0, desc, offense, int(clamp(field_pos - loss, 1, 99)), down, distance, qb, rusher

        target = self.select_receiver(offense, concept)
        defender = self.select_coverage_defender(defense, target, concept)
        air_yards = int(clamp(round(self.rng.gauss(*depth_profile)), -2, 48))
        if field_pos + air_yards > 99:
            air_yards = max(1, 100 - field_pos)

        qb_accuracy_key = "pass_accuracy_short"
        if air_yards >= 18:
            qb_accuracy_key = "pass_accuracy_deep"
        elif air_yards >= 9:
            qb_accuracy_key = "pass_accuracy_mid"

        qb_score = average([
            qb.rating(qb_accuracy_key),
            qb.rating("platform_control"),
            qb.rating("composure"),
            qb.rating("processing_speed"),
        ])
        receiver_score = weighted_average(target, RECEIVER_WEIGHTS)
        coverage_score = weighted_average(defender, COVERAGE_WEIGHTS)
        separation = receiver_score - coverage_score
        depth_penalty = max(0, air_yards - 5) * 0.014
        completion_chance = 0.630 + (qb_score - 65) * 0.0036 + separation * 0.0027 - depth_penalty
        if concept == "screen":
            completion_chance += 0.07
        if pressured:
            completion_chance -= 0.120
        completion_chance = clamp(completion_chance, 0.205, 0.865)

        interception_chance = 0.012 + max(0, air_yards - 8) * 0.00075
        interception_chance += max(0, coverage_score - qb_score) * 0.00035
        interception_chance += max(0, 62 - qb.rating("discipline")) * 0.00018
        if pressured:
            interception_chance += 0.011
        interception_chance = clamp(interception_chance, 0.002, 0.065)

        self.team_stats[offense.team_id]["plays"] += 1
        self.team_stats[offense.team_id]["pass_attempts"] += 1
        self.player_stats[qb.player_id]["pass_attempts"] += 1
        self.player_stats[target.player_id]["targets"] += 1

        if self.rng.random() < interception_chance:
            return_yards = max(0, int(round(self.rng.gauss(11, 9))))
            pick_spot = int(clamp(field_pos + max(0, air_yards), 1, 99))
            new_field = int(clamp(100 - pick_spot + return_yards, 1, 99))
            self.team_stats[offense.team_id]["turnovers"] += 1
            self.team_stats[offense.team_id]["interceptions_thrown"] += 1
            self.team_stats[defense.team_id]["interceptions"] += 1
            self.player_stats[qb.player_id]["interceptions_thrown"] += 1
            self.player_stats[defender.player_id]["interceptions"] += 1
            self.player_stats[defender.player_id]["pass_deflections"] += 1
            desc = f"{qb.name} is intercepted by {defender.name} targeting {target.name}."
            return "turnover", 0, 0, 0, desc, defense, new_field, 1, 10, target, defender

        if self.rng.random() > completion_chance:
            if self.rng.random() < 0.35:
                self.player_stats[defender.player_id]["pass_deflections"] += 1
                desc = f"{qb.name}'s pass for {target.name} is broken up by {defender.name}."
            else:
                desc = f"{qb.name}'s pass for {target.name} falls incomplete."
            return "normal", 0, 0, 0, desc, offense, field_pos, down, distance, target, defender

        yac_score = weighted_average(target, YAC_WEIGHTS)
        tackle_score = defense.tackling_score()
        yac_mean = {
            "quick": 3.3,
            "short": 3.0,
            "intermediate": 2.0,
            "deep": 1.1,
            "screen": 5.8,
        }[concept] + (yac_score - tackle_score) * 0.035
        yac = max(0, int(round(self.rng.gauss(yac_mean, 3.2))))
        if self.rng.random() < clamp((yac_score - tackle_score) * 0.0012 + 0.022, 0.006, 0.082):
            yac += int(round(self.rng.lognormvariate(1.85, 0.36)))
        yards = int(clamp(max(0, air_yards) + yac, -5, 90))
        if field_pos + yards >= 100:
            yards = max(0, 100 - field_pos)

        catch_fumble = clamp(0.006 + (58 - target.rating("ball_security")) * 0.00025 + (defender.rating("forced_fumble") - 60) * 0.00008, 0.001, 0.027)
        if self.rng.random() < catch_fumble:
            fumble_spot = int(clamp(field_pos + yards, 1, 99))
            new_field = int(clamp(100 - fumble_spot + max(0, int(round(self.rng.gauss(6, 5)))), 1, 99))
            self.team_stats[offense.team_id]["pass_completions"] += 1
            self.team_stats[offense.team_id]["pass_yards"] += yards
            self.team_stats[offense.team_id]["total_yards"] += yards
            self.team_stats[offense.team_id]["turnovers"] += 1
            self.team_stats[offense.team_id]["fumbles_lost"] += 1
            self.player_stats[qb.player_id]["pass_completions"] += 1
            self.player_stats[qb.player_id]["pass_yards"] += yards
            self.player_stats[target.player_id]["receptions"] += 1
            self.player_stats[target.player_id]["receiving_yards"] += yards
            self.player_stats[target.player_id]["fumbles"] += 1
            self.player_stats[defender.player_id]["forced_fumbles"] += 1
            self.player_stats[defender.player_id]["fumble_recoveries"] += 1
            desc = f"{target.name} catches it for {yards}, then fumbles. {defender.name} recovers."
            return "turnover", yards, 0, 0, desc, defense, new_field, 1, 10, target, defender

        new_field = field_pos + yards
        touchdown = new_field >= 100
        self.team_stats[offense.team_id]["pass_completions"] += 1
        self.team_stats[offense.team_id]["pass_yards"] += yards
        self.team_stats[offense.team_id]["total_yards"] += yards
        self.player_stats[qb.player_id]["pass_completions"] += 1
        self.player_stats[qb.player_id]["pass_yards"] += yards
        self.player_stats[target.player_id]["receptions"] += 1
        self.player_stats[target.player_id]["receiving_yards"] += yards
        if not touchdown:
            tackler = self.select_tackler(defense, yards)
            self.player_stats[tackler.player_id]["tackles"] += 1
            desc = f"{qb.name} completes to {target.name} for {yards}, tackled by {tackler.name}."
        else:
            self.add_score(offense, 6)
            self.team_stats[offense.team_id]["pass_tds"] += 1
            self.player_stats[qb.player_id]["pass_tds"] += 1
            self.player_stats[target.player_id]["receiving_tds"] += 1
            xp = self.extra_point(offense)
            desc = f"{qb.name} hits {target.name} for a {max(0, 100 - field_pos)} yard touchdown. {xp}"
            return "touchdown", yards, 6, 0, desc, self.opponent(offense), 25, 1, 10, target, defender

        return "normal", yards, 0, 0, desc, offense, int(clamp(new_field, 1, 99)), down, distance, target, defender

    def select_receiver(self, offense: TeamSnapshot, concept: str) -> PlayerSnapshot:
        options = []
        for idx, player in enumerate(offense.receiving_options()):
            weight = weighted_average(player, RECEIVER_WEIGHTS)
            if player.position == "WR":
                weight *= 1.15
            if player.position == "TE" and concept in {"short", "intermediate"}:
                weight *= 1.10
            if player.position == "RB" and concept == "screen":
                weight *= 1.75
            talent_bonus = 1.0 + clamp((weighted_average(player, RECEIVER_WEIGHTS) - 72) * 0.007, -0.06, 0.18)
            weight *= talent_bonus
            weight *= 1.0 / (idx * 0.08 + 1.0)
            options.append((player, weight))
        return weighted_choice(self.rng, options)

    def select_coverage_defender(self, defense: TeamSnapshot, target: PlayerSnapshot, concept: str) -> PlayerSnapshot:
        if target.position == "WR":
            slots = ["LCB", "RCB", "NB"] if concept != "deep" else ["LCB", "RCB", "FS", "SS"]
        elif target.position == "TE":
            slots = ["SS", "FS", "MLB", "WLB"]
        else:
            slots = ["MLB", "WLB", "NB", "SS"]
        defenders = defense.unique_starters(slots) or defense.secondary()
        return weighted_choice(self.rng, [(p, weighted_average(p, COVERAGE_WEIGHTS)) for p in defenders])

    def select_pass_rusher(self, defense: TeamSnapshot) -> PlayerSnapshot:
        rushers = defense.defensive_front() or defense.roster[:5]
        return weighted_choice(self.rng, [(p, weighted_average(p, PASS_RUSH_WEIGHTS)) for p in rushers])

    def select_tackler(self, defense: TeamSnapshot, yards: int) -> PlayerSnapshot:
        if yards <= 4:
            pool = defense.defensive_front() + defense.linebackers()
        elif yards <= 14:
            pool = defense.linebackers() + defense.secondary()
        else:
            pool = defense.secondary() + defense.linebackers()
        pool = pool or defense.roster[:11]
        return weighted_choice(self.rng, [(p, weighted_average(p, TACKLE_WEIGHTS)) for p in pool])

    def extra_point(self, offense: TeamSnapshot) -> str:
        kicker = offense.starter("PK")
        make = clamp(0.925 + (weighted_average(kicker, KICK_WEIGHTS) - 65) * 0.0022, 0.82, 0.995)
        self.count_special_teams_snap(offense, "extra_point")
        self.team_stats[offense.team_id]["xp_attempts"] += 1
        self.player_stats[kicker.player_id]["xp_attempts"] += 1
        if self.rng.random() < make:
            self.add_score(offense, 1)
            self.team_stats[offense.team_id]["xp_made"] += 1
            self.player_stats[kicker.player_id]["xp_made"] += 1
            return "Extra point good."
        return "Extra point no good."

    def field_goal(self, offense: TeamSnapshot, defense: TeamSnapshot, field_pos: int) -> tuple[str, TeamSnapshot, int, str]:
        kicker = offense.starter("PK")
        distance = 100 - field_pos + 17
        kick_score = weighted_average(kicker, KICK_WEIGHTS)
        make = 0.985 - max(0, distance - 28) * 0.014 + (kick_score - 65) * 0.0033
        make = clamp(make, 0.18, 0.985)
        self.team_stats[offense.team_id]["fg_attempts"] += 1
        self.player_stats[kicker.player_id]["fg_attempts"] += 1
        if self.rng.random() < make:
            self.add_score(offense, 3)
            self.team_stats[offense.team_id]["fg_made"] += 1
            self.player_stats[kicker.player_id]["fg_made"] += 1
            self.player_stats[kicker.player_id]["long_fg"] = max(self.player_stats[kicker.player_id]["long_fg"], distance)
            return "field_goal", defense, 25, f"{kicker.name} makes a {distance} yard field goal."
        new_field = 25 if distance >= 56 else int(clamp(100 - field_pos, 20, 99))
        return "missed_field_goal", defense, new_field, f"{kicker.name} misses a {distance} yard field goal."

    def punt(self, offense: TeamSnapshot, defense: TeamSnapshot, field_pos: int) -> tuple[TeamSnapshot, int, str]:
        punter = offense.starter("PT")
        punt_score = weighted_average(punter, PUNT_WEIGHTS)
        gross = int(clamp(round(self.rng.gauss(43 + (punt_score - 60) * 0.13, 7)), 22, 70))
        return_yards = max(0, int(round(self.rng.gauss(7 - (punt_score - 60) * 0.025, 5))))
        absolute_landing = field_pos + gross
        self.team_stats[offense.team_id]["punts"] += 1
        self.team_stats[offense.team_id]["punt_yards"] += gross
        self.player_stats[punter.player_id]["punts"] += 1
        self.player_stats[punter.player_id]["punt_yards"] += gross
        if absolute_landing >= 100:
            return defense, 20, f"{punter.name} punts {gross} yards for a touchback."
        opponent_field = int(clamp(100 - absolute_landing + return_yards, 1, 99))
        return defense, opponent_field, f"{punter.name} punts {gross} yards, returned {return_yards}."

    def record_play(
        self,
        drive_number: int,
        offense: TeamSnapshot,
        defense: TeamSnapshot,
        down: int,
        distance: int,
        field_pos: int,
        play_type: str,
        concept: str,
        yards: int,
        live_tenths: int,
        runoff_tenths: int,
        description: str,
        offense_player: PlayerSnapshot | None = None,
        target_player: PlayerSnapshot | None = None,
        defense_player: PlayerSnapshot | None = None,
        touchdown: bool = False,
        turnover: bool = False,
    ) -> None:
        if play_type in {"run", "pass"}:
            self.count_scrimmage_snap(offense, defense, play_type, concept)
        elif play_type in {"field_goal", "punt"}:
            self.count_special_teams_snap(offense, play_type)
        self.play_number += 1
        self.add_play_event(
            PlayEvent(
                play_number=self.play_number,
                drive_number=drive_number,
                quarter=self.quarter,
                clock_tenths=self.clock_tenths,
                offense_team_id=offense.team_id,
                defense_team_id=defense.team_id,
                down=down,
                distance=distance,
                yardline=field_pos,
                play_type=play_type,
                concept=concept,
                yards_gained=yards,
                offense_player_id=offense_player.player_id if offense_player else None,
                target_player_id=target_player.player_id if target_player else None,
                defense_player_id=defense_player.player_id if defense_player else None,
                is_touchdown=1 if touchdown else 0,
                is_turnover=1 if turnover else 0,
                clock_elapsed_tenths=live_tenths + runoff_tenths,
                runoff_tenths=runoff_tenths,
                description=description,
            )
        )

    def run_drive(self, offense: TeamSnapshot, start_field: int) -> tuple[TeamSnapshot, int, bool]:
        defense = self.opponent(offense)
        down = 1
        distance = 10
        field_pos = int(start_field)
        self.drive_number += 1
        drive = DriveRecord(
            drive_number=self.drive_number,
            offense_team_id=offense.team_id,
            defense_team_id=defense.team_id,
            start_quarter=self.quarter,
            start_clock_tenths=self.clock_tenths,
            start_yardline=field_pos,
        )
        start_score = self.score[offense.team_id]
        start_play_count = len(self.plays)
        start_total_clock_marker = self.total_elapsed_tenths()
        drive_yards = 0

        while True:
            ended, new_offense, new_field, new_down, new_distance = self.advance_dead_quarter_if_needed(offense, field_pos, down, distance)
            if ended:
                drive.result = "half_end" if self.quarter == 3 else "game_end"
                offense, field_pos, down, distance = new_offense, new_field, new_down, new_distance
                break
            if down == 4:
                decision = self.fourth_down_decision(offense, defense, down, distance, field_pos)
                if decision == "field_goal":
                    outcome, next_offense, next_field, desc = self.field_goal(offense, defense, field_pos)
                    live = max(25, int(round(self.rng.gauss(42, 7))))
                    consumed, runoff = self.consume_clock(live, 0)
                    self.record_play(self.drive_number, offense, defense, down, distance, field_pos, "field_goal", "field_goal", 0, consumed, runoff, desc, offense_player=offense.starter("PK"))
                    drive.result = outcome
                    offense, field_pos = next_offense, next_field
                    break
                if decision == "punt":
                    next_offense, next_field, desc = self.punt(offense, defense, field_pos)
                    live = max(30, int(round(self.rng.gauss(47, 7))))
                    consumed, runoff = self.consume_clock(live, 0)
                    self.record_play(self.drive_number, offense, defense, down, distance, field_pos, "punt", "punt", 0, consumed, runoff, desc, offense_player=offense.starter("PT"))
                    drive.result = "punt"
                    offense, field_pos = next_offense, next_field
                    break

            is_pass = self.play_call_is_pass(offense, defense, down, distance, field_pos)
            play_type = "pass" if is_pass else "run"
            penalty = self.maybe_penalty(offense, defense, play_type)
            if penalty:
                penalty_yards, label, automatic_first_down = penalty
                snap_down = down
                snap_distance = distance
                snap_field = field_pos
                live = 10
                consumed, runoff = self.consume_clock(live, 0)
                field_pos = int(clamp(snap_field + penalty_yards, 1, 99))
                desc = f"Penalty: {label}, {abs(penalty_yards)} yards"
                if penalty_yards > 0:
                    remaining = snap_distance - penalty_yards
                    if automatic_first_down or remaining <= 0:
                        self.team_stats[offense.team_id]["first_downs"] += 1
                        down = 1
                        distance = min(10, max(1, 100 - field_pos))
                        if automatic_first_down:
                            desc += ", automatic first down"
                    else:
                        distance = max(1, remaining)
                else:
                    distance = snap_distance + abs(penalty_yards)
                self.record_play(self.drive_number, offense, defense, snap_down, snap_distance, snap_field, "penalty", label, penalty_yards, consumed, runoff, desc + ".")
                continue

            old_field = field_pos
            self._last_play_concept = None
            if is_pass:
                outcome, yards, _points, _unused, desc, next_offense, next_field, _down, _dist, target, defender = self.pass_play(offense, defense, down, distance, field_pos)
                live = int(clamp(round(self.rng.gauss(42 if yards == 0 else 58, 13)), 18, 110))
                stops_clock = yards == 0 and outcome == "normal"
                if outcome in {"touchdown", "turnover"}:
                    stops_clock = True
                runoff = 0 if stops_clock else int(clamp(round(self.rng.gauss(285, 55)), 90, 410))
                consumed, actual_runoff = self.consume_clock(live, runoff)
                self.record_play(
                    self.drive_number,
                    offense,
                    defense,
                    down,
                    distance,
                    old_field,
                    "pass",
                    self._last_play_concept or "pass",
                    yards,
                    consumed - actual_runoff,
                    actual_runoff,
                    desc,
                    offense_player=offense.starter("QB"),
                    target_player=target,
                    defense_player=defender,
                    touchdown=outcome == "touchdown",
                    turnover=outcome == "turnover",
                )
            else:
                outcome, yards, _points, _unused, desc, next_offense, next_field, _down, _dist, runner, tackler = self.run_play(offense, defense, down, distance, field_pos)
                live = int(clamp(round(self.rng.gauss(58, 16)), 25, 120))
                runoff = 0 if outcome in {"touchdown", "turnover"} else int(clamp(round(self.rng.gauss(305, 55)), 110, 420))
                consumed, actual_runoff = self.consume_clock(live, runoff)
                self.record_play(
                    self.drive_number,
                    offense,
                    defense,
                    down,
                    distance,
                    old_field,
                    "run",
                    self._last_play_concept or "run",
                    yards,
                    consumed - actual_runoff,
                    actual_runoff,
                    desc,
                    offense_player=runner,
                    defense_player=tackler,
                    touchdown=outcome == "touchdown",
                    turnover=outcome == "turnover",
                )

            if outcome in {"touchdown", "turnover"}:
                if outcome == "touchdown":
                    drive_yards += max(0, yards)
                drive.result = outcome
                offense, field_pos = next_offense, next_field
                break

            field_pos = int(next_field)
            drive_yards += yards
            if old_field + yards <= 0:
                self.add_score(defense, 2)
                drive.result = "safety"
                offense, field_pos = defense, 25
                break
            if yards >= distance:
                self.team_stats[offense.team_id]["first_downs"] += 1
                down = 1
                distance = min(10, max(1, 100 - field_pos))
            else:
                down += 1
                distance = max(1, distance - yards)
                if down > 4:
                    drive.result = "turnover_on_downs"
                    offense, field_pos = defense, int(clamp(100 - field_pos, 1, 99))
                    break

        drive.end_quarter = self.quarter
        drive.end_clock_tenths = self.clock_tenths
        drive.end_yardline = field_pos
        drive.plays = len(self.plays) - start_play_count
        drive.yards = drive_yards
        drive.points = self.score[drive.offense_team_id] - start_score
        drive.time_elapsed_tenths = max(0, self.total_elapsed_tenths() - start_total_clock_marker)
        if not drive.result:
            drive.result = "end"
        self.drives.append(drive)
        game_finished = self.quarter >= 4 and self.clock_tenths == 0
        if self.quarter == 5 and self.clock_tenths == 0:
            game_finished = True
        return offense, field_pos, game_finished

    def total_elapsed_tenths(self) -> int:
        if self.quarter <= 4:
            return (self.quarter - 1) * REGULATION_QUARTER_TENTHS + (REGULATION_QUARTER_TENTHS - self.clock_tenths)
        return 4 * REGULATION_QUARTER_TENTHS + (OVERTIME_TENTHS - self.clock_tenths)

    def overtime_should_continue(self, last_offense: TeamSnapshot | None = None, last_result: str | None = None) -> bool:
        away_score = self.score[self.away.team_id]
        home_score = self.score[self.home.team_id]
        if self.clock_tenths <= 0:
            return False
        if away_score == home_score:
            return True
        if last_result == "touchdown":
            return False
        if len(self.ot_possessions) < 2:
            return True
        return False

    def simulate(self) -> GameResult:
        offense = self.first_half_receiver
        field_pos = 25
        finished = False
        while not finished:
            offense, field_pos, finished = self.run_drive(offense, field_pos)
            if self.quarter >= 4 and self.clock_tenths == 0:
                finished = True

        if self.score[self.away.team_id] == self.score[self.home.team_id]:
            self.quarter = 5
            self.clock_tenths = OVERTIME_TENTHS
            offense = self.rng.choice([self.away, self.home])
            field_pos = 25
            while self.overtime_should_continue():
                current_offense_id = offense.team_id
                before_score = dict(self.score)
                offense, field_pos, finished = self.run_drive(offense, field_pos)
                self.ot_possessions.add(current_offense_id)
                scored_td = self.score[current_offense_id] >= before_score[current_offense_id] + 6
                if scored_td and len(self.ot_possessions) == 1:
                    break
                if self.clock_tenths <= 0:
                    break
                if len(self.ot_possessions) >= 2 and self.score[self.away.team_id] != self.score[self.home.team_id]:
                    break

        return GameResult(
            schedule_game_id=self.schedule_game_id,
            season=self.season,
            week=self.week,
            away=self.away,
            home=self.home,
            away_score=int(self.score[self.away.team_id]),
            home_score=int(self.score[self.home.team_id]),
            seed=self.seed,
            plays=self.plays,
            drives=self.drives,
            team_stats=self.team_stats,
            player_stats=self.player_stats,
        )


def simulate_game(
    con: sqlite3.Connection,
    *,
    away_team_id: int,
    home_team_id: int,
    season: int = DEFAULT_SEASON,
    week: int | None = None,
    schedule_game_id: int | None = None,
    seed: int | None = None,
) -> GameResult:
    away = load_team(con, away_team_id, season)
    home = load_team(con, home_team_id, season)
    return MatchEngine(
        away=away,
        home=home,
        season=season,
        week=week,
        schedule_game_id=schedule_game_id,
        seed=seed,
    ).simulate()


def schedule_game_type(con: sqlite3.Connection, schedule_game_id: int | None) -> str | None:
    if schedule_game_id is None:
        return None
    row = con.execute(
        "SELECT game_type FROM season_games WHERE game_id = ?",
        (schedule_game_id,),
    ).fetchone()
    if not row:
        return None
    return row["game_type"] if isinstance(row, sqlite3.Row) else row[0]


def result_count_flags(con: sqlite3.Connection, result: GameResult, update_schedule: bool) -> tuple[int, int]:
    game_type = schedule_game_type(con, result.schedule_game_id)
    counts = 1 if update_schedule and result.schedule_game_id is not None and game_type == "REG" else 0
    return counts, counts


def supersede_existing_schedule_runs(
    con: sqlite3.Connection,
    *,
    schedule_game_id: int,
    superseded_by_run_id: int | None = None,
) -> None:
    if superseded_by_run_id is None:
        con.execute(
            """
            UPDATE game_sim_runs
            SET counts_for_stats = 0,
                counts_for_standings = 0,
                status = 'superseded'
            WHERE schedule_game_id = ?
              AND status <> 'superseded'
            """,
            (schedule_game_id,),
        )
    else:
        con.execute(
            """
            UPDATE game_sim_runs
            SET counts_for_stats = 0,
                counts_for_standings = 0,
                status = 'superseded',
                superseded_by_run_id = ?
            WHERE schedule_game_id = ?
              AND run_id <> ?
              AND status <> 'superseded'
            """,
            (superseded_by_run_id, schedule_game_id, superseded_by_run_id),
        )


def rebuild_season_records(con: sqlite3.Connection, season: int) -> None:
    ensure_schema(con)
    con.execute("DELETE FROM season_team_records WHERE season = ?", (season,))
    con.executemany(
        """
        INSERT INTO season_team_records (season, team_id)
        VALUES (?, ?)
        """,
        [(season, int(row["team_id"])) for row in con.execute("SELECT team_id FROM teams").fetchall()],
    )
    team_rows = con.execute("SELECT team_id, conference, division FROM teams").fetchall()
    teams = {
        int(row["team_id"]): {
            "conference": row["conference"],
            "division": row["division"],
        }
        for row in team_rows
    }
    runs = con.execute(
        """
        SELECT r.*
        FROM game_sim_runs r
        JOIN season_games sg ON sg.game_id = r.schedule_game_id
        WHERE r.season = ?
          AND r.counts_for_standings = 1
          AND r.status = 'final'
          AND sg.game_type = 'REG'
        ORDER BY sg.week, sg.week_game_number, r.run_id
        """,
        (season,),
    ).fetchall()

    def apply(team_id: int, opponent_id: int, points_for: int, points_against: int) -> None:
        win = 1 if points_for > points_against else 0
        loss = 1 if points_for < points_against else 0
        tie = 1 if points_for == points_against else 0
        team = teams[team_id]
        opponent = teams[opponent_id]
        conference_game = team["conference"] == opponent["conference"]
        division_game = team["division"] == opponent["division"]
        con.execute(
            """
            UPDATE season_team_records
            SET wins = wins + ?,
                losses = losses + ?,
                ties = ties + ?,
                points_for = points_for + ?,
                points_against = points_against + ?,
                conference_wins = conference_wins + ?,
                conference_losses = conference_losses + ?,
                conference_ties = conference_ties + ?,
                division_wins = division_wins + ?,
                division_losses = division_losses + ?,
                division_ties = division_ties + ?,
                updated_at = datetime('now')
            WHERE season = ? AND team_id = ?
            """,
            (
                win,
                loss,
                tie,
                points_for,
                points_against,
                win if conference_game else 0,
                loss if conference_game else 0,
                tie if conference_game else 0,
                win if division_game else 0,
                loss if division_game else 0,
                tie if division_game else 0,
                season,
                team_id,
            ),
        )

    for run in runs:
        apply(int(run["away_team_id"]), int(run["home_team_id"]), int(run["away_score"]), int(run["home_score"]))
        apply(int(run["home_team_id"]), int(run["away_team_id"]), int(run["home_score"]), int(run["away_score"]))


def rebuild_season_stat_tables(con: sqlite3.Connection, season: int) -> None:
    ensure_schema(con)
    con.execute("DELETE FROM season_team_stats WHERE season = ?", (season,))
    con.execute("DELETE FROM season_player_stats WHERE season = ?", (season,))
    con.execute(
        """
        INSERT INTO season_team_stats (season, team_id, stat_key, stat_value, updated_at)
        SELECT r.season, gts.team_id, gts.stat_key, SUM(gts.stat_value), datetime('now')
        FROM game_team_stats gts
        JOIN game_sim_runs r ON r.run_id = gts.run_id
        JOIN season_games sg ON sg.game_id = r.schedule_game_id
        WHERE r.season = ?
          AND r.counts_for_stats = 1
          AND r.status = 'final'
          AND sg.game_type = 'REG'
        GROUP BY r.season, gts.team_id, gts.stat_key
        """,
        (season,),
    )
    con.execute(
        """
        INSERT INTO season_player_stats (season, player_id, team_id, stat_key, stat_value, updated_at)
        SELECT r.season, gps.player_id, gps.team_id, gps.stat_key, SUM(gps.stat_value), datetime('now')
        FROM game_player_stats gps
        JOIN game_sim_runs r ON r.run_id = gps.run_id
        JOIN season_games sg ON sg.game_id = r.schedule_game_id
        WHERE r.season = ?
          AND r.counts_for_stats = 1
          AND r.status = 'final'
          AND sg.game_type = 'REG'
        GROUP BY r.season, gps.player_id, gps.team_id, gps.stat_key
        """,
        (season,),
    )


def rebuild_season_history(con: sqlite3.Connection, season: int) -> None:
    rebuild_season_records(con, season)
    rebuild_season_stat_tables(con, season)


def persist_result(
    con: sqlite3.Connection,
    result: GameResult,
    *,
    update_schedule: bool = True,
    force: bool = False,
    notes: str | None = None,
    rebuild_history: bool = True,
) -> int:
    ensure_schema(con)
    if update_schedule and result.schedule_game_id is not None and not force:
        row = con.execute(
            "SELECT played FROM season_games WHERE game_id = ?",
            (result.schedule_game_id,),
        ).fetchone()
        if row and int(row["played"] or 0):
            raise ValueError(f"Schedule game {result.schedule_game_id} is already played. Use force to overwrite.")
    if update_schedule and result.schedule_game_id is not None and force:
        supersede_existing_schedule_runs(con, schedule_game_id=result.schedule_game_id)

    counts_for_stats, counts_for_standings = result_count_flags(con, result, update_schedule)

    cur = con.execute(
        """
        INSERT INTO game_sim_runs (
            schedule_game_id, season, week, away_team_id, home_team_id, seed,
            engine_version, status, away_score, home_score, total_plays, total_drives,
            counts_for_stats, counts_for_standings, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result.schedule_game_id,
            result.season,
            result.week,
            result.away.team_id,
            result.home.team_id,
            result.seed,
            ENGINE_VERSION,
            result.status,
            result.away_score,
            result.home_score,
            len(result.plays),
            len(result.drives),
            counts_for_stats,
            counts_for_standings,
            notes,
        ),
    )
    run_id = int(cur.lastrowid)
    if update_schedule and result.schedule_game_id is not None and force:
        supersede_existing_schedule_runs(
            con,
            schedule_game_id=result.schedule_game_id,
            superseded_by_run_id=run_id,
        )

    con.executemany(
        """
        INSERT INTO game_sim_drives (
            run_id, drive_number, offense_team_id, defense_team_id,
            start_quarter, start_clock_tenths, end_quarter, end_clock_tenths,
            start_yardline, end_yardline, result, plays, yards, points, time_elapsed_tenths
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                drive.drive_number,
                drive.offense_team_id,
                drive.defense_team_id,
                drive.start_quarter,
                drive.start_clock_tenths,
                drive.end_quarter,
                drive.end_clock_tenths,
                drive.start_yardline,
                drive.end_yardline,
                drive.result,
                drive.plays,
                drive.yards,
                drive.points,
                drive.time_elapsed_tenths,
            )
            for drive in result.drives
        ],
    )

    con.executemany(
        """
        INSERT INTO game_sim_plays (
            run_id, play_number, drive_number, quarter, clock_tenths,
            offense_team_id, defense_team_id, down, distance, yardline,
            play_type, concept, offense_player_id, target_player_id,
            defense_player_id, yards_gained, is_touchdown, is_turnover,
            clock_elapsed_tenths, runoff_tenths, description
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                play.play_number,
                play.drive_number,
                play.quarter,
                play.clock_tenths,
                play.offense_team_id,
                play.defense_team_id,
                play.down,
                play.distance,
                play.yardline,
                play.play_type,
                play.concept,
                play.offense_player_id,
                play.target_player_id,
                play.defense_player_id,
                play.yards_gained,
                play.is_touchdown,
                play.is_turnover,
                play.clock_elapsed_tenths,
                play.runoff_tenths,
                play.description,
            )
            for play in result.plays
        ],
    )

    for team_id, stats in result.team_stats.items():
        con.executemany(
            """
            INSERT INTO game_team_stats (run_id, team_id, stat_key, stat_value)
            VALUES (?, ?, ?, ?)
            """,
            [(run_id, team_id, key, float(value)) for key, value in stats.items()],
        )

    player_team = {p.player_id: result.away.team_id for p in result.away.roster}
    player_team.update({p.player_id: result.home.team_id for p in result.home.roster})
    for player_id, stats in result.player_stats.items():
        team_id = player_team.get(player_id)
        if team_id is None:
            continue
        con.executemany(
            """
            INSERT INTO game_player_stats (run_id, player_id, team_id, stat_key, stat_value)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(run_id, player_id, team_id, key, float(value)) for key, value in stats.items()],
        )

    if update_schedule and result.schedule_game_id is not None:
        con.execute(
            """
            UPDATE season_games
            SET played = 1,
                away_score = ?,
                home_score = ?,
                updated_at = datetime('now')
            WHERE game_id = ?
            """,
            (result.away_score, result.home_score, result.schedule_game_id),
        )
        if rebuild_history:
            rebuild_season_history(con, result.season)

    return run_id


def update_records(con: sqlite3.Connection, result: GameResult) -> None:
    teams = [result.away, result.home]
    for team in teams:
        con.execute(
            """
            INSERT INTO season_team_records (season, team_id)
            VALUES (?, ?)
            ON CONFLICT(season, team_id) DO NOTHING
            """,
            (result.season, team.team_id),
        )

    away_outcome = "tie"
    home_outcome = "tie"
    if result.away_score > result.home_score:
        away_outcome, home_outcome = "win", "loss"
    elif result.home_score > result.away_score:
        away_outcome, home_outcome = "loss", "win"

    def apply(team: TeamSnapshot, opponent: TeamSnapshot, points_for: int, points_against: int, outcome: str) -> None:
        win = 1 if outcome == "win" else 0
        loss = 1 if outcome == "loss" else 0
        tie = 1 if outcome == "tie" else 0
        conf = team.conference == opponent.conference
        div = team.division == opponent.division
        con.execute(
            """
            UPDATE season_team_records
            SET wins = wins + ?,
                losses = losses + ?,
                ties = ties + ?,
                points_for = points_for + ?,
                points_against = points_against + ?,
                conference_wins = conference_wins + ?,
                conference_losses = conference_losses + ?,
                conference_ties = conference_ties + ?,
                division_wins = division_wins + ?,
                division_losses = division_losses + ?,
                division_ties = division_ties + ?,
                updated_at = datetime('now')
            WHERE season = ? AND team_id = ?
            """,
            (
                win,
                loss,
                tie,
                points_for,
                points_against,
                win if conf else 0,
                loss if conf else 0,
                tie if conf else 0,
                win if div else 0,
                loss if div else 0,
                tie if div else 0,
                result.season,
                team.team_id,
            ),
        )

    apply(result.away, result.home, result.away_score, result.home_score, away_outcome)
    apply(result.home, result.away, result.home_score, result.away_score, home_outcome)


def scoreline(result: GameResult) -> str:
    return (
        f"{result.away.abbreviation} {result.away_score} at "
        f"{result.home.abbreviation} {result.home_score}"
    )
