#!/usr/bin/env python3
"""Training camp and preseason processing hooks.

This layer gives the dead space between post-draft free agency and Week 1 some
football texture. It records training camp risers/sliders, occasional trait
reveals, and preseason snap/performance context without pretending preseason is
the same thing as the regular season.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import sys

import free_agency_processor
import injury_notifications
import league_schedule
import league_news
import player_personalities
import pro_player_fog
import roster_cutdown
import season_storylines
import scouting


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
OFFICIAL_2026_PRESEASON_PATH = ROOT / "data" / "schedules" / "2026_preseason.json"
SOURCE = "preseason_processor"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import match_engine  # noqa: E402
from engine import injury_model  # noqa: E402


POS_ORDER = {
    "QB": 1,
    "RB": 2,
    "FB": 2,
    "WR": 3,
    "SWR": 3,
    "TE": 4,
    "LT": 5,
    "RT": 5,
    "OT": 5,
    "LG": 6,
    "RG": 6,
    "C": 6,
    "OG": 6,
    "IOL": 6,
    "EDGE": 7,
    "DE": 7,
    "OLB": 7,
    "IDL": 8,
    "DT": 8,
    "NT": 8,
    "LB": 9,
    "ILB": 9,
    "MLB": 9,
    "CB": 10,
    "NB": 10,
    "FS": 11,
    "SS": 11,
    "S": 11,
    "K": 12,
    "P": 13,
    "LS": 14,
}


POSITIVE_TRAITS = {
    "lunch_pail",
    "film_junkie",
    "quiet_professional",
    "natural_leader",
    "mentor",
    "chip_on_shoulder",
    "big_stage",
    "coach_connector",
}

NEGATIVE_TRAITS = {
    "streaky_confidence",
    "locker_room_distraction",
    "off_field_issue",
    "greedy",
}

OFFSEASON_WORKOUT_INJURY_EVENTS = {
    "RETURNING_HC_OFFSEASON_PROGRAMS_BEGIN": {
        "event_type": "offseason_workout",
        "source": "offseason_workout",
        "one_injury_chance": 0.10,
        "two_injury_chance": 0.006,
        "rookie_only": False,
    },
    "ROOKIE_MINICAMP_WINDOW_1": {
        "event_type": "rookie_minicamp",
        "source": "rookie_minicamp",
        "one_injury_chance": 0.045,
        "two_injury_chance": 0.002,
        "rookie_only": True,
    },
    "ROOKIE_MINICAMP_WINDOW_2": {
        "event_type": "rookie_minicamp",
        "source": "rookie_minicamp",
        "one_injury_chance": 0.04,
        "two_injury_chance": 0.002,
        "rookie_only": True,
    },
    "ROOKIE_DEVELOPMENT_PROGRAM_BEGIN": {
        "event_type": "rookie_development_program",
        "source": "rookie_development_program",
        "one_injury_chance": 0.055,
        "two_injury_chance": 0.003,
        "rookie_only": True,
    },
    "ROOKIE_READINESS_PROGRAM": {
        "event_type": "rookie_readiness_program",
        "source": "rookie_readiness_program",
        "one_injury_chance": 0.035,
        "two_injury_chance": 0.001,
        "rookie_only": True,
    },
    "ROOKIE_TRAINING_CAMP_REPORTING_OPENS": {
        "event_type": "rookie_training_camp",
        "source": "rookie_training_camp",
        "one_injury_chance": 0.065,
        "two_injury_chance": 0.004,
        "rookie_only": True,
    },
}

TRAIT_DISPLAY = {
    "lunch_pail": "steady worker",
    "film_junkie": "detail-oriented",
    "quiet_professional": "low-maintenance pro",
    "natural_leader": "natural leader",
    "mentor": "mentor",
    "chip_on_shoulder": "chip-on-shoulder competitor",
    "big_stage": "big-stage personality",
    "coach_connector": "coachable connector",
    "streaky_confidence": "confidence can run hot and cold",
    "locker_room_distraction": "locker-room maintenance concern",
    "off_field_issue": "off-field maintenance concern",
    "greedy": "contract/status driven",
}


@dataclass(frozen=True)
class PreseasonResult:
    event_type: str
    inserted_events: int = 0
    injuries: int = 0
    snap_rows: int = 0
    games_scheduled: int = 0
    games_simmed: int = 0
    inbox_messages: int = 0
    league_news_items: int = 0
    fa_offers: int = 0
    fa_signings: int = 0
    fa_demand_drops: int = 0
    fa_retirements: int = 0


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
    league_schedule.ensure_schema(con)
    match_engine.ensure_schema(con)
    player_personalities.ensure_schema(con)
    season_storylines.ensure_schema(con)
    scouting.ensure_schema(con)
    league_news.ensure_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS preseason_camp_events (
            camp_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            event_date TEXT NOT NULL,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            impact_delta REAL NOT NULL DEFAULT 0,
            potential_delta REAL NOT NULL DEFAULT 0,
            trait_key TEXT,
            trait_revealed INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL,
            details TEXT,
            source TEXT NOT NULL DEFAULT 'preseason_processor',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(game_id, season, player_id, event_type, event_date)
        );

        CREATE INDEX IF NOT EXISTS idx_preseason_camp_events_game_player
            ON preseason_camp_events(game_id, season, player_id);

        CREATE TABLE IF NOT EXISTS preseason_player_snaps (
            snap_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            preseason_week INTEGER NOT NULL,
            event_date TEXT NOT NULL,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            offensive_snaps INTEGER NOT NULL DEFAULT 0,
            defensive_snaps INTEGER NOT NULL DEFAULT 0,
            special_teams_snaps INTEGER NOT NULL DEFAULT 0,
            performance_delta REAL NOT NULL DEFAULT 0,
            notes TEXT,
            source TEXT NOT NULL DEFAULT 'preseason_processor',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(game_id, season, preseason_week, player_id)
        );

        CREATE INDEX IF NOT EXISTS idx_preseason_player_snaps_game_player
            ON preseason_player_snaps(game_id, season, player_id);
        """
    )


def active_user_team_id(con: sqlite3.Connection) -> int | None:
    if not table_exists(con, "active_game_save_view"):
        return None
    row = con.execute("SELECT user_team_id FROM active_game_save_view LIMIT 1").fetchone()
    return int(row["user_team_id"]) if row and row["user_team_id"] is not None else None


