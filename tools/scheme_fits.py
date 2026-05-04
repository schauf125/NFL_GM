#!/usr/bin/env python3
"""Team, coach, and player scheme-fit foundation for NFL GM Sim.

This is intentionally a foundation layer. It gives the rest of the game a
shared vocabulary for schemes, staff preferences, team identities, and player
fit/adaptability without forcing the match engine or progression engine to
consume it yet.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_SEASON = 2026
SOURCE = "scheme_fits"


@dataclass(frozen=True)
class SchemeDefinition:
    scheme_key: str
    display_name: str
    side: str
    family: str
    tempo: str
    personnel: str
    description: str


SCHEMES = [
    SchemeDefinition(
        "west_coast_timing",
        "West Coast Timing",
        "offense",
        "timing_pass",
        "moderate",
        "11/12 personnel",
        "Rhythm passing, option routes, spacing, QB processing, and yards after catch.",
    ),
    SchemeDefinition(
        "wide_zone_play_action",
        "Wide Zone Play Action",
        "offense",
        "outside_zone",
        "moderate",
        "11/12/21 personnel",
        "Stretch run game, boot action, motion, athletic linemen, and layered crossers.",
    ),
    SchemeDefinition(
        "power_gap_play_action",
        "Power/Gap Play Action",
        "offense",
        "gap_run",
        "moderate",
        "12/21 personnel",
        "Duo, gap, counter, downhill backs, physical line play, and shot plays off run looks.",
    ),
    SchemeDefinition(
        "spread_rpo",
        "Spread RPO",
        "offense",
        "spread",
        "fast",
        "10/11 personnel",
        "Shotgun spacing, RPO stress, QB movement, quick throws, and open-field skill players.",
    ),
    SchemeDefinition(
        "vertical_air_raid",
        "Vertical Air Raid",
        "offense",
        "vertical_pass",
        "fast",
        "10/11 personnel",
        "Downfield passing, wide splits, pass protection, explosive receivers, and QB arm talent.",
    ),
    SchemeDefinition(
        "heavy_personnel_run",
        "Heavy Personnel Run",
        "offense",
        "heavy_run",
        "slow",
        "12/13/21 personnel",
        "Extra tight ends, fullbacks, condensed formations, play strength, and field-position control.",
    ),
    SchemeDefinition(
        "balanced_pro_style",
        "Balanced Pro Style",
        "offense",
        "balanced",
        "moderate",
        "11/12 personnel",
        "Flexible pro offense that can lean run or pass based on personnel and opponent.",
    ),
    SchemeDefinition(
        "qb_run_option",
        "QB Run Option",
        "offense",
        "option",
        "fast",
        "11/12 personnel",
        "Designed QB run threat, read option, RPOs, and movement passing.",
    ),
    SchemeDefinition(
        "four_man_cover3",
        "Four-Man Cover 3",
        "defense",
        "single_high_zone",
        "moderate",
        "4-3 nickel",
        "Four-man rush, fast linebackers, zone corners, single-high structure, and pursuit.",
    ),
    SchemeDefinition(
        "fangio_match_quarters",
        "Fangio Match Quarters",
        "defense",
        "two_high_match",
        "moderate",
        "nickel/light box",
        "Two-high shells, quarters/match rules, disguise, safeties, and coverage discipline.",
    ),
    SchemeDefinition(
        "pressure_man_blitz",
        "Pressure Man Blitz",
        "defense",
        "pressure",
        "aggressive",
        "nickel/dime",
        "Blitz volume, man coverage, slot/safety pressure, and aggressive downfield denial.",
    ),
    SchemeDefinition(
        "simulated_pressure_quarters",
        "Simulated Pressure Quarters",
        "defense",
        "sim_pressure",
        "aggressive",
        "multiple nickel",
        "Disguised fronts, simulated pressure, rotating safeties, and post-snap conflict.",
    ),
    SchemeDefinition(
        "three_four_multiple",
        "3-4 Multiple Front",
        "defense",
        "odd_front",
        "moderate",
        "3-4 nickel",
        "Odd fronts, stand-up edges, nose tackles, versatile linebackers, and pressure variety.",
    ),
    SchemeDefinition(
        "tampa2_zone",
        "Tampa 2 Zone",
        "defense",
        "zone",
        "moderate",
        "4-3 nickel",
        "Zone drops, athletic linebackers, deep middle safety help, and four-man rush integrity.",
    ),
    SchemeDefinition(
        "hybrid_multiple_front",
        "Hybrid Multiple Front",
        "defense",
        "hybrid",
        "moderate",
        "multiple",
        "Weekly front changes, role flexibility, disguised pressure, and matchup-specific calls.",
    ),
    SchemeDefinition(
        "man_match_single_high",
        "Man-Match Single High",
        "defense",
        "single_high_match",
        "aggressive",
        "nickel",
        "Press/man-match principles, single-high safety rotation, and physical corners.",
    ),
    SchemeDefinition(
        "special_teams_operation",
        "Special Teams Operation",
        "special_teams",
        "specialist",
        "situational",
        "specialists",
        "Kicking, punting, snapping, coverage lanes, return value, and operation reliability.",
    ),
]


OFFENSIVE_SCHEMES = [s.scheme_key for s in SCHEMES if s.side == "offense"]
DEFENSIVE_SCHEMES = [s.scheme_key for s in SCHEMES if s.side == "defense"]
SPECIAL_SCHEMES = [s.scheme_key for s in SCHEMES if s.side == "special_teams"]


SCHEME_ROLE_WEIGHTS: dict[str, dict[str, float]] = {
    "west_coast_timing": {
        "pocket_qb": 1.25,
        "slot_wr": 1.20,
        "move_te": 1.12,
        "elusive_rb": 1.05,
        "boundary_wr": 0.95,
        "pass_protecting_ot": 1.05,
        "interior_run_blocker": 0.92,
    },
    "wide_zone_play_action": {
        "pocket_qb": 1.00,
        "scrambling_qb": 1.05,
        "elusive_rb": 1.18,
        "boundary_wr": 1.08,
        "slot_wr": 1.04,
        "move_te": 1.10,
        "inline_te": 1.00,
        "pass_protecting_ot": 1.02,
        "interior_run_blocker": 0.96,
    },
    "power_gap_play_action": {
        "pocket_qb": 1.04,
        "power_rb": 1.25,
        "inline_te": 1.18,
        "boundary_wr": 1.02,
        "pass_protecting_ot": 0.96,
        "interior_run_blocker": 1.20,
    },
    "spread_rpo": {
        "scrambling_qb": 1.25,
        "pocket_qb": 0.92,
        "elusive_rb": 1.12,
        "slot_wr": 1.20,
        "boundary_wr": 1.06,
        "move_te": 1.05,
        "pass_protecting_ot": 1.02,
        "interior_run_blocker": 0.92,
    },
    "vertical_air_raid": {
        "pocket_qb": 1.16,
        "scrambling_qb": 1.02,
        "boundary_wr": 1.25,
        "slot_wr": 1.04,
        "move_te": 1.08,
        "pass_protecting_ot": 1.18,
        "elusive_rb": 0.90,
    },
    "heavy_personnel_run": {
        "power_rb": 1.28,
        "inline_te": 1.28,
        "interior_run_blocker": 1.22,
        "pass_protecting_ot": 0.90,
        "boundary_wr": 0.92,
        "pocket_qb": 0.96,
    },
    "balanced_pro_style": {
        "pocket_qb": 1.05,
        "scrambling_qb": 0.98,
        "power_rb": 1.02,
        "elusive_rb": 1.02,
        "boundary_wr": 1.03,
        "slot_wr": 1.03,
        "inline_te": 1.02,
        "move_te": 1.02,
        "pass_protecting_ot": 1.03,
        "interior_run_blocker": 1.03,
    },
    "qb_run_option": {
        "scrambling_qb": 1.34,
        "pocket_qb": 0.78,
        "elusive_rb": 1.14,
        "power_rb": 1.04,
        "slot_wr": 1.10,
        "move_te": 1.02,
        "pass_protecting_ot": 0.94,
        "interior_run_blocker": 0.98,
    },
    "four_man_cover3": {
        "speed_edge": 1.12,
        "power_edge": 1.04,
        "interior_rusher": 1.05,
        "nose_run_stopping_dt": 0.96,
        "coverage_lb": 1.10,
        "box_lb": 1.02,
        "zone_cb": 1.18,
        "man_cb": 0.94,
        "deep_safety": 1.08,
        "box_safety": 1.02,
    },
    "fangio_match_quarters": {
        "speed_edge": 1.04,
        "interior_rusher": 1.08,
        "coverage_lb": 1.16,
        "zone_cb": 1.18,
        "deep_safety": 1.24,
        "box_safety": 0.96,
        "man_cb": 0.92,
        "nose_run_stopping_dt": 0.98,
    },
    "pressure_man_blitz": {
        "speed_edge": 1.22,
        "power_edge": 1.14,
        "interior_rusher": 1.12,
        "man_cb": 1.22,
        "box_safety": 1.14,
        "box_lb": 1.08,
        "zone_cb": 0.86,
        "deep_safety": 0.96,
    },
    "simulated_pressure_quarters": {
        "speed_edge": 1.18,
        "interior_rusher": 1.10,
        "coverage_lb": 1.12,
        "zone_cb": 1.14,
        "deep_safety": 1.16,
        "box_safety": 1.04,
        "man_cb": 0.98,
        "power_edge": 1.02,
    },
    "three_four_multiple": {
        "power_edge": 1.20,
        "speed_edge": 1.08,
        "nose_run_stopping_dt": 1.22,
        "box_lb": 1.12,
        "coverage_lb": 1.02,
        "man_cb": 1.02,
        "zone_cb": 1.00,
        "deep_safety": 1.02,
    },
    "tampa2_zone": {
        "coverage_lb": 1.24,
        "zone_cb": 1.18,
        "deep_safety": 1.10,
        "interior_rusher": 1.08,
        "speed_edge": 1.04,
        "box_lb": 0.94,
        "man_cb": 0.90,
    },
    "hybrid_multiple_front": {
        "speed_edge": 1.08,
        "power_edge": 1.08,
        "interior_rusher": 1.08,
        "nose_run_stopping_dt": 1.04,
        "coverage_lb": 1.06,
        "box_lb": 1.06,
        "man_cb": 1.04,
        "zone_cb": 1.04,
        "deep_safety": 1.06,
        "box_safety": 1.06,
    },
    "man_match_single_high": {
        "man_cb": 1.24,
        "speed_edge": 1.12,
        "power_edge": 1.08,
        "box_safety": 1.14,
        "box_lb": 1.06,
        "deep_safety": 1.02,
        "zone_cb": 0.92,
    },
}


OFFENSE_HINTS: dict[str, str] = {
    "andy reid": "west_coast_timing",
    "sean payton": "west_coast_timing",
    "josh mcdaniels": "west_coast_timing",
    "frank reich": "west_coast_timing",
    "mike mccarthy": "west_coast_timing",
    "pete carmichael": "west_coast_timing",
    "kyle shanahan": "wide_zone_play_action",
    "mike mcdaniel": "wide_zone_play_action",
    "klint kubiak": "wide_zone_play_action",
    "klay kubiak": "wide_zone_play_action",
    "matt lafleur": "wide_zone_play_action",
    "mike lafleur": "wide_zone_play_action",
    "bobby slowik": "wide_zone_play_action",
    "kevin o'connell": "wide_zone_play_action",
    "wes phillips": "wide_zone_play_action",
    "sean mcvay": "wide_zone_play_action",
    "liam coen": "wide_zone_play_action",
    "zac robinson": "wide_zone_play_action",
    "kevin stefanski": "power_gap_play_action",
    "dan campbell": "power_gap_play_action",
    "jim harbaugh": "power_gap_play_action",
    "ben johnson": "power_gap_play_action",
    "todd monken": "vertical_air_raid",
    "brian daboll": "spread_rpo",
    "shane steichen": "spread_rpo",
    "kellen moore": "spread_rpo",
    "joe brady": "spread_rpo",
    "zac taylor": "spread_rpo",
}


DEFENSE_HINTS: dict[str, str] = {
    "vic fangio": "fangio_match_quarters",
    "brandon staley": "fangio_match_quarters",
    "ejiro evero": "fangio_match_quarters",
    "chris shula": "fangio_match_quarters",
    "jonathan gannon": "fangio_match_quarters",
    "lou anarumo": "fangio_match_quarters",
    "brian flores": "pressure_man_blitz",
    "steve spagnuolo": "pressure_man_blitz",
    "todd bowles": "pressure_man_blitz",
    "vance joseph": "pressure_man_blitz",
    "patrick graham": "pressure_man_blitz",
    "dennis allen": "pressure_man_blitz",
    "mike macdonald": "simulated_pressure_quarters",
    "jesse minter": "simulated_pressure_quarters",
    "dennard wilson": "man_match_single_high",
    "dan quinn": "four_man_cover3",
    "gus bradley": "four_man_cover3",
    "robert saleh": "four_man_cover3",
    "demeco ryans": "four_man_cover3",
    "jeff ulbrich": "four_man_cover3",
    "aaron glenn": "four_man_cover3",
    "raheem morris": "four_man_cover3",
}


RELATED_SCHEME = {
    "west_coast_timing": "balanced_pro_style",
    "wide_zone_play_action": "west_coast_timing",
    "power_gap_play_action": "heavy_personnel_run",
    "spread_rpo": "qb_run_option",
    "vertical_air_raid": "spread_rpo",
    "heavy_personnel_run": "power_gap_play_action",
    "balanced_pro_style": "west_coast_timing",
    "qb_run_option": "spread_rpo",
    "four_man_cover3": "tampa2_zone",
    "fangio_match_quarters": "simulated_pressure_quarters",
    "pressure_man_blitz": "man_match_single_high",
    "simulated_pressure_quarters": "fangio_match_quarters",
    "three_four_multiple": "hybrid_multiple_front",
    "tampa2_zone": "four_man_cover3",
    "hybrid_multiple_front": "three_four_multiple",
    "man_match_single_high": "pressure_man_blitz",
}


OFFENSE_POSITIONS = {"QB", "RB", "FB", "WR", "TE", "OT", "OG", "C"}
DEFENSE_POSITIONS = {"EDGE", "IDL", "LB", "ILB", "OLB", "CB", "NB", "FS", "SS", "S"}
SPECIAL_POSITIONS = {"K", "P", "LS"}


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


def clamp_int(value: float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(round(value))))


def stable_noise(*parts: Any, spread: int = 4) -> int:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    value = int(digest[:8], 16)
    return (value % (spread * 2 + 1)) - spread


def text_tokens(value: str) -> set[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in value)
    return {token for token in cleaned.split() if token}


def position_side(position: str) -> str:
    pos = position.upper()
    if pos in OFFENSE_POSITIONS:
        return "offense"
    if pos in DEFENSE_POSITIONS:
        return "defense"
    if pos in SPECIAL_POSITIONS:
        return "special_teams"
    return "offense"


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS scheme_definitions (
            scheme_key TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            side TEXT NOT NULL,
            family TEXT NOT NULL,
            tempo TEXT,
            personnel TEXT,
            description TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS coach_scheme_fits (
            coach_id INTEGER NOT NULL REFERENCES coaches(coach_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            scheme_key TEXT NOT NULL REFERENCES scheme_definitions(scheme_key) ON DELETE CASCADE,
            fit_grade INTEGER NOT NULL,
            preference_rank INTEGER NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'medium',
            source TEXT NOT NULL,
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(coach_id, season, scheme_key)
        );

        CREATE TABLE IF NOT EXISTS team_scheme_identities (
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            offense_scheme_key TEXT NOT NULL REFERENCES scheme_definitions(scheme_key) ON DELETE RESTRICT,
            defense_scheme_key TEXT NOT NULL REFERENCES scheme_definitions(scheme_key) ON DELETE RESTRICT,
            offensive_confidence INTEGER NOT NULL DEFAULT 70,
            defensive_confidence INTEGER NOT NULL DEFAULT 70,
            source TEXT NOT NULL,
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(team_id, season)
        );

        CREATE TABLE IF NOT EXISTS player_scheme_fits (
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            scheme_key TEXT NOT NULL REFERENCES scheme_definitions(scheme_key) ON DELETE CASCADE,
            side TEXT NOT NULL,
            current_fit INTEGER NOT NULL,
            growth_fit INTEGER NOT NULL,
            fit_rank INTEGER NOT NULL,
            is_best_fit INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL,
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(player_id, season, scheme_key)
        );

        CREATE INDEX IF NOT EXISTS idx_coach_scheme_fits_scheme
            ON coach_scheme_fits(season, scheme_key, fit_grade);

        CREATE INDEX IF NOT EXISTS idx_player_scheme_fits_player
            ON player_scheme_fits(player_id, season, fit_rank);

        CREATE INDEX IF NOT EXISTS idx_player_scheme_fits_scheme
            ON player_scheme_fits(season, scheme_key, current_fit);

        DROP VIEW IF EXISTS team_scheme_identities_view;
        CREATE VIEW team_scheme_identities_view AS
        SELECT
            tsi.team_id,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name,
            tsi.season,
            tsi.offense_scheme_key,
            os.display_name AS offense_scheme,
            os.family AS offense_family,
            os.tempo AS offense_tempo,
            os.personnel AS offense_personnel,
            tsi.offensive_confidence,
            tsi.defense_scheme_key,
            ds.display_name AS defense_scheme,
            ds.family AS defense_family,
            ds.tempo AS defense_tempo,
            ds.personnel AS defense_personnel,
            tsi.defensive_confidence,
            tsi.source,
            tsi.notes,
            tsi.updated_at
        FROM team_scheme_identities tsi
        JOIN teams t ON t.team_id = tsi.team_id
        JOIN scheme_definitions os ON os.scheme_key = tsi.offense_scheme_key
        JOIN scheme_definitions ds ON ds.scheme_key = tsi.defense_scheme_key;

        DROP VIEW IF EXISTS coach_scheme_fits_view;
        CREATE VIEW coach_scheme_fits_view AS
        SELECT
            csf.coach_id,
            c.team_id,
            t.abbreviation AS team,
            c.name AS coach_name,
            c.role,
            c.specialty,
            c.overall AS coach_overall,
            csf.season,
            csf.scheme_key,
            sd.display_name AS scheme,
            sd.side,
            sd.family,
            csf.fit_grade,
            csf.preference_rank,
            csf.confidence,
            csf.source,
            csf.notes,
            csf.updated_at
        FROM coach_scheme_fits csf
        JOIN coaches c ON c.coach_id = csf.coach_id
        JOIN teams t ON t.team_id = c.team_id
        JOIN scheme_definitions sd ON sd.scheme_key = csf.scheme_key;

        DROP VIEW IF EXISTS player_scheme_fits_view;
        CREATE VIEW player_scheme_fits_view AS
        SELECT
            psf.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.team_id,
            COALESCE(t.abbreviation, 'FA') AS team,
            psf.season,
            psf.scheme_key,
            sd.display_name AS scheme,
            sd.side,
            sd.family,
            psf.current_fit,
            psf.growth_fit,
            psf.fit_rank,
            psf.is_best_fit,
            psf.source,
            psf.notes,
            psf.updated_at
        FROM player_scheme_fits psf
        JOIN players p ON p.player_id = psf.player_id
        LEFT JOIN teams t ON t.team_id = p.team_id
        JOIN scheme_definitions sd ON sd.scheme_key = psf.scheme_key;

        DROP VIEW IF EXISTS current_player_scheme_fit_view;
        CREATE VIEW current_player_scheme_fit_view AS
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.team_id,
            t.abbreviation AS team,
            tsi.season,
            CASE
                WHEN p.position IN ('QB', 'RB', 'FB', 'WR', 'TE', 'OT', 'OG', 'C')
                    THEN tsi.offense_scheme_key
                WHEN p.position IN ('EDGE', 'IDL', 'LB', 'ILB', 'OLB', 'CB', 'NB', 'FS', 'SS', 'S')
                    THEN tsi.defense_scheme_key
                WHEN p.position IN ('K', 'P', 'LS')
                    THEN 'special_teams_operation'
                ELSE tsi.offense_scheme_key
            END AS current_scheme_key,
            sd.display_name AS current_scheme,
            sd.side AS current_scheme_side,
            psf.current_fit,
            psf.growth_fit,
            psf.fit_rank AS natural_scheme_rank,
            psf.is_best_fit,
            psf.notes
        FROM players p
        JOIN teams t ON t.team_id = p.team_id
        JOIN team_scheme_identities tsi ON tsi.team_id = p.team_id
        JOIN player_scheme_fits psf
          ON psf.player_id = p.player_id
         AND psf.season = tsi.season
         AND psf.scheme_key = CASE
                WHEN p.position IN ('QB', 'RB', 'FB', 'WR', 'TE', 'OT', 'OG', 'C')
                    THEN tsi.offense_scheme_key
                WHEN p.position IN ('EDGE', 'IDL', 'LB', 'ILB', 'OLB', 'CB', 'NB', 'FS', 'SS', 'S')
                    THEN tsi.defense_scheme_key
                WHEN p.position IN ('K', 'P', 'LS')
                    THEN 'special_teams_operation'
                ELSE tsi.offense_scheme_key
            END
        JOIN scheme_definitions sd ON sd.scheme_key = psf.scheme_key
        WHERE COALESCE(p.status, 'Active') != 'Retired';
        """
    )


