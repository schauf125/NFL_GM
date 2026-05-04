#!/usr/bin/env python3
"""Import yearly player profile stats into the NFL GM Sim database.

The importer is intentionally team-chunked so the roster can be filled in
alphabetical order: ARI, ATL, BAL, and so on.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import re
import sqlite3
import sys
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
USER_AGENT = "NFL_GM_SIM local stats importer"
PLAYERS_URL = "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
STATS_URL_TEMPLATE = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_reg_{season}.csv"
)

STAT_COLUMNS = [
    "team",
    "position",
    "games",
    "completions",
    "passing_attempts",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "sacks_suffered",
    "sack_yards_lost",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "rushing_fumbles",
    "rushing_fumbles_lost",
    "receptions",
    "targets",
    "receiving_yards",
    "receiving_tds",
    "receiving_fumbles",
    "receiving_fumbles_lost",
    "def_tackles_solo",
    "def_tackles_with_assist",
    "def_tackle_assists",
    "def_tackles_for_loss",
    "def_fumbles_forced",
    "def_sacks",
    "def_qb_hits",
    "def_interceptions",
    "def_interception_yards",
    "def_pass_defended",
    "def_tds",
    "def_safeties",
    "punt_returns",
    "punt_return_yards",
    "kickoff_returns",
    "kickoff_return_yards",
    "fg_made",
    "fg_att",
    "fg_long",
    "fg_pct",
    "pat_made",
    "pat_att",
    "pat_pct",
    "fantasy_points",
    "fantasy_points_ppr",
]

INT_FIELDS = {
    "games",
    "completions",
    "passing_attempts",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "sacks_suffered",
    "sack_yards_lost",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "rushing_fumbles",
    "rushing_fumbles_lost",
    "receptions",
    "targets",
    "receiving_yards",
    "receiving_tds",
    "receiving_fumbles",
    "receiving_fumbles_lost",
    "def_tackles_solo",
    "def_tackles_with_assist",
    "def_tackle_assists",
    "def_tackles_for_loss",
    "def_fumbles_forced",
    "def_qb_hits",
    "def_interceptions",
    "def_interception_yards",
    "def_pass_defended",
    "def_tds",
    "def_safeties",
    "punt_returns",
    "punt_return_yards",
    "kickoff_returns",
    "kickoff_return_yards",
    "fg_made",
    "fg_att",
    "fg_long",
    "pat_made",
    "pat_att",
}

FLOAT_FIELDS = {
    "def_sacks",
    "fg_pct",
    "pat_pct",
    "fantasy_points",
    "fantasy_points_ppr",
}

POSITION_GROUPS = {
    "QB": {"QB"},
    "RB": {"RB", "HB", "FB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "OL": {"OL", "T", "OT", "LT", "RT", "G", "OG", "LG", "RG", "C"},
    "DL": {"DL", "IDL", "DT", "NT", "DE"},
    "EDGE": {"EDGE", "DE", "OLB", "LB"},
    "LB": {"LB", "ILB", "MLB", "OLB", "EDGE"},
    "CB": {"CB"},
    "S": {"S", "FS", "SS"},
    "ST": {"K", "P", "LS"},
}


@dataclass(frozen=True)
class DbPlayer:
    player_id: int
    full_name: str
    first_name: str
    last_name: str
    position: str
    team_abbreviation: str
    age: int | None


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    while tokens and tokens[-1] in {"jr", "sr", "ii", "iii", "iv", "v"}:
        tokens.pop()
    return "".join(tokens)


def normalize_last_name(name: str) -> str:
    return normalize_name(name)


def first_initial(name: str | None) -> str:
    key = normalize_name(name or "")
    return key[0] if key else ""


def first_initial_matches(player: DbPlayer, candidate: dict[str, str]) -> bool:
    db_initial = first_initial(player.first_name)
    candidate_initials = {
        first_initial(candidate.get("display_name", "").split(" ")[0]),
        first_initial(candidate.get("first_name")),
        first_initial(candidate.get("common_first_name")),
        first_initial(candidate.get("football_name")),
    }
    return bool(db_initial and db_initial in candidate_initials)


def position_bucket(position: str) -> str:
    pos = (position or "").upper()
    for bucket, aliases in POSITION_GROUPS.items():
        if pos in aliases:
            return bucket
    return pos


def positions_compatible(db_position: str, external_position: str) -> bool:
    if not db_position or not external_position:
        return False
    return position_bucket(db_position) == position_bucket(external_position)


def normalized_team(team: str | None) -> str:
    value = (team or "").upper()
    return {"LA": "LAR", "JAC": "JAX", "WSH": "WAS"}.get(value, value)


def parse_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def candidate_age(candidate: dict[str, str]) -> int | None:
    birth_date = candidate.get("birth_date") or ""
    match = re.match(r"(\d{4})-\d{2}-\d{2}", birth_date)
    if not match:
        return None
    born = dt.date.fromisoformat(birth_date)
    today = dt.date.today()
    years = today.year - born.year
    if (today.month, today.day) < (born.month, born.day):
        years -= 1
    return years


def candidate_is_plausibly_current(
    player: DbPlayer, candidate: dict[str, str]
) -> bool:
    latest_team = normalized_team(candidate.get("latest_team"))
    if latest_team == player.team_abbreviation:
        return True

    last_season = parse_int(candidate.get("last_season"))
    if last_season is not None and last_season < 2024:
        return False

    age = candidate_age(candidate)
    if player.age is not None and age is not None and abs(age - player.age) > 3:
        return False

    return True


def int_value(value: str | None) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(float(value))
    except ValueError:
        return 0


def float_value(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def get_csv_reader(url: str) -> csv.DictReader:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    response = urllib.request.urlopen(request, timeout=60)
    text_stream = io.TextIOWrapper(response, encoding="utf-8-sig", newline="")
    return csv.DictReader(text_stream)


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS player_external_ids (
            player_id INTEGER PRIMARY KEY,
            gsis_id TEXT,
            pfr_id TEXT,
            pff_id TEXT,
            otc_id TEXT,
            espn_id TEXT,
            matched_name TEXT,
            latest_team TEXT,
            source TEXT NOT NULL DEFAULT 'nflverse_players',
            last_matched_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (player_id) REFERENCES players(player_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS player_season_stats (
            stat_id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            season INTEGER NOT NULL,
            team TEXT,
            position TEXT,
            games INTEGER NOT NULL DEFAULT 0,
            completions INTEGER NOT NULL DEFAULT 0,
            passing_attempts INTEGER NOT NULL DEFAULT 0,
            passing_yards INTEGER NOT NULL DEFAULT 0,
            passing_tds INTEGER NOT NULL DEFAULT 0,
            passing_interceptions INTEGER NOT NULL DEFAULT 0,
            sacks_suffered INTEGER NOT NULL DEFAULT 0,
            sack_yards_lost INTEGER NOT NULL DEFAULT 0,
            carries INTEGER NOT NULL DEFAULT 0,
            rushing_yards INTEGER NOT NULL DEFAULT 0,
            rushing_tds INTEGER NOT NULL DEFAULT 0,
            rushing_fumbles INTEGER NOT NULL DEFAULT 0,
            rushing_fumbles_lost INTEGER NOT NULL DEFAULT 0,
            receptions INTEGER NOT NULL DEFAULT 0,
            targets INTEGER NOT NULL DEFAULT 0,
            receiving_yards INTEGER NOT NULL DEFAULT 0,
            receiving_tds INTEGER NOT NULL DEFAULT 0,
            receiving_fumbles INTEGER NOT NULL DEFAULT 0,
            receiving_fumbles_lost INTEGER NOT NULL DEFAULT 0,
            def_tackles_solo INTEGER NOT NULL DEFAULT 0,
            def_tackles_with_assist INTEGER NOT NULL DEFAULT 0,
            def_tackle_assists INTEGER NOT NULL DEFAULT 0,
            def_tackles_for_loss INTEGER NOT NULL DEFAULT 0,
            def_fumbles_forced INTEGER NOT NULL DEFAULT 0,
            def_sacks REAL NOT NULL DEFAULT 0,
            def_qb_hits INTEGER NOT NULL DEFAULT 0,
            def_interceptions INTEGER NOT NULL DEFAULT 0,
            def_interception_yards INTEGER NOT NULL DEFAULT 0,
            def_pass_defended INTEGER NOT NULL DEFAULT 0,
            def_tds INTEGER NOT NULL DEFAULT 0,
            def_safeties INTEGER NOT NULL DEFAULT 0,
            punt_returns INTEGER NOT NULL DEFAULT 0,
            punt_return_yards INTEGER NOT NULL DEFAULT 0,
            kickoff_returns INTEGER NOT NULL DEFAULT 0,
            kickoff_return_yards INTEGER NOT NULL DEFAULT 0,
            fg_made INTEGER NOT NULL DEFAULT 0,
            fg_att INTEGER NOT NULL DEFAULT 0,
            fg_long INTEGER NOT NULL DEFAULT 0,
            fg_pct REAL,
            pat_made INTEGER NOT NULL DEFAULT 0,
            pat_att INTEGER NOT NULL DEFAULT 0,
            pat_pct REAL,
            fantasy_points REAL,
            fantasy_points_ppr REAL,
            external_player_id TEXT,
            pfr_id TEXT,
            source TEXT NOT NULL,
            source_url TEXT,
            imported_at TEXT NOT NULL,
            FOREIGN KEY (player_id) REFERENCES players(player_id) ON DELETE CASCADE,
            UNIQUE (player_id, season)
        );

        CREATE TABLE IF NOT EXISTS player_stats_import_log (
            import_id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_abbreviation TEXT NOT NULL,
            team_id INTEGER NOT NULL,
            seasons_start INTEGER NOT NULL,
            seasons_end INTEGER NOT NULL,
            source TEXT NOT NULL,
            source_detail TEXT,
            players_on_team INTEGER NOT NULL,
            players_matched_external INTEGER NOT NULL,
            players_with_stats INTEGER NOT NULL,
            stat_rows_imported INTEGER NOT NULL,
            players_without_stats INTEGER NOT NULL,
            run_at TEXT NOT NULL DEFAULT (datetime('now')),
            notes TEXT
        );

        DROP VIEW IF EXISTS player_season_stats_view;
        CREATE VIEW player_season_stats_view AS
        SELECT
            s.stat_id,
            t.abbreviation AS current_team,
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position AS current_position,
            s.season,
            s.team AS stat_team,
            s.position AS stat_position,
            s.games,
            s.completions,
            s.passing_attempts,
            s.passing_yards,
            s.passing_tds,
            s.passing_interceptions,
            s.sacks_suffered,
            s.carries,
            s.rushing_yards,
            s.rushing_tds,
            s.receptions,
            s.targets,
            s.receiving_yards,
            s.receiving_tds,
            s.def_tackles_solo,
            s.def_tackles_with_assist,
            s.def_sacks,
            s.def_qb_hits,
            s.def_interceptions,
            s.def_pass_defended,
            s.fg_made,
            s.fg_att,
            s.fg_pct,
            s.pat_made,
            s.pat_att,
            s.fantasy_points,
            s.fantasy_points_ppr,
            s.external_player_id,
            s.pfr_id,
            s.source,
            s.imported_at
        FROM player_season_stats s
        JOIN players p ON p.player_id = s.player_id
        LEFT JOIN teams t ON t.team_id = p.team_id;
        """
    )


