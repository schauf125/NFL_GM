#!/usr/bin/env python3
"""League-wide news feed for public events and rumors.

Inbox messages are private to the user. League news is the public wire:
prospect movement, suspensions, holdouts, big roster moves, rumors, and other
stories the whole league would plausibly see.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone() is not None


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS league_news_items (
            news_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL DEFAULT 'default',
            news_date TEXT NOT NULL,
            category TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'normal',
            scope TEXT NOT NULL DEFAULT 'league',
            source TEXT NOT NULL DEFAULT 'League Wire',
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
            player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
            prospect_id INTEGER REFERENCES draft_prospects(prospect_id) ON DELETE SET NULL,
            related_table TEXT,
            related_id INTEGER,
            tags_json TEXT,
            is_major INTEGER NOT NULL DEFAULT 0,
            fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(game_id, fingerprint)
        );

        CREATE INDEX IF NOT EXISTS idx_league_news_items_game_date
            ON league_news_items(game_id, news_date DESC, news_id DESC);

        CREATE INDEX IF NOT EXISTS idx_league_news_items_category
            ON league_news_items(game_id, category, news_date DESC);
        """
    )


def setting(con: sqlite3.Connection, key: str, fallback: str | None = None) -> str | None:
    if not table_exists(con, "game_settings"):
        return fallback
    row = con.execute(
        "SELECT setting_value FROM game_settings WHERE setting_key = ? LIMIT 1",
        (key,),
    ).fetchone()
    return str(row["setting_value"]) if row else fallback


def active_game_row(con: sqlite3.Connection) -> sqlite3.Row | None:
    if table_exists(con, "active_game_save_view"):
        return con.execute("SELECT * FROM active_game_save_view LIMIT 1").fetchone()
    if table_exists(con, "game_saves"):
        return con.execute(
            """
            SELECT *
            FROM game_saves
            WHERE status = 'active'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()
    return None


def active_game_id(con: sqlite3.Connection, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    row = active_game_row(con)
    if row and "game_id" in row.keys() and row["game_id"]:
        return str(row["game_id"])
    return setting(con, "active_game_id", "default") or "default"


def current_date(con: sqlite3.Connection) -> str:
    row = active_game_row(con)
    if row and "current_date" in row.keys() and row["current_date"]:
        return str(row["current_date"])
    return setting(con, "current_game_date", "2026-06-01") or "2026-06-01"


def current_season(con: sqlite3.Connection) -> int:
    value = setting(con, "current_league_year") or setting(con, "current_season")
    return int(value) if value else 2026


def fingerprint_for(*parts: Any) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def add_news_item(
    con: sqlite3.Connection,
    *,
    game_id: str | None = None,
    news_date: str | None = None,
    category: str,
    title: str,
    body: str,
    priority: str = "normal",
    scope: str = "league",
    source: str = "League Wire",
    team_id: int | None = None,
    player_id: int | None = None,
    prospect_id: int | None = None,
    related_table: str | None = None,
    related_id: int | None = None,
    tags: list[str] | None = None,
    is_major: bool = False,
    fingerprint: str | None = None,
) -> int | None:
    ensure_schema(con)
    target_game_id = active_game_id(con, game_id)
    target_date = news_date or current_date(con)
    dedupe = fingerprint or fingerprint_for(
        target_game_id,
        target_date,
        category,
        title,
        related_table,
        related_id,
        player_id,
        prospect_id,
    )
    con.execute(
        """
        INSERT OR IGNORE INTO league_news_items (
            game_id, news_date, category, priority, scope, source, title, body,
            team_id, player_id, prospect_id, related_table, related_id,
            tags_json, is_major, fingerprint
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            target_game_id,
            target_date,
            category,
            priority,
            scope,
            source,
            title,
            body,
            team_id,
            player_id,
            prospect_id,
            related_table,
            related_id,
            json.dumps(tags or [], separators=(",", ":")),
            1 if is_major else 0,
            dedupe,
        ),
    )
    row = con.execute(
        "SELECT news_id FROM league_news_items WHERE game_id = ? AND fingerprint = ?",
        (target_game_id, dedupe),
    ).fetchone()
    return int(row["news_id"]) if row else None


def row_to_news(row: sqlite3.Row, *, synthetic: bool = False) -> dict[str, Any]:
    data = dict(row)
    tags_raw = data.pop("tags_json", None)
    try:
        tags = json.loads(tags_raw) if tags_raw else []
    except json.JSONDecodeError:
        tags = []
    data["tags"] = tags
    data["synthetic"] = synthetic
    return data