def seed_scheme_definitions(con: sqlite3.Connection) -> None:
    con.executemany(
        """
        INSERT INTO scheme_definitions (
            scheme_key, display_name, side, family, tempo, personnel, description, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(scheme_key) DO UPDATE SET
            display_name = excluded.display_name,
            side = excluded.side,
            family = excluded.family,
            tempo = excluded.tempo,
            personnel = excluded.personnel,
            description = excluded.description,
            updated_at = datetime('now')
        """,
        [
            (
                scheme.scheme_key,
                scheme.display_name,
                scheme.side,
                scheme.family,
                scheme.tempo,
                scheme.personnel,
                scheme.description,
            )
            for scheme in SCHEMES
        ],
    )


def seed_master_data(con: sqlite3.Connection) -> None:
    ensure_schema(con)
    seed_scheme_definitions(con)


def role_scores_by_player(con: sqlite3.Connection, season: int) -> dict[int, dict[str, float]]:
    if not table_exists(con, "player_role_scores"):
        return {}
    rows = con.execute(
        """
        SELECT player_id, role_key, MAX(role_score) AS role_score
        FROM player_role_scores
        WHERE season = ?
        GROUP BY player_id, role_key
        """,
        (season,),
    ).fetchall()
    scores: dict[int, dict[str, float]] = {}
    for row in rows:
        scores.setdefault(int(row["player_id"]), {})[str(row["role_key"])] = float(row["role_score"] or 0)
    return scores


