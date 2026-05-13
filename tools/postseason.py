#!/usr/bin/env python3
"""Postseason and draft-order tools for NFL GM Sim.

This is the first playable postseason path. It uses the current 14-team NFL
format: four division winners and three wild cards per conference, one bye per
conference, reseeding after each round, and a neutral Super Bowl.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import match_engine  # noqa: E402


ROUND_INFO = {
    "WC": ("Wild Card", 19),
    "DIV": ("Divisional", 20),
    "CONF": ("Conference Championship", 21),
    "SB": ("Super Bowl", 22),
}

ROUND_EVENT = {
    "WC": "WILD_CARD_WEEKEND",
    "DIV": "DIVISIONAL_PLAYOFFS",
    "CONF": "CONFERENCE_CHAMPIONSHIPS",
    "SB": "SUPER_BOWL",
}

DRAFT_BUCKETS = {
    "NON_PLAYOFF": 0,
    "WC": 1,
    "DIV": 2,
    "CONF": 3,
    "SB_LOSER": 4,
    "SB_WINNER": 5,
}


@dataclass(frozen=True)
class TeamStanding:
    team_id: int
    abbreviation: str
    conference: str
    division: str
    wins: int
    losses: int
    ties: int
    points_for: int
    points_against: int
    point_diff: int
    conference_wins: int
    conference_losses: int
    conference_ties: int
    division_wins: int
    division_losses: int
    division_ties: int

    @property
    def games(self) -> int:
        return self.wins + self.losses + self.ties

    @property
    def win_pct(self) -> float:
        return pct(self.wins, self.losses, self.ties)

    @property
    def conference_pct(self) -> float:
        return pct(self.conference_wins, self.conference_losses, self.conference_ties)

    @property
    def division_pct(self) -> float:
        return pct(self.division_wins, self.division_losses, self.division_ties)


@dataclass(frozen=True)
class PlayoffTeam:
    standing: TeamStanding
    seed: int
    is_division_winner: bool


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def pct(wins: int, losses: int, ties: int) -> float:
    games = wins + losses + ties
    if games <= 0:
        return 0.0
    return (wins + ties * 0.5) / games


def record_sort_key(team: TeamStanding, *, division_context: bool = False) -> tuple:
    return (
        -team.win_pct,
        -(team.division_pct if division_context else team.conference_pct),
        -team.conference_pct,
        -team.point_diff,
        -team.points_for,
        team.abbreviation,
    )


def draft_sort_key(team: TeamStanding) -> tuple:
    return (
        team.win_pct,
        team.abbreviation,
    )


def draft_sort_key_with_sos(strength_of_schedule: dict[int, float]):
    def key(team: TeamStanding) -> tuple:
        return (
            team.win_pct,
            strength_of_schedule.get(team.team_id, 1.0),
            team.conference_pct,
            team.division_pct,
            team.point_diff,
            team.points_for,
            team.abbreviation,
        )

    return key


def table_columns(con: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row["name"]) for row in con.execute(f"PRAGMA table_info({table_name})")}


def ensure_column(con: sqlite3.Connection, table_name: str, column_sql: str) -> None:
    column_name = column_sql.split()[0]
    if column_name not in table_columns(con, table_name):
        con.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def ensure_schema(con: sqlite3.Connection) -> None:
    match_engine.ensure_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS playoff_seedings (
            season INTEGER NOT NULL,
            conference TEXT NOT NULL,
            seed INTEGER NOT NULL,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            division TEXT NOT NULL,
            is_division_winner INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            ties INTEGER NOT NULL,
            win_pct REAL NOT NULL,
            conference_pct REAL NOT NULL,
            division_pct REAL NOT NULL,
            point_diff INTEGER NOT NULL,
            tiebreaker_note TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(season, conference, seed),
            UNIQUE(season, team_id)
        );

        CREATE TABLE IF NOT EXISTS playoff_games (
            season INTEGER NOT NULL,
            round_code TEXT NOT NULL,
            round_name TEXT NOT NULL,
            game_number INTEGER NOT NULL,
            conference TEXT,
            high_seed INTEGER,
            low_seed INTEGER,
            home_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            away_team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            schedule_game_id INTEGER REFERENCES season_games(game_id) ON DELETE SET NULL,
            winner_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            loser_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(season, round_code, game_number)
        );

        CREATE TABLE IF NOT EXISTS draft_order_slots (
            draft_year INTEGER NOT NULL,
            source_season INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            team_id INTEGER NOT NULL REFERENCES teams(team_id) ON DELETE CASCADE,
            order_bucket TEXT NOT NULL,
            eliminated_round TEXT,
            playoff_seed INTEGER,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            ties INTEGER NOT NULL,
            win_pct REAL NOT NULL,
            strength_of_schedule REAL,
            point_diff INTEGER NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY(draft_year, slot),
            UNIQUE(draft_year, team_id)
        );

        DROP VIEW IF EXISTS playoff_seedings_view;
        CREATE VIEW playoff_seedings_view AS
        SELECT
            ps.*,
            t.abbreviation,
            t.city,
            t.nickname
        FROM playoff_seedings ps
        JOIN teams t ON t.team_id = ps.team_id;

        DROP VIEW IF EXISTS playoff_games_view;
        CREATE VIEW playoff_games_view AS
        SELECT
            pg.*,
            home.abbreviation AS home_team,
            away.abbreviation AS away_team,
            winner.abbreviation AS winner_team,
            loser.abbreviation AS loser_team,
            sg.played,
            sg.home_score,
            sg.away_score,
            sg.game_date
        FROM playoff_games pg
        JOIN teams home ON home.team_id = pg.home_team_id
        JOIN teams away ON away.team_id = pg.away_team_id
        LEFT JOIN teams winner ON winner.team_id = pg.winner_team_id
        LEFT JOIN teams loser ON loser.team_id = pg.loser_team_id
        LEFT JOIN season_games sg ON sg.game_id = pg.schedule_game_id;

        DROP VIEW IF EXISTS draft_order_view;
        CREATE VIEW draft_order_view AS
        SELECT
            dos.*,
            original.abbreviation AS original_team,
            owner.abbreviation AS current_team,
            dp.pick_id,
            dp.round,
            dp.pick_number,
            dp.pick_in_round,
            dp.is_traded,
            dp.trade_note,
            dp.is_used
        FROM draft_order_slots dos
        LEFT JOIN draft_picks dp
          ON dp.draft_year = dos.draft_year
         AND dp.original_team_id = dos.team_id
         AND dp.round = 1
        LEFT JOIN teams original ON original.team_id = dos.team_id
        LEFT JOIN teams owner ON owner.team_id = dp.current_team_id;
        """
    )
    ensure_column(con, "draft_order_slots", "strength_of_schedule REAL")


