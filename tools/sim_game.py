#!/usr/bin/env python3
"""Command-line entry point for the NFL GM Sim match engine."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import match_engine  # noqa: E402
from engine import injury_model  # noqa: E402
import injury_notifications  # noqa: E402
import cpu_depth_chart  # noqa: E402
import preseason_processor  # noqa: E402
import sim_control  # noqa: E402
import weekly_processor  # noqa: E402


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def team_id(con: sqlite3.Connection, abbreviation: str) -> int:
    row = con.execute(
        "SELECT team_id FROM teams WHERE abbreviation = ?",
        (abbreviation.upper(),),
    ).fetchone()
    if not row:
        raise ValueError(f"Team not found: {abbreviation}")
    return int(row["team_id"])


def schedule_game(con: sqlite3.Connection, game_id: int) -> sqlite3.Row:
    row = con.execute(
        """
        SELECT sg.*, away.abbreviation AS away_team, home.abbreviation AS home_team
        FROM season_games sg
        JOIN teams away ON away.team_id = sg.away_team_id
        JOIN teams home ON home.team_id = sg.home_team_id
        WHERE sg.game_id = ?
        """,
        (game_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Schedule game not found: {game_id}")
    return row


def player_lookup(result: match_engine.GameResult) -> dict[int, tuple[str, str, int]]:
    lookup = {}
    for team in [result.away, result.home]:
        for player in team.roster:
            lookup[player.player_id] = (player.name, player.position, team.team_id)
    return lookup


def print_player_section(label: str, rows: list[str]) -> None:
    if not rows:
        return
    print(label)
    for row in rows:
        print(row)


def print_box_score(result: match_engine.GameResult) -> None:
    lookup = player_lookup(result)
    print("")
    print("Box Score")
    print("Team")
    for team in [result.away, result.home]:
        stats = result.team_stats[team.team_id]
        print(
            f"  {team.abbreviation}: "
            f"{int(stats.get('total_yards', 0))} yds, "
            f"{int(stats.get('first_downs', 0))} 1st downs, "
            f"{int(stats.get('turnovers', 0))} TO, "
            f"{int(stats.get('penalty_yards', 0))} penalty yds"
        )

    for team in [result.away, result.home]:
        print("")
        print(team.abbreviation)
        passing = []
        rushing = []
        receiving = []
        kicking_punting = []
        defense = []

        for player_id, stats in result.player_stats.items():
            name, _position, team_id = lookup.get(player_id, (f"Player {player_id}", "", 0))
            if team_id != team.team_id:
                continue
            if stats.get("pass_attempts", 0):
                interceptions_thrown = stats.get("interceptions_thrown", stats.get("interceptions", 0))
                passing.append(
                    f"  {name}: "
                    f"{int(stats.get('pass_completions', 0))}/{int(stats.get('pass_attempts', 0))}, "
                    f"{int(stats.get('pass_yards', 0))} yds, "
                    f"{int(stats.get('pass_tds', 0))} TD, "
                    f"{int(interceptions_thrown)} INT, "
                    f"{int(stats.get('sacks_taken', 0))} sacks"
                )
            if stats.get("rush_attempts", 0):
                attempts = int(stats.get("rush_attempts", 0))
                yards = int(stats.get("rush_yards", 0))
                rushing.append(
                    (
                        yards,
                        f"  {name}: {attempts} car, {yards} yds, "
                        f"{(yards / attempts if attempts else 0):.1f} avg, "
                        f"{int(stats.get('rush_tds', 0))} TD",
                    )
                )
            if stats.get("targets", 0) or stats.get("receptions", 0):
                receptions = int(stats.get("receptions", 0))
                targets = int(stats.get("targets", 0))
                yards = int(stats.get("receiving_yards", 0))
                receiving.append(
                    (
                        yards,
                        f"  {name}: {receptions}/{targets}, {yards} yds, "
                        f"{(yards / receptions if receptions else 0):.1f} avg, "
                        f"{int(stats.get('receiving_tds', 0))} TD",
                    )
                )
            if stats.get("fg_attempts", 0) or stats.get("xp_attempts", 0):
                kicking_punting.append(
                    f"  {name}: FG {int(stats.get('fg_made', 0))}/{int(stats.get('fg_attempts', 0))}, "
                    f"XP {int(stats.get('xp_made', 0))}/{int(stats.get('xp_attempts', 0))}, "
                    f"long {int(stats.get('long_fg', 0))}"
                )
            if stats.get("punts", 0):
                punts = int(stats.get("punts", 0))
                yards = int(stats.get("punt_yards", 0))
                kicking_punting.append(
                    f"  {name}: {punts} punts, {yards} yds, {(yards / punts if punts else 0):.1f} avg"
                )
            defense_total = sum(
                int(stats.get(key, 0))
                for key in (
                    "tackles",
                    "solo_tackles",
                    "assisted_tackles",
                    "sacks",
                    "interceptions",
                    "pass_deflections",
                    "forced_fumbles",
                    "fumble_recoveries",
                )
            )
            if defense_total:
                tackles = int(stats.get("tackles", 0))
                solo = int(stats.get("solo_tackles", tackles if not stats.get("assisted_tackles", 0) else 0))
                assisted = int(stats.get("assisted_tackles", 0))
                defense.append(
                    (
                        tackles,
                        name,
                        f"  {name}: {tackles} TKL ({solo} solo, {assisted} ast), "
                        f"{int(stats.get('sacks', 0))} SK, "
                        f"{int(stats.get('interceptions', 0))} INT, "
                        f"{int(stats.get('pass_deflections', 0))} PD, "
                        f"{int(stats.get('forced_fumbles', 0))} FF, "
                        f"{int(stats.get('fumble_recoveries', 0))} FR",
                    )
                )

        print_player_section("Passing", passing)
        print_player_section("Rushing", [line for _yards, line in sorted(rushing, reverse=True)])
        print_player_section("Receiving", [line for _yards, line in sorted(receiving, reverse=True)])
        print_player_section("Kicking/Punting", kicking_punting)
        print_player_section(
            "Defense",
            [line for _tackles, _name, line in sorted(defense, reverse=True)[:10]],
        )


def print_result(result: match_engine.GameResult, *, show_plays: int = 16, box: bool = False) -> None:
    print(f"Final: {match_engine.scoreline(result)}")
    print(f"Season: {result.season}  Week: {result.week or '-'}  Seed: {result.seed}")
    print(f"Engine: {match_engine.ENGINE_VERSION}")
    print("")
    print("Team Stats")
    for team in [result.away, result.home]:
        stats = result.team_stats[team.team_id]
        completions = int(stats.get("pass_completions", 0))
        attempts = int(stats.get("pass_attempts", 0))
        rushes = int(stats.get("rush_attempts", 0))
        print(
            f"  {team.abbreviation}: "
            f"{int(stats.get('total_yards', 0))} yards, "
            f"{completions}/{attempts} passing, "
            f"{int(stats.get('pass_yards', 0))} pass yds, "
            f"{rushes} rushes for {int(stats.get('rush_yards', 0))}, "
            f"{int(stats.get('turnovers', 0))} TO, "
            f"{int(stats.get('first_downs', 0))} 1st downs"
        )

    if box:
        print_box_score(result)

    print("")
    print("Drive Summary")
    for drive in result.drives:
        offense = result.away if drive.offense_team_id == result.away.team_id else result.home
        print(
            f"  {drive.drive_number:>2}. {offense.abbreviation} "
            f"Q{drive.start_quarter} {match_engine.clock_string(drive.start_clock_tenths)} "
            f"at {match_engine.format_yardline(drive.start_yardline)}: "
            f"{drive.result}, {drive.plays} plays"
        )

    if show_plays:
        print("")
        print(f"Last {min(show_plays, len(result.plays))} Plays")
        for play in result.plays[-show_plays:]:
            offense = result.away if play.offense_team_id == result.away.team_id else result.home
            print(
                f"  Q{play.quarter} {match_engine.clock_string(play.clock_tenths)} "
                f"{offense.abbreviation} {play.down}&{play.distance} "
                f"at {match_engine.format_yardline(play.yardline)}: {play.description}"
            )


def action_setup(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        match_engine.ensure_schema(con)
        con.commit()
        print("Match engine schema ready.")
    finally:
        con.close()


def action_matchup(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        result = match_engine.simulate_game(
            con,
            away_team_id=team_id(con, args.away),
            home_team_id=team_id(con, args.home),
            season=args.season,
            week=args.week,
            seed=args.seed,
        )
        print_result(result, show_plays=args.show_plays, box=args.box)
    finally:
        con.close()


def action_game(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        row = schedule_game(con, args.schedule_game_id)
        if int(row["played"] or 0) and not args.force:
            raise ValueError(f"Schedule game {args.schedule_game_id} is already played. Use --force to resim.")
        result = match_engine.simulate_game(
            con,
            away_team_id=int(row["away_team_id"]),
            home_team_id=int(row["home_team_id"]),
            season=int(row["season"]),
            week=int(row["week"]) if row["week"] is not None else None,
            schedule_game_id=int(row["game_id"]),
            seed=args.seed,
        )
        print_result(result, show_plays=args.show_plays, box=args.box)
        if args.apply:
            injury_marker = injury_notifications.max_event_id(con)
            run_id = match_engine.persist_result(
                con,
                result,
                update_schedule=True,
                force=args.force,
                notes=args.notes,
            )
            injury_summary = injury_notifications.create_injury_notifications(
                con,
                min_event_id=injury_marker,
            )
            con.commit()
            print(f"\nSaved sim run {run_id} and marked schedule game {args.schedule_game_id} played.")
            if injury_summary["injury_events"]:
                print(
                    "Injury notifications: "
                    f"{injury_summary['inbox_created']} inbox, "
                    f"{injury_summary['league_news_created']} league news."
                )
        else:
            print("\nDry run only. Add --apply to save this result.")
    finally:
        con.close()


def scheduled_game_rows(
    con: sqlite3.Connection,
    *,
    season: int,
    game_type: str = "REG",
    force: bool = False,
    week: int | None = None,
    start_week: int | None = None,
    end_week: int | None = None,
) -> list[sqlite3.Row]:
    game_type = str(game_type or "REG").upper()
    filters = ["season = ?", "game_type = ?"]
    params: list[object] = [season, game_type]
    if week is not None:
        filters.append("week = ?")
        params.append(week)
    else:
        if start_week is not None:
            filters.append("week >= ?")
            params.append(start_week)
        if end_week is not None:
            filters.append("week <= ?")
            params.append(end_week)
    if not force:
        filters.append("played = 0")
    return con.execute(
        f"""
        SELECT *
        FROM season_games
        WHERE {' AND '.join(filters)}
        ORDER BY week, week_game_number, game_id
        """,
        params,
    ).fetchall()


def simulate_scheduled_rows(
    con: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    seed: int | None = None,
    apply: bool = False,
    force: bool = False,
    notes: str | None = None,
    rebuild_at_end: bool = True,
    cancel_db_path: Path | None = None,
) -> tuple[int, int]:
    saved = 0
    injury_marker = injury_notifications.max_event_id(con) if apply else 0
    for idx, row in enumerate(rows):
        if cancel_db_path is not None:
            sim_control.raise_if_cancelled(
                cancel_db_path,
                f"before {int(row['season'])} Week {int(row['week'])} game {int(row['game_id'])}.",
            )
        game_seed = seed + idx if seed is not None else None
        result = match_engine.simulate_game(
            con,
            away_team_id=int(row["away_team_id"]),
            home_team_id=int(row["home_team_id"]),
            season=int(row["season"]),
            week=int(row["week"]),
            schedule_game_id=int(row["game_id"]),
            seed=game_seed,
        )
        print(f"{row['game_id']}: {match_engine.scoreline(result)}")
        if apply:
            match_engine.persist_result(
                con,
                result,
                update_schedule=True,
                force=force,
                notes=notes,
                rebuild_history=False,
            )
            saved += 1
            con.commit()
    if apply and rebuild_at_end and rows:
        if cancel_db_path is not None:
            sim_control.raise_if_cancelled(cancel_db_path, "before rebuilding season history.")
        match_engine.rebuild_season_history(con, int(rows[0]["season"]))
        con.commit()
    if apply and rows:
        if cancel_db_path is not None:
            sim_control.raise_if_cancelled(cancel_db_path, "before injury notifications.")
        injury_summary = injury_notifications.create_injury_notifications(con, min_event_id=injury_marker)
        if injury_summary["injury_events"]:
            print(
                "Game injury notifications: "
                f"{injury_summary['inbox_created']} inbox, "
                f"{injury_summary['league_news_created']} league news."
            )
    return len(rows), saved


def process_weekly_hooks_for_rows(
    con: sqlite3.Connection,
    rows: list[sqlite3.Row],
    *,
    game_type: str = "REG",
    force: bool = False,
    ai_gm_enabled: bool = True,
    cancel_db_path: Path | None = None,
) -> None:
    weeks = sorted({int(row["week"]) for row in rows})
    if not weeks:
        return
    season = int(rows[0]["season"])
    game_type = str(game_type or rows[0]["game_type"] or "REG").upper()
    if game_type == "PRE":
        game_row = con.execute(
            """
            SELECT setting_value
            FROM game_settings
            WHERE setting_key IN ('active_game_id', 'activeGameId')
            ORDER BY CASE setting_key WHEN 'active_game_id' THEN 0 ELSE 1 END
            LIMIT 1
            """
        ).fetchone()
        game_id = str(game_row["setting_value"]) if game_row and game_row["setting_value"] else "preseason"
        for week in weeks:
            if cancel_db_path is not None:
                sim_control.raise_if_cancelled(cancel_db_path, f"before preseason hooks for {season} Week {week}.")
            completion = con.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN played = 1 THEN 1 ELSE 0 END) AS played
                FROM season_games
                WHERE season = ? AND week = ? AND game_type = 'PRE'
                """,
                (season, week),
            ).fetchone()
            total_games = int(completion["total"] or 0) if completion else 0
            played_games = int(completion["played"] or 0) if completion else 0
            if total_games <= 0 or played_games < total_games:
                print(f"Preseason hooks skipped for {season} Week {week}: {played_games}/{total_games} games complete.")
                continue
            has_calendar_events = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = 'calendar_events'"
            ).fetchone()
            event_row = (
                con.execute(
                    """
                    SELECT event_start_date
                    FROM calendar_events
                    WHERE season = ?
                      AND event_code = ?
                    ORDER BY event_start_date
                    LIMIT 1
                    """,
                    (season, f"PRESEASON_WEEK_{week}"),
                ).fetchone()
                if has_calendar_events
                else None
            )
            event_date = str(event_row["event_start_date"]) if event_row else str(rows[0]["game_date"] or f"{season}-08-01")
            result = preseason_processor.process_preseason_week(
                con,
                game_id=game_id,
                season=season,
                preseason_week=week,
                event_date=event_date,
                seed=f"{game_id}:{season}:preseason-week:{week}",
                emit_messages=True,
                process_market=True,
                simulate_games=False,
            )
            print(preseason_processor.result_summary(result))
            cpu_depth_chart.mark_all_cpu_depth_charts_stale(
                con,
                reason=f"Preseason Week {week} completed.",
            )
        return
    if game_type != "REG":
        print(f"No weekly hooks configured for game type {game_type}.")
        return
    for week in weeks:
        if cancel_db_path is not None:
            sim_control.raise_if_cancelled(cancel_db_path, f"before weekly hooks for {season} Week {week}.")
        completion = con.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN played = 1 THEN 1 ELSE 0 END) AS played
            FROM season_games
            WHERE season = ? AND week = ? AND game_type = 'REG'
            """,
            (season, week),
        ).fetchone()
        total_games = int(completion["total"] or 0) if completion else 0
        played_games = int(completion["played"] or 0) if completion else 0
        if total_games <= 0 or played_games < total_games:
            print(f"Weekly practice injuries skipped for {season} Week {week}: {played_games}/{total_games} games complete.")
        else:
            practice_events = injury_model.create_weekly_practice_injuries(
                con,
                season=season,
                week=week,
                seed=season * 1000 + week * 97,
                apply=True,
            )
            if practice_events:
                expected_games = sum(event.expected_games for event in practice_events)
                print(
                    f"Weekly practice injuries for {season} Week {week}: "
                    f"{len(practice_events)} event(s), {expected_games} expected games missed."
                )
        try:
            result = weekly_processor.process_week(
                con,
                season=season,
                week=week,
                force=force,
                require_complete=True,
                ai_gm_enabled=ai_gm_enabled,
            )
        except ValueError as exc:
            print(f"Weekly hooks skipped for {season} Week {week}: {exc}")
            continue
        injury_summary = injury_notifications.create_injury_notifications(con, season=season, week=week)
        if injury_summary["injury_events"]:
            print(
                f"Week {week} injury notifications: "
                f"{injury_summary['inbox_created']} inbox, "
                f"{injury_summary['league_news_created']} league news."
            )
        weekly_processor.print_weekly_result(result)


def action_week(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        rows = scheduled_game_rows(
            con,
            season=args.season,
            game_type=args.game_type,
            week=args.week,
            force=args.force,
        )
        if args.limit:
            rows = rows[: args.limit]
        if not rows:
            print(f"No unsimmed games found for {args.season} Week {args.week}.")
            return
        if args.apply:
            refresh = cpu_depth_chart.rebuild_dirty_depth_charts(con, season=args.season, apply=True)
            if int(refresh.get("teams", 0) or 0):
                print(f"Depth charts refreshed before Week {args.week}: {refresh['teams']} CPU team(s).")
        _simulated, saved = simulate_scheduled_rows(
            con,
            rows,
            seed=args.seed,
            apply=args.apply,
            force=args.force,
            notes=args.notes,
            cancel_db_path=args.db if args.apply else None,
        )
        if args.apply:
            if args.weekly_hooks:
                process_weekly_hooks_for_rows(
                    con,
                    rows,
                    game_type=args.game_type,
                    force=args.force,
                    ai_gm_enabled=not args.no_ai_gm,
                    cancel_db_path=args.db,
                )
            refresh = cpu_depth_chart.rebuild_dirty_depth_charts(con, season=args.season, apply=True)
            if int(refresh.get("teams", 0) or 0):
                print(f"Depth charts refreshed after Week {args.week} hooks: {refresh['teams']} CPU team(s).")
            con.commit()
            print(f"Saved {saved} game result(s).")
        else:
            print("Dry run only. Add --apply to save these results.")
    except sim_control.SimCancelled as exc:
        if args.apply:
            match_engine.rebuild_season_history(con, args.season)
            injury_summary = injury_notifications.create_injury_notifications(con, season=args.season)
            if injury_summary["injury_events"]:
                print(
                    "Injury notifications checked before pausing: "
                    f"{injury_summary['inbox_created']} inbox, "
                    f"{injury_summary['league_news_created']} league news."
                )
            con.commit()
            sim_control.clear_cancel(args.db)
        print(f"{exc} Saved progress through the last completed safe checkpoint.")
    finally:
        con.close()


def action_season(args: argparse.Namespace) -> None:
    con = connect(args.db)
    try:
        rows = scheduled_game_rows(
            con,
            season=args.season,
            game_type=args.game_type,
            start_week=args.start_week,
            end_week=args.end_week,
            force=args.force,
        )
        if args.limit:
            rows = rows[: args.limit]
        if not rows:
            print(f"No unsimmed games found for {args.season}.")
            return
        simulated = 0
        saved = 0
        rows_by_week: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            rows_by_week.setdefault(int(row["week"]), []).append(row)
        for week in sorted(rows_by_week):
            week_rows = rows_by_week[week]
            if args.apply:
                sim_control.raise_if_cancelled(args.db, f"before simulating {args.season} Week {week}.")
                refresh = cpu_depth_chart.rebuild_dirty_depth_charts(con, season=args.season, apply=True)
                if int(refresh.get("teams", 0) or 0):
                    print(f"Depth charts refreshed before Week {week}: {refresh['teams']} CPU team(s).")
            week_simulated, week_saved = simulate_scheduled_rows(
                con,
                week_rows,
                seed=(args.seed + simulated if args.seed is not None else None),
                apply=args.apply,
                force=args.force,
                notes=args.notes,
                cancel_db_path=args.db if args.apply else None,
            )
            simulated += week_simulated
            saved += week_saved
            if args.apply:
                if args.weekly_hooks:
                    process_weekly_hooks_for_rows(
                        con,
                        week_rows,
                        game_type=args.game_type,
                        force=args.force,
                        ai_gm_enabled=not args.no_ai_gm,
                        cancel_db_path=args.db,
                    )
                refresh = cpu_depth_chart.rebuild_dirty_depth_charts(con, season=args.season, apply=True)
                if int(refresh.get("teams", 0) or 0):
                    print(f"Depth charts refreshed after Week {week} hooks: {refresh['teams']} CPU team(s).")
                con.commit()
                print(f"Week {week} checkpoint saved ({week_saved} game result(s)).")
        if args.apply:
            print(f"Saved {saved} game result(s) for {args.season}.")
        else:
            print(f"Dry run only. Simulated {simulated} game(s). Add --apply to save these results.")
    except sim_control.SimCancelled as exc:
        if args.apply:
            match_engine.rebuild_season_history(con, args.season)
            injury_summary = injury_notifications.create_injury_notifications(con, season=args.season)
            if injury_summary["injury_events"]:
                print(
                    "Injury notifications checked before pausing: "
                    f"{injury_summary['inbox_created']} inbox, "
                    f"{injury_summary['league_news_created']} league news."
                )
            con.commit()
            sim_control.clear_cancel(args.db)
        print(f"{exc} Saved progress through the last completed safe checkpoint.")
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simulate NFL GM games.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Create match engine result tables.")
    setup_parser.set_defaults(func=action_setup)

    matchup_parser = subparsers.add_parser("matchup", help="Dry-run a matchup by team abbreviation.")
    matchup_parser.add_argument("away")
    matchup_parser.add_argument("home")
    matchup_parser.add_argument("--season", type=int, default=match_engine.DEFAULT_SEASON)
    matchup_parser.add_argument("--week", type=int)
    matchup_parser.add_argument("--seed", type=int)
    matchup_parser.add_argument("--show-plays", type=int, default=16)
    matchup_parser.add_argument("--box", action="store_true", help="Include a player box score.")
    matchup_parser.set_defaults(func=action_matchup)

    game_parser = subparsers.add_parser("game", help="Simulate a scheduled game id.")
    game_parser.add_argument("schedule_game_id", type=int)
    game_parser.add_argument("--seed", type=int)
    game_parser.add_argument("--apply", action="store_true", help="Save result and mark game played.")
    game_parser.add_argument("--force", action="store_true", help="Allow resimming an already played game.")
    game_parser.add_argument("--notes")
    game_parser.add_argument("--show-plays", type=int, default=16)
    game_parser.add_argument("--box", action="store_true", help="Include a player box score.")
    game_parser.set_defaults(func=action_game)

    week_parser = subparsers.add_parser("week", help="Simulate every unplayed game in a week.")
    week_parser.add_argument("week", type=int)
    week_parser.add_argument("--season", type=int, default=match_engine.DEFAULT_SEASON)
    week_parser.add_argument("--game-type", choices=["REG", "PRE"], default="REG")
    week_parser.add_argument("--seed", type=int)
    week_parser.add_argument("--limit", type=int)
    week_parser.add_argument("--apply", action="store_true", help="Save results and mark games played.")
    week_parser.add_argument("--force", action="store_true", help="Include already-played games.")
    week_parser.add_argument("--notes")
    week_parser.add_argument(
        "--weekly-hooks",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run weekly event/roster hooks after saving a completed week.",
    )
    week_parser.add_argument("--no-ai-gm", action="store_true", help="Skip heavy AI GM scans during weekly hooks.")
    week_parser.set_defaults(func=action_week)

    season_parser = subparsers.add_parser("season", help="Simulate every unplayed scheduled game.")
    season_parser.add_argument("--season", type=int, default=match_engine.DEFAULT_SEASON)
    season_parser.add_argument("--game-type", choices=["REG", "PRE"], default="REG")
    season_parser.add_argument("--start-week", type=int)
    season_parser.add_argument("--end-week", type=int)
    season_parser.add_argument("--seed", type=int)
    season_parser.add_argument("--limit", type=int)
    season_parser.add_argument("--apply", action="store_true", help="Save results and mark games played.")
    season_parser.add_argument("--force", action="store_true", help="Include already-played games.")
    season_parser.add_argument("--notes")
    season_parser.add_argument(
        "--weekly-hooks",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run weekly event/roster hooks after each completed week.",
    )
    season_parser.add_argument("--no-ai-gm", action="store_true", help="Skip heavy AI GM scans during weekly hooks.")
    season_parser.set_defaults(func=action_season)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