def rookie_player_ids_for_team(con: sqlite3.Connection, team_id: int) -> set[int]:
    rows = con.execute(
        """
        SELECT player_id
        FROM players
        WHERE team_id = ?
          AND COALESCE(status, 'Active') NOT IN ('Retired', 'Released', 'Free Agent')
          AND (COALESCE(is_rookie, 0) = 1 OR COALESCE(years_exp, 0) = 0)
        """,
        (int(team_id),),
    ).fetchall()
    return {int(row["player_id"]) for row in rows}


def team_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT team_id, abbreviation FROM teams ORDER BY abbreviation").fetchall()


def regular_opponent_pairs(con: sqlite3.Connection, season: int) -> set[frozenset[int]]:
    rows = con.execute(
        """
        SELECT away_team_id, home_team_id
        FROM season_games
        WHERE season = ? AND game_type = 'REG'
        """,
        (season,),
    ).fetchall()
    return {frozenset((int(row["away_team_id"]), int(row["home_team_id"]))) for row in rows}


def team_ids_by_abbreviation(con: sqlite3.Connection) -> dict[str, int]:
    return {
        str(row["abbreviation"]): int(row["team_id"])
        for row in con.execute("SELECT team_id, abbreviation FROM teams").fetchall()
    }


def load_official_2026_preseason() -> dict[str, Any] | None:
    if not OFFICIAL_2026_PRESEASON_PATH.exists():
        return None
    payload = json.loads(OFFICIAL_2026_PRESEASON_PATH.read_text(encoding="utf-8"))
    games = payload.get("games") or []
    if len(games) != 48:
        raise ValueError(f"Official 2026 preseason schedule should contain 48 games, found {len(games)}.")
    return payload


def insert_official_preseason_schedule(con: sqlite3.Connection, *, season: int, payload: dict[str, Any]) -> int:
    ids = team_ids_by_abbreviation(con)
    missing = sorted(
        {
            str(game[side])
            for game in payload.get("games", [])
            for side in ("away", "home")
            if str(game[side]) not in ids
        }
    )
    if missing:
        raise ValueError(f"Official preseason schedule has unknown team abbreviations: {', '.join(missing)}")
    con.execute(
        """
        INSERT INTO season_schedule_sources (season, source_name, source_url, is_official, notes)
        VALUES (?, ?, ?, 1, ?)
        ON CONFLICT(season, source_name) DO UPDATE SET
            source_url = excluded.source_url,
            is_official = excluded.is_official,
            notes = excluded.notes
        """,
        (
            season,
            str(payload.get("source") or "NFL preseason schedule"),
            str(payload.get("source_url") or ""),
            str(payload.get("notes") or "Official preseason schedule."),
        ),
    )
    source_row = con.execute(
        "SELECT source_id FROM season_schedule_sources WHERE season = ? AND source_name = ?",
        (season, str(payload.get("source") or "NFL preseason schedule")),
    ).fetchone()
    source_id = int(source_row["source_id"]) if source_row else None
    games = sorted(
        payload["games"],
        key=lambda game: (
            int(game["week"]),
            str(game.get("game_date") or "9999-12-31"),
            str(game.get("game_time_et") or "99:99"),
            int(game.get("week_game_number") or 999),
        ),
    )
    dates_by_week: dict[int, list[str]] = {week: [] for week in range(1, 4)}
    for game in games:
        dates_by_week[int(game["week"])].append(str(game["game_date"]))
    for week in range(1, 4):
        week_dates = sorted(dates_by_week[week])
        con.execute(
            """
            INSERT INTO season_weeks (
                season, week, week_type, week_start_date, primary_game_date, status, notes
            )
            VALUES (?, ?, 'PRE', ?, ?, 'official_opponents', ?)
            ON CONFLICT(season, week, week_type) DO UPDATE SET
                week_start_date = excluded.week_start_date,
                primary_game_date = excluded.primary_game_date,
                status = excluded.status,
                notes = excluded.notes,
                updated_at = datetime('now')
            """,
            (
                season,
                week,
                week_dates[0],
                week_dates[-1],
                "Official NFL preseason opponents; non-national game dates sit inside official week windows.",
            ),
        )
    inserted = 0
    for number, game in enumerate(games, start=1):
        away = str(game["away"])
        home = str(game["home"])
        status = "preseason_official" if game.get("date_status") == "official" else "preseason_official_week_projected_date"
        notes = "Official 2026 NFL preseason matchup."
        if game.get("network"):
            notes += f" National TV: {game['network']}."
        if game.get("date_status") != "official":
            notes += " Date assigned inside official week window until club-specific kickoff is announced."
        before = con.total_changes
        con.execute(
            """
            INSERT OR IGNORE INTO season_games (
                season, week, game_type, week_game_number,
                away_team_id, home_team_id, game_date, game_time_et,
                schedule_status, source_id, matchup_bucket, notes
            )
            VALUES (?, ?, 'PRE', ?, ?, ?, ?, ?, ?, ?, 'PRESEASON', ?)
            """,
            (
                season,
                int(game["week"]),
                int(game.get("week_game_number") or number),
                ids[away],
                ids[home],
                str(game["game_date"]),
                str(game.get("game_time_et") or ""),
                status,
                source_id,
                notes,
            ),
        )
        if con.total_changes > before:
            inserted += 1
    return inserted


def choose_preseason_pairing(
    rng: random.Random,
    team_ids: list[int],
    *,
    regular_pairs: set[frozenset[int]],
    used_pairs: set[frozenset[int]],
) -> list[tuple[int, int]]:
    best_pairs: list[tuple[int, int]] = []
    best_penalty: int | None = None
    for _attempt in range(1400):
        remaining = team_ids[:]
        rng.shuffle(remaining)
        pairs: list[tuple[int, int]] = []
        penalty = 0
        while remaining:
            team_id = remaining.pop()
            scored: list[tuple[int, float, int]] = []
            for candidate in remaining:
                pair_key = frozenset((team_id, candidate))
                score = 0
                if pair_key in regular_pairs:
                    score += 100
                if pair_key in used_pairs:
                    score += 25
                scored.append((score, rng.random(), candidate))
            score, _tie, opponent_id = min(scored)
            remaining.remove(opponent_id)
            penalty += score
            pairs.append((team_id, opponent_id))
        if best_penalty is None or penalty < best_penalty:
            best_penalty = penalty
            best_pairs = pairs
            if penalty == 0:
                break
    return best_pairs