def player_base_score(player: sqlite3.Row) -> float:
    overall = float(player["overall"] or 55)
    potential = float(player["potential"] or overall)
    return max(35.0, min(92.0, overall * 0.82 + potential * 0.18))


def weighted_role_score(
    player: sqlite3.Row,
    scheme_key: str,
    player_roles: dict[str, float],
) -> float:
    weights = SCHEME_ROLE_WEIGHTS.get(scheme_key, {})
    if not weights:
        return player_base_score(player)
    usable = [(player_roles[role], weight) for role, weight in weights.items() if role in player_roles]
    if not usable:
        return player_base_score(player)
    numerator = sum(score * weight for score, weight in usable)
    denominator = sum(weight for _score, weight in usable)
    return numerator / max(0.1, denominator)


def physical_adjustment(player: sqlite3.Row, scheme_key: str) -> float:
    pos = str(player["position"] or "").upper()
    height = int(player["height_in"] or 0)
    weight = int(player["weight_lbs"] or 0)
    adjustment = 0.0

    if scheme_key == "wide_zone_play_action":
        if pos in {"OT", "OG", "C"} and weight and weight <= 315:
            adjustment += 2.2
        if pos == "RB" and weight and weight <= 220:
            adjustment += 1.5
        if pos in {"TE", "WR"} and height >= 74:
            adjustment += 0.8
    elif scheme_key in {"power_gap_play_action", "heavy_personnel_run"}:
        if pos in {"OT", "OG", "C"} and weight >= 315:
            adjustment += 2.4
        if pos == "RB" and weight >= 220:
            adjustment += 2.0
        if pos == "TE" and weight >= 250:
            adjustment += 1.8
        if pos == "FB":
            adjustment += 4.0
    elif scheme_key in {"spread_rpo", "qb_run_option"}:
        if pos == "QB" and weight and weight <= 225:
            adjustment += 1.8
        if pos in {"WR", "RB"} and weight and weight <= 215:
            adjustment += 1.2
    elif scheme_key == "vertical_air_raid":
        if pos == "WR" and height >= 73:
            adjustment += 1.6
        if pos == "OT":
            adjustment += 1.2

    if scheme_key == "three_four_multiple":
        if pos == "IDL" and weight >= 310:
            adjustment += 3.0
        if pos in {"EDGE", "OLB"} and weight >= 250:
            adjustment += 1.6
    elif scheme_key in {"four_man_cover3", "tampa2_zone"}:
        if pos in {"LB", "ILB", "OLB"} and weight and weight <= 240:
            adjustment += 1.2
        if pos == "CB" and height >= 72:
            adjustment += 0.8
    elif scheme_key == "fangio_match_quarters":
        if pos in {"FS", "S", "SS"}:
            adjustment += 1.6
        if pos == "CB" and height >= 71:
            adjustment += 0.8
    elif scheme_key in {"pressure_man_blitz", "man_match_single_high"}:
        if pos in {"EDGE", "OLB"}:
            adjustment += 1.8
        if pos in {"CB", "NB"} and height >= 71:
            adjustment += 1.2

    return adjustment


