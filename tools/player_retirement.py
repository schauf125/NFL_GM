#!/usr/bin/env python3
"""Offseason retirement model for NFL GM Sim.

The model is intentionally probabilistic and auditable:
- position sets the rough age curve,
- a Gaussian target age gives players individual variance,
- high-quality and low-injury-history players usually last longer,
- fringe free agents can quietly retire earlier,
- medical retirement is rare and tied to severe active/history risk.
"""

from __future__ import annotations

import argparse
import math
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

import setup_contract_years  # noqa: E402
import setup_transactions_cap_ledger  # noqa: E402


MODEL_VERSION = "retirement_v1"
SOURCE = "player_retirement"

POSITION_AGE_CURVES = {
    "QB": (36.0, 3.4),
    "RB": (29.5, 2.2),
    "FB": (30.0, 2.3),
    "WR": (31.3, 2.5),
    "TE": (31.8, 2.6),
    "OT": (33.4, 2.7),
    "OG": (33.1, 2.6),
    "C": (33.4, 2.6),
    "EDGE": (32.6, 2.7),
    "IDL": (32.9, 2.7),
    "DT": (32.9, 2.7),
    "NT": (32.5, 2.7),
    "ILB": (31.5, 2.4),
    "OLB": (31.7, 2.5),
    "LB": (31.5, 2.4),
    "CB": (31.0, 2.4),
    "S": (31.8, 2.6),
    "FS": (31.8, 2.6),
    "SS": (31.8, 2.6),
    "K": (38.0, 3.7),
    "P": (38.0, 3.7),
    "LS": (36.5, 3.2),
}


@dataclass(frozen=True)
class RetirementCandidate:
    player_id: int
    team_id: int | None
    name: str
    position: str
    age: int
    years_exp: int
    status: str
    overall: float
    quality_score: float
    durability: float
    future_contract_years: int
    current_season_games: int
    current_snaps: int
    career_games: int
    injury_history_rows: int
    injury_games_missed: int
    major_injury_rows: int
    head_neck_rows: int
    active_injury_days: int
    active_injury_status: str | None


@dataclass(frozen=True)
class RetirementDecision:
    candidate: RetirementCandidate
    retired: bool
    decision_type: str
    probability: float
    roll: float
    target_age: float
    reason_code: str
    reason: str


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