def ensure_preseason_schedule(
    con: sqlite3.Connection,
    *,
    season: int,
    event_date: str,
    event_week: int,
    seed: str | int | None = None,
) -> int:
    ensure_schema(con)
    official_payload = load_official_2026_preseason() if season == 2026 else None
    expected_games = len(official_payload.get("games") or []) if official_payload else 48
    existing = con.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN played = 1 THEN 1 ELSE 0 END) AS played,
            SUM(CASE WHEN schedule_status LIKE 'preseason_official%' THEN 1 ELSE 0 END) AS official
        FROM season_games
        WHERE season = ? AND game_type = 'PRE'
        """,
        (season,),
    ).fetchone()
    total_existing = int(existing["total"] or 0) if existing else 0
    played_existing = int(existing["played"] or 0) if existing else 0
    official_existing = int(existing["official"] or 0) if existing else 0
    if official_payload and total_existing >= expected_games and official_existing >= expected_games:
        return 0
    if total_existing >= expected_games and not official_payload:
        return 0
    if total_existing and played_existing == 0:
        con.execute("DELETE FROM season_games WHERE season = ? AND game_type = 'PRE'", (season,))
        con.execute("DELETE FROM season_weeks WHERE season = ? AND week_type = 'PRE'", (season,))
    elif total_existing:
        return 0
    if official_payload:
        return insert_official_preseason_schedule(con, season=season, payload=official_payload)

    teams = [int(row["team_id"]) for row in team_rows(con)]
    if len(teams) != 32:
        raise ValueError(f"Preseason schedule requires 32 teams, found {len(teams)}.")
    regular_pairs = regular_opponent_pairs(con, season)
    rng = random.Random(str(seed or f"{season}:preseason-schedule"))
    base_date = datetime.strptime(str(event_date)[:10], "%Y-%m-%d").date() - timedelta(days=7 * (event_week - 1))
    used_pairs: set[frozenset[int]] = set()
    home_counts = {team_id: 0 for team_id in teams}
    inserted = 0

    for week in range(1, 4):
        week_start = base_date + timedelta(days=7 * (week - 1))
        con.execute(
            """
            INSERT INTO season_weeks (
                season, week, week_type, week_start_date, primary_game_date, status, notes
            )
            VALUES (?, ?, 'PRE', ?, ?, 'projected', ?)
            ON CONFLICT(season, week, week_type) DO UPDATE SET
                week_start_date = excluded.week_start_date,
                primary_game_date = excluded.primary_game_date,
                notes = excluded.notes,
                updated_at = datetime('now')
            """,
            (
                season,
                week,
                week_start.isoformat(),
                week_start.isoformat(),
                "Generated preseason schedule; matchups avoid regular-season opponents when possible.",
            ),
        )
        pairs = choose_preseason_pairing(rng, teams, regular_pairs=regular_pairs, used_pairs=used_pairs)
        for number, (team_a, team_b) in enumerate(pairs, start=1):
            pair_key = frozenset((team_a, team_b))
            used_pairs.add(pair_key)
            if home_counts[team_a] < home_counts[team_b]:
                home_team_id, away_team_id = team_a, team_b
            elif home_counts[team_b] < home_counts[team_a]:
                home_team_id, away_team_id = team_b, team_a
            elif rng.random() < 0.5:
                home_team_id, away_team_id = team_a, team_b
            else:
                home_team_id, away_team_id = team_b, team_a
            home_counts[home_team_id] += 1
            game_day = week_start + timedelta(days=(number - 1) // 4)
            game_time = ("8:00 PM", "7:00 PM", "4:00 PM", "1:00 PM")[(number - 1) % 4]
            before = con.total_changes
            con.execute(
                """
                INSERT OR IGNORE INTO season_games (
                    season, week, game_type, week_game_number,
                    away_team_id, home_team_id, game_date, game_time_et,
                    schedule_status, matchup_bucket, notes
                )
                VALUES (?, ?, 'PRE', ?, ?, ?, ?, ?, 'preseason_generated', 'PRESEASON', ?)
                """,
                (
                    season,
                    week,
                    number,
                    away_team_id,
                    home_team_id,
                    game_day.isoformat(),
                    game_time,
                    (
                        "Generated preseason matchup. Avoids a regular-season opponent."
                        if pair_key not in regular_pairs
                        else "Generated preseason matchup; regular-season opponent fallback was required."
                    ),
                ),
            )
            if con.total_changes > before:
                inserted += 1
    return inserted


def team_roster(con: sqlite3.Connection, team_id: int) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT
            p.player_id,
            p.team_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            COALESCE(p.age, 26) AS age,
            COALESCE(p.years_exp, 0) AS years_exp,
            COALESCE(p.is_rookie, 0) AS is_rookie,
            COALESCE(p.overall, 50) AS overall,
            COALESCE(p.potential, COALESCE(p.overall, 50)) AS potential,
            COALESCE(p.status, 'Active') AS status,
            COALESCE(p.dev_trait, 'Normal') AS dev_trait
        FROM players p
        WHERE p.team_id = ?
          AND COALESCE(p.status, 'Active') NOT IN ('Free Agent', 'Retired')
        ORDER BY
            CASE COALESCE(p.status, 'Active') WHEN 'Active' THEN 0 WHEN 'Practice Squad' THEN 1 ELSE 2 END,
            COALESCE(p.overall, 50) DESC,
            p.player_id
        """,
        (team_id,),
    ).fetchall()


def load_traits(con: sqlite3.Connection, *, game_id: str, season: int) -> dict[int, dict[str, int]]:
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


def trait_score(traits: dict[str, int], keys: set[str]) -> float:
    return sum(max(0, int(traits.get(key, 0) or 0)) for key in keys) / 100.0


def camp_weight(player: sqlite3.Row, traits: dict[str, int], rng: random.Random) -> float:
    age = int(player["age"] or 26)
    exp = int(player["years_exp"] or 0)
    overall = int(player["overall"] or 50)
    potential = int(player["potential"] or overall)
    weight = 1.0
    if int(player["is_rookie"] or 0):
        weight += 3.6
    elif exp <= 2:
        weight += 2.4
    elif exp <= 4:
        weight += 1.0
    if str(player["status"]) == "Practice Squad":
        weight += 1.5
    if potential - overall >= 8:
        weight += 1.2
    if overall <= 66:
        weight += 0.7
    if age >= 30:
        weight += 0.5
    weight += trait_score(traits, POSITIVE_TRAITS | NEGATIVE_TRAITS) * 0.35
    return max(0.25, weight + rng.random() * 0.25)


def choose_weighted(rng: random.Random, players: list[sqlite3.Row], traits_by_player: dict[int, dict[str, int]], count: int) -> list[sqlite3.Row]:
    pool = list(players)
    chosen: list[sqlite3.Row] = []
    for _ in range(min(count, len(pool))):
        weights = [camp_weight(player, traits_by_player.get(int(player["player_id"]), {}), rng) for player in pool]
        pick = rng.choices(pool, weights=weights, k=1)[0]
        chosen.append(pick)
        pool.remove(pick)
    return chosen


