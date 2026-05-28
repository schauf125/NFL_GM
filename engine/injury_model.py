"""Injury model shared by the match engine and player profile export.

This is a first playable model, not a medical simulator. It aims to produce
NFL-shaped availability pressure: mostly short soft-tissue/contact injuries,
occasional multi-week injuries, and rare season-altering injuries that become
part of a player's recurring body-area risk.
"""

from __future__ import annotations

import math
import random
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any


MODEL_VERSION = "injury_v1"
MATCH_SOURCE = "match_engine"
WEEKLY_PRACTICE_SOURCE = "weekly_practice"
INJURY_UNAVAILABLE_STATUSES = {"Out", "IR", "PUP", "NFI"}
INJURY_STATUS_CODES = {"Questionable", "Doubtful", "Out", "IR", "PUP", "NFI"}


@dataclass(frozen=True)
class InjuryCatalogEntry:
    injury_code: str
    label: str
    body_region: str
    body_part: str
    severity_bucket: str
    min_days: int
    max_days: int
    recurrence_risk: float
    weight_contact: float
    weight_non_contact: float
    weight_sack: float
    weight_special_teams: float


@dataclass(frozen=True)
class InjuryEvent:
    play_number: int
    quarter: int
    clock_tenths: int
    player_id: int
    team_id: int
    opponent_player_id: int | None
    opponent_team_id: int | None
    injury_code: str
    injury_label: str
    body_region: str
    body_part: str
    severity: str
    mechanism: str
    expected_days: int
    expected_games: int
    status: str
    description: str


@dataclass(frozen=True)
class PracticePlayerSnapshot:
    player_id: int
    name: str
    position: str
    team_id: int
    ratings: dict[str, int]
    metadata: dict[str, object]

    def rating(self, key: str, default: float = 50.0) -> float:
        return float(self.ratings.get(key, default))


INJURY_CATALOG: tuple[InjuryCatalogEntry, ...] = (
    InjuryCatalogEntry("ankle_sprain", "Ankle sprain", "lower_body", "ankle", "minor", 5, 21, 0.18, 16, 4, 7, 15),
    InjuryCatalogEntry("high_ankle_sprain", "High ankle sprain", "lower_body", "ankle", "moderate", 21, 63, 0.28, 6, 2, 4, 7),
    InjuryCatalogEntry("hamstring_strain", "Hamstring strain", "lower_body", "hamstring", "minor", 7, 28, 0.32, 3, 18, 2, 5),
    InjuryCatalogEntry("calf_strain", "Calf strain", "lower_body", "calf", "minor", 7, 24, 0.25, 2, 9, 1, 3),
    InjuryCatalogEntry("quad_strain", "Quadriceps strain", "lower_body", "quadriceps", "minor", 7, 28, 0.23, 2, 8, 1, 3),
    InjuryCatalogEntry("groin_strain", "Groin strain", "lower_body", "groin", "minor", 7, 28, 0.27, 2, 9, 1, 2),
    InjuryCatalogEntry("knee_sprain", "Knee sprain", "lower_body", "knee", "moderate", 10, 42, 0.25, 9, 5, 5, 5),
    InjuryCatalogEntry("mcl_sprain", "MCL sprain", "lower_body", "knee", "moderate", 21, 70, 0.30, 5, 2, 3, 3),
    InjuryCatalogEntry("meniscus_injury", "Meniscus injury", "lower_body", "knee", "major", 35, 120, 0.34, 3, 2, 2, 2),
    InjuryCatalogEntry("acl_tear", "ACL tear", "lower_body", "knee", "major", 250, 365, 0.46, 0.35, 0.25, 0.25, 0.25),
    InjuryCatalogEntry("achilles_tear", "Achilles tear", "lower_body", "achilles", "major", 260, 365, 0.44, 0.20, 0.35, 0.20, 0.15),
    InjuryCatalogEntry("foot_sprain", "Foot sprain", "lower_body", "foot", "moderate", 10, 42, 0.22, 5, 5, 3, 4),
    InjuryCatalogEntry("foot_fracture", "Foot fracture", "lower_body", "foot", "major", 45, 120, 0.30, 2, 1, 1, 2),
    InjuryCatalogEntry("concussion", "Concussion", "head_neck", "head", "moderate", 7, 28, 0.36, 7, 0.5, 9, 6),
    InjuryCatalogEntry("neck_stinger", "Neck stinger", "head_neck", "neck", "minor", 3, 14, 0.20, 6, 0.5, 5, 4),
    InjuryCatalogEntry("shoulder_sprain", "Shoulder sprain", "upper_body", "shoulder", "minor", 7, 28, 0.20, 9, 1, 7, 5),
    InjuryCatalogEntry("shoulder_separation", "Shoulder separation", "upper_body", "shoulder", "moderate", 21, 70, 0.28, 4, 0.5, 4, 3),
    InjuryCatalogEntry("pectoral_strain", "Pectoral strain", "upper_body", "pectoral", "moderate", 14, 56, 0.22, 3, 3, 4, 2),
    InjuryCatalogEntry("pectoral_tear", "Pectoral tear", "upper_body", "pectoral", "major", 90, 180, 0.30, 0.7, 0.4, 0.8, 0.4),
    InjuryCatalogEntry("rib_injury", "Rib injury", "torso", "ribs", "moderate", 10, 35, 0.18, 8, 0.5, 8, 5),
    InjuryCatalogEntry("back_spasm", "Back spasm", "torso", "back", "minor", 4, 18, 0.26, 2, 7, 4, 2),
    InjuryCatalogEntry("wrist_hand_injury", "Wrist/hand injury", "upper_body", "hand", "minor", 5, 21, 0.15, 6, 1, 5, 4),
    InjuryCatalogEntry("elbow_sprain", "Elbow sprain", "upper_body", "elbow", "minor", 7, 28, 0.17, 3, 2, 4, 2),
)


