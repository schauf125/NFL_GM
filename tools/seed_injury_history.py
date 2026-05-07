#!/usr/bin/env python3
"""Seed approximate real-world injury history for prominent players.

The goal is an FM-style medical baseline, not a perfect medical record. The
script uses curated public-profile memories for well-known players and fills
the rest of the top-player set with deterministic medical variance driven by
durability, age, position, and body type.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import injury_model  # noqa: E402


SOURCE_CURATED = "approx_public_injury_history_v1"
SOURCE_GENERATED = "estimated_medical_variance_v1"
SOURCES = (SOURCE_CURATED, SOURCE_GENERATED)


@dataclass(frozen=True)
class InjurySeed:
    injury_code: str
    start_date: str
    expected_days: int | None = None
    games_missed: int | None = None
    notes: str = ""


@dataclass(frozen=True)
class PlayerSeedResult:
    player_id: int
    player_name: str
    team: str
    curated_rows: int
    generated_rows: int


CURATED_INJURIES: dict[str, tuple[InjurySeed, ...]] = {
    "joe burrow": (
        InjurySeed("acl_tear", "2020-11-22", 300, 6, "Rookie-year left knee ACL/MCL reconstruction profile."),
        InjurySeed("calf_strain", "2023-07-27", 38, 1, "Training-camp calf strain that affected early-season mobility."),
        InjurySeed("wrist_hand_injury", "2023-11-16", 175, 7, "Throwing-wrist ligament injury profile."),
    ),
    "christian mccaffrey": (
        InjurySeed("high_ankle_sprain", "2020-09-20", 42, 6, "High-ankle history from Carolina workload years."),
        InjurySeed("hamstring_strain", "2021-09-23", 42, 5, "Recurring lower-body soft-tissue history."),
        InjurySeed("calf_strain", "2024-09-01", 72, 8, "Calf/Achilles-area availability concern seed."),
    ),
    "nick bosa": (
        InjurySeed("acl_tear", "2020-09-20", 295, 14, "Major knee injury history."),
    ),
    "saquon barkley": (
        InjurySeed("acl_tear", "2020-09-20", 310, 14, "Major right knee injury history."),
        InjurySeed("ankle_sprain", "2021-10-10", 31, 4, "Recurring ankle availability marker."),
        InjurySeed("high_ankle_sprain", "2023-09-17", 21, 3, "Recent ankle sprain marker."),
    ),
    "lamar jackson": (
        InjurySeed("ankle_sprain", "2021-12-12", 28, 4, "Lower-body missed-time history."),
        InjurySeed("knee_sprain", "2022-12-04", 42, 5, "Late-season knee sprain history."),
    ),
    "matthew stafford": (
        InjurySeed("back_spasm", "2019-11-10", 45, 8, "Veteran back history."),
        InjurySeed("elbow_sprain", "2022-08-01", 42, 0, "Throwing-arm management marker."),
        InjurySeed("concussion", "2022-11-13", 21, 2, "Head/neck missed-time marker."),
    ),
    "tua tagovailoa": (
        InjurySeed("concussion", "2022-09-29", 24, 2, "Documented concussion history marker."),
        InjurySeed("concussion", "2022-12-25", 28, 3, "Recurring head/neck history marker."),
        InjurySeed("concussion", "2024-09-12", 35, 4, "Recent head/neck availability risk marker."),
        InjurySeed("rib_injury", "2021-09-19", 24, 3, "Torso impact history."),
    ),
    "dak prescott": (
        InjurySeed("foot_fracture", "2020-10-11", 165, 11, "Major ankle/lower-leg fracture profile represented as foot/ankle history."),
        InjurySeed("wrist_hand_injury", "2021-10-31", 18, 1, "Calf/hand era availability marker."),
        InjurySeed("hamstring_strain", "2024-11-03", 70, 8, "Late-season soft-tissue absence profile."),
    ),
    "kirk cousins": (
        InjurySeed("achilles_tear", "2023-10-29", 285, 9, "Achilles rupture history."),
    ),
    "aaron rodgers": (
        InjurySeed("achilles_tear", "2023-09-11", 260, 16, "Achilles rupture history."),
        InjurySeed("calf_strain", "2022-12-01", 21, 0, "Late-career lower-leg management marker."),
    ),
    "justin herbert": (
        InjurySeed("rib_injury", "2022-09-15", 24, 0, "Rib cartilage impact history."),
        InjurySeed("wrist_hand_injury", "2023-12-10", 70, 4, "Finger/hand injury profile."),
        InjurySeed("ankle_sprain", "2024-09-15", 17, 1, "Recent ankle management marker."),
    ),
    "trevor lawrence": (
        InjurySeed("knee_sprain", "2023-10-15", 14, 0, "Knee sprain management marker."),
        InjurySeed("ankle_sprain", "2023-12-04", 21, 1, "Late-season ankle sprain."),
        InjurySeed("concussion", "2023-12-17", 14, 1, "Head/neck marker."),
        InjurySeed("shoulder_sprain", "2024-11-03", 45, 6, "Shoulder availability marker."),
    ),
    "justin jefferson": (
        InjurySeed("hamstring_strain", "2023-10-08", 52, 7, "Major hamstring absence profile."),
        InjurySeed("chest_rib_injury", "2023-12-10", 7, 0, "Chest/rib scare represented as torso risk."),
    ),
    "ceedee lamb": (
        InjurySeed("shoulder_sprain", "2024-12-22", 28, 1, "Late-season shoulder sprain marker."),
    ),
    "ja'marr chase": (
        InjurySeed("groin_strain", "2022-10-23", 36, 4, "Hip/groin absence represented as groin risk."),
        InjurySeed("shoulder_sprain", "2023-12-17", 14, 1, "Shoulder sprain marker."),
    ),
    "jamar chase": (
        InjurySeed("groin_strain", "2022-10-23", 36, 4, "Hip/groin absence represented as groin risk."),
        InjurySeed("shoulder_sprain", "2023-12-17", 14, 1, "Shoulder sprain marker."),
    ),
    "cooper kupp": (
        InjurySeed("acl_tear", "2018-11-11", 285, 8, "Major knee injury history."),
        InjurySeed("high_ankle_sprain", "2022-11-13", 70, 8, "High-ankle surgery profile."),
        InjurySeed("hamstring_strain", "2023-08-01", 45, 4, "Recurring soft-tissue marker."),
    ),
    "deebo samuel": (
        InjurySeed("hamstring_strain", "2020-12-13", 24, 3, "Recurring soft-tissue marker."),
        InjurySeed("ankle_sprain", "2022-12-11", 21, 3, "Ankle sprain marker."),
        InjurySeed("calf_strain", "2024-09-15", 17, 1, "Recent calf strain marker."),
    ),
    "a.j. brown": (
        InjurySeed("hamstring_strain", "2021-09-26", 21, 1, "Soft-tissue history marker."),
        InjurySeed("knee_sprain", "2021-11-21", 24, 3, "Knee sprain marker."),
        InjurySeed("hamstring_strain", "2024-09-15", 24, 3, "Recent hamstring marker."),
    ),
    "aj brown": (
        InjurySeed("hamstring_strain", "2021-09-26", 21, 1, "Soft-tissue history marker."),
        InjurySeed("knee_sprain", "2021-11-21", 24, 3, "Knee sprain marker."),
        InjurySeed("hamstring_strain", "2024-09-15", 24, 3, "Recent hamstring marker."),
    ),
    "puka nacua": (
        InjurySeed("knee_sprain", "2024-09-08", 35, 5, "Recent knee sprain absence marker."),
    ),
    "tyreek hill": (
        InjurySeed("ankle_sprain", "2023-12-11", 14, 1, "Ankle sprain marker."),
        InjurySeed("wrist_hand_injury", "2024-09-01", 56, 0, "Wrist management marker."),
    ),
    "mike evans": (
        InjurySeed("hamstring_strain", "2019-12-08", 24, 3, "Historical hamstring marker."),
        InjurySeed("hamstring_strain", "2024-10-21", 24, 3, "Recent hamstring marker."),
    ),
    "chris godwin": (
        InjurySeed("acl_tear", "2021-12-19", 260, 3, "Major knee injury history."),
        InjurySeed("ankle_sprain", "2024-10-21", 120, 10, "Major ankle/lower-leg absence marker."),
    ),
    "christian watson": (
        InjurySeed("hamstring_strain", "2022-08-01", 21, 1, "Recurring hamstring marker."),
        InjurySeed("hamstring_strain", "2023-08-31", 45, 5, "Recurring hamstring marker."),
        InjurySeed("acl_tear", "2025-01-05", 285, 0, "Major knee injury profile seed."),
    ),
    "tank dell": (
        InjurySeed("foot_fracture", "2023-12-03", 120, 6, "Rookie-season leg/foot fracture marker."),
        InjurySeed("knee_sprain", "2024-12-21", 180, 0, "Major knee/lower-leg injury profile seed."),
    ),
    "malik nabers": (
        InjurySeed("concussion", "2024-09-26", 14, 2, "Rookie-year concussion marker."),
        InjurySeed("groin_strain", "2024-08-01", 14, 0, "Camp soft-tissue marker."),
    ),
    "george kittle": (
        InjurySeed("knee_sprain", "2020-09-13", 14, 2, "Knee sprain marker."),
        InjurySeed("foot_sprain", "2020-11-01", 56, 6, "Foot fracture/sprain absence profile."),
        InjurySeed("calf_strain", "2021-10-03", 28, 3, "Calf strain marker."),
    ),
    "travis kelce": (
        InjurySeed("knee_sprain", "2023-09-05", 10, 1, "Knee hyperextension marker."),
        InjurySeed("back_spasm", "2024-12-25", 10, 0, "Late-career back management marker."),
    ),
    "mark andrews": (
        InjurySeed("ankle_sprain", "2023-11-16", 56, 6, "Major ankle/lower-leg absence marker."),
    ),
    "t.j. hockenson": (
        InjurySeed("acl_tear", "2023-12-24", 285, 7, "Major knee injury history."),
        InjurySeed("mcl_sprain", "2023-12-24", 90, 7, "Associated knee ligament marker."),
    ),
    "tj hockenson": (
        InjurySeed("acl_tear", "2023-12-24", 285, 7, "Major knee injury history."),
        InjurySeed("mcl_sprain", "2023-12-24", 90, 7, "Associated knee ligament marker."),
    ),
    "dallas goedert": (
        InjurySeed("shoulder_sprain", "2022-11-14", 35, 5, "Shoulder injury marker."),
        InjurySeed("wrist_hand_injury", "2023-11-05", 28, 3, "Forearm/hand absence marker."),
        InjurySeed("knee_sprain", "2024-12-01", 28, 4, "Late-season knee sprain marker."),
    ),
    "jonathan taylor": (
        InjurySeed("high_ankle_sprain", "2022-10-02", 70, 6, "Recurring ankle marker."),
        InjurySeed("ankle_sprain", "2023-08-29", 28, 4, "Ankle recovery marker."),
        InjurySeed("thumb_hand_injury", "2023-11-26", 28, 3, "Thumb injury represented as hand risk."),
    ),
    "derrick henry": (
        InjurySeed("foot_fracture", "2021-10-31", 70, 9, "Jones fracture foot history."),
    ),
    "breece hall": (
        InjurySeed("acl_tear", "2022-10-23", 285, 10, "Major knee injury history."),
    ),
    "jahmyr gibbs": (
        InjurySeed("hamstring_strain", "2024-08-12", 21, 0, "Camp hamstring marker."),
    ),
    "bijan robinson": (
        InjurySeed("hamstring_strain", "2023-10-01", 10, 0, "Minor soft-tissue marker."),
    ),
    "josh jacobs": (
        InjurySeed("quad_strain", "2021-09-13", 18, 1, "Lower-body strain marker."),
        InjurySeed("knee_sprain", "2023-12-10", 28, 4, "Late-season knee absence marker."),
    ),
    "nick chubb": (
        InjurySeed("acl_tear", "2015-10-10", 300, 0, "College major knee history carried forward as recurring area risk."),
        InjurySeed("mcl_sprain", "2023-09-18", 285, 15, "Major knee reconstruction profile."),
    ),
    "travis etienne jr.": (
        InjurySeed("foot_fracture", "2021-08-23", 300, 17, "Lisfranc/foot injury history."),
    ),
    "travis etienne": (
        InjurySeed("foot_fracture", "2021-08-23", 300, 17, "Lisfranc/foot injury history."),
    ),
    "christian darrisaw": (
        InjurySeed("acl_tear", "2024-10-24", 285, 10, "Major knee injury history."),
        InjurySeed("knee_sprain", "2022-11-20", 21, 2, "Earlier knee/leg availability marker."),
    ),
    "rashawn slater": (
        InjurySeed("pectoral_tear", "2022-09-25", 150, 14, "Major upper-body injury history."),
    ),
    "lane johnson": (
        InjurySeed("ankle_sprain", "2020-11-22", 40, 5, "Ankle history marker."),
        InjurySeed("groin_strain", "2022-12-24", 21, 2, "Core/groin management marker."),
    ),
    "trent williams": (
        InjurySeed("ankle_sprain", "2022-10-23", 28, 3, "Veteran ankle history marker."),
        InjurySeed("back_spasm", "2024-11-17", 28, 2, "Late-career back/ankle management marker."),
    ),
    "tristan wirfs": (
        InjurySeed("ankle_sprain", "2022-11-27", 24, 3, "Ankle sprain marker."),
        InjurySeed("knee_sprain", "2024-11-10", 28, 4, "Knee sprain marker."),
    ),
    "ronnie stanley": (
        InjurySeed("ankle_sprain", "2020-11-01", 330, 8, "Severe ankle injury profile."),
        InjurySeed("ankle_sprain", "2021-09-19", 250, 16, "Recurring ankle surgery/recovery marker."),
    ),
    "tyron smith": (
        InjurySeed("neck_stinger", "2020-10-11", 120, 14, "Neck/upper-body history marker."),
        InjurySeed("knee_sprain", "2022-08-24", 120, 13, "Major leg injury profile."),
    ),
    "marlon humphrey": (
        InjurySeed("pectoral_tear", "2021-12-05", 120, 5, "Pectoral tear marker."),
        InjurySeed("foot_sprain", "2023-08-16", 42, 4, "Foot surgery/recovery marker."),
    ),
    "derwin james jr.": (
        InjurySeed("foot_fracture", "2019-08-15", 90, 11, "Foot stress fracture marker."),
        InjurySeed("meniscus_injury", "2020-08-30", 300, 16, "Major knee history."),
        InjurySeed("hamstring_strain", "2022-12-18", 21, 2, "Soft-tissue marker."),
    ),
    "derwin james": (
        InjurySeed("foot_fracture", "2019-08-15", 90, 11, "Foot stress fracture marker."),
        InjurySeed("meniscus_injury", "2020-08-30", 300, 16, "Major knee history."),
    ),
    "trevon diggs": (
        InjurySeed("acl_tear", "2023-09-21", 285, 15, "Major knee injury history."),
    ),
    "jalen ramsey": (
        InjurySeed("meniscus_injury", "2023-07-27", 70, 7, "Meniscus repair marker."),
        InjurySeed("shoulder_sprain", "2021-12-01", 21, 0, "Shoulder management marker."),
    ),
    "denzel ward": (
        InjurySeed("concussion", "2018-11-11", 14, 2, "Head/neck history marker."),
        InjurySeed("concussion", "2022-10-16", 21, 3, "Recurring head/neck marker."),
        InjurySeed("concussion", "2023-11-19", 14, 1, "Recent head/neck marker."),
    ),
    "jaire alexander": (
        InjurySeed("shoulder_sprain", "2021-10-03", 70, 10, "Shoulder/AC joint history marker."),
        InjurySeed("knee_sprain", "2023-11-05", 42, 6, "Knee sprain marker."),
    ),
    "marshon lattimore": (
        InjurySeed("abdomen_strain", "2022-10-09", 70, 10, "Abdomen/core injury represented as groin/torso risk."),
        InjurySeed("hamstring_strain", "2023-11-12", 42, 7, "Hamstring marker."),
    ),
    "sauce gardener": (),
    "sauce gardner": (
        InjurySeed("hamstring_strain", "2023-12-10", 10, 1, "Minor soft-tissue marker."),
    ),
    "jeffery simmons": (
        InjurySeed("acl_tear", "2019-02-12", 250, 0, "Pre-draft ACL history carried forward as knee risk."),
        InjurySeed("ankle_sprain", "2023-12-03", 21, 2, "Ankle marker."),
    ),
    "chris jones": (
        InjurySeed("groin_strain", "2020-01-12", 14, 0, "Groin strain marker."),
        InjurySeed("wrist_hand_injury", "2023-09-01", 14, 0, "Hand/wrist management marker."),
    ),
    "myles garrett": (
        InjurySeed("shoulder_sprain", "2022-09-25", 24, 1, "Shoulder/biceps car crash aftermath marker."),
        InjurySeed("foot_sprain", "2024-09-22", 21, 0, "Foot management marker."),
    ),
    "micah parsons": (
        InjurySeed("ankle_sprain", "2024-09-26", 28, 4, "High-ankle style absence marker."),
    ),
    "maxx crosby": (
        InjurySeed("ankle_sprain", "2024-09-22", 56, 5, "Ankle injury/surgery profile marker."),
    ),
    "fred warner": (
        InjurySeed("ankle_sprain", "2024-10-10", 14, 0, "Ankle management marker."),
    ),
    "kyle hamilton": (
        InjurySeed("knee_sprain", "2023-12-17", 14, 1, "Knee sprain marker."),
    ),
    "t.j. watt": (
        InjurySeed("pectoral_tear", "2022-09-11", 70, 7, "Pectoral tear marker."),
        InjurySeed("knee_sprain", "2023-01-08", 21, 0, "Knee sprain marker."),
    ),
    "tj watt": (
        InjurySeed("pectoral_tear", "2022-09-11", 70, 7, "Pectoral tear marker."),
    ),
}


ALIASES = {
    "de'von achane": "devon achane",
    "kenneth walker iii": "kenneth walker",
    "marvin harrison jr.": "marvin harrison jr",
    "marvin harrison jr": "marvin harrison jr",
    "patrick surtain ii": "patrick surtain",
    "odell beckham jr.": "odell beckham jr",
    "aj brown": "a.j. brown",
    "tj watt": "t.j. watt",
    "tj hockenson": "t.j. hockenson",
}


CODE_ALIASES = {
    "chest_rib_injury": "rib_injury",
    "thumb_hand_injury": "wrist_hand_injury",
    "abdomen_strain": "groin_strain",
}


POSITION_BODY_WEIGHTS = {
    "QB": [("shoulder_sprain", 6), ("rib_injury", 5), ("ankle_sprain", 4), ("concussion", 4), ("elbow_sprain", 3), ("wrist_hand_injury", 3)],
    "RB": [("ankle_sprain", 7), ("hamstring_strain", 6), ("knee_sprain", 5), ("high_ankle_sprain", 4), ("concussion", 2), ("shoulder_sprain", 2)],
    "FB": [("ankle_sprain", 6), ("shoulder_sprain", 5), ("concussion", 4), ("knee_sprain", 4), ("hamstring_strain", 3)],
    "WR": [("hamstring_strain", 8), ("ankle_sprain", 6), ("high_ankle_sprain", 3), ("knee_sprain", 3), ("concussion", 3), ("shoulder_sprain", 2)],
    "TE": [("ankle_sprain", 6), ("knee_sprain", 5), ("shoulder_sprain", 5), ("concussion", 3), ("hamstring_strain", 3), ("rib_injury", 2)],
    "OT": [("knee_sprain", 6), ("ankle_sprain", 5), ("pectoral_strain", 4), ("back_spasm", 4), ("shoulder_sprain", 3)],
    "OG": [("knee_sprain", 6), ("ankle_sprain", 5), ("pectoral_strain", 4), ("back_spasm", 4), ("shoulder_sprain", 3)],
    "C": [("knee_sprain", 5), ("ankle_sprain", 5), ("pectoral_strain", 4), ("back_spasm", 4), ("shoulder_sprain", 3)],
    "EDGE": [("ankle_sprain", 6), ("knee_sprain", 5), ("shoulder_sprain", 5), ("pectoral_strain", 4), ("concussion", 2)],
    "IDL": [("knee_sprain", 6), ("ankle_sprain", 5), ("shoulder_sprain", 5), ("pectoral_strain", 4), ("back_spasm", 3)],
    "DT": [("knee_sprain", 6), ("ankle_sprain", 5), ("shoulder_sprain", 5), ("pectoral_strain", 4), ("back_spasm", 3)],
    "NT": [("knee_sprain", 6), ("ankle_sprain", 5), ("shoulder_sprain", 5), ("pectoral_strain", 4), ("back_spasm", 3)],
    "LB": [("ankle_sprain", 6), ("hamstring_strain", 4), ("shoulder_sprain", 5), ("knee_sprain", 4), ("concussion", 4)],
    "ILB": [("ankle_sprain", 6), ("hamstring_strain", 4), ("shoulder_sprain", 5), ("knee_sprain", 4), ("concussion", 4)],
    "OLB": [("ankle_sprain", 6), ("hamstring_strain", 4), ("shoulder_sprain", 5), ("knee_sprain", 4), ("concussion", 4)],
    "CB": [("hamstring_strain", 7), ("ankle_sprain", 6), ("groin_strain", 4), ("concussion", 3), ("knee_sprain", 3)],
    "S": [("hamstring_strain", 5), ("ankle_sprain", 6), ("shoulder_sprain", 5), ("concussion", 5), ("knee_sprain", 3)],
    "FS": [("hamstring_strain", 5), ("ankle_sprain", 6), ("shoulder_sprain", 5), ("concussion", 5), ("knee_sprain", 3)],
    "SS": [("hamstring_strain", 5), ("ankle_sprain", 6), ("shoulder_sprain", 5), ("concussion", 5), ("knee_sprain", 3)],
    "K": [("quad_strain", 7), ("groin_strain", 4), ("ankle_sprain", 3)],
    "P": [("quad_strain", 5), ("groin_strain", 4), ("ankle_sprain", 3)],
    "LS": [("shoulder_sprain", 4), ("back_spasm", 4), ("knee_sprain", 3)],
}


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def normalize_name(name: str) -> str:
    clean = " ".join(name.lower().replace("%", "'").replace("’", "'").split())
    return ALIASES.get(clean, clean)


def ensure_schema(con: sqlite3.Connection) -> None:
    injury_model.ensure_schema(con)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS injury_history_seed_runs (
            seed_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            season INTEGER NOT NULL,
            limit_count INTEGER NOT NULL,
            rng_seed INTEGER NOT NULL,
            source TEXT NOT NULL,
            curated_rows INTEGER NOT NULL DEFAULT 0,
            generated_rows INTEGER NOT NULL DEFAULT 0,
            players_touched INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def catalog(con: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {
        row["injury_code"]: row
        for row in con.execute("SELECT * FROM injury_catalog").fetchall()
    }


def top_players(con: sqlite3.Connection, season: int, limit: int) -> list[sqlite3.Row]:
    return con.execute(
        """
        WITH role AS (
            SELECT player_id, MAX(role_score) AS top_role_score
            FROM player_role_scores
            WHERE season = ?
            GROUP BY player_id
        ),
        durability AS (
            SELECT player_id, rating_value AS durability
            FROM player_ratings
            WHERE season = ? AND rating_key = 'durability'
        )
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            p.age,
            p.weight_lbs,
            p.overall,
            p.injury_prone,
            t.abbreviation AS team,
            COALESCE(role.top_role_score, p.overall, 50) AS top_role_score,
            COALESCE(durability.durability, p.injury_prone, 60) AS durability
        FROM players p
        JOIN teams t ON t.team_id = p.team_id
        LEFT JOIN role ON role.player_id = p.player_id
        LEFT JOIN durability ON durability.player_id = p.player_id
        LEFT JOIN roster_status_types rst ON rst.status_code = p.status
        WHERE COALESCE(
                rst.counts_against_roster_limit,
                CASE WHEN COALESCE(p.status, 'Active') NOT IN ('Retired', 'Free Agent') THEN 1 ELSE 0 END
              ) = 1
        ORDER BY COALESCE(role.top_role_score, p.overall, 50) DESC, p.overall DESC, p.player_id
        LIMIT ?
        """,
        (season, season, limit),
    ).fetchall()


def weighted_choice(rng: random.Random, items: list[tuple[str, float]]) -> str:
    total = sum(max(0.01, weight) for _item, weight in items)
    roll = rng.random() * total
    cursor = 0.0
    for item, weight in items:
        cursor += max(0.01, weight)
        if roll <= cursor:
            return item
    return items[-1][0]


def generated_count(rng: random.Random, player: sqlite3.Row) -> int:
    durability = float(player["durability"] or 60)
    age = int(player["age"] or 26)
    position = player["position"]
    score = 0.0
    score += max(0.0, 72.0 - durability) / 13.0
    score += max(0, age - 29) * 0.12
    if position in {"RB", "FB", "WR", "TE", "CB", "S", "FS", "SS"}:
        score += 0.22
    if position in {"OT", "OG", "C", "IDL", "DT", "NT", "EDGE"} and age >= 30:
        score += 0.18
    if durability >= 72:
        thresholds = (0.15, 0.02, 0.0)
    elif durability >= 64:
        thresholds = (0.42, 0.09, 0.01)
    elif durability >= 56:
        thresholds = (0.78, 0.30, 0.05)
    else:
        thresholds = (0.95, 0.58, 0.14)
    count = 0
    roll_bias = min(0.22, score * 0.025)
    for threshold in thresholds:
        if rng.random() < min(0.98, threshold + roll_bias):
            count += 1
    return count


def generated_seed(rng: random.Random, player: sqlite3.Row, idx: int) -> InjurySeed:
    position = player["position"]
    options = POSITION_BODY_WEIGHTS.get(position, POSITION_BODY_WEIGHTS["LB"])
    code = weighted_choice(rng, options)
    item = next(c for c in injury_model.INJURY_CATALOG if c.injury_code == code)
    age = int(player["age"] or 26)
    durability = float(player["durability"] or 60)
    year_max = 2025
    years_back = min(9, max(1, int((age - 21) * rng.uniform(0.45, 1.05))))
    year = max(2017, year_max - rng.randint(0, max(1, years_back)))
    month = rng.choice([8, 9, 9, 10, 10, 11, 11, 12])
    day = rng.randint(1, 24)
    base_days = rng.randint(int(item.min_days), int(item.max_days))
    if item.severity_bucket == "major":
        base_days = max(base_days, rng.randint(90, int(item.max_days)))
    elif durability < 58 and rng.random() < 0.18:
        base_days = int(base_days * rng.uniform(1.25, 1.75))
    expected_days = max(3, min(365, base_days))
    games_missed = 0 if expected_days <= 10 and rng.random() < 0.45 else max(1, round(expected_days / 8.5))
    if idx >= 1 and item.severity_bucket != "major":
        expected_days = max(5, int(expected_days * rng.uniform(0.65, 1.05)))
    return InjurySeed(
        code,
        f"{year:04d}-{month:02d}-{day:02d}",
        expected_days,
        int(games_missed),
        "Estimated medical-variance seed from durability, age, position, and role prominence.",
    )


def resolve_seed(seed: InjurySeed, player: sqlite3.Row, cat: dict[str, sqlite3.Row]) -> dict[str, Any]:
    code = CODE_ALIASES.get(seed.injury_code, seed.injury_code)
    if code not in cat:
        raise ValueError(f"Unknown injury code {seed.injury_code!r} for {player['player_name']}.")
    entry = cat[code]
    expected_days = int(seed.expected_days if seed.expected_days is not None else max(entry["min_days"], 7))
    games_missed = int(seed.games_missed if seed.games_missed is not None else max(0, round(expected_days / 8.5)))
    start = date.fromisoformat(seed.start_date)
    resolved = start + timedelta(days=expected_days)
    return {
        "player_id": int(player["player_id"]),
        "injury_code": code,
        "injury_label": entry["label"],
        "body_region": entry["body_region"],
        "body_part": entry["body_part"],
        "severity": "severe" if entry["severity_bucket"] == "major" and expected_days >= 180 else entry["severity_bucket"],
        "start_date": start.isoformat(),
        "resolved_date": resolved.isoformat(),
        "expected_days": expected_days,
        "games_missed": games_missed,
        "recurrence_risk": float(entry["recurrence_risk"]),
        "notes": seed.notes,
    }


def delete_existing_seed_rows(con: sqlite3.Connection) -> None:
    placeholders = ",".join("?" for _ in SOURCES)
    con.execute(f"DELETE FROM player_injury_history WHERE source IN ({placeholders})", SOURCES)


def insert_history(con: sqlite3.Connection, row: dict[str, Any], source: str, seed_run_id: int | None, dry_run: bool) -> None:
    if dry_run:
        return
    con.execute(
        """
        INSERT INTO player_injury_history (
            player_id, injury_code, injury_label, body_region, body_part, severity,
            start_date, resolved_date, expected_days, games_missed, recurrence_risk,
            source, source_run_id, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["player_id"],
            row["injury_code"],
            row["injury_label"],
            row["body_region"],
            row["body_part"],
            row["severity"],
            row["start_date"],
            row["resolved_date"],
            row["expected_days"],
            row["games_missed"],
            row["recurrence_risk"],
            source,
            seed_run_id,
            row["notes"],
        ),
    )


