#!/usr/bin/env python3
"""Hidden player personality traits for NFL GM Sim.

These traits are simulation flavor. Baseline traits are positive/neutral public-role
style suggestions, while sensitive or negative traits are generated only inside a
save so the database does not pretend to know real private character.
"""

from __future__ import annotations

import argparse
import random
import re
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_SEASON = 2026
DEFAULT_BASELINE_OMIT_CHANCE = 0.15
DEFAULT_RANDOM_TRAIT_CAP = 3
SOURCE = "player_personalities"


@dataclass(frozen=True)
class TraitDefinition:
    trait_key: str
    display_name: str
    category: str
    polarity: str
    random_base_rate: float
    min_intensity: int
    max_intensity: int
    sensitive: int
    description: str


TRAITS = [
    TraitDefinition(
        "natural_leader",
        "Natural Leader",
        "leadership",
        "positive",
        0.020,
        62,
        95,
        0,
        "Pulls teammates toward standards, preparation, and accountability.",
    ),
    TraitDefinition(
        "locker_room_distraction",
        "Locker Room Distraction",
        "chemistry",
        "negative",
        0.006,
        45,
        85,
        1,
        "Creates occasional chemistry management problems in this save universe.",
    ),
    TraitDefinition(
        "off_field_issue",
        "Off Field Issue",
        "risk",
        "negative",
        0.004,
        35,
        90,
        1,
        "Hidden save-only risk for a suspension, legal event, gambling issue, or accident storyline.",
    ),
    TraitDefinition(
        "greedy",
        "Greedy",
        "contract",
        "negative",
        0.014,
        52,
        92,
        1,
        "Prioritizes maximum money and guarantees over fit, loyalty, or ring chasing.",
    ),
    TraitDefinition(
        "lunch_pail",
        "Lunch Pail Guy",
        "work_ethic",
        "positive",
        0.048,
        58,
        96,
        0,
        "High-effort, low-maintenance worker who keeps grinding past normal NFL expectations.",
    ),
    TraitDefinition(
        "jokester",
        "Jokester",
        "chemistry",
        "neutral",
        0.026,
        45,
        88,
        0,
        "Keeps the room loose and gives the team more personality.",
    ),
    TraitDefinition(
        "mentor",
        "Mentor",
        "leadership",
        "positive",
        0.021,
        55,
        92,
        0,
        "Invests in younger players and helps position rooms stabilize.",
    ),
    TraitDefinition(
        "film_junkie",
        "Film Junkie",
        "preparation",
        "positive",
        0.030,
        58,
        94,
        0,
        "Obsessive prep habits and strong opponent-study routine.",
    ),
    TraitDefinition(
        "big_stage",
        "Big Stage Performer",
        "competitive",
        "positive",
        0.016,
        56,
        96,
        0,
        "Tends to embrace leverage, spotlight, and high-pressure situations.",
    ),
    TraitDefinition(
        "quiet_professional",
        "Quiet Professional",
        "chemistry",
        "positive",
        0.040,
        50,
        88,
        0,
        "Reliable, steady, and drama-free behind the scenes.",
    ),
    TraitDefinition(
        "chip_on_shoulder",
        "Chip On Shoulder",
        "motivation",
        "neutral",
        0.034,
        52,
        92,
        0,
        "Runs hot on slights, draft position, role competition, or public doubt.",
    ),
    TraitDefinition(
        "media_savvy",
        "Media Savvy",
        "market",
        "positive",
        0.018,
        48,
        90,
        0,
        "Comfortable as a public face of the team and rarely creates accidental headlines.",
    ),
    TraitDefinition(
        "ring_chaser",
        "Ring Chaser",
        "contract",
        "neutral",
        0.010,
        50,
        88,
        0,
        "More willing than most to favor contender fit once money is close.",
    ),
    TraitDefinition(
        "hometown_pull",
        "Hometown Pull",
        "contract",
        "neutral",
        0.014,
        45,
        82,
        0,
        "Can become unusually interested in a hometown, college-region, or family-stability fit.",
    ),
    TraitDefinition(
        "coach_connector",
        "Coach Connector",
        "culture",
        "positive",
        0.018,
        54,
        90,
        0,
        "Builds trust with coaches and helps scheme buy-in spread through the room.",
    ),
    TraitDefinition(
        "streaky_confidence",
        "Streaky Confidence",
        "mental",
        "neutral",
        0.020,
        44,
        86,
        0,
        "Confidence can snowball in either direction across a long season.",
    ),
]