def get_team_order(con: sqlite3.Connection) -> list[str]:
    rows = con.execute("SELECT abbreviation FROM teams ORDER BY abbreviation").fetchall()
    return [row[0] for row in rows]


def get_target_team(
    con: sqlite3.Connection, team: str | None, next_team: bool
) -> tuple[int, str]:
    if team and next_team:
        raise ValueError("Use either --team or --next, not both.")

    if team:
        row = con.execute(
            "SELECT team_id, abbreviation FROM teams WHERE abbreviation = ?",
            (team.upper(),),
        ).fetchone()
        if not row:
            raise ValueError(f"Team not found: {team}")
        return int(row[0]), row[1]

    if next_team:
        row = con.execute(
            """
            SELECT team_id, abbreviation
            FROM teams
            WHERE abbreviation NOT IN (
                SELECT team_abbreviation FROM player_stats_import_log
            )
            ORDER BY abbreviation
            LIMIT 1
            """
        ).fetchone()
        if not row:
            raise ValueError("Every team has at least one stats import log entry.")
        return int(row[0]), row[1]

    row = con.execute(
        "SELECT team_id, abbreviation FROM teams ORDER BY abbreviation LIMIT 1"
    ).fetchone()
    if not row:
        raise ValueError("No teams found in database.")
    return int(row[0]), row[1]


