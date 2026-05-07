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
from urllib.parse import parse_qs, unquote, urlparse

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
    "advance_to_draft",
    "draft_start",
    "draft_pause",
    "draft_resume",
    "draft_pick",
    "draft_skip",
    "draft_skip_to_user",
    "draft_finish",
    "sim_week",
    "sim_season",
    "postseason",
    "complete_season",
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
    "draft_start",
    "draft_pause",
    "draft_resume",
    "draft_pick",
    "draft_skip",
    "draft_skip_to_user",
    "draft_finish",
    "contract_extend",
    "contract_release",
    "contract_restructure",
    "depth_chart_set",
    "depth_chart_move",
}
SKIP_PLAYER_REEXPORT_ACTIONS = {
    "contract_extend",
    "contract_release",
    "contract_restructure",
}
DRAFT_RUN_ACTIONS = {
    "advance_to_draft",
    "draft_start",
    "draft_pause",
    "draft_resume",
    "draft_pick",
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
CALENDAR_RUN_ACTIONS = {
    "advance_next_event",
    "advance_next_league_year",
    "advance_to_draft",
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


def depth_chart_state_patch(db_path: Path) -> dict[str, Any]:
    context = game_context(db_path)
    target_team = context.get("userTeam") or "MIN"
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        depth_chart = export_game_center_ui_data.depth_chart_summary(
            con,
            target_team,
            int(context["currentSeason"]),
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
        draft = export_game_center_ui_data.draft_summary(con, draft_year, user_team_id=user_team_id)
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
    for value in (
        active.get("current_league_year"),
        settings.get("current_contract_year"),
        settings.get("current_league_year"),
        settings.get("current_season"),
    ):
        if value:
            return int(value)
    return 2026


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


def write_lightweight_action_exports(action: str) -> tuple[dict[str, Any], dict[str, Any]]:
    db_path = active_db_path()
    if action in DRAFT_RUN_ACTIONS:
        patch = draft_state_patch(db_path)
        return patch, app_shell_payload_for_active_db()
    if action in {"depth_chart_set", "depth_chart_move"}:
        patch = depth_chart_state_patch(db_path)
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


def calendar_payload_for_active_db(season: int | None = None, current_date: str | None = None) -> dict[str, Any]:
    db_path = active_db_path()
    context = game_context(db_path)
    target_season = int(season or context["currentSeason"])
    target_date = current_date or str(context["currentDate"])
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        active = export_game_center_ui_data.active_save(conn) or {}
        game_id = active.get("game_id") or active.get("save_id")
        user_team_id = active.get("user_team_id")
        calendar = export_game_center_ui_data.calendar_summary(
            conn,
            season=target_season,
            current_date=target_date,
            game_id=game_id,
            user_team_id=user_team_id,
        )
        events = export_game_center_ui_data.upcoming_events(conn)
    return {
        "season": target_season,
        "currentDate": target_date,
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


def league_news_payload_for_active_db(limit: int = 80) -> dict[str, Any]:
    db_path = active_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        news = export_game_center_ui_data.league_news.build_ui_payload(conn, limit=limit)
    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "leagueNews": news,
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
        draft = export_game_center_ui_data.draft_summary(conn, target_year, user_team_id=user_team_id)
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


def scouting_payload_for_active_db(limit: int = 80) -> dict[str, Any]:
    db_path = active_db_path()
    context = game_context(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        active = export_game_center_ui_data.active_save(conn) or {}
        user_team_id = active.get("user_team_id")
        draft_year = export_game_center_ui_data.draft_year(conn, int(context["currentSeason"]))
        draft = export_game_center_ui_data.draft_summary(conn, draft_year, user_team_id=user_team_id)
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
    target_year = int(league_year or context["draftYear"])
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
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


def depth_chart_payload_for_active_db(season: int | None = None, team: str | None = None) -> dict[str, Any]:
    db_path = active_db_path()
    context = game_context(db_path)
    target_season = int(season or context["currentSeason"])
    target_team = team or context.get("userTeam") or "MIN"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        depth_chart = export_game_center_ui_data.depth_chart_summary(conn, target_team, target_season)
    return {
        "season": target_season,
        "team": target_team,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "depthChart": depth_chart,
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
            "--commit-each",
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
            "draft_skip": "Draft Pick Skipped",
            "draft_skip_to_user": "Advanced To User Pick",
            "draft_finish": "Draft Finished",
        }.get(action, "Draft Updated")
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
            "advance_next_league_year": "Advanced To Next League Year",
        }.get(action, summary["title"])
    if params.get("apply"):
        summary["mode"] = "applied"
    elif "dry" in action or "dry run" in (stdout or "").lower():
        summary["mode"] = "dry_run"
    return summary


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
    response["summary"] = action_response_summary(
        action,
        params,
        result.returncode,
        result.stdout,
        result.stderr,
        duration_seconds,
    )
    if action in LIGHTWEIGHT_PRESTATE_ACTIONS:
        state_patch, app_shell_after = write_lightweight_action_exports(action)
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
                season = int(requested_season[0]) if requested_season and requested_season[0] else None
                current_date = requested_date[0] if requested_date and requested_date[0] else None
                self.write_json(HTTPStatus.OK, calendar_payload_for_active_db(season, current_date))
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
                limit = int(requested_limit[0]) if requested_limit and requested_limit[0] else 80
                self.write_json(HTTPStatus.OK, league_news_payload_for_active_db(limit))
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
                requested_team = params.get("team")
                season = int(requested_season[0]) if requested_season and requested_season[0] else None
                team = requested_team[0] if requested_team and requested_team[0] else None
                self.write_json(HTTPStatus.OK, depth_chart_payload_for_active_db(season, team))
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