BASELINE_TRAITS = [
    ("Patrick Mahomes", "natural_leader", 94, "Franchise QB baseline leadership flavor."),
    ("Patrick Mahomes", "film_junkie", 86, "Franchise QB baseline preparation flavor."),
    ("Patrick Mahomes", "big_stage", 96, "Franchise QB baseline pressure flavor."),
    ("Josh Allen", "natural_leader", 90, "Franchise QB baseline leadership flavor."),
    ("Josh Allen", "big_stage", 84, "Franchise QB baseline pressure flavor."),
    ("Lamar Jackson", "natural_leader", 88, "Franchise QB baseline leadership flavor."),
    ("Lamar Jackson", "big_stage", 87, "Franchise QB baseline pressure flavor."),
    ("Jalen Hurts", "natural_leader", 90, "Franchise QB baseline leadership flavor."),
    ("Jalen Hurts", "film_junkie", 84, "Franchise QB baseline preparation flavor."),
    ("Joe Burrow", "natural_leader", 88, "Franchise QB baseline leadership flavor."),
    ("Joe Burrow", "film_junkie", 88, "Franchise QB baseline preparation flavor."),
    ("Joe Burrow", "big_stage", 88, "Franchise QB baseline pressure flavor."),
    ("C.J. Stroud", "natural_leader", 82, "Young franchise QB baseline leadership flavor."),
    ("C.J. Stroud", "film_junkie", 82, "Young franchise QB baseline preparation flavor."),
    ("Justin Jefferson", "media_savvy", 82, "Star-skill-player public face flavor."),
    ("Justin Jefferson", "big_stage", 84, "Star-skill-player spotlight flavor."),
    ("Travis Kelce", "natural_leader", 84, "Veteran star baseline leadership flavor."),
    ("Travis Kelce", "jokester", 88, "Public-facing personality flavor."),
    ("Travis Kelce", "media_savvy", 90, "Public-facing personality flavor."),
    ("George Kittle", "natural_leader", 84, "Veteran star baseline leadership flavor."),
    ("George Kittle", "jokester", 90, "Public-facing personality flavor."),
    ("George Kittle", "lunch_pail", 88, "High-effort veteran tight end flavor."),
    ("Fred Warner", "natural_leader", 90, "Defensive captain baseline leadership flavor."),
    ("Fred Warner", "film_junkie", 86, "Defensive captain preparation flavor."),
    ("Fred Warner", "lunch_pail", 86, "Defensive captain work ethic flavor."),
    ("Maxx Crosby", "lunch_pail", 94, "High-motor star defender flavor."),
    ("Maxx Crosby", "natural_leader", 80, "Veteran tone-setter flavor."),
    ("Aidan Hutchinson", "lunch_pail", 88, "High-motor young defender flavor."),
    ("Amon-Ra St. Brown", "lunch_pail", 92, "High-effort receiver flavor."),
    ("Amon-Ra St. Brown", "chip_on_shoulder", 90, "Motivational flavor."),
    ("Puka Nacua", "lunch_pail", 88, "High-effort receiver flavor."),
    ("Puka Nacua", "chip_on_shoulder", 80, "Motivational flavor."),
    ("Saquon Barkley", "natural_leader", 82, "Veteran skill-player leadership flavor."),
    ("Baker Mayfield", "jokester", 78, "Public-facing personality flavor."),
    ("Baker Mayfield", "chip_on_shoulder", 88, "Motivational flavor."),
]


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def normalize_name(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", value.lower()))


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS personality_trait_definitions (
            trait_key TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            category TEXT NOT NULL,
            polarity TEXT NOT NULL,
            random_base_rate REAL NOT NULL DEFAULT 0,
            min_intensity INTEGER NOT NULL DEFAULT 40,
            max_intensity INTEGER NOT NULL DEFAULT 90,
            sensitive INTEGER NOT NULL DEFAULT 0,
            description TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS player_personality_baselines (
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            trait_key TEXT NOT NULL REFERENCES personality_trait_definitions(trait_key) ON DELETE CASCADE,
            baseline_intensity INTEGER NOT NULL,
            omit_chance REAL NOT NULL DEFAULT 0.15,
            source TEXT NOT NULL DEFAULT 'manual_sim_baseline',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(player_id, trait_key)
        );

        CREATE TABLE IF NOT EXISTS new_game_personality_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            rng_seed INTEGER NOT NULL,
            baseline_omit_chance REAL NOT NULL,
            baseline_count INTEGER NOT NULL DEFAULT 0,
            baseline_kept_count INTEGER NOT NULL DEFAULT 0,
            baseline_omitted_count INTEGER NOT NULL DEFAULT 0,
            random_assignment_count INTEGER NOT NULL DEFAULT 0,
            player_count INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(game_id, season)
        );

        CREATE TABLE IF NOT EXISTS player_personalities (
            game_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            trait_key TEXT NOT NULL REFERENCES personality_trait_definitions(trait_key) ON DELETE CASCADE,
            intensity INTEGER NOT NULL,
            assignment_type TEXT NOT NULL,
            hidden INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(game_id, season, player_id, trait_key)
        );

        CREATE INDEX IF NOT EXISTS idx_player_personalities_player
            ON player_personalities(player_id, game_id, season);

        CREATE INDEX IF NOT EXISTS idx_player_personalities_trait
            ON player_personalities(game_id, season, trait_key);

        CREATE TABLE IF NOT EXISTS player_free_agency_preferences (
            game_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            preference_archetype TEXT NOT NULL,
            money_priority INTEGER NOT NULL,
            security_priority INTEGER NOT NULL,
            contender_priority INTEGER NOT NULL,
            role_priority INTEGER NOT NULL,
            loyalty_priority INTEGER NOT NULL,
            location_priority INTEGER NOT NULL,
            contract_year_preference INTEGER NOT NULL,
            market_patience_modifier INTEGER NOT NULL DEFAULT 0,
            hometown_discount_pct REAL NOT NULL DEFAULT 0,
            contender_discount_pct REAL NOT NULL DEFAULT 0,
            minimum_over_ask_pct REAL NOT NULL DEFAULT 0,
            hidden INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(game_id, season, player_id)
        );

        CREATE INDEX IF NOT EXISTS idx_player_fa_preferences_player
            ON player_free_agency_preferences(player_id, game_id, season);

        DROP VIEW IF EXISTS player_personalities_view;
        CREATE VIEW player_personalities_view AS
        SELECT
            pp.game_id,
            pp.season,
            pp.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            COALESCE(t.abbreviation, 'FA') AS team,
            pp.trait_key,
            ptd.display_name,
            ptd.category,
            ptd.polarity,
            ptd.sensitive,
            pp.intensity,
            pp.assignment_type,
            pp.hidden,
            pp.source,
            pp.notes,
            pp.created_at
        FROM player_personalities pp
        JOIN players p ON p.player_id = pp.player_id
        LEFT JOIN teams t ON t.team_id = p.team_id
        JOIN personality_trait_definitions ptd ON ptd.trait_key = pp.trait_key;

        DROP VIEW IF EXISTS player_personality_baselines_view;
        CREATE VIEW player_personality_baselines_view AS
        SELECT
            ppb.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            COALESCE(t.abbreviation, 'FA') AS team,
            ppb.trait_key,
            ptd.display_name,
            ptd.category,
            ptd.polarity,
            ppb.baseline_intensity,
            ppb.omit_chance,
            ppb.source,
            ppb.notes,
            ppb.updated_at
        FROM player_personality_baselines ppb
        JOIN players p ON p.player_id = ppb.player_id
        LEFT JOIN teams t ON t.team_id = p.team_id
        JOIN personality_trait_definitions ptd ON ptd.trait_key = ppb.trait_key;

        DROP VIEW IF EXISTS player_free_agency_preferences_view;
        CREATE VIEW player_free_agency_preferences_view AS
        SELECT
            pref.game_id,
            pref.season,
            pref.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.years_exp,
            COALESCE(t.abbreviation, 'FA') AS team,
            pref.preference_archetype,
            pref.money_priority,
            pref.security_priority,
            pref.contender_priority,
            pref.role_priority,
            pref.loyalty_priority,
            pref.location_priority,
            pref.contract_year_preference,
            pref.market_patience_modifier,
            pref.hometown_discount_pct,
            pref.contender_discount_pct,
            pref.minimum_over_ask_pct,
            pref.hidden,
            pref.source,
            pref.notes,
            pref.created_at,
            pref.updated_at
        FROM player_free_agency_preferences pref
        JOIN players p ON p.player_id = pref.player_id
        LEFT JOIN teams t ON t.team_id = p.team_id;
        """
    )


def seed_trait_definitions(con: sqlite3.Connection) -> None:
    con.executemany(
        """
        INSERT INTO personality_trait_definitions (
            trait_key, display_name, category, polarity, random_base_rate,
            min_intensity, max_intensity, sensitive, description, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(trait_key) DO UPDATE SET
            display_name = excluded.display_name,
            category = excluded.category,
            polarity = excluded.polarity,
            random_base_rate = excluded.random_base_rate,
            min_intensity = excluded.min_intensity,
            max_intensity = excluded.max_intensity,
            sensitive = excluded.sensitive,
            description = excluded.description,
            updated_at = datetime('now')
        """,
        [
            (
                trait.trait_key,
                trait.display_name,
                trait.category,
                trait.polarity,
                trait.random_base_rate,
                trait.min_intensity,
                trait.max_intensity,
                trait.sensitive,
                trait.description,
            )
            for trait in TRAITS
        ],
    )


def player_lookup(con: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = con.execute(
        """
        SELECT p.*, COALESCE(t.abbreviation, 'FA') AS team
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        """
    ).fetchall()
    return {normalize_name(f"{row['first_name']} {row['last_name']}"): row for row in rows}


def seed_baselines(con: sqlite3.Connection) -> dict[str, int]:
    lookup = player_lookup(con)
    inserted = 0
    missing = 0
    for name, trait_key, intensity, notes in BASELINE_TRAITS:
        player = lookup.get(normalize_name(name))
        if not player:
            missing += 1
            continue
        con.execute(
            """
            INSERT INTO player_personality_baselines (
                player_id, trait_key, baseline_intensity, omit_chance, source, notes, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(player_id, trait_key) DO UPDATE SET
                baseline_intensity = excluded.baseline_intensity,
                omit_chance = excluded.omit_chance,
                source = excluded.source,
                notes = excluded.notes,
                updated_at = datetime('now')
            """,
            (
                player["player_id"],
                trait_key,
                intensity,
                DEFAULT_BASELINE_OMIT_CHANCE,
                "public_role_sim_baseline",
                notes,
            ),
        )
        inserted += 1
    return {"baselines": inserted, "missing": missing}


def seed_master_data(con: sqlite3.Connection) -> dict[str, int]:
    ensure_schema(con)
    seed_trait_definitions(con)
    return seed_baselines(con)


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
            p.overall,
            p.potential,
            p.status
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        ORDER BY p.player_id
        """
    ).fetchall()


def adjusted_trait_rate(trait: sqlite3.Row, player: sqlite3.Row) -> float:
    rate = float(trait["random_base_rate"])
    age = player["age"] if player["age"] is not None else 26
    exp = player["years_exp"] if player["years_exp"] is not None else 2
    position = player["position"]
    overall = player["overall"] if player["overall"] is not None else 50

    if trait["trait_key"] in {"mentor", "natural_leader", "media_savvy"} and (age >= 29 or exp >= 7):
        rate *= 1.7
    if trait["trait_key"] in {"film_junkie", "natural_leader"} and position == "QB":
        rate *= 1.8
    if trait["trait_key"] in {"ring_chaser"} and age >= 30:
        rate *= 2.1
    if trait["trait_key"] in {"hometown_pull", "chip_on_shoulder", "streaky_confidence"} and exp <= 3:
        rate *= 1.35
    if trait["trait_key"] in {"greedy", "media_savvy", "big_stage"} and overall >= 82:
        rate *= 1.45
    if trait["trait_key"] in {"locker_room_distraction", "off_field_issue"} and exp == 0:
        rate *= 0.75
    return min(rate, 0.18)


def random_intensity(rng: random.Random, trait: sqlite3.Row) -> int:
    low = int(trait["min_intensity"])
    high = int(trait["max_intensity"])
    return max(low, min(high, int(round(rng.gauss((low + high) / 2, (high - low) / 6)))))


def clamp_priority(value: float) -> int:
    return max(1, min(20, int(round(value))))


def clamp_years(value: float) -> int:
    return max(1, min(5, int(round(value))))


def trait_intensity_factor(traits: dict[str, int], trait_key: str) -> float:
    return max(0.0, min(1.0, traits.get(trait_key, 0) / 100.0))


def active_trait_map_from_assignments(
    assignments: dict[tuple[int, str], tuple[int, str, str]],
) -> dict[int, dict[str, int]]:
    trait_map: dict[int, dict[str, int]] = {}
    for (player_id, trait_key), (intensity, _assignment_type, _notes) in assignments.items():
        trait_map.setdefault(player_id, {})[trait_key] = int(intensity)
    return trait_map


def load_trait_map(con: sqlite3.Connection, *, game_id: str, season: int) -> dict[int, dict[str, int]]:
    rows = con.execute(
        """
        SELECT player_id, trait_key, intensity
        FROM player_personalities
        WHERE game_id = ? AND season = ?
        """,
        (game_id, season),
    ).fetchall()
    trait_map: dict[int, dict[str, int]] = {}
    for row in rows:
        trait_map.setdefault(int(row["player_id"]), {})[str(row["trait_key"])] = int(row["intensity"])
    return trait_map


def preference_archetype(priorities: dict[str, int], traits: dict[str, int]) -> str:
    if traits.get("greedy", 0) >= 62 or priorities["money"] >= 16:
        return "money_first"
    if traits.get("ring_chaser", 0) >= 58 or priorities["contender"] >= 16:
        return "ring_chaser"
    if traits.get("hometown_pull", 0) >= 56 or priorities["location"] >= 16:
        return "location_fit"
    if priorities["security"] >= 16:
        return "security_seeker"
    if priorities["role"] >= 16:
        return "role_hunter"
    if priorities["loyalty"] >= 16:
        return "loyalist"
    return "balanced"


def build_free_agency_preference(
    player: sqlite3.Row,
    traits: dict[str, int],
    rng: random.Random,
) -> tuple[int, int, int, int, int, int, int, int, float, float, float, str, str]:
    age = int(player["age"]) if player["age"] is not None else 26
    exp = int(player["years_exp"]) if player["years_exp"] is not None else 3
    overall = int(player["overall"]) if player["overall"] is not None else 60

    money = rng.gauss(10, 2.2)
    security = rng.gauss(10, 2.0)
    contender = rng.gauss(10, 2.1)
    role = rng.gauss(10, 2.0)
    loyalty = rng.gauss(10, 1.8)
    location = rng.gauss(8, 1.9)

    if age >= 30:
        contender += 2.4
        security -= 1.2
    if age <= 25 or exp <= 3:
        role += 2.6
        security += 1.0
    if overall >= 82:
        money += 2.0
        contender += 1.0
    if overall < 68:
        role += 1.6
        security += 1.4

    money += 7.0 * trait_intensity_factor(traits, "greedy")
    security += 3.4 * trait_intensity_factor(traits, "greedy")
    contender -= 3.0 * trait_intensity_factor(traits, "greedy")
    contender += 7.0 * trait_intensity_factor(traits, "ring_chaser")
    money -= 2.2 * trait_intensity_factor(traits, "ring_chaser")
    location += 8.0 * trait_intensity_factor(traits, "hometown_pull")
    role += 4.8 * trait_intensity_factor(traits, "chip_on_shoulder")
    role += 2.2 * trait_intensity_factor(traits, "lunch_pail")
    role += 1.6 * trait_intensity_factor(traits, "film_junkie")
    loyalty += 3.8 * trait_intensity_factor(traits, "natural_leader")
    loyalty += 2.8 * trait_intensity_factor(traits, "mentor")
    loyalty += 2.2 * trait_intensity_factor(traits, "quiet_professional")
    contender += 2.4 * trait_intensity_factor(traits, "big_stage")
    contender += 1.4 * trait_intensity_factor(traits, "film_junkie")
    location += 1.6 * trait_intensity_factor(traits, "jokester")
    loyalty -= 3.0 * trait_intensity_factor(traits, "locker_room_distraction")
    security += 2.0 * trait_intensity_factor(traits, "off_field_issue")

    priorities = {
        "money": clamp_priority(money),
        "security": clamp_priority(security),
        "contender": clamp_priority(contender),
        "role": clamp_priority(role),
        "loyalty": clamp_priority(loyalty),
        "location": clamp_priority(location),
    }
    archetype = preference_archetype(priorities, traits)

    if archetype == "ring_chaser":
        contract_years = 1 if age >= 30 else 2
    elif archetype == "role_hunter":
        contract_years = 1 if overall < 72 else 2
    elif archetype == "security_seeker":
        contract_years = 4 if age <= 29 else 2
    elif archetype == "money_first":
        contract_years = 3 if age <= 30 else 1
    else:
        contract_years = 3 if age <= 27 else 2 if age <= 30 else 1
    if priorities["security"] >= 16 and age <= 31:
        contract_years += 1
    if priorities["role"] >= 16 and overall < 74:
        contract_years -= 1
    contract_years = clamp_years(contract_years)

    patience_modifier = 0
    patience_modifier += max(-3, min(4, (priorities["money"] - 10) // 3))
    patience_modifier += max(-2, min(3, (priorities["security"] - 10) // 4))
    if archetype == "ring_chaser":
        patience_modifier += 1
    if archetype == "role_hunter":
        patience_modifier -= 1

    hometown_discount = 0.08 * trait_intensity_factor(traits, "hometown_pull")
    contender_discount = 0.10 * trait_intensity_factor(traits, "ring_chaser")
    minimum_over_ask = 0.10 * trait_intensity_factor(traits, "greedy")

    notes = (
        f"Money {priorities['money']}/20, security {priorities['security']}/20, "
        f"contender {priorities['contender']}/20, role {priorities['role']}/20."
    )
    return (
        priorities["money"],
        priorities["security"],
        priorities["contender"],
        priorities["role"],
        priorities["loyalty"],
        priorities["location"],
        contract_years,
        patience_modifier,
        round(hometown_discount, 3),
        round(contender_discount, 3),
        round(minimum_over_ask, 3),
        archetype,
        notes,
    )


def seed_free_agency_preferences(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    seed: int,
    trait_map: dict[int, dict[str, int]] | None = None,
    dry_run: bool = False,
) -> int:
    ensure_schema(con)
    rng = random.Random(seed)
    players = load_players(con)
    traits_by_player = trait_map if trait_map is not None else load_trait_map(con, game_id=game_id, season=season)
    rows = []
    for player in players:
        player_id = int(player["player_id"])
        preferences = build_free_agency_preference(player, traits_by_player.get(player_id, {}), rng)
        (
            money_priority,
            security_priority,
            contender_priority,
            role_priority,
            loyalty_priority,
            location_priority,
            contract_year_preference,
            market_patience_modifier,
            hometown_discount_pct,
            contender_discount_pct,
            minimum_over_ask_pct,
            archetype,
            notes,
        ) = preferences
        rows.append(
            (
                game_id,
                season,
                player_id,
                archetype,
                money_priority,
                security_priority,
                contender_priority,
                role_priority,
                loyalty_priority,
                location_priority,
                contract_year_preference,
                market_patience_modifier,
                hometown_discount_pct,
                contender_discount_pct,
                minimum_over_ask_pct,
                f"new_game_personality_preferences:{game_id}",
                notes,
            )
        )
    if dry_run:
        return len(rows)
    con.executemany(
        """
        INSERT INTO player_free_agency_preferences (
            game_id, season, player_id, preference_archetype,
            money_priority, security_priority, contender_priority, role_priority,
            loyalty_priority, location_priority, contract_year_preference,
            market_patience_modifier, hometown_discount_pct, contender_discount_pct,
            minimum_over_ask_pct, hidden, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, datetime('now'))
        ON CONFLICT(game_id, season, player_id) DO UPDATE SET
            preference_archetype = excluded.preference_archetype,
            money_priority = excluded.money_priority,
            security_priority = excluded.security_priority,
            contender_priority = excluded.contender_priority,
            role_priority = excluded.role_priority,
            loyalty_priority = excluded.loyalty_priority,
            location_priority = excluded.location_priority,
            contract_year_preference = excluded.contract_year_preference,
            market_patience_modifier = excluded.market_patience_modifier,
            hometown_discount_pct = excluded.hometown_discount_pct,
            contender_discount_pct = excluded.contender_discount_pct,
            minimum_over_ask_pct = excluded.minimum_over_ask_pct,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        rows,
    )
    return len(rows)


def check_game_available(con: sqlite3.Connection, game_id: str, season: int) -> None:
    row = con.execute(
        """
        SELECT run_id
        FROM new_game_personality_runs
        WHERE game_id = ? AND season = ?
        """,
        (game_id, season),
    ).fetchone()
    if row:
        raise ValueError(f"Personality traits already seeded for {game_id} {season} (run_id={row['run_id']}).")


def apply_personality_variance(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    seed: int,
    baseline_omit_chance: float = DEFAULT_BASELINE_OMIT_CHANCE,
    random_trait_cap: int = DEFAULT_RANDOM_TRAIT_CAP,
    notes: str | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    seed_master_data(con)
    check_game_available(con, game_id, season)
    rng = random.Random(seed)
    players = load_players(con)
    traits = con.execute("SELECT * FROM personality_trait_definitions ORDER BY trait_key").fetchall()
    baselines = con.execute("SELECT * FROM player_personality_baselines ORDER BY player_id, trait_key").fetchall()
    baseline_by_player: dict[int, list[sqlite3.Row]] = {}
    for row in baselines:
        baseline_by_player.setdefault(int(row["player_id"]), []).append(row)

    assignments: dict[tuple[int, str], tuple[int, str, str]] = {}
    baseline_count = 0
    baseline_kept = 0
    baseline_omitted = 0

    for player_id, rows in baseline_by_player.items():
        for baseline in rows:
            baseline_count += 1
            omit_chance = baseline_omit_chance if baseline_omit_chance is not None else float(baseline["omit_chance"])
            if rng.random() < omit_chance:
                baseline_omitted += 1
                continue
            jitter = rng.randint(-8, 8)
            intensity = max(1, min(100, int(baseline["baseline_intensity"]) + jitter))
            assignments[(player_id, baseline["trait_key"])] = (
                intensity,
                "baseline",
                baseline["notes"] or "Baseline trait kept with new-save variance.",
            )
            baseline_kept += 1

    random_count = 0
    for player in players:
        player_id = int(player["player_id"])
        current_trait_count = sum(1 for key in assignments if key[0] == player_id)
        if current_trait_count >= random_trait_cap:
            continue
        shuffled_traits = list(traits)
        rng.shuffle(shuffled_traits)
        for trait in shuffled_traits:
            if current_trait_count >= random_trait_cap:
                break
            trait_key = trait["trait_key"]
            if (player_id, trait_key) in assignments:
                continue
            if rng.random() >= adjusted_trait_rate(trait, player):
                continue
            assignments[(player_id, trait_key)] = (
                random_intensity(rng, trait),
                "random",
                "Random hidden trait seeded for this save.",
            )
            random_count += 1
            current_trait_count += 1

    if dry_run:
        return {
            "players": len(players),
            "baseline_count": baseline_count,
            "baseline_kept": baseline_kept,
            "baseline_omitted": baseline_omitted,
            "random_assignments": random_count,
            "total_assignments": len(assignments),
            "preference_rows": len(players),
            "run_id": 0,
        }

    cur = con.execute(
        """
        INSERT INTO new_game_personality_runs (
            game_id, season, rng_seed, baseline_omit_chance, baseline_count,
            baseline_kept_count, baseline_omitted_count, random_assignment_count,
            player_count, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_id,
            season,
            seed,
            baseline_omit_chance,
            baseline_count,
            baseline_kept,
            baseline_omitted,
            random_count,
            len(players),
            notes,
        ),
    )
    run_id = int(cur.lastrowid)
    con.executemany(
        """
        INSERT INTO player_personalities (
            game_id, season, player_id, trait_key, intensity,
            assignment_type, hidden, source, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        [
            (
                game_id,
                season,
                player_id,
                trait_key,
                intensity,
                assignment_type,
                f"new_game_personality:{game_id}:{run_id}",
                assignment_notes,
            )
            for (player_id, trait_key), (intensity, assignment_type, assignment_notes) in assignments.items()
        ],
    )
    preference_rows = seed_free_agency_preferences(
        con,
        game_id=game_id,
        season=season,
        seed=seed ^ 0xC0FFEE,
        trait_map=active_trait_map_from_assignments(assignments),
        dry_run=False,
    )
    return {
        "players": len(players),
        "baseline_count": baseline_count,
        "baseline_kept": baseline_kept,
        "baseline_omitted": baseline_omitted,
        "random_assignments": random_count,
        "total_assignments": len(assignments),
        "preference_rows": preference_rows,
        "run_id": run_id,
    }


def print_apply_summary(result: dict[str, int], *, game_id: str, season: int, seed: int, dry_run: bool) -> None:
    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print(f"Game ID: {game_id}")
    print(f"Season: {season}")
    print(f"Seed: {seed}")
    print(f"Players considered: {result['players']}")
    print(
        f"Baseline traits: {result['baseline_kept']} kept, "
        f"{result['baseline_omitted']} omitted out of {result['baseline_count']}"
    )
    print(f"Random traits added: {result['random_assignments']}")
    print(f"Total hidden traits: {result['total_assignments']}")
    if "preference_rows" in result:
        print(f"Free-agency preference rows: {result['preference_rows']}")
    if result["run_id"]:
        print(f"Personality run id: {result['run_id']}")


def action_setup(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        counts = seed_master_data(con)
        con.commit()
    print(f"Personality trait definitions: {len(TRAITS)}")
    print(f"Baseline assignments seeded: {counts['baselines']} ({counts['missing']} skipped because player was missing).")


def action_apply(args: argparse.Namespace) -> None:
    seed = args.seed if args.seed is not None else secrets.randbits(63)
    with connect(args.db) as con:
        result = apply_personality_variance(
            con,
            game_id=args.game_id,
            season=args.season,
            seed=seed,
            baseline_omit_chance=args.baseline_omit_chance,
            random_trait_cap=args.random_trait_cap,
            notes=args.notes,
            dry_run=not args.apply,
        )
        if args.apply:
            con.commit()
    print_apply_summary(result, game_id=args.game_id, season=args.season, seed=seed, dry_run=not args.apply)


def action_summary(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_schema(con)
        rows = con.execute(
            """
            SELECT ptd.display_name, ptd.polarity, COUNT(pp.player_id) AS player_count
            FROM personality_trait_definitions ptd
            LEFT JOIN player_personalities pp
              ON pp.trait_key = ptd.trait_key
             AND pp.game_id = ?
             AND pp.season = ?
            GROUP BY ptd.trait_key
            ORDER BY player_count DESC, ptd.display_name
            """,
            (args.game_id, args.season),
        ).fetchall()
    for row in rows:
        print(f"{row['display_name']:<24} {row['polarity']:<8} {row['player_count']}")


def action_preferences(args: argparse.Namespace) -> None:
    seed = args.seed if args.seed is not None else secrets.randbits(63)
    with connect(args.db) as con:
        ensure_schema(con)
        rows = seed_free_agency_preferences(
            con,
            game_id=args.game_id,
            season=args.season,
            seed=seed,
            dry_run=not args.apply,
        )
        if args.apply:
            con.commit()
    print(f"Mode: {'DRY RUN' if not args.apply else 'APPLY'}")
    print(f"Game ID: {args.game_id}")
    print(f"Season: {args.season}")
    print(f"Seed: {seed}")
    print(f"Free-agency preference rows: {rows}")


def action_show(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_schema(con)
        filters = ["game_id = ?", "season = ?"]
        params: list[object] = [args.game_id, args.season]
        if args.player:
            filters.append("lower(player_name) LIKE ?")
            params.append(f"%{args.player.lower()}%")
        if args.team:
            filters.append("team = ?")
            params.append(args.team.upper())
        if args.trait:
            filters.append("(trait_key = ? OR lower(display_name) = lower(?))")
            params.extend([args.trait, args.trait])
        rows = con.execute(
            f"""
            SELECT *
            FROM player_personalities_view
            WHERE {' AND '.join(filters)}
            ORDER BY team, player_name, display_name
            LIMIT ?
            """,
            (*params, args.limit),
        ).fetchall()
    if not rows:
        print("No personality traits found.")
        return
    for row in rows:
        print(
            f"{row['team']:<3} {row['player_name']:<24} {row['position']:<4} "
            f"{row['display_name']:<24} {row['intensity']:>3} {row['assignment_type']}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed and inspect hidden player personalities.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Create personality tables and seed trait definitions/baselines.")
    setup_parser.set_defaults(func=action_setup)

    apply_parser = subparsers.add_parser("apply", help="Apply personality variance for one new save.")
    apply_parser.add_argument("--game-id", required=True)
    apply_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    apply_parser.add_argument("--seed", type=int)
    apply_parser.add_argument("--baseline-omit-chance", type=float, default=DEFAULT_BASELINE_OMIT_CHANCE)
    apply_parser.add_argument("--random-trait-cap", type=int, default=DEFAULT_RANDOM_TRAIT_CAP)
    apply_parser.add_argument("--notes", default="New game start personality variance.")
    apply_parser.add_argument("--apply", action="store_true", help="Persist the traits. Omit for dry run.")
    apply_parser.set_defaults(func=action_apply)

    summary_parser = subparsers.add_parser("summary", help="Summarize trait counts for one save.")
    summary_parser.add_argument("--game-id", required=True)
    summary_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    summary_parser.set_defaults(func=action_summary)

    preferences_parser = subparsers.add_parser("preferences", help="Seed or refresh hidden free-agency preferences for one save.")
    preferences_parser.add_argument("--game-id", required=True)
    preferences_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    preferences_parser.add_argument("--seed", type=int)
    preferences_parser.add_argument("--apply", action="store_true", help="Persist the preferences. Omit for dry run.")
    preferences_parser.set_defaults(func=action_preferences)

    show_parser = subparsers.add_parser("show", help="Show hidden traits for debugging.")
    show_parser.add_argument("--game-id", required=True)
    show_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    show_parser.add_argument("--player")
    show_parser.add_argument("--team")
    show_parser.add_argument("--trait")
    show_parser.add_argument("--limit", type=int, default=50)
    show_parser.set_defaults(func=action_show)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
