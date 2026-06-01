#!/usr/bin/env python3
"""Archive long-term league history from the live save tables.

The sim already records the raw ingredients for history in several places:
season stats, awards, transactions, draft picks, and standings. This module
turns those islands into durable, queryable history tables that can survive
long saves and feed user-facing story views.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import player_accolades  # noqa: E402
import refresh_career_stats  # noqa: E402
import postseason  # noqa: E402
from engine import match_engine  # noqa: E402


SOURCE = "history_archive"

CAREER_MILESTONES: list[dict[str, Any]] = [
    {
        "field": "career_games",
        "key": "career_games",
        "label": "Career Games",
        "group": "Longevity",
        "thresholds": [50, 100, 150, 200, 250],
        "sort": 90,
    },
    {
        "field": "passing_yards",
        "key": "passing_yards",
        "label": "Passing Yards",
        "group": "Passing",
        "thresholds": [10000, 20000, 30000, 40000, 50000, 60000],
        "sort": 10,
    },
    {
        "field": "passing_tds",
        "key": "passing_tds",
        "label": "Passing Touchdowns",
        "group": "Passing",
        "thresholds": [100, 200, 300, 400, 500],
        "sort": 11,
    },
    {
        "field": "rushing_yards",
        "key": "rushing_yards",
        "label": "Rushing Yards",
        "group": "Rushing",
        "thresholds": [3000, 5000, 8000, 10000, 12000],
        "sort": 20,
    },
    {
        "field": "rushing_tds",
        "key": "rushing_tds",
        "label": "Rushing Touchdowns",
        "group": "Rushing",
        "thresholds": [25, 50, 75, 100],
        "sort": 21,
    },
    {
        "field": "receiving_yards",
        "key": "receiving_yards",
        "label": "Receiving Yards",
        "group": "Receiving",
        "thresholds": [3000, 5000, 8000, 10000, 12000, 15000],
        "sort": 30,
    },
    {
        "field": "receptions",
        "key": "receptions",
        "label": "Receptions",
        "group": "Receiving",
        "thresholds": [250, 500, 750, 1000, 1250],
        "sort": 31,
    },
    {
        "field": "receiving_tds",
        "key": "receiving_tds",
        "label": "Receiving Touchdowns",
        "group": "Receiving",
        "thresholds": [25, 50, 75, 100],
        "sort": 32,
    },
    {
        "field": "scrimmage_yards",
        "key": "scrimmage_yards",
        "label": "Scrimmage Yards",
        "group": "Offense",
        "thresholds": [5000, 8000, 10000, 12000, 15000],
        "sort": 35,
    },
    {
        "field": "def_sacks",
        "key": "def_sacks",
        "label": "Sacks",
        "group": "Defense",
        "thresholds": [25, 50, 75, 100, 125, 150],
        "sort": 40,
    },
    {
        "field": "def_tackles_combined",
        "key": "def_tackles_combined",
        "label": "Tackles",
        "group": "Defense",
        "thresholds": [500, 750, 1000, 1250],
        "sort": 41,
    },
    {
        "field": "def_interceptions",
        "key": "def_interceptions",
        "label": "Interceptions",
        "group": "Defense",
        "thresholds": [10, 25, 40, 50],
        "sort": 42,
    },
    {
        "field": "fg_made",
        "key": "fg_made",
        "label": "Field Goals Made",
        "group": "Kicking",
        "thresholds": [100, 200, 300, 400, 500],
        "sort": 50,
    },
]

SEASON_FEATS: list[dict[str, Any]] = [
    {"field": "passing_yards", "key": "passing_5000", "label": "5,000-yard passing season", "threshold": 5000},
    {"field": "passing_tds", "key": "passing_td_45", "label": "45-touchdown passing season", "threshold": 45},
    {"field": "rushing_yards", "key": "rushing_1800", "label": "1,800-yard rushing season", "threshold": 1800},
    {"field": "rushing_tds", "key": "rushing_td_18", "label": "18-rushing-touchdown season", "threshold": 18},
    {"field": "receiving_yards", "key": "receiving_1800", "label": "1,800-yard receiving season", "threshold": 1800},
    {"field": "receiving_tds", "key": "receiving_td_16", "label": "16-receiving-touchdown season", "threshold": 16},
    {"field": "def_sacks", "key": "sacks_20", "label": "20-sack season", "threshold": 20},
    {"field": "def_interceptions", "key": "interceptions_7", "label": "7-interception season", "threshold": 7},
    {"field": "fg_made", "key": "fg_40", "label": "40-field-goal season", "threshold": 40},
]

TEAM_PLAYER_RECORDS: list[tuple[str, str, str]] = [
    ("passing_yards", "Passing Yards", "Passing"),
    ("passing_tds", "Passing Touchdowns", "Passing"),
    ("rushing_yards", "Rushing Yards", "Rushing"),
    ("rushing_tds", "Rushing Touchdowns", "Rushing"),
    ("receiving_yards", "Receiving Yards", "Receiving"),
    ("receiving_tds", "Receiving Touchdowns", "Receiving"),
    ("receptions", "Receptions", "Receiving"),
    ("def_sacks", "Sacks", "Defense"),
    ("def_interceptions", "Interceptions", "Defense"),
    ("def_tackles_combined", "Tackles", "Defense"),
    ("fg_made", "Field Goals Made", "Kicking"),
]

TEAM_SEASON_RECORDS: list[tuple[str, str]] = [
    ("wins", "Wins"),
    ("points_for", "Points For"),
    ("point_diff", "Point Differential"),
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


def columns(con: sqlite3.Connection, name: str) -> set[str]:
    if not table_exists(con, name):
        return set()
    return {str(row["name"]) for row in con.execute(f"PRAGMA table_info({name})").fetchall()}


def ensure_schema(con: sqlite3.Connection) -> None:
    match_engine.ensure_schema(con)
    postseason.ensure_schema(con)
    player_accolades.ensure_schema(con)
    refresh_career_stats.ensure_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS franchise_history_runs (
            season INTEGER PRIMARY KEY,
            draft_through_year INTEGER,
            teams_archived INTEGER NOT NULL DEFAULT 0,
            milestones_archived INTEGER NOT NULL DEFAULT 0,
            story_events_archived INTEGER NOT NULL DEFAULT 0,
            draft_classes_archived INTEGER NOT NULL DEFAULT 0,
            team_records_archived INTEGER NOT NULL DEFAULT 0,
            summary_json TEXT,
            notes TEXT,
            archived_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS team_season_history (
            season INTEGER NOT NULL,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            abbreviation TEXT,
            conference TEXT,
            division TEXT,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            ties INTEGER NOT NULL DEFAULT 0,
            win_pct REAL NOT NULL DEFAULT 0,
            points_for INTEGER NOT NULL DEFAULT 0,
            points_against INTEGER NOT NULL DEFAULT 0,
            point_diff INTEGER NOT NULL DEFAULT 0,
            division_rank INTEGER,
            conference_rank INTEGER,
            playoff_seed INTEGER,
            playoff_result TEXT NOT NULL DEFAULT 'Missed Playoffs',
            playoff_round_code TEXT,
            is_champion INTEGER NOT NULL DEFAULT 0,
            is_runner_up INTEGER NOT NULL DEFAULT 0,
            draft_slot INTEGER,
            source TEXT NOT NULL DEFAULT 'history_archive',
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (season, team_id)
        );

        DROP VIEW IF EXISTS team_season_history_view;
        CREATE VIEW team_season_history_view AS
        SELECT
            h.*,
            t.city,
            t.nickname,
            t.city || ' ' || t.nickname AS team_name
        FROM team_season_history h
        JOIN teams t ON t.team_id = h.team_id;

        CREATE TABLE IF NOT EXISTS player_career_milestones (
            milestone_id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            milestone_key TEXT NOT NULL,
            milestone_name TEXT NOT NULL,
            milestone_group TEXT NOT NULL,
            milestone_value REAL NOT NULL,
            threshold_value REAL NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 100,
            source TEXT NOT NULL DEFAULT 'history_archive',
            notes TEXT,
            fingerprint TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        DROP VIEW IF EXISTS player_career_milestones_view;
        CREATE VIEW player_career_milestones_view AS
        SELECT
            m.*,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            t.abbreviation AS team
        FROM player_career_milestones m
        JOIN players p ON p.player_id = m.player_id
        LEFT JOIN teams t ON t.team_id = m.team_id;

        CREATE TABLE IF NOT EXISTS career_story_events (
            story_id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER REFERENCES players(player_id) ON DELETE CASCADE,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            season INTEGER NOT NULL,
            story_date TEXT,
            story_type TEXT NOT NULL,
            story_tier TEXT NOT NULL DEFAULT 'note',
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            related_table TEXT,
            related_id INTEGER,
            fingerprint TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL DEFAULT 'history_archive',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        DROP VIEW IF EXISTS career_story_events_view;
        CREATE VIEW career_story_events_view AS
        SELECT
            e.*,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            t.abbreviation AS team
        FROM career_story_events e
        LEFT JOIN players p ON p.player_id = e.player_id
        LEFT JOIN teams t ON t.team_id = e.team_id;

        CREATE TABLE IF NOT EXISTS draft_class_history (
            draft_year INTEGER PRIMARY KEY,
            draft_class_id INTEGER REFERENCES draft_classes(draft_class_id) ON DELETE SET NULL,
            class_name TEXT,
            source_season INTEGER,
            status TEXT,
            total_prospects INTEGER NOT NULL DEFAULT 0,
            selected_count INTEGER NOT NULL DEFAULT 0,
            qbs_selected INTEGER NOT NULL DEFAULT 0,
            first_round_qbs INTEGER NOT NULL DEFAULT 0,
            top50_power_count INTEGER NOT NULL DEFAULT 0,
            top50_non_power_count INTEGER NOT NULL DEFAULT 0,
            top50_avg_true_grade REAL,
            top50_avg_potential REAL,
            top_pick_player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            top_pick_name TEXT,
            top_pick_position TEXT,
            top_pick_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            top_pick_true_grade INTEGER,
            top_pick_potential INTEGER,
            summary TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        DROP VIEW IF EXISTS draft_class_history_view;
        CREATE VIEW draft_class_history_view AS
        SELECT
            d.*,
            t.abbreviation AS top_pick_team
        FROM draft_class_history d
        LEFT JOIN teams t ON t.team_id = d.top_pick_team_id;

        CREATE TABLE IF NOT EXISTS draft_class_pick_history (
            draft_year INTEGER NOT NULL,
            pick_id INTEGER NOT NULL,
            round INTEGER,
            pick_number INTEGER,
            pick_in_round INTEGER,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            original_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            prospect_id INTEGER REFERENCES draft_prospects(prospect_id) ON DELETE SET NULL,
            player_name TEXT,
            position TEXT,
            college TEXT,
            true_grade INTEGER,
            potential INTEGER,
            public_rank INTEGER,
            true_rank INTEGER,
            scout_confidence TEXT,
            summary TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (draft_year, pick_id)
        );

        DROP VIEW IF EXISTS draft_class_pick_history_view;
        CREATE VIEW draft_class_pick_history_view AS
        SELECT
            h.*,
            owner.abbreviation AS team,
            original.abbreviation AS original_team
        FROM draft_class_pick_history h
        LEFT JOIN teams owner ON owner.team_id = h.team_id
        LEFT JOIN teams original ON original.team_id = h.original_team_id;

        CREATE TABLE IF NOT EXISTS team_record_book (
            record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_scope TEXT NOT NULL,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE CASCADE,
            stat_key TEXT NOT NULL,
            stat_name TEXT NOT NULL,
            stat_group TEXT,
            season INTEGER NOT NULL,
            player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            player_name TEXT,
            value REAL NOT NULL,
            rank INTEGER NOT NULL DEFAULT 1,
            notes TEXT,
            fingerprint TEXT NOT NULL UNIQUE,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        DROP VIEW IF EXISTS team_record_book_view;
        CREATE VIEW team_record_book_view AS
        SELECT
            r.*,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name
        FROM team_record_book r
        LEFT JOIN teams t ON t.team_id = r.team_id;
        """
    )


