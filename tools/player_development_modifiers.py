#!/usr/bin/env python3
"""Hidden player development and regression modifiers for NFL GM Sim.

This is a foundation layer, not the full progression engine. New saves seed
hidden -10..10 modifier values that future development/regression processing can
read when deciding how players grow, stall, regress, or change potential.
"""

from __future__ import annotations

import argparse
import random
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_SEASON = 2026
SOURCE = "player_development_modifiers"


@dataclass(frozen=True)
class FactorDefinition:
    factor_key: str
    display_name: str
    category: str
    rookie_weight: float
    young_weight: float
    prime_weight: float
    veteran_weight: float
    late_veteran_weight: float
    description: str


FACTORS = [
    FactorDefinition(
        "playing_time_response",
        "Playing Time Response",
        "opportunity",
        1.20,
        1.12,
        0.82,
        0.54,
        0.34,
        "How strongly actual snaps and role size accelerate or stall development.",
    ),
    FactorDefinition(
        "scheme_fit_response",
        "Scheme Fit Response",
        "fit",
        1.00,
        1.08,
        1.00,
        0.88,
        0.74,
        "How much being in the right football role matters for growth and confidence.",
    ),
    FactorDefinition(
        "mentor_response",
        "Mentor Response",
        "environment",
        1.24,
        1.05,
        0.46,
        0.18,
        0.08,
        "How well a younger player benefits from a veteran mentor ahead of him.",
    ),
    FactorDefinition(
        "coaching_response",
        "Coaching Response",
        "environment",
        1.12,
        1.08,
        0.96,
        0.78,
        0.62,
        "How strongly position-group coaching quality changes outcomes.",
    ),
    FactorDefinition(
        "practice_habits",
        "Practice Habits",
        "work_ethic",
        1.10,
        1.05,
        0.92,
        0.82,
        0.70,
        "Hidden day-to-day work habits that help a player reach tools faster.",
    ),
    FactorDefinition(
        "practice_squad_response",
        "Practice Squad Response",
        "opportunity",
        1.32,
        1.18,
        0.84,
        0.58,
        0.36,
        "How a player develops when stashed on the practice squad instead of playing real snaps.",
    ),
    FactorDefinition(
        "football_iq_growth",
        "Football IQ Growth",
        "mental",
        1.18,
        1.08,
        0.82,
        0.58,
        0.40,
        "How quickly processing, recognition, and assignment detail improve.",
    ),
    FactorDefinition(
        "confidence_response",
        "Confidence Response",
        "mental",
        1.06,
        1.08,
        0.96,
        0.78,
        0.62,
        "Whether success, failure, and role pressure create momentum or drag.",
    ),
    FactorDefinition(
        "adversity_response",
        "Adversity Response",
        "mental",
        1.00,
        1.04,
        1.00,
        0.92,
        0.82,
        "How well a player rebounds from benching, bad years, injury, or criticism.",
    ),
    FactorDefinition(
        "injury_recovery_response",
        "Injury Recovery Response",
        "health",
        0.92,
        0.98,
        1.05,
        1.14,
        1.24,
        "How much injuries slow development or accelerate decline once injuries exist.",
    ),
    FactorDefinition(
        "role_stability_response",
        "Role Stability Response",
        "fit",
        0.82,
        0.96,
        1.06,
        1.08,
        0.94,
        "Whether a player needs a stable role or can thrive amid usage changes.",
    ),
    FactorDefinition(
        "position_change_response",
        "Position Change Response",
        "adaptability",
        0.98,
        1.08,
        0.90,
        0.66,
        0.42,
        "How well a player handles cross-training or a true position switch.",
    ),
    FactorDefinition(
        "competition_response",
        "Competition Response",
        "opportunity",
        1.08,
        1.10,
        0.96,
        0.76,
        0.56,
        "How a player reacts to crowded depth charts and starting-job battles.",
    ),
    FactorDefinition(
        "pressure_environment",
        "Pressure Environment",
        "mental",
        0.94,
        1.00,
        1.04,
        0.96,
        0.82,
        "How media, expectations, playoff pushes, and fan pressure affect trajectory.",
    ),
    FactorDefinition(
        "team_success_response",
        "Team Success Response",
        "environment",
        0.86,
        0.98,
        1.08,
        1.10,
        0.92,
        "How much team winning, losing, culture stability, and playoff contention affect development.",
    ),
    FactorDefinition(
        "leadership_room_response",
        "Leadership Room Response",
        "environment",
        0.90,
        1.00,
        1.08,
        1.12,
        1.00,
        "How much the player benefits from or contributes to room standards.",
    ),
    FactorDefinition(
        "potential_volatility",
        "Potential Volatility",
        "ceiling",
        1.30,
        1.18,
        0.78,
        0.42,
        0.22,
        "How likely potential is to move meaningfully instead of staying fixed.",
    ),
    FactorDefinition(
        "regression_resistance",
        "Regression Resistance",
        "aging",
        0.30,
        0.48,
        0.84,
        1.26,
        1.38,
        "How much routine, body maintenance, and skill detail resist decline.",
    ),
    FactorDefinition(
        "late_bloomer_tendency",
        "Late Bloomer Tendency",
        "ceiling",
        1.04,
        1.16,
        0.90,
        0.54,
        0.28,
        "Chance that improvement arrives later than scouting or early career suggests.",
    ),
    FactorDefinition(
        "decline_acceleration_risk",
        "Decline Acceleration Risk",
        "aging",
        0.18,
        0.28,
        0.72,
        1.18,
        1.42,
        "Risk that erosion comes faster once age, injuries, or lost role show up.",
    ),
]


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