def saved_news(con: sqlite3.Connection, *, game_id: str, limit: int) -> list[dict[str, Any]]:
    ensure_schema(con)
    rows = con.execute(
        """
        SELECT
            ln.*,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name,
            p.first_name || ' ' || p.last_name AS player_name,
            dp.first_name || ' ' || dp.last_name AS prospect_name,
            dp.position AS prospect_position,
            dp.college AS prospect_college
        FROM league_news_items ln
        LEFT JOIN teams t ON t.team_id = ln.team_id
        LEFT JOIN players p ON p.player_id = ln.player_id
        LEFT JOIN draft_prospects dp ON dp.prospect_id = ln.prospect_id
        WHERE ln.game_id IN (?, 'default')
        ORDER BY ln.news_date DESC, ln.news_id DESC
        LIMIT ?
        """,
        (game_id, limit),
    ).fetchall()
    return [row_to_news(row) for row in rows]


def draft_class_story(con: sqlite3.Connection, *, game_id: str) -> dict[str, Any] | None:
    if not table_exists(con, "draft_classes") or not table_exists(con, "draft_prospects"):
        return None
    season = current_season(con)
    class_row = con.execute(
        """
        SELECT *
        FROM draft_classes
        WHERE draft_year >= ?
        ORDER BY draft_year, draft_class_id
        LIMIT 1
        """,
        (season + 1,),
    ).fetchone()
    if not class_row:
        return None
    counts = con.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN COALESCE(public_board_status, 'public_board') = 'off_public_board' THEN 1 ELSE 0 END) AS off_board,
            SUM(CASE WHEN COALESCE(public_board_status, 'public_board') <> 'off_public_board' THEN 1 ELSE 0 END) AS public_board
        FROM draft_prospects
        WHERE draft_class_id = ?
        """,
        (int(class_row["draft_class_id"]),),
    ).fetchone()
    public_board = int(counts["public_board"] or 0)
    off_board = int(counts["off_board"] or 0)
    year = int(class_row["draft_year"])
    return {
        "news_id": f"synthetic-draft-class-{year}",
        "game_id": game_id,
        "news_date": current_date(con),
        "category": "Prospects",
        "priority": "normal",
        "scope": "league",
        "source": "Draft Wire",
        "title": f"{year} draft cycle begins to take shape",
        "body": (
            f"Public boards are tracking {public_board} prospects for the {year} class. "
            f"Scouting departments also believe roughly {off_board} off-board names could surface "
            "through the fall, all-star circuit, and pro-day process."
        ),
        "team_id": None,
        "player_id": None,
        "prospect_id": None,
        "related_table": "draft_classes",
        "related_id": int(class_row["draft_class_id"]),
        "tags": ["draft", "prospects", "watchlist"],
        "is_major": 0,
        "synthetic": True,
    }


def game_flow_stories(con: sqlite3.Connection, *, game_id: str, limit: int = 8) -> list[dict[str, Any]]:
    if not table_exists(con, "game_flow_log"):
        return []
    rows = con.execute(
        """
        SELECT log_id, game_id, game_date, log_type, event_code, title, details, created_at
        FROM game_flow_log
        WHERE game_id IS NULL OR game_id = ?
        ORDER BY game_date DESC, log_id DESC
        LIMIT ?
        """,
        (game_id, limit),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        log_type = str(row["log_type"] or "")
        if log_type == "GAME_START":
            title = "League file opens for business"
            body = "Front offices around the league begin a new cycle with roster building, scouting, and cap planning now active."
            category = "League Office"
            tags = ["league", "calendar"]
        elif log_type == "CALENDAR_EVENT":
            title = str(row["title"] or "League calendar update")
            body = str(row["details"] or "A league calendar milestone has arrived.")
            category = "Calendar"
            tags = ["calendar"]
        elif "FREE_AGENCY" in log_type or "CONTRACT" in log_type:
            title = str(row["title"] or "Roster market update")
            body = str(row["details"] or "A roster market event has been logged.")
            category = "Transactions"
            tags = ["transactions"]
        else:
            continue
        items.append(
            {
                "news_id": f"synthetic-log-{row['log_id']}",
                "game_id": game_id,
                "news_date": row["game_date"],
                "category": category,
                "priority": "normal",
                "scope": "league",
                "source": "League Office",
                "title": title,
                "body": body,
                "team_id": None,
                "player_id": None,
                "prospect_id": None,
                "related_table": "game_flow_log",
                "related_id": int(row["log_id"]),
                "tags": tags,
                "is_major": 1 if log_type == "GAME_START" else 0,
                "synthetic": True,
            }
        )
    return items


def draft_room_stories(con: sqlite3.Connection, *, game_id: str, limit: int = 12) -> list[dict[str, Any]]:
    if not table_exists(con, "draft_room_events"):
        return []
    rows = con.execute(
        """
        SELECT
            dre.event_id,
            dre.draft_year,
            dre.pick_number,
            dre.round,
            dre.event_type,
            dre.message,
            dre.created_at,
            dre.player_id,
            dre.prospect_id,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name,
            p.first_name || ' ' || p.last_name AS player_name,
            dp.first_name || ' ' || dp.last_name AS prospect_name,
            dp.position AS prospect_position,
            dp.college AS prospect_college
        FROM draft_room_events dre
        LEFT JOIN teams t ON t.team_id = dre.team_id
        LEFT JOIN players p ON p.player_id = dre.player_id
        LEFT JOIN draft_prospects dp ON dp.prospect_id = dre.prospect_id
        ORDER BY dre.created_at DESC, dre.event_id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    stories = []
    for row in rows:
        stories.append(
            {
                "news_id": f"synthetic-draft-event-{row['event_id']}",
                "game_id": game_id,
                "news_date": str(row["created_at"] or current_date(con))[:10],
                "category": "Draft",
                "priority": "normal",
                "scope": "league",
                "source": "Draft Room",
                "title": f"{row['team'] or 'Team'} makes pick #{row['pick_number'] or '-'}",
                "body": row["message"],
                "team": row["team"],
                "team_name": row["team_name"],
                "player_id": row["player_id"],
                "prospect_id": row["prospect_id"],
                "player_name": row["player_name"],
                "prospect_name": row["prospect_name"],
                "prospect_position": row["prospect_position"],
                "prospect_college": row["prospect_college"],
                "related_table": "draft_room_events",
                "related_id": int(row["event_id"]),
                "tags": ["draft"],
                "is_major": 1 if int(row["round"] or 0) == 1 else 0,
                "synthetic": True,
            }
        )
    return stories