def current_season(con: sqlite3.Connection) -> int:
    for key in ("current_league_year", "current_season"):
        row = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = ?",
            (key,),
        ).fetchone()
        if row and row["setting_value"]:
            return int(row["setting_value"])
    return match_engine.DEFAULT_SEASON


def fmt_number(value: float | int) -> str:
    number = float(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.1f}"


def team_rank_maps(con: sqlite3.Connection, season: int) -> tuple[dict[int, int], dict[int, int]]:
    rows = con.execute(
        """
        SELECT team_id, conference, division, wins, losses, ties, point_diff, points_for
        FROM season_standings_view
        WHERE season = ?
        """,
        (season,),
    ).fetchall()
    conference_rank: dict[int, int] = {}
    division_rank: dict[int, int] = {}
    for conference in sorted({row["conference"] for row in rows}):
        pool = [row for row in rows if row["conference"] == conference]
        ordered = sorted(
            pool,
            key=lambda row: (
                -((int(row["wins"] or 0) + 0.5 * int(row["ties"] or 0)) / max(1, int(row["wins"] or 0) + int(row["losses"] or 0) + int(row["ties"] or 0))),
                -int(row["point_diff"] or 0),
                -int(row["points_for"] or 0),
                int(row["team_id"]),
            ),
        )
        for rank, row in enumerate(ordered, start=1):
            conference_rank[int(row["team_id"])] = rank
    for conference in sorted({row["conference"] for row in rows}):
        for division in sorted({row["division"] for row in rows if row["conference"] == conference}):
            pool = [row for row in rows if row["conference"] == conference and row["division"] == division]
            ordered = sorted(
                pool,
                key=lambda row: (
                    -((int(row["wins"] or 0) + 0.5 * int(row["ties"] or 0)) / max(1, int(row["wins"] or 0) + int(row["losses"] or 0) + int(row["ties"] or 0))),
                    -int(row["point_diff"] or 0),
                    -int(row["points_for"] or 0),
                    int(row["team_id"]),
                ),
            )
            for rank, row in enumerate(ordered, start=1):
                division_rank[int(row["team_id"])] = rank
    return conference_rank, division_rank


