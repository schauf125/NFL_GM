#!/usr/bin/env python3
"""Build and inspect NFL GM Sim season schedules.

The 2026 schedule uses the official NFL.com weekly release stored in
data/schedules/2026_regular_season.json. If that data file is missing, the
tool can still fall back to the older official opponent matrix with generated
week placement.

For future seasons, the tool generates projected schedules from the NFL formula:
division home/away, rotating full-division opponents, same-place games, and the
17th interconference game. Week placement is intentionally projected because the
real NFL optimizer is not public and exact dates are released later.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import league_calendar


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
OFFICIAL_2026_SCHEDULE_PATH = ROOT / "data" / "schedules" / "2026_regular_season.json"
DEFAULT_SEASON = 2026
NFL_2026_OPPONENTS_URL = (
    "https://www.nfl.com/news/2026-nfl-season-team-by-team-opponents-for-every-game/"
)
NFL_2026_SCHEDULE_URL = "https://www.nfl.com/schedules/2026/by-week/reg-{week}"
NFL_OPS_SCHEDULE_FORMULA_URL = "https://operations.nfl.com/the-game/creating-the-nfl-schedule/"

DIVISIONS = {
    "AFC East": ["BUF", "MIA", "NE", "NYJ"],
    "AFC North": ["BAL", "CIN", "CLE", "PIT"],
    "AFC South": ["HOU", "IND", "JAX", "TEN"],
    "AFC West": ["DEN", "KC", "LAC", "LV"],
    "NFC East": ["DAL", "NYG", "PHI", "WAS"],
    "NFC North": ["CHI", "DET", "GB", "MIN"],
    "NFC South": ["ATL", "CAR", "NO", "TB"],
    "NFC West": ["ARI", "LAR", "SEA", "SF"],
}

TEAM_DIVISION = {
    team: division
    for division, teams in DIVISIONS.items()
    for team in teams
}

TEAM_CONFERENCE = {
    team: division.split()[0]
    for team, division in TEAM_DIVISION.items()
}

CONFERENCE_DIVISIONS = {
    "AFC": ["AFC East", "AFC North", "AFC South", "AFC West"],
    "NFC": ["NFC East", "NFC North", "NFC South", "NFC West"],
}

# Seeded from the known 2026 formula pairings, then rotated forward.
INTRACONFERENCE_FULL_ROTATION = [
    [(0, 3), (1, 2)],  # East-West, North-South
    [(0, 1), (2, 3)],  # East-North, South-West
    [(0, 2), (1, 3)],  # East-South, North-West
]
INTERCONFERENCE_2026_NFC_INDEX = [1, 2, 0, 3]
SEVENTEENTH_2026_NFC_INDEX = [3, 0, 1, 2]

WEEK_GAME_TARGETS = {
    1: 16,
    2: 16,
    3: 16,
    4: 16,
    5: 15,
    6: 14,
    7: 14,
    8: 14,
    9: 15,
    10: 14,
    11: 13,
    12: 16,
    13: 14,
    14: 15,
    15: 16,
    16: 16,
    17: 16,
    18: 16,
}

BYE_COUNTS = {week: (16 - games) * 2 for week, games in WEEK_GAME_TARGETS.items()}
MATCHUP_SORT = {
    "DIVISION": 0,
    "INTRACONFERENCE": 1,
    "INTERCONFERENCE": 2,
    "INTERCONFERENCE_17TH": 3,
}

# Official 2026 regular-season home opponents from NFL.com.
OFFICIAL_2026_HOME_OPPONENTS = {
    "NE": ["BUF", "MIA", "NYJ", "DEN", "GB", "LV", "MIN", "PIT"],
    "BUF": ["MIA", "NE", "NYJ", "BAL", "CHI", "DET", "KC", "LAC"],
    "MIA": ["BUF", "NE", "NYJ", "CHI", "CIN", "DET", "KC", "LAC"],
    "NYJ": ["BUF", "MIA", "NE", "CLE", "DEN", "GB", "LV", "MIN"],
    "PIT": ["BAL", "CIN", "CLE", "ATL", "CAR", "DEN", "HOU", "IND"],
    "BAL": ["CIN", "CLE", "PIT", "JAX", "LAC", "NO", "TB", "TEN"],
    "CIN": ["BAL", "CLE", "PIT", "JAX", "KC", "NO", "TB", "TEN"],
    "CLE": ["BAL", "CIN", "PIT", "ATL", "CAR", "HOU", "IND", "LV"],
    "JAX": ["HOU", "IND", "TEN", "CLE", "NE", "PHI", "PIT", "WAS"],
    "HOU": ["IND", "JAX", "TEN", "BAL", "BUF", "CIN", "DAL", "NYG"],
    "IND": ["HOU", "JAX", "TEN", "BAL", "CIN", "DAL", "MIA", "NYG"],
    "TEN": ["HOU", "IND", "JAX", "CLE", "NYJ", "PHI", "PIT", "WAS"],
    "DEN": ["KC", "LV", "LAC", "BUF", "JAX", "LAR", "MIA", "SEA"],
    "LAC": ["DEN", "KC", "LV", "ARI", "HOU", "NE", "NYJ", "SF"],
    "KC": ["DEN", "LV", "LAC", "ARI", "IND", "NE", "NYJ", "SF"],
    "LV": ["DEN", "KC", "LAC", "BUF", "LAR", "MIA", "SEA", "TEN"],
    "PHI": ["DAL", "NYG", "WAS", "CAR", "HOU", "IND", "LAR", "PIT", "SEA"],
    "DAL": ["NYG", "PHI", "WAS", "ARI", "BAL", "JAX", "SF", "TB", "TEN"],
    "WAS": ["DAL", "NYG", "PHI", "ATL", "CIN", "HOU", "IND", "LAR", "SEA"],
    "NYG": ["DAL", "PHI", "WAS", "ARI", "CLE", "JAX", "NO", "SF", "TEN"],
    "CHI": ["DET", "GB", "MIN", "JAX", "NE", "NO", "NYJ", "PHI", "TB"],
    "GB": ["CHI", "DET", "MIN", "ATL", "BUF", "CAR", "DAL", "HOU", "MIA"],
    "MIN": ["CHI", "DET", "GB", "ATL", "BUF", "CAR", "IND", "MIA", "WAS"],
    "DET": ["CHI", "GB", "MIN", "NE", "NO", "NYG", "NYJ", "TB", "TEN"],
    "CAR": ["ATL", "NO", "TB", "BAL", "CHI", "CIN", "DEN", "DET", "SEA"],
    "TB": ["ATL", "CAR", "NO", "CLE", "GB", "LAC", "LAR", "MIN", "PIT"],
    "ATL": ["CAR", "NO", "TB", "BAL", "CHI", "CIN", "DET", "KC", "SF"],
    "NO": ["ATL", "CAR", "TB", "ARI", "CLE", "GB", "LV", "MIN", "PIT"],
    "SEA": ["ARI", "LAR", "SF", "CHI", "DAL", "KC", "LAC", "NE", "NYG"],
    "LAR": ["ARI", "SF", "SEA", "BUF", "DAL", "GB", "KC", "LAC", "NYG"],
    "SF": ["ARI", "LAR", "SEA", "DEN", "LV", "MIA", "MIN", "PHI", "WAS"],
    "ARI": ["LAR", "SF", "SEA", "DEN", "DET", "LV", "NYJ", "PHI", "WAS"],
}

# Deterministic provisional schedule generated from the official home/away
# opponent matrix. Tuples are (away, home). Dates/times remain replaceable.
PROVISIONAL_2026_WEEKS = {
    1: [
        ("ATL", "MIN"), ("BAL", "HOU"), ("BUF", "NE"), ("CAR", "TB"),
        ("CIN", "IND"), ("DET", "CHI"), ("GB", "NO"), ("JAX", "NYG"),
        ("LAC", "LV"), ("LAR", "SEA"), ("MIA", "DEN"), ("NYJ", "TEN"),
        ("PHI", "DAL"), ("PIT", "CLE"), ("SF", "KC"), ("WAS", "ARI"),
    ],
    2: [
        ("ARI", "SF"), ("ATL", "PIT"), ("CAR", "PHI"), ("CHI", "MIN"),
        ("CLE", "CIN"), ("GB", "NYJ"), ("HOU", "WAS"), ("JAX", "BAL"),
        ("KC", "SEA"), ("LAR", "DEN"), ("LV", "NO"), ("MIA", "BUF"),
        ("NE", "LAC"), ("NYG", "DET"), ("TB", "DAL"), ("TEN", "IND"),
    ],
    3: [
        ("ATL", "TB"), ("CAR", "GB"), ("CIN", "WAS"), ("CLE", "NYJ"),
        ("DAL", "IND"), ("DEN", "PIT"), ("HOU", "JAX"), ("LAC", "KC"),
        ("MIA", "LV"), ("MIN", "DET"), ("NE", "BUF"), ("NO", "CHI"),
        ("PHI", "NYG"), ("SEA", "LAR"), ("SF", "ARI"), ("TEN", "BAL"),
    ],
    4: [
        ("ARI", "KC"), ("CAR", "PIT"), ("CIN", "ATL"), ("DAL", "GB"),
        ("IND", "CLE"), ("LAR", "WAS"), ("LV", "LAC"), ("MIA", "MIN"),
        ("NE", "CHI"), ("NO", "DET"), ("NYJ", "BUF"), ("PHI", "JAX"),
        ("SEA", "DEN"), ("SF", "NYG"), ("TB", "BAL"), ("TEN", "HOU"),
    ],
    5: [
        ("ATL", "CAR"), ("CHI", "MIA"), ("CLE", "NYG"), ("DAL", "WAS"),
        ("DEN", "KC"), ("DET", "BUF"), ("GB", "TB"), ("HOU", "IND"),
        ("LAR", "LV"), ("MIN", "NO"), ("NE", "NYJ"), ("PHI", "ARI"),
        ("PIT", "BAL"), ("SF", "SEA"), ("TEN", "JAX"),
    ],
    6: [
        ("ARI", "NYG"), ("CIN", "PIT"), ("DAL", "HOU"), ("DEN", "SF"),
        ("GB", "DET"), ("JAX", "IND"), ("KC", "ATL"), ("LAC", "BUF"),
        ("LAR", "PHI"), ("LV", "CLE"), ("MIN", "NE"), ("NO", "CAR"),
        ("NYJ", "CHI"), ("SEA", "WAS"),
    ],
    7: [
        ("BAL", "ATL"), ("BUF", "GB"), ("CLE", "NO"), ("DAL", "NYG"),
        ("DET", "ARI"), ("JAX", "CIN"), ("LAC", "DEN"), ("LAR", "SF"),
        ("MIA", "IND"), ("MIN", "NYJ"), ("PHI", "WAS"), ("SEA", "CAR"),
        ("TB", "CHI"), ("TEN", "LV"),
    ],
    8: [
        ("ATL", "NO"), ("BAL", "PIT"), ("BUF", "DEN"), ("DAL", "SEA"),
        ("GB", "MIN"), ("IND", "KC"), ("LAR", "TB"), ("LV", "ARI"),
        ("MIA", "SF"), ("NE", "DET"), ("NYG", "HOU"), ("NYJ", "LAC"),
        ("TEN", "CIN"), ("WAS", "JAX"),
    ],
    9: [
        ("ARI", "NO"), ("BAL", "BUF"), ("CHI", "CAR"), ("CIN", "CLE"),
        ("DEN", "LAC"), ("IND", "PIT"), ("JAX", "HOU"), ("MIA", "NE"),
        ("MIN", "GB"), ("NYG", "SEA"), ("NYJ", "KC"), ("PHI", "TEN"),
        ("TB", "DET"), ("WAS", "SF"),
    ],
    10: [
        ("ATL", "CLE"), ("BAL", "CAR"), ("BUF", "MIA"), ("CHI", "GB"),
        ("DEN", "NE"), ("HOU", "PIT"), ("IND", "MIN"), ("JAX", "DAL"),
        ("LAC", "TB"), ("LV", "KC"), ("NO", "CIN"), ("NYG", "PHI"),
        ("SF", "LAR"), ("WAS", "TEN"),
    ],
    11: [
        ("ARI", "SEA"), ("ATL", "WAS"), ("BAL", "CLE"), ("CHI", "BUF"),
        ("HOU", "LAC"), ("KC", "CIN"), ("LV", "NYJ"), ("MIA", "GB"),
        ("NE", "JAX"), ("NYG", "LAR"), ("PIT", "PHI"), ("SF", "DAL"),
        ("TB", "CAR"), ("TEN", "DET"),
    ],
    12: [
        ("CHI", "DET"), ("CLE", "BAL"), ("DEN", "CAR"), ("GB", "LAR"),
        ("HOU", "PHI"), ("KC", "LV"), ("LAC", "MIA"), ("NYG", "IND"),
        ("NYJ", "NE"), ("PIT", "CIN"), ("SEA", "ARI"), ("SF", "ATL"),
        ("TB", "NO"), ("TEN", "DAL"), ("WAS", "MIN"),
    ],
    13: [
        ("BAL", "CIN"), ("BUF", "HOU"), ("CLE", "JAX"), ("DEN", "ARI"),
        ("DET", "CAR"), ("IND", "TEN"), ("KC", "LAR"), ("LV", "NE"),
        ("MIN", "CHI"), ("NO", "ATL"), ("NYG", "DAL"), ("NYJ", "MIA"),
        ("PIT", "TB"), ("SEA", "PHI"), ("SF", "LAC"),
    ],
    14: [
        ("ARI", "DAL"), ("BUF", "NYJ"), ("CAR", "NO"), ("CIN", "BAL"),
        ("CLE", "PIT"), ("DET", "MIN"), ("HOU", "GB"), ("IND", "WAS"),
        ("JAX", "TEN"), ("KC", "MIA"), ("LAC", "LAR"), ("LV", "DEN"),
        ("NE", "SEA"), ("PHI", "CHI"), ("TB", "ATL"),
    ],
    15: [
        ("CAR", "MIN"), ("CIN", "HOU"), ("CLE", "TB"), ("DEN", "NYJ"),
        ("DET", "ATL"), ("GB", "CHI"), ("IND", "JAX"), ("KC", "BUF"),
        ("LAC", "BAL"), ("LAR", "ARI"), ("NE", "MIA"), ("NO", "NYG"),
        ("PHI", "SF"), ("PIT", "TEN"), ("SEA", "LV"), ("WAS", "DAL"),
    ],
    16: [
        ("BUF", "LAR"), ("CHI", "ATL"), ("CIN", "CAR"), ("CLE", "TEN"),
        ("DAL", "PHI"), ("DET", "MIA"), ("GB", "NE"), ("IND", "HOU"),
        ("KC", "DEN"), ("LAC", "SEA"), ("LV", "SF"), ("MIN", "TB"),
        ("NO", "BAL"), ("NYG", "WAS"), ("NYJ", "ARI"), ("PIT", "JAX"),
    ],
    17: [
        ("ARI", "LAR"), ("ATL", "GB"), ("BAL", "DAL"), ("BUF", "MIN"),
        ("CAR", "CLE"), ("CIN", "MIA"), ("DEN", "LV"), ("HOU", "TEN"),
        ("IND", "PHI"), ("JAX", "CHI"), ("KC", "LAC"), ("NO", "TB"),
        ("NYJ", "DET"), ("PIT", "NE"), ("SEA", "SF"), ("WAS", "NYG"),
    ],
    18: [
        ("ARI", "LAC"), ("BAL", "IND"), ("BUF", "LV"), ("CAR", "ATL"),
        ("CHI", "SEA"), ("DAL", "LAR"), ("DET", "GB"), ("HOU", "CLE"),
        ("JAX", "DEN"), ("MIA", "NYJ"), ("MIN", "SF"), ("NE", "KC"),
        ("PIT", "NO"), ("TB", "CIN"), ("TEN", "NYG"), ("WAS", "PHI"),
    ],
}


@dataclass(frozen=True)
class ScheduleValidation:
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class GameSpec:
    away: str
    home: str
    matchup_bucket: str
    notes: str


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS season_schedule_sources (
            source_id INTEGER PRIMARY KEY AUTOINCREMENT,
            season INTEGER NOT NULL,
            source_name TEXT NOT NULL,
            source_url TEXT,
            is_official INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(season, source_name)
        );

        CREATE TABLE IF NOT EXISTS season_weeks (
            season INTEGER NOT NULL,
            week INTEGER NOT NULL,
            week_type TEXT NOT NULL DEFAULT 'REG',
            week_start_date TEXT NOT NULL,
            primary_game_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'projected',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(season, week, week_type)
        );

        CREATE TABLE IF NOT EXISTS season_games (
            game_id INTEGER PRIMARY KEY AUTOINCREMENT,
            season INTEGER NOT NULL,
            week INTEGER,
            game_type TEXT NOT NULL DEFAULT 'REG',
            week_game_number INTEGER,
            away_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            home_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            game_date TEXT,
            game_time_et TEXT,
            neutral_site INTEGER NOT NULL DEFAULT 0,
            site_label TEXT,
            schedule_status TEXT NOT NULL DEFAULT 'opponents_official_week_projected',
            source_id INTEGER REFERENCES season_schedule_sources(source_id) ON DELETE SET NULL,
            matchup_bucket TEXT,
            notes TEXT,
            played INTEGER NOT NULL DEFAULT 0,
            away_score INTEGER,
            home_score INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(game_type IN ('PRE', 'REG', 'POST')),
            CHECK(neutral_site IN (0, 1)),
            CHECK(played IN (0, 1)),
            UNIQUE(season, game_type, away_team_id, home_team_id)
        );

        CREATE INDEX IF NOT EXISTS idx_season_games_week
            ON season_games(season, game_type, week, game_date);

        CREATE INDEX IF NOT EXISTS idx_season_games_away
            ON season_games(season, away_team_id);

        CREATE INDEX IF NOT EXISTS idx_season_games_home
            ON season_games(season, home_team_id);

        CREATE TABLE IF NOT EXISTS season_team_byes (
            season INTEGER NOT NULL,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            week INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'projected',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(season, team_id)
        );

        DROP VIEW IF EXISTS weekly_schedule_view;
        CREATE VIEW weekly_schedule_view AS
        SELECT
            sg.game_id,
            sg.season,
            sg.week,
            sg.game_type,
            sg.week_game_number,
            sg.game_date,
            sg.game_time_et,
            away.abbreviation AS away_team,
            away.city AS away_city,
            away.nickname AS away_nickname,
            home.abbreviation AS home_team,
            home.city AS home_city,
            home.nickname AS home_nickname,
            sg.matchup_bucket,
            sg.schedule_status,
            sg.played,
            sg.away_score,
            sg.home_score,
            sg.notes
        FROM season_games sg
        JOIN teams away ON away.team_id = sg.away_team_id
        JOIN teams home ON home.team_id = sg.home_team_id;

        DROP VIEW IF EXISTS team_schedule_view;
        CREATE VIEW team_schedule_view AS
        SELECT
            sg.game_id,
            sg.season,
            sg.week,
            sg.game_type,
            sg.game_date,
            sg.game_time_et,
            team.team_id,
            team.abbreviation AS team,
            opponent.abbreviation AS opponent,
            opponent.city AS opponent_city,
            opponent.nickname AS opponent_nickname,
            CASE
                WHEN sg.home_team_id = team.team_id THEN 'HOME'
                ELSE 'AWAY'
            END AS site,
            sg.matchup_bucket,
            sg.schedule_status,
            sg.played,
            CASE
                WHEN sg.home_team_id = team.team_id THEN sg.home_score
                ELSE sg.away_score
            END AS team_score,
            CASE
                WHEN sg.home_team_id = team.team_id THEN sg.away_score
                ELSE sg.home_score
            END AS opponent_score
        FROM season_games sg
        JOIN teams team ON team.team_id IN (sg.away_team_id, sg.home_team_id)
        JOIN teams opponent ON opponent.team_id =
            CASE
                WHEN sg.home_team_id = team.team_id THEN sg.away_team_id
                ELSE sg.home_team_id
            END;
        """
    )