def growth_adjustment(player: sqlite3.Row, current_fit: int, scheme_key: str) -> int:
    overall = int(player["overall"] or 55)
    potential = int(player["potential"] or overall)
    age = int(player["age"] or 26)
    gap = max(0, potential - overall)
    age_bonus = 4 if age <= 23 else 2 if age <= 25 else 0 if age <= 29 else -2 if age <= 33 else -4
    raw = current_fit + gap * 0.22 + age_bonus + stable_noise(player["player_id"], scheme_key, "growth", spread=3)
    return clamp_int(raw)


def player_scheme_rows(
    con: sqlite3.Connection,
    season: int,
    player_ids: list[int] | tuple[int, ...] | set[int] | None = None,
) -> list[tuple[Any, ...]]:
    role_scores = role_scores_by_player(con, season)
    params: list[Any] = []
    player_filter = ""
    if player_ids is not None:
        ids = sorted({int(player_id) for player_id in player_ids})
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        player_filter = f"AND p.player_id IN ({placeholders})"
        params.extend(ids)
    players = con.execute(
        f"""
        SELECT player_id, first_name || ' ' || last_name AS player_name, position, team_id,
               age, years_exp, height_in, weight_lbs, overall, potential, status
        FROM players p
        WHERE COALESCE(status, 'Active') != 'Retired'
          {player_filter}
        ORDER BY player_id
        """,
        params,
    ).fetchall()
    rows: list[tuple[Any, ...]] = []
    for player in players:
        player_id = int(player["player_id"])
        side = position_side(str(player["position"]))
        schemes = (
            OFFENSIVE_SCHEMES
            if side == "offense"
            else DEFENSIVE_SCHEMES
            if side == "defense"
            else SPECIAL_SCHEMES
        )
        scored: list[tuple[str, int, int]] = []
        for scheme_key in schemes:
            if side == "special_teams":
                score = player_base_score(player) + stable_noise(player_id, scheme_key, spread=3)
            else:
                score = (
                    weighted_role_score(player, scheme_key, role_scores.get(player_id, {}))
                    + physical_adjustment(player, scheme_key)
                    + stable_noise(player_id, scheme_key, "current", spread=4)
                )
            current_fit = clamp_int(score)
            growth_fit = growth_adjustment(player, current_fit, scheme_key)
            scored.append((scheme_key, current_fit, growth_fit))
        scored.sort(key=lambda item: (item[1], item[2], item[0]), reverse=True)
        for rank, (scheme_key, current_fit, growth_fit) in enumerate(scored, start=1):
            rows.append(
                (
                    player_id,
                    season,
                    scheme_key,
                    side,
                    current_fit,
                    growth_fit,
                    rank,
                    1 if rank <= 2 else 0,
                    SOURCE,
                    "Deterministic fit from role scores, physical profile, age, and position family.",
                )
            )
    return rows


