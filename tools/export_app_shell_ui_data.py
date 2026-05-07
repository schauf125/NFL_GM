"""Export data for the static start/load shell UI."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "database" / "nfl_gm.db"
DEFAULT_OUTPUT = PROJECT_ROOT / "ui" / "app_shell" / "app-shell-data.js"
SAVE_REGISTRY = PROJECT_ROOT / "saves" / "save_registry.json"


def clean_hex(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    value = value.strip().lstrip("#")
    if len(value) in {3, 6} and all(char in "0123456789abcdefABCDEF" for char in value):
        return f"#{value}"
    return fallback


def relative_ui_path(local_path: str | None) -> str | None:
    if not local_path:
        return None
    return "../../" + local_path.replace("\\", "/").lstrip("/")


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def settings(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT setting_key, setting_value FROM game_settings").fetchall()
    return {row["setting_key"]: row["setting_value"] for row in rows}


def teams(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            t.team_id,
            t.abbreviation,
            t.city,
            t.nickname,
            t.conference,
            t.division,
            t.stadium,
            t.prestige,
            g.local_path,
            g.color,
            g.alternate_color
        FROM teams t
        LEFT JOIN team_graphics_assets g
          ON g.team_id = t.team_id
         AND g.variant = 'primary'
         AND g.asset_type = 'logo'
        ORDER BY t.abbreviation
        """
    ).fetchall()
    return [
        {
            "id": int(row["team_id"]),
            "abbr": row["abbreviation"],
            "name": f"{row['city']} {row['nickname']}",
            "city": row["city"],
            "nickname": row["nickname"],
            "conference": row["conference"],
            "division": row["division"],
            "stadium": row["stadium"] or "-",
            "prestige": int(row["prestige"] or 50),
            "logo": relative_ui_path(row["local_path"]),
            "primary": clean_hex(row["color"], "#75808f"),
            "secondary": clean_hex(row["alternate_color"], "#d6dde6"),
        }
        for row in rows
    ]


def upcoming_events(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    if not table_exists(conn, "upcoming_league_events_view"):
        return []
    rows = conn.execute(
        """
        SELECT event_start_date, event_name, event_category, phase_name, event_time_et
        FROM upcoming_league_events_view
        ORDER BY event_start_date, sort_order
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "date": row["event_start_date"],
            "name": row["event_name"],
            "category": row["event_category"],
            "phase": row["phase_name"],
            "time": row["event_time_et"],
        }
        for row in rows
    ]


def load_registry() -> dict[str, Any]:
    registry = read_json(
        SAVE_REGISTRY,
        {
            "version": 1,
            "active_game_id": None,
            "saves": {},
        },
    )
    saves = []
    for game_id, record in sorted(registry.get("saves", {}).items()):
        saves.append({
            "gameId": game_id,
            "name": record.get("name") or game_id,
            "userTeam": record.get("user_team"),
            "dbPath": record.get("db_path"),
            "manifestPath": record.get("manifest_path"),
            "currentDate": record.get("current_date"),
            "phase": record.get("current_phase_code"),
            "status": record.get("status"),
            "createdAt": record.get("created_at"),
            "lastPlayedAt": record.get("last_played_at"),
            "active": game_id == registry.get("active_game_id"),
        })
    return {
        "activeGameId": registry.get("active_game_id"),
        "saves": saves,
    }


def default_export_db() -> Path:
    registry = read_json(
        SAVE_REGISTRY,
        {"version": 1, "active_game_id": None, "saves": {}},
    )
    active_game_id = registry.get("active_game_id")
    if active_game_id:
        record = registry.get("saves", {}).get(active_game_id)
        if record and record.get("db_path"):
            path = PROJECT_ROOT / record["db_path"]
            if path.exists():
                return path
    return DEFAULT_DB


def build_payload(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        game_settings = settings(conn)
        return {
            "projectName": "NFL GM",
            "database": str(db_path),
            "settings": game_settings,
            "currentDate": game_settings.get("current_game_date", "2026-06-01"),
            "currentSeason": game_settings.get("current_season", "2026"),
            "currentPhase": game_settings.get("current_calendar_phase", "OFFSEASON_OPEN"),
            "teams": teams(conn),
            "events": upcoming_events(conn),
            "registry": load_registry(),
            "commands": {
                "newGameTemplate": "python tools\\play.py new --game-id <game_id> --name \"<save name>\" --user-team <TEAM>",
                "loadTemplate": "python tools\\play.py load <game_id>",
                "active": "python tools\\play.py active",
                "gameCenterExport": "python tools\\export_game_center_ui_data.py",
                "frontOfficeExport": "python tools\\export_front_office_ui_data.py",
            },
        }
    finally:
        conn.close()


def export(db_path: Path, output_path: Path) -> dict[str, Any]:
    payload = build_payload(db_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "/* Generated by tools/export_app_shell_ui_data.py. */\n"
        "window.APP_SHELL_DATA = "
        + json.dumps(payload, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Export data for the static app shell UI.")
    parser.add_argument("--db", help="Path to nfl_gm.db. Defaults to the active save DB when available.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JS file")
    args = parser.parse_args()
    db_path = Path(args.db) if args.db else default_export_db()
    export(db_path, Path(args.output))
    print(f"Exported app shell data from {db_path} to {Path(args.output)}")


if __name__ == "__main__":
    main()