def postseason_results(con: sqlite3.Connection, season: int) -> dict[int, dict[str, Any]]:
    results: dict[int, dict[str, Any]] = {}
    if table_exists(con, "playoff_seedings"):
        for row in con.execute(
            "SELECT team_id, seed FROM playoff_seedings WHERE season = ?",
            (season,),
        ).fetchall():
            results[int(row["team_id"])] = {
                "seed": int(row["seed"]),
                "result": "Made Playoffs",
                "round": None,
                "champion": False,
                "runner_up": False,
            }
    try:
        eliminated, champion, runner_up = postseason.elimination_rounds(con, season)
    except Exception:
        eliminated, champion, runner_up = {}, None, None
    round_labels = {
        "WC": "Lost Wild Card",
        "DIV": "Lost Divisional Round",
        "CONF": "Lost Conference Championship",
        "SB": "Lost Super Bowl",
    }
    for team_id, round_code in eliminated.items():
        item = results.setdefault(int(team_id), {"seed": None})
        item.update(
            {
                "result": round_labels.get(round_code, "Eliminated"),
                "round": round_code,
                "champion": False,
                "runner_up": int(team_id) == runner_up,
            }
        )
    if runner_up is not None:
        item = results.setdefault(int(runner_up), {"seed": None})
        item.update({"result": "Lost Super Bowl", "round": "SB", "runner_up": True, "champion": False})
    if champion is not None:
        item = results.setdefault(int(champion), {"seed": None})
        item.update({"result": "Won Super Bowl", "round": "SB", "champion": True, "runner_up": False})
    return results