def hinted_scheme(name: str | None, specialty: str | None, side: str) -> str:
    text = f"{name or ''} {specialty or ''}".lower()
    hints = OFFENSE_HINTS if side == "offense" else DEFENSE_HINTS
    for key, scheme in hints.items():
        if key in text:
            return scheme
    tokens = text_tokens(text)
    if side == "offense":
        if {"qb", "pass", "passing", "wr", "receiver", "receivers"} & tokens:
            return "west_coast_timing"
        if {"run", "running", "ol", "line", "offensive"} & tokens:
            return "power_gap_play_action"
        if {"te", "tight", "ends"} & tokens:
            return "balanced_pro_style"
        return "balanced_pro_style"
    if {"db", "safety", "safeties", "secondary"} & tokens:
        return "fangio_match_quarters"
    if {"linebacker", "linebackers", "front", "line"} & tokens:
        return "four_man_cover3"
    return "hybrid_multiple_front"


def coach_side(role: str, specialty: str | None) -> str:
    text = f"{role} {specialty or ''}".lower()
    tokens = text_tokens(text)
    if "defensive" in tokens or "defense" in tokens:
        return "defense"
    if "offensive" in tokens or "offense" in tokens:
        return "offense"
    if {"qb", "wr", "receiver", "receivers", "te", "tight", "ol"} & tokens:
        return "offense"
    if {"linebacker", "linebackers", "db", "front", "secondary"} & tokens:
        return "defense"
    return "defense" if "head coach" in text and "def" in text else "offense"