def row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def clamp_int(value: float, low: int, high: int) -> int:
    return max(low, min(high, int(round(value))))


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS development_factor_definitions (
            factor_key TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            category TEXT NOT NULL,
            min_value INTEGER NOT NULL DEFAULT -10,
            max_value INTEGER NOT NULL DEFAULT 10,
            rookie_weight REAL NOT NULL DEFAULT 1,
            young_weight REAL NOT NULL DEFAULT 1,
            prime_weight REAL NOT NULL DEFAULT 1,
            veteran_weight REAL NOT NULL DEFAULT 1,
            late_veteran_weight REAL NOT NULL DEFAULT 1,
            description TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS new_game_development_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            rng_seed INTEGER NOT NULL,
            player_count INTEGER NOT NULL DEFAULT 0,
            factor_count INTEGER NOT NULL DEFAULT 0,
            modifier_count INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(game_id, season)
        );

        CREATE TABLE IF NOT EXISTS player_development_profiles (
            game_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            age_band TEXT NOT NULL,
            development_bias INTEGER NOT NULL DEFAULT 0,
            potential_volatility INTEGER NOT NULL DEFAULT 0,
            regression_resistance INTEGER NOT NULL DEFAULT 0,
            late_bloomer_chance INTEGER NOT NULL DEFAULT 0,
            decline_risk INTEGER NOT NULL DEFAULT 0,
            hidden INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(game_id, season, player_id)
        );

        CREATE TABLE IF NOT EXISTS player_development_modifiers (
            game_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            factor_key TEXT NOT NULL REFERENCES development_factor_definitions(factor_key) ON DELETE CASCADE,
            modifier_value INTEGER NOT NULL,
            age_band TEXT NOT NULL,
            hidden INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(game_id, season, player_id, factor_key)
        );

        CREATE INDEX IF NOT EXISTS idx_player_development_modifiers_player
            ON player_development_modifiers(player_id, game_id, season);

        CREATE INDEX IF NOT EXISTS idx_player_development_modifiers_factor
            ON player_development_modifiers(game_id, season, factor_key);

        DROP VIEW IF EXISTS player_development_modifiers_view;
        CREATE VIEW player_development_modifiers_view AS
        SELECT
            pdm.game_id,
            pdm.season,
            pdm.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.years_exp,
            COALESCE(t.abbreviation, 'FA') AS team,
            pdm.age_band,
            pdm.factor_key,
            dfd.display_name,
            dfd.category,
            pdm.modifier_value,
            pdm.hidden,
            pdm.source,
            pdm.notes,
            pdm.created_at,
            pdm.updated_at
        FROM player_development_modifiers pdm
        JOIN players p ON p.player_id = pdm.player_id
        LEFT JOIN teams t ON t.team_id = p.team_id
        JOIN development_factor_definitions dfd ON dfd.factor_key = pdm.factor_key;

        DROP VIEW IF EXISTS player_development_profiles_view;
        CREATE VIEW player_development_profiles_view AS
        SELECT
            pdp.game_id,
            pdp.season,
            pdp.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.years_exp,
            COALESCE(t.abbreviation, 'FA') AS team,
            pdp.age_band,
            pdp.development_bias,
            pdp.potential_volatility,
            pdp.regression_resistance,
            pdp.late_bloomer_chance,
            pdp.decline_risk,
            pdp.hidden,
            pdp.source,
            pdp.notes,
            pdp.created_at,
            pdp.updated_at
        FROM player_development_profiles pdp
        JOIN players p ON p.player_id = pdp.player_id
        LEFT JOIN teams t ON t.team_id = p.team_id;
        """
    )


def seed_factor_definitions(con: sqlite3.Connection) -> None:
    con.executemany(
        """
        INSERT INTO development_factor_definitions (
            factor_key, display_name, category, min_value, max_value,
            rookie_weight, young_weight, prime_weight, veteran_weight,
            late_veteran_weight, description, updated_at
        )
        VALUES (?, ?, ?, -10, 10, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(factor_key) DO UPDATE SET
            display_name = excluded.display_name,
            category = excluded.category,
            rookie_weight = excluded.rookie_weight,
            young_weight = excluded.young_weight,
            prime_weight = excluded.prime_weight,
            veteran_weight = excluded.veteran_weight,
            late_veteran_weight = excluded.late_veteran_weight,
            description = excluded.description,
            updated_at = datetime('now')
        """,
        [
            (
                factor.factor_key,
                factor.display_name,
                factor.category,
                factor.rookie_weight,
                factor.young_weight,
                factor.prime_weight,
                factor.veteran_weight,
                factor.late_veteran_weight,
                factor.description,
            )
            for factor in FACTORS
        ],
    )


def seed_master_data(con: sqlite3.Connection) -> None:
    ensure_schema(con)
    seed_factor_definitions(con)


def load_players(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            COALESCE(t.abbreviation, 'FA') AS team,
            p.team_id,
            p.age,
            p.years_exp,
            COALESCE(p.is_rookie, 0) AS is_rookie,
            p.overall,
            p.potential,
            p.status
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        ORDER BY p.player_id
        """
    ).fetchall()


