#!/usr/bin/env python3
"""Season-to-season player progression and regression.

This processor turns the hidden development foundation into actual rating
movement. It is intentionally auditable and dry-run first: it records every
player result and every rating-row change only when --apply is passed.
"""

from __future__ import annotations

import argparse
import random
import secrets
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_FROM_SEASON = 2026
SOURCE = "player_progression"
PROGRESSION_COACH_NOTE_TYPE = "PROGRESSION_COACH_NOTE"
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import apply_new_game_variance  # noqa: E402
import player_development_modifiers  # noqa: E402
import scheme_fits  # noqa: E402


POSITION_GROUP = {
    "QB": "QB",
    "RB": "RB",
    "FB": "RB",
    "WR": "WR",
    "TE": "TE",
    "OT": "OL",
    "OG": "OL",
    "C": "OL",
    "EDGE": "EDGE",
    "OLB": "EDGE",
    "IDL": "DL",
    "LB": "LB",
    "ILB": "LB",
    "CB": "CB",
    "NB": "CB",
    "FS": "S",
    "SS": "S",
    "S": "S",
    "K": "ST",
    "P": "ST",
    "LS": "ST",
}

SPEED_DECAY_RATINGS = {"speed", "acceleration", "agility", "elusiveness", "speed_rush"}
POWER_HOLD_RATINGS = {"strength", "contact_power", "power_rush", "hit_power"}

OFFENSE_GROUPS = {"QB", "RB", "WR", "TE", "OL"}
DEFENSE_GROUPS = {"EDGE", "DL", "LB", "CB", "S"}

PHYSICAL_RATINGS = {
    "speed",
    "acceleration",
    "agility",
    "balance",
    "strength",
    "stamina",
    "durability",
    "throw_power",
    "kick_power",
    "contact_power",
    "elusiveness",
    "hit_power",
    "power_rush",
    "speed_rush",
}

MENTAL_RATINGS = {
    "play_recognition",
    "processing_speed",
    "discipline",
    "composure",
    "consistency",
    "run_patience",
    "route_timing",
    "rush_plan",
    "gap_integrity",
    "coverage_communication",
    "ball_security",
}

SPECIALIST_RATINGS = {"kick_power", "kick_accuracy"}

HIGH_VALUE_STATS = {
    "QB": ("pass_attempts", "pass_yards", "pass_tds", "interceptions_thrown", "sacks_taken"),
    "RB": ("rush_attempts", "rush_yards", "rush_tds", "targets"),
    "FB": ("rush_attempts", "targets"),
    "WR": ("targets", "receptions", "receiving_yards", "receiving_tds"),
    "TE": ("targets", "receptions", "receiving_yards", "receiving_tds"),
    "OT": (),
    "OG": (),
    "C": (),
    "EDGE": ("tackles", "sacks", "forced_fumbles"),
    "OLB": ("tackles", "sacks", "interceptions", "pass_deflections"),
    "IDL": ("tackles", "sacks", "forced_fumbles"),
    "LB": ("tackles", "sacks", "interceptions", "pass_deflections"),
    "ILB": ("tackles", "sacks", "interceptions", "pass_deflections"),
    "CB": ("tackles", "interceptions", "pass_deflections"),
    "NB": ("tackles", "interceptions", "pass_deflections"),
    "FS": ("tackles", "interceptions", "pass_deflections"),
    "SS": ("tackles", "interceptions", "pass_deflections"),
    "S": ("tackles", "interceptions", "pass_deflections"),
    "K": ("fg_attempts", "fg_made", "xp_attempts", "xp_made"),
    "P": ("punts", "punt_yards"),
}

STAT_ALIASES = {
    "interceptions": ("interceptions_thrown",),
    "interceptions_thrown": ("interceptions",),
    "fg_attempts": ("fg_att",),
    "fg_made": ("field_goals_made",),
    "xp_attempts": ("pat_att",),
    "xp_made": ("pat_made",),
}

POSITION_RELEVANT_RATING_GROUPS = {
    "QB": {"universal", "passer", "ball_carrier"},
    "RB": {"universal", "ball_carrier", "receiver", "blocker"},
    "FB": {"universal", "ball_carrier", "receiver", "blocker", "tackler"},
    "WR": {"universal", "receiver", "ball_carrier", "blocker"},
    "TE": {"universal", "receiver", "blocker", "ball_carrier"},
    "OT": {"universal", "blocker"},
    "OG": {"universal", "blocker"},
    "C": {"universal", "blocker"},
    "OL": {"universal", "blocker"},
    "EDGE": {"universal", "pass_rusher", "run_defender", "tackler"},
    "DE": {"universal", "pass_rusher", "run_defender", "tackler"},
    "IDL": {"universal", "pass_rusher", "run_defender", "tackler"},
    "DT": {"universal", "pass_rusher", "run_defender", "tackler"},
    "NT": {"universal", "run_defender", "pass_rusher", "tackler"},
    "DL": {"universal", "pass_rusher", "run_defender", "tackler"},
    "OLB": {"universal", "pass_rusher", "run_defender", "coverage", "tackler"},
    "ILB": {"universal", "run_defender", "coverage", "tackler", "pass_rusher"},
    "LB": {"universal", "run_defender", "coverage", "tackler", "pass_rusher"},
    "CB": {"universal", "coverage", "tackler", "run_defender", "ball_carrier"},
    "NB": {"universal", "coverage", "tackler", "run_defender", "ball_carrier"},
    "FS": {"universal", "coverage", "tackler", "run_defender", "ball_carrier"},
    "SS": {"universal", "coverage", "tackler", "run_defender", "ball_carrier"},
    "S": {"universal", "coverage", "tackler", "run_defender", "ball_carrier"},
    "K": {"universal", "specialist", "tackler"},
    "P": {"universal", "specialist", "tackler", "passer"},
    "LS": {"universal", "specialist", "blocker", "tackler"},
}


@dataclass(frozen=True)
class PlayerContext:
    player_id: int
    name: str
    team: str
    team_id: int | None
    position: str
    age: int
    years_exp: int
    status: str
    old_overall: int
    old_potential: int
    age_band: str
    development_score: float
    usage_score: float
    scheme_score: float
    coaching_score: float
    team_success_score: float
    performance_score: float
    personality_score: float
    personality_regression_score: float
    personality_variance_score: float
    injury_score: float
    hidden_factor_score: float
    circumstance_score: float
    practice_squad_score: float
    random_score: float
    breakout_delta: float
    decline_delta: float
    potential_miss_delta: float
    base_delta: float
    potential_delta: int
    notes: str


@dataclass(frozen=True)
class PlayerResult:
    context: PlayerContext
    old_avg_rating: float
    new_avg_rating: float
    old_overall: int
    new_overall: int
    old_potential: int
    new_potential: int
    rating_count: int

    @property
    def overall_delta(self) -> int:
        return self.new_overall - self.old_overall

    @property
    def potential_delta(self) -> int:
        return self.new_potential - self.old_potential


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def clamp_int(value: float, low: int = 1, high: int = 99) -> int:
    return max(low, min(high, int(round(value))))


def rating_type(rating_key: str) -> str:
    if rating_key in SPECIALIST_RATINGS:
        return "specialist"
    if rating_key in PHYSICAL_RATINGS:
        return "physical"
    if rating_key in MENTAL_RATINGS:
        return "mental"
    return "skill"


def rating_group_is_relevant(position: str, rating_group: str) -> bool:
    relevant = POSITION_RELEVANT_RATING_GROUPS.get(position, {"universal"})
    return rating_group in relevant


def profile_value(profile: Mapping[str, Any] | None, key: str, default: float = 0.0) -> float:
    if not profile:
        return default
    try:
        value = profile[key]
    except (KeyError, IndexError):
        return default
    return float(value or default)


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS player_progression_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            from_season INTEGER NOT NULL,
            to_season INTEGER NOT NULL,
            rng_seed INTEGER NOT NULL,
            player_count INTEGER NOT NULL DEFAULT 0,
            rating_row_count INTEGER NOT NULL DEFAULT 0,
            overall_changed_count INTEGER NOT NULL DEFAULT 0,
            potential_changed_count INTEGER NOT NULL DEFAULT 0,
            hidden_modifier_rows INTEGER NOT NULL DEFAULT 0,
            role_score_updates INTEGER NOT NULL DEFAULT 0,
            scheme_fit_rows INTEGER NOT NULL DEFAULT 0,
            age_players INTEGER NOT NULL DEFAULT 1,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(game_id, from_season, to_season)
        );

        CREATE TABLE IF NOT EXISTS player_progression_results (
            run_id INTEGER NOT NULL REFERENCES player_progression_runs(run_id) ON DELETE CASCADE,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            from_season INTEGER NOT NULL,
            to_season INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            team TEXT NOT NULL,
            position TEXT NOT NULL,
            age INTEGER NOT NULL,
            years_exp INTEGER NOT NULL,
            status TEXT,
            age_band TEXT NOT NULL,
            old_overall INTEGER NOT NULL,
            new_overall INTEGER NOT NULL,
            old_potential INTEGER NOT NULL,
            new_potential INTEGER NOT NULL,
            old_avg_rating REAL NOT NULL,
            new_avg_rating REAL NOT NULL,
            rating_count INTEGER NOT NULL,
            development_score REAL NOT NULL,
            usage_score REAL NOT NULL,
            scheme_score REAL NOT NULL,
            coaching_score REAL NOT NULL,
            team_success_score REAL NOT NULL,
            performance_score REAL NOT NULL,
            personality_score REAL NOT NULL DEFAULT 0,
            personality_regression_score REAL NOT NULL DEFAULT 0,
            personality_variance_score REAL NOT NULL DEFAULT 0,
            injury_score REAL NOT NULL DEFAULT 0,
            hidden_factor_score REAL NOT NULL DEFAULT 0,
            circumstance_score REAL NOT NULL DEFAULT 0,
            practice_squad_score REAL NOT NULL,
            random_score REAL NOT NULL,
            breakout_delta REAL NOT NULL,
            decline_delta REAL NOT NULL,
            potential_miss_delta REAL NOT NULL DEFAULT 0,
            base_delta REAL NOT NULL,
            potential_delta INTEGER NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(run_id, player_id)
        );

        CREATE TABLE IF NOT EXISTS player_progression_rating_detail (
            run_id INTEGER NOT NULL REFERENCES player_progression_runs(run_id) ON DELETE CASCADE,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            from_season INTEGER NOT NULL,
            to_season INTEGER NOT NULL,
            rating_key TEXT NOT NULL,
            rating_type TEXT NOT NULL,
            old_rating INTEGER NOT NULL,
            new_rating INTEGER NOT NULL,
            delta INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(run_id, player_id, rating_key)
        );

        DROP VIEW IF EXISTS player_progression_results_view;
        CREATE VIEW player_progression_results_view AS
        SELECT
            ppr.*,
            ppr.new_overall - ppr.old_overall AS overall_delta,
            ppr.new_potential - ppr.old_potential AS potential_change
        FROM player_progression_results ppr;

        CREATE TABLE IF NOT EXISTS game_alerts (
            alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            alert_date TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'INFO',
            alert_type TEXT NOT NULL,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            event_id INTEGER,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            due_date TEXT,
            status TEXT NOT NULL DEFAULT 'Open',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_game_alerts_game_status
            ON game_alerts(game_id, status, alert_date, severity);
        """
    )
    for column, definition in {
        "personality_score": "REAL NOT NULL DEFAULT 0",
        "personality_regression_score": "REAL NOT NULL DEFAULT 0",
        "personality_variance_score": "REAL NOT NULL DEFAULT 0",
        "injury_score": "REAL NOT NULL DEFAULT 0",
        "hidden_factor_score": "REAL NOT NULL DEFAULT 0",
        "circumstance_score": "REAL NOT NULL DEFAULT 0",
        "potential_miss_delta": "REAL NOT NULL DEFAULT 0",
    }.items():
        columns = {str(row["name"]) for row in con.execute("PRAGMA table_info(player_progression_results)").fetchall()}
        if column not in columns:
            con.execute(f"ALTER TABLE player_progression_results ADD COLUMN {column} {definition}")
    con.executescript(
        """
        DROP VIEW IF EXISTS player_progression_results_view;
        CREATE VIEW player_progression_results_view AS
        SELECT
            ppr.*,
            ppr.new_overall - ppr.old_overall AS overall_delta,
            ppr.new_potential - ppr.old_potential AS potential_change
        FROM player_progression_results ppr;
        """
    )


def active_game_id(con: sqlite3.Connection) -> str:
    if table_exists(con, "active_game_save_view"):
        row = con.execute("SELECT game_id FROM active_game_save_view LIMIT 1").fetchone()
        if row and row["game_id"]:
            return str(row["game_id"])
    if table_exists(con, "game_settings"):
        row = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = 'active_game_id'"
        ).fetchone()
        if row and row["setting_value"]:
            return str(row["setting_value"])
    return "default"


def age_band(age: int, years_exp: int, is_rookie: int) -> str:
    if is_rookie or years_exp <= 0 or (age <= 23 and years_exp <= 1):
        return "rookie"
    if age <= 25 or years_exp <= 3:
        return "young"
    if age <= 29:
        return "prime"
    if age <= 33:
        return "veteran"
    return "late_veteran"


def load_players(con: sqlite3.Connection, from_season: int) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            COALESCE(t.abbreviation, 'FA') AS team,
            p.team_id,
            COALESCE(p.age, 26) AS age,
            COALESCE(p.years_exp, 0) AS years_exp,
            COALESCE(p.is_rookie, 0) AS is_rookie,
            COALESCE(p.overall, 50) AS overall,
            COALESCE(p.potential, COALESCE(p.overall, 50)) AS potential,
            COALESCE(p.dev_trait, 'Normal') AS dev_trait,
            COALESCE(p.status, 'Active') AS status
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        WHERE EXISTS (
            SELECT 1 FROM player_ratings pr
            WHERE pr.player_id = p.player_id
              AND pr.season = ?
        )
          AND COALESCE(p.status, 'Active') != 'Retired'
        ORDER BY p.player_id
        """,
        (from_season,),
    ).fetchall()