def scouting_stories(con: sqlite3.Connection, *, game_id: str) -> list[dict[str, Any]]:
    stories: list[dict[str, Any]] = []
    if table_exists(con, "scouting_prospect_progress") and table_exists(con, "draft_classes"):
        row = con.execute(
            """
            SELECT spp.draft_year, COUNT(*) AS discovered
            FROM scouting_prospect_progress spp
            WHERE spp.game_id = ?
              AND spp.visibility_status = 'discovered'
            GROUP BY spp.draft_year
            ORDER BY spp.draft_year DESC
            LIMIT 1
            """,
            (game_id,),
        ).fetchone()
        if row and int(row["discovered"] or 0) > 0:
            stories.append(
                {
                    "news_id": f"synthetic-discovered-{row['draft_year']}",
                    "game_id": game_id,
                    "news_date": current_date(con),
                    "category": "Prospects",
                    "priority": "normal",
                    "scope": "league",
                    "source": "Draft Wire",
                    "title": "Off-board prospects begin to surface",
                    "body": (
                        f"Regional scouts have pushed {int(row['discovered'])} previously off-board "
                        f"prospect(s) into wider discussion for the {int(row['draft_year'])} draft cycle."
                    ),
                    "related_table": "scouting_prospect_progress",
                    "related_id": None,
                    "tags": ["draft", "rumor", "prospects"],
                    "is_major": 0,
                    "synthetic": True,
                }
            )
    if table_exists(con, "scouting_senior_bowl_runs"):
        row = con.execute(
            """
            SELECT *
            FROM scouting_senior_bowl_runs
            WHERE game_id = ?
            ORDER BY draft_year DESC
            LIMIT 1
            """,
            (game_id,),
        ).fetchone()
        if row:
            stories.append(
                {
                    "news_id": f"synthetic-senior-bowl-{row['draft_year']}",
                    "game_id": game_id,
                    "news_date": row["event_date"],
                    "category": "Prospects",
                    "priority": "normal",
                    "scope": "league",
                    "source": "Senior Bowl Wire",
                    "title": "Senior Bowl week shifts the board",
                    "body": (
                        f"{int(row['accepted_count'] or 0)} prospects accepted Senior Bowl invites. "
                        f"Teams generated {int(row['team_report_count'] or 0)} public-facing practice notes "
                        "from the week."
                    ),
                    "related_table": "scouting_senior_bowl_runs",
                    "related_id": None,
                    "tags": ["draft", "senior-bowl"],
                    "is_major": 1,
                    "synthetic": True,
                }
            )
    return stories


