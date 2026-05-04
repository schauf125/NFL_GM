"""Export compact data for the static front-office UI prototype."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "database" / "nfl_gm.db"
DEFAULT_OUTPUT = PROJECT_ROOT / "ui" / "front_office" / "front-office-data.js"
SAVE_REGISTRY = PROJECT_ROOT / "saves" / "save_registry.json"
CURRENT_SEASON = 2026

POSITION_ORDER = {
    "QB": 1,
    "RB": 2,
    "FB": 3,
    "WR": 4,
    "TE": 5,
    "OT": 6,
    "OG": 7,
    "C": 8,
    "EDGE": 9,
    "IDL": 10,
    "LB": 11,
    "CB": 12,
    "S": 13,
    "K": 14,
    "P": 15,
    "LS": 16,
}

ROLE_REPLACEMENTS = {
    "qb": "QB",
    "wr": "WR",
    "rb": "RB",
    "ot": "OT",
    "te": "TE",
    "idl": "IDL",
    "cb": "CB",
}


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


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


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


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


def grade_label(value: float | None) -> str:
    if value is None:
        return "Unknown"
    if value >= 90:
        return "Elite"
    if value >= 82:
        return "Excellent"
    if value >= 74:
        return "Strong"
    if value >= 66:
        return "Solid"
    if value >= 58:
        return "Developing"
    if value >= 50:
        return "Raw"
    return "Concern"


def role_label(role_key: str | None) -> str:
    if not role_key:
        return "Depth Role"
    words = []
    for part in role_key.split("_"):
        words.append(ROLE_REPLACEMENTS.get(part, part.title()))
    return " ".join(words)


def years_label(years_exp: int | None, is_rookie: int | None) -> str:
    if is_rookie or years_exp == 0:
        return "R"
    if years_exp is None:
        return "-"
    return str(years_exp)


def latest_cap_by_team(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.*
        FROM team_cap_ledger_snapshots s
        JOIN (
            SELECT team_id, MAX(cap_snapshot_id) AS cap_snapshot_id
            FROM team_cap_ledger_snapshots
            GROUP BY team_id
        ) latest ON latest.cap_snapshot_id = s.cap_snapshot_id
        """
    ).fetchall()
    return {
        int(row["team_id"]): {
            "salaryCap": int(row["salary_cap"] or 0),
            "capSpace": int(row["cap_space"] or 0),
            "totalCommitted": int(row["total_committed"] or 0),
            "top51PlayerCap": int(row["top51_player_cap"] or 0),
            "otherCharges": int(row["other_cap_charges"] or 0),
            "activeContracts": int(row["active_contracts"] or 0),
            "contractsCounted": int(row["contracts_counted"] or 0),
            "contractsExcluded": int(row["contracts_excluded"] or 0),
            "mode": row["cap_accounting_mode"],
            "label": row["snapshot_label"],
        }
        for row in rows
    }


def team_logos(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT team_id, local_path, color, alternate_color
        FROM team_graphics_assets
        WHERE variant = 'primary' AND asset_type = 'logo'
        """
    ).fetchall()
    return {
        int(row["team_id"]): {
            "logo": relative_ui_path(row["local_path"]),
            "primary": clean_hex(row["color"], "#75808f"),
            "secondary": clean_hex(row["alternate_color"], "#d6dde6"),
        }
        for row in rows
    }


def player_headshots(conn: sqlite3.Connection) -> dict[int, str]:
    if not table_exists(conn, "player_graphics_assets"):
        return {}
    rows = conn.execute(
        """
        SELECT player_id, local_path
        FROM player_graphics_assets
        WHERE asset_key = 'headshot_espn_full'
        """
    ).fetchall()
    return {int(row["player_id"]): relative_ui_path(row["local_path"]) for row in rows}


def role_scores(conn: sqlite3.Connection, season: int) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT player_id, role_key, role_score
        FROM player_role_scores
        WHERE season = ? AND scheme_key = 'default'
        ORDER BY player_id, role_score DESC
        """,
        (season,),
    ).fetchall()
    roles: dict[int, dict[str, Any]] = {}
    for row in rows:
        player_id = int(row["player_id"])
        if player_id in roles:
            continue
        value = float(row["role_score"])
        roles[player_id] = {
            "label": role_label(row["role_key"]),
            "value": round(max(0, min(100, value)), 1),
            "grade": grade_label(value),
        }
    return roles


