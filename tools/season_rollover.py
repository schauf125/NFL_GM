#!/usr/bin/env python3
"""Complete a season and prepare the next playable league year."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import game_flow  # noqa: E402
import daily_processor  # noqa: E402
import league_calendar  # noqa: E402
import league_schedule  # noqa: E402
import player_progression  # noqa: E402
import postseason  # noqa: E402
from engine import match_engine  # noqa: E402


@dataclass(frozen=True)
class GameStatus:
    total: int
    played: int

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.total == self.played


@dataclass(frozen=True)
class PostseasonStatus(GameStatus):
    winner_rows: int
    champion_team_id: int | None
    runner_up_team_id: int | None

    @property
    def complete(self) -> bool:
        return self.total == 13 and self.played == 13 and self.winner_rows == 13 and self.champion_team_id is not None


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def team_abbr(con: sqlite3.Connection, team_id: int | None) -> str:
    if team_id is None:
        return "TBD"
    row = con.execute("SELECT abbreviation FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    return row["abbreviation"] if row else str(team_id)


def ensure_schema(con: sqlite3.Connection) -> None:
    postseason.ensure_schema(con)
    game_flow.ensure_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS season_completions (
            season INTEGER PRIMARY KEY,
            draft_year INTEGER NOT NULL,
            champion_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            runner_up_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            postseason_games_played INTEGER NOT NULL DEFAULT 0,
            draft_order_slots INTEGER NOT NULL DEFAULT 0,
            next_schedule_season INTEGER NOT NULL,
            next_schedule_games INTEGER NOT NULL DEFAULT 0,
            offseason_start_date TEXT,
            active_game_advanced INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            completed_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        DROP VIEW IF EXISTS season_completions_view;
        CREATE VIEW season_completions_view AS
        SELECT
            sc.*,
            champ.abbreviation AS champion_team,
            runner.abbreviation AS runner_up_team
        FROM season_completions sc
        LEFT JOIN teams champ ON champ.team_id = sc.champion_team_id
        LEFT JOIN teams runner ON runner.team_id = sc.runner_up_team_id;
        """
    )


def ensure_calendar_window(con: sqlite3.Connection, season: int) -> None:
    league_calendar.ensure_schema(con)
    for league_year in (season, season + 1):
        row = con.execute(
            "SELECT 1 FROM league_years WHERE league_year = ?",
            (league_year,),
        ).fetchone()
        if row:
            continue
        con.execute(
            """
            INSERT INTO league_years (
                league_year, sim_year_start, sim_year_end, nfl_season,
                status, notes, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                league_year,
                f"{league_year}-06-01",
                f"{league_year + 1}-05-31",
                league_year,
                "official_and_projected" if league_year == 2026 else "projected",
                "Sim league year starts June 1 and ends May 31.",
            ),
        )
        for phase in league_calendar.phases_for_league_year(league_year):
            league_calendar.insert_phase(con, league_year, phase)
        for event in league_calendar.events_for_league_year(league_year):
            league_calendar.insert_event(con, league_year, event)


def regular_season_status(con: sqlite3.Connection, season: int) -> GameStatus:
    row = con.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN played = 1 THEN 1 ELSE 0 END) AS played
        FROM season_games
        WHERE season = ? AND game_type = 'REG'
        """,
        (season,),
    ).fetchone()
    return GameStatus(total=int(row["total"] or 0), played=int(row["played"] or 0))