def build_ui_payload(con: sqlite3.Connection, *, limit: int = 60) -> dict[str, Any]:
    ensure_schema(con)
    game_id = active_game_id(con)
    items = saved_news(con, game_id=game_id, limit=limit)
    synthetic: list[dict[str, Any]] = []
    story = draft_class_story(con, game_id=game_id)
    if story:
        synthetic.append(story)
    synthetic.extend(scouting_stories(con, game_id=game_id))
    synthetic.extend(game_flow_stories(con, game_id=game_id))
    synthetic.extend(draft_room_stories(con, game_id=game_id))

    seen = {str(item.get("fingerprint") or item.get("news_id")) for item in items}
    for item in synthetic:
        key = str(item.get("news_id"))
        if key not in seen:
            items.append(item)
            seen.add(key)
    items.sort(key=lambda item: (str(item.get("news_date") or ""), int(item.get("is_major") or 0)), reverse=True)
    items = items[:limit]
    categories = sorted({str(item.get("category") or "League") for item in items})
    return {
        "gameId": game_id,
        "items": items,
        "categories": categories,
        "counts": {
            "total": len(items),
            "major": sum(1 for item in items if int(item.get("is_major") or 0)),
            "rumors": sum(1 for item in items if "rumor" in [str(tag).lower() for tag in item.get("tags", [])]),
            "prospects": sum(1 for item in items if str(item.get("category") or "").lower() == "prospects"),
        },
        "updatedAt": current_date(con),
    }


def seed_baseline(con: sqlite3.Connection, *, game_id: str | None = None) -> dict[str, int]:
    ensure_schema(con)
    target_game_id = active_game_id(con, game_id)
    before = con.total_changes
    story = draft_class_story(con, game_id=target_game_id)
    if story:
        add_news_item(
            con,
            game_id=target_game_id,
            news_date=story["news_date"],
            category=story["category"],
            title=story["title"],
            body=story["body"],
            source=story["source"],
            tags=story["tags"],
            related_table=story["related_table"],
            related_id=story["related_id"],
            fingerprint=str(story["news_id"]),
        )
    for item in game_flow_stories(con, game_id=target_game_id, limit=5):
        add_news_item(
            con,
            game_id=target_game_id,
            news_date=item["news_date"],
            category=item["category"],
            title=item["title"],
            body=item["body"],
            source=item["source"],
            tags=item["tags"],
            related_table=item["related_table"],
            related_id=item["related_id"],
            is_major=bool(item["is_major"]),
            fingerprint=str(item["news_id"]),
        )
    con.commit()
    return {"inserted_or_checked": con.total_changes - before, "total": len(saved_news(con, game_id=target_game_id, limit=500))}


def print_items(items: list[dict[str, Any]]) -> None:
    if not items:
        print("No league news items.")
        return
    for item in items:
        major = " [MAJOR]" if int(item.get("is_major") or 0) else ""
        print(f"{item.get('news_date')} | {item.get('category')}{major} | {item.get('title')}")
        print(f"  {item.get('body')}")


def action_list(args: argparse.Namespace) -> None:
    with connect(Path(args.db)) as con:
        payload = build_ui_payload(con, limit=args.limit)
    print_items(payload["items"])


def action_seed(args: argparse.Namespace) -> None:
    with connect(Path(args.db)) as con:
        result = seed_baseline(con, game_id=args.game_id)
    print(f"League news ready: {result['total']} stored item(s), {result['inserted_or_checked']} change(s).")


def action_add(args: argparse.Namespace) -> None:
    tags = [tag.strip() for tag in (args.tags or "").split(",") if tag.strip()]
    with connect(Path(args.db)) as con:
        news_id = add_news_item(
            con,
            game_id=args.game_id,
            news_date=args.date,
            category=args.category,
            priority=args.priority,
            source=args.source,
            title=args.title,
            body=args.body,
            tags=tags,
            is_major=args.major,
        )
        con.commit()
    print(f"League news item saved: {news_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage league-wide public news items.")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to nfl_gm.db or active save database.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="Show league news.")
    list_parser.add_argument("--limit", type=int, default=25)
    list_parser.set_defaults(func=action_list)

    seed_parser = subparsers.add_parser("seed", help="Seed baseline public stories from current save state.")
    seed_parser.add_argument("--game-id")
    seed_parser.set_defaults(func=action_seed)

    add_parser = subparsers.add_parser("add", help="Add one public league news item.")
    add_parser.add_argument("--game-id")
    add_parser.add_argument("--date")
    add_parser.add_argument("--category", required=True)
    add_parser.add_argument("--priority", default="normal")
    add_parser.add_argument("--source", default="League Wire")
    add_parser.add_argument("--title", required=True)
    add_parser.add_argument("--body", required=True)
    add_parser.add_argument("--tags", default="")
    add_parser.add_argument("--major", action="store_true")
    add_parser.set_defaults(func=action_add)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