def archive_team_seasons(con: sqlite3.Connection, season: int) -> int:
    if not table_exists(con, "season_standings_view"):
        return 0
    conf_rank, div_rank = team_rank_maps(con, season)
    post = postseason_results(con, season)
    draft_slots = {
        int(row["team_id"]): int(row["slot"])
        for row in con.execute(
            """
            SELECT team_id, slot
            FROM draft_order_slots
            WHERE source_season = ?
            """,
            (season,),
        ).fetchall()
    } if table_exists(con, "draft_order_slots") else {}
    rows = con.execute(
        """
        SELECT *
        FROM season_standings_view
        WHERE season = ?
        """,
        (season,),
    ).fetchall()
    changed = 0
    for row in rows:
        team_id = int(row["team_id"])
        games = int(row["wins"] or 0) + int(row["losses"] or 0) + int(row["ties"] or 0)
        if games <= 0:
            continue
        result = post.get(team_id, {})
        con.execute(
            """
            INSERT INTO team_season_history (
                season, team_id, abbreviation, conference, division, wins, losses, ties,
                win_pct, points_for, points_against, point_diff, division_rank,
                conference_rank, playoff_seed, playoff_result, playoff_round_code,
                is_champion, is_runner_up, draft_slot, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(season, team_id) DO UPDATE SET
                abbreviation = excluded.abbreviation,
                conference = excluded.conference,
                division = excluded.division,
                wins = excluded.wins,
                losses = excluded.losses,
                ties = excluded.ties,
                win_pct = excluded.win_pct,
                points_for = excluded.points_for,
                points_against = excluded.points_against,
                point_diff = excluded.point_diff,
                division_rank = excluded.division_rank,
                conference_rank = excluded.conference_rank,
                playoff_seed = excluded.playoff_seed,
                playoff_result = excluded.playoff_result,
                playoff_round_code = excluded.playoff_round_code,
                is_champion = excluded.is_champion,
                is_runner_up = excluded.is_runner_up,
                draft_slot = excluded.draft_slot,
                updated_at = datetime('now')
            """,
            (
                season,
                team_id,
                row["abbreviation"],
                row["conference"],
                row["division"],
                int(row["wins"] or 0),
                int(row["losses"] or 0),
                int(row["ties"] or 0),
                float(row["win_pct"] or 0),
                int(row["points_for"] or 0),
                int(row["points_against"] or 0),
                int(row["point_diff"] or 0),
                div_rank.get(team_id),
                conf_rank.get(team_id),
                result.get("seed"),
                result.get("result") or "Missed Playoffs",
                result.get("round"),
                1 if result.get("champion") else 0,
                1 if result.get("runner_up") else 0,
                draft_slots.get(team_id),
                SOURCE,
            ),
        )
        changed += 1
    return changed


def archive_career_milestones(con: sqlite3.Connection, season: int) -> int:
    refresh_career_stats.refresh_career_stats(con)
    rows = con.execute(
        """
        SELECT c.*, p.team_id, p.position, p.first_name || ' ' || p.last_name AS player_name
        FROM player_career_stats c
        JOIN players p ON p.player_id = c.player_id
        """
    ).fetchall()
    inserted = 0
    for row in rows:
        for definition in CAREER_MILESTONES:
            value = float(row[definition["field"]] or 0)
            for threshold in definition["thresholds"]:
                if value < float(threshold):
                    continue
                key = f"{definition['key']}_{threshold}"
                label = f"{fmt_number(threshold)} {definition['label']}"
                fingerprint = f"milestone:{int(row['player_id'])}:{key}"
                before = con.total_changes
                con.execute(
                    """
                    INSERT OR IGNORE INTO player_career_milestones (
                        player_id, season, team_id, milestone_key, milestone_name,
                        milestone_group, milestone_value, threshold_value, sort_order,
                        source, notes, fingerprint, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (
                        int(row["player_id"]),
                        season,
                        row["team_id"],
                        key,
                        label,
                        definition["group"],
                        value,
                        float(threshold),
                        int(definition["sort"]),
                        SOURCE,
                        f"Reached during or before the {season} season.",
                        fingerprint,
                    ),
                )
                if con.total_changes > before:
                    inserted += 1
    return inserted


def story_tier_for_award(key: str) -> str:
    if key in {"MVP", "SUPER_BOWL_TITLE"}:
        return "gold"
    if key in {"FIRST_TEAM_ALL_PRO", "ROOKIE_OF_YEAR", "COMEBACK_PLAYER_OF_YEAR"}:
        return "silver"
    if key == "SECOND_TEAM_ALL_PRO":
        return "bronze"
    return "note"


def insert_story(
    con: sqlite3.Connection,
    *,
    player_id: int | None,
    team_id: int | None,
    season: int,
    story_date: str | None,
    story_type: str,
    story_tier: str,
    title: str,
    summary: str,
    related_table: str | None,
    related_id: int | None,
    fingerprint: str,
) -> int:
    before = con.total_changes
    con.execute(
        """
        INSERT OR IGNORE INTO career_story_events (
            player_id, team_id, season, story_date, story_type, story_tier,
            title, summary, related_table, related_id, fingerprint, source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            player_id,
            team_id,
            season,
            story_date,
            story_type,
            story_tier,
            title[:160],
            summary[:600],
            related_table,
            related_id,
            fingerprint,
            SOURCE,
        ),
    )
    return 1 if con.total_changes > before else 0


