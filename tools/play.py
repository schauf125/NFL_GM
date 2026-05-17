"""Save-aware play command for NFL GM Sim.

Use this for normal gameplay. It automatically targets the active isolated
save database from saves/save_registry.json, so the master database is not
mutated while playing.
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import daily_processor
import game_flow
import roster_cutdown
import roster_rules
import save_manager
import scouting
import sim_control
import view_team


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"


def save_db(game_id: str | None = None) -> tuple[str, Path]:
    target_game_id, record = save_manager.get_save_record(game_id)
    db_path = ROOT / record["db_path"]
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    return target_game_id, db_path


def open_save_db(game_id: str | None = None):
    target_game_id, db_path = save_db(game_id)
    return target_game_id, db_path, game_flow.connect(db_path)


def _sync_active_game_row_to_settings(con: sqlite3.Connection, target_game_id: str) -> None:
    active_setting = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'active_game_id'"
    ).fetchone()
    active_game_id = str(active_setting["setting_value"]) if active_setting and active_setting["setting_value"] else target_game_id
    row = con.execute("SELECT * FROM game_saves WHERE game_id = ?", (active_game_id,)).fetchone()
    if row is None:
        return
    setting = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
    ).fetchone()
    if not setting or not setting["setting_value"]:
        return
    settings_date = str(setting["setting_value"])
    if date.fromisoformat(settings_date) <= date.fromisoformat(str(row["current_date"])):
        return
    phase = game_flow.phase_for_date(con, settings_date)
    con.execute(
        """
        UPDATE game_saves
        SET "current_date" = ?,
            current_league_year = ?,
            current_phase_code = ?,
            updated_at = datetime('now')
        WHERE game_id = ?
        """,
        (settings_date, int(phase["league_year"]), phase["phase_code"], row["game_id"]),
    )
    refreshed = con.execute("SELECT * FROM game_saves WHERE game_id = ?", (row["game_id"],)).fetchone()
    game_flow.sync_game_settings(con, refreshed)


def sync_active_game_row_to_settings(game_id: str | None = None) -> str:
    target_game_id, db_path = save_db(game_id)
    with game_flow.connect(db_path) as con:
        _sync_active_game_row_to_settings(con, target_game_id)
        con.commit()
    sync_save(target_game_id)
    return target_game_id


def sync_save(game_id: str) -> None:
    target_game_id, db_path = save_db(game_id)
    with game_flow.connect(db_path) as con:
        _sync_active_game_row_to_settings(con, target_game_id)
        con.commit()
    manifest = save_manager.sync_manifest_from_db(game_id)
    if manifest:
        registry = save_manager.load_registry()
        save_manager.register_save(
            manifest,
            activate=registry.get("active_game_id") == game_id,
        )


def ensure_regular_season_specialists(game_id: str | None, season: int) -> None:
    target_game_id, db_path = save_db(game_id)
    con = game_flow.connect(db_path)
    signed = 0
    still_missing: list[str] = []
    try:
        roster_cutdown.ensure_cutdown_schema(con)
        teams = con.execute(
            """
            SELECT team_id, abbreviation
            FROM teams
            ORDER BY abbreviation
            """
        ).fetchall()
        for team in teams:
            for position in ("K", "P", "LS"):
                before = con.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM players
                    WHERE team_id = ?
                      AND status IN ('Active', 'Questionable', 'Doubtful', 'Out')
                      AND position = ?
                    """,
                    (team["team_id"], position),
                ).fetchone()["count"]
                if int(before or 0) > 0:
                    continue
                if roster_cutdown.sign_missing_specialist(con, team=team, position=position, season=season):
                    signed += 1
                    continue
                still_missing.append(f"{team['abbreviation']} {position}")
        if signed:
            roster_cutdown.rebuild_contract_years(con)
            roster_cutdown.sync_team_cap_space(con)
            roster_cutdown.snapshot_cap_ledger(
                con,
                label="after_specialist_pre_sim",
                phase=roster_cutdown.PHASE,
                source="play_sim_preflight",
                replace=True,
            )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    if signed:
        print(f"Signed {signed} missing K/P/LS specialists before regular-season simulation.")
        sync_save(target_game_id)
    if still_missing:
        print("Warning: no free agent specialist was available for: " + ", ".join(still_missing))


def ensure_regular_season_rosters(game_id: str | None, season: int) -> None:
    target_game_id, db_path = save_db(game_id)
    con = game_flow.connect(db_path)
    try:
        over_limit = con.execute(
            """
            SELECT COUNT(*) AS count
            FROM (
                SELECT team_id, COUNT(*) AS active_count
                FROM players
                WHERE team_id IS NOT NULL
                  AND status IN ('Active', 'Questionable', 'Doubtful', 'Out')
                GROUP BY team_id
                HAVING active_count > 53
            )
            """
        ).fetchone()["count"]
    finally:
        con.close()
    if int(over_limit or 0) <= 0:
        return
    command = [
        sys.executable,
        str(TOOLS_DIR / "roster_cutdown.py"),
        "--db",
        str(db_path),
        "run",
        "--game-id",
        target_game_id,
        "--season",
        str(season),
        "--active-limit",
        "53",
        "--practice-squad-limit",
        "16",
        "--apply",
        "--no-backup",
    ]
    subprocess.run(command, check=True)
    sync_save(target_game_id)


def ensure_cpu_depth_charts(game_id: str | None, season: int, *, user_team: str = "MIN") -> None:
    target_game_id, db_path = save_db(game_id)
    command = [
        sys.executable,
        str(TOOLS_DIR / "cpu_depth_chart.py"),
        "--db",
        str(db_path),
        "rebuild",
        "--season",
        str(season),
        "--user-team",
        user_team,
        "--apply",
    ]
    subprocess.run(command, check=True)
    sync_save(target_game_id)


def table_exists(con, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def next_draft_year(con, current_season: int | None) -> int:
    season = current_season or game_flow.DEFAULT_START_YEAR
    if table_exists(con, "draft_classes"):
        row = con.execute(
            """
            SELECT draft_year
            FROM draft_classes
            WHERE draft_year >= ?
            ORDER BY draft_year
            LIMIT 1
            """,
            (season + 1,),
        ).fetchone()
        if row:
            return int(row["draft_year"])
    return season + 1


def draft_event_date(con, draft_year: int) -> str:
    if not table_exists(con, "league_calendar_events"):
        raise ValueError("No league calendar table found.")
    row = con.execute(
        """
        SELECT event_start_date
        FROM league_calendar_events
        WHERE event_code = 'NFL_DRAFT'
          AND (league_year = ? OR event_name = ?)
        ORDER BY event_start_date
        LIMIT 1
        """,
        (draft_year - 1, f"{draft_year} NFL Draft"),
    ).fetchone()
    if not row:
        raise ValueError(f"No NFL_DRAFT calendar event found for {draft_year}.")
    return str(row["event_start_date"])


def next_league_year_start(con) -> tuple[int, str, str]:
    game = game_flow.active_game(con)
    if not game:
        raise ValueError("No active game. Run start first.")
    if table_exists(con, "league_calendar_events"):
        row = con.execute(
            """
            SELECT league_year, event_start_date, event_name
            FROM league_calendar_events
            WHERE event_code = 'SIM_YEAR_START'
              AND event_start_date > ?
            ORDER BY event_start_date
            LIMIT 1
            """,
            (game.current_date,),
        ).fetchone()
        if row:
            return int(row["league_year"]), str(row["event_start_date"]), str(row["event_name"])

    current = date.fromisoformat(str(game.current_date))
    target_year = current.year if current < date(current.year, 6, 1) else current.year + 1
    target_date = f"{target_year}-06-01"
    return target_year, target_date, f"{target_year} Sim League Year Opens"


def active_game_current_date(con) -> str:
    game = game_flow.active_game(con)
    if game:
        return str(game.current_date)
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = 'current_game_date'"
    ).fetchone()
    return str(row["setting_value"]) if row else f"{game_flow.DEFAULT_START_YEAR}-06-01"


def draft_order_status(con, draft_year: int) -> tuple[int, int]:
    slots = con.execute(
        "SELECT COUNT(*) AS count FROM draft_order_slots WHERE draft_year = ?",
        (draft_year,),
    ).fetchone()["count"]
    mismatches = con.execute(
        """
        SELECT COUNT(*) AS count
        FROM draft_order_slots dos
        JOIN draft_picks dp
          ON dp.draft_year = dos.draft_year
         AND dp.original_team_id = dos.team_id
         AND dp.round = 1
         AND COALESCE(dp.is_comp_pick, 0) = 0
        WHERE dos.draft_year = ?
          AND (
              COALESCE(dp.pick_in_round, -1) != dos.slot
              OR COALESCE(dp.pick_number, -1) != dos.slot
          )
        """,
        (draft_year,),
    ).fetchone()["count"]
    return int(slots or 0), int(mismatches or 0)


def postseason_result_count(con, season: int) -> int:
    columns = {
        str(row["name"])
        for row in con.execute("PRAGMA table_info(playoff_games)").fetchall()
    }
    played_clause = "AND COALESCE(played, 0) = 1" if "played" in columns else ""
    row = con.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM playoff_games
        WHERE season = ?
          {played_clause}
          AND winner_team_id IS NOT NULL
          AND loser_team_id IS NOT NULL
        """,
        (season,),
    ).fetchone()
    return int(row["count"] or 0)


def regular_season_status(con, season: int) -> tuple[int, int]:
    row = con.execute(
        """
        SELECT COUNT(*) AS games,
               SUM(CASE WHEN COALESCE(played, 0) = 1 THEN 1 ELSE 0 END) AS played
        FROM season_games
        WHERE season = ? AND game_type = 'REG'
        """,
        (season,),
    ).fetchone()
    return int(row["games"] or 0), int(row["played"] or 0)


def postseason_game_count(con, season: int) -> int:
    table = con.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type IN ('table', 'view') AND name = 'playoff_games'
        """
    ).fetchone()
    if not table:
        return 0
    row = con.execute(
        "SELECT COUNT(*) AS count FROM playoff_games WHERE season = ?",
        (season,),
    ).fetchone()
    return int(row["count"] or 0)


def ensure_playoff_tree_if_ready(game_id: str | None, season: int) -> None:
    target_game_id, _db_path, con = open_save_db(game_id)
    try:
        regular_games, regular_played = regular_season_status(con, season)
        playoff_games = postseason_game_count(con, season)
    finally:
        con.close()

    if regular_games != 272 or regular_played != 272 or playoff_games > 0:
        return
    run_tool_script(
        target_game_id,
        "postseason.py",
        ["tree", "--season", str(season), "--apply"],
    )


def ensure_regular_season_complete_before_draft(
    game_id: str | None,
    season: int,
    *,
    auto_roster_cutdown: bool = False,
) -> None:
    target_game_id, _db_path, con = open_save_db(game_id)
    try:
        regular_games, regular_played = regular_season_status(con, season)
        user_team = active_user_team(con) or "MIN"
    finally:
        con.close()
    if regular_games == 0:
        raise ValueError(f"No {season} regular-season schedule found.")
    if regular_played >= regular_games:
        ensure_playoff_tree_if_ready(target_game_id, season)
        return

    if not auto_roster_cutdown and stop_for_user_roster_cutdown_if_due(target_game_id, season):
        raise RuntimeError(
            "Roster cutdown/practice squad setup is required before simming to the draft. "
            "Choose automatic roster cutdown or handle it manually first."
        )
    ensure_regular_season_rosters(target_game_id, season)
    ensure_regular_season_specialists(target_game_id, season)
    ensure_cpu_depth_charts(target_game_id, season, user_team=user_team)
    print(f"Completing {regular_games - regular_played} unplayed regular-season game(s) before advancing to the draft.")
    run_tool_script(
        target_game_id,
        "sim_game.py",
        ["season", "--season", str(season), "--apply", "--no-ai-gm"],
    )
    ensure_playoff_tree_if_ready(target_game_id, season)