def load_ratings(con: sqlite3.Connection, from_season: int) -> dict[int, dict[str, int]]:
    rows = con.execute(
        """
        SELECT player_id, rating_key, rating_value
        FROM player_ratings
        WHERE season = ?
        """,
        (from_season,),
    ).fetchall()
    ratings: dict[int, dict[str, int]] = {}
    for row in rows:
        ratings.setdefault(int(row["player_id"]), {})[str(row["rating_key"])] = int(row["rating_value"])
    return ratings


def load_rating_groups(con: sqlite3.Connection) -> dict[str, str]:
    if not table_exists(con, "rating_definitions"):
        return {}
    return {
        str(row["rating_key"]): str(row["rating_group"] or "universal")
        for row in con.execute(
            """
            SELECT rating_key, rating_group
            FROM rating_definitions
            """
        ).fetchall()
    }


def load_modifiers(con: sqlite3.Connection, game_id: str, season: int) -> dict[int, dict[str, int]]:
    if not table_exists(con, "player_development_modifiers"):
        return {}
    rows = con.execute(
        """
        SELECT player_id, factor_key, modifier_value
        FROM player_development_modifiers
        WHERE game_id = ? AND season = ?
        """,
        (game_id, season),
    ).fetchall()
    values: dict[int, dict[str, int]] = {}
    for row in rows:
        values.setdefault(int(row["player_id"]), {})[str(row["factor_key"])] = int(row["modifier_value"] or 0)
    return values


def load_profiles(con: sqlite3.Connection, game_id: str, season: int) -> dict[int, sqlite3.Row]:
    if not table_exists(con, "player_development_profiles"):
        return {}
    return {
        int(row["player_id"]): row
        for row in con.execute(
            """
            SELECT *
            FROM player_development_profiles
            WHERE game_id = ? AND season = ?
            """,
            (game_id, season),
        ).fetchall()
    }


def missing_development_player_ids(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    player_ids: list[int],
) -> list[int]:
    if not player_ids:
        return []
    player_development_modifiers.seed_master_data(con)
    expected_factors = int(con.execute("SELECT COUNT(*) FROM development_factor_definitions").fetchone()[0] or 0)
    placeholders = ",".join("?" for _ in player_ids)
    modifier_counts = {
        int(row["player_id"]): int(row["factor_count"] or 0)
        for row in con.execute(
            f"""
            SELECT player_id, COUNT(*) AS factor_count
            FROM player_development_modifiers
            WHERE game_id = ? AND season = ? AND player_id IN ({placeholders})
            GROUP BY player_id
            """,
            [game_id, season, *player_ids],
        ).fetchall()
    } if table_exists(con, "player_development_modifiers") else {}
    profile_ids = {
        int(row["player_id"])
        for row in con.execute(
            f"""
            SELECT player_id
            FROM player_development_profiles
            WHERE game_id = ? AND season = ? AND player_id IN ({placeholders})
            """,
            [game_id, season, *player_ids],
        ).fetchall()
    } if table_exists(con, "player_development_profiles") else set()
    return [
        player_id
        for player_id in player_ids
        if modifier_counts.get(player_id, 0) < expected_factors or player_id not in profile_ids
    ]


def ensure_development_foundation(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    player_ids: list[int],
    seed: int,
) -> int:
    missing_ids = missing_development_player_ids(con, game_id=game_id, season=season, player_ids=player_ids)
    if not missing_ids:
        return 0
    result = player_development_modifiers.seed_development_for_players(
        con,
        player_ids=missing_ids,
        game_id=game_id,
        season=season,
        seed=f"{seed}:{game_id}:{season}:progression_foundation",
        notes="Auto-seeded missing hidden development foundation before season progression.",
    )
    return int(result.get("modifiers", 0) or 0)


def add_transient_development_foundation(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    seed: int,
    players: list[sqlite3.Row],
    modifiers: dict[int, dict[str, int]],
    profiles: dict[int, Mapping[str, Any]],
    traits: dict[int, dict[str, int]],
) -> tuple[dict[int, dict[str, int]], dict[int, Mapping[str, Any]]]:
    player_development_modifiers.seed_master_data(con)
    factors = con.execute("SELECT * FROM development_factor_definitions ORDER BY factor_key").fetchall()
    expected_factors = len(factors)
    for player in players:
        player_id = int(player["player_id"])
        if len(modifiers.get(player_id, {})) >= expected_factors and player_id in profiles:
            continue
        rng = random.Random(f"{seed}:{game_id}:{season}:transient_development:{player_id}")
        values = dict(modifiers.get(player_id, {}))
        player_traits = traits.get(player_id, {})
        for factor in factors:
            key = str(factor["factor_key"])
            if key not in values:
                values[key] = player_development_modifiers.factor_value(
                    rng,
                    factor=factor,
                    player=player,
                    traits=player_traits,
                )
        modifiers[player_id] = values
        if player_id not in profiles:
            band = player_development_modifiers.age_band(player)
            profile = player_development_modifiers.profile_from_modifiers(values, band)
            profiles[player_id] = {
                "age_band": band,
                "development_bias": profile[0],
                "potential_volatility": profile[1],
                "regression_resistance": profile[2],
                "late_bloomer_chance": profile[3],
                "decline_risk": profile[4],
                "notes": profile[5],
            }
    return modifiers, profiles


def load_stats(con: sqlite3.Connection, season: int) -> dict[int, dict[str, float]]:
    if not table_exists(con, "season_player_stats"):
        return {}
    rows = con.execute(
        """
        SELECT player_id, stat_key, SUM(stat_value) AS stat_value
        FROM season_player_stats
        WHERE season = ?
        GROUP BY player_id, stat_key
        """,
        (season,),
    ).fetchall()
    stats: dict[int, dict[str, float]] = {}
    for row in rows:
        stats.setdefault(int(row["player_id"]), {})[str(row["stat_key"])] = float(row["stat_value"] or 0)
    return stats


def load_injury_context(con: sqlite3.Connection, season: int) -> dict[int, dict[str, float]]:
    injuries: dict[int, dict[str, float]] = {}
    if table_exists(con, "player_injury_history"):
        for row in con.execute(
            """
            SELECT
                player_id,
                COUNT(*) AS history_rows,
                SUM(COALESCE(games_missed, 0)) AS games_missed,
                SUM(CASE
                    WHEN severity IN ('major', 'severe', 'season_ending')
                      OR injury_code IN ('acl_tear', 'achilles', 'meniscus')
                    THEN 1 ELSE 0 END) AS major_rows,
                SUM(CASE WHEN substr(start_date, 1, 4) = ? THEN COALESCE(games_missed, 0) ELSE 0 END) AS recent_games_missed,
                MAX(COALESCE(recurrence_risk, 0)) AS max_recurrence_risk
            FROM player_injury_history
            GROUP BY player_id
            """,
            (str(season),),
        ).fetchall():
            injuries[int(row["player_id"])] = {
                "history_rows": float(row["history_rows"] or 0),
                "games_missed": float(row["games_missed"] or 0),
                "major_rows": float(row["major_rows"] or 0),
                "recent_games_missed": float(row["recent_games_missed"] or 0),
                "max_recurrence_risk": float(row["max_recurrence_risk"] or 0),
                "active_expected_games": 0.0,
                "season_expected_games": 0.0,
            }
    if table_exists(con, "active_player_injuries"):
        for row in con.execute(
            """
            SELECT player_id, SUM(COALESCE(expected_games, 0)) AS expected_games
            FROM active_player_injuries
            WHERE resolved_at IS NULL
            GROUP BY player_id
            """
        ).fetchall():
            item = injuries.setdefault(
                int(row["player_id"]),
                {
                    "history_rows": 0.0,
                    "games_missed": 0.0,
                    "major_rows": 0.0,
                    "recent_games_missed": 0.0,
                    "max_recurrence_risk": 0.0,
                    "active_expected_games": 0.0,
                    "season_expected_games": 0.0,
                },
            )
            item["active_expected_games"] = float(row["expected_games"] or 0)
    if table_exists(con, "game_injury_events"):
        for row in con.execute(
            """
            SELECT player_id, SUM(COALESCE(expected_games, 0)) AS expected_games
            FROM game_injury_events
            WHERE season = ?
            GROUP BY player_id
            """,
            (season,),
        ).fetchall():
            item = injuries.setdefault(
                int(row["player_id"]),
                {
                    "history_rows": 0.0,
                    "games_missed": 0.0,
                    "major_rows": 0.0,
                    "recent_games_missed": 0.0,
                    "max_recurrence_risk": 0.0,
                    "active_expected_games": 0.0,
                    "season_expected_games": 0.0,
                },
            )
            item["season_expected_games"] = float(row["expected_games"] or 0)
    return injuries


def load_depth_rank(con: sqlite3.Connection) -> dict[int, int]:
    if not table_exists(con, "depth_charts"):
        return {}
    rows = con.execute(
        """
        SELECT player_id, MIN(depth_rank) AS best_rank
        FROM depth_charts
        GROUP BY player_id
        """
    ).fetchall()
    return {int(row["player_id"]): int(row["best_rank"] or 99) for row in rows}


def load_scheme_context(con: sqlite3.Connection, season: int) -> dict[int, sqlite3.Row]:
    if not table_exists(con, "current_player_scheme_fit_view"):
        return {}
    return {
        int(row["player_id"]): row
        for row in con.execute(
            """
            SELECT *
            FROM current_player_scheme_fit_view
            WHERE season = ?
            """,
            (season,),
        ).fetchall()
    }


def load_team_success(con: sqlite3.Connection, season: int) -> dict[int, float]:
    if not table_exists(con, "season_team_records"):
        return {}
    scores: dict[int, float] = {}
    for row in con.execute(
        """
        SELECT team_id, wins, losses, ties, points_for, points_against
        FROM season_team_records
        WHERE season = ?
        """,
        (season,),
    ).fetchall():
        games = float((row["wins"] or 0) + (row["losses"] or 0) + (row["ties"] or 0))
        if games <= 0:
            scores[int(row["team_id"])] = 0.0
            continue
        win_pct = ((row["wins"] or 0) + 0.5 * (row["ties"] or 0)) / games
        point_diff = float((row["points_for"] or 0) - (row["points_against"] or 0))
        scores[int(row["team_id"])] = clamp((win_pct - 0.5) * 8.0 + point_diff / 220.0, -5.0, 5.0)
    return scores


def load_contract_context(con: sqlite3.Connection, season: int) -> dict[int, dict[str, float]]:
    if not table_exists(con, "contracts"):
        return {}
    rows = con.execute(
        """
        SELECT
            player_id,
            start_year,
            end_year,
            total_years,
            aav,
            total_value,
            signing_bonus,
            contract_type
        FROM contracts
        WHERE COALESCE(is_active, 1) = 1
        """
    ).fetchall()
    context: dict[int, dict[str, float]] = {}
    for row in rows:
        player_id = int(row["player_id"])
        aav = float(row["aav"] or 0.0)
        total_years = int(row["total_years"] or 0)
        start_year = int(row["start_year"] or 0)
        end_year = int(row["end_year"] or 0)
        context[player_id] = {
            "contract_year": 1.0 if end_year == season else 0.0,
            "new_big_deal": 1.0 if start_year == season and total_years >= 3 and aav >= 12_000_000 else 0.0,
            "new_major_deal": 1.0 if start_year == season and total_years >= 3 and aav >= 22_000_000 else 0.0,
            "aav_millions": aav / 1_000_000.0,
            "years_remaining": float(max(0, end_year - season + 1)),
        }
    return context


def mentor_room_scores(
    players: list[sqlite3.Row],
    traits_by_player: dict[int, dict[str, int]],
) -> dict[tuple[int, str], float]:
    scores: dict[tuple[int, str], float] = {}
    for player in players:
        team_id = player["team_id"]
        if team_id is None:
            continue
        age = int(player["age"] or 26)
        years_exp = int(player["years_exp"] or 0)
        traits = traits_by_player.get(int(player["player_id"]), {})
        mentor = trait_strength(traits, "mentor")
        leader = trait_strength(traits, "natural_leader") * 0.45
        professional = trait_strength(traits, "quiet_professional") * 0.25
        if age < 28 and years_exp < 6 and mentor <= 0:
            continue
        score = mentor + leader + professional + max(0.0, years_exp - 5) * 0.015
        if score <= 0:
            continue
        key = (int(team_id), position_group(str(player["position"])))
        scores[key] = max(scores.get(key, 0.0), score)
    return scores


def coach_position_scores(con: sqlite3.Connection) -> dict[tuple[int, str], float]:
    if not table_exists(con, "coach_position_ratings"):
        return {}
    rows = con.execute(
        """
        SELECT c.team_id, c.role, cpr.position_group, cpr.rating
        FROM coach_position_ratings cpr
        JOIN coaches c ON c.coach_id = cpr.coach_id
        """
    ).fetchall()
    weighted: dict[tuple[int, str], list[tuple[float, float]]] = {}
    for row in rows:
        group = str(row["position_group"])
        role = str(row["role"] or "")
        if role == "Head Coach":
            weight = 0.45
        elif role == "Offensive Coordinator":
            weight = 0.75 if group in OFFENSE_GROUPS else 0.12
        elif role == "Defensive Coordinator":
            weight = 0.75 if group in DEFENSE_GROUPS else 0.12
        else:
            weight = 0.20
        if group == "ST":
            weight = max(weight, 0.25)
        weighted.setdefault((int(row["team_id"]), group), []).append((float(row["rating"] or 10), weight))
    scores: dict[tuple[int, str], float] = {}
    for key, items in weighted.items():
        total_weight = sum(weight for _rating, weight in items)
        if total_weight <= 0:
            continue
        avg = sum(rating * weight for rating, weight in items) / total_weight
        scores[key] = clamp((avg - 10.0) * 1.15, -8.0, 8.0)
    return scores


def stat_value(stats: dict[str, float], key: str) -> float:
    value = float(stats.get(key, 0.0) or 0.0)
    if value:
        return value
    for alias in STAT_ALIASES.get(key, ()):
        value = float(stats.get(alias, 0.0) or 0.0)
        if value:
            return value
    return 0.0