def standings(con: sqlite3.Connection, season: int) -> list[TeamStanding]:
    ensure_schema(con)
    rows = con.execute(
        """
        SELECT *
        FROM season_standings_view
        WHERE season = ?
        """,
        (season,),
    ).fetchall()
    return [
        TeamStanding(
            team_id=int(row["team_id"]),
            abbreviation=row["abbreviation"],
            conference=row["conference"],
            division=row["division"],
            wins=int(row["wins"] or 0),
            losses=int(row["losses"] or 0),
            ties=int(row["ties"] or 0),
            points_for=int(row["points_for"] or 0),
            points_against=int(row["points_against"] or 0),
            point_diff=int(row["point_diff"] or 0),
            conference_wins=int(row["conference_wins"] or 0),
            conference_losses=int(row["conference_losses"] or 0),
            conference_ties=int(row["conference_ties"] or 0),
            division_wins=int(row["division_wins"] or 0),
            division_losses=int(row["division_losses"] or 0),
            division_ties=int(row["division_ties"] or 0),
        )
        for row in rows
    ]


def require_regular_season_complete(con: sqlite3.Connection, season: int) -> None:
    row = con.execute(
        """
        SELECT COUNT(*) AS games,
               SUM(CASE WHEN played = 1 THEN 1 ELSE 0 END) AS played
        FROM season_games
        WHERE season = ? AND game_type = 'REG'
        """,
        (season,),
    ).fetchone()
    games = int(row["games"] or 0)
    played = int(row["played"] or 0)
    if games != 272 or played != 272:
        raise ValueError(f"{season} regular season is not complete: {played}/{games} games played.")
    match_engine.rebuild_season_history(con, season)


def build_playoff_seeds(con: sqlite3.Connection, season: int) -> dict[str, list[PlayoffTeam]]:
    teams = standings(con, season)
    if len(teams) != 32 or any(team.games != 17 for team in teams):
        raise ValueError(f"{season} standings are incomplete. Sim the regular season first.")

    result: dict[str, list[PlayoffTeam]] = {}
    for conference in ("AFC", "NFC"):
        conference_teams = [team for team in teams if team.conference == conference]
        division_winners = []
        for division in sorted({team.division for team in conference_teams}):
            division_teams = [team for team in conference_teams if team.division == division]
            division_winners.append(sorted(division_teams, key=lambda team: record_sort_key(team, division_context=True))[0])

        winner_ids = {team.team_id for team in division_winners}
        ordered_winners = sorted(division_winners, key=record_sort_key)
        wild_cards = sorted(
            [team for team in conference_teams if team.team_id not in winner_ids],
            key=record_sort_key,
        )[:3]

        playoff_teams = []
        for seed, team in enumerate([*ordered_winners, *wild_cards], start=1):
            playoff_teams.append(
                PlayoffTeam(
                    standing=team,
                    seed=seed,
                    is_division_winner=team.team_id in winner_ids,
                )
            )
        result[conference] = playoff_teams
    return result