def camp_outcome(player: sqlite3.Row, traits: dict[str, int], rng: random.Random) -> tuple[str, float, float, str]:
    age = int(player["age"] or 26)
    exp = int(player["years_exp"] or 0)
    overall = int(player["overall"] or 50)
    potential = int(player["potential"] or overall)
    positive_pull = 0.46
    positive_pull += min(0.15, max(0, potential - overall) * 0.012)
    positive_pull += trait_score(traits, POSITIVE_TRAITS) * 0.035
    positive_pull -= trait_score(traits, NEGATIVE_TRAITS) * 0.045
    if exp <= 2:
        positive_pull += 0.06
    if age >= 31:
        positive_pull -= 0.12
    positive_pull = max(0.18, min(0.72, positive_pull))

    if rng.random() < positive_pull:
        delta = rng.uniform(0.20, 0.95)
        if exp <= 2:
            delta += rng.uniform(0.05, 0.35)
        if potential - overall >= 8:
            delta += rng.uniform(0.05, 0.28)
        return "camp_riser", round(delta, 3), round(delta * rng.uniform(0.10, 0.32), 3), "Riser"

    delta = -rng.uniform(0.18, 0.85)
    if age >= 31:
        delta -= rng.uniform(0.05, 0.35)
    if trait_score(traits, NEGATIVE_TRAITS) >= 0.55:
        delta -= rng.uniform(0.05, 0.22)
    return "camp_slump", round(delta, 3), round(delta * rng.uniform(0.05, 0.22), 3), "Slump"


def reveal_trait(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    player_id: int,
    rng: random.Random,
) -> tuple[str | None, str | None]:
    rows = con.execute(
        """
        SELECT pp.trait_key, pp.intensity, ptd.display_name
        FROM player_personalities pp
        LEFT JOIN personality_trait_definitions ptd ON ptd.trait_key = pp.trait_key
        WHERE pp.game_id = ?
          AND pp.season = ?
          AND pp.player_id = ?
          AND COALESCE(pp.hidden, 1) = 1
        ORDER BY ABS(COALESCE(pp.intensity, 0)) DESC, pp.trait_key
        """,
        (game_id, season, player_id),
    ).fetchall()
    if not rows:
        return None, None
    row = rng.choice(rows[: min(3, len(rows))])
    con.execute(
        """
        UPDATE player_personalities
        SET hidden = 0
        WHERE game_id = ? AND season = ? AND player_id = ? AND trait_key = ?
        """,
        (game_id, season, player_id, row["trait_key"]),
    )
    return str(row["trait_key"]), str(row["display_name"] or TRAIT_DISPLAY.get(str(row["trait_key"]), row["trait_key"]))