def load_players_by_ids(con: sqlite3.Connection, player_ids: list[int] | tuple[int, ...] | set[int]) -> list[sqlite3.Row]:
    ids = sorted({int(player_id) for player_id in player_ids})
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return con.execute(
        f"""
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            COALESCE(t.abbreviation, 'FA') AS team,
            p.team_id,
            p.age,
            p.years_exp,
            COALESCE(p.is_rookie, 0) AS is_rookie,
            p.overall,
            p.potential,
            p.status
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        WHERE p.player_id IN ({placeholders})
        ORDER BY p.player_id
        """,
        ids,
    ).fetchall()


def load_personality_traits(con: sqlite3.Connection, *, game_id: str, season: int) -> dict[int, dict[str, int]]:
    if not table_exists(con, "player_personalities"):
        return {}
    rows = con.execute(
        """
        SELECT player_id, trait_key, intensity
        FROM player_personalities
        WHERE game_id = ? AND season = ?
        """,
        (game_id, season),
    ).fetchall()
    traits: dict[int, dict[str, int]] = {}
    for row in rows:
        traits.setdefault(int(row["player_id"]), {})[str(row["trait_key"])] = int(row["intensity"] or 0)
    return traits


def age_band(player: sqlite3.Row) -> str:
    age = int(row_value(player, "age", 26) or 26)
    exp = int(row_value(player, "years_exp", 0) or 0)
    is_rookie = int(row_value(player, "is_rookie", 0) or 0) == 1 or exp == 0
    if is_rookie or (age <= 23 and exp <= 1):
        return "rookie"
    if age <= 25 or exp <= 3:
        return "young"
    if age <= 29:
        return "prime"
    if age <= 33:
        return "veteran"
    return "late_veteran"


def age_weight(factor: sqlite3.Row, band: str) -> float:
    key = {
        "rookie": "rookie_weight",
        "young": "young_weight",
        "prime": "prime_weight",
        "veteran": "veteran_weight",
        "late_veteran": "late_veteran_weight",
    }.get(band, "prime_weight")
    return float(factor[key] or 1.0)


