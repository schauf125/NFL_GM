#!/usr/bin/env python3
"""Local UI runner for NFL GM.

This serves the static UI and exposes a small whitelist of local game actions.
It deliberately does not run arbitrary browser-supplied shell commands.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import subprocess
import sys
from datetime import datetime
from time import perf_counter
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import export_app_shell_ui_data
import export_front_office_ui_data
import export_game_center_ui_data
import export_player_card_ui_data
import export_player_profile_ui_data


ROOT = Path(__file__).resolve().parents[1]
MASTER_DB = ROOT / "database" / "nfl_gm.db"
SAVE_REGISTRY = ROOT / "saves" / "save_registry.json"
GAME_CENTER_OUTPUT = ROOT / "ui" / "game_center" / "game-center-data.js"
APP_SHELL_OUTPUT = ROOT / "ui" / "app_shell" / "app-shell-data.js"
FRONT_OFFICE_OUTPUT = ROOT / "ui" / "front_office" / "front-office-data.js"
PLAYER_CARD_OUTPUT = ROOT / "ui" / "player_card" / "player-data.js"
PLAYER_PROFILE_OUTPUT = ROOT / "ui" / "player_profile" / "player-profile-data.js"
PLAYER_EXPORT_ACTIONS = {
    "new_june1_save",
    "load_game",
    "draft_pick",
    "draft_skip",
    "draft_skip_to_user",
    "draft_finish",
    "advance_next_league_year",
    "free_agency_advance_hour",
    "free_agency_advance_day",
    "free_agency_resolve",
    "free_agency_offer",
    "free_agency_cpu_seed",
    "contract_extend",
    "contract_release",
    "contract_restructure",
    "depth_chart_set",
    "depth_chart_move",
}
LIGHTWEIGHT_PRESTATE_ACTIONS = {
    "contract_extend",
    "contract_release",
    "contract_restructure",
}
SKIP_PLAYER_REEXPORT_ACTIONS = {
    "contract_extend",
    "contract_release",
    "contract_restructure",
}


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def active_db_path() -> Path:
    registry = read_json(SAVE_REGISTRY, {"active_game_id": None, "saves": {}})
    active_id = registry.get("active_game_id")
    if active_id:
        record = registry.get("saves", {}).get(active_id)
        if record and record.get("db_path"):
            db_path = ROOT / record["db_path"]
            if db_path.exists():
                return db_path
    return MASTER_DB


def payload_for_active_db() -> dict[str, Any]:
    return export_game_center_ui_data.build_payload(active_db_path())


def app_shell_payload_for_active_db() -> dict[str, Any]:
    return export_app_shell_ui_data.build_payload(active_db_path())


def game_context(db_path: Path) -> dict[str, Any]:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        game_settings = export_game_center_ui_data.settings(con)
        active = export_game_center_ui_data.active_save(con)
        current_season = int(
            (active or {}).get("current_league_year")
            or game_settings.get("current_league_year")
            or game_settings.get("current_season")
            or 2026
        )
        current_date = (
            (active or {}).get("current_date")
            or game_settings.get("current_game_date")
            or f"{current_season}-06-01"
        )
        phase = (
            (active or {}).get("phase_name")
            or (active or {}).get("current_phase_code")
            or game_settings.get("current_calendar_phase")
            or "OFFSEASON_OPEN"
        )
        user_team = (active or {}).get("user_team")
        draft_year = export_game_center_ui_data.draft_year(con, current_season)
        fa_start = export_game_center_ui_data.free_agency_start_date(con, draft_year)
        return {
            "settings": game_settings,
            "activeSave": active,
            "currentSeason": current_season,
            "currentDate": current_date,
            "currentPhase": phase,
            "userTeam": user_team,
            "draftYear": draft_year,
            "freeAgencyStart": fa_start,
        }


def contract_state_patch(db_path: Path) -> dict[str, Any]:
    context = game_context(db_path)
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        contract_payload = export_game_center_ui_data.contract_negotiation_summary(
            con,
            int(context["currentSeason"]),
            context.get("userTeam"),
        )
        commands = export_game_center_ui_data.command_set(
            int(context["currentSeason"]),
            int(context["draftYear"]),
            context.get("userTeam"),
            str(context["freeAgencyStart"]),
        )
    return {
        "currentDate": context["currentDate"],
        "currentSeason": context["currentSeason"],
        "currentPhase": context["currentPhase"],
        "settings": context["settings"],
        "activeSave": context["activeSave"],
        "contractNegotiations": contract_payload,
        "commands": commands,
    }


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def lightweight_action_state() -> dict[str, Any]:
    """Small state object for actions that only need season and user team."""
    db_path = active_db_path()
    registry = read_json(SAVE_REGISTRY, {"active_game_id": None, "saves": {}})
    active_id = registry.get("active_game_id")
    active_record = (registry.get("saves") or {}).get(active_id or "", {})
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        settings: dict[str, str] = {}
        if table_exists(con, "game_settings"):
            settings = {
                str(row["setting_key"]): str(row["setting_value"])
                for row in con.execute("SELECT setting_key, setting_value FROM game_settings")
            }
        active: dict[str, Any] = dict(active_record) if isinstance(active_record, dict) else {}
        if table_exists(con, "active_game_save_view"):
            row = con.execute("SELECT * FROM active_game_save_view LIMIT 1").fetchone()
            if row:
                active.update(dict(row))

    current_season = int(
        active.get("current_league_year")
        or settings.get("current_league_year")
        or settings.get("current_contract_year")
        or settings.get("current_season")
        or 2026
    )
    user_team = (
        active.get("user_team")
        or settings.get("user_team")
        or settings.get("active_user_team")
        or "MIN"
    )
    return {
        "database": str(db_path),
        "currentSeason": current_season,
        "settings": settings,
        "activeSave": {**active, "user_team": user_team},
        "draft": {"year": int(settings.get("current_draft_year") or current_season + 1)},
    }


def player_export_season(payload: dict[str, Any]) -> int:
    settings = payload.get("settings") or {}
    for key in ("current_contract_year", "current_league_year", "current_season"):
        value = settings.get(key)
        if value:
            return int(value)
    return int(payload.get("currentSeason") or 2026)


def write_exports(*, include_players: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    db_path = active_db_path()
    payload = export_game_center_ui_data.export(db_path, GAME_CENTER_OUTPUT)
    app_shell_payload = export_app_shell_ui_data.export(db_path, APP_SHELL_OUTPUT)
    season = player_export_season(payload)
    export_front_office_ui_data.export(db_path, FRONT_OFFICE_OUTPUT, season)
    if not include_players:
        return payload, app_shell_payload
    export_player_card_ui_data.export(db_path, PLAYER_CARD_OUTPUT, season)
    export_player_profile_ui_data.export(db_path, PLAYER_PROFILE_OUTPUT, season)
    return payload, app_shell_payload


def write_contract_exports() -> tuple[dict[str, Any], dict[str, Any]]:
    db_path = active_db_path()
    patch = contract_state_patch(db_path)
    app_shell_payload = export_app_shell_ui_data.export(db_path, APP_SHELL_OUTPUT)
    export_front_office_ui_data.export(
        db_path,
        FRONT_OFFICE_OUTPUT,
        player_export_season({"currentSeason": patch.get("currentSeason"), "settings": patch.get("settings") or {}}),
    )
    return patch, app_shell_payload


def action_command(action: str, params: dict[str, Any], state: dict[str, Any]) -> list[str]:
    season = int(state.get("currentSeason") or 2026)
    draft_year = int(state.get("draft", {}).get("year") or season + 1)
    user_team = (
        (state.get("activeSave") or {}).get("user_team")
        or params.get("user_team")
        or "MIN"
    )

    def play(*args: str) -> list[str]:
        return [sys.executable, str(ROOT / "tools" / "play.py"), *args]

    if action == "status":
        return play("status")
    if action == "preflight":
        return play("preflight")
    if action == "new_june1_save":
        start_year = int(params.get("start_year") or season or 2026)
        team = str(params.get("user_team") or user_team or "MIN").upper()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        game_id = str(params.get("game_id") or f"{team.lower()}_{start_year}_june1_{stamp}")
        name = str(params.get("name") or f"{team} June 1 Start")
        command = [
            "new",
            "--game-id",
            game_id,
            "--name",
            name,
            "--user-team",
            team,
            "--start-year",
            str(start_year),
        ]
        if params.get("seed") is not None:
            command.extend(["--seed", str(int(params["seed"]))])
        if params.get("no_variance"):
            command.append("--no-variance")
        if params.get("no_personality_variance"):
            command.append("--no-personality-variance")
        if params.get("no_development_modifiers"):
            command.append("--no-development-modifiers")
        return play(*command)
    if action == "load_game":
        game_id = str(params.get("game_id") or "").strip()
        if not game_id:
            raise ValueError("load_game requires game_id.")
        return play("load", game_id)
    if action == "advance_next_event":
        return play("advance-to-next-event")
    if action == "validate_rosters":
        return play("validate-rosters", "--summary-only")
    if action == "auto_cutdown":
        return play("roster-cutdown", "--season", str(season), "--apply")
    if action == "sim_week":
        week = params.get("week") or state.get("season", {}).get("nextWeek")
        if not week:
            raise ValueError("No next regular-season week is available.")
        return play("sim-week", str(int(week)), "--season", str(season), "--apply")
    if action == "sim_season":
        return play("sim-season", "--season", str(season), "--apply", "--seed", f"{season}00")
    if action == "postseason":
        return play("postseason", "run", "--season", str(season), "--apply", "--seed", f"{season}99")
    if action == "complete_season":
        return play("complete-season", "--season", str(season), "--apply", "--seed", f"{season}99")
    if action == "contract_extend":
        player_id = params.get("player_id")
        if not player_id:
            raise ValueError("contract_extend requires player_id.")
        command = [
            "contract",
            "extend",
            "--season",
            str(season),
            "--team",
            str(user_team),
            "--player-id",
            str(int(player_id)),
            "--apply",
            "--fast",
        ]
        if params.get("years"):
            command.extend(["--years", str(int(params["years"]))])
        if params.get("aav"):
            command.extend(["--aav", str(int(params["aav"]))])
        return play(*command)
    if action == "contract_release":
        player_id = params.get("player_id")
        if not player_id:
            raise ValueError("contract_release requires player_id.")
        command = [
            "contract",
            "release",
            "--season",
            str(season),
            "--team",
            str(user_team),
            "--player-id",
            str(int(player_id)),
            "--apply",
        ]
        if params.get("post_june1"):
            command.append("--post-june1")
        return play(*command)
    if action == "contract_restructure":
        player_id = params.get("player_id")
        if not player_id:
            raise ValueError("contract_restructure requires player_id.")
        command = [
            "contract",
            "restructure",
            "--season",
            str(season),
            "--team",
            str(user_team),
            "--player-id",
            str(int(player_id)),
            "--apply",
        ]
        if params.get("amount"):
            command.extend(["--amount", str(int(params["amount"]))])
        return play(*command)
    if action == "depth_chart_set":
        player_id = params.get("player_id")
        position = params.get("position")
        rank = params.get("rank")
        if not player_id or not position or not rank:
            raise ValueError("depth_chart_set requires player_id, position, and rank.")
        command = [
            "depth-chart",
            "set",
            "--team",
            str(user_team),
            "--position",
            str(position),
            "--rank",
            str(int(rank)),
            "--player-id",
            str(int(player_id)),
            "--apply",
        ]
        if params.get("unit"):
            command.extend(["--unit", str(params["unit"])])
        return play(*command)
    if action == "depth_chart_move":
        player_id = params.get("player_id")
        position = params.get("position")
        direction = params.get("direction")
        if not player_id or not position or direction not in {"up", "down"}:
            raise ValueError("depth_chart_move requires player_id, position, and direction up/down.")
        return play(
            "depth-chart",
            "move",
            "--team",
            str(user_team),
            "--position",
            str(position),
            "--player-id",
            str(int(player_id)),
            "--direction",
            str(direction),
            "--apply",
        )
    if action == "free_agency_start":
        start_date = (
            params.get("start_date")
            or (state.get("freeAgency") or {}).get("startDate")
            or f"{draft_year}-03-10"
        )
        return play(
            "free-agency",
            "start",
            "--league-year",
            str(draft_year),
            "--start-date",
            str(start_date),
            "--no-cap-snapshot",
            "--apply",
        )
    if action == "free_agency_advance_hour":
        return play(
            "free-agency",
            "advance-hour",
            "--league-year",
            str(draft_year),
            "--no-cap-snapshot",
            "--apply",
        )
    if action == "free_agency_advance_day":
        days = int(params.get("days") or 1)
        return play(
            "free-agency",
            "advance-day",
            "--league-year",
            str(draft_year),
            "--days",
            str(days),
            "--no-cap-snapshot",
            "--apply",
        )
    if action == "free_agency_offer":
        player_id = params.get("player_id")
        if not player_id:
            raise ValueError("free_agency_offer requires player_id.")
        years = int(params.get("years") or 1)
        aav = int(params.get("aav") or 0)
        if aav <= 0:
            raise ValueError("free_agency_offer requires a positive aav.")
        command = [
            "free-agency",
            "offer",
            "--league-year",
            str(draft_year),
            "--team",
            str(user_team),
            "--player",
            str(int(player_id)),
            "--years",
            str(years),
            "--aav",
            str(aav),
            "--bonus",
            str(int(params.get("bonus") or 0)),
            "--guarantee-pct",
            str(int(params.get("guarantee_pct") or 0)),
            "--apply",
        ]
        if params.get("cpu_response_offers") is not None:
            command.extend(["--cpu-response-offers", str(int(params["cpu_response_offers"]))])
        return play(*command)
    if action == "free_agency_cpu_seed":
        return play(
            "free-agency",
            "cpu-seed",
            "--league-year",
            str(draft_year),
            "--no-cap-snapshot",
            "--apply",
        )
    if action == "advance_to_draft":
        return play("advance-to-draft", "--draft-year", str(draft_year), "--user-team", str(user_team))
    if action == "advance_next_league_year":
        return play("advance-to-next-league-year")
    if action == "draft_start":
        return play("draft-room", "start", "--draft-year", str(draft_year), "--user-team", str(user_team), "--paused", "--apply")
    if action == "draft_skip":
        return play(
            "draft-room",
            "skip",
            "--draft-year",
            str(draft_year),
            "--count",
            "1",
            "--until-user-pick",
            "--no-cap-snapshot",
            "--apply",
        )
    if action == "draft_skip_to_user":
        remaining = int(((state.get("draft") or {}).get("pickTotals") or {}).get("remaining") or 999)
        return play(
            "draft-room",
            "skip",
            "--draft-year",
            str(draft_year),
            "--count",
            str(max(remaining, 1)),
            "--until-user-pick",
            "--no-cap-snapshot",
            "--apply",
        )
    if action == "draft_finish":
        remaining = int(((state.get("draft") or {}).get("pickTotals") or {}).get("remaining") or 999)
        return play(
            "draft-room",
            "skip",
            "--draft-year",
            str(draft_year),
            "--count",
            str(max(remaining, 1)),
            "--include-user-pick",
            "--no-cap-snapshot",
            "--apply",
        )
    if action == "draft_pause":
        return play("draft-room", "pause", "--draft-year", str(draft_year), "--apply")
    if action == "draft_resume":
        return play("draft-room", "resume", "--draft-year", str(draft_year), "--apply")
    if action == "draft_pick":
        prospect_id = params.get("prospect_id")
        if not prospect_id:
            raise ValueError("draft_pick requires prospect_id.")
        draft_state = (state.get("draft") or {}).get("state") or {}
        current_team = str(draft_state.get("current_team") or "").upper()
        user_team_abbr = str(draft_state.get("user_team") or user_team or "").upper()
        if current_team and user_team_abbr and current_team != user_team_abbr and not params.get("allow_cpu_pick"):
            current_pick = draft_state.get("current_pick_number") or "?"
            raise ValueError(
                f"{user_team_abbr} is not on the clock. Current pick #{current_pick} belongs to {current_team}. "
                "Use Skip Next Pick."
            )
        return play(
            "draft-room",
            "pick",
            "--draft-year",
            str(draft_year),
            "--prospect-id",
            str(int(prospect_id)),
            "--no-cap-snapshot",
            "--apply",
        )
    if action == "ai_gm_setup":
        return play("ai-gm", "setup", "--season", str(season), "--no-backup")
    if action == "ai_gm_enable_ollama":
        return play(
            "ai-gm",
            "config",
            "--provider",
            "ollama",
            "--endpoint",
            str(params.get("endpoint") or "http://127.0.0.1:11434/api/chat"),
            "--model",
            str(params.get("model") or "llama3.1:8b"),
            "--enable",
        )
    if action == "ai_gm_show_config":
        return play("ai-gm", "show-config")
    if action == "ai_gm_profiles":
        return play("ai-gm", "profiles", "--team", str(params.get("team") or user_team), "--season", str(season))
    if action == "ai_gm_context":
        return play("ai-gm", "context", "--team", str(params.get("team") or user_team), "--decision-type", str(params.get("decision_type") or "draft_strategy_update"))
    if action == "ai_gm_run":
        return play("ai-gm", "run", "--team", str(params.get("team") or user_team), "--decision-type", str(params.get("decision_type") or "draft_strategy_update"))
    if action == "ai_gm_logs":
        return play("ai-gm", "logs", "--team", str(params.get("team") or user_team), "--limit", str(int(params.get("limit") or 12)))
    if action == "scouting_setup":
        command = ["scouting", "setup"]
        if params.get("draft_year"):
            command.extend(["--draft-year", str(int(params["draft_year"]))])
        if params.get("reset"):
            command.append("--reset")
        return play(*command)
    if action == "scouting_assign":
        prospect_id = params.get("prospect_id")
        if not prospect_id:
            raise ValueError("scouting_assign requires prospect_id.")
        focus = str(params.get("focus") or "film")
        return play("scouting", "assign", "--prospect-id", str(int(prospect_id)), "--focus", focus)
    if action == "scouting_process_week":
        slots = int(params.get("slots") or 8)
        return play("scouting", "process-week", "--slots", str(max(1, slots)))
    if action == "scouting_auto":
        return play("scouting", "auto")
    if action == "scouting_one":
        prospect_id = params.get("prospect_id")
        if not prospect_id:
            raise ValueError("scouting_one requires prospect_id.")
        return play("scouting", "scout-one", "--prospect-id", str(int(prospect_id)))
    if action == "scouting_random_two":
        return play("scouting", "random")
    if action == "scouting_discover_four":
        return play("scouting", "discover")
    if action == "scouting_senior_bowl_setup":
        return play("scouting", "senior-bowl-setup")
    if action == "scouting_senior_bowl_process":
        command = ["scouting", "senior-bowl-process"]
        if params.get("force"):
            command.append("--force")
        return play(*command)
    if action == "scouting_top30_visit":
        prospect_id = params.get("prospect_id")
        if not prospect_id:
            raise ValueError("scouting_top30_visit requires prospect_id.")
        return play("scouting", "top30-visit", "--prospect-id", str(int(prospect_id)))
    if action == "scouting_top30_auto":
        return play("scouting", "top30-auto", "--include-cpu")
    if action == "inbox_mark_read":
        command = ["scouting", "mark-read"]
        if params.get("message_id"):
            command.extend(["--message-id", str(int(params["message_id"]))])
        return play(*command)
    if action == "league_news_seed":
        return play("league-news", "seed")
    if action == "event_generate_week":
        season_data = state.get("season") if isinstance(state.get("season"), dict) else {}
        season = int(params.get("season") or state.get("currentSeason") or season_data.get("season") or 2026)
        week = int(params.get("week") or season_data.get("nextWeek") or 1)
        command = [
            "event-gen",
            "weekly",
            "--season",
            str(season),
            "--week",
            str(week),
            "--run-key",
            "manual",
            "--apply",
        ]
        if params.get("force"):
            command.append("--force")
        return play(*command)
    if action == "box_score":
        schedule_game_id = params.get("game_id") or params.get("schedule_game_id")
        if not schedule_game_id:
            raise ValueError("box_score requires game_id.")
        return [
            sys.executable,
            str(ROOT / "tools" / "view_box_score.py"),
            "--db",
            str(active_db_path()),
            "--game-id",
            str(int(schedule_game_id)),
            "--show-plays",
            str(int(params.get("show_plays") or 16)),
        ]
    if action == "refresh":
        return [sys.executable, str(ROOT / "tools" / "export_game_center_ui_data.py"), "--db", str(active_db_path()), "--output", str(GAME_CENTER_OUTPUT)]
    if action == "export_front_office":
        return [sys.executable, str(ROOT / "tools" / "export_front_office_ui_data.py"), "--db", str(active_db_path())]
    raise ValueError(f"Unknown UI action: {action}")


def run_action(action: str, params: dict[str, Any]) -> dict[str, Any]:
    before = lightweight_action_state() if action in LIGHTWEIGHT_PRESTATE_ACTIONS else payload_for_active_db()
    command = action_command(action, params, before)
    started = perf_counter()
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=int(params.get("timeout_seconds") or 3600),
    )
    duration_seconds = perf_counter() - started
    include_players = action == "refresh" or (
        action in PLAYER_EXPORT_ACTIONS and action not in SKIP_PLAYER_REEXPORT_ACTIONS
    )
    response = {
        "action": action,
        "command": " ".join(f'"{part}"' if " " in str(part) else str(part) for part in command),
        "returncode": result.returncode,
        "duration_seconds": round(duration_seconds, 2),
        "stdout": result.stdout[-20000:],
        "stderr": result.stderr[-20000:],
    }
    if action in LIGHTWEIGHT_PRESTATE_ACTIONS:
        state_patch, app_shell_after = write_contract_exports()
        response["statePatch"] = state_patch
        response["app_shell_state"] = app_shell_after
    else:
        after, app_shell_after = write_exports(include_players=include_players)
        response["state"] = after
        response["app_shell_state"] = app_shell_after
    return response


class UiHandler(SimpleHTTPRequestHandler):
    server_version = "NFLGMUIRunner/0.1"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/ui/app_shell/index.html")
            self.end_headers()
            return
        if parsed.path == "/api/state":
            try:
                self.write_json(HTTPStatus.OK, payload_for_active_db())
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/app-shell-state":
            try:
                self.write_json(HTTPStatus.OK, app_shell_payload_for_active_db())
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/run":
            self.write_json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint."})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            request = json.loads(body)
            action = str(request.get("action") or "")
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError("params must be an object.")
            self.write_json(HTTPStatus.OK, run_action(action, params))
        except subprocess.TimeoutExpired as exc:
            self.write_json(
                HTTPStatus.REQUEST_TIMEOUT,
                {"error": f"Action timed out after {exc.timeout} seconds."},
            )
        except Exception as exc:
            self.write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc), "state": payload_for_active_db()})


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the local NFL GM UI runner.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    mimetypes.add_type("text/javascript", ".js")
    write_exports()
    server = ThreadingHTTPServer((args.host, args.port), UiHandler)
    print(f"NFL GM UI runner serving http://{args.host}:{args.port}/ui/app_shell/index.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping UI runner.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