def load_db_players(con: sqlite3.Connection, team_id: int) -> list[DbPlayer]:
    rows = con.execute(
        """
        SELECT p.player_id,
               p.first_name || ' ' || p.last_name AS full_name,
               p.first_name,
               p.last_name,
               p.position,
               t.abbreviation,
               p.age
        FROM players p
        JOIN teams t ON t.team_id = p.team_id
        WHERE p.team_id = ?
        ORDER BY full_name
        """,
        (team_id,),
    ).fetchall()
    return [
        DbPlayer(
            player_id=int(row[0]),
            full_name=row[1],
            first_name=row[2],
            last_name=row[3],
            position=row[4],
            team_abbreviation=row[5],
            age=row[6],
        )
        for row in rows
    ]


def choose_external_match(
    player: DbPlayer, candidates: list[dict[str, str]]
) -> dict[str, str] | None:
    if not candidates:
        return None

    scored: list[tuple[int, dict[str, str]]] = []
    for candidate in candidates:
        if not candidate_is_plausibly_current(player, candidate):
            continue

        score = 0
        if normalized_team(candidate.get("latest_team")) == player.team_abbreviation:
            score += 100
        if positions_compatible(player.position, candidate.get("position", "")):
            score += 20
        age = candidate_age(candidate)
        if player.age is not None and age is not None and abs(age - player.age) <= 2:
            score += 20
        if normalize_name(candidate.get("display_name", "")) == normalize_name(
            player.full_name
        ):
            score += 10
        scored.append((score, candidate))

    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return None
    if len(scored) > 1 and scored[0][0] == scored[1][0] and scored[0][0] < 100:
        return None
    return scored[0][1]