def store_playoff_seeds(con: sqlite3.Connection, season: int, seeds: dict[str, list[PlayoffTeam]]) -> None:
    con.execute("DELETE FROM playoff_seedings WHERE season = ?", (season,))
    for conference, teams in seeds.items():
        for playoff_team in teams:
            team = playoff_team.standing
            con.execute(
                """
                INSERT INTO playoff_seedings (
                    season, conference, seed, team_id, division, is_division_winner,
                    wins, losses, ties, win_pct, conference_pct, division_pct,
                    point_diff, tiebreaker_note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    season,
                    conference,
                    playoff_team.seed,
                    team.team_id,
                    team.division,
                    1 if playoff_team.is_division_winner else 0,
                    team.wins,
                    team.losses,
                    team.ties,
                    team.win_pct,
                    team.conference_pct,
                    team.division_pct,
                    team.point_diff,
                    "Basic tiebreakers: record, division/conference record, point differential, points for.",
                ),
            )


def playoff_seeds(con: sqlite3.Connection, season: int) -> dict[str, dict[int, PlayoffTeam]]:
    rows = con.execute(
        """
        SELECT ps.*, t.abbreviation, t.conference, t.division AS team_division
        FROM playoff_seedings ps
        JOIN teams t ON t.team_id = ps.team_id
        WHERE ps.season = ?
        ORDER BY ps.conference, ps.seed
        """,
        (season,),
    ).fetchall()
    if len(rows) != 14:
        seeds = build_playoff_seeds(con, season)
        store_playoff_seeds(con, season, seeds)
        return {conf: {team.seed: team for team in teams} for conf, teams in seeds.items()}
    result: dict[str, dict[int, PlayoffTeam]] = {"AFC": {}, "NFC": {}}
    for row in rows:
        standing = TeamStanding(
            team_id=int(row["team_id"]),
            abbreviation=row["abbreviation"],
            conference=row["conference"],
            division=row["division"],
            wins=int(row["wins"]),
            losses=int(row["losses"]),
            ties=int(row["ties"]),
            points_for=0,
            points_against=0,
            point_diff=int(row["point_diff"]),
            conference_wins=0,
            conference_losses=0,
            conference_ties=0,
            division_wins=0,
            division_losses=0,
            division_ties=0,
        )
        result[row["conference"]][int(row["seed"])] = PlayoffTeam(
            standing=standing,
            seed=int(row["seed"]),
            is_division_winner=bool(row["is_division_winner"]),
        )
    return result


def postseason_date(con: sqlite3.Connection, season: int, round_code: str) -> str:
    event_code = ROUND_EVENT[round_code]
    try:
        row = con.execute(
            """
            SELECT event_start_date
            FROM league_calendar_events
            WHERE league_year = ? AND event_code = ?
            """,
            (season, event_code),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row and row["event_start_date"]:
        return row["event_start_date"]

    week18 = con.execute(
        """
        SELECT MAX(game_date)
        FROM season_games
        WHERE season = ? AND game_type = 'REG' AND week = 18
        """,
        (season,),
    ).fetchone()[0]
    base = date.fromisoformat(week18) if week18 else date(season + 1, 1, 10)
    offsets = {"WC": 6, "DIV": 13, "CONF": 21, "SB": 35}
    return (base + timedelta(days=offsets[round_code])).isoformat()


def team_abbr(con: sqlite3.Connection, team_id: int) -> str:
    row = con.execute("SELECT abbreviation FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    return row["abbreviation"] if row else str(team_id)


def insert_playoff_game(
    con: sqlite3.Connection,
    *,
    season: int,
    round_code: str,
    game_number: int,
    conference: str | None,
    high_seed: int | None,
    low_seed: int | None,
    home_team_id: int,
    away_team_id: int,
    neutral_site: bool = False,
) -> int:
    round_name, week = ROUND_INFO[round_code]
    game_date = postseason_date(con, season, round_code)
    cur = con.execute(
        """
        INSERT INTO season_games (
            season, week, game_type, week_game_number,
            away_team_id, home_team_id, game_date, game_time_et,
            neutral_site, site_label, schedule_status, matchup_bucket, notes
        )
        VALUES (?, ?, 'POST', ?, ?, ?, ?, ?, ?, ?, 'playoff_projected', 'POSTSEASON', ?)
        """,
        (
            season,
            week,
            game_number,
            away_team_id,
            home_team_id,
            game_date,
            "1:00 PM" if round_code != "SB" else "6:30 PM",
            1 if neutral_site else 0,
            "Super Bowl" if neutral_site else None,
            round_name,
        ),
    )
    schedule_game_id = int(cur.lastrowid)
    con.execute(
        """
        INSERT INTO playoff_games (
            season, round_code, round_name, game_number, conference,
            high_seed, low_seed, home_team_id, away_team_id, schedule_game_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            season,
            round_code,
            round_name,
            game_number,
            conference,
            high_seed,
            low_seed,
            home_team_id,
            away_team_id,
            schedule_game_id,
        ),
    )
    return schedule_game_id


def clear_postseason(con: sqlite3.Connection, season: int, *, force: bool = False) -> None:
    played = con.execute(
        """
        SELECT COUNT(*)
        FROM season_games
        WHERE season = ? AND game_type = 'POST' AND played = 1
        """,
        (season,),
    ).fetchone()[0]
    if played and not force:
        raise ValueError(f"{season} postseason already has played games. Use --force to rebuild it.")
    post_ids = [
        int(row["game_id"])
        for row in con.execute(
            "SELECT game_id FROM season_games WHERE season = ? AND game_type = 'POST'",
            (season,),
        ).fetchall()
    ]
    if post_ids:
        placeholders = ",".join("?" for _ in post_ids)
        con.execute(
            f"""
            UPDATE game_sim_runs
            SET status = 'superseded',
                counts_for_stats = 0,
                counts_for_standings = 0
            WHERE schedule_game_id IN ({placeholders})
            """,
            post_ids,
        )
    con.execute("DELETE FROM playoff_games WHERE season = ?", (season,))
    con.execute("DELETE FROM season_games WHERE season = ? AND game_type = 'POST'", (season,))