def archive_accolade_stories(con: sqlite3.Connection, season: int) -> int:
    if not table_exists(con, "player_accolades"):
        return 0
    rows = con.execute(
        """
        SELECT pa.*, p.first_name || ' ' || p.last_name AS player_name, t.abbreviation AS team
        FROM player_accolades pa
        JOIN players p ON p.player_id = pa.player_id
        LEFT JOIN teams t ON t.team_id = pa.team_id
        WHERE pa.season = ?
          AND pa.award_key != 'POSITION_OF_YEAR'
        """,
        (season,),
    ).fetchall()
    inserted = 0
    for row in rows:
        key = str(row["award_key"] or "")
        if key == "PRO_BOWL":
            continue
        title = str(row["award_name"] or row["badge_label"] or "Accolade")
        summary = f"{row['player_name']} earned {title}"
        if row["team"]:
            summary += f" with {row['team']}"
        summary += f" for the {season} season."
        inserted += insert_story(
            con,
            player_id=int(row["player_id"]),
            team_id=row["team_id"],
            season=season,
            story_date=f"{season + 1}-02-15",
            story_type="award",
            story_tier=story_tier_for_award(key),
            title=title,
            summary=summary,
            related_table="player_accolades",
            related_id=int(row["accolade_id"]),
            fingerprint=f"story:accolade:{row['accolade_id']}",
        )
    return inserted


def archive_season_feat_stories(con: sqlite3.Connection, season: int) -> int:
    if not table_exists(con, "player_season_stats"):
        return 0
    inserted = 0
    for feat in SEASON_FEATS:
        if feat["field"] not in columns(con, "player_season_stats"):
            continue
        rows = con.execute(
            f"""
            SELECT
                s.player_id,
                s.team,
                s.position,
                s.{feat['field']} AS value,
                p.first_name || ' ' || p.last_name AS player_name,
                t.team_id
            FROM player_season_stats s
            JOIN players p ON p.player_id = s.player_id
            LEFT JOIN teams t ON t.abbreviation = s.team
            WHERE s.season = ?
              AND COALESCE(s.{feat['field']}, 0) >= ?
            ORDER BY s.{feat['field']} DESC
            """,
            (season, feat["threshold"]),
        ).fetchall()
        for row in rows:
            value = float(row["value"] or 0)
            inserted += insert_story(
                con,
                player_id=int(row["player_id"]),
                team_id=row["team_id"],
                season=season,
                story_date=f"{season + 1}-01-15",
                story_type="season_feat",
                story_tier="silver",
                title=str(feat["label"]),
                summary=f"{row['player_name']} posted {fmt_number(value)} in {feat['label']} territory for {row['team'] or 'his club'} in {season}.",
                related_table="player_season_stats",
                related_id=None,
                fingerprint=f"story:season-feat:{season}:{row['player_id']}:{feat['key']}",
            )
    return inserted


def archive_transaction_stories(con: sqlite3.Connection, season: int) -> int:
    if not table_exists(con, "transaction_log_view"):
        return 0
    rows = con.execute(
        """
        SELECT *
        FROM transaction_log_view
        WHERE season = ?
          AND player_id IS NOT NULL
          AND (
                COALESCE(ABS(cash_delta), 0) >= 8000000
             OR COALESCE(ABS(cap_delta_current), 0) >= 8000000
             OR LOWER(COALESCE(transaction_type, '')) LIKE '%trade%'
             OR LOWER(COALESCE(transaction_type, '')) LIKE '%tag%'
             OR LOWER(COALESCE(transaction_type, '')) LIKE '%extension%'
             OR LOWER(COALESCE(transaction_type, '')) LIKE '%retire%'
          )
        ORDER BY transaction_date, transaction_id
        """,
        (season,),
    ).fetchall()
    inserted = 0
    for row in rows:
        title = str(row["transaction_type"] or row["transaction_category"] or "Transaction")
        description = str(row["description"] or "")
        if not description:
            description = f"{row['player_name']} was part of a {title.lower()}."
        category = str(row["transaction_category"] or title).lower()
        tier = "silver" if "trade" in category or "contract" in category else "note"
        inserted += insert_story(
            con,
            player_id=int(row["player_id"]),
            team_id=row["team_id"] or row["to_team_id"] or row["from_team_id"],
            season=season,
            story_date=row["transaction_date"],
            story_type="transaction",
            story_tier=tier,
            title=title,
            summary=description,
            related_table="transaction_log",
            related_id=int(row["transaction_id"]),
            fingerprint=f"story:transaction:{row['transaction_id']}",
        )
    return inserted