def load_external_ids(
    con: sqlite3.Connection, players: list[DbPlayer]
) -> tuple[dict[int, dict[str, str]], dict[str, int]]:
    target_names = {normalize_name(player.full_name) for player in players}
    target_last_names = {normalize_last_name(player.last_name) for player in players}
    external_by_name: dict[str, list[dict[str, str]]] = {name: [] for name in target_names}
    external_by_last: dict[str, list[dict[str, str]]] = {
        last_name: [] for last_name in target_last_names
    }
    seen_candidates: dict[str, set[str]] = {name: set() for name in target_names}
    seen_last_candidates: dict[str, set[str]] = {
        last_name: set() for last_name in target_last_names
    }

    for row in get_csv_reader(PLAYERS_URL):
        possible_names = {
            row.get("display_name", ""),
            f"{row.get('first_name', '')} {row.get('last_name', '')}",
            f"{row.get('common_first_name', '')} {row.get('last_name', '')}",
            f"{row.get('football_name', '')} {row.get('last_name', '')}",
        }
        candidate_id = row.get("gsis_id") or row.get("pfr_id") or row.get("display_name") or ""
        for possible_name in possible_names:
            key = normalize_name(possible_name)
            if key in external_by_name and candidate_id not in seen_candidates[key]:
                external_by_name[key].append(row)
                seen_candidates[key].add(candidate_id)
        last_key = normalize_last_name(row.get("last_name", ""))
        if (
            last_key in external_by_last
            and candidate_id not in seen_last_candidates[last_key]
        ):
            external_by_last[last_key].append(row)
            seen_last_candidates[last_key].add(candidate_id)

    matched_by_player_id: dict[int, dict[str, str]] = {}
    gsis_to_db_player_id: dict[str, int] = {}
    matched_at = now_utc()

    for player in players:
        key = normalize_name(player.full_name)
        match = choose_external_match(player, external_by_name.get(key, []))
        if not match:
            last_key = normalize_last_name(player.last_name)
            last_candidates = [
                candidate
                for candidate in external_by_last.get(last_key, [])
                if normalized_team(candidate.get("latest_team"))
                == player.team_abbreviation
                and positions_compatible(player.position, candidate.get("position", ""))
                and first_initial_matches(player, candidate)
            ]
            match = choose_external_match(player, last_candidates)
        if not match:
            continue

        con.execute(
            """
            INSERT INTO player_external_ids (
                player_id, gsis_id, pfr_id, pff_id, otc_id, espn_id,
                matched_name, latest_team, source, last_matched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'nflverse_players', ?)
            ON CONFLICT(player_id) DO UPDATE SET
                gsis_id = excluded.gsis_id,
                pfr_id = excluded.pfr_id,
                pff_id = excluded.pff_id,
                otc_id = excluded.otc_id,
                espn_id = excluded.espn_id,
                matched_name = excluded.matched_name,
                latest_team = excluded.latest_team,
                source = excluded.source,
                last_matched_at = excluded.last_matched_at
            """,
            (
                player.player_id,
                match.get("gsis_id") or None,
                match.get("pfr_id") or None,
                match.get("pff_id") or None,
                match.get("otc_id") or None,
                match.get("espn_id") or None,
                match.get("display_name") or None,
                match.get("latest_team") or None,
                matched_at,
            ),
        )
        matched_by_player_id[player.player_id] = match
        gsis_id = match.get("gsis_id") or ""
        if gsis_id:
            gsis_to_db_player_id[gsis_id] = player.player_id

    return matched_by_player_id, gsis_to_db_player_id