def trait_bonus(factor_key: str, traits: dict[str, int]) -> float:
    def strength(key: str) -> float:
        return max(0.0, min(1.0, traits.get(key, 0) / 100.0))

    bonus = 0.0
    if factor_key in {"practice_habits", "coaching_response", "football_iq_growth"}:
        bonus += 3.2 * strength("lunch_pail")
        bonus += 2.6 * strength("film_junkie")
        bonus += 1.4 * strength("quiet_professional")
    if factor_key == "practice_squad_response":
        bonus += 2.2 * strength("lunch_pail")
        bonus += 1.7 * strength("film_junkie")
        bonus += 1.1 * strength("quiet_professional")
        bonus += 0.8 * strength("mentor")
        bonus -= 1.8 * strength("locker_room_distraction")
        bonus -= 1.2 * strength("off_field_issue")
    if factor_key in {"mentor_response", "leadership_room_response"}:
        bonus += 2.8 * strength("mentor")
        bonus += 1.8 * strength("natural_leader")
    if factor_key in {"confidence_response", "pressure_environment"}:
        bonus += 2.4 * strength("big_stage")
        bonus += 1.2 * strength("media_savvy")
        bonus -= 1.6 * strength("streaky_confidence")
    if factor_key == "team_success_response":
        bonus += 2.2 * strength("natural_leader")
        bonus += 1.8 * strength("big_stage")
        bonus += 1.4 * strength("coach_connector")
        bonus += 1.2 * strength("quiet_professional")
        bonus += 1.1 * strength("ring_chaser")
        bonus -= 1.8 * strength("locker_room_distraction")
        bonus -= 1.0 * strength("streaky_confidence")
    if factor_key in {"adversity_response", "competition_response"}:
        bonus += 2.2 * strength("chip_on_shoulder")
        bonus += 1.2 * strength("lunch_pail")
    if factor_key == "regression_resistance":
        bonus += 2.0 * strength("lunch_pail")
        bonus += 1.2 * strength("quiet_professional")
    if factor_key == "decline_acceleration_risk":
        bonus -= 1.4 * strength("lunch_pail")
        bonus += 1.6 * strength("off_field_issue")
    if factor_key == "potential_volatility":
        bonus += 2.0 * strength("streaky_confidence")
        bonus += 1.4 * strength("chip_on_shoulder")
    if factor_key == "scheme_fit_response":
        bonus += 1.4 * strength("film_junkie")
        bonus += 1.1 * strength("coach_connector")
    if factor_key == "playing_time_response":
        bonus += 1.6 * strength("chip_on_shoulder")
        bonus -= 0.8 * strength("locker_room_distraction")
    return bonus


def factor_value(
    rng: random.Random,
    *,
    factor: sqlite3.Row,
    player: sqlite3.Row,
    traits: dict[str, int],
) -> int:
    band = age_band(player)
    if str(factor["factor_key"]) == "practice_squad_response":
        return practice_squad_factor_value(rng, band=band, traits=traits)
    weight = age_weight(factor, band)
    sigma = max(1.8, 3.2 * weight)
    value = rng.gauss(0.0, sigma) + trait_bonus(str(factor["factor_key"]), traits)
    overall = int(row_value(player, "overall", 60) or 60)
    potential = int(row_value(player, "potential", overall) or overall)
    if str(factor["factor_key"]) in {"late_bloomer_tendency", "potential_volatility"} and potential - overall >= 10:
        value += 0.8
    if str(factor["factor_key"]) == "decline_acceleration_risk" and band in {"veteran", "late_veteran"}:
        value += 0.8
    return clamp_int(value, -10, 10)