def insert_camp_event(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    event_date: str,
    player: sqlite3.Row,
    event_type: str,
    impact_delta: float,
    potential_delta: float,
    title: str,
    details: str,
    trait_key: str | None = None,
    trait_revealed: bool = False,
) -> bool:
    before = con.total_changes
    con.execute(
        """
        INSERT OR IGNORE INTO preseason_camp_events (
            game_id, season, event_date, team_id, player_id, event_type,
            impact_delta, potential_delta, trait_key, trait_revealed, title, details
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_id,
            season,
            event_date,
            int(player["team_id"]),
            int(player["player_id"]),
            event_type,
            impact_delta,
            potential_delta,
            trait_key,
            1 if trait_revealed else 0,
            title,
            details,
        ),
    )
    return con.total_changes > before


def process_free_agency_movement(
    con: sqlite3.Connection,
    *,
    league_year: int,
    event_date: str,
    cpu_offers: int,
    days: int,
    seed: str,
) -> dict[str, int]:
    try:
        if not table_exists(con, "free_agency_periods"):
            return {"cpu_offers": 0, "signings": 0, "demand_drops": 0, "retirements": 0}
        period = free_agency_processor.current_period(con, league_year)
        if not period or str(period["status"]) != "active":
            return {"cpu_offers": 0, "signings": 0, "demand_drops": 0, "retirements": 0}
        args = SimpleNamespace(
            league_year=league_year,
            cpu_offers=cpu_offers,
            signing_limit=None,
            seed=seed,
            force=True,
            no_cap_snapshot=True,
            skip_cap_cleanup=True,
            sync_game_date=False,
            apply=True,
        )
        result = free_agency_processor.process_tick(con, args, days=max(1, days))
        negative_cap = con.execute(
            "SELECT 1 FROM team_cap_view WHERE cap_space < 0 LIMIT 1"
        ).fetchone()
        if negative_cap:
            user_team = free_agency_processor.cpu_excluded_user_team(con, args)
            cleanup = free_agency_processor.cpu_cap_compliance_sweep(
                con,
                league_year,
                user_team=user_team,
                min_space=1_000_000,
                max_moves_per_team=4,
                max_teams=16,
                time_budget_seconds=12.0,
                write_snapshot=False,
            )
            result["cap_cleanup_teams"] = int(cleanup.get("teams", 0) or 0)
            result["cap_cleanup_restructures"] = int(cleanup.get("restructures", 0) or 0)
            result["cap_cleanup_releases"] = int(cleanup.get("releases", 0) or 0)
            result["cap_cleanup_still_over"] = int(cleanup.get("still_over", 0) or 0)
        return result
    except Exception as exc:
        free_agency_processor.log_event(
            con,
            league_year=league_year,
            event_date=event_date,
            event_hour=None,
            event_type="preseason_market_skip",
            message=f"Preseason free-agency movement skipped: {exc}",
        )
        return {"cpu_offers": 0, "signings": 0, "demand_drops": 0, "retirements": 0}


def process_workout_injuries(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    event_date: str,
    event_type: str,
    source: str,
    seed: str | int | None,
    one_injury_chance: float,
    two_injury_chance: float,
    rookie_only: bool = False,
) -> int:
    injury_model.ensure_schema(con)
    existing = con.execute(
        """
        SELECT COUNT(*) AS count
        FROM game_injury_events
        WHERE season = ?
          AND game_date = ?
          AND source = ?
        """,
        (int(season), event_date, source),
    ).fetchone()
    if existing and int(existing["count"] or 0) > 0:
        return 0

    injury_model.resolve_available_injuries(con, event_date)
    marker = injury_notifications.max_event_id(con)
    rng = random.Random(str(seed or f"{game_id}:{season}:{event_date}:{event_type}:injuries"))
    context = {
        "offseason_workout": "during offseason workouts",
        "rookie_minicamp": "during rookie minicamp",
        "rookie_development_program": "during rookie development work",
        "rookie_readiness_program": "during rookie readiness work",
        "rookie_training_camp": "during rookie training camp",
        "training_camp": "during training camp",
    }.get(event_type, "during team workouts")
    events: list[injury_model.InjuryEvent] = []
    used: set[int] = set()
    play_number = 860000

    for team_id in injury_model.active_team_ids(con):
        candidates = injury_model.load_practice_candidates(
            con,
            season=season,
            team_id=team_id,
            as_of_date=event_date,
        )
        if rookie_only:
            rookie_ids = rookie_player_ids_for_team(con, team_id)
            candidates = [player for player in candidates if player.player_id in rookie_ids]
        if not candidates:
            continue
        team_roll = rng.random()
        injuries_for_team = 0
        if team_roll < one_injury_chance:
            injuries_for_team = 1
        if team_roll < two_injury_chance:
            injuries_for_team = 2
        for _ in range(injuries_for_team):
            player = injury_model.weighted_practice_choice(rng, candidates, used)
            if player is None:
                break
            used.add(player.player_id)
            high_impact = rng.random() < (0.018 if event_type == "training_camp" else 0.010)
            item = injury_model.choose_catalog_entry(
                rng,
                player,
                mechanism="practice",
                high_impact=high_impact,
            )
            days = injury_model.expected_days_for_injury(rng, player, item)
            status = injury_model.status_for_expected_days(days)
            body_risk = injury_model.existing_body_risk(player, item.body_part)
            description = f"{player.name} picked up a {item.label.lower()} {context}."
            if body_risk:
                description += " Prior body-area history increased the risk."
            severity = "severe" if item.severity_bucket == "major" and days >= 180 else item.severity_bucket
            events.append(
                injury_model.InjuryEvent(
                    play_number=play_number,
                    quarter=0,
                    clock_tenths=0,
                    player_id=player.player_id,
                    team_id=player.team_id,
                    opponent_player_id=None,
                    opponent_team_id=None,
                    injury_code=item.injury_code,
                    injury_label=item.label,
                    body_region=item.body_region,
                    body_part=item.body_part,
                    severity=severity,
                    mechanism="practice",
                    expected_days=days,
                    expected_games=injury_model.expected_games(days),
                    status=status,
                    description=description,
                )
            )
            play_number += 1

    persisted = injury_model.persist_injury_events(
        con,
        events,
        season=season,
        week=0,
        game_date=event_date,
        source=source,
        source_run_id=None,
        schedule_game_id=None,
        run_id=None,
    )
    if persisted:
        injury_notifications.create_injury_notifications(con, min_event_id=marker)
    return persisted


def process_post_preseason_week_three_market_pass(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    event_date: str,
    seed: str | int | None,
) -> dict[str, int]:
    if not table_exists(con, "active_player_injuries"):
        return {"signings": 0, "teams": 0, "specialist_signings": 0}
    roster_cutdown.ensure_cutdown_schema(con)
    user_team_id = active_user_team_id(con)
    rng = random.Random(str(seed or f"{game_id}:{season}:{event_date}:post-pre3-depth"))
    pressure_rows = con.execute(
        """
        SELECT
            t.team_id,
            t.abbreviation,
            p.position,
            COUNT(*) AS injuries
        FROM active_player_injuries api
        JOIN players p ON p.player_id = api.player_id
        JOIN teams t ON t.team_id = p.team_id
        WHERE api.resolved_at IS NULL
          AND api.status IN ('IR', 'PUP', 'NFI', 'Out', 'Doubtful')
          AND COALESCE(p.status, 'Active') NOT IN ('Free Agent', 'Retired', 'Released')
        GROUP BY t.team_id, t.abbreviation, p.position
        ORDER BY injuries DESC, t.abbreviation, p.position
        """,
    ).fetchall()
    teams = {int(row["team_id"]): row for row in team_rows(con)}
    signed_players: set[int] = set()
    signed_teams: set[int] = set()
    signings = 0
    for row in pressure_rows:
        if signings >= 12:
            break
        team_id = int(row["team_id"])
        if user_team_id is not None and team_id == user_team_id:
            continue
        if team_id in signed_teams:
            continue
        group = roster_cutdown.POSITION_TO_GROUP.get(str(row["position"] or "").upper(), "OTHER")
        if group in {"OTHER", "ST"}:
            continue
        minimum = int(roster_cutdown.MIN_ACTIVE_BY_GROUP.get(group, 0) or 0)
        if minimum and roster_cutdown.active_group_count(con, team_id, group) >= minimum + 1:
            continue
        player = roster_cutdown.best_free_agent_group_replacement(con, group=group)
        if not player:
            continue
        player_id = int(player["player_id"])
        if player_id in signed_players:
            continue
        if int(player["overall"] or 0) > 73 and rng.random() < 0.82:
            continue
        team = teams.get(team_id)
        if not team:
            continue
        roster_cutdown.sign_free_agent_replacement(
            con,
            player=player,
            team=team,
            season=season,
            reason="CPU post-preseason injury depth signing after Week 3.",
        )
        signed_players.add(player_id)
        signed_teams.add(team_id)
        signings += 1
    if signings:
        roster_cutdown.sync_team_cap_space(con)
    return {"signings": signings, "teams": len(signed_teams), "specialist_signings": 0}


def process_training_camp(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    event_date: str,
    seed: str | int | None = None,
    emit_messages: bool = True,
    process_market: bool = True,
) -> PreseasonResult:
    rng = random.Random(str(seed or f"{game_id}:{season}:{event_date}:camp"))
    traits_by_player = load_traits(con, game_id=game_id, season=season)
    user_team_id = active_user_team_id(con)
    inserted = 0
    inbox = 0
    news = 0

    for team in team_rows(con):
        roster = team_roster(con, int(team["team_id"]))
        if not roster:
            continue
        count = rng.randint(2, 4)
        for player in choose_weighted(rng, roster, traits_by_player, count):
            player_id = int(player["player_id"])
            traits = traits_by_player.get(player_id, {})
            event_type, delta, potential_delta, label = camp_outcome(player, traits, rng)
            trait_key = None
            trait_label = None
            trait_revealed = False
            if rng.random() < (0.10 if int(player["team_id"]) != user_team_id else 0.22):
                trait_key, trait_label = reveal_trait(con, game_id=game_id, season=season, player_id=player_id, rng=rng)
                trait_revealed = trait_key is not None
            trend = "stood out" if delta > 0 else "lost ground"
            player_name = str(player["player_name"])
            title = f"{player_name} {trend} in camp"
            detail_bits = [
                f"{player_name} ({player['position']}) {trend} during training camp evaluation.",
                f"Camp impact {delta:+.2f}; this will feed the season progression context.",
            ]
            if trait_label:
                detail_bits.append(f"Coaches also got a clearer read: {trait_label}.")
            details = " ".join(detail_bits)
            if insert_camp_event(
                con,
                game_id=game_id,
                season=season,
                event_date=event_date,
                player=player,
                event_type=event_type if not trait_revealed else f"{event_type}_trait_reveal",
                impact_delta=delta,
                potential_delta=potential_delta,
                trait_key=trait_key,
                trait_revealed=trait_revealed,
                title=title,
                details=details,
            ):
                inserted += 1
                pro_player_fog.apply_evaluation_event(
                    con,
                    game_id=game_id,
                    player_id=player_id,
                    team_id=int(player["team_id"]),
                    season=season,
                    event_date=event_date,
                    event_type=event_type if not trait_revealed else f"{event_type}_trait_reveal",
                    signal_strength=delta + potential_delta,
                    snap_count=0,
                    source="training_camp_pro_fog",
                    notes="Training camp report sharpened the staff evaluation read.",
                )
                if emit_messages and int(player["team_id"]) == user_team_id:
                    scouting.add_inbox_message(
                        con,
                        game_id=game_id,
                        title=title,
                        body=pro_player_fog.append_event_read_note(
                            details,
                            con,
                            game_id=game_id,
                            team_id=int(player["team_id"]),
                            player_id=player_id,
                            season=season,
                        ),
                        category="Player Development",
                        priority="high" if abs(delta) >= 0.85 or trait_revealed else "normal",
                        source="Coaching Staff",
                        message_date=event_date,
                        related_table="players",
                        related_id=player_id,
                    )
                    inbox += 1
                if emit_messages and abs(delta) >= 1.0 and rng.random() < 0.18:
                    news_id = league_news.add_news_item(
                        con,
                        game_id=game_id,
                        news_date=event_date,
                        category="Training Camp",
                        priority="normal",
                        scope="league",
                        source="Camp Wire",
                        title=title,
                        body=details,
                        related_table="players",
                        related_id=player_id,
                        tags=["training_camp", "development", str(player["position"])],
                        is_major=False,
                        fingerprint=league_news.fingerprint_for("preseason-camp", game_id, season, player_id, event_type, event_date),
                    )
                    if news_id is not None:
                        news += 1

    storyline_result = season_storylines.process_camp_storylines(
        con,
        game_id=game_id,
        season=season,
        event_date=event_date,
        seed=f"{game_id}:{season}:{event_date}:camp-storylines",
        emit_messages=emit_messages,
    )
    inserted += int(storyline_result.get("inserted", 0))
    inbox += int(storyline_result.get("inbox", 0))
    news += int(storyline_result.get("news", 0))

    camp_injuries = process_workout_injuries(
        con,
        game_id=game_id,
        season=season,
        event_date=event_date,
        event_type="training_camp",
        source="training_camp_practice",
        seed=f"{game_id}:{season}:{event_date}:camp-injuries",
        one_injury_chance=0.18,
        two_injury_chance=0.018,
        rookie_only=False,
    )

    fa = (
        process_free_agency_movement(
            con,
            league_year=season,
            event_date=event_date,
            cpu_offers=28,
            days=3,
            seed=f"{game_id}:{season}:{event_date}:camp-fa",
        )
        if process_market
        else {"cpu_offers": 0, "signings": 0, "demand_drops": 0, "retirements": 0}
    )
    return PreseasonResult(
        event_type="training_camp",
        inserted_events=inserted,
        injuries=camp_injuries,
        inbox_messages=inbox,
        league_news_items=news,
        fa_offers=int(fa.get("cpu_offers", 0)),
        fa_signings=int(fa.get("signings", 0)),
        fa_demand_drops=int(fa.get("demand_drops", 0)),
        fa_retirements=int(fa.get("retirements", 0)),
    )


def snap_weight(player: sqlite3.Row) -> float:
    exp = int(player["years_exp"] or 0)
    age = int(player["age"] or 26)
    overall = int(player["overall"] or 50)
    potential = int(player["potential"] or overall)
    status = str(player["status"] or "Active")
    weight = 0.6
    if int(player["is_rookie"] or 0):
        weight += 4.0
    elif exp <= 2:
        weight += 2.8
    elif exp <= 4:
        weight += 1.2
    if status == "Practice Squad":
        weight += 2.2
    if overall <= 66:
        weight += 1.4
    elif overall <= 72:
        weight += 0.8
    if potential - overall >= 8:
        weight += 1.0
    if age >= 30 and overall >= 76:
        weight *= 0.18
    elif age >= 28 and overall >= 80:
        weight *= 0.28
    return max(0.05, weight)


def distribute_preseason_snaps(
    rng: random.Random,
    roster: list[sqlite3.Row],
    *,
    preseason_week: int,
) -> list[tuple[sqlite3.Row, int, int, int, float, str]]:
    rows: list[tuple[sqlite3.Row, int, int, int, float, str]] = []
    by_pos: dict[str, list[sqlite3.Row]] = {}
    for player in roster:
        by_pos.setdefault(str(player["position"]), []).append(player)

    for pos, players in sorted(by_pos.items(), key=lambda item: POS_ORDER.get(item[0], 99)):
        sorted_players = sorted(players, key=lambda p: (snap_weight(p), int(p["potential"] or 0)), reverse=True)
        for index, player in enumerate(sorted_players):
            group = POS_ORDER.get(pos, 99)
            base = 0
            if group <= 6:
                base = max(0, int(rng.gauss(24, 7) - index * 5))
                def_snaps = 0
                off_snaps = base
            elif group <= 11:
                base = max(0, int(rng.gauss(24, 7) - index * 5))
                off_snaps = 0
                def_snaps = base
            else:
                off_snaps = 0
                def_snaps = 0
                base = max(4, int(rng.gauss(8, 2)))
            if preseason_week == 3 and int(player["years_exp"] or 0) >= 5 and int(player["overall"] or 50) >= 80:
                base = int(base * 0.30)
                off_snaps = int(off_snaps * 0.30)
                def_snaps = int(def_snaps * 0.30)
            if index >= 5:
                off_snaps = int(off_snaps * 0.55)
                def_snaps = int(def_snaps * 0.55)
            st_snaps = 0
            if int(player["overall"] or 50) <= 74 or int(player["years_exp"] or 0) <= 3:
                st_snaps = max(0, int(rng.gauss(9, 4)))
            if off_snaps + def_snaps + st_snaps <= 0:
                continue
            perf = rng.gauss(0.0, 0.22)
            if int(player["potential"] or 50) - int(player["overall"] or 50) >= 8:
                perf += rng.uniform(-0.04, 0.10)
            if int(player["is_rookie"] or 0):
                perf += rng.uniform(-0.08, 0.12)
            notes = "Young/depth preseason evaluation reps."
            rows.append((player, off_snaps, def_snaps, st_snaps, round(perf, 3), notes))
    return rows


def simulate_preseason_schedule_week(
    con: sqlite3.Connection,
    *,
    season: int,
    preseason_week: int,
    seed: str | int | None = None,
) -> int:
    rows = con.execute(
        """
        SELECT *
        FROM season_games
        WHERE season = ?
          AND game_type = 'PRE'
          AND week = ?
          AND played = 0
        ORDER BY game_date, COALESCE(game_time_et, '99:99'), week_game_number, game_id
        """,
        (season, preseason_week),
    ).fetchall()
    if not rows:
        return 0
    injury_marker = injury_notifications.max_event_id(con)
    saved = 0
    rng = random.Random(str(seed or f"{season}:preseason:{preseason_week}:games"))
    for row in rows:
        game_seed = rng.randint(1, 2_000_000_000)
        result = match_engine.simulate_game(
            con,
            away_team_id=int(row["away_team_id"]),
            home_team_id=int(row["home_team_id"]),
            season=int(row["season"]),
            week=int(row["week"]),
            schedule_game_id=int(row["game_id"]),
            seed=game_seed,
        )
        match_engine.persist_result(
            con,
            result,
            update_schedule=True,
            force=False,
            notes=f"Preseason Week {preseason_week}. Does not count toward regular-season standings or leaders.",
            rebuild_history=False,
        )
        saved += 1
    injury_notifications.create_injury_notifications(con, min_event_id=injury_marker)
    return saved


def process_preseason_week(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    preseason_week: int,
    event_date: str,
    seed: str | int | None = None,
    emit_messages: bool = True,
    process_market: bool = True,
    simulate_games: bool = False,
) -> PreseasonResult:
    rng = random.Random(str(seed or f"{game_id}:{season}:{event_date}:preseason:{preseason_week}"))
    games_scheduled = ensure_preseason_schedule(
        con,
        season=season,
        event_date=event_date,
        event_week=preseason_week,
        seed=f"{game_id}:{season}:preseason-schedule",
    )
    games_simmed = (
        simulate_preseason_schedule_week(
            con,
            season=season,
            preseason_week=preseason_week,
            seed=f"{game_id}:{season}:preseason-games:{preseason_week}",
        )
        if simulate_games
        else 0
    )
    snap_rows = 0
    inbox = 0
    news = 0
    existing_snap_rows = con.execute(
        """
        SELECT COUNT(*) AS count
        FROM preseason_player_snaps
        WHERE game_id = ?
          AND season = ?
          AND preseason_week = ?
        """,
        (game_id, season, preseason_week),
    ).fetchone()
    if existing_snap_rows and int(existing_snap_rows["count"] or 0) > 0:
        return PreseasonResult(
            event_type=f"preseason_week_{preseason_week}",
            snap_rows=0,
            games_scheduled=games_scheduled,
            games_simmed=games_simmed,
            inbox_messages=0,
            league_news_items=0,
            fa_offers=0,
            fa_signings=0,
            fa_demand_drops=0,
            fa_retirements=0,
        )
    user_team_id = active_user_team_id(con)

    for team in team_rows(con):
        roster = team_roster(con, int(team["team_id"]))
        for player, off_snaps, def_snaps, st_snaps, perf, notes in distribute_preseason_snaps(
            rng,
            roster,
            preseason_week=preseason_week,
        ):
            before = con.total_changes
            con.execute(
                """
                INSERT OR IGNORE INTO preseason_player_snaps (
                    game_id, season, preseason_week, event_date, team_id, player_id,
                    offensive_snaps, defensive_snaps, special_teams_snaps, performance_delta, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    game_id,
                    season,
                    preseason_week,
                    event_date,
                    int(player["team_id"]),
                    int(player["player_id"]),
                    off_snaps,
                    def_snaps,
                    st_snaps,
                    perf,
                    notes,
                ),
            )
            if con.total_changes <= before:
                continue
            snap_rows += 1
            total_snaps = off_snaps + def_snaps + st_snaps
            fog_snaps = pro_player_fog.effective_snap_evidence_from_counts(
                off_snaps,
                def_snaps,
                st_snaps,
                preseason=True,
            )
            if fog_snaps >= 6.0 or abs(perf) >= 0.42:
                pro_player_fog.apply_evaluation_event(
                    con,
                    game_id=game_id,
                    player_id=int(player["player_id"]),
                    team_id=int(player["team_id"]),
                    season=season,
                    event_date=event_date,
                    event_type=f"preseason_week_{preseason_week}_evaluation",
                    signal_strength=perf,
                    snap_count=fog_snaps,
                    source="preseason_pro_fog",
                    notes="Preseason reps gave the staff a discounted evaluation signal.",
                )
            if emit_messages and int(player["team_id"]) == user_team_id and (abs(perf) >= 0.42 or total_snaps >= 38):
                player_name = str(player["player_name"])
                title = f"Preseason read: {player_name}"
                body = (
                    f"{player_name} logged {total_snaps} preseason snap(s) in Week {preseason_week}. "
                    f"Coaches graded the outing {perf:+.2f}; this is a small input to long-term development."
                )
                scouting.add_inbox_message(
                    con,
                    game_id=game_id,
                    title=title,
                    body=pro_player_fog.append_event_read_note(
                        body,
                        con,
                        game_id=game_id,
                        team_id=int(player["team_id"]),
                        player_id=int(player["player_id"]),
                        season=season,
                    ),
                    category="Player Development",
                    priority="normal",
                    source="Coaching Staff",
                    message_date=event_date,
                    related_table="players",
                    related_id=int(player["player_id"]),
                )
                inbox += 1
            if emit_messages and abs(perf) >= 0.58 and total_snaps >= 30 and rng.random() < 0.08:
                player_name = str(player["player_name"])
                direction = "made a push" if perf > 0 else "had a rough preseason outing"
                news_id = league_news.add_news_item(
                    con,
                    game_id=game_id,
                    news_date=event_date,
                    category="Preseason",
                    priority="normal",
                    scope="league",
                    source="Preseason Wire",
                    title=f"{player_name} {direction}",
                    body=(
                        f"{player_name} ({player['position']}) played {total_snaps} snaps in preseason Week "
                        f"{preseason_week} and drew a {perf:+.2f} coaching grade."
                    ),
                    related_table="players",
                    related_id=int(player["player_id"]),
                    tags=["preseason", "development", str(player["position"])],
                    is_major=False,
                    fingerprint=league_news.fingerprint_for(
                        "preseason-snaps",
                        game_id,
                        season,
                        preseason_week,
                        int(player["player_id"]),
                    ),
                )
                if news_id is not None:
                    news += 1

    if preseason_week in {2, 3}:
        storyline_result = season_storylines.process_camp_storylines(
            con,
            game_id=game_id,
            season=season,
            event_date=event_date,
            seed=f"{game_id}:{season}:{event_date}:preseason-battles:{preseason_week}",
            emit_messages=emit_messages,
        )
        inbox += int(storyline_result.get("inbox", 0))
        news += int(storyline_result.get("news", 0))

    fa = (
        process_free_agency_movement(
            con,
            league_year=season,
            event_date=event_date,
            cpu_offers=18,
            days=4,
            seed=f"{game_id}:{season}:{event_date}:preseason-fa:{preseason_week}",
        )
        if process_market
        else {"cpu_offers": 0, "signings": 0, "demand_drops": 0, "retirements": 0}
    )
    post_preseason_fa = (
        process_post_preseason_week_three_market_pass(
            con,
            game_id=game_id,
            season=season,
            event_date=event_date,
            seed=f"{game_id}:{season}:{event_date}:post-pre3-depth",
        )
        if process_market and preseason_week == 3
        else {"signings": 0, "teams": 0, "specialist_signings": 0}
    )
    return PreseasonResult(
        event_type=f"preseason_week_{preseason_week}",
        snap_rows=snap_rows,
        games_scheduled=games_scheduled,
        games_simmed=games_simmed,
        inbox_messages=inbox,
        league_news_items=news,
        fa_offers=int(fa.get("cpu_offers", 0)),
        fa_signings=(
            int(fa.get("signings", 0))
            + int(post_preseason_fa.get("signings", 0))
            + int(post_preseason_fa.get("specialist_signings", 0))
        ),
        fa_demand_drops=int(fa.get("demand_drops", 0)),
        fa_retirements=int(fa.get("retirements", 0)),
    )