def ensure_schema(con: sqlite3.Connection) -> None:
    setup_contract_years.ensure_schema(con)
    setup_transactions_cap_ledger.ensure_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS player_retirement_decisions (
            decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT,
            season INTEGER NOT NULL,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            decision_date TEXT NOT NULL,
            decision_type TEXT NOT NULL,
            retired INTEGER NOT NULL DEFAULT 0,
            probability REAL NOT NULL DEFAULT 0,
            roll REAL NOT NULL DEFAULT 0,
            target_age REAL NOT NULL DEFAULT 0,
            age INTEGER NOT NULL DEFAULT 0,
            years_exp INTEGER NOT NULL DEFAULT 0,
            quality_score REAL NOT NULL DEFAULT 0,
            durability REAL NOT NULL DEFAULT 0,
            career_games INTEGER NOT NULL DEFAULT 0,
            injury_games_missed INTEGER NOT NULL DEFAULT 0,
            active_injury_days INTEGER NOT NULL DEFAULT 0,
            reason_code TEXT NOT NULL,
            reason TEXT,
            model_version TEXT NOT NULL,
            rng_seed TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(game_id, season, player_id, model_version)
        );

        CREATE INDEX IF NOT EXISTS idx_player_retirement_decisions_season
            ON player_retirement_decisions(season, retired, decision_type);
        CREATE INDEX IF NOT EXISTS idx_player_retirement_decisions_player
            ON player_retirement_decisions(player_id, season);
        """
    )


def current_game_id(con: sqlite3.Connection) -> str | None:
    if not table_exists(con, "game_settings"):
        return None
    row = con.execute("SELECT setting_value FROM game_settings WHERE setting_key = 'active_game_id'").fetchone()
    return str(row["setting_value"]) if row and row["setting_value"] else None


def current_game_date(con: sqlite3.Connection, season: int) -> str:
    if table_exists(con, "game_settings"):
        row = con.execute("SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'").fetchone()
        if row and row["setting_value"]:
            return str(row["setting_value"])
    return f"{season + 1}-02-15"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def position_curve(position: str) -> tuple[float, float]:
    return POSITION_AGE_CURVES.get(position, (32.0, 2.7))


def quality_extension(quality: float, position: str) -> float:
    if quality >= 91:
        base = 3.4
    elif quality >= 86:
        base = 2.5
    elif quality >= 80:
        base = 1.4
    elif quality >= 73:
        base = 0.4
    elif quality >= 64:
        base = -0.5
    else:
        base = -1.4
    if position in {"QB", "K", "P", "LS"} and quality >= 78:
        base += 0.7
    return base


def health_extension(candidate: RetirementCandidate) -> float:
    history_penalty = min(3.4, candidate.injury_games_missed * 0.045 + candidate.major_injury_rows * 0.45)
    if candidate.head_neck_rows >= 2:
        history_penalty += 0.7
    active_penalty = min(2.0, candidate.active_injury_days / 120.0)
    clean_bonus = 0.0
    if candidate.injury_history_rows == 0 and candidate.durability >= 78:
        clean_bonus = 1.3
    elif candidate.injury_games_missed <= 4 and candidate.durability >= 74:
        clean_bonus = 0.7
    durability_adjustment = (candidate.durability - 70.0) * 0.035
    return clamp(clean_bonus + durability_adjustment - history_penalty - active_penalty, -4.5, 2.4)


def contract_extension(candidate: RetirementCandidate) -> float:
    if candidate.future_contract_years <= 0:
        return -0.7 if candidate.status == "Free Agent" else 0.0
    return min(1.0, candidate.future_contract_years * 0.28)


def retirement_target_age(candidate: RetirementCandidate, rng: random.Random) -> float:
    mean, deviation = position_curve(candidate.position)
    target = rng.gauss(mean, deviation)
    target += quality_extension(candidate.quality_score, candidate.position)
    target += health_extension(candidate)
    target += contract_extension(candidate)
    return clamp(target, 25.5, 45.5)


def medical_probability(candidate: RetirementCandidate) -> float:
    if candidate.age < 24:
        return 0.0
    medical_load = 0.0
    medical_load += candidate.injury_games_missed / 95.0
    medical_load += candidate.major_injury_rows * 0.28
    medical_load += candidate.head_neck_rows * 0.22
    medical_load += candidate.active_injury_days / 210.0
    medical_load += max(0.0, 62.0 - candidate.durability) * 0.012
    if candidate.active_injury_status in {"Out", "Doubtful"} and candidate.active_injury_days >= 140:
        medical_load += 0.45
    if medical_load < 1.15:
        return 0.0
    chance = (medical_load - 1.15) * 0.025
    if candidate.age >= 32:
        chance += min(0.018, (candidate.age - 31) * 0.003)
    if candidate.quality_score >= 84:
        chance *= 0.72
    return clamp(chance, 0.001, 0.075)


def normal_retirement_probability(candidate: RetirementCandidate, target_age: float) -> tuple[float, str]:
    if candidate.age < 27 and candidate.status != "Free Agent":
        return 0.0, "too_young"

    age_pressure = candidate.age - target_age
    if age_pressure < -2.5 and not (candidate.status == "Free Agent" and candidate.quality_score < 63):
        return 0.0, "below_curve"

    probability = 0.0
    reason = "age_curve"
    if age_pressure >= -2.5:
        probability = 0.018 + sigmoid((age_pressure - 0.3) / 1.55) * 0.24
    if age_pressure >= 3.0:
        probability += min(0.22, (age_pressure - 2.5) * 0.055)

    if candidate.status == "Free Agent":
        probability += 0.055
        reason = "free_agent_market"
        if candidate.quality_score < 64 and candidate.years_exp >= 3 and candidate.age >= 27:
            probability += 0.12
            reason = "fringe_market_exit"
        if candidate.quality_score < 58 and candidate.age >= 25:
            probability += 0.10
            reason = "fringe_market_exit"

    if 0 < candidate.current_season_games <= 2 and candidate.current_snaps < 120 and candidate.age >= 30:
        probability += 0.045
        reason = "reduced_role"
    if candidate.quality_score >= 88:
        probability *= 0.46
    elif candidate.quality_score >= 82:
        probability *= 0.64
    elif candidate.quality_score < 62:
        probability *= 1.28

    if candidate.future_contract_years > 0 and candidate.status != "Free Agent":
        probability *= 0.78
    if candidate.injury_history_rows == 0 and candidate.durability >= 80:
        probability *= 0.82
    probability += min(0.06, candidate.injury_games_missed * 0.0015)

    if candidate.position == "RB" and candidate.age >= 29:
        probability += 0.035
    if candidate.position in {"K", "P", "LS"} and candidate.quality_score >= 75:
        probability *= 0.62
    if candidate.position == "QB" and candidate.quality_score >= 78:
        probability *= 0.68

    return clamp(probability, 0.0, 0.86), reason


def decision_for_candidate(candidate: RetirementCandidate, rng: random.Random) -> RetirementDecision:
    target_age = retirement_target_age(candidate, rng)
    medical_chance = medical_probability(candidate)
    normal_chance, normal_reason = normal_retirement_probability(candidate, target_age)

    medical_roll = rng.random()
    if medical_chance > 0 and medical_roll < medical_chance:
        reason = (
            f"Medical retirement after {candidate.injury_games_missed} prior missed game(s), "
            f"{candidate.major_injury_rows} major history marker(s), and "
            f"{candidate.active_injury_days} active injury day(s)."
        )
        return RetirementDecision(
            candidate=candidate,
            retired=True,
            decision_type="medical",
            probability=medical_chance,
            roll=medical_roll,
            target_age=target_age,
            reason_code="medical_risk",
            reason=reason,
        )

    roll = rng.random()
    retired = roll < normal_chance
    reason = (
        f"Age {candidate.age}, target {target_age:.1f}, quality {candidate.quality_score:.1f}, "
        f"durability {candidate.durability:.0f}, injury missed games {candidate.injury_games_missed}."
    )
    return RetirementDecision(
        candidate=candidate,
        retired=retired,
        decision_type="standard",
        probability=normal_chance,
        roll=roll,
        target_age=target_age,
        reason_code=normal_reason,
        reason=reason,
    )


def role_scores(con: sqlite3.Connection, season: int) -> dict[int, float]:
    if not table_exists(con, "player_role_scores"):
        return {}
    rows = con.execute(
        """
        SELECT player_id, MAX(role_score) AS role_score
        FROM player_role_scores
        WHERE season = ?
        GROUP BY player_id
        """,
        (season,),
    ).fetchall()
    return {int(row["player_id"]): float(row["role_score"] or 0) for row in rows}


def offseason_rating_season(con: sqlite3.Connection, season: int) -> int:
    if not table_exists(con, "player_ratings"):
        return season
    row = con.execute(
        "SELECT COUNT(*) AS count FROM player_ratings WHERE season = ?",
        (season + 1,),
    ).fetchone()
    return season + 1 if int(row["count"] or 0) > 0 else season


def durability_scores(con: sqlite3.Connection, season: int) -> dict[int, float]:
    if not table_exists(con, "player_ratings"):
        return {}
    rows = con.execute(
        """
        SELECT player_id, rating_value
        FROM player_ratings
        WHERE season = ? AND rating_key = 'durability'
        """,
        (season,),
    ).fetchall()
    return {int(row["player_id"]): float(row["rating_value"] or 0) for row in rows}


def current_season_usage(con: sqlite3.Connection, season: int) -> dict[int, dict[str, int]]:
    usage: dict[int, dict[str, int]] = {}
    if table_exists(con, "game_player_stats") and table_exists(con, "game_sim_runs"):
        rows = con.execute(
            """
            SELECT
                gps.player_id,
                COUNT(DISTINCT gps.run_id) AS games,
                SUM(CASE WHEN gps.stat_key IN ('offensive_snaps', 'defensive_snaps', 'special_teams_snaps')
                         THEN gps.stat_value ELSE 0 END) AS snaps
            FROM game_player_stats gps
            JOIN game_sim_runs r ON r.run_id = gps.run_id
            LEFT JOIN season_games sg ON sg.game_id = r.schedule_game_id
            WHERE r.season = ?
              AND COALESCE(r.counts_for_stats, 1) = 1
              AND COALESCE(r.status, 'final') = 'final'
              AND (sg.game_id IS NULL OR sg.game_type = 'REG')
            GROUP BY gps.player_id
            """,
            (season,),
        ).fetchall()
        for row in rows:
            usage[int(row["player_id"])] = {
                "games": int(row["games"] or 0),
                "snaps": int(round(float(row["snaps"] or 0))),
            }
    if table_exists(con, "season_player_stats"):
        rows = con.execute(
            """
            SELECT player_id,
                   SUM(CASE WHEN stat_key IN ('offensive_snaps', 'defensive_snaps', 'special_teams_snaps')
                            THEN stat_value ELSE 0 END) AS snaps
            FROM season_player_stats
            WHERE season = ?
            GROUP BY player_id
            """,
            (season,),
        ).fetchall()
        for row in rows:
            item = usage.setdefault(int(row["player_id"]), {"games": 0, "snaps": 0})
            item["snaps"] = max(item["snaps"], int(round(float(row["snaps"] or 0))))
    return usage


def career_games(con: sqlite3.Connection, season: int) -> dict[int, int]:
    totals: dict[int, int] = {}
    if table_exists(con, "player_season_stats_view"):
        rows = con.execute(
            """
            SELECT player_id, SUM(COALESCE(games, 0)) AS games
            FROM player_season_stats_view
            WHERE season <= ?
            GROUP BY player_id
            """,
            (season,),
        ).fetchall()
        totals.update({int(row["player_id"]): int(row["games"] or 0) for row in rows})
    if table_exists(con, "game_player_stats") and table_exists(con, "game_sim_runs"):
        rows = con.execute(
            """
            SELECT gps.player_id, COUNT(DISTINCT gps.run_id) AS games
            FROM game_player_stats gps
            JOIN game_sim_runs r ON r.run_id = gps.run_id
            WHERE r.season <= ?
              AND COALESCE(r.counts_for_stats, 1) = 1
              AND COALESCE(r.status, 'final') = 'final'
            GROUP BY gps.player_id
            """,
            (season,),
        ).fetchall()
        for row in rows:
            player_id = int(row["player_id"])
            totals[player_id] = max(totals.get(player_id, 0), int(row["games"] or 0))
    return totals


def injury_summaries(con: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    summaries: dict[int, dict[str, Any]] = {}
    if table_exists(con, "player_injury_history"):
        rows = con.execute(
            """
            SELECT player_id,
                   COUNT(*) AS history_rows,
                   SUM(COALESCE(games_missed, 0)) AS games_missed,
                   SUM(CASE WHEN severity IN ('major', 'season_ending') OR injury_code IN ('acl_tear', 'achilles', 'meniscus')
                            THEN 1 ELSE 0 END) AS major_rows,
                   SUM(CASE WHEN body_region = 'head_neck' THEN 1 ELSE 0 END) AS head_neck_rows
            FROM player_injury_history
            WHERE player_id IS NOT NULL
            GROUP BY player_id
            """
        ).fetchall()
        for row in rows:
            summaries[int(row["player_id"])] = {
                "history_rows": int(row["history_rows"] or 0),
                "games_missed": int(row["games_missed"] or 0),
                "major_rows": int(row["major_rows"] or 0),
                "head_neck_rows": int(row["head_neck_rows"] or 0),
                "active_days": 0,
                "active_status": None,
            }
    if table_exists(con, "active_player_injuries"):
        rows = con.execute(
            """
            SELECT player_id,
                   MAX(COALESCE(expected_days, 0)) AS active_days,
                   MAX(CASE status WHEN 'Out' THEN 4 WHEN 'Doubtful' THEN 3 WHEN 'Questionable' THEN 2 WHEN 'Probable' THEN 1 ELSE 0 END) AS status_rank
            FROM active_player_injuries
            WHERE resolved_at IS NULL
            GROUP BY player_id
            """
        ).fetchall()
        labels = {4: "Out", 3: "Doubtful", 2: "Questionable", 1: "Probable", 0: None}
        for row in rows:
            item = summaries.setdefault(
                int(row["player_id"]),
                {"history_rows": 0, "games_missed": 0, "major_rows": 0, "head_neck_rows": 0, "active_days": 0, "active_status": None},
            )
            item["active_days"] = int(row["active_days"] or 0)
            item["active_status"] = labels.get(int(row["status_rank"] or 0))
    return summaries


def future_contract_years(con: sqlite3.Connection, season: int) -> dict[int, int]:
    if not table_exists(con, "contract_years"):
        return {}
    rows = con.execute(
        """
        SELECT player_id, COUNT(DISTINCT season) AS years
        FROM contract_years
        WHERE season > ?
          AND is_active = 1
        GROUP BY player_id
        """,
        (season,),
    ).fetchall()
    return {int(row["player_id"]): int(row["years"] or 0) for row in rows}


def load_candidates(con: sqlite3.Connection, season: int) -> list[RetirementCandidate]:
    ensure_schema(con)
    rating_season = offseason_rating_season(con, season)
    roles = role_scores(con, rating_season)
    if not roles and rating_season != season:
        roles = role_scores(con, season)
    durability = durability_scores(con, rating_season)
    if not durability and rating_season != season:
        durability = durability_scores(con, season)
    usage = current_season_usage(con, season)
    career = career_games(con, season)
    injuries = injury_summaries(con)
    contract_years = future_contract_years(con, season)
    rows = con.execute(
        """
        SELECT player_id, first_name, last_name, position, team_id, age, years_exp,
               COALESCE(status, 'Active') AS status, COALESCE(overall, 50) AS overall,
               COALESCE(injury_prone, 50) AS injury_prone
        FROM players
        WHERE COALESCE(status, 'Active') NOT IN ('Retired')
        ORDER BY player_id
        """
    ).fetchall()
    candidates = []
    for row in rows:
        player_id = int(row["player_id"])
        injury = injuries.get(player_id, {})
        player_usage = usage.get(player_id, {})
        quality = max(float(row["overall"] or 50), roles.get(player_id, 0.0))
        player_durability = durability.get(player_id)
        if player_durability is None:
            player_durability = max(1.0, 100.0 - float(row["injury_prone"] or 50))
        candidates.append(
            RetirementCandidate(
                player_id=player_id,
                team_id=int(row["team_id"]) if row["team_id"] is not None else None,
                name=f"{row['first_name']} {row['last_name']}".strip(),
                position=str(row["position"] or ""),
                age=int(row["age"] or 0),
                years_exp=int(row["years_exp"] or 0),
                status=str(row["status"] or "Active"),
                overall=float(row["overall"] or 50),
                quality_score=quality,
                durability=float(player_durability),
                future_contract_years=contract_years.get(player_id, 0),
                current_season_games=int(player_usage.get("games", 0)),
                current_snaps=int(player_usage.get("snaps", 0)),
                career_games=max(career.get(player_id, 0), int(row["years_exp"] or 0) * 8),
                injury_history_rows=int(injury.get("history_rows", 0)),
                injury_games_missed=int(injury.get("games_missed", 0)),
                major_injury_rows=int(injury.get("major_rows", 0)),
                head_neck_rows=int(injury.get("head_neck_rows", 0)),
                active_injury_days=int(injury.get("active_days", 0)),
                active_injury_status=injury.get("active_status"),
            )
        )
    return candidates


def build_decisions(con: sqlite3.Connection, season: int, seed: str | int | None) -> list[RetirementDecision]:
    rng = random.Random(str(seed if seed is not None else f"{season}:retirement"))
    return [decision_for_candidate(candidate, rng) for candidate in load_candidates(con, season)]


def existing_run(con: sqlite3.Connection, game_id: str | None, season: int) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM player_retirement_decisions
        WHERE COALESCE(game_id, '') = COALESCE(?, '')
          AND season = ?
          AND model_version = ?
        LIMIT 1
        """,
        (game_id, season, MODEL_VERSION),
    ).fetchone()
    return row is not None