def contract_rows(conn: sqlite3.Connection, season: int) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            cy.player_id,
            cy.cap_hit,
            cy.cash_due,
            cy.dead_cap_if_cut_pre_june1,
            c.aav,
            c.end_year,
            c.contract_type
        FROM contract_years cy
        LEFT JOIN contracts c ON c.contract_id = cy.contract_id
        WHERE cy.season = ? AND cy.is_active = 1
        """,
        (season,),
    ).fetchall()
    return {
        int(row["player_id"]): {
            "capHit": int(row["cap_hit"] or 0),
            "cashDue": int(row["cash_due"] or 0),
            "deadPreJune1": int(row["dead_cap_if_cut_pre_june1"] or 0),
            "aav": int(row["aav"] or 0),
            "through": row["end_year"],
            "type": row["contract_type"] or "Standard",
        }
        for row in rows
    }


def players_by_team(conn: sqlite3.Connection, season: int) -> dict[int, list[dict[str, Any]]]:
    headshots = player_headshots(conn)
    roles = role_scores(conn, season)
    contracts = contract_rows(conn, season)
    rows = conn.execute(
        """
        SELECT
            p.player_id,
            p.first_name,
            p.last_name,
            p.position,
            p.team_id,
            p.age,
            p.years_exp,
            p.is_rookie,
            p.college,
            p.status,
            p.jersey_number
        FROM players p
        WHERE p.team_id IS NOT NULL
          AND COALESCE(p.status, 'Active') != 'Retired'
        ORDER BY p.team_id, p.last_name, p.first_name
        """
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        player_id = int(row["player_id"])
        role = roles.get(player_id, {"label": "Depth Role", "value": 50, "grade": "Raw"})
        player = {
            "id": player_id,
            "name": f"{row['first_name']} {row['last_name']}".strip(),
            "position": row["position"],
            "sortOrder": POSITION_ORDER.get(row["position"], 99),
            "age": row["age"],
            "exp": years_label(row["years_exp"], row["is_rookie"]),
            "college": row["college"] or "-",
            "status": row["status"] or "Active",
            "jersey": f"#{row['jersey_number']}" if row["jersey_number"] is not None else "-",
            "headshot": headshots.get(player_id),
            "role": role,
            "contract": contracts.get(player_id, {
                "capHit": 0,
                "cashDue": 0,
                "deadPreJune1": 0,
                "aav": 0,
                "through": None,
                "type": "Unsigned",
            }),
        }
        grouped.setdefault(int(row["team_id"]), []).append(player)

    for roster in grouped.values():
        roster.sort(key=lambda item: (item["sortOrder"], -float(item["role"]["value"]), item["name"]))
    return grouped


def depth_by_team(conn: sqlite3.Connection) -> dict[int, dict[str, list[int]]]:
    rows = conn.execute(
        """
        SELECT team_id, player_id, position, depth_rank
        FROM depth_charts
        ORDER BY team_id, position, depth_rank
        """
    ).fetchall()
    grouped: dict[int, dict[str, list[int]]] = {}
    for row in rows:
        team_depth = grouped.setdefault(int(row["team_id"]), {})
        team_depth.setdefault(row["position"], []).append(int(row["player_id"]))
    return grouped


def coaches_by_team(conn: sqlite3.Connection) -> dict[int, list[dict[str, Any]]]:
    coach_rows = conn.execute(
        """
        SELECT coach_id, team_id, name, role, specialty, overall
        FROM coaches
        ORDER BY team_id,
            CASE role
                WHEN 'Head Coach' THEN 1
                WHEN 'Offensive Coordinator' THEN 2
                WHEN 'Defensive Coordinator' THEN 3
                ELSE 9
            END,
            name
        """
    ).fetchall()
    rating_rows = conn.execute(
        """
        SELECT coach_id, position_group, rating
        FROM coach_position_ratings
        ORDER BY coach_id, position_group
        """
    ).fetchall()
    ratings: dict[int, list[dict[str, Any]]] = {}
    for row in rating_rows:
        value = int(row["rating"])
        ratings.setdefault(int(row["coach_id"]), []).append({
            "group": row["position_group"],
            "value": value,
            "grade": "High" if value >= 15 else "Good" if value >= 12 else "Average" if value >= 9 else "Low",
        })

    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in coach_rows:
        grouped.setdefault(int(row["team_id"]), []).append({
            "id": int(row["coach_id"]),
            "name": row["name"],
            "role": row["role"],
            "specialty": row["specialty"] or "",
            "overall": int(row["overall"] or 0),
            "ratings": ratings.get(int(row["coach_id"]), []),
        })
    return grouped


def records_by_team(conn: sqlite3.Connection, season: int) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT team_id, wins, losses, ties, points_for, points_against
        FROM season_team_records
        WHERE season = ?
        """,
        (season,),
    ).fetchall()
    return {
        int(row["team_id"]): {
            "wins": int(row["wins"] or 0),
            "losses": int(row["losses"] or 0),
            "ties": int(row["ties"] or 0),
            "pointsFor": int(row["points_for"] or 0),
            "pointsAgainst": int(row["points_against"] or 0),
        }
        for row in rows
    }