def postseason_status(con: sqlite3.Connection, season: int) -> PostseasonStatus:
    row = con.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN played = 1 THEN 1 ELSE 0 END) AS played
        FROM season_games
        WHERE season = ? AND game_type = 'POST'
        """,
        (season,),
    ).fetchone()
    winners = con.execute(
        """
        SELECT COUNT(*) AS winners
        FROM playoff_games
        WHERE season = ?
          AND winner_team_id IS NOT NULL
          AND loser_team_id IS NOT NULL
        """,
        (season,),
    ).fetchone()
    _eliminated, champion, runner_up = postseason.elimination_rounds(con, season)
    return PostseasonStatus(
        total=int(row["total"] or 0),
        played=int(row["played"] or 0),
        winner_rows=int(winners["winners"] or 0),
        champion_team_id=champion,
        runner_up_team_id=runner_up,
    )


def draft_order_count(con: sqlite3.Connection, draft_year: int) -> int:
    row = con.execute(
        "SELECT COUNT(*) AS count FROM draft_order_slots WHERE draft_year = ?",
        (draft_year,),
    ).fetchone()
    return int(row["count"] or 0)


def next_schedule_count(con: sqlite3.Connection, season: int) -> int:
    row = con.execute(
        """
        SELECT COUNT(*) AS count
        FROM season_games
        WHERE season = ? AND game_type = 'REG'
        """,
        (season,),
    ).fetchone()
    return int(row["count"] or 0)


def offseason_start_date(con: sqlite3.Connection, season: int) -> str:
    row = con.execute(
        """
        SELECT event_start_date
        FROM league_calendar_events
        WHERE league_year = ?
          AND event_code = 'POST_SUPER_BOWL_OFFSEASON_START'
        """,
        (season,),
    ).fetchone()
    if not row:
        raise ValueError(f"No post-Super Bowl offseason date found for {season}.")
    return row["event_start_date"]


def active_game_advance_needed(con: sqlite3.Connection, target_date: str) -> bool:
    game = game_flow.active_game(con)
    if not game:
        return False
    return date.fromisoformat(game.current_date) < date.fromisoformat(target_date)


def advance_active_game(con: sqlite3.Connection, target_date: str, *, process_days: bool) -> bool:
    game = game_flow.active_game(con)
    if not game:
        print("No active game save row found in this DB; calendar date was not advanced.")
        return False
    if date.fromisoformat(game.current_date) >= date.fromisoformat(target_date):
        print(f"Active game already at {game.current_date}; no date advance needed.")
        return False
    phase, crossed_events = game_flow.update_active_game_date(con, game, target_date)
    print(f"Advanced active game to {target_date} ({phase['phase_name']}).")
    print(f"Calendar events crossed: {len(crossed_events)}")
    if process_days:
        processing_result = daily_processor.process_range(
            con,
            game_id=game.game_id,
            from_date=game.current_date,
            to_date=target_date,
        )
        print(
            "Daily processing: "
            f"{processing_result.days_processed} day(s), "
            f"{processing_result.alerts_created} alert(s)."
        )
    else:
        event_result = daily_processor.process_event_range(
            con,
            game_id=game.game_id,
            from_date=game.current_date,
            to_date=target_date,
            include_start=True,
        )
        daily_processor.print_event_range_result(event_result)
        print("Daily roster processing skipped for fast season rollover.")
    return True


def print_status(con: sqlite3.Connection, season: int) -> None:
    ensure_schema(con)
    reg = regular_season_status(con, season)
    post = postseason_status(con, season)
    draft_year = season + 1
    next_season = season + 1
    draft_slots = draft_order_count(con, draft_year)
    next_games = next_schedule_count(con, next_season)
    completion = con.execute(
        "SELECT * FROM season_completions_view WHERE season = ?",
        (season,),
    ).fetchone()

    print(f"{season} season status")
    print(f"  Regular season: {reg.played}/{reg.total} played")
    print(f"  Postseason:     {post.played}/{post.total} played, {post.winner_rows} result row(s)")
    print(f"  Champion:       {team_abbr(con, post.champion_team_id)}")
    print(f"  Draft order:    {draft_slots}/32 slots for {draft_year}")
    print(f"  Next schedule:  {next_games}/272 REG games for {next_season}")
    if completion:
        print(
            f"  Completion log: yes, completed {completion['completed_at']}, "
            f"offseason date {completion['offseason_start_date'] or 'n/a'}"
        )
    else:
        print("  Completion log: no")


def upsert_completion(
    con: sqlite3.Connection,
    *,
    season: int,
    champion_team_id: int | None,
    runner_up_team_id: int | None,
    postseason_games_played: int,
    draft_order_slots: int,
    next_schedule_games: int,
    offseason_date: str,
    active_game_advanced: bool,
    notes: str | None,
) -> None:
    con.execute(
        """
        INSERT INTO season_completions (
            season, draft_year, champion_team_id, runner_up_team_id,
            postseason_games_played, draft_order_slots, next_schedule_season,
            next_schedule_games, offseason_start_date, active_game_advanced,
            notes, completed_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(season) DO UPDATE SET
            draft_year = excluded.draft_year,
            champion_team_id = excluded.champion_team_id,
            runner_up_team_id = excluded.runner_up_team_id,
            postseason_games_played = excluded.postseason_games_played,
            draft_order_slots = excluded.draft_order_slots,
            next_schedule_season = excluded.next_schedule_season,
            next_schedule_games = excluded.next_schedule_games,
            offseason_start_date = excluded.offseason_start_date,
            active_game_advanced = excluded.active_game_advanced,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            season,
            season + 1,
            champion_team_id,
            runner_up_team_id,
            postseason_games_played,
            draft_order_slots,
            season + 1,
            next_schedule_games,
            offseason_date,
            1 if active_game_advanced else 0,
            notes,
        ),
    )