def upsert_source(con: sqlite3.Connection, season: int) -> int:
    con.execute(
        """
        INSERT INTO season_schedule_sources (
            season, source_name, source_url, is_official, notes
        )
        VALUES (?, ?, ?, 1, ?)
        ON CONFLICT(season, source_name) DO UPDATE SET
            source_url = excluded.source_url,
            is_official = excluded.is_official,
            notes = excluded.notes
        """,
        (
            season,
            "NFL.com team-by-team opponents",
            NFL_2026_OPPONENTS_URL,
            "Official home/away opponent matrix. Week placement is generated.",
        ),
    )
    row = con.execute(
        """
        SELECT source_id
        FROM season_schedule_sources
        WHERE season = ? AND source_name = ?
        """,
        (season, "NFL.com team-by-team opponents"),
    ).fetchone()
    if not row:
        raise RuntimeError("Could not create schedule source row.")
    return int(row["source_id"])


def upsert_official_schedule_source(con: sqlite3.Connection, season: int, source_url: str) -> int:
    con.execute(
        """
        INSERT INTO season_schedule_sources (
            season, source_name, source_url, is_official, notes
        )
        VALUES (?, ?, ?, 1, ?)
        ON CONFLICT(season, source_name) DO UPDATE SET
            source_url = excluded.source_url,
            is_official = excluded.is_official,
            notes = excluded.notes
        """,
        (
            season,
            "NFL.com official regular-season schedule",
            source_url,
            "Official weekly schedule, dates, sites, and announced kickoff windows from NFL.com.",
        ),
    )
    row = con.execute(
        """
        SELECT source_id
        FROM season_schedule_sources
        WHERE season = ? AND source_name = ?
        """,
        (season, "NFL.com official regular-season schedule"),
    ).fetchone()
    if not row:
        raise RuntimeError("Could not create official schedule source row.")
    return int(row["source_id"])