def active_contract_id(con: sqlite3.Connection, player_id: int) -> int | None:
    if not table_exists(con, "contracts"):
        return None
    row = con.execute(
        """
        SELECT contract_id
        FROM contracts
        WHERE player_id = ? AND is_active = 1
        ORDER BY end_year DESC, contract_id DESC
        LIMIT 1
        """,
        (player_id,),
    ).fetchone()
    return int(row["contract_id"]) if row else None


def retire_player(con: sqlite3.Connection, decision: RetirementDecision, *, season: int, decision_date: str) -> None:
    candidate = decision.candidate
    contract_id = active_contract_id(con, candidate.player_id)
    old_status = candidate.status
    if contract_id is not None:
        con.execute("UPDATE contracts SET is_active = 0 WHERE contract_id = ?", (contract_id,))
        if table_exists(con, "contract_years"):
            con.execute(
                "UPDATE contract_years SET is_active = 0 WHERE contract_id = ? AND season >= ?",
                (contract_id, season),
            )
    con.execute(
        "UPDATE players SET status = 'Retired' WHERE player_id = ?",
        (candidate.player_id,),
    )
    if table_exists(con, "depth_charts"):
        con.execute("DELETE FROM depth_charts WHERE player_id = ?", (candidate.player_id,))
    if table_exists(con, "player_roster_status_history"):
        con.execute(
            """
            INSERT INTO player_roster_status_history (
                player_id, old_status, new_status, effective_date, season, reason
            )
            VALUES (?, ?, 'Retired', ?, ?, ?)
            """,
            (candidate.player_id, old_status, decision_date, season, decision.reason),
        )
    setup_transactions_cap_ledger.insert_transaction(
        con,
        transaction_date=decision_date,
        season=season,
        phase="Offseason",
        transaction_type="Retirement",
        team_id=candidate.team_id,
        player_id=candidate.player_id,
        contract_id=contract_id,
        from_team_id=candidate.team_id,
        old_status=old_status,
        new_status="Retired",
        description=f"{candidate.name} retired. {decision.reason}",
        source=SOURCE,
        external_ref=f"retirement:{season}:{candidate.player_id}:{MODEL_VERSION}",
    )