def ensure_final_draft_order(game_id: str | None, *, season: int, draft_year: int) -> None:
    target_game_id, _db_path, con = open_save_db(game_id)
    try:
        slots, mismatches = draft_order_status(con, draft_year)
        playoff_results = postseason_result_count(con, season)
    finally:
        con.close()

    if slots == 32 and mismatches == 0:
        return
    if playoff_results == 0:
        run_tool_script(
            game_id,
            "postseason.py",
            ["run", "--season", str(season), "--apply", "--seed", f"{season}99"],
        )
        _target_game_id, _db_path, con = open_save_db(game_id)
        try:
            playoff_results = postseason_result_count(con, season)
        finally:
            con.close()
    elif 0 < playoff_results < 13:
        print(f"Completing partial {season} postseason before building the {draft_year} draft order.")
        for _ in range(4):
            run_tool_script(
                game_id,
                "postseason.py",
                ["round", "--season", str(season), "--apply", "--seed", f"{season}99"],
            )
            _target_game_id, _db_path, con = open_save_db(game_id)
            try:
                playoff_results = postseason_result_count(con, season)
            finally:
                con.close()
            if playoff_results >= 13:
                break

    if playoff_results >= 13:
        run_tool_script(
            game_id,
            "postseason.py",
            ["draft-order", "--season", str(season), "--apply"],
        )
    else:
        raise ValueError(
            f"{season} postseason is partial ({playoff_results}/13 games). "
            "Finish or rebuild postseason before starting the draft."
        )

    _target_game_id, _db_path, con = open_save_db(game_id)
    try:
        slots, mismatches = draft_order_status(con, draft_year)
        if slots != 32 or mismatches:
            raise ValueError(
                f"{draft_year} draft order is still invalid after rebuild: "
                f"{slots}/32 slots, {mismatches} pick mismatch(es)."
            )
    finally:
        con.close()
    sync_save(target_game_id)


def progression_needed_for_league_year(con, target_year: int) -> bool:
    from_season = target_year - 1
    if table_exists(con, "player_progression_runs"):
        row = con.execute(
            """
            SELECT 1
            FROM player_progression_runs
            WHERE from_season = ? AND to_season = ?
            LIMIT 1
            """,
            (from_season, target_year),
        ).fetchone()
        if row:
            return False
    previous = con.execute(
        "SELECT COUNT(DISTINCT player_id) AS players FROM player_ratings WHERE season = ?",
        (from_season,),
    ).fetchone()
    previous_players = int(previous["players"] or 0)
    if previous_players == 0:
        return False
    return True


def postseason_complete_for_progression(con, season: int) -> bool:
    if not table_exists(con, "playoff_games"):
        return False
    return postseason_result_count(con, season) >= 13


def ensure_progression_for_league_year(game_id: str, target_year: int) -> None:
    _target_game_id, db_path = save_db(game_id)
    with game_flow.connect(db_path) as con:
        needed = progression_needed_for_league_year(con, target_year)
    if not needed:
        return
    from_season = target_year - 1
    seed = int(f"{from_season}{target_year}")
    print(
        f"Current-year ratings/foundations are incomplete for {target_year}; "
        f"running progression {from_season}->{target_year}."
    )
    command = [
        sys.executable,
        str(TOOLS_DIR / "player_progression.py"),
        "--db",
        str(db_path),
        "run",
        "--game-id",
        game_id,
        "--from-season",
        str(from_season),
        "--to-season",
        str(target_year),
        "--seed",
        str(seed),
        "--apply",
    ]
    subprocess.run(command, check=True)


def ensure_postseason_progression_for_league_year(game_id: str | None, target_year: int) -> None:
    target_game_id, db_path = save_db(game_id)
    from_season = target_year - 1
    with game_flow.connect(db_path) as con:
        if not postseason_complete_for_progression(con, from_season):
            return
    ensure_progression_for_league_year(target_game_id, target_year)
    sync_save(target_game_id)


def arg_value(args: list[str], *names: str) -> str | None:
    for index, item in enumerate(args):
        for name in names:
            if item == name and index + 1 < len(args):
                return args[index + 1]
            prefix = f"{name}="
            if item.startswith(prefix):
                return item[len(prefix):]
    return None


def maybe_ensure_progression_before_free_agency(game_id: str | None, free_agency_args: list[str]) -> None:
    command = next((item for item in free_agency_args if not item.startswith("-")), "")
    if command not in {"start", "cpu-seed", "offer", "advance-hour", "advance-day", "resolve"}:
        return
    year_value = arg_value(free_agency_args, "--league-year")
    if not year_value:
        return
    ensure_postseason_progression_for_league_year(game_id, int(year_value))


def maybe_ensure_progression_before_contract_talks(game_id: str | None, contract_args: list[str]) -> None:
    command = next((item for item in contract_args if not item.startswith("-")), "")
    if command not in {"list", "extend", "tag", "release", "restructure"}:
        return
    season_value = arg_value(contract_args, "--season")
    if not season_value:
        return
    ensure_postseason_progression_for_league_year(game_id, int(season_value) + 1)


