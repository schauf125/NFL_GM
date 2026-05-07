#!/usr/bin/env python3
"""Manual play-through harness for testing the match engine.

The regular season simulator is built for bulk CPU simulation. This tool is a
playtest cockpit: pick a team, step through its next scheduled game, choose
offensive play families when that team has the ball, and get a log bundle that
is easy to send back with bug reports.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import traceback
from contextlib import redirect_stdout
from dataclasses import asdict
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
LOG_ROOT = ROOT / "logs" / "playtests"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import match_engine  # noqa: E402
import sim_game  # noqa: E402


RUN_CONCEPTS = {
    "1": "inside_zone",
    "2": "outside_zone",
    "3": "power",
    "4": "draw",
}
PASS_CONCEPTS = {
    "1": "quick",
    "2": "short",
    "3": "intermediate",
    "4": "deep",
    "5": "screen",
}


class ManualQuit(Exception):
    pass


class PlaytestLogger:
    def __init__(
        self,
        *,
        log_root: Path,
        team: str,
        opponent: str,
        seed: int,
        session_meta: dict[str, Any],
    ) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = f"{stamp}_{team}_vs_{opponent}_seed_{seed}".replace(" ", "_")
        self.path = log_root / safe
        self.path.mkdir(parents=True, exist_ok=True)
        self.lines_path = self.path / "play_by_play.log"
        self.events_path = self.path / "events.jsonl"
        self.session_path = self.path / "session.json"
        self.result_path = self.path / "result.json"
        self.box_score_path = self.path / "box_score.txt"
        self.crash_path = self.path / "crash_report.txt"
        self.issue_path = self.path / "ISSUE_TEMPLATE.md"
        self._line_file = self.lines_path.open("w", encoding="utf-8")
        self._event_file = self.events_path.open("w", encoding="utf-8")
        self.write_json(self.session_path, session_meta)
        self.write_issue_template(session_meta)

    def close(self) -> None:
        self._line_file.close()
        self._event_file.close()

    def line(self, message: str = "") -> None:
        print(message)
        self._line_file.write(message + "\n")
        self._line_file.flush()

    def event(self, event_type: str, payload: dict[str, Any]) -> None:
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event_type": event_type,
            **payload,
        }
        self._event_file.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
        self._event_file.flush()

    @staticmethod
    def write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def write_issue_template(self, meta: dict[str, Any]) -> None:
        self.issue_path.write_text(
            "\n".join(
                [
                    "# Manual Playtest Issue",
                    "",
                    "Paste this block back to Codex with what felt wrong.",
                    "",
                    "## What I Saw",
                    "- ",
                    "",
                    "## What I Expected",
                    "- ",
                    "",
                    "## Repro",
                    f"- Command: `{meta.get('command', '')}`",
                    f"- Save id: `{meta.get('save_id') or '-'}`",
                    f"- DB: `{meta.get('db_path')}`",
                    f"- Schedule game id: `{meta.get('schedule_game_id') or '-'}`",
                    f"- Seed: `{meta.get('seed')}`",
                    f"- Log folder: `{self.path}`",
                    "",
                    "## Helpful Files",
                    f"- Play log: `{self.lines_path}`",
                    f"- Events JSONL: `{self.events_path}`",
                    f"- Session JSON: `{self.session_path}`",
                    f"- Box score: `{self.box_score_path}`",
                    f"- Result JSON: `{self.result_path}`",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def write_result(self, result: match_engine.GameResult) -> None:
        payload = {
            "score": {
                result.away.abbreviation: result.away_score,
                result.home.abbreviation: result.home_score,
            },
            "season": result.season,
            "week": result.week,
            "schedule_game_id": result.schedule_game_id,
            "seed": result.seed,
            "engine_version": match_engine.ENGINE_VERSION,
            "drives": [asdict(drive) for drive in result.drives],
            "plays": [asdict(play) for play in result.plays],
            "team_stats": {
                str(team_id): dict(stats)
                for team_id, stats in result.team_stats.items()
            },
            "player_stats": {
                str(player_id): dict(stats)
                for player_id, stats in result.player_stats.items()
            },
        }
        self.write_json(self.result_path, payload)

        buffer = StringIO()
        with redirect_stdout(buffer):
            sim_game.print_result(result, show_plays=24, box=True)
        text = buffer.getvalue()
        self.box_score_path.write_text(text, encoding="utf-8")

    def write_crash(self, exc: BaseException) -> None:
        self.crash_path.write_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            encoding="utf-8",
        )


class ManualMatchEngine(match_engine.MatchEngine):
    def __init__(
        self,
        *,
        user_team_id: int,
        logger: PlaytestLogger,
        interactive: bool,
        pause_defense: bool,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.user_team_id = user_team_id
        self.logger = logger
        self.interactive = interactive
        self.pause_defense = pause_defense
        self.forced_run_concept: str | None = None
        self.forced_pass_concept: str | None = None
        self.auto_drive_number: int | None = None

    def score_text(self) -> str:
        return (
            f"{self.away.abbreviation} {int(self.score[self.away.team_id])} - "
            f"{self.home.abbreviation} {int(self.score[self.home.team_id])}"
        )

    def prompt_header(
        self,
        offense: match_engine.TeamSnapshot,
        defense: match_engine.TeamSnapshot,
        down: int,
        distance: int,
        field_pos: int,
    ) -> str:
        return (
            f"Q{self.quarter} {match_engine.clock_string(self.clock_tenths)} | "
            f"{self.score_text()} | {offense.abbreviation} ball | "
            f"{down}&{distance} at {match_engine.format_yardline(field_pos)}"
        )

    def input_choice(self, prompt: str, valid: set[str], default: str) -> str:
        if not self.interactive:
            return default
        while True:
            raw = input(prompt).strip().lower()
            if not raw:
                raw = default
            if raw in valid:
                return raw
            self.logger.line(f"Invalid choice: {raw}. Valid choices: {', '.join(sorted(valid))}")

    def choose_manual_offense(
        self,
        offense: match_engine.TeamSnapshot,
        defense: match_engine.TeamSnapshot,
        down: int,
        distance: int,
        field_pos: int,
    ) -> bool:
        if not self.interactive:
            return super().play_call_is_pass(offense, defense, down, distance, field_pos)
        if self.auto_drive_number == self.drive_number:
            return super().play_call_is_pass(offense, defense, down, distance, field_pos)

        self.logger.line("")
        self.logger.line(self.prompt_header(offense, defense, down, distance, field_pos))
        self.logger.line("Vikings offense: [r]un, [p]ass, [c]oach suggestion, [s]im drive, [q]uit")
        choice = self.input_choice("> ", {"r", "p", "c", "s", "q"}, "c")
        self.logger.event(
            "manual_choice",
            {
                "team": offense.abbreviation,
                "choice": choice,
                "quarter": self.quarter,
                "clock": match_engine.clock_string(self.clock_tenths),
                "down": down,
                "distance": distance,
                "yardline": field_pos,
            },
        )
        if choice == "q":
            raise ManualQuit("Manual playtest quit by user.")
        if choice == "s":
            self.auto_drive_number = self.drive_number
            self.logger.line("Drive set to CPU simulation.")
            return super().play_call_is_pass(offense, defense, down, distance, field_pos)
        if choice == "c":
            return super().play_call_is_pass(offense, defense, down, distance, field_pos)
        if choice == "r":
            self.logger.line("Run concept: 1 inside zone, 2 outside zone, 3 power, 4 draw")
            concept_choice = self.input_choice("> ", set(RUN_CONCEPTS), "1")
            self.forced_run_concept = RUN_CONCEPTS[concept_choice]
            self.logger.event("manual_concept", {"play_type": "run", "concept": self.forced_run_concept})
            return False
        self.logger.line("Pass concept: 1 quick, 2 short, 3 intermediate, 4 deep, 5 screen")
        concept_choice = self.input_choice("> ", set(PASS_CONCEPTS), "2")
        self.forced_pass_concept = PASS_CONCEPTS[concept_choice]
        self.logger.event("manual_concept", {"play_type": "pass", "concept": self.forced_pass_concept})
        return True

    def play_call_is_pass(
        self,
        offense: match_engine.TeamSnapshot,
        defense: match_engine.TeamSnapshot,
        down: int,
        distance: int,
        field_pos: int,
    ) -> bool:
        if offense.team_id == self.user_team_id:
            return self.choose_manual_offense(offense, defense, down, distance, field_pos)
        if defense.team_id == self.user_team_id and self.interactive and self.pause_defense:
            self.logger.line("")
            self.logger.line(self.prompt_header(offense, defense, down, distance, field_pos))
            self.logger.line("Vikings defense is CPU-resolved for now. Press Enter to watch, [s] sim drive, [q] quit.")
            choice = self.input_choice("> ", {"", "s", "q"}, "")
            if choice == "q":
                raise ManualQuit("Manual playtest quit by user.")
            if choice == "s":
                self.auto_drive_number = self.drive_number
        return super().play_call_is_pass(offense, defense, down, distance, field_pos)

    def fourth_down_decision(
        self,
        offense: match_engine.TeamSnapshot,
        defense: match_engine.TeamSnapshot,
        down: int,
        distance: int,
        field_pos: int,
    ) -> str:
        if not self.interactive or offense.team_id != self.user_team_id or self.auto_drive_number == self.drive_number:
            return super().fourth_down_decision(offense, defense, down, distance, field_pos)
        self.logger.line("")
        self.logger.line(self.prompt_header(offense, defense, down, distance, field_pos))
        self.logger.line("Fourth down: [g]o, [f]ield goal, [p]unt, [c]oach suggestion, [q]uit")
        choice = self.input_choice("> ", {"g", "f", "p", "c", "q"}, "c")
        if choice == "q":
            raise ManualQuit("Manual playtest quit by user.")
        if choice == "g":
            decision = "go"
        elif choice == "f":
            decision = "field_goal"
        elif choice == "p":
            decision = "punt"
        else:
            decision = super().fourth_down_decision(offense, defense, down, distance, field_pos)
        self.logger.event("manual_fourth_down", {"choice": choice, "decision": decision})
        return decision

    def choose_run_concept(
        self,
        offense: match_engine.TeamSnapshot,
        defense: match_engine.TeamSnapshot,
        down: int,
        distance: int,
        field_pos: int,
    ) -> str:
        if self.forced_run_concept:
            concept = self.forced_run_concept
            self.forced_run_concept = None
            return concept
        return super().choose_run_concept(offense, defense, down, distance, field_pos)

    def choose_pass_concept(
        self,
        offense: match_engine.TeamSnapshot,
        defense: match_engine.TeamSnapshot,
        down: int,
        distance: int,
        field_pos: int,
    ) -> str:
        if self.forced_pass_concept:
            concept = self.forced_pass_concept
            self.forced_pass_concept = None
            return concept
        return super().choose_pass_concept(offense, defense, down, distance, field_pos)

    def add_play_event(self, event: match_engine.PlayEvent) -> None:
        super().add_play_event(event)
        offense = self.away if event.offense_team_id == self.away.team_id else self.home
        text = (
            f"#{event.play_number:03d} Q{event.quarter} {match_engine.clock_string(event.clock_tenths)} "
            f"{offense.abbreviation} {event.down}&{event.distance} "
            f"at {match_engine.format_yardline(event.yardline)} "
            f"[{event.play_type}/{event.concept}] {event.description}"
        )
        self.logger.line(text)
        self.logger.event("play", asdict(event))


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


def scheduled_game(
    con: sqlite3.Connection,
    *,
    schedule_game_id: int | None,
    team: str,
    opponent: str | None,
    season: int,
    force: bool,
) -> sqlite3.Row | None:
    if schedule_game_id is not None:
        row = con.execute(
            """
            SELECT sg.*, away.abbreviation AS away_team, home.abbreviation AS home_team
            FROM season_games sg
            JOIN teams away ON away.team_id = sg.away_team_id
            JOIN teams home ON home.team_id = sg.home_team_id
            WHERE sg.game_id = ?
            """,
            (schedule_game_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Schedule game not found: {schedule_game_id}")
        return row

    params: list[Any] = [season, team.upper(), team.upper()]
    filters = [
        "sg.season = ?",
        "sg.game_type = 'REG'",
        "(away.abbreviation = ? OR home.abbreviation = ?)",
    ]
    if opponent:
        filters.append("(away.abbreviation = ? OR home.abbreviation = ?)")
        params.extend([opponent.upper(), opponent.upper()])
    if not force:
        filters.append("COALESCE(sg.played, 0) = 0")
    return con.execute(
        f"""
        SELECT sg.*, away.abbreviation AS away_team, home.abbreviation AS home_team
        FROM season_games sg
        JOIN teams away ON away.team_id = sg.away_team_id
        JOIN teams home ON home.team_id = sg.home_team_id
        WHERE {' AND '.join(filters)}
        ORDER BY sg.week, sg.week_game_number, sg.game_id
        LIMIT 1
        """,
        params,
    ).fetchone()


def current_season(con: sqlite3.Connection) -> int:
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_season'"
    ).fetchone()
    return int(row["setting_value"]) if row else match_engine.DEFAULT_SEASON


def build_session_meta(
    *,
    args: argparse.Namespace,
    db_path: Path,
    save_id: str | None,
    game_row: sqlite3.Row | None,
    away: match_engine.TeamSnapshot,
    home: match_engine.TeamSnapshot,
    seed: int,
) -> dict[str, Any]:
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "command": " ".join(sys.argv),
        "db_path": str(db_path),
        "save_id": save_id,
        "team": args.team.upper(),
        "opponent": args.opponent.upper() if args.opponent else None,
        "season": args.season,
        "week": int(game_row["week"]) if game_row and game_row["week"] is not None else args.week,
        "schedule_game_id": int(game_row["game_id"]) if game_row else None,
        "away": away.abbreviation,
        "home": home.abbreviation,
        "seed": seed,
        "engine_version": match_engine.ENGINE_VERSION,
        "interactive": not args.auto and sys.stdin.isatty(),
        "apply": bool(args.apply),
    }


def action_run(args: argparse.Namespace) -> None:
    db_path = Path(args.db)
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    con = connect(db_path)
    logger: PlaytestLogger | None = None
    try:
        season = args.season or current_season(con)
        args.season = season
        row = scheduled_game(
            con,
            schedule_game_id=args.schedule_game_id,
            team=args.team,
            opponent=args.opponent,
            season=season,
            force=args.force,
        )
        if not row:
            if not args.opponent:
                raise ValueError(f"No scheduled game found for {args.team} in {season}.")
            away_team_id = team_id(con, args.opponent)
            home_team_id = team_id(con, args.team)
            week = args.week
            schedule_game_id = None
            away_abbr = args.opponent.upper()
            home_abbr = args.team.upper()
        else:
            if int(row["played"] or 0) and not args.force:
                raise ValueError(f"Schedule game {row['game_id']} is already played. Use --force to test it.")
            away_team_id = int(row["away_team_id"])
            home_team_id = int(row["home_team_id"])
            week = int(row["week"]) if row["week"] is not None else args.week
            schedule_game_id = int(row["game_id"])
            away_abbr = row["away_team"]
            home_abbr = row["home_team"]

        seed = int(args.seed if args.seed is not None else datetime.now().strftime("%H%M%S"))
        away = match_engine.load_team(con, away_team_id, season)
        home = match_engine.load_team(con, home_team_id, season)
        user_team_id = team_id(con, args.team)
        opponent_abbr = home_abbr if args.team.upper() == away_abbr else away_abbr
        session_meta = build_session_meta(
            args=args,
            db_path=db_path,
            save_id=args.save_id,
            game_row=row,
            away=away,
            home=home,
            seed=seed,
        )
        logger = PlaytestLogger(
            log_root=Path(args.log_root),
            team=args.team.upper(),
            opponent=opponent_abbr,
            seed=seed,
            session_meta=session_meta,
        )
        interactive = not args.auto and sys.stdin.isatty()
        logger.line("Manual playtest starting.")
        logger.line(f"Log folder: {logger.path}")
        logger.line(f"Matchup: {away.display_name} at {home.display_name}")
        logger.line(f"Seed: {seed} | Engine: {match_engine.ENGINE_VERSION}")
        if not interactive:
            logger.line("Running in CPU/auto mode because --auto was set or stdin is not interactive.")

        engine = ManualMatchEngine(
            away=away,
            home=home,
            season=season,
            week=week,
            schedule_game_id=schedule_game_id,
            seed=seed,
            user_team_id=user_team_id,
            logger=logger,
            interactive=interactive,
            pause_defense=args.pause_defense,
        )
        try:
            result = engine.simulate()
        except ManualQuit as exc:
            logger.line(str(exc))
            logger.event("manual_quit", {"message": str(exc)})
            return

        logger.write_result(result)
        logger.line("")
        logger.line(f"Final: {match_engine.scoreline(result)}")
        logger.line(f"Box score: {logger.box_score_path}")
        logger.line(f"Issue template: {logger.issue_path}")

        if args.apply:
            run_id = match_engine.persist_result(
                con,
                result,
                update_schedule=schedule_game_id is not None,
                force=args.force,
                notes=args.notes or "Manual playtest result",
            )
            con.commit()
            logger.line(f"Saved manual sim run {run_id}.")
        else:
            logger.line("Dry run only. Add --apply to save the result.")
    except Exception as exc:
        if logger:
            logger.write_crash(exc)
            logger.line("")
            logger.line(f"Crash report written: {logger.crash_path}")
        raise
    finally:
        if logger:
            logger.close()
        con.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manual play-through harness for NFL GM Sim.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--save-id", help="Save id for logging only.")
    parser.add_argument("--log-root", type=Path, default=LOG_ROOT)
    parser.add_argument("--team", default="MIN", help="Team controlled manually. Default: MIN.")
    parser.add_argument("--opponent", help="Optional opponent abbreviation. Defaults to next scheduled game.")
    parser.add_argument("--schedule-game-id", type=int, help="Specific scheduled game to test.")
    parser.add_argument("--season", type=int)
    parser.add_argument("--week", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--auto", action="store_true", help="CPU-run the same path while still producing logs.")
    parser.add_argument("--pause-defense", action="store_true", help="Pause before opponent snaps while your team is on defense.")
    parser.add_argument("--apply", action="store_true", help="Save the completed result to the season.")
    parser.add_argument("--force", action="store_true", help="Allow testing an already-played scheduled game.")
    parser.add_argument("--notes")
    parser.set_defaults(func=action_run)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
