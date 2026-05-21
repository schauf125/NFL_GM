"""Game flow controller for NFL GM Sim.

This is the first command-line spine for a playable save:

- create/start a game save
- sync the active save to game_settings/current calendar views
- show current status and upcoming events
- advance one day, several days, to a date, or to the next calendar event
  with calendar-event hooks only
- optionally validate rosters when the active phase enforces limits
"""

from __future__ import annotations

import argparse
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import apply_new_game_variance
import daily_processor
import draft_class_bootstrap
import event_generator
import league_calendar
import player_development_modifiers
import player_personalities
import preseason_processor
import roster_rules
import scouting
import scheme_fits


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_START_YEAR = 2026
DEFAULT_CALENDAR_YEARS = 10


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


@dataclass(frozen=True)
class ActiveGame:
    game_id: str
    display_name: str
    start_league_year: int
    current_date: str
    current_league_year: int
    current_phase_code: str
    status: str
    control_mode: str
    user_team_id: int | None
    rng_seed: int | None
    rating_variance_run_id: int | None
    personality_run_id: int | None
    development_run_id: int | None


def connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Use YYYY-MM-DD date format, got {value!r}.") from exc


def money(value: int | None) -> str:
    if value is None:
        return "-"
    if value < 0:
        return "-" + money(abs(value))
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value}"


def ensure_schema(con: sqlite3.Connection) -> None:
    league_calendar.ensure_schema(con)
    roster_rules.ensure_schema(con)
    roster_rules.seed_rules(con)
    player_personalities.ensure_schema(con)
    player_personalities.seed_master_data(con)
    scheme_fits.seed_master_data(con)
    player_development_modifiers.seed_master_data(con)
    daily_processor.ensure_schema(con)
    event_generator.ensure_schema(con)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS game_saves (
            game_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            start_league_year INTEGER NOT NULL,
            current_date TEXT NOT NULL,
            current_league_year INTEGER NOT NULL,
            current_phase_code TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            control_mode TEXT NOT NULL DEFAULT 'team',
            user_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            rng_seed INTEGER,
            rating_variance_run_id INTEGER,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(status IN ('active', 'paused', 'archived')),
            CHECK(control_mode IN ('team', 'observe'))
        );

        CREATE INDEX IF NOT EXISTS idx_game_saves_status
            ON game_saves(status, updated_at);

        CREATE TABLE IF NOT EXISTS game_flow_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL REFERENCES game_saves(game_id) ON DELETE CASCADE,
            game_date TEXT NOT NULL,
            log_type TEXT NOT NULL,
            event_id INTEGER REFERENCES league_calendar_events(event_id) ON DELETE SET NULL,
            event_code TEXT,
            title TEXT NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_game_flow_log_game_date
            ON game_flow_log(game_id, game_date, log_type);

        DROP TRIGGER IF EXISTS trg_game_saves_no_backdate;
        CREATE TRIGGER trg_game_saves_no_backdate
        BEFORE UPDATE OF "current_date" ON game_saves
        FOR EACH ROW
        WHEN OLD."current_date" IS NOT NULL
          AND NEW."current_date" IS NOT NULL
          AND date(NEW."current_date") < date(OLD."current_date")
        BEGIN
            SELECT RAISE(IGNORE);
        END;

        DROP VIEW IF EXISTS active_game_save_view;
        CREATE VIEW active_game_save_view AS
        SELECT
            gs.*,
            t.abbreviation AS user_team,
            t.city AS user_team_city,
            t.nickname AS user_team_nickname,
            lp.phase_name,
            lp.roster_limits_enforced,
            lp.roster_rule_phase,
            lp.transactions_open,
            lp.salary_cap_mode
        FROM game_saves gs
        LEFT JOIN teams t ON t.team_id = gs.user_team_id
        LEFT JOIN league_phase_windows lp
          ON lp.league_year = gs.current_league_year
         AND lp.phase_code = gs.current_phase_code
        WHERE gs.status = 'active';
        """
    )
    cols = {row["name"] for row in con.execute("PRAGMA table_info(game_saves)").fetchall()}
    if "control_mode" not in cols:
        con.execute("ALTER TABLE game_saves ADD COLUMN control_mode TEXT NOT NULL DEFAULT 'team'")
    if "personality_run_id" not in cols:
        con.execute("ALTER TABLE game_saves ADD COLUMN personality_run_id INTEGER")
    if "development_run_id" not in cols:
        con.execute("ALTER TABLE game_saves ADD COLUMN development_run_id INTEGER")


def ensure_calendar_seeded(con: sqlite3.Connection, start_year: int, years: int) -> None:
    ensure_schema(con)
    row = con.execute(
        "SELECT COUNT(*) AS count FROM league_years WHERE league_year = ?",
        (start_year,),
    ).fetchone()
    if int(row["count"] or 0) == 0:
        league_calendar.seed_calendar(
            con,
            start_year=start_year,
            years=years,
            set_current_date=False,
        )


def upsert_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    league_calendar.upsert_setting(con, key, value, overwrite=True)


def phase_for_date(con: sqlite3.Connection, target_date: str) -> sqlite3.Row:
    phase = league_calendar.phase_for_date(con, target_date)
    if not phase:
        raise ValueError(f"No calendar phase found for {target_date}.")
    return phase


def sync_game_settings(con: sqlite3.Connection, game: sqlite3.Row | ActiveGame) -> None:
    if isinstance(game, ActiveGame):
        current_date = game.current_date
        current_league_year = game.current_league_year
        phase_code = game.current_phase_code
        game_id = game.game_id
        user_team_id = game.user_team_id
    else:
        current_date = game["current_date"]
        current_league_year = int(game["current_league_year"])
        phase_code = game["current_phase_code"]
        game_id = game["game_id"]
        user_team_id = game["user_team_id"] if "user_team_id" in game.keys() else None

    phase = phase_for_date(con, current_date)
    upsert_setting(con, "active_game_id", game_id)
    upsert_setting(con, "current_game_date", current_date)
    upsert_setting(con, "current_league_year", str(current_league_year))
    upsert_setting(con, "current_season", str(current_league_year))
    upsert_setting(con, "current_calendar_phase", phase_code)
    if isinstance(game, ActiveGame):
        control_mode = game.control_mode
    else:
        control_mode = game["control_mode"] if "control_mode" in game.keys() and game["control_mode"] else "team"
    upsert_setting(con, "control_mode", str(control_mode))
    if user_team_id is not None:
        team = con.execute("SELECT abbreviation FROM teams WHERE team_id = ?", (int(user_team_id),)).fetchone()
        if team and team["abbreviation"]:
            upsert_setting(con, "user_team", str(team["abbreviation"]))
            upsert_setting(con, "active_user_team", str(team["abbreviation"]))
    else:
        con.execute("DELETE FROM game_settings WHERE setting_key IN ('user_team', 'active_user_team')")
    upsert_setting(con, "roster_limits_enforced", str(int(phase["roster_limits_enforced"] or 0)))
    if phase["salary_cap_mode"]:
        upsert_setting(con, "cap_accounting_mode", phase["salary_cap_mode"])


def active_game(con: sqlite3.Connection) -> ActiveGame | None:
    ensure_schema(con)
    setting = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'active_game_id'"
    ).fetchone()
    row = None
    if setting:
        row = con.execute(
            "SELECT * FROM game_saves WHERE game_id = ? AND status = 'active'",
            (setting["setting_value"],),
        ).fetchone()
    if row is None:
        row = con.execute(
            """
            SELECT *
            FROM game_saves
            WHERE status = 'active'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    current_date = row["current_date"]
    current_league_year = int(row["current_league_year"])
    current_phase_code = row["current_phase_code"]
    setting_date = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
    ).fetchone()
    if setting_date and setting_date["setting_value"] and setting_date["setting_value"] > current_date:
        current_date = setting_date["setting_value"]
        phase = phase_for_date(con, current_date)
        current_league_year = int(phase["league_year"])
        current_phase_code = phase["phase_code"]
        con.execute(
            """
            UPDATE game_saves
            SET "current_date" = ?,
                current_league_year = ?,
                current_phase_code = ?,
                updated_at = datetime('now')
            WHERE game_id = ?
            """,
            (current_date, current_league_year, current_phase_code, row["game_id"]),
        )
    return ActiveGame(
        game_id=row["game_id"],
        display_name=row["display_name"],
        start_league_year=int(row["start_league_year"]),
        current_date=current_date,
        current_league_year=current_league_year,
        current_phase_code=current_phase_code,
        status=row["status"],
        control_mode=row["control_mode"] if "control_mode" in row.keys() and row["control_mode"] else "team",
        user_team_id=row["user_team_id"],
        rng_seed=row["rng_seed"],
        rating_variance_run_id=row["rating_variance_run_id"],
        personality_run_id=row["personality_run_id"],
        development_run_id=row["development_run_id"],
    )