def build_name_match_index(players: list[DbPlayer]) -> dict[str, int]:
    buckets: dict[str, list[int]] = {}
    for player in players:
        buckets.setdefault(normalize_name(player.full_name), []).append(player.player_id)
    return {
        key: player_ids[0]
        for key, player_ids in buckets.items()
        if len(player_ids) == 1
    }


def clear_team_stats(con: sqlite3.Connection, team_id: int, team_abbreviation: str) -> None:
    con.execute(
        """
        DELETE FROM player_season_stats
        WHERE player_id IN (SELECT player_id FROM players WHERE team_id = ?)
        """,
        (team_id,),
    )
    con.execute(
        """
        DELETE FROM player_external_ids
        WHERE player_id IN (SELECT player_id FROM players WHERE team_id = ?)
        """,
        (team_id,),
    )
    con.execute(
        "DELETE FROM player_stats_import_log WHERE team_abbreviation = ?",
        (team_abbreviation,),
    )


def row_stat_values(row: dict[str, str]) -> dict[str, object]:
    values: dict[str, object] = {
        "team": row.get("recent_team") or None,
        "position": row.get("position") or None,
    }
    for field in STAT_COLUMNS:
        if field in {"team", "position"}:
            continue
        source_field = "attempts" if field == "passing_attempts" else field
        if field in INT_FIELDS:
            values[field] = int_value(row.get(source_field))
        elif field in FLOAT_FIELDS:
            values[field] = float_value(row.get(source_field))
        else:
            values[field] = row.get(source_field) or None
    return values


def upsert_stat_row(
    con: sqlite3.Connection,
    player_id: int,
    season: int,
    values: dict[str, object],
    external: dict[str, str] | None,
    source_url: str,
) -> None:
    source = "nflverse_stats_player_reg"
    imported_at = now_utc()
    insert_columns = [
        "player_id",
        "season",
        *STAT_COLUMNS,
        "external_player_id",
        "pfr_id",
        "source",
        "source_url",
        "imported_at",
    ]
    insert_values = [
        player_id,
        season,
        *(values[column] for column in STAT_COLUMNS),
        (external or {}).get("gsis_id"),
        (external or {}).get("pfr_id"),
        source,
        source_url,
        imported_at,
    ]
    placeholders = ", ".join("?" for _ in insert_columns)
    assignments = ", ".join(
        f"{column} = excluded.{column}" for column in insert_columns[2:]
    )

    con.execute(
        f"""
        INSERT INTO player_season_stats ({", ".join(insert_columns)})
        VALUES ({placeholders})
        ON CONFLICT(player_id, season) DO UPDATE SET
            {assignments}
        """,
        insert_values,
    )