def persist_decisions(
    con: sqlite3.Connection,
    decisions: list[RetirementDecision],
    *,
    game_id: str | None,
    season: int,
    decision_date: str,
    seed: str | int | None,
) -> None:
    con.executemany(
        """
        INSERT INTO player_retirement_decisions (
            game_id, season, player_id, team_id, decision_date, decision_type,
            retired, probability, roll, target_age, age, years_exp,
            quality_score, durability, career_games, injury_games_missed,
            active_injury_days, reason_code, reason, model_version, rng_seed
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_id, season, player_id, model_version) DO UPDATE SET
            team_id = excluded.team_id,
            decision_date = excluded.decision_date,
            decision_type = excluded.decision_type,
            retired = excluded.retired,
            probability = excluded.probability,
            roll = excluded.roll,
            target_age = excluded.target_age,
            age = excluded.age,
            years_exp = excluded.years_exp,
            quality_score = excluded.quality_score,
            durability = excluded.durability,
            career_games = excluded.career_games,
            injury_games_missed = excluded.injury_games_missed,
            active_injury_days = excluded.active_injury_days,
            reason_code = excluded.reason_code,
            reason = excluded.reason,
            rng_seed = excluded.rng_seed,
            created_at = datetime('now')
        """,
        [
            (
                game_id,
                season,
                decision.candidate.player_id,
                decision.candidate.team_id,
                decision_date,
                decision.decision_type,
                1 if decision.retired else 0,
                decision.probability,
                decision.roll,
                decision.target_age,
                decision.candidate.age,
                decision.candidate.years_exp,
                decision.candidate.quality_score,
                decision.candidate.durability,
                decision.candidate.career_games,
                decision.candidate.injury_games_missed,
                decision.candidate.active_injury_days,
                decision.reason_code,
                decision.reason,
                MODEL_VERSION,
                str(seed) if seed is not None else None,
            )
            for decision in decisions
        ],
    )