def upsert_formula_source(con: sqlite3.Connection, season: int) -> int:
    con.execute(
        """
        INSERT INTO season_schedule_sources (
            season, source_name, source_url, is_official, notes
        )
        VALUES (?, ?, ?, 0, ?)
        ON CONFLICT(season, source_name) DO UPDATE SET
            source_url = excluded.source_url,
            is_official = excluded.is_official,
            notes = excluded.notes
        """,
        (
            season,
            "Projected NFL scheduling formula",
            NFL_OPS_SCHEDULE_FORMULA_URL,
            "Generated from the NFL opponent formula. Week placement, byes, and kickoff times are projected.",
        ),
    )
    row = con.execute(
        """
        SELECT source_id
        FROM season_schedule_sources
        WHERE season = ? AND source_name = ?
        """,
        (season, "Projected NFL scheduling formula"),
    ).fetchone()
    if not row:
        raise RuntimeError("Could not create projected schedule source row.")
    return int(row["source_id"])


def season_has_played_games(con: sqlite3.Connection, season: int) -> bool:
    row = con.execute(
        """
        SELECT COUNT(*) AS count
        FROM season_games
        WHERE season = ? AND game_type = 'REG' AND played = 1
        """,
        (season,),
    ).fetchone()
    return bool(row and int(row["count"] or 0))