def primary_snap_context(position: str, stats: dict[str, float]) -> tuple[float, float]:
    side = position_group(position)
    offensive_snaps = stat_value(stats, "offensive_snaps")
    defensive_snaps = stat_value(stats, "defensive_snaps")
    special_snaps = stat_value(stats, "special_teams_snaps")
    if side in {"QB", "RB", "WR", "TE", "OL"}:
        return offensive_snaps, 850.0 if position == "QB" else 780.0
    if side in {"DL", "EDGE", "LB", "CB", "S"}:
        return defensive_snaps, 760.0 if side in {"DL", "EDGE"} else 850.0
    if side == "ST":
        return special_snaps, 150.0
    return max(offensive_snaps, defensive_snaps, special_snaps), 720.0


def low_primary_snap_penalty(
    player: sqlite3.Row,
    *,
    primary_snaps: float,
    full_time: float,
    depth_rank: int | None,
) -> float:
    """Penalize established players who were effectively benched.

    Depth charts can be manipulated or changed after the season, so progression
    should trust actual primary snaps more than a nominal depth slot.
    """
    position = str(player["position"])
    if position_group(position) == "ST":
        return 0.0
    overall = int(player["overall"] or 50)
    age = int(player["age"] or 26)
    years_exp = int(player["years_exp"] or 0)
    band = age_band(age, years_exp, int(player["is_rookie"] or 0))
    share = primary_snaps / max(1.0, full_time)
    penalty = 0.0
    if primary_snaps <= 0:
        if overall >= 82:
            penalty -= 1.55
        elif overall >= 74:
            penalty -= 1.15
        elif overall >= 70:
            penalty -= 1.05
        elif overall >= 68:
            penalty -= 0.82
        elif band not in {"rookie", "young"}:
            penalty -= 0.42
    elif share < 0.18:
        if overall >= 82:
            penalty -= 1.05
        elif overall >= 74:
            penalty -= 0.78
        elif overall >= 68:
            penalty -= 0.42
    elif share < 0.35 and overall >= 74:
        penalty -= 0.36
    if depth_rank is not None and depth_rank >= 3 and overall >= 70:
        penalty -= 0.20
    potential_gap = int(player["potential"] or overall) - overall
    if band in {"rookie", "young"} and potential_gap >= 8 and overall < 74:
        penalty *= 0.68
    elif band in {"rookie", "young"} and potential_gap >= 4 and overall < 70:
        penalty *= 0.78
    if depth_rank is not None and depth_rank <= 2 and band in {"rookie", "young"} and overall < 74:
        penalty *= 0.82
    if band in {"rookie", "young"}:
        penalty *= 0.85 if overall >= 70 else 0.72
    elif band in {"veteran", "late_veteran"}:
        penalty *= 1.18
    return clamp(penalty, -1.9, 0.0)


def usage_score(player: sqlite3.Row, stats: dict[str, float], depth_rank: int | None) -> float:
    position = str(player["position"])
    status = str(player["status"] or "Active")
    if status == "Practice Squad":
        return -2.8
    if status == "Free Agent" or player["team_id"] is None:
        return -1.6

    depth_component = 0.0
    if depth_rank is not None:
        if depth_rank == 1:
            depth_component = 2.2
        elif depth_rank == 2:
            depth_component = 0.8
        elif depth_rank == 3:
            depth_component = -0.2
        else:
            depth_component = -0.8

    offensive_snaps = stat_value(stats, "offensive_snaps")
    defensive_snaps = stat_value(stats, "defensive_snaps")
    special_snaps = stat_value(stats, "special_teams_snaps")
    total_snaps = offensive_snaps + defensive_snaps + special_snaps
    primary_snaps, full_time = primary_snap_context(position, stats)
    snap_penalty = low_primary_snap_penalty(
        player,
        primary_snaps=primary_snaps,
        full_time=full_time,
        depth_rank=depth_rank,
    )
    if total_snaps > 0:
        snap_component = clamp(primary_snaps / full_time * 4.2 - 1.1, -1.5, 3.4)
        st_component = 0.0
        if position_group(position) != "ST" and special_snaps > 0:
            st_component = clamp(special_snaps / 320.0 * 1.15, 0.0, 1.15)
            if primary_snaps <= 0:
                st_component *= 0.80
            if int(player["overall"] or 50) >= 76:
                st_component *= 0.42
        return clamp(snap_component * 0.74 + depth_component * 0.21 + snap_penalty + st_component, -3.4, 4.0)

    if position == "QB":
        stat_component = clamp(stat_value(stats, "pass_attempts") / 560.0 * 4.0 - 1.0, -1.2, 3.2)
    elif position in {"RB", "FB"}:
        touches = stat_value(stats, "rush_attempts") + stat_value(stats, "targets") * 0.55
        stat_component = clamp(touches / 210.0 * 4.0 - 1.0, -1.2, 3.2)
    elif position in {"WR", "TE"}:
        stat_component = clamp(stat_value(stats, "targets") / 110.0 * 4.0 - 1.0, -1.2, 3.2)
    elif position in {"OT", "OG", "C"}:
        stat_component = depth_component
    elif position in {"K", "P", "LS"}:
        attempts = stat_value(stats, "fg_attempts") + stat_value(stats, "punts")
        stat_component = clamp(attempts / 65.0 * 3.0 - 0.4, -0.8, 2.4)
    else:
        impact = (
            stat_value(stats, "tackles") / 80.0
            + stat_value(stats, "sacks") / 8.0
            + stat_value(stats, "interceptions") / 4.0
            + stat_value(stats, "pass_deflections") / 12.0
        )
        stat_component = clamp(impact * 2.4 - 0.8, -1.2, 3.2)
    return clamp((stat_component + depth_component) / 2.0 + snap_penalty, -3.4, 4.0)


def performance_score(position: str, stats: dict[str, float]) -> float:
    if position == "QB":
        score = (
            stat_value(stats, "pass_yards") / 4100.0
            + stat_value(stats, "pass_tds") / 28.0
            - stat_value(stats, "interceptions_thrown") / 14.0
            - stat_value(stats, "sacks_taken") / 55.0
        )
        return clamp(score * 1.6, -2.0, 2.5)
    if position in {"RB", "FB"}:
        score = stat_value(stats, "rush_yards") / 950.0 + stat_value(stats, "rush_tds") / 9.0
        return clamp(score * 1.2, -1.2, 2.2)
    if position in {"WR", "TE"}:
        score = stat_value(stats, "receiving_yards") / 950.0 + stat_value(stats, "receiving_tds") / 8.0
        return clamp(score * 1.2, -1.2, 2.2)
    if position in {"K", "P", "LS"}:
        return 0.0
    score = (
        stat_value(stats, "tackles") / 95.0
        + stat_value(stats, "sacks") / 8.0
        + stat_value(stats, "interceptions") / 4.0
        + stat_value(stats, "pass_deflections") / 13.0
    )
    return clamp(score * 1.1, -1.2, 2.2)


def development_score(mods: dict[str, int], profile: Mapping[str, Any] | None) -> float:
    if profile:
        score = profile_value(profile, "development_bias") * 0.9
        score += profile_value(profile, "late_bloomer_chance") * 0.25
        score -= profile_value(profile, "decline_risk") * 0.15
        return clamp(score, -8.0, 8.0)
    keys = [
        "playing_time_response",
        "scheme_fit_response",
        "coaching_response",
        "practice_habits",
        "football_iq_growth",
        "confidence_response",
        "competition_response",
        "team_success_response",
        "leadership_room_response",
    ]
    values = [mods.get(key, 0) for key in keys]
    return clamp(sum(values) / max(1, len(values)), -8.0, 8.0)


def hidden_context_score(
    mods: dict[str, int],
    *,
    band: str,
    scheme_score: float,
    team_success_score: float,
    performance: float,
    usage: float,
) -> float:
    mentor = float(mods.get("mentor_response", 0) or 0)
    adaptability = float(mods.get("position_change_response", 0) or 0)
    pressure = float(mods.get("pressure_environment", 0) or 0)
    stability = float(mods.get("role_stability_response", 0) or 0)

    score = 0.0
    score += mentor * (0.075 if band in {"rookie", "young"} else 0.020)
    if abs(scheme_score) >= 1.5:
        score += adaptability * 0.055
    else:
        score += adaptability * 0.020
    environment = clamp(team_success_score * 0.35 + performance * 0.50 + usage * 0.15, -3.0, 3.0)
    score += pressure * 0.030 * environment
    score += pressure * 0.018
    score += stability * 0.020
    return clamp(score, -2.2, 2.2)


def personality_circumstance_score(
    rng: random.Random,
    *,
    player: sqlite3.Row,
    traits: dict[str, int],
    mods: dict[str, int],
    stats: dict[str, float],
    contract: dict[str, float],
    mentor_score: float,
    team_success_score: float,
    scheme_score: float,
    coaching_score: float,
    usage_score: float,
    performance_score: float,
    opportunity_drag: float,
    band: str,
) -> float:
    """Contextual career variance from personality meeting real incentives."""
    greedy = trait_strength(traits, "greedy")
    chip = trait_strength(traits, "chip_on_shoulder")
    streaky = trait_strength(traits, "streaky_confidence")
    big_stage = trait_strength(traits, "big_stage")
    ring = trait_strength(traits, "ring_chaser")
    media = trait_strength(traits, "media_savvy")
    lunch = trait_strength(traits, "lunch_pail")
    film = trait_strength(traits, "film_junkie")
    mentor_trait = trait_strength(traits, "mentor")
    distraction = trait_strength(traits, "locker_room_distraction")
    off_field = trait_strength(traits, "off_field_issue")
    adversity_response = float(mods.get("adversity_response", 0) or 0)
    competition_response = float(mods.get("competition_response", 0) or 0)
    recovery_response = float(mods.get("injury_recovery_response", 0) or 0)
    leadership_response = float(mods.get("leadership_room_response", 0) or 0)
    team_code = str(player["team"] or "")
    status = str(player["status"] or "")
    is_rostered = team_code.upper() != "FA" and status.lower() not in {"free agent", "retired"}

    score = 0.0
    if contract.get("contract_year", 0.0):
        score += greedy * rng.uniform(0.35, 1.45)
        score += trait_strength(traits, "big_stage") * rng.uniform(0.10, 0.55)
        score += trait_strength(traits, "chip_on_shoulder") * rng.uniform(0.08, 0.45)
        if performance_score > 0.7:
            score += greedy * 0.25
    if contract.get("new_big_deal", 0.0):
        payday_drag = greedy * rng.uniform(0.45, 1.70)
        payday_drag += distraction * 0.25 + off_field * 0.35
        payday_drag -= lunch * 0.30 + film * 0.22 + mentor_trait * 0.18
        score -= max(0.0, payday_drag)
    if contract.get("new_major_deal", 0.0) and media > 0:
        score += media * rng.uniform(-0.35, 0.35)

    if band in {"rookie", "young"}:
        if mentor_score <= 0.08:
            score -= max(0.0, 0.34 - mentor_score) * (0.7 + max(0.0, -opportunity_drag) * 0.22)
        else:
            score += min(0.55, mentor_score * 0.42) * (0.65 + film * 0.35 + lunch * 0.25)
    if chip > 0 and usage_score < 0.35 and performance_score >= -0.2:
        score += chip * rng.uniform(0.10, 0.75)
    if chip > 0 and usage_score < -1.0 and performance_score < 0:
        score -= chip * rng.uniform(0.10, 0.55)

    adversity_pressure = 0.0
    adversity_pressure += max(0.0, -usage_score) * 0.38
    adversity_pressure += max(0.0, -performance_score) * 0.28
    adversity_pressure += max(0.0, -team_success_score) * 0.14
    adversity_pressure += max(0.0, -scheme_score) * 0.08
    adversity_pressure += max(0.0, -coaching_score) * 0.07
    adversity_pressure += max(0.0, -opportunity_drag) * 0.30
    adversity_pressure = clamp(adversity_pressure, 0.0, 2.8)
    if is_rostered and adversity_pressure > 0.35 and adversity_response > 0:
        grinder_traits = lunch * 0.28 + film * 0.24 + chip * 0.34 + big_stage * 0.12
        room_support = max(0.0, mentor_score) * (0.15 + max(0.0, leadership_response) * 0.004)
        response = adversity_response * 0.012 + competition_response * 0.004
        score += adversity_pressure * (response + grinder_traits + room_support) * rng.uniform(0.35, 0.95)
    if adversity_pressure > 0.8 and adversity_response < 0:
        score += adversity_pressure * adversity_response * rng.uniform(0.010, 0.026)
    if is_rostered and recovery_response > 0 and performance_score < 0.15 and usage_score < 0.3:
        score += min(0.45, recovery_response * 0.012) * (0.45 + lunch * 0.25 + film * 0.20)
    special_snaps = stat_value(stats, "special_teams_snaps")
    primary_snaps, _full_time = primary_snap_context(str(player["position"]), stats)
    if is_rostered and special_snaps >= 120 and primary_snaps < 220:
        st_role = clamp((special_snaps - 100.0) / 340.0, 0.0, 1.0)
        st_response = competition_response * 0.006 + adversity_response * 0.006
        st_response += lunch * 0.16 + film * 0.12 + chip * 0.12
        st_response -= distraction * 0.16 + off_field * 0.20
        if band in {"rookie", "young"}:
            score += st_role * st_response * rng.uniform(0.35, 0.95)
        elif st_response < 0:
            score += st_role * st_response * rng.uniform(0.20, 0.60)

    if streaky > 0:
        momentum = performance_score * 0.50 + usage_score * 0.20 + team_success_score * 0.12
        score += streaky * clamp(momentum, -1.20, 1.20) * rng.uniform(0.45, 1.05)
    if big_stage > 0:
        score += big_stage * clamp(team_success_score, -1.0, 1.0) * rng.uniform(0.18, 0.65)
    if ring > 0:
        score += ring * clamp(team_success_score, -1.0, 1.0) * 0.40
        if team_success_score < -1.0:
            score -= ring * 0.35

    if scheme_score < -2.0:
        score -= (abs(scheme_score) - 2.0) * (0.07 + max(0.0, -float(traits.get("coach_connector", 0) or 0) / 100.0) * 0.02)
    if coaching_score < -1.5:
        score -= abs(coaching_score) * (0.035 + distraction * 0.035)

    return clamp(score, -2.4, 2.0)


