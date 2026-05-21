#!/usr/bin/env python3
"""Local UI runner for NFL GM.

This serves the static UI and exposes a small whitelist of local game actions.
It deliberately does not run arbitrary browser-supplied shell commands.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import mimetypes
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime
from time import perf_counter
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import export_app_shell_ui_data
import export_front_office_ui_data
import export_game_center_ui_data
import export_player_card_ui_data
import export_player_profile_ui_data
import free_agency_processor
import injury_notifications
import roster_rules
import sim_control
import trade_engine
import view_box_score


ROOT = Path(__file__).resolve().parents[1]
MASTER_DB = ROOT / "database" / "nfl_gm.db"
SAVE_REGISTRY = ROOT / "saves" / "save_registry.json"
GAME_CENTER_OUTPUT = ROOT / "ui" / "game_center" / "game-center-data.js"
APP_SHELL_OUTPUT = ROOT / "ui" / "app_shell" / "app-shell-data.js"
FRONT_OFFICE_OUTPUT = ROOT / "ui" / "front_office" / "front-office-data.js"
PLAYER_CARD_OUTPUT = ROOT / "ui" / "player_card" / "player-data.js"
PLAYER_PROFILE_OUTPUT = ROOT / "ui" / "player_profile" / "player-profile-data.js"
RUN_ACTION_LOCK = threading.Lock()
RUNNING_ACTION: str | None = None
PLAYER_EXPORT_ACTIONS = {
    "new_june1_save",
    "load_game",
    "draft_class_generate",
    "draft_class_import",
    "advance_to_draft",
    "draft_start",
    "draft_pause",
    "draft_resume",
    "draft_pick",
    "draft_user_trade",
    "trade_submit",
    "trade_cpu_market",
    "draft_skip",
    "draft_skip_to_user",
    "draft_finish",
    "sim_week",
    "sim_season",
    "postseason",
    "postseason_round",
    "complete_season",
    "advance_to_date",
    "advance_next_league_year",
    "free_agency_advance_hour",
    "free_agency_advance_day",
    "free_agency_resolve",
    "free_agency_offer",
    "free_agency_cpu_seed",
    "contract_extend",
    "contract_tag",
    "contract_option_exercise",
    "contract_option_decline",
    "contract_release",
    "contract_restructure",
    "depth_chart_set",
    "depth_chart_move",
    "roster_release_player",
    "roster_change_number",
    "practice_squad_assign",
    "practice_squad_release",
    "auto_cutdown",
    "auto_cutdown_continue",
}
LIGHTWEIGHT_PRESTATE_ACTIONS = {
    "new_june1_save",
    "load_game",
    "delete_save",
    "draft_start",
    "draft_pause",
    "draft_resume",
    "draft_pick",
    "draft_user_trade",
    "trade_submit",
    "trade_cpu_market",
    "draft_skip",
    "draft_skip_to_user",
    "draft_finish",
    "contract_extend",
    "contract_tag",
    "contract_option_exercise",
    "contract_option_decline",
    "contract_release",
    "contract_restructure",
    "depth_chart_set",
    "depth_chart_move",
    "roster_release_player",
    "roster_change_number",
    "practice_squad_assign",
    "practice_squad_release",
    "free_agency_start",
    "free_agency_cpu_seed",
    "free_agency_advance_hour",
    "free_agency_advance_day",
    "free_agency_resolve",
    "free_agency_offer",
}
SKIP_PLAYER_REEXPORT_ACTIONS = {
    "contract_extend",
    "contract_tag",
    "contract_option_exercise",
    "contract_option_decline",
    "contract_release",
    "contract_restructure",
    "roster_release_player",
    "roster_change_number",
    "practice_squad_assign",
    "practice_squad_release",
}
DRAFT_RUN_ACTIONS = {
    "draft_class_generate",
    "draft_class_import",
    "advance_to_draft",
    "draft_start",
    "draft_pause",
    "draft_resume",
    "draft_pick",
    "draft_user_trade",
    "draft_skip",
    "draft_skip_to_user",
    "draft_finish",
}
FREE_AGENCY_RUN_ACTIONS = {
    "free_agency_start",
    "free_agency_cpu_seed",
    "free_agency_advance_hour",
    "free_agency_advance_day",
    "free_agency_resolve",
    "free_agency_offer",
}
TRADE_RUN_ACTIONS = {
    "trade_submit",
    "trade_cpu_market",
}
CALENDAR_RUN_ACTIONS = {
    "advance_next_event",
    "advance_to_date",
    "advance_next_league_year",
    "advance_to_draft",
    "auto_cutdown_continue",
}
INJURY_ALERT_ACTIONS = {"sim_week", "sim_season"}
SIM_CANCEL_ACTIONS = {"sim_week", "sim_season", "advance_to_draft", "draft_skip", "draft_skip_to_user", "draft_finish"}
ACTION_TIMEOUT_DEFAULTS = {
    "advance_to_draft": 6 * 60 * 60,
    "auto_cutdown_continue": 6 * 60 * 60,
    "sim_season": 3 * 60 * 60,
}


def action_timeout_seconds(action: str, params: dict[str, Any]) -> int:
    return int(params.get("timeout_seconds") or ACTION_TIMEOUT_DEFAULTS.get(action, 3600))


def remaining_draft_pick_count(draft_year: int) -> int:
    db_path = active_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT COUNT(*) AS remaining
            FROM draft_picks
            WHERE draft_year = ?
              AND COALESCE(is_used, 0) = 0
            """,
            (int(draft_year),),
        ).fetchone()
    return int(row["remaining"] or 0) if row else 0


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_record(game_id: str) -> dict[str, Any] | None:
    registry = read_json(SAVE_REGISTRY, {"active_game_id": None, "saves": {}})
    return (registry.get("saves") or {}).get(game_id)


def stop_processes_using_save(game_id: str) -> list[int]:
    record = save_record(game_id)
    if not record:
        return []
    db_path = ROOT / str(record.get("db_path") or "")
    save_dir = db_path.parent
    needles = {str(db_path), str(save_dir), str(db_path).replace("\\", "\\\\")}
    stopped: list[int] = []
    if sys.platform != "win32":
        return stopped
    script = r"""
$needles = @(
__NEEDLES__
)
$current = $PID
$procs = Get-CimInstance Win32_Process | Where-Object {
  $_.ProcessId -ne $current -and $_.Name -like 'python*' -and $null -ne $_.CommandLine
}
foreach ($proc in $procs) {
  foreach ($needle in $needles) {
    if ($needle -and $needle.Length -gt 0 -and $proc.CommandLine -like "*$needle*") {
      Stop-Process -Id $proc.ProcessId -Force
      Write-Output $proc.ProcessId
      break
    }
  }
}
"""
    needles_literal = "\n".join(json.dumps(value) + "," for value in sorted(needles))
    script = script.replace("__NEEDLES__", needles_literal)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
        )
    except Exception:
        return stopped
    for line in (result.stdout or "").splitlines():
        try:
            stopped.append(int(line.strip()))
        except ValueError:
            continue
    return stopped


def active_db_path() -> Path:
    registry = read_json(SAVE_REGISTRY, {"active_game_id": None, "saves": {}})
    active_id = registry.get("active_game_id") or registry.get("activeGameId")
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