def clear_regular_schedule(
    con: sqlite3.Connection,
    season: int,
    *,
    replace_played: bool = False,
) -> None:
    if season_has_played_games(con, season) and not replace_played:
        raise ValueError(
            f"{season} has played games. Refusing to replace the schedule without --replace-played."
        )
    con.execute("DELETE FROM season_team_byes WHERE season = ?", (season,))
    con.execute(
        "DELETE FROM season_games WHERE season = ? AND game_type = 'REG'",
        (season,),
    )
    con.execute(
        "DELETE FROM season_weeks WHERE season = ? AND week_type = 'REG'",
        (season,),
    )


def team_ids(con: sqlite3.Connection) -> dict[str, int]:
    rows = con.execute("SELECT team_id, abbreviation FROM teams").fetchall()
    teams = {row["abbreviation"]: int(row["team_id"]) for row in rows}
    missing = sorted(set(TEAM_DIVISION) - set(teams))
    if missing:
        raise ValueError(f"Missing teams in database: {', '.join(missing)}")
    return teams


def regular_week_dates(season: int) -> dict[int, tuple[str, str]]:
    kickoff = league_calendar.regular_season_kickoff(season)
    week1_monday = kickoff - timedelta(days=kickoff.weekday())
    dates = {}
    for week in range(1, 19):
        start = week1_monday + timedelta(weeks=week - 1)
        primary = start + timedelta(days=6)
        dates[week] = (start.isoformat(), primary.isoformat())
    return dates


def home_conference_for_17th_game(season: int) -> str:
    # Since the 17-game schedule began, the extra home game has alternated
    # conferences. 2026 is an NFC home year, so odd years project as AFC home.
    return "AFC" if season % 2 else "NFC"


def full_intraconference_division_pairs(season: int, conference: str) -> list[tuple[str, str]]:
    divisions = CONFERENCE_DIVISIONS[conference]
    rotation = INTRACONFERENCE_FULL_ROTATION[(season - DEFAULT_SEASON) % 3]
    return [(divisions[left], divisions[right]) for left, right in rotation]


def full_interconference_division_pairs(season: int) -> list[tuple[str, str]]:
    shift = (season - DEFAULT_SEASON) % 4
    afc_divisions = CONFERENCE_DIVISIONS["AFC"]
    nfc_divisions = CONFERENCE_DIVISIONS["NFC"]
    return [
        (afc_divisions[index], nfc_divisions[(nfc_index + shift) % 4])
        for index, nfc_index in enumerate(INTERCONFERENCE_2026_NFC_INDEX)
    ]


def seventeenth_game_division_pairs(season: int) -> list[tuple[str, str]]:
    shift = (season - DEFAULT_SEASON) % 4
    afc_divisions = CONFERENCE_DIVISIONS["AFC"]
    nfc_divisions = CONFERENCE_DIVISIONS["NFC"]
    return [
        (afc_divisions[index], nfc_divisions[(nfc_index + shift) % 4])
        for index, nfc_index in enumerate(SEVENTEENTH_2026_NFC_INDEX)
    ]


def same_place_intraconference_orientations(season: int, conference: str) -> list[tuple[str, str]]:
    """Return (home_division, away_division) same-place pair orientations.

    The remaining same-conference division graph is a four-edge cycle after the
    full-division rotation is removed. Orienting that cycle gives every division
    one same-place home game and one same-place road game.
    """
    divisions = CONFERENCE_DIVISIONS[conference]
    full_pairs = {
        frozenset(pair)
        for pair in full_intraconference_division_pairs(season, conference)
    }
    remaining_edges = [
        (left, right)
        for left in range(4)
        for right in range(left + 1, 4)
        if frozenset((divisions[left], divisions[right])) not in full_pairs
    ]
    adjacency = {index: [] for index in range(4)}
    for left, right in remaining_edges:
        adjacency[left].append(right)
        adjacency[right].append(left)

    cycle = [0]
    previous = None
    current = 0
    while len(cycle) < 4:
        candidates = [
            node
            for node in adjacency[current]
            if node != previous and node not in cycle
        ]
        if not candidates:
            candidates = [node for node in adjacency[current] if node != previous]
        if not candidates:
            raise RuntimeError(f"Could not orient same-place rotation for {conference} {season}.")
        next_node = candidates[0]
        cycle.append(next_node)
        previous, current = current, next_node

    flip = (season + (0 if conference == "AFC" else 1)) % 2 == 1
    oriented = []
    for index, home_index in enumerate(cycle):
        away_index = cycle[(index + 1) % len(cycle)]
        if flip:
            home_index, away_index = away_index, home_index
        oriented.append((divisions[home_index], divisions[away_index]))
    return oriented


def matchup_bucket(away: str, home: str) -> str:
    if TEAM_DIVISION[away] == TEAM_DIVISION[home]:
        return "DIVISION"
    if TEAM_CONFERENCE[away] == TEAM_CONFERENCE[home]:
        return "INTRACONFERENCE"
    return "INTERCONFERENCE"


def fallback_division_rankings(con: sqlite3.Connection, season: int) -> dict[str, list[str]]:
    rankings = {}
    rows = con.execute(
        """
        SELECT abbreviation, division, prestige, team_id
        FROM teams
        ORDER BY division, prestige DESC, abbreviation
        """
    ).fetchall()
    for division in DIVISIONS:
        division_rows = [row for row in rows if row["division"] == division]
        # Rotate exact ties a little by season so projected future schedules do
        # not get the same same-place matrix forever before real standings exist.
        division_rows.sort(
            key=lambda row: (
                -(row["prestige"] if row["prestige"] is not None else 50),
                (int(row["team_id"]) + season) % 4,
                row["abbreviation"],
            )
        )
        rankings[division] = [row["abbreviation"] for row in division_rows]
    return rankings


def division_rankings(con: sqlite3.Connection, completed_season: int) -> dict[str, list[str]]:
    rows = con.execute(
        """
        SELECT
            t.abbreviation,
            t.division,
            str.wins,
            str.losses,
            str.ties,
            str.points_for,
            str.points_against
        FROM season_team_records str
        JOIN teams t ON t.team_id = str.team_id
        WHERE str.season = ?
        """,
        (completed_season,),
    ).fetchall()
    has_real_records = len(rows) == 32 and any(
        int(row["wins"] or 0) + int(row["losses"] or 0) + int(row["ties"] or 0) > 0
        for row in rows
    )
    if not has_real_records:
        return fallback_division_rankings(con, completed_season)

    rankings: dict[str, list[str]] = {}
    for division in DIVISIONS:
        division_rows = [row for row in rows if row["division"] == division]
        division_rows.sort(
            key=lambda row: (
                -(int(row["wins"] or 0) + 0.5 * int(row["ties"] or 0)),
                -int(row["wins"] or 0),
                -(int(row["points_for"] or 0) - int(row["points_against"] or 0)),
                -int(row["points_for"] or 0),
                row["abbreviation"],
            )
        )
        rankings[division] = [row["abbreviation"] for row in division_rows]
    return rankings