def coach_grade_anchor(raw_overall: Any) -> float:
    raw = float(raw_overall or 10)
    if raw <= 20:
        return 50.0 + raw * 2.5
    return raw


def coach_fit_rows(con: sqlite3.Connection, season: int) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    coaches = con.execute(
        """
        SELECT coach_id, team_id, name, role, specialty, overall
        FROM coaches
        ORDER BY coach_id
        """
    ).fetchall()
    for coach in coaches:
        side = coach_side(str(coach["role"]), coach["specialty"])
        primary = hinted_scheme(coach["name"], coach["specialty"], side)
        secondary = RELATED_SCHEME.get(primary, "balanced_pro_style" if side == "offense" else "hybrid_multiple_front")
        tertiary = "balanced_pro_style" if side == "offense" else "hybrid_multiple_front"
        overall = coach_grade_anchor(coach["overall"])
        primary_grade = clamp_int(80 + (overall - 70) * 0.35 + stable_noise(coach["coach_id"], primary, spread=4), 55, 98)
        secondary_grade = clamp_int(primary_grade - 10 + stable_noise(coach["coach_id"], secondary, spread=3), 45, 92)
        tertiary_grade = clamp_int(primary_grade - 18 + stable_noise(coach["coach_id"], tertiary, spread=3), 38, 86)
        entries = [(primary, primary_grade), (secondary, secondary_grade), (tertiary, tertiary_grade)]
        seen: set[str] = set()
        rank = 1
        for scheme_key, grade in entries:
            if scheme_key in seen:
                continue
            seen.add(scheme_key)
            rows.append(
                (
                    int(coach["coach_id"]),
                    season,
                    scheme_key,
                    grade,
                    rank,
                    "medium",
                    SOURCE,
                    f"Inferred from {coach['role']} specialty: {coach['specialty'] or 'general staff profile'}.",
                )
            )
            rank += 1
    return rows