def import_team_stats(
    con: sqlite3.Connection,
    team_id: int,
    team_abbreviation: str,
    seasons: list[int],
) -> dict[str, object]:
    players = load_db_players(con, team_id)
    matched_external, gsis_to_db_player_id = load_external_ids(con, players)
    name_to_db_player_id = build_name_match_index(players)
    external_by_db_player_id = {
        player_id: external for player_id, external in matched_external.items()
    }

    rows_imported = 0
    players_with_stats: set[int] = set()
    skipped_seasons: list[int] = []

    for season in seasons:
        source_url = STATS_URL_TEMPLATE.format(season=season)
        try:
            reader = get_csv_reader(source_url)
            for row in reader:
                db_player_id = gsis_to_db_player_id.get(row.get("player_id") or "")
                if not db_player_id:
                    name_key = normalize_name(row.get("player_display_name", ""))
                    fallback_player_id = name_to_db_player_id.get(name_key)
                    same_current_team = (
                        normalized_team(row.get("recent_team")) == team_abbreviation
                    )
                    if (
                        fallback_player_id
                        and fallback_player_id not in matched_external
                        and season >= 2024
                        and same_current_team
                    ):
                        db_player_id = fallback_player_id
                if not db_player_id:
                    continue

                values = row_stat_values(row)
                external = external_by_db_player_id.get(db_player_id)
                upsert_stat_row(
                    con,
                    db_player_id,
                    season,
                    values,
                    external,
                    source_url,
                )
                players_with_stats.add(db_player_id)
                rows_imported += 1
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                skipped_seasons.append(season)
                continue
            raise

    players_without_stats = len(players) - len(players_with_stats)
    notes = (
        "Direct local Pro Football Reference scraping is blocked by Cloudflare. "
        "This import uses nflverse regular-season player stats with PFR IDs from "
        "the nflverse players file."
    )
    if skipped_seasons:
        notes += " Skipped missing stat files for seasons: " + ", ".join(
            str(season) for season in skipped_seasons
        )

    con.execute(
        """
        INSERT INTO player_stats_import_log (
            team_abbreviation, team_id, seasons_start, seasons_end,
            source, source_detail, players_on_team, players_matched_external,
            players_with_stats, stat_rows_imported, players_without_stats,
            run_at, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            team_abbreviation,
            team_id,
            min(seasons),
            max(seasons),
            "nflverse_stats_player_reg",
            f"{PLAYERS_URL}; {STATS_URL_TEMPLATE}",
            len(players),
            len(matched_external),
            len(players_with_stats),
            rows_imported,
            players_without_stats,
            now_utc(),
            notes,
        ),
    )

    return {
        "team": team_abbreviation,
        "players_on_team": len(players),
        "players_matched_external": len(matched_external),
        "players_with_stats": len(players_with_stats),
        "players_without_stats": players_without_stats,
        "stat_rows_imported": rows_imported,
        "seasons_start": min(seasons),
        "seasons_end": max(seasons),
        "skipped_seasons": skipped_seasons,
    }


def parse_seasons(raw: str) -> list[int]:
    parts = raw.split("-")
    if len(parts) == 1:
        season = int(parts[0])
        return [season]
    if len(parts) == 2:
        start = int(parts[0])
        end = int(parts[1])
        if end < start:
            raise ValueError("Season range end cannot be before start.")
        return list(range(start, end + 1))
    raise ValueError("Use a single year like 2025 or a range like 1999-2025.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import yearly player stats by team chunk."
    )
    parser.add_argument(
        "--team",
        help="Team abbreviation to import. Defaults to the first team alphabetically.",
    )
    parser.add_argument(
        "--next",
        action="store_true",
        help="Import the first team alphabetically that has no import log entry.",
    )
    parser.add_argument(
        "--seasons",
        default="1999-2025",
        help="Season or inclusive season range. Default: 1999-2025.",
    )
    parser.add_argument(
        "--db",
        default=str(DB_PATH),
        help=f"SQLite database path. Default: {DB_PATH}",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing stats/import log rows for the team before importing.",
    )
    args = parser.parse_args()

    seasons = parse_seasons(args.seasons)
    db_path = Path(args.db)
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    with sqlite3.connect(db_path) as con:
        con.execute("PRAGMA foreign_keys = ON")
        ensure_schema(con)
        team_id, team_abbreviation = get_target_team(con, args.team, args.next)
        if args.replace:
            clear_team_stats(con, team_id, team_abbreviation)
        order = get_team_order(con)
        result = import_team_stats(con, team_id, team_abbreviation, seasons)
        con.commit()

    print("Imported yearly stats chunk")
    print(f"Team: {result['team']}")
    print(f"Alphabetical order: {', '.join(order)}")
    print(f"Seasons: {result['seasons_start']}-{result['seasons_end']}")
    print(f"Players on roster: {result['players_on_team']}")
    print(f"External ID matches: {result['players_matched_external']}")
    print(f"Players with imported stats: {result['players_with_stats']}")
    print(f"Players without prior stats: {result['players_without_stats']}")
    print(f"Season stat rows imported: {result['stat_rows_imported']}")
    if result["skipped_seasons"]:
        print("Skipped seasons:", ", ".join(str(s) for s in result["skipped_seasons"]))
    print("Next chunk command: python tools/import_player_yearly_stats.py --next")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