def get_team_id(con: sqlite3.Connection, team_abbr: str | None) -> int | None:
    if not team_abbr:
        return None
    row = con.execute(
        "SELECT team_id FROM teams WHERE abbreviation = ?",
        (team_abbr.upper(),),
    ).fetchone()
    if not row:
        raise ValueError(f"Team not found: {team_abbr}")
    return int(row["team_id"])


def log_game_event(
    con: sqlite3.Connection,
    *,
    game_id: str,
    game_date: str,
    log_type: str,
    title: str,
    details: str | None = None,
    event: sqlite3.Row | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO game_flow_log (
            game_id, game_date, log_type, event_id, event_code, title, details
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_id,
            game_date,
            log_type,
            event["event_id"] if event else None,
            event["event_code"] if event else None,
            title,
            details,
        ),
    )


def latest_variance_run_id(con: sqlite3.Connection, game_id: str, season: int) -> int | None:
    row = con.execute(
        """
        SELECT run_id
        FROM new_game_variance_runs
        WHERE game_id = ? AND season = ?
        ORDER BY run_id DESC
        LIMIT 1
        """,
        (game_id, season),
    ).fetchone()
    return int(row["run_id"]) if row else None


def start_game(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_calendar_seeded(con, args.start_year, args.calendar_years)
    if con.execute("SELECT 1 FROM game_saves WHERE game_id = ?", (args.game_id,)).fetchone():
        raise ValueError(f"Game save already exists: {args.game_id}")

    start_date = f"{args.start_year}-06-01"
    phase = phase_for_date(con, start_date)
    requested_mode = str(getattr(args, "control_mode", "") or "").strip().lower()
    observe_requested = bool(getattr(args, "observe_mode", False))
    control_mode = "observe" if observe_requested or requested_mode == "observe" or not args.user_team else "team"
    if requested_mode and requested_mode not in {"team", "observe"}:
        raise ValueError(f"Unsupported control mode: {requested_mode}")
    user_team_id = None if control_mode == "observe" else get_team_id(con, args.user_team)
    seed = args.seed if args.seed is not None else secrets.randbits(63)
    display_name = args.name or args.game_id
    variance_run_id = None
    personality_run_id = None
    development_run_id = None
    draft_class_details = "Draft class setup is pending user choice."

    if not args.no_variance:
        player_results, role_score_updates = apply_new_game_variance.apply_variance(
            con,
            game_id=args.game_id,
            season=args.start_year,
            seed=seed,
            rating_max_delta=args.rating_max_delta,
            rookie_potential_max_delta=args.rookie_potential_max_delta,
            young_potential_max_delta=args.young_potential_max_delta,
            young_age_cutoff=args.young_age_cutoff,
            notes=f"Game flow start for {args.game_id}",
            dry_run=False,
        )
        variance_run_id = latest_variance_run_id(con, args.game_id, args.start_year)
        variance_details = (
            f"Applied new-game rating variance to {len(player_results)} players; "
            f"recalculated {role_score_updates} role scores."
        )
    else:
        variance_details = "New-game rating variance skipped."

    scheme_result = scheme_fits.seed_all(con, season=args.start_year, dry_run=False)
    scheme_details = (
        f"Seeded scheme foundation: {scheme_result['teams']} team identities, "
        f"{scheme_result['coach_fits']} coach fits, {scheme_result['player_fits']} player fit rows."
    )

    if not getattr(args, "no_personality_variance", False):
        personality_seed = seed ^ 0x5F3759DF
        personality_result = player_personalities.apply_personality_variance(
            con,
            game_id=args.game_id,
            season=args.start_year,
            seed=personality_seed,
            notes=f"Game flow start for {args.game_id}",
            dry_run=False,
        )
        personality_run_id = personality_result["run_id"]
        personality_details = (
            f"Applied hidden personality variance: {personality_result['total_assignments']} traits "
            f"({personality_result['baseline_kept']} baseline kept, "
            f"{personality_result['baseline_omitted']} baseline omitted, "
            f"{personality_result['random_assignments']} random); "
            f"{personality_result.get('preference_rows', 0)} free-agency preference rows."
        )
    else:
        personality_details = "Hidden personality variance skipped."

    if not getattr(args, "no_development_modifiers", False):
        development_seed = seed ^ 0x00D3A7A
        development_result = player_development_modifiers.apply_development_modifiers(
            con,
            game_id=args.game_id,
            season=args.start_year,
            seed=development_seed,
            notes=f"Game flow start for {args.game_id}",
            dry_run=False,
        )
        development_run_id = development_result["run_id"]
        development_details = (
            f"Applied hidden development modifiers: {development_result['modifiers']} factor rows "
            f"for {development_result['players']} players."
        )
    else:
        development_details = "Hidden development modifiers skipped."

    if not getattr(args, "no_draft_class_generation", True):
        draft_year = args.start_year + 1
        draft_result = draft_class_bootstrap.ensure_draft_class(
            con,
            draft_year=draft_year,
            seed=f"{seed}:draft-class:{draft_year}",
            public_count=getattr(args, "draft_class_count", draft_class_bootstrap.DEFAULT_PUBLIC_PROSPECT_COUNT),
            hidden_count=0
            if getattr(args, "no_hidden_draft_prospects", False)
            else getattr(args, "draft_hidden_count", None),
            class_strength=getattr(args, "draft_class_strength", draft_class_bootstrap.DEFAULT_CLASS_STRENGTH),
            notes=f"Generated at start of save {args.game_id}.",
            refresh_legacy_without_offboard=True,
            replace_existing=True,
        )
        scouting_result = scouting.initialize_for_game(
            con,
            game_id=args.game_id,
            draft_year=draft_year,
            welcome_message=True,
        )
        draft_class_details = (
            f"{draft_result.message} Scouting desk initialized with "
            f"{scouting_result['public']} public prospects and "
            f"{scouting_result['hidden']} off-board discovery candidates."
        )
    else:
        league_calendar.upsert_setting(
            con,
            "draft_class_setup_pending_year",
            str(args.start_year + 1),
            overwrite=True,
        )
        league_calendar.upsert_setting(
            con,
            "draft_class_setup_pending_reason",
            "Choose Generate or Import Draft Class for this June 1 save.",
            overwrite=True,
        )
        draft_class_details = (
            f"{args.start_year + 1} draft class setup pending. "
            "Choose Generate or Import Draft Class from the game UI."
        )

    preseason_games = preseason_processor.ensure_preseason_schedule(
        con,
        season=args.start_year,
        event_date=f"{args.start_year}-08-13",
        event_week=1,
        seed=f"{args.game_id}:{args.start_year}:preseason-schedule",
    )
    preseason_details = (
        f"Seeded {preseason_games} preseason game(s)."
        if preseason_games
        else "Preseason schedule already available."
    )

    con.execute(
        """
        INSERT INTO game_saves (
            game_id, display_name, start_league_year, "current_date",
            current_league_year, current_phase_code, status, control_mode, user_team_id,
            rng_seed, rating_variance_run_id, personality_run_id, development_run_id, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            args.game_id,
            display_name,
            args.start_year,
            start_date,
            int(phase["league_year"]),
            phase["phase_code"],
            control_mode,
            user_team_id,
            seed
            if (
                not args.no_variance
                or not getattr(args, "no_personality_variance", False)
                or not getattr(args, "no_development_modifiers", False)
            )
            else None,
            variance_run_id,
            personality_run_id,
            development_run_id,
            args.notes,
        ),
    )
    con.execute(
        """
        UPDATE game_saves
        SET "current_date" = ?,
            current_league_year = ?,
            current_phase_code = ?,
            updated_at = datetime('now')
        WHERE game_id = ?
        """,
        (start_date, int(phase["league_year"]), phase["phase_code"], args.game_id),
    )
    game_row = con.execute("SELECT * FROM game_saves WHERE game_id = ?", (args.game_id,)).fetchone()
    sync_game_settings(con, game_row)
    log_game_event(
        con,
        game_id=args.game_id,
        game_date=start_date,
        log_type="GAME_START",
        title="Game started",
        details=(
            f"{scheme_details} {variance_details} {personality_details} "
            f"{development_details} {draft_class_details} {preseason_details}"
        ),
    )
    for event in events_on_date(con, start_date):
        log_game_event(
            con,
            game_id=args.game_id,
            game_date=event["event_start_date"],
            log_type="CALENDAR_EVENT",
            title=event["event_name"],
            details=event["notes"],
            event=event,
        )
    con.commit()

    print(f"Started game: {display_name} ({args.game_id})")
    print(f"Date: {start_date}")
    print(f"Phase: {phase['phase_name']} ({phase['phase_code']})")
    print(f"Roster limits enforced: {phase['roster_limits_enforced']}")
    if user_team_id:
        print(f"User team: {args.user_team.upper()}")
    if not args.no_variance:
        print(f"Variance seed: {seed}")
        print(variance_details)
    if not getattr(args, "no_personality_variance", False):
        print(personality_details)
    if not getattr(args, "no_development_modifiers", False):
        print(development_details)
    print(scheme_details)
    print(draft_class_details)
    print(preseason_details)


def upcoming_events(con: sqlite3.Connection, current_date: str, *, limit: int, strict: bool = False) -> list[sqlite3.Row]:
    comparator = ">" if strict else ">="
    return list(
        con.execute(
            f"""
            SELECT *
            FROM league_calendar_view
            WHERE date(event_start_date) {comparator} date(?)
            ORDER BY event_start_date, sort_order
            LIMIT ?
            """,
            (current_date, limit),
        )
    )


def events_on_date(con: sqlite3.Connection, target_date: str) -> list[sqlite3.Row]:
    return list(
        con.execute(
            """
            SELECT *
            FROM league_calendar_view
            WHERE date(event_start_date) = date(?)
            ORDER BY event_start_date, sort_order
            """,
            (target_date,),
        )
    )


def draft_class_has_prospects(con: sqlite3.Connection, draft_year: int) -> bool:
    if not table_exists(con, "draft_classes") or not table_exists(con, "draft_prospects"):
        return False
    row = con.execute(
        """
        SELECT COUNT(dp.prospect_id) AS prospect_count
        FROM draft_classes dc
        LEFT JOIN draft_prospects dp ON dp.draft_class_id = dc.draft_class_id
        WHERE dc.draft_year = ?
        GROUP BY dc.draft_class_id
        """,
        (draft_year,),
    ).fetchone()
    return bool(row and int(row["prospect_count"] or 0) > 0)


def pending_draft_class_setup_year(con: sqlite3.Connection) -> int | None:
    if not table_exists(con, "game_settings"):
        return None
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'draft_class_setup_pending_year'"
    ).fetchone()
    if not row or not row["setting_value"]:
        return None
    try:
        draft_year = int(row["setting_value"])
    except (TypeError, ValueError):
        return None
    if draft_class_has_prospects(con, draft_year):
        con.execute(
            "DELETE FROM game_settings WHERE setting_key IN ('draft_class_setup_pending_year', 'draft_class_setup_pending_reason')"
        )
        upsert_setting(con, "draft_class_ready_year", str(draft_year))
        return None
    return draft_year


def first_sim_year_start_between(
    con: sqlite3.Connection,
    current_date: str,
    target_date: str,
) -> sqlite3.Row | None:
    if not table_exists(con, "league_calendar_events"):
        return None
    return con.execute(
        """
        SELECT *
        FROM league_calendar_events
        WHERE event_code = 'SIM_YEAR_START'
          AND date(event_start_date) > date(?)
          AND date(event_start_date) <= date(?)
        ORDER BY event_start_date, sort_order
        LIMIT 1
        """,
        (current_date, target_date),
    ).fetchone()


def print_events(events: list[sqlite3.Row]) -> None:
    for event in events:
        end = f" to {event['event_end_date']}" if event["event_end_date"] else ""
        time = f" {event['event_time_et']} ET" if event["event_time_et"] else ""
        official = "official" if event["is_official"] else "projected"
        print(f"  {event['event_start_date']}{end}{time}: {event['event_name']} ({official})")


def status(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    game = active_game(con)
    if game:
        sync_game_settings(con, game)
        phase = phase_for_date(con, game.current_date)
        print(f"Game: {game.display_name} ({game.game_id})")
        print(f"Date: {game.current_date}")
        print(f"League year: {game.current_league_year}")
        print(f"Phase: {phase['phase_name']} ({phase['phase_code']})")
        print(f"Roster limits: {'ON' if phase['roster_limits_enforced'] else 'OFF'}")
        print(f"Roster rule phase: {phase['roster_rule_phase'] or 'None'}")
        print(f"Transactions open: {'YES' if phase['transactions_open'] else 'NO'}")
        print(f"Cap mode: {phase['salary_cap_mode']}")
        if game.rating_variance_run_id:
            print(f"New-game variance run: {game.rating_variance_run_id} seed={game.rng_seed}")
        if game.personality_run_id:
            print(f"Hidden personality run: {game.personality_run_id}")
        if game.development_run_id:
            print(f"Hidden development run: {game.development_run_id}")
        current_date = game.current_date
    else:
        row = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
        ).fetchone()
        current_date = row["setting_value"] if row else f"{DEFAULT_START_YEAR}-06-01"
        phase = phase_for_date(con, current_date)
        print("No active game save yet.")
        print(f"Calendar date: {current_date}")
        print(f"Phase: {phase['phase_name']} ({phase['phase_code']})")

    print("\nUpcoming events:")
    print_events(upcoming_events(con, current_date, limit=args.limit))


def events_between(con: sqlite3.Connection, old_date: str, new_date: str) -> list[sqlite3.Row]:
    return list(
        con.execute(
            """
            SELECT *
            FROM league_calendar_view
            WHERE date(event_start_date) > date(?)
              AND date(event_start_date) <= date(?)
            ORDER BY event_start_date, sort_order
            """,
            (old_date, new_date),
        )
    )


def update_active_game_date(con: sqlite3.Connection, game: ActiveGame, target_date: str) -> tuple[sqlite3.Row, list[sqlite3.Row]]:
    old_date = game.current_date
    phase = phase_for_date(con, target_date)
    crossed_events = events_between(con, old_date, target_date)
    con.execute(
        """
        UPDATE game_saves
        SET "current_date" = ?,
            current_league_year = ?,
            current_phase_code = ?,
            updated_at = datetime('now')
        WHERE game_id = ?
        """,
        (target_date, int(phase["league_year"]), phase["phase_code"], game.game_id),
    )
    refreshed = con.execute("SELECT * FROM game_saves WHERE game_id = ?", (game.game_id,)).fetchone()
    sync_game_settings(con, refreshed)
    log_game_event(
        con,
        game_id=game.game_id,
        game_date=target_date,
        log_type="DATE_ADVANCE",
        title=f"Advanced from {old_date} to {target_date}",
        details=f"Phase is now {phase['phase_name']} ({phase['phase_code']}).",
    )
    for event in crossed_events:
        log_game_event(
            con,
            game_id=game.game_id,
            game_date=event["event_start_date"],
            log_type="CALENDAR_EVENT",
            title=event["event_name"],
            details=event["notes"],
            event=event,
        )
    return phase, crossed_events


def copy_cpu_scouting_to_user(
    con: sqlite3.Connection,
    *,
    game_id: str,
    team_id: int,
) -> dict[str, int]:
    scouting.ensure_schema(con)
    if not table_exists(con, "cpu_scouting_prospect_progress") or not table_exists(con, "scouting_prospect_progress"):
        return {"copied": 0, "draft_years": 0}
    source_count = con.execute(
        """
        SELECT COUNT(*) AS count
        FROM cpu_scouting_prospect_progress
        WHERE game_id = ?
          AND team_id = ?
        """,
        (game_id, team_id),
    ).fetchone()
    draft_years = con.execute(
        """
        SELECT COUNT(DISTINCT draft_year) AS count
        FROM cpu_scouting_prospect_progress
        WHERE game_id = ?
          AND team_id = ?
        """,
        (game_id, team_id),
    ).fetchone()
    con.execute(
        """
        INSERT INTO scouting_prospect_progress (
            game_id, draft_year, prospect_id, visibility_status, scouting_level,
            scouting_confidence, times_scouted, personality_known,
            last_scouted_season, last_scouted_week, last_scouted_date, last_report,
            created_at, updated_at
        )
        SELECT
            game_id, draft_year, prospect_id, visibility_status, scouting_level,
            scouting_confidence, times_scouted, COALESCE(personality_known, 0),
            last_scouted_season, last_scouted_week, last_scouted_date, last_report,
            datetime('now'), datetime('now')
        FROM cpu_scouting_prospect_progress
        WHERE game_id = ?
          AND team_id = ?
        ON CONFLICT(game_id, draft_year, prospect_id) DO UPDATE SET
            visibility_status = CASE
                WHEN scouting_prospect_progress.visibility_status = 'hidden' THEN excluded.visibility_status
                ELSE scouting_prospect_progress.visibility_status
            END,
            scouting_level = MAX(scouting_prospect_progress.scouting_level, excluded.scouting_level),
            scouting_confidence = CASE
                WHEN excluded.scouting_level >= scouting_prospect_progress.scouting_level THEN excluded.scouting_confidence
                ELSE scouting_prospect_progress.scouting_confidence
            END,
            times_scouted = MAX(scouting_prospect_progress.times_scouted, excluded.times_scouted),
            personality_known = MAX(scouting_prospect_progress.personality_known, excluded.personality_known),
            last_scouted_season = COALESCE(excluded.last_scouted_season, scouting_prospect_progress.last_scouted_season),
            last_scouted_week = COALESCE(excluded.last_scouted_week, scouting_prospect_progress.last_scouted_week),
            last_scouted_date = COALESCE(excluded.last_scouted_date, scouting_prospect_progress.last_scouted_date),
            last_report = COALESCE(excluded.last_report, scouting_prospect_progress.last_report),
            updated_at = datetime('now')
        """,
        (game_id, team_id),
    )
    return {
        "copied": int(source_count["count"] or 0) if source_count else 0,
        "draft_years": int(draft_years["count"] or 0) if draft_years else 0,
    }


def action_take_over_team(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game = active_game(con)
    if not game:
        raise ValueError("No active game save to take over.")
    team_abbr = str(args.team or "").strip().upper()
    if not team_abbr:
        raise ValueError("A team abbreviation is required.")
    team_id = get_team_id(con, team_abbr)
    team = con.execute(
        "SELECT abbreviation, city || ' ' || nickname AS team_name FROM teams WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    if not team:
        raise ValueError(f"Team not found: {team_abbr}")

    con.execute(
        """
        UPDATE game_saves
        SET control_mode = 'team',
            user_team_id = ?,
            updated_at = datetime('now')
        WHERE game_id = ?
        """,
        (team_id, game.game_id),
    )
    if table_exists(con, "draft_room_state"):
        con.execute(
            """
            UPDATE draft_room_state
            SET user_team_id = ?,
                updated_at = datetime('now')
            WHERE status <> 'complete'
            """,
            (team_id,),
        )
    refreshed = con.execute("SELECT * FROM game_saves WHERE game_id = ?", (game.game_id,)).fetchone()
    sync_game_settings(con, refreshed)
    scouting_result = copy_cpu_scouting_to_user(con, game_id=game.game_id, team_id=team_id)
    log_game_event(
        con,
        game_id=game.game_id,
        game_date=refreshed["current_date"],
        log_type="CONTROL_CHANGE",
        title=f"Took over {team['abbreviation']}",
        details=(
            f"Control mode changed from {game.control_mode} to team. "
            f"{team['team_name']} is now user-controlled. "
            f"Copied {scouting_result['copied']} CPU scouting file(s) "
            f"across {scouting_result['draft_years']} draft class(es)."
        ),
    )
    con.commit()
    print(f"Now controlling {team['team_name']} ({team['abbreviation']}).")
    if scouting_result["copied"]:
        print(
            f"Copied {scouting_result['copied']} existing CPU scouting file(s) "
            f"for {team['abbreviation']} into the user scouting board."
        )


def advance_to_date(
    con: sqlite3.Connection,
    target_date: str,
) -> tuple[
    ActiveGame,
    sqlite3.Row,
    list[sqlite3.Row],
    daily_processor.EventRangeResult,
    event_generator.GenerationResult | None,
]:
    ensure_schema(con)
    game = active_game(con)
    if not game:
        raise ValueError("No active game. Run start first.")
    current = parse_date(game.current_date)
    target = parse_date(target_date)
    if target <= current:
        raise ValueError(f"Target date must be after current date {game.current_date}.")
    pending_draft_year = pending_draft_class_setup_year(con)
    if pending_draft_year is not None:
        raise ValueError(
            f"Draft class setup is required for the {pending_draft_year} draft. "
            "Choose Generate or Import Draft Class before advancing the calendar."
        )
    sim_year_start = first_sim_year_start_between(con, game.current_date, target_date)
    if sim_year_start and parse_date(str(sim_year_start["event_start_date"])) < target:
        raise ValueError(
            f"{sim_year_start['event_start_date']} is a June 1 league-year start. "
            "Advance to that date first, then choose Generate or Import Draft Class before continuing."
        )
    phase, crossed_events = update_active_game_date(con, game, target_date)
    processing_result = daily_processor.process_event_range(
        con,
        game_id=game.game_id,
        from_date=game.current_date,
        to_date=target_date,
        include_start=True,
    )
    news_result = None
    if crossed_events:
        news_result = event_generator.generate_weekly_events(
            con,
            game_id=game.game_id,
            season=int(phase["league_year"] or game.current_league_year),
            week=0,
            event_date=target_date,
            apply=True,
            run_key=f"calendar:{target_date}",
        )
    con.commit()
    return game, phase, crossed_events, processing_result, news_result


def print_advance_result(
    game: ActiveGame,
    phase: sqlite3.Row,
    crossed_events: list[sqlite3.Row],
    target_date: str,
    processing_result: daily_processor.EventRangeResult,
    news_result: event_generator.GenerationResult | None = None,
) -> None:
    print(f"Advanced {game.game_id} to {target_date}")
    print(f"Phase: {phase['phase_name']} ({phase['phase_code']})")
    print(f"Roster limits: {'ON' if phase['roster_limits_enforced'] else 'OFF'}")
    if crossed_events:
        print("Events reached:")
        print_events(crossed_events)
    else:
        print("No calendar events reached.")
    print()
    daily_processor.print_event_range_result(processing_result)
    if news_result:
        print(f"  League news rolled: {news_result.planned_count} public event(s) [{news_result.cadence}]")


def action_advance_day(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    if args.days <= 0:
        raise ValueError("--days must be greater than zero.")
    game = active_game(con)
    if not game:
        raise ValueError("No active game. Run start first.")
    target_date = (parse_date(game.current_date) + timedelta(days=args.days)).isoformat()
    _old_game, phase, crossed_events, processing_result, news_result = advance_to_date(con, target_date)
    print_advance_result(game, phase, crossed_events, target_date, processing_result, news_result)


def action_advance_to_date(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game, phase, crossed_events, processing_result, news_result = advance_to_date(con, args.date)
    print_advance_result(game, phase, crossed_events, args.date, processing_result, news_result)


def action_advance_to_next_event(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    game = active_game(con)
    if not game:
        raise ValueError("No active game. Run start first.")
    events = upcoming_events(con, game.current_date, limit=1, strict=True)
    if not events:
        raise ValueError("No future calendar events found.")
    target_date = events[0]["event_start_date"]
    _old_game, phase, crossed_events, processing_result, news_result = advance_to_date(con, target_date)
    print_advance_result(game, phase, crossed_events, target_date, processing_result, news_result)


def action_events(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    game = active_game(con)
    if game:
        current_date = game.current_date
        print(f"Upcoming events for {game.display_name} ({game.game_id}) from {current_date}:")
    else:
        row = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
        ).fetchone()
        current_date = row["setting_value"] if row else f"{DEFAULT_START_YEAR}-06-01"
        print(f"Upcoming events from {current_date}:")
    print_events(upcoming_events(con, current_date, limit=args.limit))


def action_log(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    game = active_game(con)
    if not game:
        raise ValueError("No active game. Run start first.")
    rows = con.execute(
        """
        SELECT *
        FROM game_flow_log
        WHERE game_id = ?
        ORDER BY game_date DESC, log_id DESC
        LIMIT ?
        """,
        (game.game_id, args.limit),
    ).fetchall()
    for row in rows:
        code = f" [{row['event_code']}]" if row["event_code"] else ""
        print(f"{row['game_date']} {row['log_type']}{code}: {row['title']}")
        if row["details"]:
            print(f"  {row['details']}")


def current_rule_phase(con: sqlite3.Connection, game: ActiveGame) -> tuple[sqlite3.Row, str | None]:
    phase = phase_for_date(con, game.current_date)
    if not int(phase["roster_limits_enforced"] or 0):
        return phase, None
    return phase, phase["roster_rule_phase"]


def action_validate_rosters(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_schema(con)
    game = active_game(con)
    if not game:
        raise ValueError("No active game. Run start first.")
    phase, rule_phase = current_rule_phase(con, game)
    if rule_phase is None:
        print(f"Roster limits are OFF in {phase['phase_name']} ({phase['phase_code']}).")
        print("No roster validation needed right now.")
        return

    rule_set = roster_rules.get_rule_set(con, game.current_league_year, rule_phase)
    teams = con.execute("SELECT * FROM teams ORDER BY abbreviation").fetchall()
    totals = {"passed": 0, "errors": 0, "warnings": 0, "infos": 0}
    saved = []
    for team in teams:
        summary, issues = roster_rules.validate_team(
            con,
            team,
            rule_set,
            include_info=args.include_info,
        )
        if not args.no_save:
            saved.append(roster_rules.save_validation_run(con, rule_set, summary, issues))
        totals["passed"] += int(summary["passed"])
        totals["errors"] += int(summary["error_count"])
        totals["warnings"] += int(summary["warning_count"])
        totals["infos"] += int(summary["info_count"])
        if not args.summary_only or int(summary["error_count"]) > 0:
            roster_rules.print_team_result(rule_set, summary, issues, detail=not args.summary_only)

    failed = len(teams) - totals["passed"]
    log_game_event(
        con,
        game_id=game.game_id,
        game_date=game.current_date,
        log_type="ROSTER_VALIDATION",
        title=f"Roster validation for {rule_set['phase']}",
        details=(
            f"{totals['passed']} passed, {failed} failed, "
            f"{totals['errors']} errors, {totals['warnings']} warnings, {totals['infos']} infos."
        ),
    )
    con.commit()
    print(
        f"Validated {len(teams)} teams for {game.current_league_year} {rule_set['phase']}: "
        f"{totals['passed']} passed, {failed} failed, "
        f"{totals['errors']} errors, {totals['warnings']} warnings, {totals['infos']} infos."
    )
    if saved:
        print(f"Saved validation run ids: {min(saved)}-{max(saved)}")


def action_setup(con: sqlite3.Connection, args: argparse.Namespace) -> None:
    ensure_calendar_seeded(con, args.start_year, args.calendar_years)
    con.commit()
    print("Game flow schema is ready.")
    print(f"Calendar coverage checked from {args.start_year} for {args.calendar_years} years.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NFL GM Sim game flow controller.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help=f"SQLite DB path. Default: {DB_PATH}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Create game flow tables/views.")
    setup_parser.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    setup_parser.add_argument("--calendar-years", type=int, default=DEFAULT_CALENDAR_YEARS)

    start_parser = subparsers.add_parser("start", help="Start a new game save.")
    start_parser.add_argument("--game-id", required=True)
    start_parser.add_argument("--name")
    start_parser.add_argument("--user-team", help="Optional user-controlled team abbreviation.")
    start_parser.add_argument("--control-mode", choices=("team", "observe"), help="Use 'observe' for no user-controlled team.")
    start_parser.add_argument("--observe-mode", action="store_true", help="Start with no user-controlled team.")
    start_parser.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    start_parser.add_argument("--calendar-years", type=int, default=DEFAULT_CALENDAR_YEARS)
    start_parser.add_argument("--seed", type=int)
    start_parser.add_argument("--notes")
    start_parser.add_argument("--no-variance", action="store_true", help="Skip new-game rating variance.")
    start_parser.add_argument("--no-personality-variance", action="store_true", help="Skip hidden personality seeding.")
    start_parser.add_argument("--no-development-modifiers", action="store_true", help="Skip hidden development modifier seeding.")
    start_parser.add_argument(
        "--no-draft-class-generation",
        dest="no_draft_class_generation",
        action="store_true",
        default=True,
        help="Skip automatic draft class generation. This is the default; choose the class from the UI.",
    )
    start_parser.add_argument(
        "--generate-draft-class-at-start",
        dest="no_draft_class_generation",
        action="store_false",
        help="Legacy mode: generate the next draft class immediately when the save starts.",
    )
    start_parser.add_argument("--draft-class-count", type=int, default=draft_class_bootstrap.DEFAULT_PUBLIC_PROSPECT_COUNT)
    start_parser.add_argument("--draft-hidden-count", type=int, help="Exact off-public-board prospect count.")
    start_parser.add_argument("--no-hidden-draft-prospects", action="store_true", help="Generate only the public draft board.")
    start_parser.add_argument("--draft-class-strength", type=int, default=draft_class_bootstrap.DEFAULT_CLASS_STRENGTH)
    start_parser.add_argument("--rating-max-delta", type=float, default=0.10)
    start_parser.add_argument("--rookie-potential-max-delta", type=float, default=0.20)
    start_parser.add_argument("--young-potential-max-delta", type=float, default=0.15)
    start_parser.add_argument("--young-age-cutoff", type=int, default=25)

    status_parser = subparsers.add_parser("status", help="Show active game status.")
    status_parser.add_argument("--limit", type=int, default=8, help="Upcoming event count.")

    advance_day_parser = subparsers.add_parser("advance-day", help="Advance the active game by N days.")
    advance_day_parser.add_argument("--days", type=int, default=1)

    advance_date_parser = subparsers.add_parser("advance-to-date", help="Advance the active game to a date.")
    advance_date_parser.add_argument("--date", required=True)

    subparsers.add_parser("advance-to-next-event", help="Advance to the next future calendar event.")

    events_parser = subparsers.add_parser("events", help="Show upcoming events for the active date.")
    events_parser.add_argument("--limit", type=int, default=12)

    log_parser = subparsers.add_parser("log", help="Show recent game flow log entries.")
    log_parser.add_argument("--limit", type=int, default=20)

    validate_parser = subparsers.add_parser("validate-rosters", help="Validate rosters for the active enforced phase.")
    validate_parser.add_argument("--summary-only", action="store_true")
    validate_parser.add_argument("--include-info", action="store_true")
    validate_parser.add_argument("--no-save", action="store_true")

    takeover_parser = subparsers.add_parser("take-over-team", help="Switch an observe save to a user-controlled team.")
    takeover_parser.add_argument("--team", required=True, help="Team abbreviation to control.")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    con = connect(args.db)
    try:
        if args.command == "setup":
            action_setup(con, args)
        elif args.command == "start":
            start_game(con, args)
        elif args.command == "status":
            status(con, args)
        elif args.command == "events":
            action_events(con, args)
        elif args.command == "advance-day":
            action_advance_day(con, args)
        elif args.command == "advance-to-date":
            action_advance_to_date(con, args)
        elif args.command == "advance-to-next-event":
            action_advance_to_next_event(con, args)
        elif args.command == "log":
            action_log(con, args)
        elif args.command == "validate-rosters":
            action_validate_rosters(con, args)
        elif args.command == "take-over-team":
            action_take_over_team(con, args)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