def seed_history(
    con: sqlite3.Connection,
    *,
    season: int,
    limit: int,
    seed: int,
    force: bool,
    dry_run: bool,
) -> tuple[list[PlayerSeedResult], int, int]:
    ensure_schema(con)
    existing = con.execute(
        f"SELECT COUNT(*) AS count FROM player_injury_history WHERE source IN ({','.join('?' for _ in SOURCES)})",
        SOURCES,
    ).fetchone()
    if int(existing["count"] or 0) and not force:
        raise ValueError("Seeded injury history already exists. Use --force to replace rows from this seed source.")
    if force and not dry_run:
        delete_existing_seed_rows(con)
    rng = random.Random(seed)
    cat = catalog(con)
    players = top_players(con, season, limit)
    seen_player_ids = {int(player["player_id"]) for player in players}
    for player in top_players(con, season, 5000):
        name_key = normalize_name(player["player_name"])
        if name_key not in CURATED_INJURIES or not CURATED_INJURIES[name_key]:
            continue
        player_id = int(player["player_id"])
        if player_id in seen_player_ids:
            continue
        players.append(player)
        seen_player_ids.add(player_id)
    if dry_run:
        seed_run_id = None
    else:
        cur = con.execute(
            """
            INSERT INTO injury_history_seed_runs (season, limit_count, rng_seed, source, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                season,
                limit,
                seed,
                "top_player_medical_seed_v1",
                "Approximate public-profile and deterministic generated injury history seed.",
            ),
        )
        seed_run_id = int(cur.lastrowid)

    results: list[PlayerSeedResult] = []
    total_curated = 0
    total_generated = 0
    for player in players:
        name_key = normalize_name(player["player_name"])
        curated = list(CURATED_INJURIES.get(name_key, ()))
        for seed_item in curated:
            row = resolve_seed(seed_item, player, cat)
            insert_history(con, row, SOURCE_CURATED, seed_run_id, dry_run)
        generated_target = generated_count(rng, player)
        if curated and float(player["durability"] or 60) >= 58:
            generated_target = max(0, generated_target - 1)
        generated = [generated_seed(rng, player, idx) for idx in range(generated_target)]
        seen_parts = {cat[CODE_ALIASES.get(item.injury_code, item.injury_code)]["body_part"] for item in curated if CODE_ALIASES.get(item.injury_code, item.injury_code) in cat}
        generated_rows = 0
        for idx, seed_item in enumerate(generated):
            row = resolve_seed(seed_item, player, cat)
            if row["body_part"] in seen_parts and idx > 0 and rng.random() < 0.55:
                continue
            seen_parts.add(row["body_part"])
            insert_history(con, row, SOURCE_GENERATED, seed_run_id, dry_run)
            generated_rows += 1
        curated_rows = len(curated)
        total_curated += curated_rows
        total_generated += generated_rows
        if curated_rows or generated_rows:
            results.append(
                PlayerSeedResult(
                    player_id=int(player["player_id"]),
                    player_name=player["player_name"],
                    team=player["team"],
                    curated_rows=curated_rows,
                    generated_rows=generated_rows,
                )
            )

    if seed_run_id is not None:
        con.execute(
            """
            UPDATE injury_history_seed_runs
            SET curated_rows = ?,
                generated_rows = ?,
                players_touched = ?
            WHERE seed_run_id = ?
            """,
            (total_curated, total_generated, len(results), seed_run_id),
        )
    return results, total_curated, total_generated


def print_results(results: list[PlayerSeedResult], curated: int, generated: int) -> None:
    print("Injury history seed complete.")
    print(f"  Players touched: {len(results)}")
    print(f"  Curated rows: {curated}")
    print(f"  Estimated rows: {generated}")
    print("  Sample:")
    for item in results[:20]:
        print(f"    {item.player_name} ({item.team}): {item.curated_rows} curated, {item.generated_rows} estimated")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed approximate injury history for top players.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260505)
    parser.add_argument("--force", action="store_true", help="Replace previous rows from this seed source.")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    con = connect(args.db)
    try:
        results, curated, generated = seed_history(
            con,
            season=args.season,
            limit=args.limit,
            seed=args.seed,
            force=args.force,
            dry_run=args.dry_run,
        )
        if args.dry_run:
            con.rollback()
        else:
            con.commit()
        print_results(results, curated, generated)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