def injury_development_score(
    injury: dict[str, float],
    *,
    band: str,
    mods: dict[str, int],
    profile: Mapping[str, Any] | None,
) -> float:
    if not injury:
        return 0.0
    total_missed = float(injury.get("games_missed", 0.0) or 0.0)
    recent_missed = float(injury.get("recent_games_missed", 0.0) or 0.0)
    active_expected = float(injury.get("active_expected_games", 0.0) or 0.0)
    season_expected = float(injury.get("season_expected_games", 0.0) or 0.0)
    major_rows = float(injury.get("major_rows", 0.0) or 0.0)
    recurrence = float(injury.get("max_recurrence_risk", 0.0) or 0.0)

    raw = 0.0
    raw += min(1.8, total_missed * 0.018)
    raw += recent_missed * 0.090
    raw += active_expected * 0.260
    raw += season_expected * 0.220
    raw += major_rows * 0.420
    raw += recurrence * 0.620
    raw *= {
        "rookie": 0.82,
        "young": 0.88,
        "prime": 1.00,
        "veteran": 1.16,
        "late_veteran": 1.32,
    }.get(band, 1.0)
    recovery = max(0.0, float(mods.get("injury_recovery_response", 0) or 0)) * 0.120
    recovery += max(0.0, profile_value(profile, "regression_resistance")) * 0.045
    return -clamp(raw - recovery, 0.0, 4.5)


def response_multiplier(mods: dict[str, int], key: str) -> float:
    """Scale how strongly a player reacts to an actual environment.

    A positive response means the circumstance matters more, good or bad. A
    negative response means the player is less shaped by that circumstance.
    """
    return clamp(1.0 + float(mods.get(key, 0) or 0) * 0.055, 0.45, 1.65)


def practice_squad_effect(mods: dict[str, int], band: str) -> float:
    """Practice squad should wash out many players, but still hide gems."""
    value = float(mods.get("practice_squad_response", 0) or 0)
    age_penalty = {
        "rookie": 0.0,
        "young": -0.20,
        "prime": -0.55,
        "veteran": -0.95,
        "late_veteran": -1.35,
    }.get(band, -0.55)
    return clamp(value * 0.38 - 1.35 + age_penalty, -5.5, 3.0)


def potential_volatility_sigma(volatility: float) -> float:
    """Positive volatility widens the range; negative volatility stabilizes it."""
    if volatility >= 0:
        return clamp(0.65 + volatility * 0.10, 0.45, 1.85)
    return clamp(0.65 + volatility * 0.045, 0.35, 0.65)


def age_curve(age_band_value: str) -> float:
    return {
        "rookie": 1.20,
        "young": 0.85,
        "prime": 0.10,
        "veteran": -0.75,
        "late_veteran": -1.55,
    }.get(age_band_value, 0.0)


def position_group(position: str) -> str:
    return POSITION_GROUP.get(position, position)


def position_age_adjustment(position: str, age: int, band: str) -> float:
    """Position-specific age curve layered over the broad age band.

    The broad band keeps the system easy to reason about, while this function
    handles the football truth that positions age differently.
    """
    group = position_group(position)
    if group == "QB":
        if band == "veteran":
            return 0.65 if age <= 35 else 0.25
        if band == "late_veteran":
            return 0.95 if age <= 38 else 0.45
        return 0.0
    if group == "RB":
        if age <= 24:
            return 0.15
        if age <= 26:
            return 0.0
        if age == 27:
            return -0.35
        if age == 28:
            return -0.70
        if age == 29:
            return -1.10
        if age == 30:
            return -1.55
        return -2.15
    if group == "WR":
        if age <= 27:
            return 0.05
        if age <= 29:
            return -0.15
        if age == 30:
            return -0.40
        if age == 31:
            return -0.75
        return -1.15
    if group == "CB":
        if age <= 27:
            return 0.05
        if age == 28:
            return -0.20
        if age == 29:
            return -0.45
        if age == 30:
            return -0.80
        if age == 31:
            return -1.10
        return -1.45
    if group == "EDGE":
        if age <= 28:
            return 0.05
        if age <= 30:
            return -0.30
        if age <= 32:
            return -0.80
        return -1.25
    if group == "LB":
        if age <= 28:
            return 0.0
        if age <= 30:
            return -0.35
        if age <= 32:
            return -0.90
        return -1.35
    if group == "S":
        if age <= 29:
            return 0.0
        if age <= 31:
            return -0.35
        if age <= 33:
            return -0.85
        return -1.30
    if group == "TE":
        if age <= 28:
            return 0.05
        if age <= 30:
            return -0.15
        if age <= 32:
            return -0.55
        return -1.05
    if group == "OL":
        if 27 <= age <= 31:
            return 0.10
        if age <= 33:
            return -0.15
        if age <= 35:
            return -0.65
        return -1.15
    if group == "DL":
        if age <= 29:
            return 0.0
        if age <= 31:
            return -0.25
        if age <= 33:
            return -0.70
        return -1.15
    if group == "ST":
        if band == "veteran":
            return 0.50
        if band == "late_veteran":
            return 1.05 if age <= 38 else 0.45
    return 0.0


def position_decline_profile(position: str, age: int, band: str) -> tuple[float, float]:
    """Return probability and severity multipliers for abrupt decline events."""
    group = position_group(position)
    probability = 1.0
    severity = 1.0
    if group == "QB":
        probability *= 0.45 if band == "veteran" else 0.60
        severity *= 0.70
        if age >= 38:
            probability *= 1.45
    elif group == "RB":
        probability *= 1.65
        severity *= 1.35
        if age >= 29:
            probability *= 1.35
            severity *= 1.15
    elif group in {"WR", "CB"}:
        probability *= 1.20
        severity *= 1.12
        if age >= 31:
            probability *= 1.20
    elif group in {"EDGE", "LB", "S"}:
        probability *= 1.12
        severity *= 1.08
    elif group in {"OL", "DL", "TE"}:
        probability *= 0.92
        severity *= 0.95
    elif group == "ST":
        probability *= 0.45
        severity *= 0.65
    return probability, severity


def rating_position_age_adjustment(position: str, age: int, rating_key: str, band: str) -> float:
    """Per-rating aging nuance so physical traits fade before mental traits."""
    group = position_group(position)
    kind = rating_type(rating_key)
    if group == "QB":
        if kind == "mental" and band in {"veteran", "late_veteran"}:
            return 0.25
        if rating_key == "throw_power" and age >= 36:
            return -0.35 if age <= 38 else -0.75
        return 0.0
    if group == "RB":
        if kind == "physical":
            if age >= 31:
                return -1.25
            if age >= 29:
                return -0.85
            if age >= 27:
                return -0.45
        if kind == "mental" and age >= 29:
            return 0.10
        return 0.0
    if group in {"WR", "CB"} and rating_key in SPEED_DECAY_RATINGS:
        if age >= 32:
            return -0.95
        if age >= 30:
            return -0.55
        if age >= 28:
            return -0.25
    if group in {"EDGE", "LB", "S"} and rating_key in SPEED_DECAY_RATINGS:
        if age >= 32:
            return -0.75
        if age >= 30:
            return -0.40
    if group in {"OL", "DL"} and rating_key in POWER_HOLD_RATINGS and band in {"veteran", "late_veteran"}:
        return 0.35
    if group == "ST" and kind == "specialist" and band in {"veteran", "late_veteran"}:
        return 0.30
    return 0.0


def random_sigma(age_band_value: str) -> float:
    return {
        "rookie": 1.45,
        "young": 1.25,
        "prime": 0.95,
        "veteran": 1.05,
        "late_veteran": 1.25,
    }.get(age_band_value, 1.0)


def trait_strength(traits: dict[str, int], trait_key: str) -> float:
    return clamp(float(traits.get(trait_key, 0) or 0) / 100.0, 0.0, 1.0)


def personality_development_score(traits: dict[str, int], band: str) -> float:
    """Soft direct personality effect on growth.

    This complements the hidden development modifiers seeded at save start.
    It should matter at the margins, not overwhelm playing time, coaching,
    scheme fit, performance, age, or randomness.
    """
    score = 0.0
    score += 1.35 * trait_strength(traits, "lunch_pail")
    score += 1.10 * trait_strength(traits, "film_junkie")
    score += 0.70 * trait_strength(traits, "coach_connector")
    score += 0.62 * trait_strength(traits, "quiet_professional")
    score += 0.55 * trait_strength(traits, "natural_leader")
    score += 0.42 * trait_strength(traits, "mentor")
    score += 0.48 * trait_strength(traits, "chip_on_shoulder")
    score += 0.20 * trait_strength(traits, "big_stage")
    score -= 1.25 * trait_strength(traits, "locker_room_distraction")
    score -= 1.35 * trait_strength(traits, "off_field_issue")
    score -= 0.18 * trait_strength(traits, "greedy")
    if band in {"rookie", "young"}:
        score += 0.35 * trait_strength(traits, "chip_on_shoulder")
        score += 0.25 * trait_strength(traits, "mentor")
    if band in {"veteran", "late_veteran"}:
        score += 0.25 * trait_strength(traits, "quiet_professional")
        score += 0.18 * trait_strength(traits, "natural_leader")
    return clamp(score, -4.0, 4.0)


def personality_regression_score(traits: dict[str, int], band: str) -> float:
    score = 0.0
    score += 1.15 * trait_strength(traits, "lunch_pail")
    score += 0.85 * trait_strength(traits, "film_junkie")
    score += 0.80 * trait_strength(traits, "quiet_professional")
    score += 0.42 * trait_strength(traits, "mentor")
    score += 0.35 * trait_strength(traits, "natural_leader")
    score -= 1.45 * trait_strength(traits, "off_field_issue")
    score -= 1.15 * trait_strength(traits, "locker_room_distraction")
    score -= 0.55 * trait_strength(traits, "streaky_confidence")
    score -= 0.18 * trait_strength(traits, "greedy")
    if band in {"veteran", "late_veteran"}:
        score += 0.40 * trait_strength(traits, "lunch_pail")
        score += 0.32 * trait_strength(traits, "quiet_professional")
    return clamp(score, -4.0, 4.0)


def personality_variance_score(traits: dict[str, int]) -> float:
    score = 0.0
    score += 1.25 * trait_strength(traits, "streaky_confidence")
    score += 0.80 * trait_strength(traits, "off_field_issue")
    score += 0.55 * trait_strength(traits, "locker_room_distraction")
    score += 0.42 * trait_strength(traits, "chip_on_shoulder")
    score += 0.25 * trait_strength(traits, "big_stage")
    score -= 0.45 * trait_strength(traits, "quiet_professional")
    score -= 0.38 * trait_strength(traits, "lunch_pail")
    score -= 0.22 * trait_strength(traits, "film_junkie")
    return clamp(score, -1.0, 2.0)


def personality_potential_score(traits: dict[str, int], band: str) -> float:
    score = 0.0
    score += 0.95 * trait_strength(traits, "lunch_pail")
    score += 0.80 * trait_strength(traits, "film_junkie")
    score += 0.55 * trait_strength(traits, "chip_on_shoulder")
    score += 0.38 * trait_strength(traits, "coach_connector")
    score += 0.30 * trait_strength(traits, "big_stage")
    score -= 0.95 * trait_strength(traits, "off_field_issue")
    score -= 0.82 * trait_strength(traits, "locker_room_distraction")
    score += 0.18 * trait_strength(traits, "streaky_confidence")
    if band in {"veteran", "late_veteran"}:
        score -= 0.20 * trait_strength(traits, "streaky_confidence")
    return clamp(score, -3.0, 3.0)


def personality_sigma(band: str, traits: dict[str, int]) -> float:
    return clamp(random_sigma(band) + personality_variance_score(traits) * 0.28, 0.65, 1.95)


def has_meaningful_stats(stats: dict[str, float]) -> bool:
    return any(float(value or 0.0) > 0 for value in stats.values())


def low_opportunity_protection(
    *,
    usage: float,
    performance: float,
    no_stats: bool,
    band: str,
    old_overall: int,
    old_potential: int,
    mods: dict[str, int],
    traits: dict[str, int],
) -> float:
    """Offset some low-snap drag when context suggests patient development."""
    if usage >= -0.35:
        return 0.0
    potential_gap = old_potential - old_overall
    protection = 0.0
    protection += max(0.0, float(mods.get("playing_time_response", 0) or 0)) * 0.018
    protection += max(0.0, float(mods.get("adversity_response", 0) or 0)) * 0.024
    protection += max(0.0, float(mods.get("competition_response", 0) or 0)) * 0.012
    protection += max(0.0, float(mods.get("practice_habits", 0) or 0)) * 0.018
    protection += max(0.0, float(mods.get("football_iq_growth", 0) or 0)) * 0.010
    protection += trait_strength(traits, "chip_on_shoulder") * 0.38
    protection += trait_strength(traits, "lunch_pail") * 0.30
    protection += trait_strength(traits, "film_junkie") * 0.26
    protection += trait_strength(traits, "quiet_professional") * 0.18
    protection += max(0.0, performance) * 0.18
    if band in {"rookie", "young"}:
        protection += clamp(potential_gap, 0, 14) * 0.035
    if no_stats:
        protection *= 0.58
    if old_overall >= 78 and band not in {"rookie", "young"}:
        protection *= 0.45
    elif old_overall >= 72 and band in {"prime", "veteran", "late_veteran"}:
        protection *= 0.65
    protection -= max(0.0, -float(mods.get("playing_time_response", 0) or 0)) * 0.012
    protection -= trait_strength(traits, "locker_room_distraction") * 0.24
    protection -= trait_strength(traits, "off_field_issue") * 0.30
    return clamp(protection, 0.0, 0.95)