def result_summary(result: PreseasonResult) -> str:
    pieces = [
        f"{result.event_type}: evaluation events={result.inserted_events}",
        f"injuries={result.injuries}",
        f"snap rows={result.snap_rows}",
        f"games scheduled={result.games_scheduled}",
        f"games simmed={result.games_simmed}",
        f"inbox={result.inbox_messages}",
        f"news={result.league_news_items}",
        (
            "FA "
            f"offers={result.fa_offers}, signings={result.fa_signings}, "
            f"demand drops={result.fa_demand_drops}, retirements={result.fa_retirements}"
        ),
    ]
    return "; ".join(pieces)


def run_for_event(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    event_code: str,
    event_date: str,
    seed: str | int | None = None,
    emit_messages: bool = True,
    process_market: bool = True,
    simulate_games: bool = False,
) -> PreseasonResult | None:
    if event_code in OFFSEASON_WORKOUT_INJURY_EVENTS:
        config = OFFSEASON_WORKOUT_INJURY_EVENTS[event_code]
        injuries = process_workout_injuries(
            con,
            game_id=game_id,
            season=season,
            event_date=event_date,
            event_type=str(config["event_type"]),
            source=str(config["source"]),
            seed=seed or f"{game_id}:{season}:{event_code}:{event_date}:workout-injuries",
            one_injury_chance=float(config["one_injury_chance"]),
            two_injury_chance=float(config["two_injury_chance"]),
            rookie_only=bool(config["rookie_only"]),
        )
        return PreseasonResult(event_type=str(config["event_type"]), injuries=injuries)
    if event_code == "VETERAN_TRAINING_CAMP_REPORTING":
        return process_training_camp(
            con,
            game_id=game_id,
            season=season,
            event_date=event_date,
            seed=seed,
            emit_messages=emit_messages,
            process_market=process_market,
        )
    if event_code.startswith("PRESEASON_WEEK_"):
        try:
            week = int(event_code.rsplit("_", 1)[1])
        except ValueError:
            return None
        return process_preseason_week(
            con,
            game_id=game_id,
            season=season,
            preseason_week=week,
            event_date=event_date,
            seed=seed,
            emit_messages=emit_messages,
            process_market=process_market,
            simulate_games=simulate_games,
        )
    return None


