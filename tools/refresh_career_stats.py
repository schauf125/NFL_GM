#!/usr/bin/env python3
"""Refresh career stat aggregates from player_season_stats.

This creates one career row for every player in the database, including players
with no NFL regular-season stats yet. Those players get clean zero rows so UI
profile pages can render consistently.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS player_career_stats (
            player_id INTEGER PRIMARY KEY,
            seasons_played INTEGER NOT NULL DEFAULT 0,
            first_season INTEGER,
            last_season INTEGER,
            teams_played_for TEXT,
            career_games INTEGER NOT NULL DEFAULT 0,

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

            scrimmage_yards INTEGER NOT NULL DEFAULT 0,
            offensive_tds INTEGER NOT NULL DEFAULT 0,
            total_tds INTEGER NOT NULL DEFAULT 0,
            fumbles_lost INTEGER NOT NULL DEFAULT 0,

            def_tackles_solo INTEGER NOT NULL DEFAULT 0,
            def_tackles_with_assist INTEGER NOT NULL DEFAULT 0,
            def_tackle_assists INTEGER NOT NULL DEFAULT 0,
            def_tackles_combined INTEGER NOT NULL DEFAULT 0,
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

            fantasy_points REAL NOT NULL DEFAULT 0,
            fantasy_points_ppr REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),

            FOREIGN KEY (player_id) REFERENCES players(player_id) ON DELETE CASCADE
        );

        DROP VIEW IF EXISTS player_career_stats_view;
        CREATE VIEW player_career_stats_view AS
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            t.abbreviation AS current_team,
            p.status,
            p.position AS current_position,
            p.age,
            p.overall,
            p.potential,
            c.seasons_played,
            c.first_season,
            c.last_season,
            c.teams_played_for,
            c.career_games,
            c.completions,
            c.passing_attempts,
            c.passing_yards,
            c.passing_tds,
            c.passing_interceptions,
            c.sacks_suffered,
            c.carries,
            c.rushing_yards,
            c.rushing_tds,
            c.receptions,
            c.targets,
            c.receiving_yards,
            c.receiving_tds,
            c.scrimmage_yards,
            c.offensive_tds,
            c.total_tds,
            c.def_tackles_solo,
            c.def_tackles_combined,
            c.def_tackles_for_loss,
            c.def_sacks,
            c.def_qb_hits,
            c.def_interceptions,
            c.def_pass_defended,
            c.punt_returns,
            c.punt_return_yards,
            c.kickoff_returns,
            c.kickoff_return_yards,
            c.fg_made,
            c.fg_att,
            c.fg_long,
            c.fg_pct,
            c.pat_made,
            c.pat_att,
            c.pat_pct,
            c.fantasy_points,
            c.fantasy_points_ppr,
            c.updated_at
        FROM player_career_stats c
        JOIN players p ON p.player_id = c.player_id
        LEFT JOIN teams t ON t.team_id = p.team_id;

        DROP VIEW IF EXISTS free_agent_career_stats_view;
        CREATE VIEW free_agent_career_stats_view AS
        SELECT
            f.player_id,
            f.player_name,
            f.position,
            f.position_group,
            f.age,
            f.overall,
            f.potential,
            f.market_tier,
            f.asking_aav,
            f.minimum_aav,
            f.preferred_years,
            f.motivation,
            f.preferred_teams,
            f.signing_notes,
            c.seasons_played,
            c.first_season,
            c.last_season,
            c.teams_played_for,
            c.career_games,
            c.passing_yards,
            c.passing_tds,
            c.passing_interceptions,
            c.rushing_yards,
            c.rushing_tds,
            c.receiving_yards,
            c.receiving_tds,
            c.scrimmage_yards,
            c.offensive_tds,
            c.def_tackles_combined,
            c.def_sacks,
            c.def_interceptions,
            c.def_pass_defended,
            c.fg_made,
            c.fg_att,
            c.fg_pct,
            c.pat_made,
            c.pat_att,
            c.updated_at
        FROM free_agent_pool_view f
        JOIN player_career_stats c ON c.player_id = f.player_id;
        """
    )