def opportunity_regression_score(
    *,
    usage: float,
    stats: dict[str, float],
    depth_rank: int | None,
    status: str,
    band: str,
    old_overall: int = 0,
    old_potential: int = 0,
    mods: dict[str, int] | None = None,
    traits: dict[str, int] | None = None,
    performance: float = 0.0,
) -> float:
    """Development drag for players who are not getting real football reps."""
    mods = mods or {}
    traits = traits or {}
    no_stats = not has_meaningful_stats(stats)
    special_snaps = stat_value(stats, "special_teams_snaps")
    if status == "Practice Squad":
        base = -0.55 if band in {"rookie", "young"} else -0.85
    elif status == "Free Agent":
        base = -0.70
    else:
        base = 0.0

    if usage < -1.5:
        base -= {"rookie": 0.45, "young": 0.60, "prime": 0.78, "veteran": 0.88, "late_veteran": 1.05}.get(band, 0.65)
    elif usage < -0.5:
        base -= {"rookie": 0.22, "young": 0.34, "prime": 0.48, "veteran": 0.58, "late_veteran": 0.72}.get(band, 0.42)
    if old_overall >= 78 and usage < -0.75:
        base -= {"rookie": 0.25, "young": 0.38, "prime": 0.70, "veteran": 0.85, "late_veteran": 1.00}.get(band, 0.55)
    elif old_overall >= 70 and usage < -1.0:
        base -= {"rookie": 0.12, "young": 0.22, "prime": 0.40, "veteran": 0.52, "late_veteran": 0.66}.get(band, 0.32)
    if no_stats and old_overall >= 70 and status == "Active":
        base -= {"rookie": 0.20, "young": 0.44, "prime": 0.72, "veteran": 0.86, "late_veteran": 1.04}.get(band, 0.52)

    if no_stats and depth_rank is None:
        base -= {"rookie": 0.16, "young": 0.28, "prime": 0.34, "veteran": 0.42, "late_veteran": 0.55}.get(band, 0.32)
    elif no_stats and depth_rank is not None and depth_rank >= 4:
        base -= {"rookie": 0.10, "young": 0.20, "prime": 0.28, "veteran": 0.36, "late_veteran": 0.48}.get(band, 0.24)

    if special_snaps >= 120 and base < 0:
        st_offset = clamp((special_snaps - 80.0) / 300.0, 0.0, 0.95)
        if old_overall >= 76 and band not in {"rookie", "young"}:
            st_offset *= 0.35
        elif old_overall >= 70 and band in {"prime", "veteran", "late_veteran"}:
            st_offset *= 0.55
        base += st_offset

    if status == "Active" and base < 0:
        base += low_opportunity_protection(
            usage=usage,
            performance=performance,
            no_stats=no_stats,
            band=band,
            old_overall=old_overall,
            old_potential=old_potential or old_overall,
            mods=mods,
            traits=traits,
        )

    return clamp(base, -3.25, 0.0)


def work_habit_context_score(mods: dict[str, int], traits: dict[str, int], band: str) -> float:
    """Let hidden/personality negatives matter more than tiny positive nudges."""
    raw = 0.0
    raw += float(mods.get("practice_habits", 0) or 0) * 0.08
    raw += float(mods.get("football_iq_growth", 0) or 0) * 0.035
    raw += 0.45 * trait_strength(traits, "lunch_pail")
    raw += 0.35 * trait_strength(traits, "film_junkie")
    raw += 0.22 * trait_strength(traits, "quiet_professional")
    raw += 0.18 * trait_strength(traits, "coach_connector")
    raw -= 0.62 * trait_strength(traits, "locker_room_distraction")
    raw -= 0.78 * trait_strength(traits, "off_field_issue")
    raw -= 0.20 * trait_strength(traits, "greedy")
    if raw < 0 and band in {"veteran", "late_veteran"}:
        raw *= 1.25
    if raw > 0 and band not in {"rookie", "young"}:
        raw *= 0.65
    return clamp(raw, -1.45, 0.65)


def potential_gravity_score(
    *,
    potential_gap: int,
    band: str,
    dev_trait: str | None,
    overall: int,
) -> float:
    """Make players near their ceiling harder to keep pushing upward."""
    if potential_gap <= 0:
        score = -0.58
    elif potential_gap <= 3:
        score = -0.30
    elif potential_gap <= 6:
        score = -0.12
    else:
        score = 0.0
    if overall >= 84:
        score -= min(0.34, (overall - 83) * 0.025)
    if band in {"veteran", "late_veteran"}:
        score *= 1.35
    elif band == "prime":
        score *= 1.10
    if dev_trait in {"Star", "Superstar", "Elite", "X-Factor"} and band in {"rookie", "young"}:
        score *= 0.55
    return clamp(score, -1.25, 0.0)


def breakout_delta(
    rng: random.Random,
    *,
    band: str,
    potential_gap: int,
    mods: dict[str, int],
    traits: dict[str, int],
    dev_trait: str | None = None,
) -> float:
    probability = 0.008
    if band in {"rookie", "young"}:
        probability += 0.018
    if potential_gap >= 10:
        probability += 0.012
    probability += max(0, mods.get("late_bloomer_tendency", 0)) * 0.002
    probability += max(0, mods.get("potential_volatility", 0)) * 0.001
    probability += trait_strength(traits, "lunch_pail") * 0.004
    probability += trait_strength(traits, "film_junkie") * 0.004
    probability += trait_strength(traits, "chip_on_shoulder") * 0.004
    probability += trait_strength(traits, "big_stage") * 0.003
    probability += trait_strength(traits, "streaky_confidence") * 0.003
    probability -= trait_strength(traits, "off_field_issue") * 0.004
    probability -= trait_strength(traits, "locker_room_distraction") * 0.003
    if dev_trait in {"Star", "Superstar", "Elite", "X-Factor"}:
        probability += 0.01
    if rng.random() < clamp(probability, 0.0, 0.08):
        ceiling = 4.2 + trait_strength(traits, "streaky_confidence") * 0.7
        return rng.uniform(1.4, ceiling)
    return 0.0


def decline_delta(
    rng: random.Random,
    *,
    position: str,
    age: int,
    band: str,
    mods: dict[str, int],
    traits: dict[str, int],
    profile: Mapping[str, Any] | None,
) -> float:
    if band not in {"veteran", "late_veteran"}:
        return 0.0
    decline_risk = profile_value(profile, "decline_risk", float(mods.get("decline_acceleration_risk", 0) or 0))
    probability = 0.025 if band == "veteran" else 0.055
    probability += max(0.0, decline_risk) * 0.006
    probability -= max(0.0, mods.get("regression_resistance", 0)) * 0.002
    probability_mult, severity_mult = position_decline_profile(position, age, band)
    personality_resistance = personality_regression_score(traits, band)
    probability += max(0.0, -personality_resistance) * 0.010
    probability -= max(0.0, personality_resistance) * 0.004
    severity_mult *= 1.0 + max(0.0, -personality_resistance) * 0.08
    severity_mult *= 1.0 - max(0.0, personality_resistance) * 0.04
    probability *= probability_mult
    if rng.random() < clamp(probability, 0.0, 0.18):
        max_loss = 4.5 if band == "late_veteran" else 3.4
        return -rng.uniform(1.2, max_loss) * clamp(severity_mult, 0.45, 1.75)
    return 0.0


def rating_delta_for_type(
    base_delta: float,
    rating_key: str,
    band: str,
    potential_gap: int,
    *,
    position: str,
    age: int,
    injury_score: float,
) -> float:
    kind = rating_type(rating_key)
    injury_adjust = 0.0
    if injury_score < 0:
        if rating_key == "durability":
            injury_adjust = injury_score * 0.62
        elif rating_key == "stamina":
            injury_adjust = injury_score * 0.36
        elif kind == "physical":
            injury_adjust = injury_score * 0.44
        elif kind == "skill":
            injury_adjust = injury_score * 0.16
        elif kind == "mental":
            injury_adjust = injury_score * 0.05
    if kind == "mental":
        age_adjust = {"rookie": 0.9, "young": 0.65, "prime": 0.30, "veteran": 0.05, "late_veteran": -0.20}.get(band, 0.0)
        return base_delta * 0.68 + age_adjust + rating_position_age_adjustment(position, age, rating_key, band) + injury_adjust
    if kind == "physical":
        age_adjust = {"rookie": 0.35, "young": 0.15, "prime": -0.20, "veteran": -1.05, "late_veteran": -1.95}.get(band, 0.0)
        return base_delta * 0.72 + age_adjust + rating_position_age_adjustment(position, age, rating_key, band) + injury_adjust
    if kind == "specialist":
        return base_delta * 0.48 + rating_position_age_adjustment(position, age, rating_key, band) + injury_adjust
    ceiling_adjust = 0.35 if potential_gap >= 12 and band in {"rookie", "young"} else 0.0
    return base_delta + ceiling_adjust + rating_position_age_adjustment(position, age, rating_key, band) + injury_adjust


def build_contexts(
    con: sqlite3.Connection,
    *,
    game_id: str,
    from_season: int,
    seed: int,
) -> tuple[list[PlayerContext], dict[int, dict[str, int]]]:
    rng = random.Random(seed)
    players = load_players(con, from_season)
    modifiers = load_modifiers(con, game_id, from_season)
    profiles = load_profiles(con, game_id, from_season)
    personality_traits = player_development_modifiers.load_personality_traits(con, game_id=game_id, season=from_season)
    modifiers, profiles = add_transient_development_foundation(
        con,
        game_id=game_id,
        season=from_season,
        seed=seed,
        players=players,
        modifiers=modifiers,
        profiles=profiles,
        traits=personality_traits,
    )
    stats_by_player = load_stats(con, from_season)
    injuries_by_player = load_injury_context(con, from_season)
    depth = load_depth_rank(con)
    scheme_context = load_scheme_context(con, from_season)
    team_success = load_team_success(con, from_season)
    contract_context = load_contract_context(con, from_season)
    mentor_scores = mentor_room_scores(players, personality_traits)
    coach_scores = coach_position_scores(con)
    rating_rows = load_ratings(con, from_season)
    contexts: list[PlayerContext] = []
    for player in players:
        player_id = int(player["player_id"])
        age = int(player["age"] or 26)
        years_exp = int(player["years_exp"] or 0)
        band = age_band(age, years_exp, int(player["is_rookie"] or 0))
        position = str(player["position"])
        group = position_group(position)
        mods = modifiers.get(player_id, {})
        traits = personality_traits.get(player_id, {})
        profile = profiles.get(player_id)
        scheme_row = scheme_context.get(player_id)
        scheme_score = 0.0
        if scheme_row:
            current_fit = float(scheme_row["current_fit"] or 50)
            growth_fit = float(scheme_row["growth_fit"] or current_fit)
            scheme_score = clamp((current_fit - 70.0) / 4.0 + (growth_fit - current_fit) / 10.0, -5.0, 5.0)
        coaching_score = 0.0
        if player["team_id"] is not None:
            coaching_score = coach_scores.get((int(player["team_id"]), group), 0.0)
        success_score = team_success.get(int(player["team_id"]), 0.0) if player["team_id"] is not None else -0.6
        player_stats = stats_by_player.get(player_id, {})
        depth_rank = depth.get(player_id)
        player_usage_score = usage_score(player, player_stats, depth_rank)
        player_perf_score = performance_score(position, player_stats)
        practice_squad_score = 0.0
        if str(player["status"]) == "Practice Squad":
            practice_squad_score = practice_squad_effect(mods, band)
        elif str(player["status"]) == "Free Agent":
            practice_squad_score = -0.7

        potential_gap = int(player["potential"] or player["overall"]) - int(player["overall"] or 50)
        dev_trait = str(player["dev_trait"] or "Normal")
        dev_score = development_score(mods, profile)
        trait_growth_score = personality_development_score(traits, band)
        trait_regression_score = personality_regression_score(traits, band)
        trait_variance_score = personality_variance_score(traits)
        opportunity_drag = opportunity_regression_score(
            usage=player_usage_score,
            stats=player_stats,
            depth_rank=depth_rank,
            status=str(player["status"] or "Active"),
            band=band,
            old_overall=int(player["overall"] or 0),
            old_potential=int(player["potential"] or player["overall"] or 0),
            mods=mods,
            traits=traits,
            performance=player_perf_score,
        )
        work_habit_effect = work_habit_context_score(mods, traits, band)
        ceiling_gravity = potential_gravity_score(
            potential_gap=potential_gap,
            band=band,
            dev_trait=dev_trait,
            overall=int(player["overall"] or 50),
        )
        player_injury_score = injury_development_score(
            injuries_by_player.get(player_id, {}),
            band=band,
            mods=mods,
            profile=profile,
        )
        mentor_score = 0.0
        if player["team_id"] is not None:
            mentor_score = mentor_scores.get((int(player["team_id"]), group), 0.0)
        random_score = rng.gauss(0.0, personality_sigma(band, traits))
        boom = breakout_delta(
            rng,
            band=band,
            potential_gap=potential_gap,
            mods=mods,
            traits=traits,
            dev_trait=dev_trait,
        )
        position_age_score = position_age_adjustment(position, age, band)
        bust = decline_delta(
            rng,
            position=position,
            age=age,
            band=band,
            mods=mods,
            traits=traits,
            profile=profile,
        )
        potential_miss = potential_miss_delta(
            rng,
            band=band,
            potential_gap=potential_gap,
            old_overall=int(player["overall"] or 50),
            mods=mods,
            traits=traits,
            scheme_score=scheme_score,
            coaching_score=coaching_score,
            usage_score=player_usage_score,
            injury_score=player_injury_score,
            opportunity_drag=opportunity_drag,
            work_habit_effect=work_habit_effect,
            dev_trait=dev_trait,
        )
        usage_effect = player_usage_score * response_multiplier(mods, "playing_time_response")
        scheme_effect = scheme_score * response_multiplier(mods, "scheme_fit_response")
        coaching_effect = coaching_score * response_multiplier(mods, "coaching_response")
        success_effect = success_score * response_multiplier(mods, "team_success_response")
        role_stability_effect = float(mods.get("role_stability_response", 0) or 0) * 0.035
        hidden_factor_effect = hidden_context_score(
            mods,
            band=band,
            scheme_score=scheme_score,
            team_success_score=success_score,
            performance=player_perf_score,
            usage=player_usage_score,
        )
        circumstance_effect = personality_circumstance_score(
            rng,
            player=player,
            traits=traits,
            mods=mods,
            stats=player_stats,
            contract=contract_context.get(player_id, {}),
            mentor_score=mentor_score,
            team_success_score=success_score,
            scheme_score=scheme_score,
            coaching_score=coaching_score,
            usage_score=player_usage_score,
            performance_score=player_perf_score,
            opportunity_drag=opportunity_drag,
            band=band,
        )
        base = (
            age_curve(band)
            + position_age_score
            + dev_score * 0.26
            + trait_growth_score * 0.33
            + trait_regression_score * 0.14
            + player_injury_score
            + hidden_factor_effect
            + circumstance_effect
            + opportunity_drag
            + work_habit_effect
            + ceiling_gravity
            + usage_effect * 0.26
            + scheme_effect * 0.18
            + coaching_effect * 0.15
            + success_effect * 0.12
            + player_perf_score * 0.18
            + role_stability_effect
            + practice_squad_score
            + random_score
            + boom
            + bust
        )
        if potential_gap <= 0 and base > 0:
            base *= 0.45
        elif potential_gap <= 4 and base > 0:
            base *= 0.72
        base = clamp(base, -6.5, 6.5)

        potential_delta = potential_change(
            rng,
            band=band,
            base_delta=base,
            potential_gap=potential_gap,
            mods=mods,
            profile=profile,
            performance=player_perf_score,
            breakout=boom,
            decline=bust,
            position_age_score=position_age_score,
            injury_score=player_injury_score,
            personality_potential=personality_potential_score(traits, band),
            opportunity_drag=opportunity_drag,
            work_habit_effect=work_habit_effect,
            ceiling_gravity=ceiling_gravity,
            circumstance_score=circumstance_effect,
            potential_miss=potential_miss,
            dev_trait=dev_trait,
        )
        notes = context_notes(
            band,
            base,
            boom,
            bust,
            scheme_score,
            coaching_score,
            player_usage_score,
            position_age_score,
            trait_growth_score,
            trait_regression_score,
            trait_variance_score,
            player_injury_score,
            hidden_factor_effect,
            circumstance_effect,
            opportunity_drag,
            work_habit_effect,
            ceiling_gravity,
            potential_miss,
        )
        contexts.append(
            PlayerContext(
                player_id=player_id,
                name=str(player["player_name"]),
                team=str(player["team"]),
                team_id=int(player["team_id"]) if player["team_id"] is not None else None,
                position=position,
                age=age,
                years_exp=years_exp,
                status=str(player["status"]),
                old_overall=int(player["overall"] or 50),
                old_potential=int(player["potential"] or player["overall"] or 50),
                age_band=band,
                development_score=dev_score,
                usage_score=player_usage_score,
                scheme_score=scheme_score,
                coaching_score=coaching_score,
                team_success_score=success_score,
                performance_score=player_perf_score,
                personality_score=trait_growth_score,
                personality_regression_score=trait_regression_score,
                personality_variance_score=trait_variance_score,
                injury_score=player_injury_score,
                hidden_factor_score=hidden_factor_effect,
                circumstance_score=circumstance_effect,
                practice_squad_score=practice_squad_score,
                random_score=random_score,
                breakout_delta=boom,
                decline_delta=bust,
                potential_miss_delta=potential_miss,
                base_delta=base,
                potential_delta=potential_delta,
                notes=notes,
            )
        )
    return contexts, rating_rows