def schedule_by_team(conn: sqlite3.Connection, season: int) -> dict[int, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT
            g.game_id,
            g.week,
            g.game_type,
            g.away_team_id,
            away.abbreviation AS away_abbr,
            away.city || ' ' || away.nickname AS away_name,
            g.home_team_id,
            home.abbreviation AS home_abbr,
            home.city || ' ' || home.nickname AS home_name,
            g.game_date,
            g.game_time_et,
            g.played,
            g.away_score,
            g.home_score
        FROM season_games g
        JOIN teams away ON away.team_id = g.away_team_id
        JOIN teams home ON home.team_id = g.home_team_id
        WHERE g.season = ?
        ORDER BY g.week, g.week_game_number
        """,
        (season,),
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        for team_id, side in ((int(row["away_team_id"]), "away"), (int(row["home_team_id"]), "home")):
            opponent_id = int(row["home_team_id"]) if side == "away" else int(row["away_team_id"])
            opponent_abbr = row["home_abbr"] if side == "away" else row["away_abbr"]
            opponent_name = row["home_name"] if side == "away" else row["away_name"]
            grouped.setdefault(team_id, []).append({
                "gameId": int(row["game_id"]),
                "week": int(row["week"]),
                "type": row["game_type"],
                "side": side,
                "opponentId": opponent_id,
                "opponent": opponent_abbr,
                "opponentName": opponent_name,
                "date": row["game_date"],
                "time": row["game_time_et"],
                "played": bool(row["played"]),
                "awayScore": row["away_score"],
                "homeScore": row["home_score"],
            })
    return grouped


def upcoming_events(conn: sqlite3.Connection, limit: int = 8) -> list[dict[str, Any]]:
    if not table_exists(conn, "upcoming_league_events_view"):
        return []
    rows = conn.execute(
        """
        SELECT event_start_date, event_category, event_name, phase_name
        FROM upcoming_league_events_view
        ORDER BY event_start_date, sort_order
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "date": row["event_start_date"],
            "type": row["event_category"],
            "title": row["event_name"],
            "phase": row["phase_name"],
        }
        for row in rows
    ]