def best_scheme_for_team(con: sqlite3.Connection, team_id: int, season: int, side: str) -> tuple[str, int]:
    roles = ["Offensive Coordinator", "Head Coach"] if side == "offense" else ["Defensive Coordinator", "Head Coach"]
    candidates = con.execute(
        """
        SELECT c.coach_id, c.role, csf.scheme_key, csf.fit_grade, csf.preference_rank
        FROM coaches c
        JOIN coach_scheme_fits csf ON csf.coach_id = c.coach_id
        JOIN scheme_definitions sd ON sd.scheme_key = csf.scheme_key
        WHERE c.team_id = ?
          AND csf.season = ?
          AND sd.side = ?
        """,
        (team_id, season, side),
    ).fetchall()
    weighted: dict[str, float] = {}
    for row in candidates:
        role = str(row["role"])
        if role not in roles:
            continue
        weight = 0.68 if role == roles[0] else 0.42
        if int(row["preference_rank"] or 9) == 1:
            weight += 0.15
        weighted[str(row["scheme_key"])] = weighted.get(str(row["scheme_key"]), 0.0) + float(row["fit_grade"] or 50) * weight
    if not weighted:
        fallback = "balanced_pro_style" if side == "offense" else "hybrid_multiple_front"
        return fallback, 55
    scheme_key, score = max(weighted.items(), key=lambda item: item[1])
    confidence = clamp_int(score, 50, 96)
    return scheme_key, confidence


def team_identity_rows(con: sqlite3.Connection, season: int) -> list[tuple[Any, ...]]:
    teams = con.execute("SELECT team_id, abbreviation FROM teams ORDER BY team_id").fetchall()
    rows = []
    for team in teams:
        offense_scheme, offense_confidence = best_scheme_for_team(con, int(team["team_id"]), season, "offense")
        defense_scheme, defense_confidence = best_scheme_for_team(con, int(team["team_id"]), season, "defense")
        rows.append(
            (
                int(team["team_id"]),
                season,
                offense_scheme,
                defense_scheme,
                offense_confidence,
                defense_confidence,
                SOURCE,
                "Team identity inferred from current head coach and coordinator scheme fits.",
            )
        )
    return rows