def export_app_shell_default() -> dict[str, Any]:
    db_path = export_app_shell_ui_data.default_export_db()
    return export_app_shell_ui_data.export(db_path, APP_SHELL_OUTPUT)


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
        current_contract_year = int(
            game_settings.get("current_contract_year")
            or game_settings.get("current_league_year")
            or current_season
        )
        active_date = str((active or {}).get("current_date") or "")
        setting_date = str(game_settings.get("current_game_date") or "")
        current_date = (
            max([value for value in (active_date, setting_date) if value], default="")
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
        fa_year = export_game_center_ui_data.free_agency_league_year(
            con,
            current_season=current_season,
            current_date=current_date,
            draft_year_value=draft_year,
            game_settings=game_settings,
        )
        fa_start = export_game_center_ui_data.free_agency_start_date(con, fa_year)
        return {
            "settings": game_settings,
            "activeSave": active,
            "currentSeason": current_season,
            "currentContractYear": current_contract_year,
            "currentDate": current_date,
            "currentPhase": phase,
            "userTeam": user_team,
            "draftYear": draft_year,
            "freeAgencyYear": fa_year,
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
            free_agency_year=int(context["freeAgencyYear"]),
        )
    return {
        "currentDate": context["currentDate"],
        "currentSeason": context["currentSeason"],
        "currentContractYear": context["currentContractYear"],
        "currentPhase": context["currentPhase"],
        "settings": context["settings"],
        "activeSave": context["activeSave"],
        "contractNegotiations": contract_payload,
        "commands": commands,
    }


def depth_chart_state_patch(db_path: Path) -> dict[str, Any]:
    context = game_context(db_path)
    target_team = context.get("userTeam") or "MIN"
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        depth_chart = export_game_center_ui_data.depth_chart_summary(
            con,
            target_team,
            int(context["currentSeason"]),
            int(context["currentContractYear"]),
        )
        commands = export_game_center_ui_data.command_set(
            int(context["currentSeason"]),
            int(context["draftYear"]),
            target_team,
            str(context["freeAgencyStart"]),
        )
    return {
        "currentDate": context["currentDate"],
        "currentSeason": context["currentSeason"],
        "currentContractYear": context["currentContractYear"],
        "currentPhase": context["currentPhase"],
        "settings": context["settings"],
        "activeSave": context["activeSave"],
        "depthChart": depth_chart,
        "depthChartGeneratedAt": datetime.now().isoformat(timespec="seconds"),
        "commands": commands,
    }


def draft_state_patch(db_path: Path) -> dict[str, Any]:
    context = game_context(db_path)
    draft_year = int(context["draftYear"])
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        active = export_game_center_ui_data.active_save(con) or {}
        user_team_id = active.get("user_team_id")
        draft = export_game_center_ui_data.draft_summary(
            con,
            draft_year,
            user_team_id=user_team_id,
            game_id=(active or {}).get("game_id") or (active or {}).get("save_id"),
            current_date_value=str(context["currentDate"]),
        )
        rookie_class = {
            "year": int(context["currentSeason"]),
            "selections": export_game_center_ui_data.draft_user_selections(
                con,
                int(context["currentSeason"]),
                user_team_id,
            ),
        }
        commands = export_game_center_ui_data.command_set(
            int(context["currentSeason"]),
            draft_year,
            context.get("userTeam"),
            str(context["freeAgencyStart"]),
        )
    return {
        "currentDate": context["currentDate"],
        "currentSeason": context["currentSeason"],
        "currentPhase": context["currentPhase"],
        "settings": context["settings"],
        "activeSave": context["activeSave"],
        "draft": draft,
        "draftGeneratedAt": datetime.now().isoformat(timespec="seconds"),
        "rookieClass": rookie_class,
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
    active_date = str(active.get("current_date") or "")
    setting_date = str(settings.get("current_game_date") or "")
    current_date = (
        max([value for value in (active_date, setting_date) if value], default="")
        or f"{current_season}-06-01"
    )
    draft_year = export_game_center_ui_data.draft_year(con, current_season)
    free_agency_year = export_game_center_ui_data.free_agency_league_year(
        con,
        current_season=current_season,
        current_date=str(current_date),
        draft_year_value=draft_year,
        game_settings=settings,
    )
    control_mode = str(active.get("control_mode") or settings.get("control_mode") or "team")
    user_team = (
        active.get("user_team")
        or settings.get("user_team")
        or settings.get("active_user_team")
        or (None if control_mode == "observe" else "MIN")
    )
    return {
        "database": str(db_path),
        "currentSeason": current_season,
        "currentDate": str(current_date),
        "settings": settings,
        "activeSave": {**active, "user_team": user_team, "control_mode": control_mode},
        "draft": {"year": draft_year},
        "freeAgency": {"leagueYear": free_agency_year},
    }


def player_export_season(payload: dict[str, Any]) -> int:
    settings = payload.get("settings") or {}
    candidates: list[int] = []
    for key in ("current_contract_year", "current_league_year", "current_season"):
        value = settings.get(key)
        if value:
            candidates.append(int(value))
    if payload.get("currentSeason"):
        candidates.append(int(payload["currentSeason"]))
    return max(candidates) if candidates else 2026


def active_export_season(db_path: Path) -> int:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        settings: dict[str, str] = {}
        if table_exists(con, "game_settings"):
            settings = {
                str(row["setting_key"]): str(row["setting_value"])
                for row in con.execute("SELECT setting_key, setting_value FROM game_settings")
            }
        active: dict[str, Any] = {}
        if table_exists(con, "active_game_save_view"):
            row = con.execute("SELECT * FROM active_game_save_view LIMIT 1").fetchone()
            if row:
                active = dict(row)
    candidates: list[int] = []
    for value in (
        active.get("current_league_year"),
        settings.get("current_contract_year"),
        settings.get("current_league_year"),
        settings.get("current_season"),
    ):
        if value:
            candidates.append(int(value))
    return max(candidates) if candidates else 2026


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


def free_agency_state_patch(db_path: Path) -> dict[str, Any]:
    context = game_context(db_path)
    target_year = int(context["freeAgencyYear"])
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        free_agency = export_game_center_ui_data.free_agency_summary(conn, target_year)
        contracts = export_game_center_ui_data.contract_negotiation_summary(
            conn,
            int(context["currentSeason"]),
            context.get("userTeam"),
        )
    return {
        "currentDate": context["currentDate"],
        "currentSeason": context["currentSeason"],
        "currentPhase": context["currentPhase"],
        "settings": context["settings"],
        "activeSave": context["activeSave"],
        "freeAgency": free_agency,
        "freeAgencyGeneratedAt": datetime.now().isoformat(timespec="seconds"),
        "contractNegotiations": contracts,
    }


def write_lightweight_action_exports(action: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if action in {"new_june1_save", "load_game", "delete_save"}:
        return {}, export_app_shell_default()
    db_path = active_db_path()
    if action in DRAFT_RUN_ACTIONS:
        patch = draft_state_patch(db_path)
        return patch, app_shell_payload_for_active_db()
    if action in {"depth_chart_set", "depth_chart_move", "roster_release_player", "roster_change_number", "practice_squad_assign", "practice_squad_release"}:
        patch = depth_chart_state_patch(db_path)
        return patch, app_shell_payload_for_active_db()
    if action in FREE_AGENCY_RUN_ACTIONS:
        patch = free_agency_state_patch(db_path)
        return patch, app_shell_payload_for_active_db()
    return write_contract_exports()


def read_window_payload(path: Path, variable_name: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    marker = f"window.{variable_name} = "
    if marker not in text:
        raise ValueError(f"{path.name} does not contain {variable_name}.")
    raw = text.split(marker, 1)[1].rsplit(";", 1)[0]
    return json.loads(raw)


def player_profile_payload_for_active_db(player_id: int | None = None) -> dict[str, Any]:
    db_path = active_db_path()
    season = active_export_season(db_path)
    if player_id is not None:
        payload = export_player_profile_ui_data.build_payload(db_path, season, player_id=player_id)
    else:
        payload = export_player_profile_ui_data.build_payload(db_path, season)
    payload["generatedAt"] = datetime.now().isoformat(timespec="seconds")
    return payload


def player_card_payload_for_active_db(player_id: int | None = None) -> dict[str, Any]:
    db_path = active_db_path()
    season = active_export_season(db_path)
    if player_id is not None:
        payload = export_player_card_ui_data.build_payload(db_path, season, player_id=player_id)
    else:
        payload = export_player_card_ui_data.build_payload(db_path, season)
    payload["generatedAt"] = datetime.now().isoformat(timespec="seconds")
    return payload


def player_search_payload_for_active_db() -> dict[str, Any]:
    db_path = active_db_path()
    season = active_export_season(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    player_rows = export_player_profile_ui_data.fetch_players(conn, None)
    player_ids = [int(row["player_id"]) for row in player_rows]
    teams = export_player_profile_ui_data.team_assets(conn)
    shots = export_player_profile_ui_data.headshots(conn)
    roles = export_player_profile_ui_data.roles_by_player(conn, season, player_ids)
    season_stats = export_player_profile_ui_data.season_stats_by_player(conn, player_ids)
    career = export_player_profile_ui_data.career_totals_by_player(
        season_stats,
        export_player_profile_ui_data.career_by_player(conn, player_ids),
    )
    players = []
    for row in player_rows:
        player_id = int(row["player_id"])
        name = f"{row['first_name']} {row['last_name']}".strip()
        role = (roles.get(player_id) or [{}])[0]
        player_career = career.get(player_id) or {}
        season_rows = season_stats.get(player_id) or []
        current_row = next(
            (item for item in season_rows if int(item.get("season") or 0) == season),
            season_rows[0] if season_rows else {},
        )
        current_summary = {
            key: current_row.get(key)
            for key in (
                "season",
                "stat_team",
                "games",
                "passing_yards",
                "passing_tds",
                "rushing_yards",
                "rushing_tds",
                "receptions",
                "targets",
                "receiving_yards",
                "receiving_tds",
                "def_sacks",
                "def_interceptions",
            )
            if key in current_row
        }
        team = teams.get(int(row["team_id"])) if row["team_id"] is not None else None
        players.append({
            "id": player_id,
            "name": name,
            "initials": "".join(part[:1] for part in name.split()[:2]).upper(),
            "position": row["position"],
            "positionLabel": export_player_profile_ui_data.POSITION_LABELS.get(row["position"], row["position"]),
            "team": team or {
                "id": None,
                "abbr": "FA",
                "name": "Free Agent",
                "conference": "",
                "division": "",
                "logo": None,
                "primary": "#75808f",
                "secondary": "#d6dde6",
            },
            "headshot": shots.get(player_id),
            "profile": {
                "age": row["age"] if row["age"] is not None else "--",
                "college": row["college"] or "--",
                "height": export_player_profile_ui_data.height_label(row["height_in"]),
                "weight": f"{row['weight_lbs']} lbs" if row["weight_lbs"] else "--",
                "jersey": f"#{row['jersey_number']}" if row["jersey_number"] is not None else "--",
                "status": row["status"] or "Active",
                "experience": export_player_profile_ui_data.years_label(row["years_exp"], row["is_rookie"]),
            },
            "roles": [role] if role else [],
            "role": role,
            "career": {
                "career_games": player_career.get("career_games") or 0,
                "total_tds": player_career.get("total_tds") or 0,
            },
            "careerGames": player_career.get("career_games") or 0,
            "totalTds": player_career.get("total_tds") or 0,
            "currentSeason": current_summary,
            "seasonStats": [current_summary] if current_summary else [],
        })
    conn.close()
    return {
        "season": season,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "playerCount": len(players),
        "players": players,
    }


def _team_by_abbr(conn: sqlite3.Connection, abbr: str | None) -> sqlite3.Row | None:
    if not abbr:
        return None
    return conn.execute(
        "SELECT * FROM teams WHERE UPPER(abbreviation) = UPPER(?)",
        (abbr,),
    ).fetchone()


def _trade_player_rows(conn: sqlite3.Connection, team_id: int, season: int, chart: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.player_id, p.first_name || ' ' || p.last_name AS name,
               p.position, p.age, p.overall, p.potential, p.dev_trait,
               p.status, p.jersey_number,
               c.aav, c.end_year
        FROM players p
        LEFT JOIN contracts c ON c.player_id = p.player_id AND c.is_active = 1
        WHERE p.team_id = ?
          AND p.status IN ('Active', 'Questionable', 'Doubtful', 'Out', 'Practice Squad')
        ORDER BY
          CASE p.position
            WHEN 'QB' THEN 1 WHEN 'RB' THEN 2 WHEN 'FB' THEN 3 WHEN 'WR' THEN 4
            WHEN 'TE' THEN 5 WHEN 'OT' THEN 6 WHEN 'OG' THEN 7 WHEN 'C' THEN 8
            WHEN 'IDL' THEN 9 WHEN 'EDGE' THEN 10 WHEN 'LB' THEN 11 WHEN 'CB' THEN 12
            WHEN 'S' THEN 13 WHEN 'K' THEN 14 WHEN 'P' THEN 15 WHEN 'LS' THEN 16
            ELSE 99 END,
          p.overall DESC, p.potential DESC, p.age ASC
        LIMIT 90
        """,
        (team_id,),
    ).fetchall()
    players = []
    for row in rows:
        value = trade_engine.player_trade_value(conn, int(row["player_id"]), season, chart)
        players.append({
            "type": "player",
            "playerId": int(row["player_id"]),
            "label": f"{row['name']} | {row['position']} {int(row['overall'] or 0)} OVR",
            "name": row["name"],
            "position": row["position"],
            "age": row["age"],
            "overall": row["overall"],
            "potential": row["potential"],
            "devTrait": row["dev_trait"],
            "status": row["status"],
            "aav": row["aav"],
            "endYear": row["end_year"],
            "value": round(value, 2),
        })
    return players


def _trade_pick_rows(conn: sqlite3.Connection, team_id: int, season: int, chart: str) -> list[dict[str, Any]]:
    if not table_exists(conn, "draft_picks"):
        return []
    rows = conn.execute(
        """
        WITH ordered AS (
            SELECT
                dp.*,
                ROW_NUMBER() OVER (
                    PARTITION BY dp.draft_year
                    ORDER BY dp.round, COALESCE(dp.pick_number, dp.pick_id), dp.pick_id
                ) AS effective_pick_number,
                ROW_NUMBER() OVER (
                    PARTITION BY dp.draft_year, dp.round
                    ORDER BY COALESCE(dp.pick_number, dp.pick_id), dp.pick_id
                ) AS effective_pick_in_round
            FROM draft_picks dp
            WHERE dp.draft_year BETWEEN ? AND ?
        )
        SELECT o.*, ot.abbreviation AS original_team
        FROM ordered o
        LEFT JOIN teams ot ON ot.team_id = o.original_team_id
        WHERE o.current_team_id = ?
          AND COALESCE(o.is_used, 0) = 0
        ORDER BY o.draft_year, o.round, o.effective_pick_number, o.pick_id
        LIMIT 40
        """,
        (season, season + 4, team_id),
    ).fetchall()
    picks = []
    for row in rows:
        pick_number = row["effective_pick_number"] or row["pick_number"]
        if pick_number:
            value = trade_engine.pick_value(conn, chart, int(pick_number))
            label = f"{row['draft_year']} #{pick_number} R{row['round']}"
        else:
            value = trade_engine.pick_value_for_round(
                conn,
                chart,
                int(row["draft_year"] or season + 1),
                int(row["round"] or 1),
                team_id,
            )
            label = f"{row['draft_year']} R{row['round']}"
        if row["original_team"]:
            label += f" ({row['original_team']})"
        picks.append({
            "type": "pick",
            "pickId": int(row["pick_id"]),
            "draftYear": int(row["draft_year"]),
            "round": int(row["round"]),
            "pickNumber": int(pick_number) if pick_number else None,
            "label": label,
            "value": round(float(value), 2),
        })
    return picks


def trade_center_payload_for_active_db(partner_abbr: str | None = None) -> dict[str, Any]:
    db_path = active_db_path()
    context = game_context(db_path)
    season = int(context["currentSeason"])
    active = context.get("activeSave") or {}
    user_abbr = str(active.get("user_team") or context.get("userTeam") or "MIN").upper()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        trade_engine.ensure_schema(conn)
        trade_engine.seed_charts(conn)
        trade_engine.assign_charts_to_gms(conn)
        teams = [
            dict(row)
            for row in conn.execute(
                """
                SELECT team_id AS teamId, abbreviation AS abbr,
                       city || ' ' || nickname AS name
                FROM teams
                ORDER BY abbreviation
                """
            ).fetchall()
        ]
        user_team = _team_by_abbr(conn, user_abbr) or conn.execute("SELECT * FROM teams ORDER BY team_id LIMIT 1").fetchone()
        user_team_id = int(user_team["team_id"])
        if not partner_abbr or str(partner_abbr).upper() == user_abbr:
            partner = conn.execute(
                "SELECT * FROM teams WHERE team_id != ? ORDER BY abbreviation LIMIT 1",
                (user_team_id,),
            ).fetchone()
        else:
            partner = _team_by_abbr(conn, partner_abbr)
        if not partner:
            partner = conn.execute(
                "SELECT * FROM teams WHERE team_id != ? ORDER BY abbreviation LIMIT 1",
                (user_team_id,),
            ).fetchone()
        partner_team_id = int(partner["team_id"])
        cpu_chart = trade_engine.gm_chart(conn, partner_team_id)
        user_chart = trade_engine.gm_chart(conn, user_team_id)
        recent = []
        if table_exists(conn, "trade_proposals_view"):
            recent = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT proposal_id AS proposalId, proposal_date AS proposalDate,
                           proposing_team AS proposingTeam, receiving_team AS receivingTeam,
                           status, proposing_value AS proposingValue,
                           receiving_value AS receivingValue,
                           proposer_note AS proposerNote,
                           responder_note AS responderNote
                    FROM trade_proposals_view
                    ORDER BY proposal_date DESC, proposal_id DESC
                    LIMIT 14
                    """
                ).fetchall()
            ]
        return {
            "season": season,
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "userTeam": {
                "id": user_team_id,
                "abbr": user_team["abbreviation"],
                "name": f"{user_team['city']} {user_team['nickname']}",
                "chart": user_chart,
            },
            "partnerTeam": {
                "id": partner_team_id,
                "abbr": partner["abbreviation"],
                "name": f"{partner['city']} {partner['nickname']}",
                "chart": cpu_chart,
            },
            "teams": teams,
            "userAssets": {
                "players": _trade_player_rows(conn, user_team_id, season, cpu_chart),
                "picks": _trade_pick_rows(conn, user_team_id, season, cpu_chart),
            },
            "partnerAssets": {
                "players": _trade_player_rows(conn, partner_team_id, season, cpu_chart),
                "picks": _trade_pick_rows(conn, partner_team_id, season, cpu_chart),
            },
            "recent": recent,
        }


def league_leaders_payload_for_active_db(season: int | None = None, category: str | None = None) -> dict[str, Any]:
    db_path = active_db_path()
    target_season = int(season or active_export_season(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        stats = export_game_center_ui_data.stat_leaders(conn, target_season)
    if category:
        stats = {category: stats.get(category, [])}
    return {
        "season": target_season,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "stats": stats,
    }


def season_payload_for_active_db(season: int | None = None) -> dict[str, Any]:
    db_path = active_db_path()
    target_season = int(season or active_export_season(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        active = export_game_center_ui_data.active_save(conn) or {}
        user_team_id = active.get("user_team_id")
        season_data = export_game_center_ui_data.season_summary(conn, target_season, user_team_id=user_team_id)
    return {
        "season": target_season,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "seasonData": season_data,
    }


def standings_payload_for_active_db(season: int | None = None) -> dict[str, Any]:
    payload = season_payload_for_active_db(season)
    return {
        "season": payload["season"],
        "generatedAt": payload["generatedAt"],
        "standings": payload["seasonData"].get("standings", []),
    }


def schedule_payload_for_active_db(season: int | None = None, week: int | None = None) -> dict[str, Any]:
    db_path = active_db_path()
    target_season = int(season or active_export_season(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if week is None:
            season_data = export_game_center_ui_data.season_summary(conn, target_season)
            target_week = season_data.get("nextWeek")
        else:
            target_week = int(week)
        games = []
        if target_week:
            games = export_game_center_ui_data.game_rows(
                conn,
                season=target_season,
                where_sql="g.game_type = 'REG' AND g.week = ?",
                params=(int(target_week),),
                order_sql="g.week, g.week_game_number, g.game_id",
                limit=64,
            )
    return {
        "season": target_season,
        "week": target_week,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "games": games,
    }


def live_calendar_focus_date(conn: sqlite3.Connection, season: int, fallback: str) -> str:
    if not export_game_center_ui_data.table_exists(conn, "season_games"):
        return fallback
    fallback_date = str(fallback or "")
    if fallback_date and fallback_date < f"{season}-09-01":
        row = conn.execute(
            """
            SELECT COALESCE(
                MIN(CASE WHEN played = 0 THEN game_date END),
                MAX(CASE WHEN played = 1 THEN game_date END)
            ) AS preseason_focus_date
            FROM season_games
            WHERE season = ?
              AND game_type = 'PRE'
            """,
            (season,),
        ).fetchone()
        if row and row["preseason_focus_date"]:
            return str(row["preseason_focus_date"])
    row = conn.execute(
        """
        SELECT MAX(game_date) AS latest_played_date
        FROM season_games
        WHERE season = ?
          AND played = 1
          AND game_type = 'REG'
        """,
        (season,),
    ).fetchone()
    if row and row["latest_played_date"]:
        return str(row["latest_played_date"])
    row = conn.execute(
        """
        SELECT MIN(game_date) AS next_game_date
        FROM season_games
        WHERE season = ?
          AND played = 0
          AND game_type = 'REG'
        """,
        (season,),
    ).fetchone()
    if row and row["next_game_date"]:
        return str(row["next_game_date"])
    return fallback


def calendar_payload_for_active_db(
    season: int | None = None,
    current_date: str | None = None,
    live_focus: bool = False,
) -> dict[str, Any]:
    db_path = active_db_path()
    context = game_context(db_path)
    target_season = int(season or context["currentSeason"])
    target_date = current_date or str(context["currentDate"])
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if live_focus:
            target_date = live_calendar_focus_date(conn, target_season, str(context["currentDate"]))
        focus_date = export_game_center_ui_data.default_calendar_focus_date(
            conn,
            season=target_season,
            current_date=target_date,
        )
        active = export_game_center_ui_data.active_save(conn) or {}
        game_id = active.get("game_id") or active.get("save_id")
        user_team_id = active.get("user_team_id")
        calendar = export_game_center_ui_data.calendar_summary(
            conn,
            season=target_season,
            current_date=target_date,
            focus_date=focus_date,
            game_id=game_id,
            user_team_id=user_team_id,
        )
        events = export_game_center_ui_data.upcoming_events(conn)
    return {
        "season": target_season,
        "currentDate": target_date,
        "saveCurrentDate": str(context["currentDate"]),
        "currentPhase": context["currentPhase"],
        "liveFocus": bool(live_focus),
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "calendar": calendar,
        "events": events,
    }


def inbox_payload_for_active_db(limit: int = 40) -> dict[str, Any]:
    db_path = active_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        scouting_module = export_game_center_ui_data.scouting
        game_id = scouting_module.active_game_id(conn)
        inbox = scouting_module.inbox_rows(conn, game_id=game_id, limit=limit)
        enrich_inbox_messages(conn, inbox)
        unread = scouting_module.unread_count(conn, game_id)
    return {
        "gameId": game_id,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "inbox": inbox,
        "counts": {
            "unread": unread,
            "messages": len(inbox),
        },
    }


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def enrich_inbox_messages(conn: sqlite3.Connection, inbox: list[dict[str, Any]]) -> None:
    player_ids = {
        int(message["related_id"])
        for message in inbox
        if str(message.get("related_table") or "").lower() == "players" and message.get("related_id")
    }
    prospect_ids = {
        int(message["related_id"])
        for message in inbox
        if str(message.get("related_table") or "").lower() == "draft_prospects" and message.get("related_id")
    }
    players: dict[int, dict[str, Any]] = {}
    if player_ids and table_exists(conn, "players"):
        placeholders = ",".join("?" for _ in player_ids)
        for row in conn.execute(
            f"""
            SELECT
                p.player_id,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position,
                t.abbreviation AS team
            FROM players p
            LEFT JOIN teams t ON t.team_id = p.team_id
            WHERE p.player_id IN ({placeholders})
            """,
            sorted(player_ids),
        ).fetchall():
            players[int(row["player_id"])] = dict(row)

    prospects: dict[int, dict[str, Any]] = {}
    prospect_columns = table_columns(conn, "draft_prospects")
    if prospect_ids and prospect_columns:
        placeholders = ",".join("?" for _ in prospect_ids)
        name_expr = (
            "player_name"
            if "player_name" in prospect_columns
            else "TRIM(COALESCE(first_name, '') || ' ' || COALESCE(last_name, ''))"
        )
        position_expr = "position" if "position" in prospect_columns else "NULL"
        college_expr = "college" if "college" in prospect_columns else "NULL"
        for row in conn.execute(
            f"""
            SELECT
                prospect_id,
                {name_expr} AS player_name,
                {position_expr} AS position,
                {college_expr} AS college
            FROM draft_prospects
            WHERE prospect_id IN ({placeholders})
            """,
            sorted(prospect_ids),
        ).fetchall():
            prospects[int(row["prospect_id"])] = dict(row)

    for message in inbox:
        table = str(message.get("related_table") or "").lower()
        related_id = int(message["related_id"]) if message.get("related_id") else None
        if table == "players" and related_id in players:
            message["relatedPlayer"] = players[related_id]
        elif table == "draft_prospects" and related_id in prospects:
            message["relatedProspect"] = prospects[related_id]

    enrich_inbox_player_mentions(conn, inbox, players)


def enrich_inbox_player_mentions(
    conn: sqlite3.Connection,
    inbox: list[dict[str, Any]],
    known_players: dict[int, dict[str, Any]] | None = None,
) -> None:
    if not inbox or not table_exists(conn, "players"):
        return
    haystack_by_message = {
        int(message["message_id"]): f"{message.get('title') or ''}\n{message.get('body') or ''}".lower()
        for message in inbox
        if message.get("message_id")
    }
    if not haystack_by_message:
        return
    rows = conn.execute(
        """
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS player_name,
            p.position,
            t.abbreviation AS team
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        WHERE p.first_name IS NOT NULL
          AND p.last_name IS NOT NULL
          AND LENGTH(TRIM(p.first_name || ' ' || p.last_name)) >= 6
        """
    ).fetchall()
    candidates = [dict(row) for row in rows]
    by_id = dict(known_players or {})
    for player in candidates:
        by_id[int(player["player_id"])] = player

    for message in inbox:
        message_id = int(message["message_id"]) if message.get("message_id") else None
        text = haystack_by_message.get(message_id or -1, "")
        if not text:
            continue
        mentioned: list[dict[str, Any]] = []
        seen: set[int] = set()
        if message.get("relatedPlayer", {}).get("player_id"):
            player_id = int(message["relatedPlayer"]["player_id"])
            mentioned.append(message["relatedPlayer"])
            seen.add(player_id)
        for player in candidates:
            player_id = int(player["player_id"])
            if player_id in seen:
                continue
            name = str(player.get("player_name") or "").strip()
            if name and name.lower() in text:
                mentioned.append(by_id[player_id])
                seen.add(player_id)
            if len(mentioned) >= 8:
                break
        if mentioned:
            message["mentionedPlayers"] = mentioned


def league_news_payload_for_active_db(limit: int = 80) -> dict[str, Any]:
    db_path = active_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        news = export_game_center_ui_data.league_news.build_ui_payload(conn, limit=limit)
    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "leagueNews": news,
    }


def transactions_payload_for_active_db(limit: int = 400, include_baseline: bool = False) -> dict[str, Any]:
    db_path = active_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        transactions = export_game_center_ui_data.league_transactions_summary(
            conn,
            limit=limit,
            include_baseline=include_baseline,
        )
    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "transactions": transactions,
    }


def injuries_payload_for_active_db(active_limit: int = 160, recent_limit: int = 120) -> dict[str, Any]:
    db_path = active_db_path()
    context = game_context(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        active = export_game_center_ui_data.active_save(conn)
        injuries = export_game_center_ui_data.injury_center_summary(
            conn,
            current_date=context.get("current_date"),
            user_team_id=(active or {}).get("user_team_id"),
            active_limit=active_limit,
            recent_limit=recent_limit,
        )
    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "injuries": injuries,
    }


def draft_payload_for_active_db(year: int | None = None) -> dict[str, Any]:
    db_path = active_db_path()
    context = game_context(db_path)
    target_year = int(year or context["draftYear"])
    current_season = int(context["currentSeason"])
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        active = export_game_center_ui_data.active_save(conn) or {}
        user_team_id = active.get("user_team_id")
        draft = export_game_center_ui_data.draft_summary(
            conn,
            target_year,
            user_team_id=user_team_id,
            game_id=(active or {}).get("game_id") or (active or {}).get("save_id"),
            current_date_value=str(context["currentDate"]),
        )
        rookie_class = {
            "year": current_season,
            "selections": export_game_center_ui_data.draft_user_selections(conn, current_season, user_team_id),
        }
    return {
        "year": target_year,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "draft": draft,
        "rookieClass": rookie_class,
    }


def scouting_payload_for_active_db(limit: int = 240) -> dict[str, Any]:
    db_path = active_db_path()
    context = game_context(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        active = export_game_center_ui_data.active_save(conn) or {}
        user_team_id = active.get("user_team_id")
        draft_year = export_game_center_ui_data.draft_year(conn, int(context["currentSeason"]))
        draft = export_game_center_ui_data.draft_summary(
            conn,
            draft_year,
            user_team_id=user_team_id,
            game_id=(active or {}).get("game_id") or (active or {}).get("save_id"),
            current_date_value=str(context["currentDate"]),
        )
        scouting_payload = export_game_center_ui_data.enrich_scouting_payload_with_draft_board(
            export_game_center_ui_data.scouting.build_ui_payload(conn, limit=limit),
            draft,
        )
    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "scouting": scouting_payload,
    }


def free_agency_payload_for_active_db(league_year: int | None = None) -> dict[str, Any]:
    db_path = active_db_path()
    context = game_context(db_path)
    target_year = int(league_year or context["freeAgencyYear"])
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        try:
            free_agency_processor.ensure_schema(conn)
            free_agency_processor.active_period(conn, target_year)
            conn.commit()
        except Exception:
            conn.rollback()
        free_agency = export_game_center_ui_data.free_agency_summary(conn, target_year)
    return {
        "leagueYear": target_year,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "freeAgency": free_agency,
    }


def contracts_payload_for_active_db(season: int | None = None) -> dict[str, Any]:
    db_path = active_db_path()
    context = game_context(db_path)
    target_season = int(season or context["currentSeason"])
    user_team = context.get("userTeam")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        contracts = export_game_center_ui_data.contract_negotiation_summary(conn, target_season, user_team)
    return {
        "season": target_season,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "contractNegotiations": contracts,
    }


def depth_chart_payload_for_active_db(
    season: int | None = None,
    team: str | None = None,
    contract_season: int | None = None,
) -> dict[str, Any]:
    db_path = active_db_path()
    context = game_context(db_path)
    target_season = int(season or context["currentSeason"])
    target_contract_season = int(contract_season or context["currentContractYear"])
    target_team = team or context.get("userTeam") or "MIN"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        depth_chart = export_game_center_ui_data.depth_chart_summary(
            conn,
            target_team,
            target_season,
            target_contract_season,
        )
    return {
        "season": target_season,
        "contractSeason": target_contract_season,
        "team": target_team,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "depthChart": depth_chart,
    }


def practice_squad_payload_for_active_db(season: int | None = None, team: str | None = None) -> dict[str, Any]:
    db_path = active_db_path()
    context = game_context(db_path)
    target_season = int(season or context["currentSeason"])
    target_team = str(team or context.get("userTeam") or "MIN").upper()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        roster_rules.ensure_schema(conn)
        roster_rules.seed_rules(conn)
        team_row = roster_rules.get_team(conn, target_team)
        rule_set = roster_rules.practice_squad_rule_set(conn, target_season, "Regular Season")
        usage = roster_rules.practice_squad_usage(conn, int(team_row["team_id"]), rule_set)
        rows = roster_rules.practice_squad_eligibility_rows(
            conn,
            team=team_row,
            season=target_season,
            rule_set=rule_set,
            include_active=True,
            include_all_active=True,
            include_current=True,
            include_blocked=True,
            limit=260,
        )
        active_count = roster_rules.active_roster_count(conn, int(team_row["team_id"]))
        if export_game_center_ui_data.table_exists(conn, "transaction_log_view"):
            recent_moves = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                        transaction_id,
                        transaction_date,
                        transaction_type,
                        team,
                        from_team,
                        to_team,
                        player_id,
                        player_name,
                        player_position,
                        description
                    FROM transaction_log_view
                    WHERE season = ?
                      AND transaction_type IN ('Practice Squad Poaching', 'Practice Squad Signing', 'Practice Squad Release')
                      AND (
                            team_id = ?
                         OR from_team_id = ?
                         OR to_team_id = ?
                         OR secondary_team_id = ?
                      )
                    ORDER BY transaction_date DESC, transaction_id DESC
                    LIMIT 12
                    """,
                    (
                        target_season,
                        int(team_row["team_id"]),
                        int(team_row["team_id"]),
                        int(team_row["team_id"]),
                        int(team_row["team_id"]),
                    ),
                ).fetchall()
            ]
        else:
            recent_moves = []
    limits = {
        "active": int(rule_set["active_roster_limit"] or 53),
        "base": int(rule_set["practice_squad_limit"] or 16),
        "developmental": int(rule_set["practice_squad_developmental_limit"] or 10),
        "veteranException": int(rule_set["practice_squad_veteran_exception_limit"] or 6),
        "internationalExemption": int(rule_set["practice_squad_international_exemption_limit"] or 1),
        "total": int(rule_set["practice_squad_limit"] or 16) + int(rule_set["practice_squad_international_exemption_limit"] or 0),
    }
    return {
        "season": target_season,
        "team": target_team,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "practiceSquad": {
            "team": target_team,
            "phase": rule_set["phase"],
            "enabled": bool(int(rule_set["practice_squad_enabled"] or 0)),
            "activeCount": active_count,
            "activeLimit": limits["active"],
            "usage": usage,
            "limits": limits,
            "candidates": rows,
            "recentMoves": recent_moves,
        },
    }


def ai_gm_payload_for_active_db(season: int | None = None, team: str | None = None) -> dict[str, Any]:
    db_path = active_db_path()
    context = game_context(db_path)
    target_season = int(season or context["currentSeason"])
    target_team = team or context.get("userTeam") or "MIN"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        active = export_game_center_ui_data.active_save(conn) or {}
        game_id = active.get("game_id") or active.get("save_id")
        ai_gm = export_game_center_ui_data.ai_gm_summary(conn, target_team, game_id, target_season)
    return {
        "season": target_season,
        "team": target_team,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "aiGm": ai_gm,
    }


def refresh_static_data_asset(path: str) -> None:
    db_path = active_db_path()
    normalized = path.replace("\\", "/")
    if normalized.endswith("/ui/game_center/game-center-data.js"):
        export_game_center_ui_data.export(db_path, GAME_CENTER_OUTPUT)
        return
    if normalized.endswith("/ui/app_shell/app-shell-data.js"):
        export_app_shell_ui_data.export(db_path, APP_SHELL_OUTPUT)
        return
    if normalized.endswith("/ui/front_office/front-office-data.js"):
        context = game_context(db_path)
        export_front_office_ui_data.export(db_path, FRONT_OFFICE_OUTPUT, int(context["currentSeason"]))
        return
    if normalized.endswith("/ui/player_card/player-data.js"):
        payload = export_game_center_ui_data.build_payload(db_path)
        export_player_card_ui_data.export(db_path, PLAYER_CARD_OUTPUT, player_export_season(payload))
        return
    if normalized.endswith("/ui/player_profile/player-profile-data.js"):
        payload = export_game_center_ui_data.build_payload(db_path)
        export_player_profile_ui_data.export(db_path, PLAYER_PROFILE_OUTPUT, player_export_season(payload))
        return


def assign_practice_squad_player(player_id: int, team: str, season: int) -> str:
    db_path = active_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        roster_rules.ensure_schema(conn)
        roster_rules.seed_rules(conn)
        team_row = roster_rules.get_team(conn, team)
        rule_set = roster_rules.practice_squad_rule_set(conn, season, "Regular Season")
        player = conn.execute(
            """
            SELECT p.*, t.abbreviation AS team
            FROM players p
            LEFT JOIN teams t ON t.team_id = p.team_id
            WHERE p.player_id = ?
            """,
            (player_id,),
        ).fetchone()
        if not player:
            raise ValueError(f"Player not found: {player_id}")
        own_team = player["team_id"] is not None and int(player["team_id"]) == int(team_row["team_id"])
        if player["status"] not in {"Active", roster_rules.PRACTICE_SQUAD_STATUS, "Free Agent", roster_rules.WAIVED_STATUS}:
            raise ValueError(f"{roster_rules.player_name(player)} has status {player['status']} and cannot be assigned.")
        if player["status"] == roster_rules.PRACTICE_SQUAD_STATUS and own_team:
            raise ValueError(f"{roster_rules.player_name(player)} is already on the practice squad.")
        if player["status"] == "Active" and not own_team:
            raise ValueError("Only your own active players can be moved directly to the practice squad.")
        eligibility = roster_rules.practice_squad_eligibility(conn, player, team_row, rule_set, season=season)
        if not eligibility["eligible"]:
            raise ValueError(
                f"{roster_rules.player_name(player)} is not practice-squad eligible: "
                + " ".join(str(item) for item in eligibility["blockers"])
            )
        old_status = player["status"]
        if old_status == roster_rules.WAIVED_STATUS:
            conn.execute(
                "UPDATE waiver_wire SET status = 'Cancelled', resolved_at = datetime('now') WHERE player_id = ? AND status = 'Open'",
                (player_id,),
            )
        roster_rules.set_player_status(
            conn,
            player=player,
            team_id=int(team_row["team_id"]),
            new_status=roster_rules.PRACTICE_SQUAD_STATUS,
            season=season,
            reason="Assigned through roster cutdown registration.",
        )
        roster_rules.clear_depth_chart(conn, player_id)
        roster_rules.record_practice_squad_move(
            conn,
            player_id=player_id,
            team_id=int(team_row["team_id"]),
            season=season,
            move_type="Sign",
            from_status=old_status,
            to_status=roster_rules.PRACTICE_SQUAD_STATUS,
            notes="Assigned through UI squad registration.",
        )
        transaction_id = roster_rules.log_transaction(
            conn,
            transaction_type="Practice Squad Signing",
            season=season,
            team_id=int(team_row["team_id"]),
            player_id=player_id,
            to_team_id=int(team_row["team_id"]),
            old_status=old_status,
            new_status=roster_rules.PRACTICE_SQUAD_STATUS,
            description=f"Assigned {roster_rules.player_name(player)} to {team_row['abbreviation']} practice squad.",
        )
        conn.commit()
    return f"Assigned {roster_rules.player_name(player)} to {team} practice squad (transaction {transaction_id})."


def release_practice_squad_player(player_id: int, team: str, season: int) -> str:
    db_path = active_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        roster_rules.ensure_schema(conn)
        roster_rules.seed_rules(conn)
        team_row = roster_rules.get_team(conn, team)
        player = conn.execute(
            """
            SELECT p.*, t.abbreviation AS team
            FROM players p
            LEFT JOIN teams t ON t.team_id = p.team_id
            WHERE p.player_id = ?
              AND p.team_id = ?
              AND p.status = ?
            """,
            (player_id, int(team_row["team_id"]), roster_rules.PRACTICE_SQUAD_STATUS),
        ).fetchone()
        if not player:
            raise ValueError("Player is not on this practice squad.")
        old_status = player["status"]
        roster_rules.set_player_status(
            conn,
            player=player,
            team_id=None,
            new_status="Free Agent",
            season=season,
            reason="Released from practice squad.",
        )
        roster_rules.record_practice_squad_move(
            conn,
            player_id=player_id,
            team_id=int(team_row["team_id"]),
            season=season,
            move_type="Release",
            from_status=old_status,
            to_status="Free Agent",
            notes="Released through UI squad registration.",
        )
        transaction_id = roster_rules.log_transaction(
            conn,
            transaction_type="Practice Squad Release",
            season=season,
            team_id=int(team_row["team_id"]),
            player_id=player_id,
            from_team_id=int(team_row["team_id"]),
            old_status=old_status,
            new_status="Free Agent",
            description=f"Released {roster_rules.player_name(player)} from {team_row['abbreviation']} practice squad.",
        )
        conn.commit()
    return f"Released {roster_rules.player_name(player)} from {team} practice squad (transaction {transaction_id})."


def action_command(action: str, params: dict[str, Any], state: dict[str, Any]) -> list[str]:
    season = int(state.get("currentSeason") or 2026)
    draft_year = int(state.get("draft", {}).get("year") or season + 1)
    free_agency_year = int((state.get("freeAgency") or {}).get("leagueYear") or season)
    active_save = state.get("activeSave") or {}
    control_mode = str(active_save.get("control_mode") or state.get("settings", {}).get("control_mode") or "team").lower()
    user_team = (
        active_save.get("user_team")
        or params.get("user_team")
        or (None if control_mode == "observe" else "MIN")
    )

    def play(*args: str) -> list[str]:
        return [sys.executable, str(ROOT / "tools" / "play.py"), *args]

    if action == "status":
        return play("status")
    if action == "preflight":
        return play("preflight")
    if action == "new_june1_save":
        start_year = int(params.get("start_year") or season or 2026)
        new_control_mode = str(params.get("control_mode") or ("observe" if params.get("observe_mode") else "team")).lower()
        observe_mode = new_control_mode == "observe"
        team = "" if observe_mode else str(params.get("user_team") or user_team or "MIN").upper()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        game_id = str(params.get("game_id") or f"{'observe' if observe_mode else team.lower()}_{start_year}_june1_{stamp}")
        name = str(params.get("name") or ("Observe June 1 Start" if observe_mode else f"{team} June 1 Start"))
        command = [
            "new",
            "--game-id",
            game_id,
            "--name",
            name,
            "--control-mode",
            "observe" if observe_mode else "team",
            "--start-year",
            str(start_year),
        ]
        if observe_mode:
            command.append("--observe-mode")
        else:
            command.extend(["--user-team", team])
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
    if action == "delete_save":
        game_id = str(params.get("game_id") or "").strip()
        if not game_id:
            raise ValueError("delete_save requires game_id.")
        stopped = stop_processes_using_save(game_id)
        if stopped:
            print(f"Stopped {len(stopped)} process(es) using save {game_id}: {', '.join(str(pid) for pid in stopped)}")
        return play("delete-save", game_id)
    if action == "take_over_team":
        team = str(params.get("team") or "").strip().upper()
        if not team:
            raise ValueError("take_over_team requires team.")
        return play("take-over-team", "--team", team)
    if action == "draft_class_generate":
        draft_year = int(params.get("draft_year") or draft_year)
        command = [
            "draft-class",
            "generate",
            "--draft-year",
            str(draft_year),
        ]
        if params.get("force"):
            command.append("--force")
        return play(*command)
    if action == "draft_class_import":
        draft_year = int(params.get("draft_year") or draft_year)
        package = str(params.get("package") or "").strip()
        if not package:
            raise ValueError("draft_class_import requires package.")
        command = [
            "draft-class",
            "import",
            "--draft-year",
            str(draft_year),
            "--package",
            package,
        ]
        if params.get("force"):
            command.append("--force")
        return play(*command)
    if action == "advance_next_event":
        command = play("advance-to-next-event")
        if params.get("auto_roster_cutdown"):
            command.append("--auto-roster-cutdown")
        return command
    if action == "advance_to_date":
        target_date = str(params.get("date") or "").strip()
        if not target_date:
            raise ValueError("advance_to_date requires date.")
        command = play("advance-to-date", "--date", target_date)
        if params.get("auto_roster_cutdown"):
            command.append("--auto-roster-cutdown")
        return command
    if action == "validate_rosters":
        return play("validate-rosters", "--summary-only")
    if action == "auto_cutdown":
        return play("roster-cutdown", "--season", str(season), "--apply")
    if action == "sim_week":
        week = params.get("week") or state.get("season", {}).get("nextWeek")
        if not week:
            raise ValueError("No next scheduled week is available.")
        game_type = str(params.get("game_type") or state.get("season", {}).get("nextGameType") or "REG").upper()
        if game_type not in {"REG", "PRE"}:
            game_type = "REG"
        command = play("sim-week", str(int(week)), "--season", str(season), "--game-type", game_type, "--apply")
        if params.get("skip_roster_gate") or params.get("auto_roster_cutdown"):
            command.append("--skip-roster-gate")
        return command
    if action == "sim_season":
        game_type = str(params.get("game_type") or state.get("season", {}).get("nextGameType") or "REG").upper()
        if game_type not in {"REG", "PRE"}:
            game_type = "REG"
        command = play("sim-season", "--season", str(season), "--game-type", game_type, "--apply", "--seed", f"{season}00")
        if params.get("skip_roster_gate") or params.get("auto_roster_cutdown"):
            command.append("--skip-roster-gate")
        return command
    if action == "postseason":
        return play("postseason", "run", "--season", str(season), "--apply", "--seed", f"{season}99")
    if action == "postseason_round":
        return play("postseason", "round", "--season", str(season), "--apply", "--seed", f"{season}99")
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
    if action == "contract_tag":
        player_id = params.get("player_id")
        if not player_id:
            raise ValueError("contract_tag requires player_id.")
        tag_type = str(params.get("tag_type") or "franchise").lower()
        if tag_type not in {"franchise", "exclusive", "transition", "rfa_first", "rfa_second", "rfa_original", "rfa_rofr", "erfa"}:
            raise ValueError("contract_tag tag_type must be franchise, exclusive, transition, rfa_first, rfa_second, rfa_original, rfa_rofr, or erfa.")
        return play(
            "contract",
            "tag",
            "--season",
            str(season),
            "--team",
            str(user_team),
            "--player-id",
            str(int(player_id)),
            "--tag-type",
            tag_type,
            "--apply",
            "--fast",
        )
    if action in {"contract_option_exercise", "contract_option_decline"}:
        player_id = params.get("player_id")
        if not player_id:
            raise ValueError(f"{action} requires player_id.")
        command = [
            "contract",
            "option-exercise" if action == "contract_option_exercise" else "option-decline",
            "--league-year",
            str(season + 1),
            "--team",
            str(user_team),
            "--player-id",
            str(int(player_id)),
            "--apply",
        ]
        if action == "contract_option_exercise":
            command.append("--fast")
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
    if action == "roster_release_player":
        player_id = params.get("player_id")
        if not player_id:
            raise ValueError("roster_release_player requires player_id.")
        with sqlite3.connect(active_db_path()) as con:
            con.row_factory = sqlite3.Row
            player = con.execute(
                """
                SELECT p.player_id, p.first_name || ' ' || p.last_name AS player_name
                FROM players p
                JOIN teams t ON t.team_id = p.team_id
                WHERE p.player_id = ? AND t.abbreviation = ?
                """,
                (int(player_id), str(user_team)),
            ).fetchone()
        if not player:
            raise ValueError("Player is not on the active user-team roster.")
        command = [
            "roster",
            "release",
            "--team",
            str(user_team),
            "--player",
            str(player["player_name"]),
        ]
        if params.get("post_june1"):
            command.append("--post-june1")
        return play(*command)
    if action == "roster_change_number":
        player_id = params.get("player_id")
        number = params.get("number")
        if not player_id or number is None:
            raise ValueError("roster_change_number requires player_id and number.")
        player_id = int(player_id)
        number = int(number)
        if number < 0 or number > 99:
            raise ValueError("Jersey number must be between 0 and 99.")
        with sqlite3.connect(active_db_path()) as con:
            con.row_factory = sqlite3.Row
            team_row = con.execute("SELECT team_id FROM teams WHERE abbreviation = ?", (str(user_team),)).fetchone()
            if not team_row:
                raise ValueError(f"Team not found: {user_team}")
            team_id = int(team_row["team_id"])
            player = con.execute(
                "SELECT player_id FROM players WHERE player_id = ? AND team_id = ?",
                (player_id, team_id),
            ).fetchone()
            if not player:
                raise ValueError("Player is not on the active user-team roster.")
            conflict = con.execute(
                """
                SELECT first_name || ' ' || last_name AS player_name
                FROM players
                WHERE team_id = ? AND jersey_number = ? AND player_id != ?
                  AND COALESCE(status, 'Active') != 'Retired'
                LIMIT 1
                """,
                (team_id, number, player_id),
            ).fetchone()
            if conflict:
                raise ValueError(f"#{number} is already assigned to {conflict['player_name']}.")
            con.execute("UPDATE players SET jersey_number = ? WHERE player_id = ?", (number, player_id))
            con.commit()
        return [sys.executable, "-c", f"print('Jersey number updated to #{number}.')"]
    if action == "practice_squad_assign":
        player_id = params.get("player_id")
        if not player_id:
            raise ValueError("practice_squad_assign requires player_id.")
        message = assign_practice_squad_player(int(player_id), str(user_team), season)
        return [sys.executable, "-c", f"print({json.dumps(message)})"]
    if action == "practice_squad_release":
        player_id = params.get("player_id")
        if not player_id:
            raise ValueError("practice_squad_release requires player_id.")
        message = release_practice_squad_player(int(player_id), str(user_team), season)
        return [sys.executable, "-c", f"print({json.dumps(message)})"]
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
            or f"{free_agency_year}-03-10"
        )
        return play(
            "free-agency",
            "start",
            "--league-year",
            str(free_agency_year),
            "--start-date",
            str(start_date),
            "--opening-cpu-offers",
            str(int(params.get("opening_cpu_offers") or 112)),
            "--cpu-resign-per-team",
            str(int(params.get("cpu_resign_per_team") or 2)),
            "--cpu-retention-per-team",
            str(int(params.get("cpu_retention_per_team") or 1)),
            "--no-cap-snapshot",
            "--apply",
        )
    if action == "free_agency_advance_hour":
        return play(
            "free-agency",
            "advance-hour",
            "--league-year",
            str(free_agency_year),
            "--cpu-offers",
            str(int(params.get("cpu_offers") or 44)),
            "--no-cap-snapshot",
            "--apply",
        )
    if action == "free_agency_advance_day":
        days = int(params.get("days") or 1)
        return play(
            "free-agency",
            "advance-day",
            "--league-year",
            str(free_agency_year),
            "--days",
            str(days),
            "--cpu-offers",
            str(int(params.get("cpu_offers") or 36)),
            "--no-cap-snapshot",
            "--apply",
        )
    if action == "free_agency_offer":
        if not user_team:
            raise ValueError("Free-agent offers require a team-controlled save.")
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
            str(free_agency_year),
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
            "--structure",
            str(params.get("structure") or "balanced"),
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
            str(free_agency_year),
            "--cpu-offers",
            str(int(params.get("cpu_offers") or 64)),
            "--no-cap-snapshot",
            "--apply",
        )
    if action == "advance_to_draft":
        command = play("advance-to-draft", "--draft-year", str(draft_year))
        if user_team:
            command.extend(["--user-team", str(user_team)])
        if params.get("auto_roster_cutdown"):
            command.append("--auto-roster-cutdown")
        return command
    if action == "advance_next_league_year":
        command = play("advance-to-next-league-year")
        if params.get("auto_roster_cutdown"):
            command.append("--auto-roster-cutdown")
        return command
    if action == "draft_start":
        command = ["draft-room", "start", "--draft-year", str(draft_year), "--paused", "--apply"]
        if user_team:
            command.extend(["--user-team", str(user_team)])
        return play(*command)
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
            "--commit-each",
            "--apply",
        )
    if action == "draft_skip_to_user":
        remaining = remaining_draft_pick_count(draft_year)
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
        remaining = remaining_draft_pick_count(draft_year)
        return play(
            "draft-room",
            "skip",
            "--draft-year",
            str(draft_year),
            "--count",
            str(max(remaining, 1)),
            "--include-user-pick",
            "--no-cap-snapshot",
            "--commit-each",
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
    if action == "draft_user_trade":
        if not user_team:
            raise ValueError("User draft trades require a team-controlled save.")
        target_pick_id = params.get("target_pick_id")
        if not target_pick_id:
            raise ValueError("draft_user_trade requires target_pick_id.")
        command = [
            "draft-room",
            "user-trade",
            "--draft-year",
            str(draft_year),
            "--target-pick-id",
            str(int(target_pick_id)),
            "--user-team",
            str(user_team),
            "--apply",
        ]
        for pick_id in params.get("offer_pick_ids") or []:
            command.extend(["--offer-pick-id", str(int(pick_id))])
        return play(*command)
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
    if action == "ai_gm_autonomy_show":
        command = ["ai-gm", "autonomy-show"]
        if params.get("team"):
            command.extend(["--team", str(params["team"])])
        return play(*command)
    if action == "ai_gm_autonomy_config":
        mode = str(params.get("mode") or "advisory_only")
        command = ["ai-gm", "autonomy-config", "--mode", mode, "--queue-llm"]
        if params.get("team"):
            command.extend(["--team", str(params["team"])])
        if mode == "auto_apply_low_risk" or params.get("auto_apply_low_risk"):
            command.append("--auto-apply-low-risk")
        else:
            command.append("--no-auto-apply-low-risk")
        return play(*command)
    if action == "ai_gm_daily_run":
        command = ["ai-gm", "daily-run"]
        if params.get("all"):
            command.append("--all")
        else:
            command.extend(["--team", str(params.get("team") or user_team)])
        command.extend(["--phase", str(params.get("phase") or "auto")])
        if params.get("mode"):
            command.extend(["--mode", str(params["mode"])])
        if params.get("limit"):
            command.extend(["--limit", str(int(params["limit"]))])
        if params.get("include_user_team"):
            command.append("--include-user-team")
        if params.get("persist"):
            command.append("--persist")
        if params.get("apply"):
            command.append("--apply")
        return play(*command)
    if action == "ai_gm_review_inbox":
        command = ["ai-gm", "review-inbox"]
        if params.get("team"):
            command.extend(["--team", str(params["team"])])
        status = params.get("status")
        if status:
            command.extend(["--status", str(status)])
        if params.get("risk"):
            command.extend(["--risk", str(params["risk"])])
        if params.get("type"):
            command.extend(["--type", str(params["type"])])
        command.extend(["--limit", str(int(params.get("limit") or 20))])
        return play(*command)
    if action == "ai_gm_review_history":
        command = ["ai-gm", "review-history"]
        if params.get("team"):
            command.extend(["--team", str(params["team"])])
        status = params.get("status")
        if status:
            command.extend(["--status", str(status)])
        if params.get("risk"):
            command.extend(["--risk", str(params["risk"])])
        if params.get("type"):
            command.extend(["--type", str(params["type"])])
        command.extend(["--limit", str(int(params.get("limit") or 20))])
        return play(*command)
    if action == "ai_gm_review_show":
        review_id = params.get("review_id")
        if not review_id:
            raise ValueError("ai_gm_review_show requires review_id.")
        return play("ai-gm", "review-show", "--review-id", str(int(review_id)))
    if action == "ai_gm_review_update":
        review_id = params.get("review_id")
        status = params.get("status")
        if not review_id:
            raise ValueError("ai_gm_review_update requires review_id.")
        if not status:
            raise ValueError("ai_gm_review_update requires status.")
        command = ["ai-gm", "review-update", "--review-id", str(int(review_id)), "--status", str(status)]
        if params.get("note"):
            command.extend(["--note", str(params["note"])])
        if params.get("reviewed_by"):
            command.extend(["--reviewed-by", str(params["reviewed_by"])])
        return play(*command)
    if action == "ai_gm_review_apply":
        command = ["ai-gm", "review-apply"]
        if params.get("review_id"):
            command.extend(["--review-id", str(int(params["review_id"]))])
        else:
            command.append("--all-approved")
        if params.get("team"):
            command.extend(["--team", str(params["team"])])
        if params.get("risk"):
            command.extend(["--risk", str(params["risk"])])
        if params.get("type"):
            command.extend(["--type", str(params["type"])])
        if params.get("limit"):
            command.extend(["--limit", str(int(params["limit"]))])
        if params.get("allow_warning"):
            command.append("--allow-warning")
        if params.get("allow_stale"):
            command.append("--allow-stale")
        if params.get("apply"):
            command.append("--apply")
        return play(*command)
    if action == "ai_gm_dev_seed_review":
        command = ["ai-gm", "dev-seed-review", "--team", str(params.get("team") or user_team)]
        if params.get("clear_existing", True):
            command.append("--clear-existing")
        return play(*command)
    if action == "ai_gm_dev_clear_reviews":
        command = ["ai-gm", "dev-clear-reviews"]
        if params.get("team"):
            command.extend(["--team", str(params["team"])])
        return play(*command)
    if action == "ai_gm_profiles":
        return play("ai-gm", "profiles", "--team", str(params.get("team") or user_team), "--season", str(season))
    if action == "ai_gm_evaluate":
        command = ["ai-gm", "evaluate", "--team", str(params.get("team") or user_team), "--season", str(season)]
        if params.get("persist"):
            command.append("--persist")
        return play(*command)
    if action == "ai_gm_cutdown_plan":
        command = ["ai-gm", "cutdown-plan", "--team", str(params.get("team") or user_team), "--season", str(season)]
        if params.get("persist"):
            command.append("--persist")
        return play(*command)
    if action == "ai_gm_cutdown_plan_persist":
        return play(
            "ai-gm",
            "cutdown-plan",
            "--team",
            str(params.get("team") or user_team),
            "--season",
            str(season),
            "--persist",
        )
    if action == "ai_gm_cutdown_plans":
        return play(
            "ai-gm",
            "cutdown-plans",
            "--team",
            str(params.get("team") or user_team),
            "--limit",
            str(int(params.get("limit") or 12)),
        )
    if action == "ai_gm_contract_plan":
        command = ["ai-gm", "contract-plan", "--team", str(params.get("team") or user_team), "--season", str(season)]
        if params.get("persist"):
            command.append("--persist")
        return play(*command)
    if action == "ai_gm_contract_plan_persist":
        return play(
            "ai-gm",
            "contract-plan",
            "--team",
            str(params.get("team") or user_team),
            "--season",
            str(season),
            "--persist",
        )
    if action == "ai_gm_contract_plans":
        return play(
            "ai-gm",
            "contract-plans",
            "--team",
            str(params.get("team") or user_team),
            "--limit",
            str(int(params.get("limit") or 12)),
        )
    if action == "ai_gm_apply_contract_plan":
        plan_id = params.get("plan_id")
        if not plan_id:
            raise ValueError("ai_gm_apply_contract_plan requires plan_id.")
        command = [
            "ai-gm",
            "apply-contract-plan",
            "--plan-id",
            str(int(plan_id)),
            "--max-extensions",
            str(int(params.get("max_extensions") or 4)),
        ]
        if params.get("allow_stale"):
            command.append("--allow-stale")
        if params.get("max_total_aav"):
            command.extend(["--max-total-aav", str(int(params["max_total_aav"]))])
        if params.get("apply"):
            command.append("--apply")
        return play(*command)
    if action == "ai_gm_free_agent_plan":
        command = [
            "ai-gm",
            "free-agent-plan",
            "--team",
            str(params.get("team") or user_team),
            "--league-year",
            str(int(params.get("league_year") or season)),
            "--season",
            str(season),
        ]
        if params.get("persist"):
            command.append("--persist")
        if params.get("refresh_market"):
            command.append("--refresh-market")
        return play(*command)
    if action == "ai_gm_free_agent_plan_persist":
        return play(
            "ai-gm",
            "free-agent-plan",
            "--team",
            str(params.get("team") or user_team),
            "--league-year",
            str(int(params.get("league_year") or season)),
            "--season",
            str(season),
            "--persist",
        )
    if action == "ai_gm_free_agent_plans":
        return play(
            "ai-gm",
            "free-agent-plans",
            "--team",
            str(params.get("team") or user_team),
            "--limit",
            str(int(params.get("limit") or 12)),
        )
    if action == "ai_gm_draft_plan":
        command = [
            "ai-gm",
            "draft-plan",
            "--team",
            str(params.get("team") or user_team),
            "--draft-year",
            str(int(params.get("draft_year") or draft_year)),
            "--season",
            str(season),
        ]
        if params.get("persist"):
            command.append("--persist")
        return play(*command)
    if action == "ai_gm_draft_plan_persist":
        return play(
            "ai-gm",
            "draft-plan",
            "--team",
            str(params.get("team") or user_team),
            "--draft-year",
            str(int(params.get("draft_year") or draft_year)),
            "--season",
            str(season),
            "--persist",
        )
    if action == "ai_gm_draft_plans":
        return play(
            "ai-gm",
            "draft-plans",
            "--team",
            str(params.get("team") or user_team),
            "--draft-year",
            str(int(params.get("draft_year") or draft_year)),
            "--limit",
            str(int(params.get("limit") or 12)),
        )
    if action == "ai_gm_apply_free_agent_plan":
        plan_id = params.get("plan_id")
        if not plan_id:
            raise ValueError("ai_gm_apply_free_agent_plan requires plan_id.")
        command = [
            "ai-gm",
            "apply-free-agent-plan",
            "--plan-id",
            str(int(plan_id)),
            "--max-offers",
            str(int(params.get("max_offers") or 4)),
        ]
        if params.get("allow_stale"):
            command.append("--allow-stale")
        if params.get("max_total_aav"):
            command.extend(["--max-total-aav", str(int(params["max_total_aav"]))])
        if params.get("apply"):
            command.append("--apply")
        return play(*command)
    if action == "ai_gm_offseason_run":
        command = [
            "ai-gm",
            "offseason-run",
            "--phase",
            str(params.get("phase") or "pre-free-agency"),
            "--season",
            str(int(params.get("season") or season)),
        ]
        if params.get("team"):
            command.extend(["--team", str(params["team"])])
        else:
            command.append("--all")
        if params.get("league_year"):
            command.extend(["--league-year", str(int(params["league_year"]))])
        if params.get("include_user_team"):
            command.append("--include-user-team")
        if params.get("allow_stale"):
            command.append("--allow-stale")
        if params.get("max_extensions_per_team"):
            command.extend(["--max-extensions-per-team", str(int(params["max_extensions_per_team"]))])
        if params.get("max_extension_aav"):
            command.extend(["--max-extension-aav", str(int(params["max_extension_aav"]))])
        if params.get("max_offers_per_team"):
            command.extend(["--max-offers-per-team", str(int(params["max_offers_per_team"]))])
        if params.get("max_fa_aav"):
            command.extend(["--max-fa-aav", str(int(params["max_fa_aav"]))])
        if params.get("refresh_market"):
            command.append("--refresh-market")
        if params.get("apply"):
            command.append("--apply")
        return play(*command)
    if action == "ai_gm_ops":
        command = [
            "ai-gm",
            "ops",
            "--team",
            str(params.get("team") or user_team),
            "--phase",
            str(params.get("phase") or "auto"),
            "--limit",
            str(int(params.get("limit") or 20)),
        ]
        if params.get("enqueue"):
            command.append("--enqueue")
        return play(*command)
    if action == "ai_gm_queue":
        return play(
            "ai-gm",
            "queue",
            "--team",
            str(params.get("team") or user_team),
            "--limit",
            str(int(params.get("limit") or 12)),
        )
    if action == "ai_gm_process_queue":
        return play(
            "ai-gm",
            "process-queue",
            "--team",
            str(params.get("team") or user_team),
            "--limit",
            str(int(params.get("limit") or 3)),
        )
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
        return play("scouting", "assign", "--prospect-id", str(int(prospect_id)))
    if action == "scouting_assign_batch":
        prospect_ids = params.get("prospect_ids") or []
        if not isinstance(prospect_ids, list) or not prospect_ids:
            raise ValueError("scouting_assign_batch requires prospect_ids.")
        command = ["scouting", "assign-batch"]
        for prospect_id in prospect_ids:
            command.extend(["--prospect-id", str(int(prospect_id))])
        return play(*command)
    if action == "scouting_unassign":
        prospect_id = params.get("prospect_id")
        if not prospect_id:
            raise ValueError("scouting_unassign requires prospect_id.")
        return play("scouting", "unassign", "--prospect-id", str(int(prospect_id)))
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


def first_output_line(stdout: str, stderr: str) -> str:
    for text in (stdout, stderr):
        for line in (text or "").splitlines():
            clean = line.strip()
            if clean:
                return clean
    return ""


def action_response_summary(
    action: str,
    params: dict[str, Any],
    returncode: int,
    stdout: str,
    stderr: str,
    duration_seconds: float,
) -> dict[str, Any]:
    label = action.replace("_", " ").title()
    output_line = first_output_line(stdout, stderr)
    ok = returncode == 0
    summary: dict[str, Any] = {
        "title": label,
        "message": output_line or (f"{label} complete." if ok else f"{label} returned an issue."),
        "status": "ok" if ok else "error",
        "durationSeconds": round(duration_seconds, 2),
        "affectedPanels": [],
    }
    if action in DRAFT_RUN_ACTIONS:
        summary["affectedPanels"] = ["draft", "scouting", "season", "calendar"]
        summary["title"] = {
            "advance_to_draft": "Advanced To Draft",
            "draft_start": "Draft Room Started",
            "draft_pause": "Draft Room Paused",
            "draft_resume": "Draft Room Resumed",
            "draft_pick": "Draft Pick Submitted",
            "draft_user_trade": "Draft Trade Proposed",
            "draft_skip": "Draft Pick Skipped",
            "draft_skip_to_user": "Advanced To User Pick",
            "draft_finish": "Draft Finished",
        }.get(action, "Draft Updated")
        if action == "draft_user_trade" and "Trade rejected" in (stdout or ""):
            summary["title"] = "Draft Trade Rejected"
            summary["status"] = "warning"
        elif action == "draft_user_trade" and "Trade accepted" in (stdout or ""):
            summary["title"] = "Draft Trade Accepted"
    elif action in FREE_AGENCY_RUN_ACTIONS:
        summary["affectedPanels"] = ["freeAgency", "contracts", "calendar", "season"]
        summary["title"] = {
            "free_agency_start": "Free Agency Opened",
            "free_agency_cpu_seed": "CPU Offers Seeded",
            "free_agency_advance_hour": "Free Agency Hour Advanced",
            "free_agency_advance_day": "Free Agency Day Advanced",
            "free_agency_resolve": "Free Agency Resolved",
            "free_agency_offer": "Free-Agent Offer Submitted",
        }.get(action, "Free Agency Updated")
    elif action in CALENDAR_RUN_ACTIONS:
        summary["affectedPanels"] = ["calendar", "season", "inbox", "leagueNews"]
        summary["title"] = {
            "advance_next_event": "Advanced To Next Date",
            "advance_to_date": "Advanced To Date",
            "advance_next_league_year": "Advanced To Next League Year",
            "auto_cutdown_continue": "Auto Cutdown Complete",
        }.get(action, summary["title"])
    elif action == "auto_cutdown":
        summary["affectedPanels"] = ["roster", "depth", "contracts", "transactions"]
        summary["title"] = "Auto Cutdown Complete"
    if params.get("apply"):
        summary["mode"] = "applied"
    elif "dry" in action or "dry run" in (stdout or "").lower():
        summary["mode"] = "dry_run"
    elif action == "delete_save":
        summary["title"] = "Save Deleted" if returncode == 0 else "Delete Save Failed"
    return summary


ROSTER_GATE_MARKERS = (
    "Roster cutdown/practice squad setup required",
    "Stopping at final roster cutdown day",
    "Stopping when practice squads open",
)


def roster_gate_payload(action: str, params: dict[str, Any], stdout: str, stderr: str) -> dict[str, Any] | None:
    combined = f"{stdout or ''}\n{stderr or ''}"
    if not any(marker in combined for marker in ROSTER_GATE_MARKERS):
        return None
    state = payload_for_active_db()
    current_date = str(state.get("currentDate") or state.get("activeSave", {}).get("current_date") or "")
    season = int(state.get("currentSeason") or state.get("activeSave", {}).get("current_league_year") or 0)
    if current_date and season and current_date > f"{season}-09-15":
        return None
    return {
        "title": "Roster Cutdown Needed",
        "message": first_output_line(stdout, stderr)
        or "Final cuts and practice squad decisions are due before the regular season.",
        "stoppedAction": action,
        "stoppedParams": params or {},
    }


def run_auto_cutdown_continue_action(params: dict[str, Any]) -> dict[str, Any]:
    started = perf_counter()
    allowed_continue = {
        "advance_next_event",
        "advance_to_date",
        "advance_next_league_year",
        "advance_to_draft",
        "sim_week",
        "sim_season",
    }
    continue_action = str(params.get("continue_action") or "").strip()
    continue_params = params.get("continue_params") if isinstance(params.get("continue_params"), dict) else {}
    if continue_action not in allowed_continue:
        raise ValueError("auto_cutdown_continue requires a supported continue_action.")

    before = payload_for_active_db()
    season = int(before.get("currentSeason") or (before.get("season") or {}).get("season") or datetime.now().year)
    cutdown_command = action_command("auto_cutdown", {"season": season}, before)
    cutdown = subprocess.run(
        cutdown_command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=action_timeout_seconds("auto_cutdown_continue", params),
    )
    stdout_parts = [cutdown.stdout]
    stderr_parts = [cutdown.stderr]
    command_parts = [cutdown_command]
    returncode = cutdown.returncode
    if returncode == 0:
        after_cutdown = payload_for_active_db()
        continue_params = {**continue_params, "skip_roster_gate": True}
        continue_command = action_command(continue_action, continue_params, after_cutdown)
        command_parts.append(continue_command)
        if continue_action in SIM_CANCEL_ACTIONS:
            sim_control.clear_cancel(active_db_path())
        continued = subprocess.run(
            continue_command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=action_timeout_seconds(continue_action, params),
        )
        stdout_parts.append(continued.stdout)
        stderr_parts.append(continued.stderr)
        returncode = continued.returncode

    duration_seconds = perf_counter() - started
    stdout = "\n".join(part for part in stdout_parts if part)
    stderr = "\n".join(part for part in stderr_parts if part)
    response = {
        "action": "auto_cutdown_continue",
        "command": " && ".join(" ".join(f'"{part}"' if " " in str(part) else str(part) for part in command) for command in command_parts),
        "returncode": returncode,
        "duration_seconds": round(duration_seconds, 2),
        "stdout": stdout[-20000:],
        "stderr": stderr[-20000:],
    }
    response["summary"] = action_response_summary(
        "auto_cutdown_continue",
        params,
        returncode,
        stdout,
        stderr,
        duration_seconds,
    )
    after, app_shell_after = write_exports(include_players=True)
    response["state"] = after
    response["app_shell_state"] = app_shell_after
    response["continuedAction"] = continue_action
    response["rosterGate"] = roster_gate_payload(continue_action, continue_params, stdout, stderr)
    return response


def busy_action_response(action: str) -> dict[str, Any]:
    running = RUNNING_ACTION or "another action"
    state: dict[str, Any] | None = None
    try:
        state = payload_for_active_db()
    except Exception:
        state = None
    return {
        "action": action,
        "returncode": 1,
        "duration_seconds": 0,
        "stdout": "",
        "stderr": "",
        "summary": {
            "title": "Action Already Running",
            "message": f"{running.replace('_', ' ').title()} is already running. Wait for it to finish before starting another action.",
            "status": "warning",
            "durationSeconds": 0,
            "affectedPanels": [],
        },
        "state": state,
    }


def run_action(action: str, params: dict[str, Any]) -> dict[str, Any]:
    global RUNNING_ACTION
    if not RUN_ACTION_LOCK.acquire(blocking=False):
        return busy_action_response(action)
    RUNNING_ACTION = action
    try:
        return run_action_locked(action, params)
    finally:
        RUNNING_ACTION = None
        RUN_ACTION_LOCK.release()


def run_action_locked(action: str, params: dict[str, Any]) -> dict[str, Any]:
    if action == "box_score":
        return run_box_score_action(params)
    if action == "auto_cutdown_continue":
        return run_auto_cutdown_continue_action(params)
    if action in TRADE_RUN_ACTIONS:
        return run_trade_action(action, params)
    before = lightweight_action_state() if action in LIGHTWEIGHT_PRESTATE_ACTIONS else payload_for_active_db()
    command = action_command(action, params, before)
    if action in SIM_CANCEL_ACTIONS:
        sim_control.clear_cancel(active_db_path())
    injury_marker = None
    if action in INJURY_ALERT_ACTIONS:
        try:
            with sqlite3.connect(active_db_path()) as marker_con:
                marker_con.row_factory = sqlite3.Row
                injury_marker = injury_notifications.max_event_id(marker_con)
        except Exception:
            injury_marker = None
    started = perf_counter()
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=action_timeout_seconds(action, params),
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
    response["summary"] = action_response_summary(
        action,
        params,
        result.returncode,
        result.stdout,
        result.stderr,
        duration_seconds,
    )
    response["rosterGate"] = roster_gate_payload(action, params, result.stdout, result.stderr)
    if action in LIGHTWEIGHT_PRESTATE_ACTIONS:
        state_patch, app_shell_after = write_lightweight_action_exports(action)
        response["statePatch"] = state_patch
        response["app_shell_state"] = app_shell_after
        if action in {"new_june1_save", "load_game"} and result.returncode == 0:
            try:
                _after, refreshed_app_shell = write_exports(include_players=True)
                response["app_shell_state"] = refreshed_app_shell
                response["stateDeferred"] = True
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                response["refreshWarning"] = str(exc)
    else:
        after, app_shell_after = write_exports(include_players=include_players)
        response["state"] = after
        response["app_shell_state"] = app_shell_after
    if result.returncode == 0 and injury_marker is not None:
        try:
            with sqlite3.connect(active_db_path()) as alert_con:
                alert_con.row_factory = sqlite3.Row
                response["injuryAlerts"] = injury_notifications.alert_payloads_since(
                    alert_con,
                    min_event_id=injury_marker,
                )
        except Exception as exc:
            response["injuryAlertsError"] = str(exc)
    return response


def _trade_asset_from_ui(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    asset_type = str(item.get("type") or item.get("asset_type") or "").lower()
    if asset_type == "player" and item.get("playerId"):
        return {
            "asset_type": "PlayerContract",
            "player_id": int(item["playerId"]),
            "description": item.get("label"),
        }
    if asset_type == "pick" and item.get("pickId"):
        return {
            "asset_type": "DraftPick",
            "pick_id": int(item["pickId"]),
            "draft_year": int(item["draftYear"]) if item.get("draftYear") else None,
            "round": int(item["round"]) if item.get("round") else None,
            "pick_number": int(item["pickNumber"]) if item.get("pickNumber") else None,
            "description": item.get("label"),
        }
    return None


def run_trade_action(action: str, params: dict[str, Any]) -> dict[str, Any]:
    started = perf_counter()
    stdout = ""
    stderr = ""
    returncode = 0
    summary_title = "Trade Updated"
    summary_status = "ok"
    try:
        db_path = active_db_path()
        context = game_context(db_path)
        season = int(context["currentSeason"])
        active = context.get("activeSave") or {}
        game_id = str(active.get("game_id") or active.get("id") or "ui_trade")
        user_abbr = str(active.get("user_team") or context.get("userTeam") or "MIN").upper()
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            trade_engine.ensure_schema(conn)
            trade_engine.seed_charts(conn)
            trade_engine.assign_charts_to_gms(conn)
            if action == "trade_cpu_market":
                result = trade_engine.ai_gm_process_trade_market(
                    conn,
                    game_id=game_id,
                    season=season,
                    max_proposals_per_team=int(params.get("max_proposals_per_team") or 1),
                    include_user_team_as_target=not bool(params.get("no_user_offers")),
                    execute_cpu_cpu=not bool(params.get("no_execute_cpu_cpu")),
                    ignore_trade_window=bool(params.get("ignore_window", True)),
                    current_date=str(context["currentDate"]),
                )
                conn.commit()
                counts = result.get("counts") or {}
                stdout = (
                    f"CPU trade market generated {counts.get('generated', 0)} proposal(s), "
                    f"executed {counts.get('executed', 0)} CPU trade(s)."
                )
                summary_title = "CPU Trade Market Run"
            else:
                partner_abbr = str(params.get("partner_team") or "").upper()
                partner = _team_by_abbr(conn, partner_abbr)
                user_team = _team_by_abbr(conn, user_abbr)
                if not partner or not user_team:
                    raise ValueError("Could not resolve user or partner team.")
                user_assets = [
                    asset for asset in (_trade_asset_from_ui(item) for item in params.get("user_assets") or [])
                    if asset
                ][:5]
                partner_assets = [
                    asset for asset in (_trade_asset_from_ui(item) for item in params.get("partner_assets") or [])
                    if asset
                ][:5]
                if not user_assets or not partner_assets:
                    raise ValueError("Trade offers need at least one asset from each team.")
                proposal_id = trade_engine.create_proposal(
                    conn,
                    game_id=game_id,
                    proposing_team_id=int(user_team["team_id"]),
                    receiving_team_id=int(partner["team_id"]),
                    proposing_assets=user_assets,
                    receiving_assets=partner_assets,
                    proposer_note="Submitted from Trade Center.",
                    proposal_date=str(context["currentDate"]),
                )
                response = trade_engine.ai_gm_respond(conn, proposal_id=proposal_id, game_id=game_id)
                if response.get("action") == "accept":
                    trade_engine.execute_trade(conn, proposal_id)
                    summary_title = "Trade Accepted"
                    stdout = f"Trade accepted and executed by {partner['abbreviation']}."
                elif response.get("action") == "counter_suggestion":
                    summary_title = "Trade Countered"
                    summary_status = "warning"
                    shortfall = response.get("shortfall")
                    stdout = f"{partner['abbreviation']} wants more value. Shortfall: {shortfall}."
                else:
                    summary_title = "Trade Rejected"
                    summary_status = "warning"
                    stdout = f"{partner['abbreviation']} rejected the offer."
                evaluation = response.get("evaluation") or {}
                if evaluation.get("reason"):
                    stdout += f" {evaluation['reason']}"
                conn.commit()
    except Exception as exc:
        returncode = 1
        stderr = str(exc)
        summary_status = "error"
        summary_title = "Trade Failed"
    duration_seconds = perf_counter() - started
    try:
        state, app_shell_after = write_exports(include_players=True)
    except Exception:
        state, app_shell_after = payload_for_active_db(), app_shell_payload_for_active_db()
    return {
        "action": action,
        "command": f"{action} via Trade Center",
        "returncode": returncode,
        "duration_seconds": round(duration_seconds, 2),
        "stdout": stdout[-20000:],
        "stderr": stderr[-20000:],
        "summary": {
            "title": summary_title,
            "message": stdout or stderr or summary_title,
            "status": summary_status,
            "durationSeconds": round(duration_seconds, 2),
            "affectedPanels": ["trade", "roster", "transactions", "contracts"],
        },
        "state": state,
        "app_shell_state": app_shell_after,
    }


def request_runner_cancel(action: str | None = None) -> dict[str, Any]:
    db_path = active_db_path()
    marker = sim_control.request_cancel(
        db_path,
        reason=f"UI stop requested for {action or 'running action'}",
    )
    return {
        "status": "requested",
        "message": (
            "Pause requested. The draft will stop after the current pick."
            if str(action or "").startswith("draft_")
            else "Stop requested. The sim will pause after the current game or weekly hook finishes."
        ),
        "marker": str(marker),
    }


def run_box_score_action(params: dict[str, Any]) -> dict[str, Any]:
    schedule_game_id = params.get("game_id") or params.get("schedule_game_id")
    if not schedule_game_id:
        raise ValueError("box_score requires game_id.")
    show_plays = int(params.get("show_plays") or 16)
    started = perf_counter()
    stdout = ""
    stderr = ""
    returncode = 0
    try:
        buffer = io.StringIO()
        with sqlite3.connect(active_db_path()) as con:
            con.row_factory = sqlite3.Row
            with contextlib.redirect_stdout(buffer):
                view_box_score.print_box_score(con, int(schedule_game_id), show_plays)
        stdout = buffer.getvalue()
    except Exception as exc:
        returncode = 1
        stderr = str(exc)
    duration_seconds = perf_counter() - started
    response = {
        "action": "box_score",
        "command": f"view_box_score --game-id {int(schedule_game_id)} --show-plays {show_plays}",
        "returncode": returncode,
        "duration_seconds": round(duration_seconds, 2),
        "stdout": stdout[-20000:],
        "stderr": stderr[-20000:],
    }
    response["summary"] = action_response_summary(
        "box_score",
        params,
        returncode,
        stdout,
        stderr,
        duration_seconds,
    )
    return response


def box_score_payload_for_active_db(schedule_game_id: int, show_plays: int = 16) -> dict[str, Any]:
    started = perf_counter()
    stdout = ""
    stderr = ""
    returncode = 0
    try:
        buffer = io.StringIO()
        with sqlite3.connect(active_db_path()) as con:
            con.row_factory = sqlite3.Row
            with contextlib.redirect_stdout(buffer):
                view_box_score.print_box_score(con, int(schedule_game_id), int(show_plays))
        stdout = buffer.getvalue()
    except Exception as exc:
        returncode = 1
        stderr = str(exc)
    duration_seconds = perf_counter() - started
    return {
        "action": "box_score",
        "gameId": int(schedule_game_id),
        "params": {"game_id": int(schedule_game_id), "show_plays": int(show_plays)},
        "returncode": returncode,
        "duration_seconds": round(duration_seconds, 2),
        "stdout": stdout[-20000:],
        "stderr": stderr[-20000:],
    }


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
                self.write_json(HTTPStatus.OK, export_app_shell_ui_data.build_payload(export_app_shell_ui_data.default_export_db()))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/player-search":
            try:
                self.write_json(HTTPStatus.OK, player_search_payload_for_active_db())
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/player-profile":
            try:
                params = parse_qs(parsed.query)
                requested = params.get("id") or params.get("player") or params.get("player_id")
                player_id = int(requested[0]) if requested and requested[0] else None
                self.write_json(HTTPStatus.OK, player_profile_payload_for_active_db(player_id))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/player-card":
            try:
                params = parse_qs(parsed.query)
                requested = params.get("id") or params.get("player") or params.get("player_id")
                player_id = int(requested[0]) if requested and requested[0] else None
                self.write_json(HTTPStatus.OK, player_card_payload_for_active_db(player_id))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/league-leaders":
            try:
                params = parse_qs(parsed.query)
                requested_season = params.get("season")
                season = int(requested_season[0]) if requested_season and requested_season[0] else None
                requested_category = params.get("category")
                category = requested_category[0] if requested_category and requested_category[0] else None
                self.write_json(HTTPStatus.OK, league_leaders_payload_for_active_db(season, category))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/season":
            try:
                params = parse_qs(parsed.query)
                requested_season = params.get("season")
                season = int(requested_season[0]) if requested_season and requested_season[0] else None
                self.write_json(HTTPStatus.OK, season_payload_for_active_db(season))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/standings":
            try:
                params = parse_qs(parsed.query)
                requested_season = params.get("season")
                season = int(requested_season[0]) if requested_season and requested_season[0] else None
                self.write_json(HTTPStatus.OK, standings_payload_for_active_db(season))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/schedule":
            try:
                params = parse_qs(parsed.query)
                requested_season = params.get("season")
                requested_week = params.get("week")
                season = int(requested_season[0]) if requested_season and requested_season[0] else None
                week = int(requested_week[0]) if requested_week and requested_week[0] else None
                self.write_json(HTTPStatus.OK, schedule_payload_for_active_db(season, week))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/calendar":
            try:
                params = parse_qs(parsed.query)
                requested_season = params.get("season")
                requested_date = params.get("date")
                requested_live = params.get("live")
                season = int(requested_season[0]) if requested_season and requested_season[0] else None
                current_date = requested_date[0] if requested_date and requested_date[0] else None
                live_focus = bool(requested_live and requested_live[0] in {"1", "true", "yes"})
                self.write_json(HTTPStatus.OK, calendar_payload_for_active_db(season, current_date, live_focus))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/box-score":
            try:
                params = parse_qs(parsed.query)
                requested_game = params.get("game_id") or params.get("schedule_game_id")
                if not requested_game or not requested_game[0]:
                    raise ValueError("game_id is required.")
                requested_plays = params.get("show_plays")
                show_plays = int(requested_plays[0]) if requested_plays and requested_plays[0] else 16
                self.write_json(HTTPStatus.OK, box_score_payload_for_active_db(int(requested_game[0]), show_plays))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/inbox":
            try:
                params = parse_qs(parsed.query)
                requested_limit = params.get("limit")
                limit = int(requested_limit[0]) if requested_limit and requested_limit[0] else 40
                self.write_json(HTTPStatus.OK, inbox_payload_for_active_db(limit))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/league-news":
            try:
                params = parse_qs(parsed.query)
                requested_limit = params.get("limit")
                limit = int(requested_limit[0]) if requested_limit and requested_limit[0] else 240
                self.write_json(HTTPStatus.OK, league_news_payload_for_active_db(limit))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/transactions":
            try:
                params = parse_qs(parsed.query)
                requested_limit = params.get("limit")
                requested_baseline = params.get("include_baseline")
                limit = int(requested_limit[0]) if requested_limit and requested_limit[0] else 400
                include_baseline = bool(requested_baseline and requested_baseline[0] in {"1", "true", "yes"})
                self.write_json(HTTPStatus.OK, transactions_payload_for_active_db(limit, include_baseline))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/injuries":
            try:
                params = parse_qs(parsed.query)
                active_limit_raw = params.get("active_limit")
                recent_limit_raw = params.get("recent_limit")
                active_limit = int(active_limit_raw[0]) if active_limit_raw and active_limit_raw[0] else 160
                recent_limit = int(recent_limit_raw[0]) if recent_limit_raw and recent_limit_raw[0] else 120
                self.write_json(HTTPStatus.OK, injuries_payload_for_active_db(active_limit, recent_limit))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/draft":
            try:
                params = parse_qs(parsed.query)
                requested_year = params.get("year")
                year = int(requested_year[0]) if requested_year and requested_year[0] else None
                self.write_json(HTTPStatus.OK, draft_payload_for_active_db(year))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/scouting":
            try:
                params = parse_qs(parsed.query)
                requested_limit = params.get("limit")
                limit = int(requested_limit[0]) if requested_limit and requested_limit[0] else 80
                self.write_json(HTTPStatus.OK, scouting_payload_for_active_db(limit))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/free-agency":
            try:
                params = parse_qs(parsed.query)
                requested_year = params.get("league_year") or params.get("year")
                league_year = int(requested_year[0]) if requested_year and requested_year[0] else None
                self.write_json(HTTPStatus.OK, free_agency_payload_for_active_db(league_year))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/trade-center":
            try:
                params = parse_qs(parsed.query)
                partner = (params.get("partner") or [None])[0]
                self.write_json(HTTPStatus.OK, trade_center_payload_for_active_db(partner))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/contracts":
            try:
                params = parse_qs(parsed.query)
                requested_season = params.get("season")
                season = int(requested_season[0]) if requested_season and requested_season[0] else None
                self.write_json(HTTPStatus.OK, contracts_payload_for_active_db(season))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/depth-chart":
            try:
                params = parse_qs(parsed.query)
                requested_season = params.get("season")
                requested_contract_season = params.get("contractSeason")
                requested_team = params.get("team")
                season = int(requested_season[0]) if requested_season and requested_season[0] else None
                contract_season = (
                    int(requested_contract_season[0])
                    if requested_contract_season and requested_contract_season[0]
                    else None
                )
                team = requested_team[0] if requested_team and requested_team[0] else None
                self.write_json(HTTPStatus.OK, depth_chart_payload_for_active_db(season, team, contract_season))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/practice-squad":
            try:
                params = parse_qs(parsed.query)
                requested_season = params.get("season")
                requested_team = params.get("team")
                season = int(requested_season[0]) if requested_season and requested_season[0] else None
                team = requested_team[0] if requested_team and requested_team[0] else None
                self.write_json(HTTPStatus.OK, practice_squad_payload_for_active_db(season, team))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        if parsed.path == "/api/ai-gm":
            try:
                params = parse_qs(parsed.query)
                requested_season = params.get("season")
                requested_team = params.get("team")
                season = int(requested_season[0]) if requested_season and requested_season[0] else None
                team = requested_team[0] if requested_team and requested_team[0] else None
                self.write_json(HTTPStatus.OK, ai_gm_payload_for_active_db(season, team))
            except Exception as exc:
                self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        try:
            refresh_static_data_asset(parsed.path)
        except Exception as exc:
            self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Failed to refresh UI data: {exc}"})
            return
        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/cancel":
            try:
                length = int(self.headers.get("Content-Length") or "0")
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                request = json.loads(body)
                action = str(request.get("action") or "")
                self.write_json(HTTPStatus.OK, request_runner_cancel(action))
            except Exception as exc:
                self.write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
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
    mimetypes.add_type("image/webp", ".webp")
    try:
        write_exports()
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc).lower():
            raise
        print(f"Initial export skipped because the active save is busy: {exc}")
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