def potential_change(
    rng: random.Random,
    *,
    band: str,
    base_delta: float,
    potential_gap: int,
    mods: dict[str, int],
    profile: Mapping[str, Any] | None,
    performance: float,
    breakout: float,
    decline: float,
    position_age_score: float,
    injury_score: float,
    personality_potential: float,
    opportunity_drag: float,
    work_habit_effect: float,
    ceiling_gravity: float,
    circumstance_score: float,
    potential_miss: float,
    dev_trait: str | None,
) -> int:
    volatility = profile_value(profile, "potential_volatility", float(mods.get("potential_volatility", 0) or 0))
    late = profile_value(profile, "late_bloomer_chance", float(mods.get("late_bloomer_tendency", 0) or 0))
    decline_risk = profile_value(profile, "decline_risk", float(mods.get("decline_acceleration_risk", 0) or 0))
    resistance = profile_value(profile, "regression_resistance", float(mods.get("regression_resistance", 0) or 0))
    sigma = potential_volatility_sigma(volatility)
    if band in {"rookie", "young"}:
        raw = base_delta * 0.35 + late * 0.12 + performance * 0.18 + rng.gauss(0.0, sigma)
    elif band == "prime":
        raw = base_delta * 0.16 + late * 0.06 + performance * 0.10 + rng.gauss(0.0, sigma * 0.75)
    else:
        raw = base_delta * 0.08 - decline_risk * 0.18 + resistance * 0.12 + rng.gauss(0.0, sigma * 0.65)
    raw += breakout * 0.65 + decline * 0.75
    raw += position_age_score * (0.14 if band in {"rookie", "young"} else 0.24)
    raw += injury_score * (0.20 if band in {"rookie", "young"} else 0.34)
    raw += personality_potential * (0.24 if band in {"rookie", "young"} else 0.14)
    raw += opportunity_drag * 0.18
    raw += min(0.0, work_habit_effect) * 0.22
    raw += ceiling_gravity * 0.42
    raw += circumstance_score * (0.22 if band in {"rookie", "young"} else 0.12)
    raw += potential_miss
    if potential_gap >= 14 and raw < 0 and band in {"rookie", "young"}:
        raw *= 0.65
    if raw < 0 and band in {"rookie", "young"}:
        if dev_trait in {"Star", "Superstar", "Elite", "X-Factor"} and potential_gap >= 8:
            raw *= 0.55
        elif dev_trait in {"Superstar", "Elite", "X-Factor"}:
            raw *= 0.72
    if band == "late_veteran":
        raw -= 0.8
    return clamp_int(raw, -8, 8)


def potential_miss_delta(
    rng: random.Random,
    *,
    band: str,
    potential_gap: int,
    old_overall: int,
    mods: dict[str, int],
    traits: dict[str, int],
    scheme_score: float,
    coaching_score: float,
    usage_score: float,
    injury_score: float,
    opportunity_drag: float,
    work_habit_effect: float,
    dev_trait: str | None,
) -> float:
    """Occasional ceiling recalibration for young players who are not panning out."""
    if band not in {"rookie", "young"} or potential_gap < 4:
        return 0.0
    def strength(key: str) -> float:
        return trait_strength(traits, key)

    volatility = max(0.0, float(mods.get("potential_volatility", 0) or 0))
    decline_risk = max(0.0, float(mods.get("decline_acceleration_risk", 0) or 0))
    regression_resistance = float(mods.get("regression_resistance", 0) or 0)
    mentor_response = float(mods.get("mentor_response", 0) or 0)
    coaching_response = float(mods.get("coaching_response", 0) or 0)
    scheme_response = float(mods.get("scheme_fit_response", 0) or 0)
    playing_time_response = float(mods.get("playing_time_response", 0) or 0)
    adversity_response = float(mods.get("adversity_response", 0) or 0)
    competition_response = float(mods.get("competition_response", 0) or 0)
    injury_recovery_response = float(mods.get("injury_recovery_response", 0) or 0)
    adversity_pressure = clamp(
        max(0.0, -usage_score) * 0.36
        + max(0.0, -scheme_score) * 0.10
        + max(0.0, -coaching_score) * 0.08
        + max(0.0, -injury_score) * 0.18
        + max(0.0, -opportunity_drag) * 0.30
        + max(0.0, -work_habit_effect) * 0.10,
        0.0,
        3.0,
    )

    risk = 0.025 if band == "rookie" else 0.032
    risk += min(0.040, max(0, potential_gap - 8) * 0.003)
    risk += volatility * 0.0045
    risk += decline_risk * 0.0035
    risk += max(0.0, -regression_resistance) * 0.004
    risk += max(0.0, -mentor_response) * 0.0038
    risk += max(0.0, -coaching_response) * 0.0028
    risk += max(0.0, -scheme_response) * 0.0028
    risk += max(0.0, -playing_time_response) * 0.0025
    risk += max(0.0, -scheme_score) * 0.006
    risk += max(0.0, -coaching_score) * 0.004
    risk += max(0.0, -usage_score) * 0.010
    risk += max(0.0, -injury_score) * 0.014
    risk += max(0.0, -opportunity_drag) * 0.012
    risk += max(0.0, -work_habit_effect) * 0.014
    risk += strength("off_field_issue") * 0.035
    risk += strength("locker_room_distraction") * 0.028
    risk += strength("streaky_confidence") * 0.018
    risk -= max(0.0, adversity_response) * adversity_pressure * 0.0035
    risk -= max(0.0, competition_response) * adversity_pressure * 0.0014
    risk -= max(0.0, injury_recovery_response) * max(0.0, -injury_score) * 0.0035
    risk -= strength("chip_on_shoulder") * adversity_pressure * 0.010
    risk -= strength("lunch_pail") * 0.018
    risk -= strength("film_junkie") * 0.015
    risk -= strength("quiet_professional") * 0.012
    risk -= strength("coach_connector") * 0.010
    risk -= max(0.0, regression_resistance) * 0.0025
    risk -= max(0.0, mentor_response) * 0.0025
    risk -= max(0.0, coaching_score) * 0.005
    risk -= max(0.0, scheme_score) * 0.004
    risk -= max(0.0, usage_score) * 0.004

    if dev_trait in {"Star", "Superstar", "Elite", "X-Factor"}:
        risk *= 0.58 if old_overall < 75 else 0.42
    risk = clamp(risk, 0.006, 0.18)
    if rng.random() >= risk:
        return 0.0

    severity = rng.uniform(1.0, 2.4)
    severity += max(0, potential_gap - 10) * 0.10
    severity += max(0.0, -scheme_score) * 0.12
    severity += max(0.0, -coaching_score) * 0.08
    severity += max(0.0, -usage_score) * 0.18
    severity += max(0.0, -injury_score) * 0.22
    severity += max(0.0, -opportunity_drag) * 0.18
    severity += strength("off_field_issue") * 1.1
    severity += strength("locker_room_distraction") * 0.8
    severity += strength("streaky_confidence") * 0.45
    severity -= max(0.0, adversity_response) * adversity_pressure * 0.035
    severity -= max(0.0, competition_response) * adversity_pressure * 0.014
    severity -= strength("chip_on_shoulder") * adversity_pressure * 0.40
    severity -= strength("lunch_pail") * 0.45
    severity -= strength("film_junkie") * 0.35
    severity -= max(0.0, mentor_response) * 0.055
    severity -= max(0.0, coaching_score) * 0.10
    if dev_trait in {"Star", "Superstar", "Elite", "X-Factor"}:
        severity *= 0.62
    cap = 5.0 if potential_gap >= 12 else 3.5
    return -clamp(severity, 0.8, cap)


def context_notes(
    band: str,
    base_delta: float,
    breakout: float,
    decline: float,
    scheme_score: float,
    coaching_score: float,
    usage_score: float,
    position_age_score: float,
    personality_score: float,
    personality_regression_score: float,
    personality_variance_score: float,
    injury_score: float,
    hidden_factor_score: float,
    circumstance_score: float,
    opportunity_drag: float,
    work_habit_effect: float,
    ceiling_gravity: float,
    potential_miss: float,
) -> str:
    bits = [f"{band} curve", f"base {base_delta:+.2f}"]
    if abs(position_age_score) >= 0.2:
        bits.append(f"position aging {position_age_score:+.1f}")
    if abs(personality_score) >= 0.8:
        bits.append(f"personality growth {personality_score:+.1f}")
    if abs(personality_regression_score) >= 0.8:
        bits.append(f"personality regression {personality_regression_score:+.1f}")
    if abs(personality_variance_score) >= 0.8:
        bits.append(f"personality variance {personality_variance_score:+.1f}")
    if injury_score <= -0.8:
        bits.append(f"injury drag {injury_score:+.1f}")
    if abs(hidden_factor_score) >= 0.7:
        bits.append(f"hidden context {hidden_factor_score:+.1f}")
    if abs(circumstance_score) >= 0.35:
        bits.append(f"circumstance {circumstance_score:+.1f}")
    if opportunity_drag <= -0.5:
        bits.append(f"opportunity drag {opportunity_drag:+.1f}")
    if abs(work_habit_effect) >= 0.6:
        bits.append(f"work habits {work_habit_effect:+.1f}")
    if ceiling_gravity <= -0.4:
        bits.append(f"ceiling gravity {ceiling_gravity:+.1f}")
    if breakout:
        bits.append(f"breakout {breakout:+.2f}")
    if decline:
        bits.append(f"decline {decline:+.2f}")
    if potential_miss <= -0.8:
        bits.append(f"potential miss {potential_miss:+.1f}")
    if abs(scheme_score) >= 2.0:
        bits.append(f"scheme {scheme_score:+.1f}")
    if abs(coaching_score) >= 2.0:
        bits.append(f"coaching {coaching_score:+.1f}")
    if abs(usage_score) >= 2.0:
        bits.append(f"usage {usage_score:+.1f}")
    return "; ".join(bits)