def seed_coach_scheme_fits(con: sqlite3.Connection, season: int) -> int:
    rows = coach_fit_rows(con, season)
    con.executemany(
        """
        INSERT INTO coach_scheme_fits (
            coach_id, season, scheme_key, fit_grade, preference_rank,
            confidence, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(coach_id, season, scheme_key) DO UPDATE SET
            fit_grade = excluded.fit_grade,
            preference_rank = excluded.preference_rank,
            confidence = excluded.confidence,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        rows,
    )
    return len(rows)


def seed_team_identities(con: sqlite3.Connection, season: int) -> int:
    rows = team_identity_rows(con, season)
    con.executemany(
        """
        INSERT INTO team_scheme_identities (
            team_id, season, offense_scheme_key, defense_scheme_key,
            offensive_confidence, defensive_confidence, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(team_id, season) DO UPDATE SET
            offense_scheme_key = excluded.offense_scheme_key,
            defense_scheme_key = excluded.defense_scheme_key,
            offensive_confidence = excluded.offensive_confidence,
            defensive_confidence = excluded.defensive_confidence,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        rows,
    )
    return len(rows)


def seed_player_scheme_fits(
    con: sqlite3.Connection,
    season: int,
    player_ids: list[int] | tuple[int, ...] | set[int] | None = None,
) -> int:
    rows = player_scheme_rows(con, season, player_ids=player_ids)
    con.executemany(
        """
        INSERT INTO player_scheme_fits (
            player_id, season, scheme_key, side, current_fit, growth_fit,
            fit_rank, is_best_fit, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(player_id, season, scheme_key) DO UPDATE SET
            side = excluded.side,
            current_fit = excluded.current_fit,
            growth_fit = excluded.growth_fit,
            fit_rank = excluded.fit_rank,
            is_best_fit = excluded.is_best_fit,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        rows,
    )
    return len(rows)


def seed_all(con: sqlite3.Connection, *, season: int, dry_run: bool = False) -> dict[str, int]:
    seed_master_data(con)
    if dry_run:
        coach_count = len(coach_fit_rows(con, season))
        return {
            "schemes": len(SCHEMES),
            "coach_fits": coach_count,
            "teams": con.execute("SELECT COUNT(*) FROM teams").fetchone()[0],
            "player_fits": len(player_scheme_rows(con, season)),
        }
    con.execute("DELETE FROM coach_scheme_fits WHERE season = ?", (season,))
    con.execute("DELETE FROM team_scheme_identities WHERE season = ?", (season,))
    con.execute("DELETE FROM player_scheme_fits WHERE season = ?", (season,))
    coach_count = seed_coach_scheme_fits(con, season)
    team_count = seed_team_identities(con, season)
    player_count = seed_player_scheme_fits(con, season)
    return {
        "schemes": len(SCHEMES),
        "coach_fits": coach_count,
        "teams": team_count,
        "player_fits": player_count,
    }


def action_setup(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        seed_master_data(con)
        con.commit()
    print(f"Scheme definitions: {len(SCHEMES)}")


def action_seed(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        result = seed_all(con, season=args.season, dry_run=not args.apply)
        if args.apply:
            con.commit()
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"Season: {args.season}")
    print(f"Scheme definitions: {result['schemes']}")
    print(f"Coach scheme fits: {result['coach_fits']}")
    print(f"Team scheme identities: {result['teams']}")
    print(f"Player scheme fit rows: {result['player_fits']}")


def action_summary(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        seed_master_data(con)
        filters = ["season = ?"]
        params: list[Any] = [args.season]
        if args.team:
            filters.append("team = ?")
            params.append(args.team.upper())
        teams = con.execute(
            f"""
            SELECT *
            FROM team_scheme_identities_view
            WHERE {' AND '.join(filters)}
            ORDER BY team
            """,
            params,
        ).fetchall()
        top_fits = con.execute(
            """
            SELECT current_scheme_key, current_scheme, COUNT(*) AS players,
                   ROUND(AVG(current_fit), 1) AS avg_current_fit,
                   ROUND(AVG(growth_fit), 1) AS avg_growth_fit
            FROM current_player_scheme_fit_view
            WHERE season = ?
            GROUP BY current_scheme_key, current_scheme
            ORDER BY current_scheme
            """,
            (args.season,),
        ).fetchall()
    if not teams:
        print("No team schemes found. Run seed --apply first.")
        return
    for row in teams:
        print(
            f"{row['team']:<3} offense {row['offense_scheme']:<24} ({row['offensive_confidence']:>2}) | "
            f"defense {row['defense_scheme']:<28} ({row['defensive_confidence']:>2})"
        )
    if args.team:
        return
    print()
    print("Current roster fit by team scheme:")
    for row in top_fits:
        print(
            f"{row['current_scheme']:<28} players {row['players']:>4} | "
            f"current {row['avg_current_fit']:>5} | growth {row['avg_growth_fit']:>5}"
        )


def action_player(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        seed_master_data(con)
        filters = ["season = ?"]
        params: list[Any] = [args.season]
        if args.player_id:
            filters.append("player_id = ?")
            params.append(args.player_id)
        if args.player:
            filters.append("lower(player_name) LIKE ?")
            params.append(f"%{args.player.lower()}%")
        if args.team:
            filters.append("team = ?")
            params.append(args.team.upper())
        rows = con.execute(
            f"""
            SELECT *
            FROM player_scheme_fits_view
            WHERE {' AND '.join(filters)}
              AND fit_rank <= ?
            ORDER BY team, player_name, side, fit_rank
            LIMIT ?
            """,
            (*params, args.top, args.limit),
        ).fetchall()
        current_rows = con.execute(
            """
            SELECT *
            FROM current_player_scheme_fit_view
            WHERE season = ?
              AND (? IS NULL OR player_id = ?)
              AND (? IS NULL OR lower(player_name) LIKE ?)
              AND (? IS NULL OR team = ?)
            ORDER BY team, player_name
            LIMIT ?
            """,
            (
                args.season,
                args.player_id,
                args.player_id,
                args.player.lower() if args.player else None,
                f"%{args.player.lower()}%" if args.player else None,
                args.team.upper() if args.team else None,
                args.team.upper() if args.team else None,
                args.limit,
            ),
        ).fetchall()
    if current_rows:
        print("Current team scheme fit:")
        for row in current_rows:
            print(
                f"{row['team']:<3} {row['player_name']:<24} {row['position']:<4} "
                f"{row['current_scheme']:<28} fit {row['current_fit']:>2} growth {row['growth_fit']:>2}"
            )
        print()
    if not rows:
        print("No player scheme fits found.")
        return
    print("Best natural scheme fits:")
    for row in rows:
        print(
            f"{row['team']:<3} {row['player_name']:<24} {row['position']:<4} "
            f"#{row['fit_rank']} {row['scheme']:<28} current {row['current_fit']:>2} growth {row['growth_fit']:>2}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed and inspect coach/team/player scheme fits.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Create scheme tables and seed scheme definitions.")
    setup_parser.set_defaults(func=action_setup)

    seed_parser = subparsers.add_parser("seed", help="Seed coach, team, and player scheme fits.")
    seed_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    seed_parser.add_argument("--apply", action="store_true", help="Persist changes. Omit for dry run.")
    seed_parser.set_defaults(func=action_seed)

    summary_parser = subparsers.add_parser("summary", help="Show team scheme identities.")
    summary_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    summary_parser.add_argument("--team")
    summary_parser.set_defaults(func=action_summary)

    player_parser = subparsers.add_parser("player", help="Show a player's scheme fits.")
    player_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    player_parser.add_argument("--player-id", type=int)
    player_parser.add_argument("--player")
    player_parser.add_argument("--team")
    player_parser.add_argument("--top", type=int, default=3)
    player_parser.add_argument("--limit", type=int, default=40)
    player_parser.set_defaults(func=action_player)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