def add_full_rotation_games(
    games: list[GameSpec],
    *,
    season: int,
    left_division: str,
    right_division: str,
    bucket: str,
    notes: str,
) -> None:
    season_offset = season - DEFAULT_SEASON
    for left_index, left_team in enumerate(DIVISIONS[left_division]):
        for right_index, right_team in enumerate(DIVISIONS[right_division]):
            if (left_index + right_index + season_offset) % 2 == 0:
                away, home = right_team, left_team
            else:
                away, home = left_team, right_team
            games.append(GameSpec(away, home, bucket, notes))


def generate_formula_opponents(con: sqlite3.Connection, season: int) -> list[GameSpec]:
    rankings = division_rankings(con, season - 1)
    games: list[GameSpec] = []

    for division, teams in DIVISIONS.items():
        for index, home_first_team in enumerate(teams):
            for away_first_team in teams[index + 1 :]:
                games.append(
                    GameSpec(
                        away_first_team,
                        home_first_team,
                        "DIVISION",
                        "Division home-and-away game.",
                    )
                )
                games.append(
                    GameSpec(
                        home_first_team,
                        away_first_team,
                        "DIVISION",
                        "Division home-and-away game.",
                    )
                )

    for conference in ("AFC", "NFC"):
        for left_division, right_division in full_intraconference_division_pairs(season, conference):
            add_full_rotation_games(
                games,
                season=season,
                left_division=left_division,
                right_division=right_division,
                bucket="INTRACONFERENCE",
                notes="Rotating full-division intraconference opponent.",
            )

    for afc_division, nfc_division in full_interconference_division_pairs(season):
        add_full_rotation_games(
            games,
            season=season,
            left_division=afc_division,
            right_division=nfc_division,
            bucket="INTERCONFERENCE",
            notes="Rotating full-division interconference opponent.",
        )

    for conference in ("AFC", "NFC"):
        for home_division, away_division in same_place_intraconference_orientations(season, conference):
            for rank_index in range(4):
                home = rankings[home_division][rank_index]
                away = rankings[away_division][rank_index]
                games.append(
                    GameSpec(
                        away,
                        home,
                        "INTRACONFERENCE",
                        "Same-place intraconference opponent from prior-year standings.",
                    )
                )

    home_conference = home_conference_for_17th_game(season)
    for afc_division, nfc_division in seventeenth_game_division_pairs(season):
        for rank_index in range(4):
            afc_team = rankings[afc_division][rank_index]
            nfc_team = rankings[nfc_division][rank_index]
            if home_conference == "AFC":
                away, home = nfc_team, afc_team
            else:
                away, home = afc_team, nfc_team
            games.append(
                GameSpec(
                    away,
                    home,
                    "INTERCONFERENCE_17TH",
                    "17th interconference game matched by prior-year division finish.",
                )
            )

    return games


def projected_byes(season: int) -> dict[str, int]:
    teams = sorted(TEAM_DIVISION)
    rng = random.Random(season * 17)
    rng.shuffle(teams)
    byes = {}
    cursor = 0
    for week in range(5, 15):
        for _ in range(BYE_COUNTS[week]):
            byes[teams[cursor]] = week
            cursor += 1
    if set(byes) != set(TEAM_DIVISION):
        raise RuntimeError(f"Projected bye assignment for {season} did not cover all teams.")
    return byes


def week_game_score(game: GameSpec, week: int) -> float:
    if game.matchup_bucket == "DIVISION":
        if week >= 17:
            return 100
        if week >= 15:
            return 45
        if week <= 4:
            return 8
        return -2
    return 12 if week <= 14 else -12


def division_game(game: GameSpec) -> bool:
    return TEAM_DIVISION[game.away] == TEAM_DIVISION[game.home]


def matchup_pair(game: GameSpec | tuple[str, str]) -> tuple[str, str]:
    if isinstance(game, GameSpec):
        return tuple(sorted((game.away, game.home)))
    return tuple(sorted((game[0], game[1])))


def consecutive_division_rematches_by_week(
    week_schedule: dict[int, list[GameSpec | tuple[str, str]]],
) -> list[tuple[int, int, tuple[str, str]]]:
    violations: list[tuple[int, int, tuple[str, str]]] = []
    for week in range(1, 18):
        current_pairs = {
            matchup_pair(game)
            for game in week_schedule.get(week, [])
            if TEAM_DIVISION[game.away if isinstance(game, GameSpec) else game[0]]
            == TEAM_DIVISION[game.home if isinstance(game, GameSpec) else game[1]]
        }
        next_pairs = {
            matchup_pair(game)
            for game in week_schedule.get(week + 1, [])
            if TEAM_DIVISION[game.away if isinstance(game, GameSpec) else game[0]]
            == TEAM_DIVISION[game.home if isinstance(game, GameSpec) else game[1]]
        }
        for pair in sorted(current_pairs & next_pairs):
            violations.append((week, week + 1, pair))
    return violations


def conflicts_with_adjacent_division_rematch(
    game: GameSpec,
    *,
    week: int,
    scheduled: dict[int, list[GameSpec]],
) -> bool:
    if not division_game(game):
        return False
    pair = matchup_pair(game)
    for adjacent_week in (week - 1, week + 1):
        for adjacent_game in scheduled.get(adjacent_week, []):
            if division_game(adjacent_game) and matchup_pair(adjacent_game) == pair:
                return True
    return False


def find_week_matching(
    games: list[GameSpec],
    *,
    available_teams: list[str],
    target_games: int,
    week: int,
    rng: random.Random,
    scheduled: dict[int, list[GameSpec]] | None = None,
) -> list[GameSpec] | None:
    available = set(available_teams)
    scheduled = scheduled or {}
    games_by_team = {team: [] for team in available}
    for game in games:
        if game.away in available and game.home in available:
            games_by_team[game.away].append(game)
            games_by_team[game.home].append(game)

    chosen: list[GameSpec] = []
    used: set[str] = set()

    def valid_candidates(team: str) -> list[GameSpec]:
        return [
            game
            for game in games_by_team[team]
            if game.away not in used and game.home not in used
            and not conflicts_with_adjacent_division_rematch(game, week=week, scheduled=scheduled)
        ]

    def search() -> bool:
        if len(chosen) == target_games:
            return True
        remaining_teams = [team for team in available if team not in used]
        if not remaining_teams:
            return len(chosen) == target_games

        candidate_counts = [(len(valid_candidates(team)), team) for team in remaining_teams]
        count, team = min(candidate_counts)
        if count == 0:
            return False

        candidates = valid_candidates(team)
        rng.shuffle(candidates)
        candidates.sort(
            key=lambda game: week_game_score(game, week) + rng.random() * 3,
            reverse=True,
        )
        for game in candidates:
            used.add(game.away)
            used.add(game.home)
            chosen.append(game)
            if search():
                return True
            chosen.pop()
            used.remove(game.away)
            used.remove(game.home)
        return False

    if not search():
        return None
    return chosen[:]