def parse_date(value: str | None, fallback: date | None = None) -> date:
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return fallback or date.today()


def table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS injury_catalog (
            injury_code TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            body_region TEXT NOT NULL,
            body_part TEXT NOT NULL,
            severity_bucket TEXT NOT NULL,
            min_days INTEGER NOT NULL,
            max_days INTEGER NOT NULL,
            recurrence_risk REAL NOT NULL,
            weight_contact REAL NOT NULL DEFAULT 1,
            weight_non_contact REAL NOT NULL DEFAULT 1,
            weight_sack REAL NOT NULL DEFAULT 1,
            weight_special_teams REAL NOT NULL DEFAULT 1,
            model_version TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS player_injury_history (
            injury_history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            injury_code TEXT NOT NULL,
            injury_label TEXT NOT NULL,
            body_region TEXT NOT NULL,
            body_part TEXT NOT NULL,
            severity TEXT NOT NULL,
            start_date TEXT NOT NULL,
            resolved_date TEXT,
            expected_days INTEGER NOT NULL DEFAULT 0,
            games_missed INTEGER NOT NULL DEFAULT 0,
            recurrence_risk REAL NOT NULL DEFAULT 0,
            source TEXT NOT NULL,
            source_run_id INTEGER,
            schedule_game_id INTEGER REFERENCES season_games(game_id) ON DELETE SET NULL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_player_injury_history_player
            ON player_injury_history(player_id, body_part, start_date);

        CREATE TABLE IF NOT EXISTS active_player_injuries (
            active_injury_id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            injury_history_id INTEGER REFERENCES player_injury_history(injury_history_id) ON DELETE CASCADE,
            injury_code TEXT NOT NULL,
            injury_label TEXT NOT NULL,
            body_region TEXT NOT NULL,
            body_part TEXT NOT NULL,
            severity TEXT NOT NULL,
            start_date TEXT NOT NULL,
            expected_days INTEGER NOT NULL DEFAULT 0,
            expected_games INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            return_earliest_date TEXT NOT NULL,
            resolved_at TEXT,
            source TEXT NOT NULL,
            source_run_id INTEGER,
            schedule_game_id INTEGER REFERENCES season_games(game_id) ON DELETE SET NULL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_active_player_injuries_player
            ON active_player_injuries(player_id, resolved_at, return_earliest_date);

        CREATE TABLE IF NOT EXISTS game_injury_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER REFERENCES game_sim_runs(run_id) ON DELETE CASCADE,
            schedule_game_id INTEGER REFERENCES season_games(game_id) ON DELETE SET NULL,
            season INTEGER NOT NULL,
            week INTEGER,
            game_date TEXT NOT NULL,
            quarter INTEGER NOT NULL,
            clock_tenths INTEGER NOT NULL,
            play_number INTEGER NOT NULL,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            opponent_player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            opponent_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            injury_history_id INTEGER REFERENCES player_injury_history(injury_history_id) ON DELETE SET NULL,
            active_injury_id INTEGER REFERENCES active_player_injuries(active_injury_id) ON DELETE SET NULL,
            injury_code TEXT NOT NULL,
            injury_label TEXT NOT NULL,
            body_region TEXT NOT NULL,
            body_part TEXT NOT NULL,
            severity TEXT NOT NULL,
            mechanism TEXT NOT NULL,
            expected_days INTEGER NOT NULL DEFAULT 0,
            expected_games INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            description TEXT,
            source TEXT NOT NULL DEFAULT 'match_engine',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_game_injury_events_run
            ON game_injury_events(run_id, play_number);

        DROP VIEW IF EXISTS player_injury_risk_view;
        CREATE VIEW player_injury_risk_view AS
        SELECT
            pih.player_id,
            pih.body_region,
            pih.body_part,
            COUNT(*) AS injury_count,
            SUM(CASE WHEN pih.severity IN ('major', 'severe') THEN 1 ELSE 0 END) AS major_count,
            SUM(pih.games_missed) AS games_missed,
            MAX(pih.start_date) AS last_injury_date,
            MAX(pih.recurrence_risk) AS max_recurrence_risk,
            MIN(CASE WHEN api.resolved_at IS NULL THEN api.return_earliest_date ELSE NULL END) AS active_return_date,
            MAX(CASE WHEN api.resolved_at IS NULL THEN api.status ELSE NULL END) AS active_status
        FROM player_injury_history pih
        LEFT JOIN active_player_injuries api
          ON api.injury_history_id = pih.injury_history_id
        GROUP BY pih.player_id, pih.body_region, pih.body_part;
        """
    )
    seed_injury_catalog(con)
    seed_injury_statuses(con)
    upsert_setting(con, "injuries_enabled", "1")
    upsert_setting(con, "injury_model_version", MODEL_VERSION)


def upsert_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    if not table_exists(con, "game_settings"):
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS game_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
    con.execute(
        """
        INSERT INTO game_settings (setting_key, setting_value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(setting_key) DO UPDATE SET
            setting_value = excluded.setting_value,
            updated_at = datetime('now')
        """,
        (key, value),
    )


def seed_injury_catalog(con: sqlite3.Connection) -> None:
    con.executemany(
        """
        INSERT INTO injury_catalog (
            injury_code, label, body_region, body_part, severity_bucket, min_days,
            max_days, recurrence_risk, weight_contact, weight_non_contact,
            weight_sack, weight_special_teams, model_version, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(injury_code) DO UPDATE SET
            label = excluded.label,
            body_region = excluded.body_region,
            body_part = excluded.body_part,
            severity_bucket = excluded.severity_bucket,
            min_days = excluded.min_days,
            max_days = excluded.max_days,
            recurrence_risk = excluded.recurrence_risk,
            weight_contact = excluded.weight_contact,
            weight_non_contact = excluded.weight_non_contact,
            weight_sack = excluded.weight_sack,
            weight_special_teams = excluded.weight_special_teams,
            model_version = excluded.model_version,
            updated_at = datetime('now')
        """,
        [
            (
                item.injury_code,
                item.label,
                item.body_region,
                item.body_part,
                item.severity_bucket,
                item.min_days,
                item.max_days,
                item.recurrence_risk,
                item.weight_contact,
                item.weight_non_contact,
                item.weight_sack,
                item.weight_special_teams,
                MODEL_VERSION,
            )
            for item in INJURY_CATALOG
        ],
    )


def seed_injury_statuses(con: sqlite3.Connection) -> None:
    if not table_exists(con, "roster_status_types"):
        return
    rows = [
        ("Questionable", "Questionable", 1, 1, 1, 0, "Injured but expected to be available unless recovery slips."),
        ("Doubtful", "Doubtful", 1, 1, 1, 0, "Injured and unlikely to be available this week."),
        ("Out", "Out / Active Roster", 1, 1, 1, 0, "Unavailable because of an active injury, but still on the active roster."),
        ("IR", "Injured Reserve", 0, 1, 0, 0, "Long-term injury designation; counts against cap but not active roster."),
        ("PUP", "Physically Unable to Perform", 0, 1, 0, 0, "Unavailable due to football injury recovery."),
        ("NFI", "Non-Football Injury", 0, 1, 0, 0, "Unavailable due to non-football injury or illness."),
    ]
    con.executemany(
        """
        INSERT INTO roster_status_types (
            status_code, display_name, counts_against_top51, counts_against_regular_cap,
            counts_against_roster_limit, counts_against_practice_squad_limit, description
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(status_code) DO UPDATE SET
            display_name = excluded.display_name,
            counts_against_top51 = excluded.counts_against_top51,
            counts_against_regular_cap = excluded.counts_against_regular_cap,
            counts_against_roster_limit = excluded.counts_against_roster_limit,
            counts_against_practice_squad_limit = excluded.counts_against_practice_squad_limit,
            description = excluded.description
        """,
        rows,
    )


def game_date_for_schedule(con: sqlite3.Connection, schedule_game_id: int | None, season: int) -> str:
    if schedule_game_id is not None and table_exists(con, "season_games"):
        row = con.execute("SELECT game_date FROM season_games WHERE game_id = ?", (schedule_game_id,)).fetchone()
        if row and row["game_date"]:
            return str(row["game_date"])
    if table_exists(con, "game_settings"):
        row = con.execute("SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'").fetchone()
        if row and row["setting_value"]:
            return str(row["setting_value"])
    return f"{season}-09-01"


def resolve_available_injuries(con: sqlite3.Connection, as_of_date: str) -> int:
    ensure_schema(con)
    cleared = con.execute(
        """
        SELECT *
        FROM active_player_injuries
        WHERE resolved_at IS NULL
          AND return_earliest_date <= ?
        """,
        (as_of_date,),
    ).fetchall()
    if not cleared:
        return 0
    cleared_player_ids = {int(row["player_id"]) for row in cleared}
    con.execute(
        """
        UPDATE active_player_injuries
        SET resolved_at = ?, status = 'Cleared'
        WHERE resolved_at IS NULL
          AND return_earliest_date <= ?
        """,
        (as_of_date, as_of_date),
    )
    con.executemany(
        """
        UPDATE player_injury_history
        SET resolved_date = ?
        WHERE injury_history_id = ?
          AND resolved_date IS NULL
        """,
        [(as_of_date, int(row["injury_history_id"])) for row in cleared if row["injury_history_id"] is not None],
    )
    for player_id in cleared_player_ids:
        reset_player_status_if_available(con, player_id)
    return len(cleared)


def reset_player_status_if_available(con: sqlite3.Connection, player_id: int) -> None:
    active = con.execute(
        """
        SELECT 1
        FROM active_player_injuries
        WHERE player_id = ?
          AND resolved_at IS NULL
          AND status IN ('Questionable', 'Doubtful', 'Out', 'IR', 'PUP', 'NFI')
        LIMIT 1
        """,
        (player_id,),
    ).fetchone()
    if active:
        return
    row = con.execute("SELECT status FROM players WHERE player_id = ?", (player_id,)).fetchone()
    if row and row["status"] in INJURY_STATUS_CODES:
        con.execute("UPDATE players SET status = 'Active' WHERE player_id = ?", (player_id,))


def unavailable_player_ids(con: sqlite3.Connection, player_ids: list[int], as_of_date: str | None) -> set[int]:
    if not player_ids or not table_exists(con, "active_player_injuries"):
        return set()
    if as_of_date:
        resolve_available_injuries(con, as_of_date)
    placeholders = ",".join("?" for _ in player_ids)
    params: list[Any] = list(player_ids)
    date_clause = ""
    if as_of_date:
        date_clause = "AND return_earliest_date > ?"
        params.append(as_of_date)
    rows = con.execute(
        f"""
        SELECT DISTINCT player_id
        FROM active_player_injuries
        WHERE player_id IN ({placeholders})
          AND resolved_at IS NULL
          AND status IN ('Out', 'IR', 'PUP', 'NFI')
          {date_clause}
        """,
        params,
    ).fetchall()
    return {int(row["player_id"]) for row in rows}


def injury_context_by_player(
    con: sqlite3.Connection,
    player_ids: list[int],
    as_of_date: str | None = None,
) -> dict[int, dict[str, Any]]:
    if not player_ids or not table_exists(con, "player_injury_history"):
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = con.execute(
        f"""
        SELECT player_id, body_region, body_part,
               COUNT(*) AS injury_count,
               SUM(games_missed) AS games_missed,
               MAX(recurrence_risk) AS max_risk,
               MAX(start_date) AS last_injury_date
        FROM player_injury_history
        WHERE player_id IN ({placeholders})
        GROUP BY player_id, body_region, body_part
        """,
        player_ids,
    ).fetchall()
    grouped: dict[int, dict[str, Any]] = {}
    today = parse_date(as_of_date, date.today())
    for row in rows:
        player_id = int(row["player_id"])
        body_part = str(row["body_part"])
        injury_count = int(row["injury_count"] or 0)
        games_missed = int(row["games_missed"] or 0)
        max_risk = float(row["max_risk"] or 0.0)
        last_date = parse_date(row["last_injury_date"], today)
        months_since = max(0.0, (today - last_date).days / 30.4)
        recency = max(0.0, 1.0 - months_since / 36.0)
        risk_score = min(2.0, max_risk + injury_count * 0.08 + games_missed * 0.012 + recency * 0.18)
        bucket = {
            "body_region": row["body_region"],
            "body_part": body_part,
            "injury_count": injury_count,
            "games_missed": games_missed,
            "max_recurrence_risk": round(max_risk, 3),
            "last_injury_date": row["last_injury_date"],
            "risk_score": round(risk_score, 3),
        }
        item = grouped.setdefault(player_id, {"body_risks": {}, "risk_score": 0.0})
        item["body_risks"][body_part] = bucket
        item["risk_score"] = round(float(item["risk_score"]) + risk_score * 0.35, 3)
    return grouped


def active_injuries_by_player(con: sqlite3.Connection, player_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not player_ids or not table_exists(con, "active_player_injuries"):
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = con.execute(
        f"""
        SELECT *
        FROM active_player_injuries
        WHERE player_id IN ({placeholders})
          AND resolved_at IS NULL
        ORDER BY return_earliest_date, active_injury_id
        """,
        player_ids,
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["player_id"]), []).append(dict(row))
    return grouped


def existing_body_risk(player: Any, body_part: str) -> float:
    body_risks = player.metadata.get("injury_body_risks", {}) if getattr(player, "metadata", None) else {}
    if isinstance(body_risks, dict):
        body = body_risks.get(body_part)
        if isinstance(body, dict):
            return float(body.get("risk_score") or 0.0)
    return 0.0


def injury_probability(
    player: Any,
    *,
    mechanism: str,
    play_type: str,
    high_impact: bool,
    snap_load: float,
) -> float:
    if mechanism == "sack":
        base = 0.0044 if player.position == "QB" else 0.0014
    elif mechanism == "special_teams":
        base = 0.0032
    elif mechanism == "trench":
        base = 0.00105
    elif mechanism == "practice":
        base = 0.0018
    elif mechanism == "non_contact":
        base = 0.00054
    else:
        base = 0.00265
    if high_impact:
        base += 0.0012
    position_multiplier = {
        "RB": 1.28,
        "FB": 1.20,
        "WR": 1.12,
        "TE": 1.10,
        "S": 1.12,
        "LB": 1.08,
        "EDGE": 1.06,
        "IDL": 1.04,
        "DT": 1.04,
        "NT": 1.04,
        "OT": 1.10,
        "OG": 1.08,
        "C": 1.06,
        "QB": 0.88 if mechanism != "sack" else 1.08,
        "K": 0.55,
        "P": 0.55,
        "LS": 0.72,
    }.get(player.position, 1.0)
    durability = player.rating("durability", max(25.0, 100.0 - float(player.metadata.get("injury_prone", 50) or 50)))
    durability_multiplier = 1.0 + (68.0 - durability) / 95.0
    age = float(player.metadata.get("age", 26) or 26)
    age_multiplier = 1.0 + max(0.0, age - 29.0) * 0.028
    history_multiplier = 1.0 + float(player.metadata.get("injury_history_risk", 0.0) or 0.0) * 0.22
    fatigue_multiplier = 1.0 + max(0.0, snap_load - 42.0) * 0.0035
    if play_type in {"kneel", "spike", "penalty"}:
        return 0.0
    if mechanism == "practice":
        fatigue_multiplier = max(fatigue_multiplier, 1.0 + max(0.0, snap_load - 58.0) * 0.004)
    return max(0.0, min(0.018, base * position_multiplier * durability_multiplier * age_multiplier * history_multiplier * fatigue_multiplier))


def choose_catalog_entry(
    rng: random.Random,
    player: Any,
    *,
    mechanism: str,
    high_impact: bool,
) -> InjuryCatalogEntry:
    weighted: list[tuple[InjuryCatalogEntry, float]] = []
    for item in INJURY_CATALOG:
        if mechanism == "sack":
            weight = item.weight_sack
        elif mechanism == "special_teams":
            weight = item.weight_special_teams
        elif mechanism in {"non_contact", "practice"}:
            weight = item.weight_non_contact
        elif mechanism == "trench":
            weight = (item.weight_contact * 0.58) + (item.weight_non_contact * 0.42)
        else:
            weight = item.weight_contact
        if item.severity_bucket == "major" and not high_impact:
            weight *= 0.35
        if mechanism == "practice":
            if item.severity_bucket == "major":
                weight *= 0.18
            if item.body_part in {"hamstring", "calf", "quadriceps", "groin", "back"}:
                weight *= 1.65
        if mechanism == "trench":
            if item.body_part in {"pectoral", "shoulder", "knee", "back", "ankle"}:
                weight *= 1.45
        if player.position == "QB" and item.body_part in {"shoulder", "elbow", "ribs", "head"}:
            weight *= 1.35
        if player.position in {"RB", "WR", "CB", "S"} and item.body_region == "lower_body":
            weight *= 1.20
        if player.position in {"OT", "OG", "C", "IDL", "DT", "NT"} and item.body_part in {"pectoral", "shoulder", "knee"}:
            weight *= 1.20
        body_risk = existing_body_risk(player, item.body_part)
        if body_risk:
            weight *= 1.0 + min(0.90, body_risk * 0.32)
        weighted.append((item, max(0.01, weight)))
    total = sum(weight for _item, weight in weighted)
    roll = rng.random() * total
    cursor = 0.0
    for item, weight in weighted:
        cursor += weight
        if roll <= cursor:
            return item
    return weighted[-1][0]


def expected_days_for_injury(rng: random.Random, player: Any, item: InjuryCatalogEntry) -> int:
    low, high = item.min_days, item.max_days
    mode = low + (high - low) * (0.35 if item.severity_bucket != "major" else 0.52)
    days = int(round(rng.triangular(low, high, mode)))
    durability = player.rating("durability", 60)
    days = int(round(days * (1.0 + max(0.0, 64.0 - durability) * 0.006)))
    body_risk = existing_body_risk(player, item.body_part)
    if body_risk:
        days = int(round(days * (1.0 + min(0.30, body_risk * 0.08))))
    if rng.random() < 0.08 and item.severity_bucket != "major":
        days = int(round(days * rng.uniform(1.25, 1.65)))
    return max(1, min(days, 390))


def status_for_expected_days(days: int) -> str:
    if days <= 5:
        return "Questionable"
    if days <= 9:
        return "Doubtful"
    if days >= 56:
        return "IR"
    return "Out"


def expected_games(days: int) -> int:
    if days <= 5:
        return 0
    return max(1, int(math.ceil(days / 7.0)))


def maybe_create_injury_event(
    rng: random.Random,
    player: Any,
    *,
    team_id: int,
    opponent_player: Any | None,
    opponent_team_id: int | None,
    play_number: int,
    quarter: int,
    clock_tenths: int,
    mechanism: str,
    play_type: str,
    high_impact: bool = False,
    snap_load: float = 0.0,
) -> InjuryEvent | None:
    chance = injury_probability(
        player,
        mechanism=mechanism,
        play_type=play_type,
        high_impact=high_impact,
        snap_load=snap_load,
    )
    if rng.random() >= chance:
        return None
    item = choose_catalog_entry(rng, player, mechanism=mechanism, high_impact=high_impact)
    days = expected_days_for_injury(rng, player, item)
    status = status_for_expected_days(days)
    games = expected_games(days)
    recurrence = item.recurrence_risk + min(0.18, existing_body_risk(player, item.body_part) * 0.08)
    severity = "severe" if item.severity_bucket == "major" and days >= 180 else item.severity_bucket
    if opponent_player is not None:
        description = f"{player.name} suffered a {item.label.lower()} after contact with {opponent_player.name}."
    elif mechanism == "practice":
        description = f"{player.name} picked up a {item.label.lower()} during the practice week."
    elif mechanism == "trench":
        description = f"{player.name} suffered a {item.label.lower()} during line play."
    elif mechanism == "non_contact":
        description = f"{player.name} suffered a non-contact {item.label.lower()}."
    else:
        description = f"{player.name} suffered a {item.label.lower()}."
    if recurrence > item.recurrence_risk + 0.01:
        description += " Prior body-area history increased the risk."
    return InjuryEvent(
        play_number=play_number,
        quarter=quarter,
        clock_tenths=clock_tenths,
        player_id=player.player_id,
        team_id=team_id,
        opponent_player_id=opponent_player.player_id if opponent_player else None,
        opponent_team_id=opponent_team_id,
        injury_code=item.injury_code,
        injury_label=item.label,
        body_region=item.body_region,
        body_part=item.body_part,
        severity=severity,
        mechanism=mechanism,
        expected_days=days,
        expected_games=games,
        status=status,
        description=description,
    )


def persist_injury_events(
    con: sqlite3.Connection,
    events: list[InjuryEvent],
    *,
    season: int,
    week: int | None,
    game_date: str,
    source: str,
    source_run_id: int | None = None,
    schedule_game_id: int | None = None,
    run_id: int | None = None,
) -> int:
    ensure_schema(con)
    if not events:
        return 0
    game_day = parse_date(game_date)
    persisted = 0
    for event in events:
        return_date = (game_day + timedelta(days=event.expected_days)).isoformat()
        history_cur = con.execute(
            """
            INSERT INTO player_injury_history (
                player_id, injury_code, injury_label, body_region, body_part,
                severity, start_date, expected_days, games_missed, recurrence_risk,
                source, source_run_id, schedule_game_id, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.player_id,
                event.injury_code,
                event.injury_label,
                event.body_region,
                event.body_part,
                event.severity,
                game_date,
                event.expected_days,
                event.expected_games,
                recurrence_for_code(event.injury_code),
                source,
                source_run_id,
                schedule_game_id,
                event.description,
            ),
        )
        history_id = int(history_cur.lastrowid)
        active_cur = con.execute(
            """
            INSERT INTO active_player_injuries (
                player_id, injury_history_id, injury_code, injury_label, body_region,
                body_part, severity, start_date, expected_days, expected_games, status,
                return_earliest_date, source, source_run_id, schedule_game_id, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.player_id,
                history_id,
                event.injury_code,
                event.injury_label,
                event.body_region,
                event.body_part,
                event.severity,
                game_date,
                event.expected_days,
                event.expected_games,
                event.status,
                return_date,
                source,
                source_run_id,
                schedule_game_id,
                event.description,
            ),
        )
        active_id = int(active_cur.lastrowid)
        con.execute(
            """
            INSERT INTO game_injury_events (
                run_id, schedule_game_id, season, week, game_date, quarter,
                clock_tenths, play_number, player_id, team_id, opponent_player_id,
                opponent_team_id, injury_history_id, active_injury_id, injury_code,
                injury_label, body_region, body_part, severity, mechanism,
                expected_days, expected_games, status, description, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                schedule_game_id,
                season,
                week,
                game_date,
                event.quarter,
                event.clock_tenths,
                event.play_number,
                event.player_id,
                event.team_id,
                event.opponent_player_id,
                event.opponent_team_id,
                history_id,
                active_id,
                event.injury_code,
                event.injury_label,
                event.body_region,
                event.body_part,
                event.severity,
                event.mechanism,
                event.expected_days,
                event.expected_games,
                event.status,
                event.description,
                source,
            ),
        )
        if event.status in INJURY_STATUS_CODES:
            con.execute("UPDATE players SET status = ? WHERE player_id = ?", (event.status, event.player_id))
        persisted += 1
    return persisted


def persist_game_injuries(con: sqlite3.Connection, result: Any, run_id: int) -> int:
    ensure_schema(con)
    events: list[InjuryEvent] = list(getattr(result, "injury_events", []) or [])
    if not events:
        return 0
    game_date = game_date_for_schedule(con, result.schedule_game_id, result.season)
    return persist_injury_events(
        con,
        events,
        season=result.season,
        week=result.week,
        game_date=game_date,
        source=MATCH_SOURCE,
        source_run_id=run_id,
        schedule_game_id=result.schedule_game_id,
        run_id=run_id,
    )


def recurrence_for_code(injury_code: str) -> float:
    for item in INJURY_CATALOG:
        if item.injury_code == injury_code:
            return item.recurrence_risk
    return 0.0


def week_practice_date(con: sqlite3.Connection, season: int, week: int) -> str:
    if table_exists(con, "season_weeks"):
        row = con.execute(
            """
            SELECT week_start_date, primary_game_date
            FROM season_weeks
            WHERE season = ? AND week = ?
            """,
            (season, week),
        ).fetchone()
        if row and row["primary_game_date"]:
            return (parse_date(str(row["primary_game_date"])) + timedelta(days=1)).isoformat()
        if row and row["week_start_date"]:
            return (parse_date(str(row["week_start_date"])) + timedelta(days=3)).isoformat()
    if table_exists(con, "season_games"):
        row = con.execute(
            """
            SELECT MAX(game_date) AS game_date
            FROM season_games
            WHERE season = ? AND week = ? AND game_date IS NOT NULL
            """,
            (season, week),
        ).fetchone()
        if row and row["game_date"]:
            return (parse_date(str(row["game_date"])) + timedelta(days=1)).isoformat()
    return f"{season}-09-01"


def active_team_ids(con: sqlite3.Connection) -> list[int]:
    return [
        int(row["team_id"])
        for row in con.execute(
            """
            SELECT team_id
            FROM teams
            ORDER BY team_id
            """
        )
    ]


def load_practice_candidates(
    con: sqlite3.Connection,
    *,
    season: int,
    team_id: int,
    as_of_date: str,
) -> list[PracticePlayerSnapshot]:
    rows = con.execute(
        """
        SELECT player_id, first_name, last_name, position, team_id, age, overall,
               injury_prone, speed, strength, agility, awareness
        FROM players
        WHERE team_id = ?
          AND COALESCE(status, 'Active') NOT IN ('Retired', 'Released', 'Free Agent')
        ORDER BY overall DESC, player_id
        """,
        (team_id,),
    ).fetchall()
    if not rows:
        return []
    unavailable = unavailable_player_ids(con, [int(row["player_id"]) for row in rows], as_of_date)
    player_ids = [int(row["player_id"]) for row in rows if int(row["player_id"]) not in unavailable]
    ratings_by_player: dict[int, dict[str, int]] = {}
    if player_ids and table_exists(con, "player_ratings"):
        placeholders = ",".join("?" for _ in player_ids)
        for row in con.execute(
            f"""
            SELECT player_id, rating_key, rating_value
            FROM player_ratings
            WHERE season = ? AND player_id IN ({placeholders})
            """,
            [season, *player_ids],
        ):
            ratings_by_player.setdefault(int(row["player_id"]), {})[str(row["rating_key"])] = int(row["rating_value"])
    contexts = injury_context_by_player(con, player_ids, as_of_date)
    players: list[PracticePlayerSnapshot] = []
    for row in rows:
        player_id = int(row["player_id"])
        if player_id in unavailable:
            continue
        ratings = ratings_by_player.get(player_id, {}).copy()
        ratings.setdefault("durability", int(row["injury_prone"] or 50))
        ratings.setdefault("speed", int(row["speed"] or 50))
        ratings.setdefault("strength", int(row["strength"] or 50))
        ratings.setdefault("agility", int(row["agility"] or 50))
        ratings.setdefault("stamina", int(row["overall"] or 55))
        context = contexts.get(player_id, {})
        metadata = {
            "age": int(row["age"] or 26),
            "injury_prone": int(row["injury_prone"] or 50),
            "injury_history_risk": float(context.get("risk_score") or 0.0),
            "injury_body_risks": context.get("body_risks", {}),
        }
        players.append(
            PracticePlayerSnapshot(
                player_id=player_id,
                name=f"{row['first_name']} {row['last_name']}",
                position=str(row["position"]),
                team_id=int(row["team_id"]),
                ratings=ratings,
                metadata=metadata,
            )
        )
    return players


def practice_candidate_weight(player: PracticePlayerSnapshot) -> float:
    position = player.position
    position_weight = {
        "RB": 1.18,
        "FB": 1.10,
        "WR": 1.10,
        "TE": 1.08,
        "OT": 1.08,
        "OG": 1.07,
        "C": 1.06,
        "EDGE": 1.06,
        "IDL": 1.06,
        "DT": 1.06,
        "NT": 1.04,
        "ILB": 1.04,
        "OLB": 1.04,
        "CB": 1.08,
        "NB": 1.08,
        "FS": 1.06,
        "SS": 1.06,
        "QB": 0.50,
        "K": 0.25,
        "P": 0.25,
        "LS": 0.35,
    }.get(position, 1.0)
    role_weight = 0.75 + max(0.0, player.rating("stamina", 55) - 50.0) * 0.012
    durability = player.rating("durability", 60)
    durability_weight = 1.0 + max(0.0, 65.0 - durability) * 0.018
    history_weight = 1.0 + float(player.metadata.get("injury_history_risk", 0.0) or 0.0) * 0.25
    return max(0.05, position_weight * role_weight * durability_weight * history_weight)


def weighted_practice_choice(
    rng: random.Random,
    players: list[PracticePlayerSnapshot],
    used: set[int],
) -> PracticePlayerSnapshot | None:
    weighted = [(player, practice_candidate_weight(player)) for player in players if player.player_id not in used]
    total = sum(weight for _player, weight in weighted)
    if total <= 0:
        return None
    roll = rng.random() * total
    cursor = 0.0
    for player, weight in weighted:
        cursor += weight
        if roll <= cursor:
            return player
    return weighted[-1][0] if weighted else None


def create_weekly_practice_injuries(
    con: sqlite3.Connection,
    *,
    season: int,
    week: int,
    seed: int | None = None,
    apply: bool = False,
) -> list[InjuryEvent]:
    ensure_schema(con)
    practice_date = week_practice_date(con, season, week)
    resolve_available_injuries(con, practice_date)
    rng = random.Random(seed if seed is not None else (season * 1000 + week * 37 + 17))
    events: list[InjuryEvent] = []
    used: set[int] = set()
    play_number = 900000 + week * 100
    for team_id in active_team_ids(con):
        candidates = load_practice_candidates(con, season=season, team_id=team_id, as_of_date=practice_date)
        if not candidates:
            continue
        team_roll = rng.random()
        injuries_for_team = 0
        if team_roll < 0.72:
            injuries_for_team = 1
        if team_roll < 0.18:
            injuries_for_team = 2
        if team_roll < 0.04:
            injuries_for_team = 3
        for _ in range(injuries_for_team):
            player = weighted_practice_choice(rng, candidates, used)
            if player is None:
                break
            used.add(player.player_id)
            high_impact = rng.random() < 0.025
            item = choose_catalog_entry(rng, player, mechanism="practice", high_impact=high_impact)
            days = expected_days_for_injury(rng, player, item)
            status = status_for_expected_days(days)
            body_risk = existing_body_risk(player, item.body_part)
            severity = "severe" if item.severity_bucket == "major" and days >= 180 else item.severity_bucket
            description = f"{player.name} picked up a {item.label.lower()} during the practice week."
            if body_risk:
                description += " Prior body-area history increased the risk."
            event = InjuryEvent(
                play_number=play_number,
                quarter=0,
                clock_tenths=0,
                player_id=player.player_id,
                team_id=team_id,
                opponent_player_id=None,
                opponent_team_id=None,
                injury_code=item.injury_code,
                injury_label=item.label,
                body_region=item.body_region,
                body_part=item.body_part,
                severity=severity,
                mechanism="practice",
                expected_days=days,
                expected_games=expected_games(days),
                status=status,
                description=description,
            )
            play_number += 1
            events.append(event)
    if apply and events:
        retract_weekly_practice_injuries(con, season=season, week=week, practice_date=practice_date)
        persist_injury_events(
            con,
            events,
            season=season,
            week=week,
            game_date=practice_date,
            source=WEEKLY_PRACTICE_SOURCE,
            source_run_id=None,
            schedule_game_id=None,
            run_id=None,
        )
    return events


def retract_weekly_practice_injuries(
    con: sqlite3.Connection,
    *,
    season: int,
    week: int,
    practice_date: str | None = None,
) -> int:
    ensure_schema(con)
    if practice_date is None:
        practice_date = week_practice_date(con, season, week)
    rows = con.execute(
        """
        SELECT DISTINCT player_id
        FROM game_injury_events
        WHERE season = ?
          AND week = ?
          AND source = ?
        """,
        (season, week, WEEKLY_PRACTICE_SOURCE),
    ).fetchall()
    player_ids = {int(row["player_id"]) for row in rows}
    active_rows = con.execute(
        """
        SELECT active_injury_id, injury_history_id
        FROM active_player_injuries
        WHERE source = ?
          AND schedule_game_id IS NULL
          AND start_date = ?
        """,
        (WEEKLY_PRACTICE_SOURCE, practice_date),
    ).fetchall()
    history_ids = [int(row["injury_history_id"]) for row in active_rows if row["injury_history_id"] is not None]
    con.execute(
        """
        DELETE FROM active_player_injuries
        WHERE source = ?
          AND schedule_game_id IS NULL
          AND start_date = ?
        """,
        (WEEKLY_PRACTICE_SOURCE, practice_date),
    )
    if history_ids:
        placeholders = ",".join("?" for _ in history_ids)
        con.execute(
            f"DELETE FROM player_injury_history WHERE injury_history_id IN ({placeholders}) AND source = ?",
            (*history_ids, WEEKLY_PRACTICE_SOURCE),
        )
    cur = con.execute(
        """
        DELETE FROM game_injury_events
        WHERE season = ?
          AND week = ?
          AND source = ?
        """,
        (season, week, WEEKLY_PRACTICE_SOURCE),
    )
    for player_id in player_ids:
        reset_player_status_if_available(con, player_id)
    return int(cur.rowcount or 0)


def retract_schedule_game_injuries(con: sqlite3.Connection, schedule_game_id: int) -> int:
    ensure_schema(con)
    rows = con.execute(
        """
        SELECT DISTINCT player_id
        FROM game_injury_events
        WHERE schedule_game_id = ?
          AND source = ?
        """,
        (schedule_game_id, MATCH_SOURCE),
    ).fetchall()
    player_ids = {int(row["player_id"]) for row in rows}
    active_rows = con.execute(
        """
        SELECT active_injury_id, injury_history_id
        FROM active_player_injuries
        WHERE schedule_game_id = ?
          AND source = ?
        """,
        (schedule_game_id, MATCH_SOURCE),
    ).fetchall()
    history_ids = [int(row["injury_history_id"]) for row in active_rows if row["injury_history_id"] is not None]
    con.execute(
        "DELETE FROM active_player_injuries WHERE schedule_game_id = ? AND source = ?",
        (schedule_game_id, MATCH_SOURCE),
    )
    if history_ids:
        placeholders = ",".join("?" for _ in history_ids)
        con.execute(
            f"DELETE FROM player_injury_history WHERE injury_history_id IN ({placeholders}) AND source = ?",
            (*history_ids, MATCH_SOURCE),
        )
    cur = con.execute(
        "DELETE FROM game_injury_events WHERE schedule_game_id = ? AND source = ?",
        (schedule_game_id, MATCH_SOURCE),
    )
    for player_id in player_ids:
        reset_player_status_if_available(con, player_id)
    return int(cur.rowcount or 0)