def practice_squad_factor_value(
    rng: random.Random,
    *,
    band: str,
    traits: dict[str, int],
) -> int:
    """Skew practice-squad development negative, with a real positive tail.

    Most players lose momentum without game reps, especially older players. A
    smaller group can benefit a lot from protected reps, coaching time, and a
    lower-pressure runway.
    """
    negative_rate_by_band = {
        "rookie": 0.58,
        "young": 0.61,
        "prime": 0.69,
        "veteran": 0.76,
        "late_veteran": 0.82,
    }
    positive_rate_by_band = {
        "rookie": 0.24,
        "young": 0.22,
        "prime": 0.14,
        "veteran": 0.09,
        "late_veteran": 0.05,
    }
    negative_rate = negative_rate_by_band.get(band, 0.66)
    positive_rate = positive_rate_by_band.get(band, 0.15)
    roll = rng.random()
    if roll < negative_rate:
        value = rng.gauss(-4.8 if band in {"veteran", "late_veteran"} else -3.8, 2.0)
    elif roll < negative_rate + positive_rate:
        value = rng.gauss(5.8 if band in {"rookie", "young"} else 4.6, 2.5)
    else:
        value = rng.gauss(0.0, 1.3)

    value += trait_bonus("practice_squad_response", traits)
    return clamp_int(value, -10, 10)


def profile_from_modifiers(modifiers: dict[str, int], band: str) -> tuple[int, int, int, int, int, str]:
    development_bias = clamp_int(
        (
            modifiers.get("playing_time_response", 0)
            + modifiers.get("scheme_fit_response", 0)
            + modifiers.get("coaching_response", 0)
            + modifiers.get("practice_habits", 0)
            + modifiers.get("football_iq_growth", 0)
            + modifiers.get("confidence_response", 0)
            + modifiers.get("competition_response", 0)
        )
        / 7,
        -10,
        10,
    )
    potential_volatility = modifiers.get("potential_volatility", 0)
    regression_resistance = clamp_int(
        (
            modifiers.get("regression_resistance", 0)
            + modifiers.get("injury_recovery_response", 0)
            - modifiers.get("decline_acceleration_risk", 0)
        )
        / 3,
        -10,
        10,
    )
    late_bloomer_chance = clamp_int(
        (
            modifiers.get("late_bloomer_tendency", 0)
            + modifiers.get("adversity_response", 0)
            + modifiers.get("coaching_response", 0)
        )
        / 3,
        -10,
        10,
    )
    decline_risk = clamp_int(
        modifiers.get("decline_acceleration_risk", 0)
        - modifiers.get("regression_resistance", 0) * 0.55
        - modifiers.get("injury_recovery_response", 0) * 0.35,
        -10,
        10,
    )
    notes = (
        f"{band}; dev {development_bias:+d}, volatility {potential_volatility:+d}, "
        f"regression resistance {regression_resistance:+d}, decline risk {decline_risk:+d}."
    )
    return development_bias, potential_volatility, regression_resistance, late_bloomer_chance, decline_risk, notes


def check_run_available(con: sqlite3.Connection, game_id: str, season: int, *, force: bool) -> None:
    row = con.execute(
        """
        SELECT run_id
        FROM new_game_development_runs
        WHERE game_id = ? AND season = ?
        """,
        (game_id, season),
    ).fetchone()
    if not row:
        return
    if not force:
        raise ValueError(
            f"Development modifiers already seeded for {game_id} {season} "
            f"(run_id={row['run_id']}). Use --force to refresh."
        )
    con.execute("DELETE FROM player_development_modifiers WHERE game_id = ? AND season = ?", (game_id, season))
    con.execute("DELETE FROM player_development_profiles WHERE game_id = ? AND season = ?", (game_id, season))
    con.execute("DELETE FROM new_game_development_runs WHERE game_id = ? AND season = ?", (game_id, season))