def run_retirements(
    con: sqlite3.Connection,
    *,
    season: int,
    decision_date: str | None = None,
    seed: str | int | None = None,
    game_id: str | None = None,
    apply: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    ensure_schema(con)
    game_id = game_id if game_id is not None else (current_game_id(con) or "global")
    decision_date = decision_date or current_game_date(con, season)
    if apply and existing_run(con, game_id, season) and not force:
        raise ValueError(f"Retirement decisions already exist for {game_id or 'global'} {season}. Use --force to rerun.")
    if apply and force:
        con.execute(
            """
            DELETE FROM player_retirement_decisions
            WHERE COALESCE(game_id, '') = COALESCE(?, '')
              AND season = ?
              AND model_version = ?
            """,
            (game_id, season, MODEL_VERSION),
        )

    decisions = build_decisions(con, season, seed)
    retired = [decision for decision in decisions if decision.retired]
    if apply:
        for decision in retired:
            retire_player(con, decision, season=season, decision_date=decision_date)
        persist_decisions(con, decisions, game_id=game_id, season=season, decision_date=decision_date, seed=seed)
        setup_contract_years.sync_team_cap_space(con)
    by_type: dict[str, int] = {}
    for decision in retired:
        by_type[decision.decision_type] = by_type.get(decision.decision_type, 0) + 1
    return {
        "game_id": game_id,
        "season": season,
        "decision_date": decision_date,
        "seed": seed,
        "considered": len(decisions),
        "retired": len(retired),
        "by_type": by_type,
        "decisions": decisions,
    }


def print_summary(result: dict[str, Any], *, apply: bool, limit: int) -> None:
    print(f"Retirement model {MODEL_VERSION}")
    print(f"Season: {result['season']}  Date: {result['decision_date']}  Seed: {result['seed']}")
    print(f"Mode: {'APPLY' if apply else 'DRY RUN'}")
    print(f"Players considered: {result['considered']}")
    print(f"Retirements: {result['retired']} {result['by_type']}")
    decisions = [decision for decision in result["decisions"] if decision.retired]
    decisions.sort(key=lambda item: (item.decision_type != "medical", -item.candidate.age, -item.probability, item.candidate.name))
    if not decisions:
        return
    print("")
    print(f"{'Type':<9} {'Player':<24} {'Pos':<4} {'Age':>3} {'Q':>5} {'Dur':>5} {'Prob':>6} {'Roll':>6} Reason")
    for decision in decisions[:limit]:
        candidate = decision.candidate
        print(
            f"{decision.decision_type:<9} {candidate.name[:24]:<24} {candidate.position:<4} "
            f"{candidate.age:>3} {candidate.quality_score:>5.1f} {candidate.durability:>5.0f} "
            f"{decision.probability:>6.3f} {decision.roll:>6.3f} {decision.reason_code}"
        )
    if len(decisions) > limit:
        print(f"...and {len(decisions) - limit} more")


def action_setup(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        ensure_schema(con)
        con.commit()
        print("Retirement schema is ready.")
    finally:
        con.close()


def action_run(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        result = run_retirements(
            con,
            season=args.season,
            decision_date=args.decision_date,
            seed=args.seed,
            game_id=args.game_id,
            apply=args.apply,
            force=args.force,
        )
        if args.apply:
            con.commit()
        else:
            con.rollback()
        print_summary(result, apply=args.apply, limit=args.limit)
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview or apply offseason player retirements.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Create retirement tables.")
    setup_parser.set_defaults(func=action_setup)

    run_parser = subparsers.add_parser("run", help="Preview or apply retirement decisions.")
    run_parser.add_argument("--season", type=int, default=2026)
    run_parser.add_argument("--decision-date")
    run_parser.add_argument("--seed")
    run_parser.add_argument("--game-id")
    run_parser.add_argument("--apply", action="store_true")
    run_parser.add_argument("--force", action="store_true")
    run_parser.add_argument("--limit", type=int, default=40)
    run_parser.set_defaults(func=action_run)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