def simulate_playoff_game(
    con: sqlite3.Connection,
    *,
    schedule_game_id: int,
    seed: int | None,
    apply: bool,
    force: bool,
) -> tuple[int, int]:
    row = con.execute("SELECT * FROM season_games WHERE game_id = ?", (schedule_game_id,)).fetchone()
    result = match_engine.simulate_game(
        con,
        away_team_id=int(row["away_team_id"]),
        home_team_id=int(row["home_team_id"]),
        season=int(row["season"]),
        week=int(row["week"]),
        schedule_game_id=int(row["game_id"]),
        seed=seed,
    )
    if result.away_score == result.home_score:
        result.home_score += 3
    winner_id = int(row["away_team_id"]) if result.away_score > result.home_score else int(row["home_team_id"])
    loser_id = int(row["home_team_id"]) if winner_id == int(row["away_team_id"]) else int(row["away_team_id"])
    print(f"{row['game_id']}: {match_engine.scoreline(result)}")
    if apply:
        match_engine.persist_result(
            con,
            result,
            update_schedule=True,
            force=force,
            notes="Postseason simulation.",
            rebuild_history=False,
        )
        con.execute(
            """
            UPDATE playoff_games
            SET winner_team_id = ?,
                loser_team_id = ?,
                updated_at = datetime('now')
            WHERE schedule_game_id = ?
            """,
            (winner_id, loser_id, schedule_game_id),
        )
    return winner_id, loser_id


def play_round(
    con: sqlite3.Connection,
    *,
    season: int,
    round_code: str,
    games: list[tuple[str | None, int | None, int | None, int, int]],
    seed_base: int | None,
    apply: bool,
    force: bool,
) -> tuple[dict[str, list[int]], list[int]]:
    winners: dict[str, list[int]] = {"AFC": [], "NFC": []}
    losers: list[int] = []
    for idx, (conference, high_seed, low_seed, home_team_id, away_team_id) in enumerate(games, start=1):
        schedule_game_id = insert_playoff_game(
            con,
            season=season,
            round_code=round_code,
            game_number=idx,
            conference=conference,
            high_seed=high_seed,
            low_seed=low_seed,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            neutral_site=round_code == "SB",
        )
        game_seed = seed_base + idx if seed_base is not None else None
        winner, loser = simulate_playoff_game(
            con,
            schedule_game_id=schedule_game_id,
            seed=game_seed,
            apply=apply,
            force=force,
        )
        if conference:
            winners[conference].append(winner)
        else:
            winner_conf = team_conference(con, winner)
            winners[winner_conf].append(winner)
        losers.append(loser)
    return winners, losers