def build_progression(
    con: sqlite3.Connection,
    *,
    game_id: str,
    from_season: int,
    to_season: int,
    seed: int,
) -> tuple[list[PlayerResult], list[tuple[int, str, int, int, str]]]:
    contexts, ratings_by_player = build_contexts(
        con,
        game_id=game_id,
        from_season=from_season,
        seed=seed,
    )
    rating_groups = load_rating_groups(con)
    details: list[tuple[int, str, int, int, str]] = []
    results: list[PlayerResult] = []
    for context in contexts:
        ratings = ratings_by_player.get(context.player_id, {})
        if not ratings:
            continue
        old_values = list(ratings.values())
        new_values: dict[str, int] = {}
        old_relevant_values: list[int] = []
        new_relevant_values: list[int] = []
        potential_gap = context.old_potential - context.old_overall
        for rating_key, old_rating in ratings.items():
            raw_delta = rating_delta_for_type(
                context.base_delta,
                rating_key,
                context.age_band,
                potential_gap,
                position=context.position,
                age=context.age,
                injury_score=context.injury_score,
            )
            new_rating = clamp_int(old_rating + raw_delta, 1, 100)
            rating_group = rating_groups.get(rating_key, "universal")
            relevant = rating_group_is_relevant(context.position, rating_group)
            if not relevant and new_rating > old_rating:
                new_rating = old_rating
            if context.age_band in {"rookie", "young"} and context.old_potential > context.old_overall:
                new_rating = min(100, new_rating)
            new_values[rating_key] = new_rating
            if relevant:
                old_relevant_values.append(old_rating)
                new_relevant_values.append(new_rating)
            details.append((context.player_id, rating_key, old_rating, new_rating, rating_type(rating_key)))
        new_avg_source = new_relevant_values or list(new_values.values())
        old_avg_source = old_relevant_values or old_values
        new_avg = sum(new_avg_source) / len(new_avg_source)
        old_avg = sum(old_avg_source) / len(old_avg_source)
        avg_delta = new_avg - old_avg
        new_overall = clamp_int(context.old_overall + avg_delta * 0.72 + context.base_delta * 0.18, 1, 99)
        new_potential = clamp_int(context.old_potential + context.potential_delta, 1, 99)
        new_potential = max(new_potential, new_overall)
        if context.age_band in {"veteran", "late_veteran"} and new_potential > new_overall + 8:
            new_potential = max(new_overall, new_potential - 1)
        results.append(
            PlayerResult(
                context=context,
                old_avg_rating=old_avg,
                new_avg_rating=new_avg,
                old_overall=context.old_overall,
                new_overall=new_overall,
                old_potential=context.old_potential,
                new_potential=new_potential,
                rating_count=len(new_values),
            )
        )
    return results, details


def check_run_available(
    con: sqlite3.Connection,
    *,
    game_id: str,
    from_season: int,
    to_season: int,
    force: bool,
) -> None:
    row = con.execute(
        """
        SELECT run_id
        FROM player_progression_runs
        WHERE game_id = ? AND from_season = ? AND to_season = ?
        """,
        (game_id, from_season, to_season),
    ).fetchone()
    if not row:
        return
    if not force:
        raise ValueError(
            f"Progression already applied for {game_id} {from_season}->{to_season} "
            f"(run_id={row['run_id']}). Use --force to replace the audit rows and ratings."
        )
    con.execute("DELETE FROM player_progression_runs WHERE run_id = ?", (row["run_id"],))


def user_team_id_for_game(con: sqlite3.Connection, game_id: str) -> int | None:
    if table_exists(con, "game_saves"):
        row = con.execute("SELECT user_team_id FROM game_saves WHERE game_id = ?", (game_id,)).fetchone()
        if row and row["user_team_id"] is not None:
            return int(row["user_team_id"])
    if table_exists(con, "active_game_save_view"):
        row = con.execute("SELECT user_team_id FROM active_game_save_view LIMIT 1").fetchone()
        if row and row["user_team_id"] is not None:
            return int(row["user_team_id"])
    return None


def current_alert_date(con: sqlite3.Connection) -> str:
    if table_exists(con, "game_settings"):
        row = con.execute("SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'").fetchone()
        if row and row["setting_value"]:
            return str(row["setting_value"])
    return date.today().isoformat()