def active_user_team(con) -> str | None:
    if table_exists(con, "active_game_save_view"):
        row = con.execute("SELECT user_team FROM active_game_save_view LIMIT 1").fetchone()
        if row and row["user_team"]:
            return str(row["user_team"]).upper()
    if table_exists(con, "game_saves"):
        row = con.execute(
            """
            SELECT user_team
            FROM game_saves
            WHERE status = 'active'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row and row["user_team"]:
            return str(row["user_team"]).upper()
    return None


def current_season_setting(con) -> int:
    row = con.execute(
        """
        SELECT setting_value
        FROM game_settings
        WHERE setting_key = 'current_season'
        LIMIT 1
        """
    ).fetchone()
    return int(row["setting_value"]) if row else game_flow.DEFAULT_START_YEAR


def calendar_event_date(con, league_year: int, event_code: str) -> str | None:
    if not table_exists(con, "league_calendar_events"):
        return None
    row = con.execute(
        """
        SELECT event_start_date
        FROM league_calendar_events
        WHERE league_year = ?
          AND event_code = ?
        ORDER BY event_start_date
        LIMIT 1
        """,
        (league_year, event_code),
    ).fetchone()
    return str(row["event_start_date"]) if row else None


def active_user_team_row(con) -> sqlite3.Row | None:
    game = game_flow.active_game(con)
    if game and game.user_team_id is not None:
        return con.execute(
            "SELECT team_id, abbreviation FROM teams WHERE team_id = ?",
            (game.user_team_id,),
        ).fetchone()
    team_abbr = active_user_team(con)
    if team_abbr:
        return con.execute(
            "SELECT team_id, abbreviation FROM teams WHERE abbreviation = ?",
            (team_abbr,),
        ).fetchone()
    return None


def user_roster_gate_message(con, season: int) -> str | None:
    user_team = active_user_team_row(con)
    if not user_team:
        return None
    roster_rules.ensure_schema(con)
    roster_rules.seed_rules(con)
    rule_set = roster_rules.practice_squad_rule_set(con, season, "Regular Season")
    team_id = int(user_team["team_id"])
    active_limit = int(rule_set["active_roster_limit"] or 53)
    ps_limit = int(rule_set["practice_squad_limit"] or 16)
    active_count = roster_rules.active_roster_count(con, team_id)
    ps_count = roster_rules.practice_squad_count(con, team_id)
    issues = []
    if active_count > active_limit:
        issues.append(f"cut active roster from {active_count} to {active_limit}")
    if ps_count < ps_limit:
        issues.append(f"fill practice squad ({ps_count}/{ps_limit})")
    if not issues:
        return None
    return (
        f"Roster cutdown/practice squad setup required for {user_team['abbreviation']}: "
        f"{'; '.join(issues)}. Use Roster Hub > Practice Squad before advancing into the regular season."
    )


def gated_calendar_target(
    con,
    game: game_flow.ActiveGame,
    requested_target: str,
    *,
    auto_roster_cutdown: bool = False,
) -> tuple[str, str | None]:
    current = date.fromisoformat(str(game.current_date))
    target = date.fromisoformat(str(requested_target))
    season = int(game.current_league_year)
    cutdown = calendar_event_date(con, season, "FINAL_ROSTER_CUTDOWN_53")
    practice_squad = calendar_event_date(con, season, "PRACTICE_SQUADS_ESTABLISHED")
    kickoff = calendar_event_date(con, season, "REGULAR_SEASON_KICKOFF")

    if cutdown and not auto_roster_cutdown:
        cutdown_date = date.fromisoformat(cutdown)
        if current < cutdown_date <= target:
            return cutdown, (
                "Stopping at final roster cutdown day. CPU teams will make their cutdowns; "
                "the user team must handle its own final roster and practice squad setup."
            )

    if practice_squad and not auto_roster_cutdown:
        practice_squad_date = date.fromisoformat(practice_squad)
        if current < practice_squad_date <= target:
            return practice_squad, "Stopping when practice squads open so the user can assign the practice squad."

    if practice_squad and kickoff and not auto_roster_cutdown:
        practice_squad_date = date.fromisoformat(practice_squad)
        kickoff_date = date.fromisoformat(kickoff)
        if current >= practice_squad_date and target >= kickoff_date:
            gate_message = user_roster_gate_message(con, season)
            if gate_message:
                raise ValueError(gate_message)
    return requested_target, None


def apply_cpu_roster_cutdowns_if_due(
    game_id: str,
    target_date: str,
    *,
    include_user_team: bool = False,
) -> None:
    target_game_id, db_path = save_db(game_id)
    with game_flow.connect(db_path) as con:
        game = game_flow.active_game(con)
        if not game:
            return
        cutdown = calendar_event_date(con, int(game.current_league_year), "FINAL_ROSTER_CUTDOWN_53")
        if not cutdown or date.fromisoformat(str(target_date)) < date.fromisoformat(cutdown):
            return
        roster_rules.ensure_schema(con)
        roster_rules.seed_rules(con)
        roster_cutdown.ensure_cutdown_schema(con)
        rule_set = roster_rules.get_rule_set(con, int(game.current_league_year), roster_cutdown.PHASE)
        active_limit = int(rule_set["active_roster_limit"] or 53)
        ps_limit = int(rule_set["practice_squad_limit"] or 16)
        user_team_id = game.user_team_id
        teams = []
        for team in roster_cutdown.team_rows(con, None):
            if not include_user_team and user_team_id is not None and int(team["team_id"]) == int(user_team_id):
                continue
            teams.append(team)
        over_limit = [
            team
            for team in teams
            if roster_rules.active_roster_count(con, int(team["team_id"])) > active_limit
        ]
        if not over_limit:
            return
        results = [
            roster_cutdown.cutdown_team(
                con,
                team=team,
                season=int(game.current_league_year),
                rule_set=rule_set,
                active_limit=active_limit,
                practice_squad_limit=ps_limit,
                save_validation=False,
                game_id=target_game_id,
            )
            for team in over_limit
        ]
        roster_cutdown.rebuild_contract_years(con)
        roster_cutdown.sync_team_cap_space(con)
        con.commit()
    if include_user_team:
        print(f"Automatic roster cutdowns applied for {len(results)} team(s), including the user team when needed.")
    else:
        print(f"CPU roster cutdowns applied for {len(results)} team(s). User roster was left for manual decisions.")
    sync_save(target_game_id)


def stop_for_user_roster_cutdown_if_due(game_id: str | None, season: int) -> bool:
    target_game_id, db_path = save_db(game_id)
    with game_flow.connect(db_path) as con:
        game = game_flow.active_game(con)
        if not game:
            return False
        current = date.fromisoformat(str(game.current_date))
        cutdown = calendar_event_date(con, season, "FINAL_ROSTER_CUTDOWN_53")
        practice_squad = calendar_event_date(con, season, "PRACTICE_SQUADS_ESTABLISHED")
        kickoff = calendar_event_date(con, season, "REGULAR_SEASON_KICKOFF")

        stop_date: str | None = None
        gate_message: str | None = None
        if cutdown and current < date.fromisoformat(cutdown):
            stop_date = cutdown
            gate_message = (
                "Stopping at final roster cutdown day. CPU teams will make their cutdowns; "
                "the user team must handle its own final roster and practice squad setup."
            )
        elif practice_squad and current < date.fromisoformat(practice_squad):
            stop_date = practice_squad
            gate_message = "Stopping when practice squads open so the user can assign the practice squad."
        elif kickoff and current < date.fromisoformat(kickoff):
            gate_message = user_roster_gate_message(con, season)

    if stop_date:
        auto_top30_before_calendar_advance(target_game_id, stop_date)
        finish_draft_before_calendar_advance(target_game_id, stop_date)
        game_id, _db_path, con = open_save_db(target_game_id)
        try:
            game_flow.action_advance_to_date(con, SimpleNamespace(date=stop_date))
        finally:
            con.close()
        sync_save(target_game_id)
    if gate_message:
        print(gate_message)
        return True
    return False


def draft_remaining_picks(con, draft_year: int) -> int:
    if not table_exists(con, "draft_picks"):
        return 0
    row = con.execute(
        """
        SELECT COUNT(*) AS remaining
        FROM draft_picks
        WHERE draft_year = ?
          AND COALESCE(is_used, 0) = 0
        """,
        (draft_year,),
    ).fetchone()
    return int(row["remaining"] or 0) if row else 0


def draft_room_started(con, draft_year: int) -> bool:
    if not table_exists(con, "draft_room_state"):
        return False
    row = con.execute(
        "SELECT 1 FROM draft_room_state WHERE draft_year = ? LIMIT 1",
        (draft_year,),
    ).fetchone()
    return row is not None


def auto_top30_before_calendar_advance(game_id: str | None, target_date: str, draft_year: int | None = None) -> None:
    target_game_id, db_path = save_db(game_id)
    with game_flow.connect(db_path) as con:
        current_date = active_game_current_date(con)
        current_season = current_season_setting(con)
        target_year = int(draft_year or next_draft_year(con, current_season))
        try:
            draft_date = draft_event_date(con, target_year)
        except ValueError:
            return
        try:
            _visit_start, visit_end = scouting.top30_visit_window(con, target_year)
        except Exception:
            return
        target = date.fromisoformat(target_date)
        current = date.fromisoformat(current_date)
        deadline = date.fromisoformat(visit_end)
        draft_day = date.fromisoformat(draft_date)
        if target <= deadline and target < draft_day:
            return
        if current > draft_day or draft_room_started(con, target_year):
            return
        try:
            result = scouting.auto_assign_top30_visits(
                con,
                game_id=target_game_id,
                draft_year=target_year,
                seed=f"{target_game_id}:{target_year}:calendar-auto-top30",
                visit_date=visit_end,
            )
            cpu_result = scouting.auto_assign_cpu_top30_visits(
                con,
                game_id=target_game_id,
                draft_year=target_year,
                seed=f"{target_game_id}:{target_year}:calendar-auto-top30",
                visit_date=visit_end,
            )
            con.commit()
        except ValueError as exc:
            print(f"Top 30 auto-fill skipped: {exc}")
            return
    if int(result.get("created") or 0) > 0:
        print(
            f"Top 30 auto-fill scheduled {result['created']} visit(s) for "
            f"{target_game_id} before the {target_year} draft."
        )
    if int(cpu_result.get("created") or 0) > 0:
        print(
            f"CPU scouting auto-filled {cpu_result['created']} Top 30 visit-equivalent report(s) "
            f"across {cpu_result['teams']} team(s)."
        )


def finish_draft_before_calendar_advance(game_id: str | None, target_date: str) -> None:
    target_game_id, db_path = save_db(game_id)
    with game_flow.connect(db_path) as con:
        current_date = active_game_current_date(con)
        current_season = current_season_setting(con)
        draft_year = next_draft_year(con, current_season)
        try:
            draft_date = draft_event_date(con, draft_year)
        except ValueError:
            return
        if date.fromisoformat(target_date) < date.fromisoformat(draft_date):
            return
        remaining = draft_remaining_picks(con, draft_year)
        if remaining <= 0:
            return
        user_team = active_user_team(con) or "MIN"
        already_started = draft_room_started(con, draft_year)

    print(
        f"Calendar advance from {current_date} to {target_date} crosses an unfinished "
        f"{draft_year} draft. Auto-simming {remaining} remaining pick(s) first."
    )
    if not already_started:
        run_tool_script(
            target_game_id,
            "draft_room.py",
            [
                "start",
                "--draft-year",
                str(draft_year),
                "--user-team",
                user_team,
                "--paused",
                "--apply",
            ],
        )
    run_tool_script(
        target_game_id,
        "draft_room.py",
        [
            "skip",
            "--draft-year",
            str(draft_year),
            "--count",
            str(max(remaining, 1)),
            "--include-user-pick",
            "--no-cap-snapshot",
            "--apply",
        ],
    )


def free_agency_days_to_draft(con, draft_year: int, draft_date: str) -> int | None:
    if not table_exists(con, "free_agency_periods"):
        return None
    row = con.execute(
        """
        SELECT "current_date"
        FROM free_agency_periods
        WHERE league_year = ?
          AND status = 'active'
        """,
        (draft_year,),
    ).fetchone()
    if not row:
        return None
    current_date = str(row["current_date"])
    if current_date >= draft_date:
        return None
    return max(1, (date.fromisoformat(draft_date) - date.fromisoformat(current_date)).days)


def free_agency_start_date(con, league_year: int) -> str:
    if table_exists(con, "league_calendar_events"):
        row = con.execute(
            """
            SELECT event_start_date
            FROM league_calendar_events
            WHERE event_code = 'NEXT_NFL_LEAGUE_YEAR_START'
              AND strftime('%Y', event_start_date) = ?
            ORDER BY event_start_date
            LIMIT 1
            """,
            (str(league_year),),
        ).fetchone()
        if row and row["event_start_date"]:
            return str(row["event_start_date"])
    return f"{league_year}-03-10"


def active_free_agency_period_date(con, league_year: int) -> str | None:
    if not table_exists(con, "free_agency_periods"):
        return None
    row = con.execute(
        """
        SELECT "current_date"
        FROM free_agency_periods
        WHERE league_year = ?
          AND status = 'active'
        """,
        (league_year,),
    ).fetchone()
    return str(row["current_date"]) if row and row["current_date"] else None


def ensure_offseason_free_agency_started(game_id: str | None, draft_year: int, draft_date: str) -> bool:
    target_game_id, db_path = save_db(game_id)
    with game_flow.connect(db_path) as con:
        current_date = active_game_current_date(con)
        if date.fromisoformat(current_date) >= date.fromisoformat(draft_date):
            return False
        if active_free_agency_period_date(con, draft_year):
            return False
        start_date = free_agency_start_date(con, draft_year)
        if date.fromisoformat(start_date) >= date.fromisoformat(draft_date):
            return False
    run_tool_script(
        target_game_id,
        "free_agency_processor.py",
        [
            "start",
            "--league-year",
            str(draft_year),
            "--start-date",
            start_date,
            "--cpu-resign-per-team",
            "2",
            "--cpu-retention-per-team",
            "1",
            "--opening-cpu-offers",
            "112",
            "--cpu-controls-user-team",
            "--no-cap-snapshot",
            "--apply",
        ],
    )
    return True


def process_offseason_free_agency_to_draft(game_id: str | None, draft_year: int, draft_date: str, base_cpu_offers: int) -> None:
    target_game_id, db_path = save_db(game_id)
    while True:
        sim_control.raise_if_cancelled(db_path, "before offseason free agency processing.")
        with game_flow.connect(db_path) as con:
            if not table_exists(con, "free_agency_periods"):
                return
            period = con.execute(
                """
                SELECT *
                FROM free_agency_periods
                WHERE league_year = ?
                  AND status = 'active'
                """,
                (draft_year,),
            ).fetchone()
            if not period:
                return
            current_date = str(period["current_date"])
            if current_date >= draft_date:
                return
            current_stage = str(period["current_stage"] or "")
            current_hour = int(period["current_hour"] or 12)
            end_hour = int(period["first_day_end_hour"] or 20)
            remaining_days = max(0, (date.fromisoformat(draft_date) - date.fromisoformat(current_date)).days)

        if current_stage == "day_one_hourly" and current_hour < end_hour:
            sim_control.raise_if_cancelled(db_path, "before advancing free agency hour.")
            run_tool_script(
                target_game_id,
                "free_agency_processor.py",
                [
                    "advance-hour",
                    "--league-year",
                    str(draft_year),
                    "--cpu-offers",
                    str(max(32, min(64, base_cpu_offers))),
                    "--signing-limit",
                    "24",
                    "--cpu-controls-user-team",
                    "--no-cap-snapshot",
                    "--apply",
                ],
            )
            continue

        if remaining_days <= 0:
            return
        if remaining_days <= 3:
            step_days = remaining_days
            cpu_offers = 24
            signing_limit = 32
        elif remaining_days <= 10:
            step_days = min(3, remaining_days)
            cpu_offers = 36
            signing_limit = 40
        elif remaining_days <= 24:
            step_days = min(7, remaining_days)
            cpu_offers = 30
            signing_limit = 40
        else:
            step_days = min(7, remaining_days)
            cpu_offers = 44 if remaining_days > 35 else 34
            signing_limit = 48
        sim_control.raise_if_cancelled(db_path, "before advancing free agency days.")
        run_tool_script(
            target_game_id,
            "free_agency_processor.py",
            [
                "advance-day",
                "--league-year",
                str(draft_year),
                "--days",
                str(step_days),
                "--cpu-offers",
                str(cpu_offers),
                "--signing-limit",
                str(signing_limit),
                "--force",
                "--cpu-controls-user-team",
                "--no-cap-snapshot",
                "--apply",
            ],
        )


def draft_room_already_started(con, draft_year: int) -> bool:
    if not table_exists(con, "draft_room_state"):
        return False
    row = con.execute(
        """
        SELECT status
        FROM draft_room_state
        WHERE draft_year = ?
        """,
        (draft_year,),
    ).fetchone()
    return bool(row and str(row["status"] or "").lower() in {"active", "paused", "complete"})


def action_new(args: argparse.Namespace) -> None:
    create_args = SimpleNamespace(
        master_db=args.master_db,
        game_id=args.game_id,
        name=args.name,
        user_team=args.user_team,
        start_year=args.start_year,
        calendar_years=args.calendar_years,
        seed=args.seed,
        notes=args.notes,
        no_variance=args.no_variance,
        no_personality_variance=args.no_personality_variance,
        no_development_modifiers=args.no_development_modifiers,
        no_draft_class_generation=args.no_draft_class_generation,
        draft_class_count=args.draft_class_count,
        draft_hidden_count=args.draft_hidden_count,
        no_hidden_draft_prospects=args.no_hidden_draft_prospects,
        draft_class_strength=args.draft_class_strength,
        no_activate=args.no_activate,
        rating_max_delta=args.rating_max_delta,
        rookie_potential_max_delta=args.rookie_potential_max_delta,
        young_potential_max_delta=args.young_potential_max_delta,
        young_age_cutoff=args.young_age_cutoff,
    )
    save_manager.create_save(create_args)


def action_saves(args: argparse.Namespace) -> None:
    save_manager.list_saves(args)


def action_load(args: argparse.Namespace) -> None:
    save_manager.load_save(args)


def action_active(args: argparse.Namespace) -> None:
    save_manager.print_active(args)


def action_status(args: argparse.Namespace) -> None:
    game_id, _db_path, con = open_save_db(args.game_id)
    try:
        game_flow.status(con, SimpleNamespace(limit=args.limit))
    finally:
        con.close()
    sync_save(game_id)


def action_events(args: argparse.Namespace) -> None:
    game_id, _db_path, con = open_save_db(args.game_id)
    try:
        game_flow.action_events(con, SimpleNamespace(limit=args.limit))
    finally:
        con.close()
    sync_save(game_id)


def action_log(args: argparse.Namespace) -> None:
    game_id, _db_path, con = open_save_db(args.game_id)
    try:
        game_flow.action_log(con, SimpleNamespace(limit=args.limit))
    finally:
        con.close()
    sync_save(game_id)


def action_advance_day(args: argparse.Namespace) -> None:
    args.game_id = sync_active_game_row_to_settings(args.game_id)
    game_id, db_path = save_db(args.game_id)
    with game_flow.connect(db_path) as con:
        game = game_flow.active_game(con)
        if not game:
            raise ValueError("No active game. Run start first.")
        target_date = (date.fromisoformat(str(game.current_date)) + timedelta(days=args.days)).isoformat()
        target_date, gate_message = gated_calendar_target(
            con,
            game,
            target_date,
            auto_roster_cutdown=args.auto_roster_cutdown,
        )
    if gate_message:
        print(gate_message)
    auto_top30_before_calendar_advance(game_id, target_date)
    finish_draft_before_calendar_advance(game_id, target_date)
    game_id, _db_path, con = open_save_db(game_id)
    try:
        game_flow.action_advance_to_date(con, SimpleNamespace(date=target_date))
    finally:
        con.close()
    apply_cpu_roster_cutdowns_if_due(game_id, target_date, include_user_team=args.auto_roster_cutdown)
    sync_save(game_id)


def action_advance_to_next_event(args: argparse.Namespace) -> None:
    args.game_id = sync_active_game_row_to_settings(args.game_id)
    game_id, db_path = save_db(args.game_id)
    with game_flow.connect(db_path) as con:
        game = game_flow.active_game(con)
        if not game:
            raise ValueError("No active game. Run start first.")
        events = game_flow.upcoming_events(con, game.current_date, limit=1, strict=True)
        if not events:
            raise ValueError("No future calendar events found.")
        target_date = str(events[0]["event_start_date"])
        target_date, gate_message = gated_calendar_target(
            con,
            game,
            target_date,
            auto_roster_cutdown=args.auto_roster_cutdown,
        )
    if gate_message:
        print(gate_message)
    auto_top30_before_calendar_advance(game_id, target_date)
    finish_draft_before_calendar_advance(game_id, target_date)
    game_id, _db_path, con = open_save_db(game_id)
    try:
        game_flow.action_advance_to_date(con, SimpleNamespace(date=target_date))
    finally:
        con.close()
    apply_cpu_roster_cutdowns_if_due(game_id, target_date, include_user_team=args.auto_roster_cutdown)
    sync_save(game_id)


def action_advance_to_date(args: argparse.Namespace) -> None:
    args.game_id = sync_active_game_row_to_settings(args.game_id)
    game_id, db_path = save_db(args.game_id)
    with game_flow.connect(db_path) as con:
        game = game_flow.active_game(con)
        if not game:
            raise ValueError("No active game. Run start first.")
        target_date, gate_message = gated_calendar_target(
            con,
            game,
            args.date,
            auto_roster_cutdown=args.auto_roster_cutdown,
        )
    if gate_message:
        print(gate_message)
    auto_top30_before_calendar_advance(game_id, target_date)
    finish_draft_before_calendar_advance(game_id, target_date)
    game_id, _db_path, con = open_save_db(args.game_id)
    try:
        game_flow.action_advance_to_date(con, SimpleNamespace(date=target_date))
    finally:
        con.close()
    apply_cpu_roster_cutdowns_if_due(game_id, target_date, include_user_team=args.auto_roster_cutdown)
    sync_save(game_id)


def action_advance_to_next_league_year(args: argparse.Namespace) -> None:
    args.game_id = sync_active_game_row_to_settings(args.game_id)
    target_game_id, db_path = save_db(args.game_id)
    with game_flow.connect(db_path) as con:
        game = game_flow.active_game(con)
        if not game:
            raise ValueError("No active game. Run start first.")
        target_year, target_date, event_name = next_league_year_start(con)
        current_date = str(game.current_date)
        gated_target, gate_message = gated_calendar_target(
            con,
            game,
            target_date,
            auto_roster_cutdown=args.auto_roster_cutdown,
        )

    if gate_message:
        print(gate_message)
    if gated_target != target_date:
        auto_top30_before_calendar_advance(target_game_id, gated_target)
        finish_draft_before_calendar_advance(target_game_id, gated_target)
        game_id, _db_path, con = open_save_db(target_game_id)
        try:
            game_flow.action_advance_to_date(con, SimpleNamespace(date=gated_target))
        finally:
            con.close()
        apply_cpu_roster_cutdowns_if_due(target_game_id, gated_target, include_user_team=args.auto_roster_cutdown)
        sync_save(target_game_id)
        return

    auto_top30_before_calendar_advance(target_game_id, target_date, draft_year=target_year)
    finish_draft_before_calendar_advance(target_game_id, target_date)
    ensure_progression_for_league_year(target_game_id, target_year)

    game_id, _db_path, con = open_save_db(target_game_id)
    try:
        current_date = active_game_current_date(con)
        if date.fromisoformat(target_date) <= date.fromisoformat(current_date):
            print(f"Already at or past {event_name} ({target_date}).")
        else:
            game_flow.action_advance_to_date(con, SimpleNamespace(date=target_date))
            print(f"Advanced {target_game_id} from {current_date} to {event_name} ({target_date}).")
    finally:
        con.close()
    apply_cpu_roster_cutdowns_if_due(target_game_id, target_date, include_user_team=args.auto_roster_cutdown)
    sync_save(game_id)


def action_advance_to_draft(args: argparse.Namespace) -> None:
    args.game_id = sync_active_game_row_to_settings(args.game_id)
    target_game_id, db_path = save_db(args.game_id)
    with game_flow.connect(db_path) as con:
        settings_row = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = 'current_season'"
        ).fetchone()
        current_season = int(settings_row["setting_value"]) if settings_row else None
        draft_year = int(args.draft_year or next_draft_year(con, current_season))
        draft_date = draft_event_date(con, draft_year)
        user_team = (args.user_team or active_user_team(con) or "MIN").upper()

    ensure_regular_season_complete_before_draft(
        args.game_id,
        season=draft_year - 1,
        auto_roster_cutdown=args.auto_roster_cutdown,
    )
    ensure_final_draft_order(args.game_id, season=draft_year - 1, draft_year=draft_year)
    ensure_postseason_progression_for_league_year(args.game_id, draft_year)

    if not args.no_resolve_free_agency:
        opened_fa = ensure_offseason_free_agency_started(args.game_id, draft_year, draft_date)
        if opened_fa:
            print(f"Opened {draft_year} offseason free agency before advancing to the draft.")
        process_offseason_free_agency_to_draft(
            args.game_id,
            draft_year,
            draft_date,
            max(28, int(args.cpu_offers or 40)),
        )

    auto_top30_before_calendar_advance(target_game_id, draft_date, draft_year)
    with game_flow.connect(db_path) as con:
        result = scouting.run_pre_draft_public_scouting_sweep(
            con,
            game_id=target_game_id,
            draft_year=draft_year,
            seed=f"{target_game_id}:{draft_year}:advance-to-draft",
        )
        con.commit()
    if result.get("already_run"):
        print(f"Pre-draft scouting sweep already completed for {draft_year}.")
    else:
        print(
            f"Pre-draft scouting sweep: {result['user_updates']} user file(s), "
            f"{result['cpu_updates']} CPU-team file(s) moved from Low to Medium."
        )

    game_id, _db_path, con = open_save_db(args.game_id)
    try:
        current_date = active_game_current_date(con)
        if date.fromisoformat(draft_date) > date.fromisoformat(current_date):
            game_flow.action_advance_to_date(con, SimpleNamespace(date=draft_date))
        else:
            print(f"Already at or past the {draft_year} draft date ({draft_date}).")
    finally:
        con.close()
    sync_save(game_id)

    room_started = False
    with game_flow.connect(db_path) as con:
        room_started = draft_room_already_started(con, draft_year)

    if not args.no_start_room and not room_started:
        run_tool_script(
            args.game_id,
            "draft_room.py",
            [
                "start",
                "--draft-year",
                str(draft_year),
                "--user-team",
                user_team,
                "--paused",
                "--apply",
            ],
        )
    elif room_started:
        print(f"Draft room already started for {draft_year}.")
    print(f"Advanced {target_game_id} to the {draft_year} NFL Draft ({draft_date}).")


def action_validate_rosters(args: argparse.Namespace) -> None:
    args.game_id = sync_active_game_row_to_settings(args.game_id)
    game_id, _db_path, con = open_save_db(args.game_id)
    try:
        game_flow.action_validate_rosters(
            con,
            SimpleNamespace(
                summary_only=args.summary_only,
                include_info=args.include_info,
                no_save=args.no_save,
            ),
        )
    finally:
        con.close()
    sync_save(game_id)


def action_process_today(args: argparse.Namespace) -> None:
    game_id, _db_path, con = open_save_db(args.game_id)
    try:
        target_date = daily_processor.current_game_date(con)
        result = daily_processor.process_range(
            con,
            game_id=game_id,
            from_date=target_date,
            to_date=target_date,
            include_start=True,
            force=args.force,
        )
        con.commit()
        daily_processor.print_range_result(result)
    finally:
        con.close()
    sync_save(game_id)


def action_process_events(args: argparse.Namespace) -> None:
    game_id, _db_path, con = open_save_db(args.game_id)
    try:
        to_date = args.to_date or daily_processor.current_game_date(con)
        from_date = args.from_date or to_date
        result = daily_processor.process_event_range(
            con,
            game_id=game_id,
            from_date=from_date,
            to_date=to_date,
            include_start=args.include_start or from_date == to_date,
            force=args.force,
        )
        con.commit()
        daily_processor.print_event_range_result(result)
    finally:
        con.close()
    sync_save(game_id)


def action_alerts(args: argparse.Namespace) -> None:
    game_id, _db_path, con = open_save_db(args.game_id)
    try:
        daily_processor.print_alerts(daily_processor.open_alerts(con, game_id, args.limit))
    finally:
        con.close()
    sync_save(game_id)


def action_view_team(args: argparse.Namespace) -> None:
    game_id, db_path = save_db(args.game_id)
    view_team.DB_PATH = str(db_path)
    view_team.view_team(args.team)
    sync_save(game_id)


def run_tool_script(game_id: str | None, script_name: str, script_args: list[str]) -> None:
    target_game_id, db_path = save_db(game_id)
    command = [sys.executable, str(TOOLS_DIR / script_name), "--db", str(db_path), *script_args]
    subprocess.run(command, check=True)
    sync_save(target_game_id)


def action_cap(args: argparse.Namespace) -> None:
    script_args = ["cap"]
    if args.team:
        script_args.extend(["--team", args.team])
    run_tool_script(args.game_id, "roster_actions.py", script_args)


def action_find_player(args: argparse.Namespace) -> None:
    run_tool_script(args.game_id, "roster_actions.py", ["find-player", args.player])


def action_roster(args: argparse.Namespace) -> None:
    if not args.roster_args:
        raise ValueError("Provide a roster action, for example: roster sign-fa --player ...")
    run_tool_script(args.game_id, "roster_actions.py", args.roster_args)


def action_roster_rules(args: argparse.Namespace) -> None:
    if not args.roster_rule_args:
        raise ValueError("Provide a roster-rules command, for example: roster-rules waiver-wire")
    run_tool_script(args.game_id, "roster_rules.py", args.roster_rule_args)


def action_depth_chart(args: argparse.Namespace) -> None:
    if not args.depth_chart_args:
        raise ValueError("Provide a depth-chart command, for example: depth-chart show --team MIN")
    run_tool_script(args.game_id, "depth_chart.py", args.depth_chart_args)


def action_cpu_depth_chart(args: argparse.Namespace) -> None:
    if not args.cpu_depth_chart_args:
        raise ValueError("Provide a CPU depth-chart command, for example: cpu-depth-chart audit")
    run_tool_script(args.game_id, "cpu_depth_chart.py", args.cpu_depth_chart_args)


def action_roster_cutdown(args: argparse.Namespace) -> None:
    target_game_id, db_path = save_db(args.game_id)
    script_args = ["run", "--game-id", target_game_id]
    if args.team:
        script_args.extend(["--team", args.team])
    if args.season is not None:
        script_args.extend(["--season", str(args.season)])
    if args.active_limit is not None:
        script_args.extend(["--active-limit", str(args.active_limit)])
    if args.practice_squad_limit is not None:
        script_args.extend(["--practice-squad-limit", str(args.practice_squad_limit)])
    if args.apply:
        script_args.append("--apply")
    if args.no_backup:
        script_args.append("--no-backup")
    if args.no_validation_save:
        script_args.append("--no-validation-save")
    command = [sys.executable, str(TOOLS_DIR / "roster_cutdown.py"), "--db", str(db_path), *script_args]
    subprocess.run(command, check=True)
    sync_save(target_game_id)


def action_weekly_hooks(args: argparse.Namespace) -> None:
    script_args = ["process-week", str(args.week), "--season", str(args.season)]
    if args.apply:
        script_args.append("--apply")
    if args.force:
        script_args.append("--force")
    if args.allow_incomplete:
        script_args.append("--allow-incomplete")
    if args.no_advance_date:
        script_args.append("--no-advance-date")
    if args.no_ai_gm:
        script_args.append("--no-ai-gm")
    run_tool_script(args.game_id, "weekly_processor.py", script_args)


def action_ai_gm(args: argparse.Namespace) -> None:
    if not args.ai_gm_args:
        raise ValueError("Provide an AI GM command, for example: ai-gm context --team MIN --decision-type trade_block_update")
    run_tool_script(args.game_id, "ai_gm.py", args.ai_gm_args)


def action_audit(args: argparse.Namespace) -> None:
    script_args = ["--season", str(args.season)]
    if args.team:
        script_args.extend(["--team", args.team])
    if args.strict:
        script_args.append("--strict")
    run_tool_script(args.game_id, "audit_database.py", script_args)


def action_schedule(args: argparse.Namespace) -> None:
    run_tool_script(
        args.game_id,
        "league_schedule.py",
        ["team", args.team, "--season", str(args.season)],
    )


def action_week(args: argparse.Namespace) -> None:
    run_tool_script(
        args.game_id,
        "league_schedule.py",
        ["week", str(args.week), "--season", str(args.season)],
    )


def action_sim_matchup(args: argparse.Namespace) -> None:
    script_args = ["matchup", args.away, args.home, "--season", str(args.season)]
    if args.week is not None:
        script_args.extend(["--week", str(args.week)])
    if args.seed is not None:
        script_args.extend(["--seed", str(args.seed)])
    script_args.extend(["--show-plays", str(args.show_plays)])
    if args.box:
        script_args.append("--box")
    run_tool_script(args.game_id, "sim_game.py", script_args)


def action_sim_game(args: argparse.Namespace) -> None:
    script_args = ["game", str(args.schedule_game_id), "--show-plays", str(args.show_plays)]
    if args.seed is not None:
        script_args.extend(["--seed", str(args.seed)])
    if args.apply:
        script_args.append("--apply")
    if args.force:
        script_args.append("--force")
    if args.notes:
        script_args.extend(["--notes", args.notes])
    if args.box:
        script_args.append("--box")
    run_tool_script(args.game_id, "sim_game.py", script_args)


def action_sim_audit(args: argparse.Namespace) -> None:
    script_args = ["--season", str(args.season), "--games", str(args.games), "--seed", str(args.seed)]
    if args.team:
        script_args.extend(["--team", args.team])
    if args.week is not None:
        script_args.extend(["--week", str(args.week)])
    if args.matchup:
        script_args.extend(["--matchup", *args.matchup])
    if args.progress_every is not None:
        script_args.extend(["--progress-every", str(args.progress_every)])
    if args.json:
        script_args.extend(["--json", str(args.json)])
    if args.csv:
        script_args.extend(["--csv", str(args.csv)])
    if args.strict:
        script_args.append("--strict")
    run_tool_script(args.game_id, "sim_audit.py", script_args)


def action_tick_playtest(args: argparse.Namespace) -> None:
    script_args = [
        args.away,
        args.home,
        "--season",
        str(args.season),
        "--down",
        str(args.down),
        "--distance",
        str(args.distance),
        "--field-pos",
        str(args.field_pos),
    ]
    if args.concept:
        script_args.extend(["--concept", args.concept])
    if args.seed is not None:
        script_args.extend(["--seed", str(args.seed)])
    if args.debug_ticks:
        script_args.append("--debug-ticks")
    if not args.events:
        script_args.append("--no-events")
    if not args.routes:
        script_args.append("--no-routes")
    if args.json:
        script_args.extend(["--json", str(args.json)])
    run_tool_script(args.game_id, "tick_playtest.py", script_args)


def action_manual_playtest(args: argparse.Namespace) -> None:
    target_game_id, db_path = save_db(args.game_id)
    script_args = ["--save-id", target_game_id, "--team", args.team]
    if args.opponent:
        script_args.extend(["--opponent", args.opponent])
    if args.schedule_game_id is not None:
        script_args.extend(["--schedule-game-id", str(args.schedule_game_id)])
    if args.season is not None:
        script_args.extend(["--season", str(args.season)])
    if args.week is not None:
        script_args.extend(["--week", str(args.week)])
    if args.seed is not None:
        script_args.extend(["--seed", str(args.seed)])
    if args.log_root:
        script_args.extend(["--log-root", args.log_root])
    if args.auto:
        script_args.append("--auto")
    if args.pause_defense:
        script_args.append("--pause-defense")
    if args.apply:
        script_args.append("--apply")
    if args.force:
        script_args.append("--force")
    if args.notes:
        script_args.extend(["--notes", args.notes])
    command = [sys.executable, str(TOOLS_DIR / "manual_playthrough.py"), "--db", str(db_path), *script_args]
    subprocess.run(command, check=True)
    if args.apply:
        sync_save(target_game_id)


def action_playtest_logs(args: argparse.Namespace) -> None:
    if not args.playtest_log_args:
        raise ValueError("Provide a playtest log command, for example: playtest-logs latest")
    command = [sys.executable, str(TOOLS_DIR / "playtest_logs.py"), *args.playtest_log_args]
    subprocess.run(command, check=True)


def action_sim_week(args: argparse.Namespace) -> None:
    args.game_id = sync_active_game_row_to_settings(args.game_id)
    game_type = str(getattr(args, "game_type", "REG") or "REG").upper()
    if args.apply:
        if game_type == "REG" and not args.skip_roster_gate and stop_for_user_roster_cutdown_if_due(args.game_id, args.season):
            return
        if game_type == "REG":
            ensure_regular_season_rosters(args.game_id, args.season)
            ensure_regular_season_specialists(args.game_id, args.season)
            ensure_cpu_depth_charts(args.game_id, args.season)
    script_args = ["week", str(args.week), "--season", str(args.season), "--game-type", game_type]
    if args.seed is not None:
        script_args.extend(["--seed", str(args.seed)])
    if args.limit is not None:
        script_args.extend(["--limit", str(args.limit)])
    if args.apply:
        script_args.append("--apply")
    if args.force:
        script_args.append("--force")
    if args.notes:
        script_args.extend(["--notes", args.notes])
    if not args.weekly_hooks:
        script_args.append("--no-weekly-hooks")
    run_tool_script(args.game_id, "sim_game.py", script_args)
    if args.apply and args.limit is None and game_type == "REG":
        ensure_playoff_tree_if_ready(args.game_id, args.season)


def action_sim_season(args: argparse.Namespace) -> None:
    args.game_id = sync_active_game_row_to_settings(args.game_id)
    game_type = str(getattr(args, "game_type", "REG") or "REG").upper()
    if args.apply:
        if game_type == "REG" and not args.skip_roster_gate and stop_for_user_roster_cutdown_if_due(args.game_id, args.season):
            return
        if game_type == "REG":
            ensure_regular_season_rosters(args.game_id, args.season)
            ensure_regular_season_specialists(args.game_id, args.season)
            ensure_cpu_depth_charts(args.game_id, args.season)
    script_args = ["season", "--season", str(args.season), "--game-type", game_type]
    if args.start_week is not None:
        script_args.extend(["--start-week", str(args.start_week)])
    if args.end_week is not None:
        script_args.extend(["--end-week", str(args.end_week)])
    if args.seed is not None:
        script_args.extend(["--seed", str(args.seed)])
    if args.limit is not None:
        script_args.extend(["--limit", str(args.limit)])
    if args.apply:
        script_args.append("--apply")
    if args.force:
        script_args.append("--force")
    if args.notes:
        script_args.extend(["--notes", args.notes])
    if not args.weekly_hooks:
        script_args.append("--no-weekly-hooks")
    run_tool_script(args.game_id, "sim_game.py", script_args)
    if args.apply and args.limit is None and game_type == "REG":
        ensure_playoff_tree_if_ready(args.game_id, args.season)


def action_trade(args: argparse.Namespace) -> None:
    if not args.trade_args:
        raise ValueError("Provide a trade command, for example: trade setup")
    run_tool_script(args.game_id, "trade_engine.py", args.trade_args)


def action_history(args: argparse.Namespace) -> None:
    if not args.history_args:
        raise ValueError("Provide a history command, for example: history standings --season 2026")
    run_tool_script(args.game_id, "stat_history.py", args.history_args)


def action_personalities(args: argparse.Namespace) -> None:
    if not args.personality_args:
        raise ValueError("Provide a personalities command, for example: personalities summary --game-id active")
    target_game_id, db_path = save_db(args.game_id)
    script_args = list(args.personality_args)
    if script_args[0] in {"apply", "summary", "show"} and "--game-id" not in script_args:
        script_args = [script_args[0], "--game-id", target_game_id, *script_args[1:]]
    command = [sys.executable, str(TOOLS_DIR / "player_personalities.py"), "--db", str(db_path), *script_args]
    subprocess.run(command, check=True)
    sync_save(target_game_id)


def action_scouting(args: argparse.Namespace) -> None:
    if not args.scouting_args:
        raise ValueError("Provide a scouting command, for example: scouting board --limit 25")
    target_game_id, db_path = save_db(args.game_id)
    command = [sys.executable, str(TOOLS_DIR / "scouting.py"), "--db", str(db_path), *args.scouting_args]
    subprocess.run(command, check=True)
    sync_save(target_game_id)


def action_league_news(args: argparse.Namespace) -> None:
    if not args.league_news_args:
        raise ValueError("Provide a league-news command, for example: league-news list --limit 25")
    target_game_id, db_path = save_db(args.game_id)
    command = [sys.executable, str(TOOLS_DIR / "league_news.py"), "--db", str(db_path), *args.league_news_args]
    subprocess.run(command, check=True)
    sync_save(target_game_id)


def action_event_gen(args: argparse.Namespace) -> None:
    if not args.event_gen_args:
        raise ValueError("Provide an event-gen command, for example: event-gen weekly --week 1 --apply")
    target_game_id, db_path = save_db(args.game_id)
    command = [sys.executable, str(TOOLS_DIR / "event_generator.py"), "--db", str(db_path), *args.event_gen_args]
    subprocess.run(command, check=True)
    sync_save(target_game_id)


def action_preflight(args: argparse.Namespace) -> None:
    target_game_id, db_path = save_db(args.game_id)
    command = [sys.executable, str(TOOLS_DIR / "preflight_check.py"), "--db", str(db_path)]
    if args.json:
        command.append("--json")
    if args.strict:
        command.append("--strict")
    subprocess.run(command, check=True)
    sync_save(target_game_id)


def action_schemes(args: argparse.Namespace) -> None:
    if not args.scheme_args:
        raise ValueError("Provide a schemes command, for example: schemes summary --team MIN")
    run_tool_script(args.game_id, "scheme_fits.py", args.scheme_args)


def action_progression(args: argparse.Namespace) -> None:
    if not args.progression_args:
        raise ValueError("Provide a progression command, for example: progression run --from-season 2026")
    target_game_id, db_path = save_db(args.game_id)
    script_args = list(args.progression_args)
    if script_args[0] in {"run", "summary", "show"} and "--game-id" not in script_args:
        script_args = [script_args[0], "--game-id", target_game_id, *script_args[1:]]
    command = [sys.executable, str(TOOLS_DIR / "player_progression.py"), "--db", str(db_path), *script_args]
    subprocess.run(command, check=True)
    sync_save(target_game_id)


def action_preseason(args: argparse.Namespace) -> None:
    if not args.preseason_args:
        raise ValueError("Provide preseason args, for example: preseason event --event-code PRESEASON_WEEK_1 --season 2026 --event-date 2026-08-13 --apply")
    target_game_id, db_path = save_db(args.game_id)
    script_args = list(args.preseason_args)
    if script_args and script_args[0] == "event":
        if "--game-id" not in script_args:
            script_args.extend(["--game-id", target_game_id])
    command = [sys.executable, str(TOOLS_DIR / "preseason_processor.py"), "--db", str(db_path), *script_args]
    subprocess.run(command, check=True)
    sync_save(target_game_id)


def action_draft(args: argparse.Namespace) -> None:
    if not args.draft_args:
        raise ValueError("Provide draft generator args, for example: draft --year 2027 --apply")
    run_tool_script(args.game_id, "generate_draft_class.py", args.draft_args)


def action_validate_draft(args: argparse.Namespace) -> None:
    if not args.validate_draft_args:
        raise ValueError("Provide validation args, for example: validate-draft db --draft-year 2027")
    run_tool_script(args.game_id, "validate_draft_class.py", args.validate_draft_args)


def action_draft_select(args: argparse.Namespace) -> None:
    if not args.draft_select_args:
        raise ValueError("Provide selection args, for example: draft-select select --draft-year 2027 --pick-id 673 --prospect-id 1 --apply")
    run_tool_script(args.game_id, "select_draft_pick.py", args.draft_select_args)


def action_draft_room(args: argparse.Namespace) -> None:
    if not args.draft_room_args:
        raise ValueError("Provide draft-room args, for example: draft-room status --draft-year 2027")
    room_args = list(args.draft_room_args)
    if room_args and room_args[0] == "start":
        draft_year = None
        if "--draft-year" in room_args:
            index = room_args.index("--draft-year")
            if index + 1 < len(room_args):
                draft_year = int(room_args[index + 1])
        game_id, db_path = save_db(args.game_id)
        with game_flow.connect(db_path) as con:
            current_season = current_season_setting(con)
            target_year = draft_year or next_draft_year(con, current_season)
            target_date = draft_event_date(con, target_year)
        auto_top30_before_calendar_advance(game_id, target_date, target_year)
    run_tool_script(args.game_id, "draft_room.py", args.draft_room_args)


def action_draft_portraits(args: argparse.Namespace) -> None:
    if not args.draft_portrait_args:
        raise ValueError("Provide portrait args, for example: draft-portraits summary --run-id draft_2027_portraits")
    command = [sys.executable, str(TOOLS_DIR / "generate_draft_portraits.py"), *args.draft_portrait_args]
    subprocess.run(command, check=True)


def action_free_agency(args: argparse.Namespace) -> None:
    if not args.free_agency_args:
        raise ValueError("Provide free-agency args, for example: free-agency status --league-year 2027")
    maybe_ensure_progression_before_free_agency(args.game_id, list(args.free_agency_args))
    run_tool_script(args.game_id, "free_agency_processor.py", args.free_agency_args)


def action_contract(args: argparse.Namespace) -> None:
    if not args.contract_args:
        raise ValueError("Provide contract args, for example: contract list --season 2026 --team MIN")
    maybe_ensure_progression_before_contract_talks(args.game_id, list(args.contract_args))
    run_tool_script(args.game_id, "contract_negotiations.py", args.contract_args)


def action_postseason(args: argparse.Namespace) -> None:
    if not args.postseason_args:
        raise ValueError("Provide a postseason command, for example: postseason run --season 2026 --apply")
    run_tool_script(args.game_id, "postseason.py", args.postseason_args)


def action_complete_season(args: argparse.Namespace) -> None:
    args.game_id = sync_active_game_row_to_settings(args.game_id)
    target_game_id, db_path = save_db(args.game_id)
    script_args = ["complete", "--season", str(args.season)]
    if args.seed is not None:
        script_args.extend(["--seed", str(args.seed)])
    if args.apply:
        script_args.append("--apply")
    if args.force_postseason:
        script_args.append("--force-postseason")
    if not args.seed_next_schedule:
        script_args.append("--no-seed-next-schedule")
    if not args.replace_next_schedule:
        script_args.append("--no-replace-next-schedule")
    if args.no_advance_date:
        script_args.append("--no-advance-date")
    if args.no_progression:
        script_args.append("--no-progression")
    if args.progression_seed is not None:
        script_args.extend(["--progression-seed", str(args.progression_seed)])
    if args.force_progression:
        script_args.append("--force-progression")
    if args.no_retirements:
        script_args.append("--no-retirements")
    if args.retirement_seed is not None:
        script_args.extend(["--retirement-seed", str(args.retirement_seed)])
    if args.force_retirements:
        script_args.append("--force-retirements")
    if args.process_days_on_advance:
        script_args.append("--process-days-on-advance")
    if args.notes:
        script_args.extend(["--notes", args.notes])
    command = [sys.executable, str(TOOLS_DIR / "season_rollover.py"), "--db", str(db_path), *script_args]
    subprocess.run(command, check=True)
    if args.apply:
        sync_save(target_game_id)


def add_save_selector(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--game-id", help="Use a specific save instead of the active save.")


def add_new_save_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--game-id", required=True)
    parser.add_argument("--name")
    parser.add_argument("--user-team")
    parser.add_argument("--start-year", type=int, default=game_flow.DEFAULT_START_YEAR)
    parser.add_argument("--calendar-years", type=int, default=game_flow.DEFAULT_CALENDAR_YEARS)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--notes")
    parser.add_argument("--no-variance", action="store_true")
    parser.add_argument("--no-personality-variance", action="store_true")
    parser.add_argument("--no-development-modifiers", action="store_true")
    parser.add_argument("--no-draft-class-generation", action="store_true")
    parser.add_argument("--draft-class-count", type=int, default=330)
    parser.add_argument("--draft-hidden-count", type=int)
    parser.add_argument("--no-hidden-draft-prospects", action="store_true")
    parser.add_argument("--draft-class-strength", type=int, default=50)
    parser.add_argument("--no-activate", action="store_true")
    parser.add_argument("--rating-max-delta", type=float, default=0.10)
    parser.add_argument("--rookie-potential-max-delta", type=float, default=0.25)
    parser.add_argument("--young-potential-max-delta", type=float, default=0.15)
    parser.add_argument("--young-age-cutoff", type=int, default=25)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Play NFL GM Sim using the active save.")
    parser.add_argument(
        "--master-db",
        type=Path,
        default=save_manager.MASTER_DB,
        help=f"Master DB path for new saves. Default: {save_manager.MASTER_DB}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_parser = subparsers.add_parser("new", help="Create a new isolated save and make it active.")
    add_new_save_args(new_parser)
    new_parser.set_defaults(func=action_new)

    saves_parser = subparsers.add_parser("saves", help="List saves.")
    saves_parser.set_defaults(func=action_saves)

    load_parser = subparsers.add_parser("load", help="Set a save active.")
    load_parser.add_argument("game_id")
    load_parser.set_defaults(func=lambda args: save_manager.load_save(SimpleNamespace(game_id=args.game_id)))

    delete_parser = subparsers.add_parser("delete-save", help="Delete a save and remove it from the registry.")
    delete_parser.add_argument("game_id")
    delete_parser.set_defaults(func=lambda args: save_manager.delete_save(SimpleNamespace(game_id=args.game_id)))

    active_parser = subparsers.add_parser("active", help="Show active save.")
    active_parser.set_defaults(func=action_active)

    status_parser = subparsers.add_parser("status", help="Show current save status.")
    add_save_selector(status_parser)
    status_parser.add_argument("--limit", type=int, default=8)
    status_parser.set_defaults(func=action_status)

    events_parser = subparsers.add_parser("events", help="Show upcoming calendar events.")
    add_save_selector(events_parser)
    events_parser.add_argument("--limit", type=int, default=12)
    events_parser.set_defaults(func=action_events)

    log_parser = subparsers.add_parser("log", help="Show recent game flow log.")
    add_save_selector(log_parser)
    log_parser.add_argument("--limit", type=int, default=20)
    log_parser.set_defaults(func=action_log)

    advance_parser = subparsers.add_parser("advance-day", help="Advance by N days.")
    add_save_selector(advance_parser)
    advance_parser.add_argument("--days", type=int, default=1)
    advance_parser.add_argument(
        "--auto-roster-cutdown",
        action="store_true",
        help="Automatically handle the user team's roster cutdown if this advance crosses cutdown day.",
    )
    advance_parser.set_defaults(func=action_advance_day)

    next_parser = subparsers.add_parser("advance-to-next-event", help="Advance to the next calendar event.")
    add_save_selector(next_parser)
    next_parser.add_argument(
        "--auto-roster-cutdown",
        action="store_true",
        help="Automatically handle the user team's roster cutdown if this advance crosses cutdown day.",
    )
    next_parser.set_defaults(func=action_advance_to_next_event)

    date_parser = subparsers.add_parser("advance-to-date", help="Advance to a specific date.")
    add_save_selector(date_parser)
    date_parser.add_argument("--date", required=True)
    date_parser.add_argument(
        "--auto-roster-cutdown",
        action="store_true",
        help="Automatically handle the user team's roster cutdown if this advance crosses cutdown day.",
    )
    date_parser.set_defaults(func=action_advance_to_date)

    next_year_parser = subparsers.add_parser("advance-to-next-league-year", help="Advance to the next June 1 sim league year.")
    add_save_selector(next_year_parser)
    next_year_parser.add_argument(
        "--auto-roster-cutdown",
        action="store_true",
        help="Automatically handle the user team's roster cutdown if this advance crosses cutdown day.",
    )
    next_year_parser.set_defaults(func=action_advance_to_next_league_year)

    draft_date_parser = subparsers.add_parser("advance-to-draft", help="Advance to the next draft event and open the draft room.")
    add_save_selector(draft_date_parser)
    draft_date_parser.add_argument("--draft-year", type=int, help="Draft year to advance to. Defaults to the next available draft class.")
    draft_date_parser.add_argument("--user-team", help="User-controlled team for draft room clock behavior.")
    draft_date_parser.add_argument("--cpu-offers", type=int, default=18, help="CPU offers to create while fast-forwarding active free agency.")
    draft_date_parser.add_argument(
        "--auto-roster-cutdown",
        action="store_true",
        help="Automatically handle the user team's roster cutdown if the regular season must be completed first.",
    )
    draft_date_parser.add_argument("--no-resolve-free-agency", action="store_true", help="Skip the free-agency fast-forward tick before the draft.")
    draft_date_parser.add_argument("--no-start-room", action="store_true", help="Only advance the calendar date; do not start the draft room.")
    draft_date_parser.set_defaults(func=action_advance_to_draft)

    validate_parser = subparsers.add_parser("validate-rosters", help="Validate rosters for the current phase.")
    add_save_selector(validate_parser)
    validate_parser.add_argument("--summary-only", action="store_true")
    validate_parser.add_argument("--include-info", action="store_true")
    validate_parser.add_argument("--no-save", action="store_true")
    validate_parser.set_defaults(func=action_validate_rosters)

    process_parser = subparsers.add_parser("process-today", help="Run full manual daily hooks for the current save date.")
    add_save_selector(process_parser)
    process_parser.add_argument("--force", action="store_true", help="Reprocess today even if a run exists.")
    process_parser.set_defaults(func=action_process_today)

    process_events_parser = subparsers.add_parser("process-events", help="Process calendar-event hooks without roster checks.")
    add_save_selector(process_events_parser)
    process_events_parser.add_argument("--from-date")
    process_events_parser.add_argument("--to-date")
    process_events_parser.add_argument("--include-start", action="store_true")
    process_events_parser.add_argument("--force", action="store_true")
    process_events_parser.add_argument("--apply", action="store_true", help=argparse.SUPPRESS)
    process_events_parser.set_defaults(func=action_process_events)

    alerts_parser = subparsers.add_parser("alerts", help="Show open gameplay alerts.")
    add_save_selector(alerts_parser)
    alerts_parser.add_argument("--limit", type=int, default=20)
    alerts_parser.set_defaults(func=action_alerts)

    view_parser = subparsers.add_parser("view-team", help="View a team from the active save.")
    add_save_selector(view_parser)
    view_parser.add_argument("team")
    view_parser.set_defaults(func=action_view_team)

    cap_parser = subparsers.add_parser("cap", help="Show cap summary from the active save.")
    add_save_selector(cap_parser)
    cap_parser.add_argument("--team")
    cap_parser.set_defaults(func=action_cap)

    find_parser = subparsers.add_parser("find-player", help="Find player by name substring in the active save.")
    add_save_selector(find_parser)
    find_parser.add_argument("player")
    find_parser.set_defaults(func=action_find_player)

    roster_parser = subparsers.add_parser("roster", help="Pass through to roster_actions.py for the active save.")
    add_save_selector(roster_parser)
    roster_parser.add_argument("roster_args", nargs=argparse.REMAINDER)
    roster_parser.set_defaults(func=action_roster)

    roster_rules_parser = subparsers.add_parser("roster-rules", help="Pass through to roster_rules.py for waiver/practice-squad tools.")
    add_save_selector(roster_rules_parser)
    roster_rules_parser.add_argument("roster_rule_args", nargs=argparse.REMAINDER)
    roster_rules_parser.set_defaults(func=action_roster_rules)

    depth_chart_parser = subparsers.add_parser("depth-chart", help="Show or edit the active-save depth chart.")
    add_save_selector(depth_chart_parser)
    depth_chart_parser.add_argument("depth_chart_args", nargs=argparse.REMAINDER)
    depth_chart_parser.set_defaults(func=action_depth_chart)

    cpu_depth_chart_parser = subparsers.add_parser("cpu-depth-chart", help="Audit or rebuild CPU depth charts.")
    add_save_selector(cpu_depth_chart_parser)
    cpu_depth_chart_parser.add_argument("cpu_depth_chart_args", nargs=argparse.REMAINDER)
    cpu_depth_chart_parser.set_defaults(func=action_cpu_depth_chart)

    cutdown_parser = subparsers.add_parser("roster-cutdown", help="Auto-trim rosters to a regular-season 53 and practice squad.")
    add_save_selector(cutdown_parser)
    cutdown_parser.add_argument("--team", help="Limit cutdown to one team abbreviation.")
    cutdown_parser.add_argument("--season", type=int, default=game_flow.DEFAULT_START_YEAR)
    cutdown_parser.add_argument("--active-limit", type=int)
    cutdown_parser.add_argument("--practice-squad-limit", type=int)
    cutdown_parser.add_argument("--apply", action="store_true", help="Persist changes. Without this, dry-runs.")
    cutdown_parser.add_argument("--no-backup", action="store_true")
    cutdown_parser.add_argument("--no-validation-save", action="store_true")
    cutdown_parser.set_defaults(func=action_roster_cutdown)

    weekly_hooks_parser = subparsers.add_parser("weekly-hooks", help="Run weekly event/roster hooks for a completed week.")
    add_save_selector(weekly_hooks_parser)
    weekly_hooks_parser.add_argument("week", type=int)
    weekly_hooks_parser.add_argument("--season", type=int, default=game_flow.DEFAULT_START_YEAR)
    weekly_hooks_parser.add_argument("--apply", action="store_true")
    weekly_hooks_parser.add_argument("--force", action="store_true")
    weekly_hooks_parser.add_argument("--allow-incomplete", action="store_true")
    weekly_hooks_parser.add_argument("--no-advance-date", action="store_true")
    weekly_hooks_parser.add_argument("--no-ai-gm", action="store_true", help="Skip AI GM weekly enqueue hook.")
    weekly_hooks_parser.set_defaults(func=action_weekly_hooks)

    ai_gm_parser = subparsers.add_parser("ai-gm", help="Pass through to ai_gm.py for the active save.")
    add_save_selector(ai_gm_parser)
    ai_gm_parser.add_argument("ai_gm_args", nargs=argparse.REMAINDER)
    ai_gm_parser.set_defaults(func=action_ai_gm)

    audit_parser = subparsers.add_parser("audit", help="Audit the active save database.")
    add_save_selector(audit_parser)
    audit_parser.add_argument("--team", help="Limit audit to one team abbreviation.")
    audit_parser.add_argument("--season", type=int, default=game_flow.DEFAULT_START_YEAR)
    audit_parser.add_argument("--strict", action="store_true", help="Exit non-zero if errors are found.")
    audit_parser.set_defaults(func=action_audit)

    schedule_parser = subparsers.add_parser("schedule", help="Show a team's regular-season schedule.")
    add_save_selector(schedule_parser)
    schedule_parser.add_argument("team")
    schedule_parser.add_argument("--season", type=int, default=game_flow.DEFAULT_START_YEAR)
    schedule_parser.set_defaults(func=action_schedule)

    week_parser = subparsers.add_parser("week", help="Show a regular-season week schedule.")
    add_save_selector(week_parser)
    week_parser.add_argument("week", type=int)
    week_parser.add_argument("--season", type=int, default=game_flow.DEFAULT_START_YEAR)
    week_parser.set_defaults(func=action_week)

    sim_matchup_parser = subparsers.add_parser("sim-matchup", help="Dry-run a matchup in the active save.")
    add_save_selector(sim_matchup_parser)
    sim_matchup_parser.add_argument("away")
    sim_matchup_parser.add_argument("home")
    sim_matchup_parser.add_argument("--season", type=int, default=game_flow.DEFAULT_START_YEAR)
    sim_matchup_parser.add_argument("--week", type=int)
    sim_matchup_parser.add_argument("--seed", type=int)
    sim_matchup_parser.add_argument("--show-plays", type=int, default=16)
    sim_matchup_parser.add_argument("--box", action="store_true", help="Include a player box score.")
    sim_matchup_parser.set_defaults(func=action_sim_matchup)

    sim_game_parser = subparsers.add_parser("sim-game", help="Simulate a scheduled game id in the active save.")
    add_save_selector(sim_game_parser)
    sim_game_parser.add_argument("schedule_game_id", type=int)
    sim_game_parser.add_argument("--seed", type=int)
    sim_game_parser.add_argument("--apply", action="store_true")
    sim_game_parser.add_argument("--force", action="store_true")
    sim_game_parser.add_argument("--notes")
    sim_game_parser.add_argument("--show-plays", type=int, default=16)
    sim_game_parser.add_argument("--box", action="store_true", help="Include a player box score.")
    sim_game_parser.set_defaults(func=action_sim_game)

    sim_audit_parser = subparsers.add_parser("sim-audit", help="Dry-run a match-engine realism audit in the active save.")
    add_save_selector(sim_audit_parser)
    sim_audit_parser.add_argument("--season", type=int, default=game_flow.DEFAULT_START_YEAR)
    sim_audit_parser.add_argument("--games", type=int, default=100)
    sim_audit_parser.add_argument("--seed", type=int, default=3000)
    sim_audit_parser.add_argument("--team", help="Sample scheduled games involving this team abbreviation.")
    sim_audit_parser.add_argument("--week", type=int, help="Sample scheduled games from one regular-season week.")
    sim_audit_parser.add_argument("--matchup", nargs=2, metavar=("AWAY", "HOME"), help="Repeat one explicit matchup instead of sampling the schedule.")
    sim_audit_parser.add_argument("--progress-every", type=int, default=0)
    sim_audit_parser.add_argument("--json", type=Path)
    sim_audit_parser.add_argument("--csv", type=Path)
    sim_audit_parser.add_argument("--strict", action="store_true")
    sim_audit_parser.set_defaults(func=action_sim_audit)

    tick_playtest_parser = subparsers.add_parser("tick-playtest", help="Dry-run one prototype tick-resolved pass play.")
    add_save_selector(tick_playtest_parser)
    tick_playtest_parser.add_argument("away")
    tick_playtest_parser.add_argument("home")
    tick_playtest_parser.add_argument("--season", type=int, default=game_flow.DEFAULT_START_YEAR)
    tick_playtest_parser.add_argument("--down", type=int, default=1)
    tick_playtest_parser.add_argument("--distance", type=int, default=10)
    tick_playtest_parser.add_argument("--field-pos", type=int, default=25)
    tick_playtest_parser.add_argument("--concept", choices=("screen", "quick", "short", "intermediate", "deep"))
    tick_playtest_parser.add_argument("--seed", type=int)
    tick_playtest_parser.add_argument("--debug-ticks", action="store_true")
    tick_playtest_parser.add_argument("--events", action=argparse.BooleanOptionalAction, default=True)
    tick_playtest_parser.add_argument("--routes", action=argparse.BooleanOptionalAction, default=True)
    tick_playtest_parser.add_argument("--json", type=Path)
    tick_playtest_parser.set_defaults(func=action_tick_playtest)

    manual_parser = subparsers.add_parser("manual-playtest", help="Manual play-through tester with a log bundle.")
    add_save_selector(manual_parser)
    manual_parser.add_argument("--team", default="MIN", help="Team controlled manually. Default: MIN.")
    manual_parser.add_argument("--opponent", help="Optional opponent abbreviation. Defaults to next scheduled game.")
    manual_parser.add_argument("--schedule-game-id", type=int, help="Specific scheduled game to test.")
    manual_parser.add_argument("--season", type=int, default=game_flow.DEFAULT_START_YEAR)
    manual_parser.add_argument("--week", type=int)
    manual_parser.add_argument("--seed", type=int)
    manual_parser.add_argument("--log-root", help="Override log output folder.")
    manual_parser.add_argument("--auto", action="store_true", help="CPU-run this playtest while still writing logs.")
    manual_parser.add_argument("--pause-defense", action="store_true", help="Pause before opponent snaps while your team is on defense.")
    manual_parser.add_argument("--apply", action="store_true", help="Save the completed result to the season.")
    manual_parser.add_argument("--force", action="store_true", help="Allow testing an already-played scheduled game.")
    manual_parser.add_argument("--notes")
    manual_parser.set_defaults(func=action_manual_playtest)

    playtest_logs_parser = subparsers.add_parser("playtest-logs", help="List or bundle manual playtest log folders.")
    playtest_logs_parser.add_argument("playtest_log_args", nargs=argparse.REMAINDER)
    playtest_logs_parser.set_defaults(func=action_playtest_logs)

    sim_week_parser = subparsers.add_parser("sim-week", help="Simulate every unplayed game in a week.")
    add_save_selector(sim_week_parser)
    sim_week_parser.add_argument("week", type=int)
    sim_week_parser.add_argument("--season", type=int, default=game_flow.DEFAULT_START_YEAR)
    sim_week_parser.add_argument("--game-type", choices=["REG", "PRE"], default="REG")
    sim_week_parser.add_argument("--seed", type=int)
    sim_week_parser.add_argument("--limit", type=int)
    sim_week_parser.add_argument("--apply", action="store_true")
    sim_week_parser.add_argument("--force", action="store_true")
    sim_week_parser.add_argument("--notes")
    sim_week_parser.add_argument(
        "--skip-roster-gate",
        action="store_true",
        help="Continue after an explicit user-approved automatic roster cutdown.",
    )
    sim_week_parser.add_argument(
        "--weekly-hooks",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run weekly event/roster hooks after saving a completed week.",
    )
    sim_week_parser.set_defaults(func=action_sim_week)

    sim_season_parser = subparsers.add_parser("sim-season", help="Simulate every unplayed regular-season game.")
    add_save_selector(sim_season_parser)
    sim_season_parser.add_argument("--season", type=int, default=game_flow.DEFAULT_START_YEAR)
    sim_season_parser.add_argument("--game-type", choices=["REG", "PRE"], default="REG")
    sim_season_parser.add_argument("--start-week", type=int)
    sim_season_parser.add_argument("--end-week", type=int)
    sim_season_parser.add_argument("--seed", type=int)
    sim_season_parser.add_argument("--limit", type=int)
    sim_season_parser.add_argument("--apply", action="store_true")
    sim_season_parser.add_argument("--force", action="store_true")
    sim_season_parser.add_argument("--notes")
    sim_season_parser.add_argument(
        "--skip-roster-gate",
        action="store_true",
        help="Continue after an explicit user-approved automatic roster cutdown.",
    )
    sim_season_parser.add_argument(
        "--weekly-hooks",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run weekly event/roster hooks after each completed week.",
    )
    sim_season_parser.set_defaults(func=action_sim_season)

    trade_parser = subparsers.add_parser("trade", help="Pass through to trade_engine.py for the active save.")
    add_save_selector(trade_parser)
    trade_parser.add_argument("trade_args", nargs=argparse.REMAINDER)
    trade_parser.set_defaults(func=action_trade)

    history_parser = subparsers.add_parser("history", help="Pass through to stat_history.py for standings/stat history.")
    add_save_selector(history_parser)
    history_parser.add_argument("history_args", nargs=argparse.REMAINDER)
    history_parser.set_defaults(func=action_history)

    personalities_parser = subparsers.add_parser("personalities", help="Pass through to player_personalities.py for hidden trait debugging.")
    add_save_selector(personalities_parser)
    personalities_parser.add_argument("personality_args", nargs=argparse.REMAINDER)
    personalities_parser.set_defaults(func=action_personalities)

    scouting_parser = subparsers.add_parser("scouting", help="Pass through to scouting.py for user inbox and draft scouting.")
    add_save_selector(scouting_parser)
    scouting_parser.add_argument("scouting_args", nargs=argparse.REMAINDER)
    scouting_parser.set_defaults(func=action_scouting)

    league_news_parser = subparsers.add_parser("league-news", help="Pass through to league_news.py for public league news.")
    add_save_selector(league_news_parser)
    league_news_parser.add_argument("league_news_args", nargs=argparse.REMAINDER)
    league_news_parser.set_defaults(func=action_league_news)

    event_gen_parser = subparsers.add_parser("event-gen", help="Pass through to event_generator.py for public personality-driven events.")
    add_save_selector(event_gen_parser)
    event_gen_parser.add_argument("event_gen_args", nargs=argparse.REMAINDER)
    event_gen_parser.set_defaults(func=action_event_gen)

    preflight_parser = subparsers.add_parser("preflight", help="Run read-only playtest readiness checks for the active save.")
    add_save_selector(preflight_parser)
    preflight_parser.add_argument("--json", action="store_true")
    preflight_parser.add_argument("--strict", action="store_true")
    preflight_parser.set_defaults(func=action_preflight)

    schemes_parser = subparsers.add_parser("schemes", help="Pass through to scheme_fits.py for staff/team/player scheme fits.")
    add_save_selector(schemes_parser)
    schemes_parser.add_argument("scheme_args", nargs=argparse.REMAINDER)
    schemes_parser.set_defaults(func=action_schemes)

    progression_parser = subparsers.add_parser("progression", help="Pass through to player_progression.py for season progression/regression.")
    add_save_selector(progression_parser)
    progression_parser.add_argument("progression_args", nargs=argparse.REMAINDER)
    progression_parser.set_defaults(func=action_progression)

    preseason_parser = subparsers.add_parser("preseason", help="Pass through to preseason_processor.py for training camp and preseason hooks.")
    add_save_selector(preseason_parser)
    preseason_parser.add_argument("preseason_args", nargs=argparse.REMAINDER)
    preseason_parser.set_defaults(func=action_preseason)

    draft_parser = subparsers.add_parser("draft", help="Pass through to generate_draft_class.py for the active save.")
    add_save_selector(draft_parser)
    draft_parser.add_argument("draft_args", nargs=argparse.REMAINDER)
    draft_parser.set_defaults(func=action_draft)

    validate_draft_parser = subparsers.add_parser("validate-draft", help="Pass through to validate_draft_class.py for the active save.")
    add_save_selector(validate_draft_parser)
    validate_draft_parser.add_argument("validate_draft_args", nargs=argparse.REMAINDER)
    validate_draft_parser.set_defaults(func=action_validate_draft)

    draft_select_parser = subparsers.add_parser("draft-select", help="Pass through to select_draft_pick.py for the active save.")
    add_save_selector(draft_select_parser)
    draft_select_parser.add_argument("draft_select_args", nargs=argparse.REMAINDER)
    draft_select_parser.set_defaults(func=action_draft_select)

    draft_room_parser = subparsers.add_parser("draft-room", help="Pass through to draft_room.py for active-save draft room flow.")
    add_save_selector(draft_room_parser)
    draft_room_parser.add_argument("draft_room_args", nargs=argparse.REMAINDER)
    draft_room_parser.set_defaults(func=action_draft_room)

    draft_portraits_parser = subparsers.add_parser("draft-portraits", help="Pass through to staged draft portrait generation.")
    draft_portraits_parser.add_argument("draft_portrait_args", nargs=argparse.REMAINDER)
    draft_portraits_parser.set_defaults(func=action_draft_portraits)

    free_agency_parser = subparsers.add_parser("free-agency", help="Pass through to free_agency_processor.py for active-save free agency flow.")
    add_save_selector(free_agency_parser)
    free_agency_parser.add_argument("free_agency_args", nargs=argparse.REMAINDER)
    free_agency_parser.set_defaults(func=action_free_agency)

    contract_parser = subparsers.add_parser("contract", help="Pass through to contract_negotiations.py for own-team contract talks.")
    add_save_selector(contract_parser)
    contract_parser.add_argument("contract_args", nargs=argparse.REMAINDER)
    contract_parser.set_defaults(func=action_contract)

    postseason_parser = subparsers.add_parser("postseason", help="Pass through to postseason.py for the active save.")
    add_save_selector(postseason_parser)
    postseason_parser.add_argument("postseason_args", nargs=argparse.REMAINDER)
    postseason_parser.set_defaults(func=action_postseason)

    complete_parser = subparsers.add_parser("complete-season", help="Finish a season and prepare the next one.")
    add_save_selector(complete_parser)
    complete_parser.add_argument("--season", type=int, default=game_flow.DEFAULT_START_YEAR)
    complete_parser.add_argument("--seed", type=int, help="Base seed for postseason simulations.")
    complete_parser.add_argument("--apply", action="store_true", help="Save the season completion changes.")
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
    complete_parser.add_argument("--no-retirements", action="store_true", help="Do not run automatic offseason retirements.")
    complete_parser.add_argument("--retirement-seed", type=int, help="Seed for automatic offseason retirements.")
    complete_parser.add_argument("--force-retirements", action="store_true", help="Replace an existing retirement run for this season.")
    complete_parser.add_argument(
        "--process-days-on-advance",
        action="store_true",
        help="Run every daily processing hook during the date jump. Slow; off by default for season rollover.",
    )
    complete_parser.add_argument("--notes")
    complete_parser.set_defaults(func=action_complete_season)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Play command unavailable: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
