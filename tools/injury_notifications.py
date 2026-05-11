#!/usr/bin/env python3
"""Create user-facing notifications for newly persisted injury events."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import injury_model

import league_news
import scouting


USER_ALERT_MIN_GAMES = 2
MAJOR_NEWS_MIN_GAMES = 4
MAJOR_SEVERITIES = {"major", "severe"}


def table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def active_game_id(con: sqlite3.Connection) -> str:
    if table_exists(con, "active_game_save_view"):
        row = con.execute("SELECT game_id FROM active_game_save_view LIMIT 1").fetchone()
        if row and row["game_id"]:
            return str(row["game_id"])
    if table_exists(con, "game_saves"):
        row = con.execute(
            """
            SELECT game_id
            FROM game_saves
            WHERE status = 'active'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row and row["game_id"]:
            return str(row["game_id"])
    return "default"


def active_user_team_id(con: sqlite3.Connection) -> int | None:
    if table_exists(con, "active_game_save_view"):
        row = con.execute("SELECT user_team_id FROM active_game_save_view LIMIT 1").fetchone()
        if row and row["user_team_id"] is not None:
            return int(row["user_team_id"])
    if table_exists(con, "game_saves"):
        row = con.execute(
            """
            SELECT user_team_id
            FROM game_saves
            WHERE status = 'active'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()
        if row and row["user_team_id"] is not None:
            return int(row["user_team_id"])
    return None


def player_name_expr(alias: str = "p") -> str:
    return f"TRIM(COALESCE({alias}.first_name, '') || ' ' || COALESCE({alias}.last_name, ''))"


def injury_rows(
    con: sqlite3.Connection,
    *,
    min_event_id: int | None = None,
    season: int | None = None,
    week: int | None = None,
    schedule_game_ids: list[int] | None = None,
    min_expected_games: int = USER_ALERT_MIN_GAMES,
) -> list[sqlite3.Row]:
    injury_model.ensure_schema(con)
    filters = ["gie.expected_games >= ?"]
    params: list[Any] = [int(min_expected_games)]
    if min_event_id is not None:
        filters.append("gie.event_id > ?")
        params.append(int(min_event_id))
    if season is not None:
        filters.append("gie.season = ?")
        params.append(int(season))
    if week is not None:
        filters.append("gie.week = ?")
        params.append(int(week))
    if schedule_game_ids:
        placeholders = ",".join("?" for _ in schedule_game_ids)
        filters.append(f"gie.schedule_game_id IN ({placeholders})")
        params.extend(int(game_id) for game_id in schedule_game_ids)
    where_sql = " AND ".join(filters)
    return con.execute(
        f"""
        SELECT
            gie.event_id,
            gie.schedule_game_id,
            gie.season,
            gie.week,
            gie.game_date,
            gie.player_id,
            {player_name_expr()} AS player_name,
            p.position,
            gie.team_id,
            t.abbreviation AS team,
            t.city || ' ' || t.nickname AS team_name,
            gie.injury_label,
            gie.severity,
            gie.expected_days,
            gie.expected_games,
            gie.status,
            gie.source,
            gie.description
        FROM game_injury_events gie
        JOIN players p ON p.player_id = gie.player_id
        JOIN teams t ON t.team_id = gie.team_id
        WHERE {where_sql}
        ORDER BY gie.event_id
        """,
        params,
    ).fetchall()


def max_event_id(con: sqlite3.Connection) -> int:
    injury_model.ensure_schema(con)
    row = con.execute("SELECT COALESCE(MAX(event_id), 0) AS max_id FROM game_injury_events").fetchone()
    return int(row["max_id"] or 0) if row else 0


def format_duration(row: sqlite3.Row) -> str:
    games = int(row["expected_games"] or 0)
    days = int(row["expected_days"] or 0)
    if games > 0:
        return f"{games} game{'s' if games != 1 else ''}"
    if days > 0:
        return f"{days} day{'s' if days != 1 else ''}"
    return "at least one week"


def notification_body(row: sqlite3.Row) -> str:
    player = str(row["player_name"] or "A player").strip()
    injury = str(row["injury_label"] or "an injury").lower()
    duration = format_duration(row)
    status = str(row["status"] or "Unavailable")
    return (
        f"{player} suffered {injury} and is expected to miss about {duration}. "
        f"Current status: {status}."
    )


def inbox_already_exists(
    con: sqlite3.Connection,
    *,
    game_id: str,
    player_id: int,
    message_date: str,
    body: str,
) -> bool:
    if not table_exists(con, "user_inbox_messages"):
        return False
    row = con.execute(
        """
        SELECT 1
        FROM user_inbox_messages
        WHERE game_id = ?
          AND category = 'Medical'
          AND source = 'Medical Staff'
          AND related_table = 'players'
          AND related_id = ?
          AND message_date = ?
          AND body = ?
        LIMIT 1
        """,
        (game_id, int(player_id), message_date, body),
    ).fetchone()
    return row is not None


def add_user_inbox_message(
    con: sqlite3.Connection,
    *,
    game_id: str,
    row: sqlite3.Row,
) -> bool:
    player = str(row["player_name"] or "Player").strip()
    body = f"{player}: {notification_body(row)}"
    if inbox_already_exists(
        con,
        game_id=game_id,
        player_id=int(row["player_id"]),
        message_date=str(row["game_date"]),
        body=body,
    ):
        return False
    priority = "high" if int(row["expected_games"] or 0) >= MAJOR_NEWS_MIN_GAMES else "normal"
    scouting.add_inbox_message(
        con,
        game_id=game_id,
        title=f"Injury Update: {player}",
        body=body,
        category="Medical",
        priority=priority,
        source="Medical Staff",
        message_date=str(row["game_date"]),
        related_table="players",
        related_id=int(row["player_id"]),
    )
    return True


def add_major_news_item(
    con: sqlite3.Connection,
    *,
    game_id: str,
    row: sqlite3.Row,
) -> bool:
    expected_games = int(row["expected_games"] or 0)
    severity = str(row["severity"] or "").lower()
    if expected_games < MAJOR_NEWS_MIN_GAMES and severity not in MAJOR_SEVERITIES:
        return False
    player = str(row["player_name"] or "Player").strip()
    team = str(row["team"] or "").strip()
    duration = format_duration(row)
    news_id = league_news.add_news_item(
        con,
        game_id=game_id,
        news_date=str(row["game_date"]),
        category="Injuries",
        title=f"{player} expected to miss {duration}",
        body=f"{team} {row['position']} {notification_body(row)}",
        priority="high",
        scope="league",
        source="League Wire",
        team_id=int(row["team_id"]),
        player_id=int(row["player_id"]),
        related_table="players",
        related_id=int(row["player_id"]),
        tags=["injury", severity, str(row["position"] or "").lower()],
        is_major=True,
        fingerprint=f"injury:{int(row['event_id'])}:league",
    )
    return news_id is not None


def create_injury_notifications(
    con: sqlite3.Connection,
    *,
    min_event_id: int | None = None,
    season: int | None = None,
    week: int | None = None,
    schedule_game_ids: list[int] | None = None,
    game_id: str | None = None,
    user_team_id: int | None = None,
) -> dict[str, int]:
    target_game_id = game_id or active_game_id(con)
    target_user_team_id = user_team_id if user_team_id is not None else active_user_team_id(con)
    rows = injury_rows(
        con,
        min_event_id=min_event_id,
        season=season,
        week=week,
        schedule_game_ids=schedule_game_ids,
        min_expected_games=USER_ALERT_MIN_GAMES,
    )
    inbox_created = 0
    news_created = 0
    for row in rows:
        if target_user_team_id is not None and int(row["team_id"]) == int(target_user_team_id):
            if add_user_inbox_message(con, game_id=target_game_id, row=row):
                inbox_created += 1
        if add_major_news_item(con, game_id=target_game_id, row=row):
            news_created += 1
    return {"injury_events": len(rows), "inbox_created": inbox_created, "league_news_created": news_created}


def alert_payloads_since(
    con: sqlite3.Connection,
    *,
    min_event_id: int,
    user_team_id: int | None = None,
) -> list[dict[str, Any]]:
    target_user_team_id = user_team_id if user_team_id is not None else active_user_team_id(con)
    if target_user_team_id is None:
        return []
    rows = injury_rows(con, min_event_id=min_event_id, min_expected_games=USER_ALERT_MIN_GAMES)
    alerts: list[dict[str, Any]] = []
    for row in rows:
        if int(row["team_id"]) != int(target_user_team_id):
            continue
        alerts.append(
            {
                "eventId": int(row["event_id"]),
                "playerId": int(row["player_id"]),
                "playerName": str(row["player_name"] or "Player").strip(),
                "position": row["position"],
                "team": row["team"],
                "injury": row["injury_label"],
                "severity": row["severity"],
                "expectedGames": int(row["expected_games"] or 0),
                "expectedDays": int(row["expected_days"] or 0),
                "status": row["status"],
                "source": row["source"],
                "gameDate": row["game_date"],
                "season": row["season"],
                "week": row["week"],
                "message": notification_body(row),
            }
        )
    return alerts