def refresh_career_stats(con: sqlite3.Connection) -> int:
    con.execute("DELETE FROM player_career_stats")
    con.execute(
        """
        INSERT INTO player_career_stats (
            player_id,
            seasons_played,
            first_season,
            last_season,
            teams_played_for,
            career_games,
            completions,
            passing_attempts,
            passing_yards,
            passing_tds,
            passing_interceptions,
            sacks_suffered,
            sack_yards_lost,
            carries,
            rushing_yards,
            rushing_tds,
            rushing_fumbles,
            rushing_fumbles_lost,
            receptions,
            targets,
            receiving_yards,
            receiving_tds,
            receiving_fumbles,
            receiving_fumbles_lost,
            scrimmage_yards,
            offensive_tds,
            total_tds,
            fumbles_lost,
            def_tackles_solo,
            def_tackles_with_assist,
            def_tackle_assists,
            def_tackles_combined,
            def_tackles_for_loss,
            def_fumbles_forced,
            def_sacks,
            def_qb_hits,
            def_interceptions,
            def_interception_yards,
            def_pass_defended,
            def_tds,
            def_safeties,
            punt_returns,
            punt_return_yards,
            kickoff_returns,
            kickoff_return_yards,
            fg_made,
            fg_att,
            fg_long,
            fg_pct,
            pat_made,
            pat_att,
            pat_pct,
            fantasy_points,
            fantasy_points_ppr,
            updated_at
        )
        SELECT
            p.player_id,
            COUNT(DISTINCT CASE WHEN s.games > 0 THEN s.season END) AS seasons_played,
            MIN(CASE WHEN s.games > 0 THEN s.season END) AS first_season,
            MAX(CASE WHEN s.games > 0 THEN s.season END) AS last_season,
            GROUP_CONCAT(DISTINCT s.team) AS teams_played_for,
            COALESCE(SUM(s.games), 0) AS career_games,

            COALESCE(SUM(s.completions), 0),
            COALESCE(SUM(s.passing_attempts), 0),
            COALESCE(SUM(s.passing_yards), 0),
            COALESCE(SUM(s.passing_tds), 0),
            COALESCE(SUM(s.passing_interceptions), 0),
            COALESCE(SUM(s.sacks_suffered), 0),
            COALESCE(SUM(s.sack_yards_lost), 0),

            COALESCE(SUM(s.carries), 0),
            COALESCE(SUM(s.rushing_yards), 0),
            COALESCE(SUM(s.rushing_tds), 0),
            COALESCE(SUM(s.rushing_fumbles), 0),
            COALESCE(SUM(s.rushing_fumbles_lost), 0),

            COALESCE(SUM(s.receptions), 0),
            COALESCE(SUM(s.targets), 0),
            COALESCE(SUM(s.receiving_yards), 0),
            COALESCE(SUM(s.receiving_tds), 0),
            COALESCE(SUM(s.receiving_fumbles), 0),
            COALESCE(SUM(s.receiving_fumbles_lost), 0),

            COALESCE(SUM(s.rushing_yards), 0) + COALESCE(SUM(s.receiving_yards), 0),
            COALESCE(SUM(s.rushing_tds), 0) + COALESCE(SUM(s.receiving_tds), 0),
            COALESCE(SUM(s.passing_tds), 0) + COALESCE(SUM(s.rushing_tds), 0)
                + COALESCE(SUM(s.receiving_tds), 0) + COALESCE(SUM(s.def_tds), 0),
            COALESCE(SUM(s.rushing_fumbles_lost), 0) + COALESCE(SUM(s.receiving_fumbles_lost), 0),

            COALESCE(SUM(s.def_tackles_solo), 0),
            COALESCE(SUM(s.def_tackles_with_assist), 0),
            COALESCE(SUM(s.def_tackle_assists), 0),
            COALESCE(SUM(s.def_tackles_solo), 0) + COALESCE(SUM(s.def_tackles_with_assist), 0),
            COALESCE(SUM(s.def_tackles_for_loss), 0),
            COALESCE(SUM(s.def_fumbles_forced), 0),
            COALESCE(SUM(s.def_sacks), 0),
            COALESCE(SUM(s.def_qb_hits), 0),
            COALESCE(SUM(s.def_interceptions), 0),
            COALESCE(SUM(s.def_interception_yards), 0),
            COALESCE(SUM(s.def_pass_defended), 0),
            COALESCE(SUM(s.def_tds), 0),
            COALESCE(SUM(s.def_safeties), 0),

            COALESCE(SUM(s.punt_returns), 0),
            COALESCE(SUM(s.punt_return_yards), 0),
            COALESCE(SUM(s.kickoff_returns), 0),
            COALESCE(SUM(s.kickoff_return_yards), 0),

            COALESCE(SUM(s.fg_made), 0),
            COALESCE(SUM(s.fg_att), 0),
            COALESCE(MAX(s.fg_long), 0),
            CASE
                WHEN COALESCE(SUM(s.fg_att), 0) > 0
                    THEN ROUND(COALESCE(SUM(s.fg_made), 0) * 100.0 / SUM(s.fg_att), 1)
                ELSE NULL
            END,
            COALESCE(SUM(s.pat_made), 0),
            COALESCE(SUM(s.pat_att), 0),
            CASE
                WHEN COALESCE(SUM(s.pat_att), 0) > 0
                    THEN ROUND(COALESCE(SUM(s.pat_made), 0) * 100.0 / SUM(s.pat_att), 1)
                ELSE NULL
            END,

            COALESCE(SUM(s.fantasy_points), 0),
            COALESCE(SUM(s.fantasy_points_ppr), 0),
            datetime('now')
        FROM players p
        LEFT JOIN player_season_stats s ON s.player_id = p.player_id
        GROUP BY p.player_id
        """
    )
    return int(con.execute("SELECT changes()").fetchone()[0])


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh career stat aggregates.")
    parser.add_argument("--db", default=str(DB_PATH), help=f"SQLite DB path. Default: {DB_PATH}")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    with sqlite3.connect(db_path) as con:
        con.execute("PRAGMA foreign_keys = ON")
        ensure_schema(con)
        rows = refresh_career_stats(con)
        con.commit()

        total = con.execute("SELECT COUNT(*) FROM player_career_stats").fetchone()[0]
        fa_total = con.execute(
            """
            SELECT COUNT(*)
            FROM player_career_stats c
            JOIN players p ON p.player_id = c.player_id
            WHERE p.status = 'Free Agent' AND p.team_id IS NULL
            """
        ).fetchone()[0]
        fa_with_games = con.execute(
            """
            SELECT COUNT(*)
            FROM player_career_stats c
            JOIN players p ON p.player_id = c.player_id
            WHERE p.status = 'Free Agent' AND p.team_id IS NULL
              AND c.career_games > 0
            """
        ).fetchone()[0]

    print(f"Career stat rows refreshed: {rows}")
    print(f"Total career rows: {total}")
    print(f"Free-agent career rows: {fa_total}")
    print(f"Free agents with NFL games: {fa_with_games}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