def generate_projected_week_schedule(
    games: list[GameSpec],
    *,
    season: int,
    max_attempts: int = 5000,
) -> tuple[dict[int, list[GameSpec]], dict[str, int]]:
    byes = projected_byes(season)
    schedule_order = [17, 18, 15, 16, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
    all_teams = sorted(TEAM_DIVISION)

    for attempt in range(max_attempts):
        rng = random.Random(season * 10000 + attempt)
        remaining = games[:]
        scheduled: dict[int, list[GameSpec]] = {}
        failed = False

        for week in schedule_order:
            available = [team for team in all_teams if byes.get(team) != week]
            matching = find_week_matching(
                remaining,
                available_teams=available,
                target_games=WEEK_GAME_TARGETS[week],
                week=week,
                rng=rng,
                scheduled=scheduled,
            )
            if matching is None:
                failed = True
                break
            scheduled[week] = sorted(
                matching,
                key=lambda game: (MATCHUP_SORT.get(game.matchup_bucket, 9), game.away, game.home),
            )
            chosen = set(matching)
            remaining = [game for game in remaining if game not in chosen]

        if not failed and not remaining and not consecutive_division_rematches_by_week(scheduled):
            return scheduled, byes

    raise RuntimeError(f"Could not generate a complete projected week schedule for {season}.")


def validate_seed_data() -> ScheduleValidation:
    errors = []
    warnings = []
    all_teams = set(TEAM_DIVISION)

    if set(OFFICIAL_2026_HOME_OPPONENTS) != all_teams:
        errors.append("Official opponent map does not cover all 32 teams.")

    official_games = set()
    for home, opponents in OFFICIAL_2026_HOME_OPPONENTS.items():
        if home not in all_teams:
            errors.append(f"Unknown home team in official map: {home}")
        for away in opponents:
            if away not in all_teams:
                errors.append(f"Unknown away opponent in official map: {away}")
            if away == home:
                errors.append(f"{home} is listed as its own opponent.")
            official_games.add((away, home))

    if len(official_games) != 272:
        errors.append(f"Official game count should be 272, found {len(official_games)}.")

    scheduled_games = set()
    team_week_counts: dict[tuple[str, int], int] = {}
    week_counts: dict[int, int] = {}
    for week, games in PROVISIONAL_2026_WEEKS.items():
        week_counts[week] = len(games)
        used_this_week = set()
        for away, home in games:
            scheduled_games.add((away, home))
            for team in (away, home):
                team_week_counts[(team, week)] = team_week_counts.get((team, week), 0) + 1
                if team in used_this_week:
                    errors.append(f"{team} appears twice in week {week}.")
                used_this_week.add(team)

    if set(PROVISIONAL_2026_WEEKS) != set(range(1, 19)):
        errors.append("Provisional schedule must include weeks 1 through 18.")

    consecutive_rematches = consecutive_division_rematches_by_week(PROVISIONAL_2026_WEEKS)
    if consecutive_rematches:
        details = ", ".join(
            f"Weeks {left}-{right} {'/'.join(pair)}"
            for left, right, pair in consecutive_rematches
        )
        warnings.append(f"2026 provisional schedule has back-to-back divisional rematches: {details}.")

    if scheduled_games != official_games:
        missing = sorted(official_games - scheduled_games)
        extra = sorted(scheduled_games - official_games)
        if missing:
            errors.append(f"Provisional schedule is missing {len(missing)} official games.")
        if extra:
            errors.append(f"Provisional schedule has {len(extra)} games outside official matrix.")

    if len(scheduled_games) != 272:
        errors.append(f"Provisional game count should be 272, found {len(scheduled_games)}.")

    full_weeks = set([1, 2, 3, 4, 15, 16, 17, 18])
    for week in full_weeks:
        if week_counts.get(week) != 16:
            errors.append(f"Week {week} should have 16 games, found {week_counts.get(week, 0)}.")
    for week in range(5, 15):
        if week_counts.get(week, 0) not in (14, 15):
            warnings.append(f"Week {week} has {week_counts.get(week, 0)} games.")

    for team in all_teams:
        total = sum(1 for away, home in scheduled_games if team in (away, home))
        home = sum(1 for away, home in scheduled_games if home == team)
        away = sum(1 for away, home in scheduled_games if away == team)
        if total != 17:
            errors.append(f"{team} should have 17 games, found {total}.")
        if home not in (8, 9) or away not in (8, 9):
            errors.append(f"{team} has invalid home/away split: {home} home, {away} away.")
        bye_weeks = [
            week
            for week in range(5, 15)
            if team_week_counts.get((team, week), 0) == 0
        ]
        if len(bye_weeks) != 1:
            errors.append(f"{team} should have one bye from Weeks 5-14, found {bye_weeks}.")

    return ScheduleValidation(errors=errors, warnings=warnings)


def insert_season_weeks(con: sqlite3.Connection, season: int, *, notes: str) -> None:
    dates = regular_week_dates(season)
    for week in range(1, 19):
        week_start, primary_game_date = dates[week]
        con.execute(
            """
            INSERT INTO season_weeks (
                season, week, week_type, week_start_date, primary_game_date, status, notes
            )
            VALUES (?, ?, 'REG', ?, ?, 'projected', ?)
            """,
            (season, week, week_start, primary_game_date, notes),
        )


def load_official_2026_schedule() -> dict:
    if not OFFICIAL_2026_SCHEDULE_PATH.exists():
        raise FileNotFoundError(
            f"Official 2026 schedule data not found: {OFFICIAL_2026_SCHEDULE_PATH}"
        )
    payload = json.loads(OFFICIAL_2026_SCHEDULE_PATH.read_text(encoding="utf-8"))
    if int(payload.get("season") or 0) != DEFAULT_SEASON:
        raise ValueError(f"Unexpected official schedule season: {payload.get('season')}")
    games = payload.get("games") or []
    if len(games) != 272:
        raise ValueError(f"Official schedule should contain 272 games, found {len(games)}.")
    return payload


def official_game_note(game: dict) -> str:
    parts = ["Official NFL.com schedule."]
    if game.get("time_tbd"):
        parts.append("Kickoff time TBD.")
    category = game.get("category")
    if category:
        parts.append(str(category))
    networks = game.get("networks") or []
    if networks:
        parts.append("TV: " + "/".join(str(network) for network in networks))
    slug = game.get("slug")
    if slug:
        parts.append(f"Slug: {slug}")
    return " ".join(parts)


def insert_official_2026_schedule(con: sqlite3.Connection, source_id: int, payload: dict) -> None:
    ids = team_ids(con)
    games = sorted(
        payload["games"],
        key=lambda item: (
            int(item["week"]),
            str(item.get("game_date") or "9999-12-31"),
            str(item.get("game_time_et") or "99:99"),
            int(item["week_game_number"]),
        ),
    )
    dates_by_week: dict[int, list[str]] = {}
    teams_by_week: dict[int, set[str]] = {}
    for week in range(1, 19):
        dates_by_week[week] = []
        teams_by_week[week] = set()
    for game in games:
        week = int(game["week"])
        away = str(game["away"])
        home = str(game["home"])
        dates_by_week[week].append(str(game["game_date"]))
        teams_by_week[week].update((away, home))

    for week in range(1, 19):
        week_dates = sorted(dates_by_week[week])
        con.execute(
            """
            INSERT INTO season_weeks (
                season, week, week_type, week_start_date, primary_game_date, status, notes
            )
            VALUES (?, ?, 'REG', ?, ?, 'official', ?)
            """,
            (
                DEFAULT_SEASON,
                week,
                week_dates[0],
                week_dates[-1],
                "Official NFL.com weekly schedule.",
            ),
        )

    numbers_by_week: dict[int, int] = {week: 0 for week in range(1, 19)}
    for game in games:
        week = int(game["week"])
        away = str(game["away"])
        home = str(game["home"])
        numbers_by_week[week] += 1
        con.execute(
            """
            INSERT INTO season_games (
                season, week, game_type, week_game_number,
                away_team_id, home_team_id, game_date, game_time_et,
                neutral_site, site_label, schedule_status, source_id, matchup_bucket, notes
            )
            VALUES (?, ?, 'REG', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                DEFAULT_SEASON,
                week,
                numbers_by_week[week],
                ids[away],
                ids[home],
                game.get("game_date"),
                game.get("game_time_et"),
                1 if game.get("neutral_site") else 0,
                game.get("site_label"),
                "official",
                source_id,
                matchup_bucket(away, home),
                official_game_note(game),
            ),
        )

    all_teams = set(TEAM_DIVISION)
    for week in range(1, 19):
        for team in sorted(all_teams - teams_by_week[week]):
            con.execute(
                """
                INSERT INTO season_team_byes (season, team_id, week, status, notes)
                VALUES (?, ?, ?, 'official', ?)
                """,
                (
                    DEFAULT_SEASON,
                    ids[team],
                    week,
                    "Official bye week from NFL.com schedule.",
                ),
            )


def seed_2026_schedule(
    con: sqlite3.Connection,
    *,
    replace: bool = True,
    replace_played: bool = False,
) -> None:
    ensure_schema(con)
    existing = con.execute(
        "SELECT COUNT(*) AS count FROM season_games WHERE season = ? AND game_type = 'REG'",
        (DEFAULT_SEASON,),
    ).fetchone()
    if int(existing["count"] or 0) and not replace:
        raise ValueError("2026 regular-season schedule already exists. Use --replace to rebuild it.")

    clear_regular_schedule(con, DEFAULT_SEASON, replace_played=replace_played)
    if OFFICIAL_2026_SCHEDULE_PATH.exists():
        payload = load_official_2026_schedule()
        source_id = upsert_official_schedule_source(
            con,
            DEFAULT_SEASON,
            str(payload.get("source_url_template") or NFL_2026_SCHEDULE_URL),
        )
        insert_official_2026_schedule(con, source_id, payload)
        con.commit()
        return

    validation = validate_seed_data()
    if not validation.ok:
        raise ValueError("Seed data failed validation:\n" + "\n".join(validation.errors))

    source_id = upsert_source(con, DEFAULT_SEASON)
    ids = team_ids(con)
    dates = regular_week_dates(DEFAULT_SEASON)

    insert_season_weeks(
        con,
        DEFAULT_SEASON,
        notes="Generated provisional week placement; replace once official dates are released.",
    )

    for week, games in PROVISIONAL_2026_WEEKS.items():
        _week_start, primary_game_date = dates[week]
        for number, (away, home) in enumerate(games, start=1):
            con.execute(
                """
                INSERT INTO season_games (
                    season, week, game_type, week_game_number,
                    away_team_id, home_team_id, game_date, game_time_et,
                    schedule_status, source_id, matchup_bucket, notes
                )
                VALUES (?, ?, 'REG', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    DEFAULT_SEASON,
                    week,
                    number,
                    ids[away],
                    ids[home],
                    primary_game_date,
                    "1:00 PM",
                    "opponents_official_week_projected",
                    source_id,
                    matchup_bucket(away, home),
                    "Official opponent; generated week/date/time placeholder.",
                ),
            )

    all_teams = set(TEAM_DIVISION)
    for week in range(5, 15):
        playing = {
            team
            for away, home in PROVISIONAL_2026_WEEKS[week]
            for team in (away, home)
        }
        for team in sorted(all_teams - playing):
            con.execute(
                """
                INSERT INTO season_team_byes (season, team_id, week, status, notes)
                VALUES (?, ?, ?, 'projected', ?)
                """,
                (
                    DEFAULT_SEASON,
                    ids[team],
                    week,
                    "Generated bye week; replace once official schedule is released.",
                ),
            )

    con.commit()


def seed_formula_schedule(
    con: sqlite3.Connection,
    season: int,
    *,
    replace: bool = True,
    replace_played: bool = False,
) -> None:
    if season == DEFAULT_SEASON:
        seed_2026_schedule(con, replace=replace, replace_played=replace_played)
        return
    ensure_schema(con)
    existing = con.execute(
        "SELECT COUNT(*) AS count FROM season_games WHERE season = ? AND game_type = 'REG'",
        (season,),
    ).fetchone()
    if int(existing["count"] or 0) and not replace:
        raise ValueError(f"{season} regular-season schedule already exists. Use --replace to rebuild it.")

    ids = team_ids(con)
    source_id = upsert_formula_source(con, season)
    games = generate_formula_opponents(con, season)
    if len(games) != 272:
        raise ValueError(f"Projected schedule should have 272 games, found {len(games)}.")
    week_schedule, byes = generate_projected_week_schedule(games, season=season)
    dates = regular_week_dates(season)

    clear_regular_schedule(con, season, replace_played=replace_played)
    insert_season_weeks(
        con,
        season,
        notes="Projected NFL formula schedule. Replace exact dates/times when the official schedule is released.",
    )

    for week in range(1, 19):
        _week_start, primary_game_date = dates[week]
        for number, game in enumerate(week_schedule[week], start=1):
            con.execute(
                """
                INSERT INTO season_games (
                    season, week, game_type, week_game_number,
                    away_team_id, home_team_id, game_date, game_time_et,
                    schedule_status, source_id, matchup_bucket, notes
                )
                VALUES (?, ?, 'REG', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    season,
                    week,
                    number,
                    ids[game.away],
                    ids[game.home],
                    primary_game_date,
                    "1:00 PM",
                    "formula_projected_week_projected",
                    source_id,
                    game.matchup_bucket,
                    game.notes,
                ),
            )

    for team, week in sorted(byes.items()):
        con.execute(
            """
            INSERT INTO season_team_byes (season, team_id, week, status, notes)
            VALUES (?, ?, ?, 'projected', ?)
            """,
            (
                season,
                ids[team],
                week,
                "Projected bye week for generated future schedule.",
            ),
        )

    con.commit()


def validate_database(con: sqlite3.Connection, season: int) -> ScheduleValidation:
    ensure_schema(con)
    errors = []
    warnings = []
    rows = con.execute(
        """
        SELECT away_team, home_team, week
        FROM weekly_schedule_view
        WHERE season = ? AND game_type = 'REG'
        """,
        (season,),
    ).fetchall()
    games = [(row["away_team"], row["home_team"], int(row["week"])) for row in rows]
    if len(games) != 272:
        errors.append(f"Database should have 272 regular-season games, found {len(games)}.")

    by_team = {team: {"total": 0, "home": 0, "away": 0} for team in TEAM_DIVISION}
    by_week_team: dict[tuple[int, str], int] = {}
    by_week_count: dict[int, int] = {}
    db_games = set()
    for away, home, week in games:
        db_games.add((away, home))
        by_week_count[week] = by_week_count.get(week, 0) + 1
        for team in (away, home):
            by_week_team[(week, team)] = by_week_team.get((week, team), 0) + 1
            by_team[team]["total"] += 1
        by_team[away]["away"] += 1
        by_team[home]["home"] += 1

    if season == DEFAULT_SEASON:
        official_games = {
            (away, home)
            for home, opponents in OFFICIAL_2026_HOME_OPPONENTS.items()
            for away in opponents
        }
    else:
        official_games = None
        warnings.append(
            f"{season} uses generated formula opponents and projected week placement; exact NFL dates are not official."
        )

    if official_games is not None and db_games != official_games:
        errors.append("Database schedule does not match the official 2026 opponent matrix.")

    expected_home_conference = home_conference_for_17th_game(season)
    for team, counts in sorted(by_team.items()):
        if counts["total"] != 17:
            errors.append(f"{team} has {counts['total']} games, expected 17.")
        if counts["home"] not in (8, 9) or counts["away"] not in (8, 9):
            errors.append(f"{team} has {counts['home']} home and {counts['away']} away games.")
        expected_home = 9 if TEAM_CONFERENCE[team] == expected_home_conference else 8
        if counts["home"] != expected_home:
            errors.append(
                f"{team} has {counts['home']} home games, expected {expected_home} for {season}."
            )
        duplicate_weeks = [
            week
            for week in range(1, 19)
            if by_week_team.get((week, team), 0) > 1
        ]
        if duplicate_weeks:
            errors.append(f"{team} plays more than once in weeks: {duplicate_weeks}.")

    for week, expected in WEEK_GAME_TARGETS.items():
        found = by_week_count.get(week, 0)
        if found != expected:
            errors.append(f"Week {week} has {found} games, expected {expected}.")

    scheduled_by_week: dict[int, list[tuple[str, str]]] = {}
    for away, home, week in games:
        scheduled_by_week.setdefault(week, []).append((away, home))
    consecutive_rematches = consecutive_division_rematches_by_week(scheduled_by_week)
    if consecutive_rematches:
        details = ", ".join(
            f"Weeks {left}-{right} {'/'.join(pair)}"
            for left, right, pair in consecutive_rematches
        )
        message = f"Back-to-back divisional rematches found: {details}."
        if season == DEFAULT_SEASON:
            warnings.append(message)
        else:
            errors.append(message)

    bye_rows = con.execute(
        """
        SELECT t.abbreviation AS team, stb.week
        FROM season_team_byes stb
        JOIN teams t ON t.team_id = stb.team_id
        WHERE stb.season = ?
        """,
        (season,),
    ).fetchall()
    byes = {row["team"]: int(row["week"]) for row in bye_rows}
    if set(byes) != set(TEAM_DIVISION):
        errors.append("Bye table does not cover all 32 teams.")
    for team, week in sorted(byes.items()):
        if not 5 <= week <= 14:
            errors.append(f"{team} has a bye outside Weeks 5-14: Week {week}.")
        if by_week_team.get((week, team), 0) != 0:
            errors.append(f"{team} is listed on bye but also plays in Week {week}.")

    return ScheduleValidation(errors=errors, warnings=warnings)


def print_validation(result: ScheduleValidation) -> None:
    if result.ok:
        print("Schedule validation passed.")
    else:
        print("Schedule validation failed:")
        for error in result.errors:
            print(f"  ERROR: {error}")
    for warning in result.warnings:
        print(f"  WARN: {warning}")


def action_setup(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        ensure_schema(con)
        con.commit()
        print("Schedule schema ready.")
    finally:
        con.close()


def action_seed_2026(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        seed_2026_schedule(con, replace=args.replace, replace_played=args.replace_played)
        print("Seeded 2026 regular-season schedule.")
        result = validate_database(con, DEFAULT_SEASON)
        print_validation(result)
    finally:
        con.close()


def action_seed_formula(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        seed_formula_schedule(
            con,
            args.season,
            replace=args.replace,
            replace_played=args.replace_played,
        )
        print(f"Seeded {args.season} projected regular-season schedule.")
        result = validate_database(con, args.season)
        print_validation(result)
    finally:
        con.close()


def action_seed_range(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        seasons = list(range(args.start_season, args.start_season + args.years))
        for season in seasons:
            seed_formula_schedule(
                con,
                season,
                replace=args.replace,
                replace_played=args.replace_played,
            )
            print(f"Seeded {season}.")
        failed = False
        for season in seasons:
            result = validate_database(con, season)
            if not result.ok:
                failed = True
            print(f"{season}: {'ok' if result.ok else 'failed'}")
            for warning in result.warnings:
                print(f"  WARN: {warning}")
            for error in result.errors:
                print(f"  ERROR: {error}")
        if failed:
            raise ValueError("One or more generated schedules failed validation.")
    finally:
        con.close()


def action_validate(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        print_validation(validate_database(con, args.season))
    finally:
        con.close()


def action_show_week(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        ensure_schema(con)
        rows = con.execute(
            """
            SELECT *
            FROM weekly_schedule_view
            WHERE season = ? AND week = ? AND game_type = 'REG'
            ORDER BY week_game_number
            """,
            (args.season, args.week),
        ).fetchall()
        if not rows:
            print(f"No Week {args.week} games found for {args.season}.")
            return
        print(f"{args.season} Week {args.week}")
        for row in rows:
            print(
                f"  {row['game_date']} {row['game_time_et'] or ''} "
                f"{row['away_team']} at {row['home_team']} "
                f"({row['matchup_bucket']})"
            )
    finally:
        con.close()


def action_show_team(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        ensure_schema(con)
        team = args.team.upper()
        rows = con.execute(
            """
            SELECT *
            FROM team_schedule_view
            WHERE season = ? AND team = ? AND game_type = 'REG'
            ORDER BY week
            """,
            (args.season, team),
        ).fetchall()
        if not rows:
            print(f"No schedule found for {team} in {args.season}.")
            return
        bye = con.execute(
            """
            SELECT stb.week
            FROM season_team_byes stb
            JOIN teams t ON t.team_id = stb.team_id
            WHERE stb.season = ? AND t.abbreviation = ?
            """,
            (args.season, team),
        ).fetchone()
        bye_week = int(bye["week"]) if bye else None
        print(f"{team} {args.season} schedule")
        for row in rows:
            prefix = "vs" if row["site"] == "HOME" else "at"
            print(
                f"  Week {row['week']:>2}: {prefix} {row['opponent']:<3} "
                f"{row['game_date']} {row['game_time_et'] or ''} "
                f"({row['matchup_bucket']})"
            )
        if bye_week:
            print(f"  Bye: Week {bye_week}")
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and inspect NFL GM Sim schedules.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Create schedule tables/views.")
    setup_parser.set_defaults(func=action_setup)

    seed_parser = subparsers.add_parser("seed-2026", help="Seed official 2026 opponents and projected weeks.")
    seed_parser.add_argument("--replace", action=argparse.BooleanOptionalAction, default=True)
    seed_parser.add_argument("--replace-played", action="store_true", help="Allow replacing a season that has played games.")
    seed_parser.set_defaults(func=action_seed_2026)

    seed_formula_parser = subparsers.add_parser("seed-formula", help="Seed one projected future schedule from the NFL formula.")
    seed_formula_parser.add_argument("season", type=int)
    seed_formula_parser.add_argument("--replace", action=argparse.BooleanOptionalAction, default=True)
    seed_formula_parser.add_argument("--replace-played", action="store_true", help="Allow replacing a season that has played games.")
    seed_formula_parser.set_defaults(func=action_seed_formula)

    seed_range_parser = subparsers.add_parser("seed-range", help="Seed a range of projected schedules.")
    seed_range_parser.add_argument("--start-season", type=int, default=DEFAULT_SEASON)
    seed_range_parser.add_argument("--years", type=int, default=10)
    seed_range_parser.add_argument("--replace", action=argparse.BooleanOptionalAction, default=True)
    seed_range_parser.add_argument("--replace-played", action="store_true", help="Allow replacing seasons that have played games.")
    seed_range_parser.set_defaults(func=action_seed_range)

    validate_parser = subparsers.add_parser("validate", help="Validate a seeded schedule.")
    validate_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    validate_parser.set_defaults(func=action_validate)

    week_parser = subparsers.add_parser("week", help="Show a week schedule.")
    week_parser.add_argument("week", type=int)
    week_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    week_parser.set_defaults(func=action_show_week)

    team_parser = subparsers.add_parser("team", help="Show a team schedule.")
    team_parser.add_argument("team")
    team_parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    team_parser.set_defaults(func=action_show_team)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