def team_conference(con: sqlite3.Connection, team_id: int) -> str:
    row = con.execute("SELECT conference FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    return row["conference"]


def run_postseason(
    con: sqlite3.Connection,
    *,
    season: int,
    seed: int | None,
    apply: bool,
    force: bool,
) -> int:
    ensure_schema(con)
    require_regular_season_complete(con, season)
    clear_postseason(con, season, force=force)
    seeds = build_playoff_seeds(con, season)
    store_playoff_seeds(con, season, seeds)
    seed_lookup = playoff_seeds(con, season)
    team_seed = {
        playoff_team.standing.team_id: playoff_team.seed
        for conference_teams in seed_lookup.values()
        for playoff_team in conference_teams.values()
    }

    wild_card_games = []
    for conference in ("AFC", "NFC"):
        for high_seed, low_seed in ((2, 7), (3, 6), (4, 5)):
            high = seed_lookup[conference][high_seed].standing.team_id
            low = seed_lookup[conference][low_seed].standing.team_id
            wild_card_games.append((conference, high_seed, low_seed, high, low))
    wc_winners, _wc_losers = play_round(
        con,
        season=season,
        round_code="WC",
        games=wild_card_games,
        seed_base=seed,
        apply=apply,
        force=force,
    )

    divisional_games = []
    for conference in ("AFC", "NFC"):
        alive = [seed_lookup[conference][1].standing.team_id, *wc_winners[conference]]
        ordered = sorted(alive, key=lambda team_id: team_seed[team_id])
        one_seed = ordered[0]
        lowest = ordered[-1]
        middle = ordered[1:-1]
        divisional_games.append((conference, team_seed[one_seed], team_seed[lowest], one_seed, lowest))
        divisional_games.append((conference, team_seed[middle[0]], team_seed[middle[1]], middle[0], middle[1]))
    div_winners, _div_losers = play_round(
        con,
        season=season,
        round_code="DIV",
        games=divisional_games,
        seed_base=seed + 100 if seed is not None else None,
        apply=apply,
        force=force,
    )

    conference_games = []
    for conference in ("AFC", "NFC"):
        ordered = sorted(div_winners[conference], key=lambda team_id: team_seed[team_id])
        conference_games.append((conference, team_seed[ordered[0]], team_seed[ordered[1]], ordered[0], ordered[1]))
    conf_winners, _conf_losers = play_round(
        con,
        season=season,
        round_code="CONF",
        games=conference_games,
        seed_base=seed + 200 if seed is not None else None,
        apply=apply,
        force=force,
    )

    afc_champ = conf_winners["AFC"][0]
    nfc_champ = conf_winners["NFC"][0]
    afc_nominal_home = season % 2 == 0
    home = afc_champ if afc_nominal_home else nfc_champ
    away = nfc_champ if afc_nominal_home else afc_champ
    sb_winners, _sb_losers = play_round(
        con,
        season=season,
        round_code="SB",
        games=[(None, team_seed.get(home), team_seed.get(away), home, away)],
        seed_base=seed + 300 if seed is not None else None,
        apply=apply,
        force=force,
    )
    champion = sb_winners["AFC"][0] if sb_winners["AFC"] else sb_winners["NFC"][0]
    print(f"Champion: {team_abbr(con, champion)}")

    if apply:
        build_draft_order(con, season=season, apply=True)
    return champion


def generate_playoff_tree(
    con: sqlite3.Connection,
    *,
    season: int,
    force: bool,
) -> None:
    ensure_schema(con)
    require_regular_season_complete(con, season)
    clear_postseason(con, season, force=force)
    seeds = build_playoff_seeds(con, season)
    store_playoff_seeds(con, season, seeds)
    seed_lookup = playoff_seeds(con, season)

    game_number = 1
    for conference in ("AFC", "NFC"):
        for high_seed, low_seed in ((2, 7), (3, 6), (4, 5)):
            high = seed_lookup[conference][high_seed].standing.team_id
            low = seed_lookup[conference][low_seed].standing.team_id
            insert_playoff_game(
                con,
                season=season,
                round_code="WC",
                game_number=game_number,
                conference=conference,
                high_seed=high_seed,
                low_seed=low_seed,
                home_team_id=high,
                away_team_id=low,
            )
            game_number += 1


def original_seed_lookup(con: sqlite3.Connection, season: int) -> dict[int, int]:
    rows = con.execute(
        "SELECT team_id, seed FROM playoff_seedings WHERE season = ?",
        (season,),
    ).fetchall()
    if len(rows) != 14:
        seeds = build_playoff_seeds(con, season)
        store_playoff_seeds(con, season, seeds)
        rows = con.execute(
            "SELECT team_id, seed FROM playoff_seedings WHERE season = ?",
            (season,),
        ).fetchall()
    return {int(row["team_id"]): int(row["seed"]) for row in rows}


def completed_round_winners(con: sqlite3.Connection, season: int, round_code: str) -> dict[str, list[int]]:
    rows = con.execute(
        """
        SELECT conference, winner_team_id
        FROM playoff_games
        WHERE season = ?
          AND round_code = ?
          AND winner_team_id IS NOT NULL
        ORDER BY game_number
        """,
        (season, round_code),
    ).fetchall()
    winners: dict[str, list[int]] = {"AFC": [], "NFC": []}
    for row in rows:
        winner = int(row["winner_team_id"])
        conference = row["conference"] or team_conference(con, winner)
        winners[conference].append(winner)
    return winners


def round_has_games(con: sqlite3.Connection, season: int, round_code: str) -> bool:
    row = con.execute(
        "SELECT COUNT(*) AS count FROM playoff_games WHERE season = ? AND round_code = ?",
        (season, round_code),
    ).fetchone()
    return int(row["count"] or 0) > 0


def round_complete(con: sqlite3.Connection, season: int, round_code: str) -> bool:
    row = con.execute(
        """
        SELECT COUNT(*) AS games,
               SUM(CASE WHEN winner_team_id IS NOT NULL THEN 1 ELSE 0 END) AS decided
        FROM playoff_games
        WHERE season = ? AND round_code = ?
        """,
        (season, round_code),
    ).fetchone()
    games = int(row["games"] or 0)
    decided = int(row["decided"] or 0)
    return games > 0 and games == decided


def insert_next_round(con: sqlite3.Connection, *, season: int, completed_round: str) -> str | None:
    team_seed = original_seed_lookup(con, season)

    if completed_round == "WC":
        next_round = "DIV"
        if round_has_games(con, season, next_round):
            return next_round
        seed_lookup = playoff_seeds(con, season)
        wc_winners = completed_round_winners(con, season, "WC")
        game_number = 1
        for conference in ("AFC", "NFC"):
            alive = [seed_lookup[conference][1].standing.team_id, *wc_winners[conference]]
            if len(alive) != 4:
                raise ValueError(f"{conference} Wild Card winners are incomplete.")
            ordered = sorted(alive, key=lambda team_id: team_seed[team_id])
            one_seed = ordered[0]
            lowest = ordered[-1]
            middle = ordered[1:-1]
            for home, away in ((one_seed, lowest), (middle[0], middle[1])):
                insert_playoff_game(
                    con,
                    season=season,
                    round_code=next_round,
                    game_number=game_number,
                    conference=conference,
                    high_seed=team_seed[home],
                    low_seed=team_seed[away],
                    home_team_id=home,
                    away_team_id=away,
                )
                game_number += 1
        return next_round

    if completed_round == "DIV":
        next_round = "CONF"
        if round_has_games(con, season, next_round):
            return next_round
        div_winners = completed_round_winners(con, season, "DIV")
        game_number = 1
        for conference in ("AFC", "NFC"):
            ordered = sorted(div_winners[conference], key=lambda team_id: team_seed[team_id])
            if len(ordered) != 2:
                raise ValueError(f"{conference} Divisional winners are incomplete.")
            insert_playoff_game(
                con,
                season=season,
                round_code=next_round,
                game_number=game_number,
                conference=conference,
                high_seed=team_seed[ordered[0]],
                low_seed=team_seed[ordered[1]],
                home_team_id=ordered[0],
                away_team_id=ordered[1],
            )
            game_number += 1
        return next_round

    if completed_round == "CONF":
        next_round = "SB"
        if round_has_games(con, season, next_round):
            return next_round
        conf_winners = completed_round_winners(con, season, "CONF")
        if len(conf_winners["AFC"]) != 1 or len(conf_winners["NFC"]) != 1:
            raise ValueError("Conference Championship winners are incomplete.")
        afc_champ = conf_winners["AFC"][0]
        nfc_champ = conf_winners["NFC"][0]
        afc_nominal_home = season % 2 == 0
        home = afc_champ if afc_nominal_home else nfc_champ
        away = nfc_champ if afc_nominal_home else afc_champ
        insert_playoff_game(
            con,
            season=season,
            round_code=next_round,
            game_number=1,
            conference=None,
            high_seed=team_seed.get(home),
            low_seed=team_seed.get(away),
            home_team_id=home,
            away_team_id=away,
            neutral_site=True,
        )
        return next_round

    if completed_round == "SB":
        build_draft_order(con, season=season, apply=True)
        return None

    return None


def simulate_next_playoff_round(
    con: sqlite3.Connection,
    *,
    season: int,
    seed: int | None,
    apply: bool,
    force: bool,
) -> str:
    ensure_schema(con)
    require_regular_season_complete(con, season)
    if not round_has_games(con, season, "WC"):
        generate_playoff_tree(con, season=season, force=force)

    for round_code in ("WC", "DIV", "CONF", "SB"):
        if not round_has_games(con, season, round_code):
            previous = {"DIV": "WC", "CONF": "DIV", "SB": "CONF"}.get(round_code)
            if previous and round_complete(con, season, previous):
                insert_next_round(con, season=season, completed_round=previous)
            else:
                continue
        rows = con.execute(
            """
            SELECT schedule_game_id, game_number
            FROM playoff_games
            WHERE season = ?
              AND round_code = ?
              AND winner_team_id IS NULL
            ORDER BY game_number
            """,
            (season, round_code),
        ).fetchall()
        if not rows:
            if round_complete(con, season, round_code):
                insert_next_round(con, season=season, completed_round=round_code)
            continue
        seed_base = None
        if seed is not None:
            seed_base = seed + ({"WC": 0, "DIV": 100, "CONF": 200, "SB": 300}[round_code])
        for row in rows:
            game_seed = seed_base + int(row["game_number"]) if seed_base is not None else None
            simulate_playoff_game(
                con,
                schedule_game_id=int(row["schedule_game_id"]),
                seed=game_seed,
                apply=apply,
                force=force,
            )
        if apply:
            insert_next_round(con, season=season, completed_round=round_code)
        return round_code

    raise ValueError(f"{season} postseason is already complete.")


def elimination_rounds(con: sqlite3.Connection, season: int) -> tuple[dict[int, str], int | None, int | None]:
    rows = con.execute(
        """
        SELECT round_code, winner_team_id, loser_team_id
        FROM playoff_games
        WHERE season = ?
          AND winner_team_id IS NOT NULL
          AND loser_team_id IS NOT NULL
        """,
        (season,),
    ).fetchall()
    eliminated = {int(row["loser_team_id"]): row["round_code"] for row in rows}
    sb = [row for row in rows if row["round_code"] == "SB"]
    champion = int(sb[0]["winner_team_id"]) if sb else None
    runner_up = int(sb[0]["loser_team_id"]) if sb else None
    if champion:
        eliminated[champion] = "SB_WINNER"
    return eliminated, champion, runner_up


def opponent_strength_of_schedule(con: sqlite3.Connection, season: int, teams: dict[int, TeamStanding]) -> dict[int, float]:
    opponent_totals: dict[int, list[int]] = {team_id: [0, 0, 0] for team_id in teams}
    rows = con.execute(
        """
        SELECT away_team_id, home_team_id
        FROM season_games
        WHERE season = ?
          AND game_type = 'REG'
          AND COALESCE(played, 0) = 1
        """,
        (season,),
    ).fetchall()
    for row in rows:
        away_id = int(row["away_team_id"])
        home_id = int(row["home_team_id"])
        for team_id, opponent_id in ((away_id, home_id), (home_id, away_id)):
            opponent = teams.get(opponent_id)
            if not opponent:
                continue
            totals = opponent_totals.setdefault(team_id, [0, 0, 0])
            totals[0] += opponent.wins
            totals[1] += opponent.losses
            totals[2] += opponent.ties
    result: dict[int, float] = {}
    for team_id, (wins, losses, ties) in opponent_totals.items():
        games = wins + losses + ties
        result[team_id] = ((wins + (ties * 0.5)) / games) if games else 1.0
    return result


def build_draft_order(con: sqlite3.Connection, *, season: int, apply: bool) -> list[tuple[int, TeamStanding, str]]:
    ensure_schema(con)
    teams = {team.team_id: team for team in standings(con, season)}
    strength_of_schedule = opponent_strength_of_schedule(con, season, teams)
    draft_key = draft_sort_key_with_sos(strength_of_schedule)
    seed_rows = con.execute(
        "SELECT team_id, seed FROM playoff_seedings WHERE season = ?",
        (season,),
    ).fetchall()
    playoff_seed = {int(row["team_id"]): int(row["seed"]) for row in seed_rows}
    playoff_team_ids = set(playoff_seed)
    eliminated, champion, _runner_up = elimination_rounds(con, season)

    ordered: list[tuple[int, TeamStanding, str]] = []
    slot = 1
    non_playoff = sorted(
        [team for team in teams.values() if team.team_id not in playoff_team_ids],
        key=draft_key,
    )
    for team in non_playoff:
        ordered.append((slot, team, "NON_PLAYOFF"))
        slot += 1

    for bucket in ("WC", "DIV", "CONF"):
        bucket_teams = sorted(
            [teams[team_id] for team_id, round_code in eliminated.items() if round_code == bucket],
            key=draft_key,
        )
        for team in bucket_teams:
            ordered.append((slot, team, bucket))
            slot += 1

    sb_loser = [
        teams[team_id]
        for team_id, round_code in eliminated.items()
        if round_code == "SB"
    ]
    for team in sorted(sb_loser, key=draft_key):
        ordered.append((slot, team, "SB_LOSER"))
        slot += 1

    if champion:
        ordered.append((slot, teams[champion], "SB_WINNER"))

    if len(ordered) != 32:
        raise ValueError(f"Draft order needs 32 teams, found {len(ordered)}. Sim postseason first.")

    draft_year = season + 1
    if apply:
        con.execute("DELETE FROM draft_order_slots WHERE draft_year = ?", (draft_year,))
        for draft_slot, team, bucket in ordered:
            round_code = None if bucket == "NON_PLAYOFF" else bucket
            notes = "Draft order from completed regular season and postseason."
            con.execute(
                """
                INSERT INTO draft_order_slots (
                    draft_year, source_season, slot, team_id, order_bucket,
                    eliminated_round, playoff_seed, wins, losses, ties,
                    win_pct, strength_of_schedule, point_diff, notes, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    draft_year,
                    season,
                    draft_slot,
                    team.team_id,
                    bucket,
                    round_code,
                    playoff_seed.get(team.team_id),
                    team.wins,
                    team.losses,
                    team.ties,
                    team.win_pct,
                    strength_of_schedule.get(team.team_id),
                    team.point_diff,
                    notes,
                ),
            )
            con.execute(
                """
                UPDATE draft_picks
                SET pick_in_round = ?,
                    pick_number = ((round - 1) * 32) + ?
                WHERE draft_year = ?
                  AND original_team_id = ?
                  AND COALESCE(is_comp_pick, 0) = 0
                """,
                (draft_slot, draft_slot, draft_year, team.team_id),
            )
    return ordered


def print_seeds(con: sqlite3.Connection, season: int) -> None:
    rows = con.execute(
        """
        SELECT *
        FROM playoff_seedings_view
        WHERE season = ?
        ORDER BY conference, seed
        """,
        (season,),
    ).fetchall()
    if not rows:
        print(f"No playoff seedings for {season}.")
        return
    current = None
    for row in rows:
        if row["conference"] != current:
            current = row["conference"]
            print(current)
        marker = "DIV" if row["is_division_winner"] else "WC"
        print(
            f"  {row['seed']}. {row['abbreviation']} {row['wins']}-{row['losses']}-{row['ties']} "
            f"{marker} diff {row['point_diff']:+}"
        )


def print_bracket(con: sqlite3.Connection, season: int) -> None:
    rows = con.execute(
        """
        SELECT *
        FROM playoff_games_view
        WHERE season = ?
        ORDER BY
            CASE round_code WHEN 'WC' THEN 1 WHEN 'DIV' THEN 2 WHEN 'CONF' THEN 3 ELSE 4 END,
            game_number
        """,
        (season,),
    ).fetchall()
    if not rows:
        print(f"No playoff bracket for {season}.")
        return
    for row in rows:
        score = ""
        if row["played"]:
            score = f"  {row['away_team']} {row['away_score']} - {row['home_team']} {row['home_score']}"
        winner = f" winner {row['winner_team']}" if row["winner_team"] else ""
        print(
            f"{row['round_code']} {row['game_number']}: "
            f"{row['away_team']} at {row['home_team']} {row['game_date']}{score}{winner}"
        )


def print_draft_order(order: list[tuple[int, TeamStanding, str]]) -> None:
    for slot, team, bucket in order:
        print(f"{slot:>2}. {team.abbreviation:<3} {team.wins}-{team.losses}-{team.ties} {bucket} diff {team.point_diff:+}")


def action_setup(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        ensure_schema(con)
        con.commit()
        print("Postseason schema ready.")
    finally:
        con.close()


def action_seed(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        require_regular_season_complete(con, args.season)
        seeds = build_playoff_seeds(con, args.season)
        store_playoff_seeds(con, args.season, seeds)
        print_seeds(con, args.season)
        if args.apply:
            con.commit()
            print(f"Saved {args.season} playoff seedings.")
        else:
            con.rollback()
            print("Dry run only. Add --apply to save seedings.")
    finally:
        con.close()


def action_tree(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        generate_playoff_tree(
            con,
            season=args.season,
            force=args.force,
        )
        print_seeds(con, args.season)
        print("")
        print_bracket(con, args.season)
        if args.apply:
            con.commit()
            print(f"Saved {args.season} playoff tree.")
        else:
            con.rollback()
            print("Dry run only. Add --apply to save the playoff tree.")
    finally:
        con.close()


def action_run(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        champion = run_postseason(
            con,
            season=args.season,
            seed=args.seed,
            apply=args.apply,
            force=args.force,
        )
        if args.apply:
            con.commit()
            print(f"Saved {args.season} postseason. Champion: {team_abbr(con, champion)}")
        else:
            con.rollback()
            print("Dry run only. Add --apply to save postseason results.")
    finally:
        con.close()


def action_round(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        round_code = simulate_next_playoff_round(
            con,
            season=args.season,
            seed=args.seed,
            apply=args.apply,
            force=args.force,
        )
        print("")
        print_bracket(con, args.season)
        if args.apply:
            con.commit()
            print(f"Saved {args.season} {ROUND_INFO[round_code][0]} playoff round.")
        else:
            con.rollback()
            print("Dry run only. Add --apply to save playoff round results.")
    finally:
        con.close()


def action_draft_order(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        order = build_draft_order(con, season=args.season, apply=args.apply)
        if args.apply:
            con.commit()
            print(f"Saved {args.season + 1} draft order and updated draft pick slots.")
        else:
            con.rollback()
            print("Dry run only. Add --apply to save draft order.")
        print_draft_order(order)
    finally:
        con.close()


def action_summary(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        ensure_schema(con)
        print(f"{args.season} Playoff Seeds")
        print_seeds(con, args.season)
        print("")
        print(f"{args.season} Bracket")
        print_bracket(con, args.season)
        print("")
        print(f"{args.season + 1} Draft Order")
        rows = con.execute(
            """
            SELECT dos.slot, t.abbreviation, dos.wins, dos.losses, dos.ties,
                   dos.order_bucket, dos.point_diff
            FROM draft_order_slots dos
            JOIN teams t ON t.team_id = dos.team_id
            WHERE dos.draft_year = ?
            ORDER BY dos.slot
            """,
            (args.season + 1,),
        ).fetchall()
        if not rows:
            print("No saved draft order.")
        for row in rows:
            print(
                f"{row['slot']:>2}. {row['abbreviation']:<3} "
                f"{row['wins']}-{row['losses']}-{row['ties']} "
                f"{row['order_bucket']} diff {row['point_diff']:+}"
            )
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed and simulate NFL GM postseason.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Create postseason tables/views.")
    setup_parser.set_defaults(func=action_setup)

    seed_parser = subparsers.add_parser("seed", help="Calculate playoff seedings.")
    seed_parser.add_argument("--season", type=int, default=match_engine.DEFAULT_SEASON)
    seed_parser.add_argument("--apply", action="store_true")
    seed_parser.set_defaults(func=action_seed)

    tree_parser = subparsers.add_parser("tree", help="Generate playoff seedings and Wild Card matchups.")
    tree_parser.add_argument("--season", type=int, default=match_engine.DEFAULT_SEASON)
    tree_parser.add_argument("--apply", action="store_true")
    tree_parser.add_argument("--force", action="store_true", help="Replace existing unplayed postseason games.")
    tree_parser.set_defaults(func=action_tree)

    run_parser = subparsers.add_parser("run", help="Simulate full postseason and set draft order.")
    run_parser.add_argument("--season", type=int, default=match_engine.DEFAULT_SEASON)
    run_parser.add_argument("--seed", type=int)
    run_parser.add_argument("--apply", action="store_true")
    run_parser.add_argument("--force", action="store_true", help="Replace existing postseason games.")
    run_parser.set_defaults(func=action_run)

    round_parser = subparsers.add_parser("round", help="Simulate the next unplayed playoff round.")
    round_parser.add_argument("--season", type=int, default=match_engine.DEFAULT_SEASON)
    round_parser.add_argument("--seed", type=int)
    round_parser.add_argument("--apply", action="store_true")
    round_parser.add_argument("--force", action="store_true")
    round_parser.set_defaults(func=action_round)

    draft_parser = subparsers.add_parser("draft-order", help="Build draft order from completed postseason.")
    draft_parser.add_argument("--season", type=int, default=match_engine.DEFAULT_SEASON)
    draft_parser.add_argument("--apply", action="store_true")
    draft_parser.set_defaults(func=action_draft_order)

    summary_parser = subparsers.add_parser("summary", help="Show playoff bracket and draft order.")
    summary_parser.add_argument("--season", type=int, default=match_engine.DEFAULT_SEASON)
    summary_parser.set_defaults(func=action_summary)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