def progression_seed(season: int, explicit_seed: int | None) -> int:
    if explicit_seed is not None:
        return explicit_seed
    return int(f"{season}{season + 1}")


def apply_offseason_progression(
    con: sqlite3.Connection,
    *,
    season: int,
    seed: int,
    force: bool,
) -> dict[str, object]:
    game = game_flow.active_game(con)
    game_id = game.game_id if game else player_progression.active_game_id(con)
    result = player_progression.apply_progression(
        con,
        game_id=game_id,
        from_season=season,
        to_season=season + 1,
        seed=seed,
        age_players=True,
        roll_modifiers=True,
        notes="Automatic post-playoffs offseason progression.",
        force=force,
        dry_run=False,
    )
    player_progression.print_run_summary(
        result,
        game_id=game_id,
        from_season=season,
        to_season=season + 1,
        seed=seed,
        dry_run=False,
    )
    return result


def action_status(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        print_status(con, args.season)
    finally:
        con.close()


def action_complete(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        ensure_schema(con)
        ensure_calendar_window(con, args.season)
        reg = regular_season_status(con, args.season)
        if reg.total != 272 or reg.played != 272:
            raise ValueError(f"{args.season} regular season is not complete: {reg.played}/{reg.total} played.")

        post = postseason_status(con, args.season)
        draft_year = args.season + 1
        next_season = args.season + 1
        target_offseason_date = offseason_start_date(con, args.season)

        needs_postseason = args.force_postseason or not post.complete
        if not args.apply:
            print("Dry run only. Add --apply to complete the season.")
            print(f"  Regular season complete: {reg.played}/{reg.total}")
            if needs_postseason:
                print("  Would simulate/rebuild postseason.")
            else:
                print(f"  Would keep existing postseason champion: {team_abbr(con, post.champion_team_id)}")
            print(f"  Would write {draft_year} draft order.")
            print(f"  Would rebuild {next_season} schedule from actual {args.season} standings.")
            if not args.no_progression:
                print(f"  Would run offseason progression/regression for {args.season}->{next_season}.")
            if not args.no_advance_date and active_game_advance_needed(con, target_offseason_date):
                print(f"  Would advance active game to {target_offseason_date}.")
            con.rollback()
            return

        if needs_postseason:
            if post.played and not args.force_postseason:
                raise ValueError(
                    f"{args.season} postseason is partial ({post.played}/{post.total}). "
                    "Use --force-postseason to rebuild it."
                )
            postseason.run_postseason(
                con,
                season=args.season,
                seed=args.seed,
                apply=True,
                force=args.force_postseason,
            )
            post = postseason_status(con, args.season)
        else:
            print(f"Keeping existing postseason champion: {team_abbr(con, post.champion_team_id)}")

        postseason.build_draft_order(con, season=args.season, apply=True)
        draft_slots = draft_order_count(con, draft_year)
        if draft_slots != 32:
            raise ValueError(f"Expected 32 draft order slots for {draft_year}, found {draft_slots}.")

        if args.seed_next_schedule:
            league_schedule.seed_formula_schedule(
                con,
                next_season,
                replace=args.replace_next_schedule,
                replace_played=False,
            )
        next_games = next_schedule_count(con, next_season)
        if next_games != 272:
            raise ValueError(f"Expected 272 regular-season games for {next_season}, found {next_games}.")

        validation = league_schedule.validate_database(con, next_season)
        if not validation.ok:
            for error in validation.errors:
                print(f"Schedule error: {error}")
            raise ValueError(f"{next_season} schedule failed validation.")
        for warning in validation.warnings:
            print(f"Schedule warning: {warning}")

        advanced = False
        if args.no_advance_date:
            print("Skipped active game date advance.")
        else:
            advanced = advance_active_game(
                con,
                target_offseason_date,
                process_days=args.process_days_on_advance,
            )

        progression_result = None
        if args.no_progression:
            print("Skipped offseason progression/regression.")
        else:
            print("")
            print("Running offseason progression/regression...")
            progression_result = apply_offseason_progression(
                con,
                season=args.season,
                seed=progression_seed(args.season, args.progression_seed),
                force=args.force_progression,
            )

        post = postseason_status(con, args.season)
        upsert_completion(
            con,
            season=args.season,
            champion_team_id=post.champion_team_id,
            runner_up_team_id=post.runner_up_team_id,
            postseason_games_played=post.played,
            draft_order_slots=draft_slots,
            next_schedule_games=next_games,
            offseason_date=target_offseason_date,
            active_game_advanced=advanced,
            notes=args.notes,
        )
        con.commit()

        print("")
        print(f"{args.season} season completed.")
        print(f"Champion: {team_abbr(con, post.champion_team_id)}")
        print(f"{draft_year} draft order slots: {draft_slots}")
        print(f"{next_season} schedule games: {next_games}")
        print(f"Offseason date: {target_offseason_date}")
        if progression_result:
            print(f"Progression run id: {progression_result['run_id']}")
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Complete a season and prepare the next league year.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show completion readiness for a season.")
    status_parser.add_argument("--season", type=int, default=match_engine.DEFAULT_SEASON)
    status_parser.set_defaults(func=action_status)

    complete_parser = subparsers.add_parser("complete", help="Complete a finished regular season.")
    complete_parser.add_argument("--season", type=int, default=match_engine.DEFAULT_SEASON)
    complete_parser.add_argument("--seed", type=int, help="Base seed for postseason simulations.")
    complete_parser.add_argument("--apply", action="store_true", help="Save postseason, draft order, schedule, and date changes.")
    complete_parser.add_argument("--force-postseason", action="store_true", help="Rebuild postseason even if games exist.")
    complete_parser.add_argument(
        "--seed-next-schedule",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Generate the next regular-season schedule from this season's standings.",
    )
    complete_parser.add_argument(
        "--replace-next-schedule",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Replace an existing unplayed next-season schedule.",
    )
    complete_parser.add_argument("--no-advance-date", action="store_true", help="Do not advance the active save date.")
    complete_parser.add_argument("--no-progression", action="store_true", help="Do not run automatic offseason progression/regression.")
    complete_parser.add_argument("--progression-seed", type=int, help="Seed for automatic offseason progression. Defaults to <season><next season>.")
    complete_parser.add_argument("--force-progression", action="store_true", help="Replace an existing progression run for this season transition.")
    complete_parser.add_argument(
        "--process-days-on-advance",
        action="store_true",
        help="Run every daily processing hook during the date jump. Slow; off by default for season rollover.",
    )
    complete_parser.add_argument("--notes")
    complete_parser.set_defaults(func=action_complete)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