def apply_development_modifiers(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    seed: int,
    notes: str | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, int]:
    seed_master_data(con)
    check_run_available(con, game_id, season, force=force)
    rng = random.Random(seed)
    players = load_players(con)
    factors = con.execute("SELECT * FROM development_factor_definitions ORDER BY factor_key").fetchall()
    traits_by_player = load_personality_traits(con, game_id=game_id, season=season)

    modifier_rows = []
    profile_rows = []
    for player in players:
        player_id = int(player["player_id"])
        band = age_band(player)
        traits = traits_by_player.get(player_id, {})
        values: dict[str, int] = {}
        for factor in factors:
            value = factor_value(rng, factor=factor, player=player, traits=traits)
            values[str(factor["factor_key"])] = value
            modifier_rows.append(
                (
                    game_id,
                    season,
                    player_id,
                    factor["factor_key"],
                    value,
                    band,
                    f"new_game_development:{game_id}",
                    f"Seeded hidden {factor['display_name']} modifier for {band} player.",
                )
            )
        profile = profile_from_modifiers(values, band)
        profile_rows.append(
            (
                game_id,
                season,
                player_id,
                band,
                profile[0],
                profile[1],
                profile[2],
                profile[3],
                profile[4],
                f"new_game_development:{game_id}",
                profile[5],
            )
        )

    if dry_run:
        return {
            "players": len(players),
            "factors": len(factors),
            "modifiers": len(modifier_rows),
            "profiles": len(profile_rows),
            "run_id": 0,
        }

    cur = con.execute(
        """
        INSERT INTO new_game_development_runs (
            game_id, season, rng_seed, player_count, factor_count,
            modifier_count, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (game_id, season, seed, len(players), len(factors), len(modifier_rows), notes),
    )
    run_id = int(cur.lastrowid)
    con.executemany(
        """
        INSERT INTO player_development_modifiers (
            game_id, season, player_id, factor_key, modifier_value,
            age_band, hidden, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, datetime('now'))
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
        """,
        profile_rows,
    )
    return {
        "players": len(players),
        "factors": len(factors),
        "modifiers": len(modifier_rows),
        "profiles": len(profile_rows),
        "run_id": run_id,
    }


def seed_development_for_players(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    player_ids: list[int] | tuple[int, ...] | set[int],
    seed: int | str | None = None,
    source: str = SOURCE,
    notes: str | None = None,
) -> dict[str, int]:
    """Seed hidden development rows for players added after new-game setup.

    Drafted rookies and post-draft UDFAs are created after the original new-save
    pass, so they need a supplemental path that uses the same factor math without
    refreshing the whole league.
    """
    seed_master_data(con)
    players = load_players_by_ids(con, player_ids)
    factors = con.execute("SELECT * FROM development_factor_definitions ORDER BY factor_key").fetchall()
    traits_by_player = load_personality_traits(con, game_id=game_id, season=season)
    base_seed = seed if seed is not None else f"{game_id}:{season}:supplemental_development"

    modifier_rows = []
    profile_rows = []
    for player in players:
        player_id = int(player["player_id"])
        band = age_band(player)
        traits = traits_by_player.get(player_id, {})
        rng = random.Random(f"{base_seed}:{player_id}")
        values: dict[str, int] = {}
        for factor in factors:
            key = str(factor["factor_key"])
            value = factor_value(rng, factor=factor, player=player, traits=traits)
            values[key] = value
            modifier_rows.append(
                (
                    game_id,
                    season,
                    player_id,
                    key,
                    value,
                    band,
                    source,
                    notes or f"Supplemental hidden {factor['display_name']} modifier for {band} player.",
                )
            )
        profile = profile_from_modifiers(values, band)
        profile_rows.append(
            (
                game_id,
                season,
                player_id,
                band,
                profile[0],
                profile[1],
                profile[2],
                profile[3],
                profile[4],
                source,
                profile[5],
            )
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
    return {
        "players": len(players),
        "factors": len(factors),
        "modifiers": len(modifier_rows),
        "profiles": len(profile_rows),
    }


def print_apply_summary(result: dict[str, int], *, game_id: str, season: int, seed: int, dry_run: bool) -> None:
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print(f"Game ID: {game_id}")
    print(f"Season: {season}")
    print(f"Seed: {seed}")
    print(f"Players considered: {result['players']}")
    print(f"Factors per player: {result['factors']}")
    print(f"Hidden modifier rows: {result['modifiers']}")
    print(f"Development profiles: {result['profiles']}")
    if result["run_id"]:
        print(f"Development run id: {result['run_id']}")


def action_setup(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        seed_master_data(con)
        con.commit()
    print(f"Development factor definitions: {len(FACTORS)}")


def action_apply(args: argparse.Namespace) -> None:
    seed = args.seed if args.seed is not None else secrets.randbits(63)
    with connect(args.db) as con:
        result = apply_development_modifiers(
            con,
            game_id=args.game_id,
            season=args.season,
            seed=seed,
            notes=args.notes,
            dry_run=not args.apply,
            force=args.force,
        )
        if args.apply:
            con.commit()
    print_apply_summary(result, game_id=args.game_id, season=args.season, seed=seed, dry_run=not args.apply)


def action_summary(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_schema(con)
        rows = con.execute(
            """
            SELECT age_band,
                   COUNT(*) AS players,
                   ROUND(AVG(development_bias), 2) AS avg_dev,
                   ROUND(AVG(potential_volatility), 2) AS avg_volatility,
                   ROUND(AVG(regression_resistance), 2) AS avg_regression_resistance,
                   ROUND(AVG(decline_risk), 2) AS avg_decline_risk
            FROM player_development_profiles
            WHERE game_id = ? AND season = ?
            GROUP BY age_band
            ORDER BY CASE age_band
                WHEN 'rookie' THEN 1
                WHEN 'young' THEN 2
                WHEN 'prime' THEN 3
                WHEN 'veteran' THEN 4
                ELSE 5
            END
            """,
            (args.game_id, args.season),
        ).fetchall()
    if not rows:
        print("No development profiles found.")
        return
    for row in rows:
        print(
            f"{row['age_band']:<13} players {row['players']:>4} | "
            f"dev {row['avg_dev']:>5} | volatility {row['avg_volatility']:>5} | "
            f"reg resist {row['avg_regression_resistance']:>5} | decline {row['avg_decline_risk']:>5}"
        )


def action_show(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_schema(con)
        filters = ["game_id = ?", "season = ?"]
        params: list[Any] = [args.game_id, args.season]
        if args.player:
            filters.append("lower(player_name) LIKE ?")
            params.append(f"%{args.player.lower()}%")
        if args.team:
            filters.append("team = ?")
            params.append(args.team.upper())
        rows = con.execute(
            f"""
            SELECT *
            FROM player_development_profiles_view
            WHERE {' AND '.join(filters)}
            ORDER BY team, player_name
            LIMIT ?
            """,
            (*params, args.limit),
        ).fetchall()
        modifier_rows = []
        if rows:
            player_ids = [int(row["player_id"]) for row in rows]
            placeholders = ",".join("?" for _ in player_ids)
            modifier_rows = con.execute(
                f"""
                SELECT *
                FROM player_development_modifiers_view
                WHERE game_id = ?
                  AND season = ?
                  AND player_id IN ({placeholders})
                ORDER BY player_name, category, factor_key
                """,
                (args.game_id, args.season, *player_ids),
            ).fetchall()
    if not rows:
        print("No development profiles found.")
        return
    modifiers_by_player: dict[int, list[sqlite3.Row]] = {}
    for row in modifier_rows:
        modifiers_by_player.setdefault(int(row["player_id"]), []).append(row)
    for row in rows:
        print(
            f"{row['team']:<3} {row['player_name']:<24} {row['position']:<4} {row['age_band']:<12} "
            f"dev {row['development_bias']:+3d} vol {row['potential_volatility']:+3d} "
            f"reg {row['regression_resistance']:+3d} decline {row['decline_risk']:+3d}"
        )
        for modifier in modifiers_by_player.get(int(row["player_id"]), [])[: args.factor_limit]:
            print(f"    {modifier['display_name']:<28} {modifier['modifier_value']:+3d}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed and inspect hidden player development modifiers.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Create development modifier tables and seed factor definitions.")
    setup_parser.set_defaults(func=action_setup)

    apply_parser = subparsers.add_parser("apply", help="Apply hidden development modifiers for one new save.")
    apply_parser.add_argument("--game-id", required=True)
    apply_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    apply_parser.add_argument("--seed", type=int)
    apply_parser.add_argument("--notes", default="New game start development modifier variance.")
    apply_parser.add_argument("--force", action="store_true", help="Refresh existing development modifiers for this save.")
    apply_parser.add_argument("--apply", action="store_true", help="Persist the modifiers. Omit for dry run.")
    apply_parser.set_defaults(func=action_apply)

    summary_parser = subparsers.add_parser("summary", help="Summarize development modifiers for one save.")
    summary_parser.add_argument("--game-id", required=True)
    summary_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    summary_parser.set_defaults(func=action_summary)

    show_parser = subparsers.add_parser("show", help="Show hidden development modifiers for debugging.")
    show_parser.add_argument("--game-id", required=True)
    show_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    show_parser.add_argument("--player")
    show_parser.add_argument("--team")
    show_parser.add_argument("--limit", type=int, default=20)
    show_parser.add_argument("--factor-limit", type=int, default=8)
    show_parser.set_defaults(func=action_show)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