def action_event(args: argparse.Namespace) -> None:
    with connect(args.db) as con:
        ensure_schema(con)
        con.commit()
        event_date = args.event_date
        if not event_date:
            row = con.execute(
                """
                SELECT event_start_date
                FROM calendar_events
                WHERE league_year = ?
                  AND event_code = ?
                ORDER BY event_start_date
                LIMIT 1
                """,
                (int(args.season), str(args.event_code)),
            ).fetchone()
            if row and row["event_start_date"]:
                event_date = str(row["event_start_date"])
            else:
                setting = con.execute(
                    "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
                ).fetchone()
                event_date = str(setting["setting_value"]) if setting and setting["setting_value"] else f"{int(args.season)}-08-01"
        con.execute("BEGIN")
        try:
            result = run_for_event(
                con,
                game_id=args.game_id,
                season=args.season,
                event_code=args.event_code,
                event_date=event_date,
                seed=args.seed,
                emit_messages=args.apply,
                process_market=args.apply,
                simulate_games=bool(args.simulate_games),
            )
            if args.apply:
                con.commit()
            else:
                con.rollback()
        except Exception:
            con.rollback()
            raise
    if result is None:
        print(f"No preseason hook for {args.event_code}.")
    else:
        print(result_summary(result))
        if not args.apply:
            print("Dry run only. Add --apply to save.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run training camp and preseason hooks.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    event = subparsers.add_parser("event", help="Process one calendar event hook.")
    event.add_argument("--game-id", required=True)
    event.add_argument("--season", type=int, required=True)
    event.add_argument("--event-code", required=True)
    event.add_argument("--event-date")
    event.add_argument("--seed")
    event.add_argument("--apply", action="store_true")
    event.add_argument(
        "--simulate-games",
        action="store_true",
        help="Also play scheduled preseason games for this event. Normal calendar events leave games for the sim buttons.",
    )
    event.set_defaults(func=action_event)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