def cap_lines_by_team(conn: sqlite3.Connection) -> dict[int, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT
            team_id,
            line_type,
            player_id,
            player_name,
            player_position,
            amount,
            cap_counted_amount,
            top51_rank,
            counts_in_top51,
            description
        FROM current_team_cap_ledger_lines_view
        ORDER BY team_id,
            CASE WHEN top51_rank IS NULL THEN 999 ELSE top51_rank END,
            ABS(cap_counted_amount) DESC,
            line_type
        """
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        items = grouped.setdefault(int(row["team_id"]), [])
        if len(items) >= 90:
            continue
        items.append({
            "type": row["line_type"],
            "playerId": row["player_id"],
            "player": row["player_name"],
            "position": row["player_position"],
            "amount": int(row["amount"] or 0),
            "counted": int(row["cap_counted_amount"] or 0),
            "rank": row["top51_rank"],
            "counts": bool(row["counts_in_top51"]),
            "description": row["description"] or "",
        })
    return grouped


def draft_picks_by_team(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT *
        FROM draft_pick_inventory_view
        WHERE COALESCE(is_used, 0) = 0
        ORDER BY current_team, draft_year, round, COALESCE(pick_number, 999), pick_id
        """
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        team = row["current_team"]
        if not team:
            continue
        grouped.setdefault(team, []).append({
            "id": int(row["pick_id"]),
            "year": int(row["draft_year"]),
            "round": int(row["round"]),
            "pickNumber": row["pick_number"],
            "pickInRound": row["pick_in_round"],
            "originalTeam": row["original_team"],
            "currentTeam": row["current_team"],
            "traded": bool(row["is_traded"]),
            "tradeNote": row["trade_note"],
            "comp": bool(row["is_comp_pick"]),
            "conditional": bool(row["has_condition"]),
            "condition": row["condition_text"],
            "resolution": row["resolution_status"],
        })
    return grouped


def transactions_by_team(conn: sqlite3.Connection, limit_per_team: int = 70) -> dict[int, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT *
        FROM transaction_log_view
        WHERE team_id IS NOT NULL
        ORDER BY transaction_id DESC
        """
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        team_id = int(row["team_id"])
        items = grouped.setdefault(team_id, [])
        if len(items) >= limit_per_team:
            continue
        items.append(format_transaction(row))
    return grouped


def format_transaction(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["transaction_id"]),
        "date": row["transaction_date"],
        "season": row["season"],
        "phase": row["phase"],
        "type": row["transaction_type"],
        "category": row["transaction_category"],
        "team": row["team"],
        "secondaryTeam": row["secondary_team"],
        "playerId": row["player_id"],
        "player": row["player_name"],
        "position": row["player_position"],
        "fromTeam": row["from_team"],
        "toTeam": row["to_team"],
        "oldStatus": row["old_status"],
        "newStatus": row["new_status"],
        "capDeltaCurrent": int(row["cap_delta_current"] or 0),
        "capDeltaNext": int(row["cap_delta_next"] or 0),
        "cashDelta": int(row["cash_delta"] or 0),
        "description": row["description"] or "",
    }


def league_transactions(conn: sqlite3.Connection, limit: int = 160) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM transaction_log_view
        ORDER BY transaction_id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [format_transaction(row) for row in rows]


def free_agents(conn: sqlite3.Connection, season: int) -> list[dict[str, Any]]:
    roles = role_scores(conn, season)
    rows = conn.execute(
        """
        SELECT *
        FROM free_agent_pool_view
        ORDER BY
            CASE market_tier
                WHEN 'Premium' THEN 1
                WHEN 'Starter' THEN 2
                WHEN 'Rotation' THEN 3
                WHEN 'Depth' THEN 4
                ELSE 9
            END,
            asking_aav DESC,
            player_name
        """
    ).fetchall()
    items = []
    for row in rows:
        player_id = int(row["player_id"])
        role = roles.get(player_id, {"label": row["market_tier"] or "Free Agent", "value": row["overall"] or 50, "grade": grade_label(row["overall"])})
        items.append({
            "id": player_id,
            "name": row["player_name"],
            "position": row["position"],
            "group": row["position_group"],
            "age": row["age"],
            "exp": years_label(row["years_exp"], 0),
            "marketTier": row["market_tier"] or "Open",
            "askingAav": int(row["asking_aav"] or 0),
            "minimumAav": int(row["minimum_aav"] or 0),
            "preferredYears": row["preferred_years"],
            "guaranteePct": row["guarantee_pct"],
            "previousTeam": row["previous_team"],
            "preferredTeams": row["preferred_teams"],
            "hometownTeams": row["hometown_teams"],
            "motivation": row["motivation"] or "",
            "notes": row["signing_notes"] or "",
            "role": role,
        })
    return items


def standings(conn: sqlite3.Connection, season: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM season_standings_view
        WHERE season = ?
        ORDER BY conference, division, wins DESC, losses ASC, point_diff DESC, abbreviation
        """,
        (season,),
    ).fetchall()
    return [
        {
            "teamId": int(row["team_id"]),
            "abbr": row["abbreviation"],
            "name": f"{row['city']} {row['nickname']}",
            "conference": row["conference"],
            "division": row["division"],
            "wins": int(row["wins"] or 0),
            "losses": int(row["losses"] or 0),
            "ties": int(row["ties"] or 0),
            "pointsFor": int(row["points_for"] or 0),
            "pointsAgainst": int(row["points_against"] or 0),
            "pointDiff": int(row["point_diff"] or 0),
            "winPct": float(row["win_pct"] or 0),
        }
        for row in rows
    ]


def waiver_wire(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not table_exists(conn, "waiver_wire_view"):
        return []
    rows = conn.execute(
        """
        SELECT *
        FROM waiver_wire_view
        ORDER BY claim_deadline, waiver_id
        LIMIT 120
        """
    ).fetchall()
    return [
        {
            "id": int(row["waiver_id"]),
            "playerId": int(row["player_id"]),
            "player": row["player_name"],
            "position": row["position"],
            "originalTeam": row["original_team"],
            "waiverDate": row["waiver_date"],
            "claimDeadline": row["claim_deadline"],
            "status": row["status"],
            "reason": row["reason"],
            "claimCount": int(row["claim_count"] or 0),
        }
        for row in rows
    ]


def export(db_path: Path, output_path: Path, season: int) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    caps = latest_cap_by_team(conn)
    logos = team_logos(conn)
    rosters = players_by_team(conn, season)
    depth = depth_by_team(conn)
    coaches = coaches_by_team(conn)
    records = records_by_team(conn, season)
    schedules = schedule_by_team(conn, season)
    cap_lines = cap_lines_by_team(conn)
    draft_picks = draft_picks_by_team(conn)
    team_transactions = transactions_by_team(conn)

    teams: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM teams ORDER BY abbreviation"):
        team_id = int(row["team_id"])
        logo = logos.get(team_id, {})
        roster = rosters.get(team_id, [])
        teams.append({
            "id": team_id,
            "abbr": row["abbreviation"],
            "name": f"{row['city']} {row['nickname']}",
            "city": row["city"],
            "nickname": row["nickname"],
            "conference": row["conference"],
            "division": row["division"],
            "stadium": row["stadium"] or "-",
            "prestige": int(row["prestige"] or 50),
            "colors": {
                "primary": logo.get("primary", "#75808f"),
                "secondary": logo.get("secondary", "#d6dde6"),
            },
            "logo": logo.get("logo"),
            "cap": caps.get(team_id, {
                "salaryCap": int(row["salary_cap"] or 0),
                "capSpace": int(row["cap_space"] or 0),
                "totalCommitted": 0,
                "top51PlayerCap": 0,
                "otherCharges": 0,
                "activeContracts": 0,
                "contractsCounted": 0,
                "contractsExcluded": 0,
                "mode": "TOP_51_ALWAYS",
                "label": "teams_table",
            }),
            "record": records.get(team_id, {
                "wins": 0,
                "losses": 0,
                "ties": 0,
                "pointsFor": 0,
                "pointsAgainst": 0,
            }),
            "roster": roster,
            "rosterCount": len(roster),
            "depth": depth.get(team_id, {}),
            "coaches": coaches.get(team_id, []),
            "schedule": schedules.get(team_id, []),
            "capLines": cap_lines.get(team_id, []),
            "draftPicks": draft_picks.get(row["abbreviation"], []),
            "transactions": team_transactions.get(team_id, []),
        })

    payload = {
        "season": season,
        "generatedFrom": str(db_path),
        "teams": teams,
        "events": upcoming_events(conn),
        "league": {
            "freeAgents": free_agents(conn, season),
            "transactions": league_transactions(conn),
            "standings": standings(conn, season),
            "waivers": waiver_wire(conn),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "/* Generated by tools/export_front_office_ui_data.py. */\n"
        "window.FRONT_OFFICE_DATA = "
        + json.dumps(payload, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    conn.close()
    return len(teams)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export static data for the front-office UI prototype.")
    parser.add_argument("--db", help="Path to nfl_gm.db. Defaults to the active save DB when available.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JS file")
    parser.add_argument("--season", type=int, default=CURRENT_SEASON, help="Season to export")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else default_export_db()
    count = export(db_path, Path(args.output), args.season)
    print(f"Exported {count} teams from {db_path} to {Path(args.output)}")


if __name__ == "__main__":
    main()