def archive_draft_class(con: sqlite3.Connection, draft_year: int) -> int:
    if not (table_exists(con, "draft_classes") and table_exists(con, "draft_picks")):
        return 0
    draft_class = con.execute(
        "SELECT * FROM draft_classes WHERE draft_year = ? ORDER BY draft_class_id DESC LIMIT 1",
        (draft_year,),
    ).fetchone()
    if not draft_class:
        return 0
    selected_count = int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM draft_picks
            WHERE draft_year = ?
              AND selected_player_id IS NOT NULL
            """,
            (draft_year,),
        ).fetchone()[0]
        or 0
    )
    if selected_count <= 0:
        return 0
    top50_rows = con.execute(
        """
        SELECT college_tier, true_grade, potential
        FROM draft_prospects
        WHERE draft_class_id = ?
          AND COALESCE(public_board_rank, scouting_rank, 9999) <= 50
        """,
        (draft_class["draft_class_id"],),
    ).fetchall() if table_exists(con, "draft_prospects") else []
    top50_power = 0
    top50_non_power = 0
    top50_true: list[float] = []
    top50_potential: list[float] = []
    for row in top50_rows:
        tier = str(row["college_tier"] or "").lower()
        if any(token in tier for token in ("power", "blue", "p5", "sec", "big ten", "big12", "acc")):
            top50_power += 1
        else:
            top50_non_power += 1
        if row["true_grade"] is not None:
            top50_true.append(float(row["true_grade"]))
        if row["potential"] is not None:
            top50_potential.append(float(row["potential"]))
    top_pick = con.execute(
        """
        SELECT
            dp.*,
            pr.prospect_id,
            COALESCE(pr.first_name || ' ' || pr.last_name, p.first_name || ' ' || p.last_name) AS player_name,
            COALESCE(pr.position, p.position) AS position,
            COALESCE(pr.true_grade, pr.overall, p.overall) AS true_grade,
            COALESCE(pr.potential, p.potential) AS potential
        FROM draft_picks dp
        LEFT JOIN draft_prospects pr ON pr.selected_pick_id = dp.pick_id
        LEFT JOIN players p ON p.player_id = dp.selected_player_id
        WHERE dp.draft_year = ?
          AND dp.selected_player_id IS NOT NULL
        ORDER BY dp.round, dp.pick_number
        LIMIT 1
        """,
        (draft_year,),
    ).fetchone()
    qb_counts = con.execute(
        """
        SELECT
            SUM(CASE WHEN COALESCE(pr.position, p.position) = 'QB' THEN 1 ELSE 0 END) AS qbs,
            SUM(CASE WHEN COALESCE(pr.position, p.position) = 'QB' AND dp.round = 1 THEN 1 ELSE 0 END) AS first_round_qbs
        FROM draft_picks dp
        LEFT JOIN draft_prospects pr ON pr.selected_pick_id = dp.pick_id
        LEFT JOIN players p ON p.player_id = dp.selected_player_id
        WHERE dp.draft_year = ?
          AND dp.selected_player_id IS NOT NULL
        """,
        (draft_year,),
    ).fetchone()
    total_prospects = int(
        con.execute(
            "SELECT COUNT(*) FROM draft_prospects WHERE draft_class_id = ?",
            (draft_class["draft_class_id"],),
        ).fetchone()[0]
        or 0
    ) if table_exists(con, "draft_prospects") else 0
    summary = f"{selected_count} players selected"
    if top_pick:
        summary += f"; first pick was {top_pick['player_name']} ({top_pick['position']})."
    con.execute(
        """
        INSERT INTO draft_class_history (
            draft_year, draft_class_id, class_name, source_season, status,
            total_prospects, selected_count, qbs_selected, first_round_qbs,
            top50_power_count, top50_non_power_count, top50_avg_true_grade,
            top50_avg_potential, top_pick_player_id, top_pick_name,
            top_pick_position, top_pick_team_id, top_pick_true_grade,
            top_pick_potential, summary, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(draft_year) DO UPDATE SET
            draft_class_id = excluded.draft_class_id,
            class_name = excluded.class_name,
            source_season = excluded.source_season,
            status = excluded.status,
            total_prospects = excluded.total_prospects,
            selected_count = excluded.selected_count,
            qbs_selected = excluded.qbs_selected,
            first_round_qbs = excluded.first_round_qbs,
            top50_power_count = excluded.top50_power_count,
            top50_non_power_count = excluded.top50_non_power_count,
            top50_avg_true_grade = excluded.top50_avg_true_grade,
            top50_avg_potential = excluded.top50_avg_potential,
            top_pick_player_id = excluded.top_pick_player_id,
            top_pick_name = excluded.top_pick_name,
            top_pick_position = excluded.top_pick_position,
            top_pick_team_id = excluded.top_pick_team_id,
            top_pick_true_grade = excluded.top_pick_true_grade,
            top_pick_potential = excluded.top_pick_potential,
            summary = excluded.summary,
            updated_at = datetime('now')
        """,
        (
            draft_year,
            int(draft_class["draft_class_id"]),
            draft_class["class_name"],
            draft_year - 1,
            draft_class["status"],
            total_prospects,
            selected_count,
            int(qb_counts["qbs"] or 0),
            int(qb_counts["first_round_qbs"] or 0),
            top50_power,
            top50_non_power,
            round(sum(top50_true) / len(top50_true), 1) if top50_true else None,
            round(sum(top50_potential) / len(top50_potential), 1) if top50_potential else None,
            int(top_pick["selected_player_id"]) if top_pick and top_pick["selected_player_id"] is not None else None,
            top_pick["player_name"] if top_pick else None,
            top_pick["position"] if top_pick else None,
            int(top_pick["current_team_id"]) if top_pick and top_pick["current_team_id"] is not None else None,
            int(top_pick["true_grade"]) if top_pick and top_pick["true_grade"] is not None else None,
            int(top_pick["potential"]) if top_pick and top_pick["potential"] is not None else None,
            summary,
        ),
    )
    return 1


def archive_draft_picks(con: sqlite3.Connection, draft_year: int) -> int:
    if not table_exists(con, "draft_picks"):
        return 0
    rows = con.execute(
        """
        SELECT
            dp.pick_id,
            dp.draft_year,
            dp.round,
            dp.pick_number,
            dp.pick_in_round,
            dp.current_team_id,
            dp.original_team_id,
            dp.selected_player_id,
            pr.prospect_id,
            COALESCE(pr.first_name || ' ' || pr.last_name, p.first_name || ' ' || p.last_name) AS player_name,
            COALESCE(pr.position, p.position) AS position,
            COALESCE(pr.college, p.college) AS college,
            COALESCE(pr.true_grade, pr.overall, p.overall) AS true_grade,
            COALESCE(pr.potential, p.potential) AS potential,
            COALESCE(pr.public_board_rank, pr.scouting_rank) AS public_rank,
            pr.true_rank,
            pr.scout_confidence
        FROM draft_picks dp
        LEFT JOIN draft_prospects pr ON pr.selected_pick_id = dp.pick_id
        LEFT JOIN players p ON p.player_id = dp.selected_player_id
        WHERE dp.draft_year = ?
          AND dp.selected_player_id IS NOT NULL
        ORDER BY dp.round, dp.pick_number, dp.pick_id
        """,
        (draft_year,),
    ).fetchall()
    changed = 0
    for row in rows:
        summary = f"Round {row['round']}, pick {row['pick_number']}: {row['player_name']} ({row['position']})"
        con.execute(
            """
            INSERT INTO draft_class_pick_history (
                draft_year, pick_id, round, pick_number, pick_in_round, team_id,
                original_team_id, player_id, prospect_id, player_name, position,
                college, true_grade, potential, public_rank, true_rank,
                scout_confidence, summary, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(draft_year, pick_id) DO UPDATE SET
                round = excluded.round,
                pick_number = excluded.pick_number,
                pick_in_round = excluded.pick_in_round,
                team_id = excluded.team_id,
                original_team_id = excluded.original_team_id,
                player_id = excluded.player_id,
                prospect_id = excluded.prospect_id,
                player_name = excluded.player_name,
                position = excluded.position,
                college = excluded.college,
                true_grade = excluded.true_grade,
                potential = excluded.potential,
                public_rank = excluded.public_rank,
                true_rank = excluded.true_rank,
                scout_confidence = excluded.scout_confidence,
                summary = excluded.summary,
                updated_at = datetime('now')
            """,
            (
                int(row["draft_year"]),
                int(row["pick_id"]),
                row["round"],
                row["pick_number"],
                row["pick_in_round"],
                row["current_team_id"],
                row["original_team_id"],
                row["selected_player_id"],
                row["prospect_id"],
                row["player_name"],
                row["position"],
                row["college"],
                row["true_grade"],
                row["potential"],
                row["public_rank"],
                row["true_rank"],
                row["scout_confidence"],
                summary,
            ),
        )
        changed += 1
    return changed


def archive_completed_drafts(con: sqlite3.Connection, through_year: int) -> tuple[int, int]:
    if not table_exists(con, "draft_classes"):
        return 0, 0
    years = [
        int(row["draft_year"])
        for row in con.execute(
            """
            SELECT draft_year
            FROM draft_classes
            WHERE draft_year <= ?
            ORDER BY draft_year
            """,
            (through_year,),
        ).fetchall()
    ]
    class_count = 0
    pick_count = 0
    for draft_year in years:
        class_count += archive_draft_class(con, draft_year)
        pick_count += archive_draft_picks(con, draft_year)
    return class_count, pick_count


def archive_team_record_book(con: sqlite3.Connection) -> int:
    if not table_exists(con, "player_season_stats"):
        return 0
    con.execute("DELETE FROM team_record_book WHERE record_scope IN ('player_single_season', 'team_single_season')")
    inserted = 0
    pss_columns = columns(con, "player_season_stats")
    for field, label, group in TEAM_PLAYER_RECORDS:
        if field not in pss_columns:
            continue
        if field == "def_tackles_combined":
            value_expr = "COALESCE(s.def_tackles_solo, 0) + COALESCE(s.def_tackles_with_assist, 0)"
        else:
            value_expr = f"COALESCE(s.{field}, 0)"
        rows = con.execute(
            f"""
            WITH ranked AS (
                SELECT
                    t.team_id,
                    s.season,
                    s.player_id,
                    p.first_name || ' ' || p.last_name AS player_name,
                    {value_expr} AS value,
                    ROW_NUMBER() OVER (
                        PARTITION BY t.team_id
                        ORDER BY {value_expr} DESC, s.season DESC, s.player_id
                    ) AS record_rank
                FROM player_season_stats s
                JOIN players p ON p.player_id = s.player_id
                JOIN teams t ON t.abbreviation = s.team
                WHERE {value_expr} > 0
            )
            SELECT *
            FROM ranked
            WHERE record_rank <= 5
            """,
        ).fetchall()
        for row in rows:
            fingerprint = f"record:player_single_season:{row['team_id']}:{field}:{row['season']}:{row['player_id']}:{row['record_rank']}"
            con.execute(
                """
                INSERT OR IGNORE INTO team_record_book (
                    record_scope, team_id, stat_key, stat_name, stat_group, season,
                    player_id, player_name, value, rank, notes, fingerprint, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    "player_single_season",
                    int(row["team_id"]),
                    field,
                    label,
                    group,
                    int(row["season"]),
                    int(row["player_id"]),
                    row["player_name"],
                    float(row["value"] or 0),
                    int(row["record_rank"]),
                    "Top five single-season player mark for this franchise.",
                    fingerprint,
                ),
            )
            inserted += 1
    for field, label in TEAM_SEASON_RECORDS:
        if field == "point_diff":
            value_expr = "COALESCE(points_for, 0) - COALESCE(points_against, 0)"
        else:
            value_expr = f"COALESCE({field}, 0)"
        rows = con.execute(
            f"""
            WITH ranked AS (
                SELECT
                    team_id,
                    season,
                    {value_expr} AS value,
                    ROW_NUMBER() OVER (
                        PARTITION BY team_id
                        ORDER BY {value_expr} DESC, season DESC
                    ) AS record_rank
                FROM season_team_records
                WHERE {value_expr} IS NOT NULL
            )
            SELECT *
            FROM ranked
            WHERE record_rank <= 5
            """,
        ).fetchall() if table_exists(con, "season_team_records") else []
        for row in rows:
            fingerprint = f"record:team_single_season:{row['team_id']}:{field}:{row['season']}:{row['record_rank']}"
            con.execute(
                """
                INSERT OR IGNORE INTO team_record_book (
                    record_scope, team_id, stat_key, stat_name, stat_group, season,
                    value, rank, notes, fingerprint, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    "team_single_season",
                    int(row["team_id"]),
                    field,
                    label,
                    "Team",
                    int(row["season"]),
                    float(row["value"] or 0),
                    int(row["record_rank"]),
                    "Top five single-season team mark for this franchise.",
                    fingerprint,
                ),
            )
            inserted += 1
    return inserted


def archive_history(
    con: sqlite3.Connection,
    *,
    season: int,
    draft_through_year: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    ensure_schema(con)
    draft_through_year = int(draft_through_year or season + 1)
    try:
        match_engine.rebuild_season_history(con, season)
    except Exception:
        pass
    if force:
        con.execute("DELETE FROM team_season_history WHERE season = ?", (season,))
        con.execute("DELETE FROM career_story_events WHERE season = ? AND source = ?", (season, SOURCE))
        con.execute("DELETE FROM player_career_milestones WHERE season = ? AND source = ?", (season, SOURCE))

    player_accolades.generate_season_accolades(con, season, force=False)
    teams = archive_team_seasons(con, season)
    milestones = archive_career_milestones(con, season)
    stories = 0
    stories += archive_accolade_stories(con, season)
    stories += archive_season_feat_stories(con, season)
    stories += archive_transaction_stories(con, season)
    draft_classes, draft_picks = archive_completed_drafts(con, draft_through_year)
    team_records = archive_team_record_book(con)

    summary = {
        "season": season,
        "draftThroughYear": draft_through_year,
        "teamsArchived": teams,
        "milestonesArchived": milestones,
        "storyEventsArchived": stories,
        "draftClassesArchived": draft_classes,
        "draftPicksArchived": draft_picks,
        "teamRecordsArchived": team_records,
    }
    con.execute(
        """
        INSERT INTO franchise_history_runs (
            season, draft_through_year, teams_archived, milestones_archived,
            story_events_archived, draft_classes_archived, team_records_archived,
            summary_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(season) DO UPDATE SET
            draft_through_year = excluded.draft_through_year,
            teams_archived = excluded.teams_archived,
            milestones_archived = excluded.milestones_archived,
            story_events_archived = excluded.story_events_archived,
            draft_classes_archived = excluded.draft_classes_archived,
            team_records_archived = excluded.team_records_archived,
            summary_json = excluded.summary_json,
            updated_at = datetime('now')
        """,
        (
            season,
            draft_through_year,
            teams,
            milestones,
            stories,
            draft_classes,
            team_records,
            json.dumps(summary, sort_keys=True),
        ),
    )
    return summary


def print_summary(summary: dict[str, Any]) -> None:
    print(f"History archived for {summary['season']} through draft {summary['draftThroughYear']}.")
    print(f"  Team seasons: {summary['teamsArchived']}")
    print(f"  Career milestones: {summary['milestonesArchived']}")
    print(f"  Career story events: {summary['storyEventsArchived']}")
    print(f"  Draft classes: {summary['draftClassesArchived']} ({summary['draftPicksArchived']} picks)")
    print(f"  Team record rows: {summary['teamRecordsArchived']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive league history, records, milestones, draft classes, and career stories.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    parser.add_argument("--season", type=int, help="Season to archive. Defaults to current season setting.")
    parser.add_argument("--draft-through-year", type=int, help="Archive completed draft classes up to this draft year.")
    parser.add_argument("--force", action="store_true", help="Replace generated rows for the selected season before archiving.")
    parser.add_argument("--apply", action="store_true", help="Persist changes. Without this, the archive is rolled back.")
    args = parser.parse_args()

    with connect(args.db) as con:
        season = int(args.season or current_season(con))
        summary = archive_history(
            con,
            season=season,
            draft_through_year=args.draft_through_year,
            force=args.force,
        )
        if args.apply:
            con.commit()
        else:
            con.rollback()
    print_summary(summary)
    if not args.apply:
        print("Dry run only. Add --apply to save history archive rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