def alert_already_exists(
    con: sqlite3.Connection,
    *,
    game_id: str,
    alert_date: str,
    alert_type: str,
    team_id: int | None,
    title: str,
) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM game_alerts
        WHERE game_id = ?
          AND alert_date = ?
          AND alert_type = ?
          AND COALESCE(team_id, -1) = COALESCE(?, -1)
          AND title = ?
        """,
        (game_id, alert_date, alert_type, team_id, title),
    ).fetchone()
    return row is not None


def trait_hint(traits: dict[str, int], *keys: str) -> float:
    return max((trait_strength(traits, key) for key in keys), default=0.0)


def primary_side_snaps(position: str, stats: dict[str, float]) -> float:
    primary_snaps, _full_time = primary_snap_context(position, stats)
    return primary_snaps


def coach_note_candidate(
    result: PlayerResult,
    *,
    traits: dict[str, int],
    stats: dict[str, float],
) -> tuple[float, str, str] | None:
    c = result.context
    if c.status != "Active" or c.team_id is None:
        return None
    st_snaps = stat_value(stats, "special_teams_snaps")
    primary_snaps = primary_side_snaps(c.position, stats)
    grinder = trait_hint(traits, "chip_on_shoulder", "lunch_pail", "film_junkie", "quiet_professional")
    unstable = trait_hint(traits, "streaky_confidence", "locker_room_distraction", "off_field_issue")
    leader = trait_hint(traits, "natural_leader", "mentor", "coach_connector")

    score = 0.0
    title = f"Coach note: {c.name}"
    templates: list[str] = []

    if st_snaps >= 150 and primary_snaps < 360:
        score += 1.6 + min(1.2, st_snaps / 320.0) + grinder * 0.6
        if result.overall_delta >= 0:
            templates.append(
                f"The staff noted that {c.name} seemed to handle the weekly special teams workload like it mattered. "
                "That does not guarantee a bigger role, but coaches sounded more comfortable trusting him in game-day details."
            )
        else:
            templates.append(
                f"The special teams tape gave coaches a longer look at {c.name}. "
                "The notes were mixed: effort showed up, but the staff still wants to see whether those reps translate beyond the kicking game."
            )

    if c.usage_score < -0.65 and result.overall_delta >= 0 and (c.circumstance_score > 0.2 or grinder > 0.25):
        score += 1.4 + grinder * 1.2 + max(0.0, c.circumstance_score) * 0.5
        templates.append(
            f"Position coaches brought up {c.name} as someone who did not let a smaller role turn into a quiet year. "
            "The read is still incomplete, but the tone around his response was more encouraging than the raw opportunity would suggest."
        )

    if c.usage_score < -0.65 and (result.overall_delta < 0 or c.circumstance_score < -0.35) and c.old_overall >= 68:
        score += 1.4 + unstable * 0.8 + max(0.0, -c.circumstance_score) * 0.6
        templates.append(
            f"Coaches were careful with the wording on {c.name}, but the year did not sound frictionless. "
            "The concern is not one single thing; it is whether a reduced role is starting to affect the day-to-day edge."
        )

    if c.injury_score <= -0.8:
        score += 0.9 + max(0.0, -c.injury_score) * 0.35
        if result.overall_delta >= 0:
            templates.append(
                f"The training and position staffs both mentioned {c.name}'s response after getting banged up. "
                "They are not declaring it solved, but the recovery habits drew a quieter kind of confidence."
            )
        else:
            templates.append(
                f"The staff still likes parts of {c.name}'s profile, though the health context left them with some caution. "
                "It sounded more like a watch item than a final judgment."
            )

    if result.overall_delta >= 3 and c.base_delta >= 2.0:
        score += 1.2 + max(0.0, c.performance_score) * 0.3 + leader * 0.3
        templates.append(
            f"Several coaches independently pointed to {c.name} when discussing who looked different by season's end. "
            "They stopped short of calling it a new baseline, but the late-year reviews were clearly warmer."
        )

    if c.potential_miss_delta <= -0.8:
        score += 1.1 + max(0.0, -c.potential_miss_delta) * 0.4
        templates.append(
            f"The development staff sounded a little less certain about how quickly {c.name}'s ceiling arrives. "
            "There is still belief in the player, but the internal notes suggest the path may be less automatic than it looked earlier."
        )

    if not templates:
        return None
    if c.old_overall >= 88 and abs(result.overall_delta) <= 1:
        score *= 0.55
    if c.position in {"K", "P", "LS"}:
        score *= 0.55
    return score, title, templates[0]


def create_progression_coach_notes(
    con: sqlite3.Connection,
    *,
    game_id: str,
    run_id: int,
    from_season: int,
    to_season: int,
    seed: int,
    results: list[PlayerResult],
) -> int:
    user_team_id = user_team_id_for_game(con, game_id)
    if user_team_id is None:
        return 0
    alert_date = current_alert_date(con)
    traits_by_player = player_development_modifiers.load_personality_traits(con, game_id=game_id, season=from_season)
    stats_by_player = load_stats(con, from_season)
    rng = random.Random(seed ^ (run_id * 1000003) ^ 0xC04C4E)
    candidates: list[tuple[float, str, str]] = []
    for result in results:
        if result.context.team_id != user_team_id:
            continue
        candidate = coach_note_candidate(
            result,
            traits=traits_by_player.get(result.context.player_id, {}),
            stats=stats_by_player.get(result.context.player_id, {}),
        )
        if not candidate:
            continue
        score, title, message = candidate
        score += rng.uniform(-0.35, 0.45)
        if score >= 1.25:
            candidates.append((score, title, message))
    if not candidates:
        return 0

    candidates.sort(key=lambda item: item[0], reverse=True)
    selected: list[tuple[float, str, str]] = []
    limit = min(5, max(2, 2 + len(candidates) // 9))
    for item in candidates:
        if len(selected) >= limit:
            break
        threshold = 0.85 if len(selected) < 2 else 0.45
        if rng.random() <= threshold:
            selected.append(item)
    if not selected:
        selected = candidates[: min(2, len(candidates))]

    created = 0
    for _score, title, message in selected:
        if alert_already_exists(
            con,
            game_id=game_id,
            alert_date=alert_date,
            alert_type=PROGRESSION_COACH_NOTE_TYPE,
            team_id=user_team_id,
            title=title,
        ):
            continue
        con.execute(
            """
            INSERT INTO game_alerts (
                game_id, alert_date, severity, alert_type, team_id,
                title, message, due_date
            )
            VALUES (?, ?, 'INFO', ?, ?, ?, ?, ?)
            """,
            (
                game_id,
                alert_date,
                PROGRESSION_COACH_NOTE_TYPE,
                user_team_id,
                title,
                f"{message} [Progression review {from_season}->{to_season}]",
                alert_date,
            ),
        )
        created += 1
    return created


def copy_role_assignments(con: sqlite3.Connection, from_season: int, to_season: int) -> int:
    if not table_exists(con, "player_role_assignments"):
        return 0
    con.execute(
        """
        INSERT INTO player_role_assignments (
            player_id, season, role_key, priority, source, notes, created_at, updated_at
        )
        SELECT player_id, ?, role_key, priority, ?, ?, datetime('now'), datetime('now')
        FROM player_role_assignments
        WHERE season = ?
        ON CONFLICT DO NOTHING
        """,
        (to_season, SOURCE, f"Copied forward from {from_season} progression.", from_season),
    )
    return con.total_changes


def roll_hidden_modifiers(
    con: sqlite3.Connection,
    *,
    game_id: str,
    from_season: int,
    to_season: int,
    seed: int,
    force: bool,
) -> int:
    player_development_modifiers.seed_master_data(con)
    players = load_players(con, from_season)
    player_ids = [int(player["player_id"]) for player in players]
    if force:
        if player_ids:
            placeholders = ",".join("?" for _ in player_ids)
            con.execute(
                f"""
                DELETE FROM player_development_modifiers
                WHERE game_id = ? AND season = ? AND player_id IN ({placeholders})
                """,
                [game_id, to_season, *player_ids],
            )
            con.execute(
                f"""
                DELETE FROM player_development_profiles
                WHERE game_id = ? AND season = ? AND player_id IN ({placeholders})
                """,
                [game_id, to_season, *player_ids],
            )
        con.execute("DELETE FROM new_game_development_runs WHERE game_id = ? AND season = ?", (game_id, to_season))

    factors = con.execute("SELECT * FROM development_factor_definitions ORDER BY factor_key").fetchall()
    from_mods = load_modifiers(con, game_id, from_season)
    traits = player_development_modifiers.load_personality_traits(con, game_id=game_id, season=from_season)
    to_traits = player_development_modifiers.load_personality_traits(con, game_id=game_id, season=to_season)
    modifier_rows = []
    profile_rows = []
    rng = random.Random(seed ^ 0x51EA50)
    for player in players:
        player_id = int(player["player_id"])
        band = player_development_modifiers.age_band(player)
        player_traits = traits.get(player_id, to_traits.get(player_id, {}))
        values: dict[str, int] = {}
        for factor in factors:
            key = str(factor["factor_key"])
            if key in from_mods.get(player_id, {}):
                drift = rng.gauss(0.0, 0.85) + player_development_modifiers.trait_bonus(key, player_traits) * 0.18
                value = player_development_modifiers.clamp_int(from_mods[player_id][key] + drift, -10, 10)
            else:
                value = player_development_modifiers.factor_value(
                    rng,
                    factor=factor,
                    player=player,
                    traits=player_traits,
                )
            values[key] = value
            modifier_rows.append(
                (
                    game_id,
                    to_season,
                    player_id,
                    key,
                    value,
                    band,
                    f"season_progression:{game_id}",
                    f"Rolled hidden {factor['display_name']} modifier forward from {from_season}.",
                )
            )
        profile = player_development_modifiers.profile_from_modifiers(values, band)
        profile_rows.append(
            (
                game_id,
                to_season,
                player_id,
                band,
                profile[0],
                profile[1],
                profile[2],
                profile[3],
                profile[4],
                f"season_progression:{game_id}",
                profile[5],
            )
        )
    con.execute(
        """
        INSERT INTO new_game_development_runs (
            game_id, season, rng_seed, player_count, factor_count, modifier_count, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_id, season) DO UPDATE SET
            rng_seed = excluded.rng_seed,
            player_count = excluded.player_count,
            factor_count = excluded.factor_count,
            modifier_count = excluded.modifier_count,
            notes = excluded.notes
        """,
        (
            game_id,
            to_season,
            seed,
            len(players),
            len(factors),
            len(modifier_rows),
            f"Rolled hidden development modifiers from {from_season} to {to_season}.",
        ),
    )
    con.executemany(
        """
        INSERT INTO player_development_modifiers (
            game_id, season, player_id, factor_key, modifier_value,
            age_band, hidden, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, datetime('now'))
        ON CONFLICT(game_id, season, player_id, factor_key) DO UPDATE SET
            modifier_value = excluded.modifier_value,
            age_band = excluded.age_band,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        modifier_rows,
    )
    con.executemany(
        """
        INSERT INTO player_development_profiles (
            game_id, season, player_id, age_band, development_bias,
            potential_volatility, regression_resistance, late_bloomer_chance,
            decline_risk, hidden, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, datetime('now'))
        ON CONFLICT(game_id, season, player_id) DO UPDATE SET
            age_band = excluded.age_band,
            development_bias = excluded.development_bias,
            potential_volatility = excluded.potential_volatility,
            regression_resistance = excluded.regression_resistance,
            late_bloomer_chance = excluded.late_bloomer_chance,
            decline_risk = excluded.decline_risk,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        profile_rows,
    )
    return len(modifier_rows)


def apply_progression(
    con: sqlite3.Connection,
    *,
    game_id: str,
    from_season: int,
    to_season: int,
    seed: int,
    age_players: bool,
    roll_modifiers: bool,
    notes: str | None,
    force: bool,
    dry_run: bool,
) -> dict[str, Any]:
    ensure_schema(con)
    scheme_fits.seed_master_data(con)
    hidden_foundation_rows = 0
    if not dry_run:
        foundation_players = load_players(con, from_season)
        hidden_foundation_rows = ensure_development_foundation(
            con,
            game_id=game_id,
            season=from_season,
            player_ids=[int(player["player_id"]) for player in foundation_players],
            seed=seed,
        )
    results, details = build_progression(
        con,
        game_id=game_id,
        from_season=from_season,
        to_season=to_season,
        seed=seed,
    )
    if dry_run:
        return {
            "run_id": 0,
            "results": results,
            "rating_details": details,
            "role_score_updates": 0,
            "scheme_fit_rows": 0,
            "hidden_modifier_rows": 0,
            "hidden_foundation_rows": 0,
            "coach_note_alerts": 0,
        }

    check_run_available(con, game_id=game_id, from_season=from_season, to_season=to_season, force=force)
    source = f"progression:{game_id}:{from_season}->{to_season}"
    hidden_rows = hidden_foundation_rows
    if roll_modifiers:
        hidden_rows += roll_hidden_modifiers(
            con,
            game_id=game_id,
            from_season=from_season,
            to_season=to_season,
            seed=seed,
            force=force,
        )

    cur = con.execute(
        """
        INSERT INTO player_progression_runs (
            game_id, from_season, to_season, rng_seed, player_count,
            rating_row_count, overall_changed_count, potential_changed_count,
            hidden_modifier_rows, age_players, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_id,
            from_season,
            to_season,
            seed,
            len(results),
            len(details),
            sum(1 for result in results if result.overall_delta != 0),
            sum(1 for result in results if result.potential_delta != 0),
            hidden_rows,
            1 if age_players else 0,
            notes,
        ),
    )
    run_id = int(cur.lastrowid)
    result_by_player = {result.context.player_id: result for result in results}

    con.executemany(
        """
        INSERT INTO player_progression_results (
            run_id, player_id, from_season, to_season, player_name, team,
            position, age, years_exp, status, age_band, old_overall,
            new_overall, old_potential, new_potential, old_avg_rating,
            new_avg_rating, rating_count, development_score, usage_score,
            scheme_score, coaching_score, team_success_score, performance_score,
            personality_score, personality_regression_score, personality_variance_score,
            injury_score, hidden_factor_score, circumstance_score, practice_squad_score,
            random_score, breakout_delta, decline_delta, potential_miss_delta,
            base_delta, potential_delta, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                result.context.player_id,
                from_season,
                to_season,
                result.context.name,
                result.context.team,
                result.context.position,
                result.context.age,
                result.context.years_exp,
                result.context.status,
                result.context.age_band,
                result.old_overall,
                result.new_overall,
                result.old_potential,
                result.new_potential,
                result.old_avg_rating,
                result.new_avg_rating,
                result.rating_count,
                result.context.development_score,
                result.context.usage_score,
                result.context.scheme_score,
                result.context.coaching_score,
                result.context.team_success_score,
                result.context.performance_score,
                result.context.personality_score,
                result.context.personality_regression_score,
                result.context.personality_variance_score,
                result.context.injury_score,
                result.context.hidden_factor_score,
                result.context.circumstance_score,
                result.context.practice_squad_score,
                result.context.random_score,
                result.context.breakout_delta,
                result.context.decline_delta,
                result.context.potential_miss_delta,
                result.context.base_delta,
                result.potential_delta,
                result.context.notes,
            )
            for result in results
        ],
    )
    con.executemany(
        """
        INSERT INTO player_progression_rating_detail (
            run_id, player_id, from_season, to_season, rating_key,
            rating_type, old_rating, new_rating, delta
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (run_id, player_id, from_season, to_season, rating_key, kind, old, new, new - old)
            for player_id, rating_key, old, new, kind in details
        ],
    )
    con.executemany(
        """
        INSERT INTO player_ratings (
            player_id, season, rating_key, rating_value, confidence, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, 'medium', ?, ?, datetime('now'))
        ON CONFLICT(player_id, season, rating_key) DO UPDATE SET
            rating_value = excluded.rating_value,
            confidence = excluded.confidence,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        [
            (
                player_id,
                to_season,
                rating_key,
                new,
                source,
                f"Season progression run {run_id}: {from_season}->{to_season}.",
            )
            for player_id, rating_key, _old, new, _kind in details
        ],
    )
    con.executemany(
        """
        UPDATE players
        SET overall = ?,
            potential = ?,
            age = age + ?,
            years_exp = years_exp + ?,
            is_rookie = 0
        WHERE player_id = ?
        """,
        [
            (
                result.new_overall,
                result.new_potential,
                1 if age_players else 0,
                1 if age_players else 0,
                result.context.player_id,
            )
            for result in results
        ],
    )
    copy_role_assignments(con, from_season, to_season)
    role_updates = apply_new_game_variance.recalculate_role_scores(con, season=to_season, source=source)
    scheme_result = scheme_fits.seed_all(con, season=to_season, dry_run=False)
    con.execute(
        """
        UPDATE player_progression_runs
        SET role_score_updates = ?,
            scheme_fit_rows = ?
        WHERE run_id = ?
        """,
        (role_updates, scheme_result["player_fits"], run_id),
    )
    coach_note_alerts = create_progression_coach_notes(
        con,
        game_id=game_id,
        run_id=run_id,
        from_season=from_season,
        to_season=to_season,
        seed=seed,
        results=list(result_by_player.values()),
    )
    return {
        "run_id": run_id,
        "results": list(result_by_player.values()),
        "rating_details": details,
        "role_score_updates": role_updates,
        "scheme_fit_rows": scheme_result["player_fits"],
        "hidden_modifier_rows": hidden_rows,
        "hidden_foundation_rows": hidden_foundation_rows,
        "coach_note_alerts": coach_note_alerts,
    }


def print_run_summary(result: dict[str, Any], *, game_id: str, from_season: int, to_season: int, seed: int, dry_run: bool) -> None:
    results: list[PlayerResult] = result["results"]
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print(f"Game ID: {game_id}")
    print(f"Seasons: {from_season}->{to_season}")
    print(f"Seed: {seed}")
    print(f"Players: {len(results)}")
    print(f"Rating rows: {len(result['rating_details'])}")
    print(f"Role scores recalculated: {result['role_score_updates'] if not dry_run else '(dry run)'}")
    print(f"Scheme fit rows: {result['scheme_fit_rows'] if not dry_run else '(dry run)'}")
    print(f"Hidden modifier rows created/rolled: {result['hidden_modifier_rows'] if not dry_run else '(dry run)'}")
    if not dry_run and result.get("hidden_foundation_rows"):
        print(f"Missing foundation rows seeded: {result['hidden_foundation_rows']}")
    if result["run_id"]:
        print(f"Progression run id: {result['run_id']}")
    if not dry_run and result.get("coach_note_alerts"):
        print(f"Coach note inbox alerts: {result['coach_note_alerts']}")
    if not results:
        return
    avg_overall_delta = sum(item.overall_delta for item in results) / len(results)
    avg_potential_delta = sum(item.potential_delta for item in results) / len(results)
    print(f"Average overall delta: {avg_overall_delta:+.2f}")
    print(f"Average potential delta: {avg_potential_delta:+.2f}")
    print()
    print("Largest gains:")
    for item in sorted(results, key=lambda value: (value.overall_delta, value.context.base_delta), reverse=True)[:10]:
        print(
            f"  {item.context.team:>3} {item.context.name:<24} {item.context.position:<4} "
            f"OVR {item.old_overall}->{item.new_overall} ({item.overall_delta:+d}) "
            f"POT {item.old_potential}->{item.new_potential} | {item.context.notes}"
        )
    print()
    print("Largest drops:")
    for item in sorted(results, key=lambda value: (value.overall_delta, value.context.base_delta))[:10]:
        print(
            f"  {item.context.team:>3} {item.context.name:<24} {item.context.position:<4} "
            f"OVR {item.old_overall}->{item.new_overall} ({item.overall_delta:+d}) "
            f"POT {item.old_potential}->{item.new_potential} | {item.context.notes}"
        )


def action_run(args: argparse.Namespace) -> None:
    seed = args.seed if args.seed is not None else secrets.randbits(63)
    to_season = args.to_season or args.from_season + 1
    game_id = args.game_id
    with connect(args.db) as con:
        if game_id == "active":
            game_id = active_game_id(con)
        result = apply_progression(
            con,
            game_id=game_id,
            from_season=args.from_season,
            to_season=to_season,
            seed=seed,
            age_players=not args.no_age_players,
            roll_modifiers=not args.no_roll_hidden_modifiers,
            notes=args.notes,
            force=args.force,
            dry_run=not args.apply,
        )
        if args.apply:
            con.commit()
    print_run_summary(
        result,
        game_id=game_id,
        from_season=args.from_season,
        to_season=to_season,
        seed=seed,
        dry_run=not args.apply,
    )


def action_summary(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_schema(con)
        game_id = active_game_id(con) if args.game_id == "active" else args.game_id
        rows = con.execute(
            """
            SELECT *
            FROM player_progression_runs
            WHERE game_id = ?
            ORDER BY run_id DESC
            LIMIT ?
            """,
            (game_id, args.limit),
        ).fetchall()
    if not rows:
        print("No progression runs found.")
        return
    for row in rows:
        print(
            f"run {row['run_id']}: {row['from_season']}->{row['to_season']} "
            f"players {row['player_count']} ratings {row['rating_row_count']} "
            f"overall changes {row['overall_changed_count']} potential changes {row['potential_changed_count']} "
            f"created {row['created_at']}"
        )


def action_show(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_schema(con)
        game_id = active_game_id(con) if args.game_id == "active" else args.game_id
        run_id = args.run_id
        if run_id is None:
            row = con.execute(
                """
                SELECT run_id
                FROM player_progression_runs
                WHERE game_id = ?
                ORDER BY run_id DESC
                LIMIT 1
                """,
                (game_id,),
            ).fetchone()
            run_id = int(row["run_id"]) if row else None
        if run_id is None:
            print("No progression run found.")
            return
        filters = ["run_id = ?"]
        params: list[Any] = [run_id]
        if args.team:
            filters.append("team = ?")
            params.append(args.team.upper())
        if args.player:
            filters.append("lower(player_name) LIKE ?")
            params.append(f"%{args.player.lower()}%")
        rows = con.execute(
            f"""
            SELECT *
            FROM player_progression_results_view
            WHERE {' AND '.join(filters)}
            ORDER BY ABS(overall_delta) DESC, player_name
            LIMIT ?
            """,
            (*params, args.limit),
        ).fetchall()
    if not rows:
        print("No progression results found.")
        return
    for row in rows:
        print(
            f"{row['team']:<3} {row['player_name']:<24} {row['position']:<4} "
            f"OVR {row['old_overall']:>2}->{row['new_overall']:>2} ({row['overall_delta']:+d}) "
            f"POT {row['old_potential']:>2}->{row['new_potential']:>2} ({row['potential_change']:+d}) "
            f"dev {row['development_score']:+.1f} scheme {row['scheme_score']:+.1f} "
            f"coach {row['coaching_score']:+.1f} usage {row['usage_score']:+.1f}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run season-to-season progression/regression.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Dry-run or apply progression.")
    run_parser.add_argument("--game-id", default="active")
    run_parser.add_argument("--from-season", type=int, default=DEFAULT_FROM_SEASON)
    run_parser.add_argument("--to-season", type=int)
    run_parser.add_argument("--seed", type=int)
    run_parser.add_argument("--notes", default="Season progression and regression.")
    run_parser.add_argument("--force", action="store_true")
    run_parser.add_argument("--no-age-players", action="store_true")
    run_parser.add_argument("--no-roll-hidden-modifiers", action="store_true")
    run_parser.add_argument("--apply", action="store_true", help="Persist changes. Omit for dry run.")
    run_parser.set_defaults(func=action_run)

    summary_parser = subparsers.add_parser("summary", help="List progression runs.")
    summary_parser.add_argument("--game-id", default="active")
    summary_parser.add_argument("--limit", type=int, default=8)
    summary_parser.set_defaults(func=action_summary)

    show_parser = subparsers.add_parser("show", help="Show progression results.")
    show_parser.add_argument("--game-id", default="active")
    show_parser.add_argument("--run-id", type=int)
    show_parser.add_argument("--team")
    show_parser.add_argument("--player")
    show_parser.add_argument("--limit", type=int, default=30)
    show_parser.set_defaults(func=action_show)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
