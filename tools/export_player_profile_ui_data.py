"""Export data for the FM-style player profile UI."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "database" / "nfl_gm.db"
DEFAULT_OUTPUT = PROJECT_ROOT / "ui" / "player_profile" / "player-profile-data.js"
CURRENT_SEASON = 2026

POSITION_LABELS = {
    "QB": "Quarterback",
    "RB": "Running Back",
    "FB": "Fullback",
    "WR": "Wide Receiver",
    "TE": "Tight End",
    "OT": "Offensive Tackle",
    "OG": "Guard",
    "C": "Center",
    "IDL": "Interior Defensive Line",
    "EDGE": "Edge Defender",
    "LB": "Linebacker",
    "CB": "Cornerback",
    "S": "Safety",
    "K": "Kicker",
    "P": "Punter",
    "LS": "Long Snapper",
}

GROUP_LABELS = {
    "universal": "Physical / Mental",
    "passer": "Passing",
    "ball_carrier": "Ball Carrying",
    "receiver": "Receiving",
    "blocker": "Blocking",
    "pass_rusher": "Pass Rush",
    "run_defender": "Run Defense",
    "coverage": "Coverage",
    "tackler": "Tackling",
    "specialist": "Special Teams",
}

GROUP_ORDER = {
    "universal": 1,
    "passer": 2,
    "ball_carrier": 3,
    "receiver": 4,
    "blocker": 5,
    "pass_rusher": 6,
    "run_defender": 7,
    "coverage": 8,
    "tackler": 9,
    "specialist": 10,
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


def clean_hex(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    value = value.strip().lstrip("#")
    if len(value) in {3, 6} and all(char in "0123456789abcdefABCDEF" for char in value):
        return f"#{value}"
    return fallback


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


def height_label(height_in: int | None) -> str:
    if not height_in:
        return "--"
    feet, inches = divmod(int(height_in), 12)
    return f"{feet}'{inches}\""


def years_label(years_exp: int | None, is_rookie: int | None) -> str:
    if is_rookie or years_exp == 0:
        return "Rookie"
    if years_exp is None:
        return "--"
    if years_exp == 1:
        return "1 year"
    return f"{years_exp} years"


def relative_ui_path(local_path: str | None) -> str | None:
    if not local_path:
        return None
    return "../../" + local_path.replace("\\", "/").lstrip("/")


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def money(value: int | None) -> int:
    return int(value or 0)


def team_assets(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT t.team_id, t.abbreviation, t.city, t.nickname, t.conference, t.division,
               g.local_path, g.color, g.alternate_color
        FROM teams t
        LEFT JOIN team_graphics_assets g
          ON g.team_id = t.team_id
         AND g.variant = 'primary'
         AND g.asset_type = 'logo'
        """
    ).fetchall()
    return {
        int(row["team_id"]): {
            "id": int(row["team_id"]),
            "abbr": row["abbreviation"],
            "name": f"{row['city']} {row['nickname']}",
            "conference": row["conference"],
            "division": row["division"],
            "logo": relative_ui_path(row["local_path"]),
            "primary": clean_hex(row["color"], "#75808f"),
            "secondary": clean_hex(row["alternate_color"], "#d6dde6"),
        }
        for row in rows
    }


def headshots(conn: sqlite3.Connection) -> dict[int, str]:
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


def fetch_players(conn: sqlite3.Connection, limit: int | None) -> list[sqlite3.Row]:
    sql = """
        SELECT p.*, t.abbreviation AS team_abbr
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        WHERE COALESCE(p.status, 'Active') != 'Retired'
        ORDER BY
            CASE WHEN p.team_id IS NULL THEN 1 ELSE 0 END,
            t.abbreviation,
            p.last_name,
            p.first_name
    """
    if limit:
        sql += " LIMIT ?"
        return conn.execute(sql, (limit,)).fetchall()
    return conn.execute(sql).fetchall()


def ratings_by_player(conn: sqlite3.Connection, season: int, player_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT player_id, rating_group, rating_key, display_name, rating_value, confidence, source
        FROM player_sim_ratings_view
        WHERE season = ? AND player_id IN ({placeholders})
        ORDER BY player_id, rating_group, display_name
        """,
        [season, *player_ids],
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        value = float(row["rating_value"])
        grouped.setdefault(int(row["player_id"]), []).append({
            "group": row["rating_group"],
            "groupLabel": GROUP_LABELS.get(row["rating_group"], row["rating_group"].replace("_", " ").title()),
            "groupOrder": GROUP_ORDER.get(row["rating_group"], 99),
            "key": row["rating_key"],
            "label": row["display_name"],
            "value": round(max(0, min(100, value)), 1),
            "grade": grade_label(value),
            "confidence": row["confidence"] or "medium",
            "source": row["source"] or "",
        })
    return grouped


def roles_by_player(conn: sqlite3.Connection, season: int, player_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT player_id, role_key, role_score, source
        FROM player_role_scores
        WHERE season = ? AND scheme_key = 'default' AND player_id IN ({placeholders})
        ORDER BY player_id, role_score DESC
        """,
        [season, *player_ids],
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        value = float(row["role_score"])
        items = grouped.setdefault(int(row["player_id"]), [])
        if len(items) < 8:
            items.append({
                "key": row["role_key"],
                "label": role_label(row["role_key"]),
                "value": round(max(0, min(100, value)), 1),
                "grade": grade_label(value),
                "source": row["source"] or "",
            })
    return grouped


def flex_by_player(conn: sqlite3.Connection, player_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT player_id, position, experience, potential, is_primary, source, notes
        FROM player_position_flex
        WHERE player_id IN ({placeholders})
        ORDER BY player_id, is_primary DESC, experience DESC, potential DESC, position
        """,
        player_ids,
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["player_id"]), []).append({
            "position": row["position"],
            "current": int(row["experience"]),
            "potential": int(row["potential"]),
            "primary": bool(row["is_primary"]),
            "source": row["source"] or "",
            "notes": row["notes"] or "",
        })
    return grouped


def career_by_player(conn: sqlite3.Connection, player_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM player_career_stats
        WHERE player_id IN ({placeholders})
        """,
        player_ids,
    ).fetchall()
    return {int(row["player_id"]): dict(row) for row in rows}


def season_stats_by_player(conn: sqlite3.Connection, player_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM player_season_stats_view
        WHERE player_id IN ({placeholders})
        ORDER BY player_id, season DESC
        """,
        player_ids,
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    stat_keys = [
        "season", "stat_team", "stat_position", "games", "completions", "passing_attempts",
        "passing_yards", "passing_tds", "passing_interceptions", "sacks_suffered",
        "carries", "rushing_yards", "rushing_tds", "receptions", "targets",
        "receiving_yards", "receiving_tds", "def_tackles_solo", "def_tackles_with_assist",
        "def_sacks", "def_qb_hits", "def_interceptions", "def_pass_defended",
        "fg_made", "fg_att", "fg_pct", "pat_made", "pat_att", "fantasy_points_ppr",
    ]
    for row in rows:
        grouped.setdefault(int(row["player_id"]), []).append({key: row[key] for key in stat_keys})
    return grouped


def contracts_by_player(conn: sqlite3.Connection, player_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM current_contract_years_view
        WHERE player_id IN ({placeholders})
        """,
        player_ids,
    ).fetchall()
    contracts: dict[int, dict[str, Any]] = {}
    for row in rows:
        contracts[int(row["player_id"])] = {
            "team": row["team"],
            "season": row["season"],
            "yearNumber": row["contract_year_number"],
            "startYear": row["start_year"],
            "endYear": row["end_year"],
            "type": row["contract_type"],
            "baseSalary": money(row["base_salary"]),
            "signingBonusProration": money(row["signing_bonus_proration"]),
            "rosterBonus": money(row["roster_bonus"]),
            "workoutBonus": money(row["workout_bonus"]),
            "guaranteedSalary": money(row["guaranteed_salary"]),
            "capHit": money(row["cap_hit"]),
            "cashDue": money(row["cash_due"]),
            "deadPreJune1": money(row["dead_cap_if_cut_pre_june1"]),
            "deadPostJune1Current": money(row["dead_cap_if_cut_post_june1_current"]),
            "deadPostJune1Next": money(row["dead_cap_if_cut_post_june1_next"]),
            "totalValue": money(row["total_value"]),
            "aav": money(row["aav"]),
            "optionYear": bool(row["is_option_year"]),
            "voidYear": bool(row["is_void_year"]),
            "source": row["source"] or "",
            "notes": row["notes"] or "",
        }
    return contracts


def free_agent_profiles(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    if not table_exists(conn, "free_agent_pool_view"):
        return {}
    rows = conn.execute("SELECT * FROM free_agent_pool_view").fetchall()
    return {
        int(row["player_id"]): {
            "marketTier": row["market_tier"],
            "askingAav": money(row["asking_aav"]),
            "minimumAav": money(row["minimum_aav"]),
            "preferredYears": row["preferred_years"],
            "guaranteePct": row["guarantee_pct"],
            "previousTeam": row["previous_team"],
            "preferredTeams": row["preferred_teams"],
            "hometownTeams": row["hometown_teams"],
            "motivation": row["motivation"] or "",
            "notes": row["signing_notes"] or "",
        }
        for row in rows
    }


def transactions_by_player(conn: sqlite3.Connection, player_ids: list[int], limit_each: int = 14) -> dict[int, list[dict[str, Any]]]:
    if not player_ids:
        return {}
    placeholders = ",".join("?" for _ in player_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM player_transaction_history_view
        WHERE player_id IN ({placeholders})
        ORDER BY player_id, transaction_id DESC
        """,
        player_ids,
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        items = grouped.setdefault(int(row["player_id"]), [])
        if len(items) >= limit_each:
            continue
        items.append({
            "id": int(row["transaction_id"]),
            "date": row["transaction_date"],
            "type": row["transaction_type"],
            "category": row["transaction_category"],
            "team": row["team"],
            "fromTeam": row["from_team"],
            "toTeam": row["to_team"],
            "oldStatus": row["old_status"],
            "newStatus": row["new_status"],
            "capDeltaCurrent": money(row["cap_delta_current"]),
            "cashDelta": money(row["cash_delta"]),
            "description": row["description"] or "",
        })
    return grouped


def build_summary(player: sqlite3.Row, role: dict[str, Any] | None, ratings: list[dict[str, Any]], career: dict[str, Any] | None) -> str:
    name = f"{player['first_name']} {player['last_name']}".strip()
    position = POSITION_LABELS.get(player["position"], player["position"])
    top_traits = sorted(ratings, key=lambda item: item["value"], reverse=True)[:3]
    trait_text = ", ".join(item["label"].lower() for item in top_traits) or "baseline traits"
    role_text = role["label"].lower() if role else "depth role"
    games = int((career or {}).get("career_games") or 0)
    experience = f"{games} career games" if games else "limited regular-season production"
    return f"{name} profiles as a {role_text} at {position}. The strongest visible indicators are {trait_text}. Current production file shows {experience}, with the rest of the page showing the hard data behind the scouting read."


def export(db_path: Path, output_path: Path, season: int, limit: int | None = None) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    players_rows = fetch_players(conn, limit)
    player_ids = [int(row["player_id"]) for row in players_rows]
    teams = team_assets(conn)
    shots = headshots(conn)
    ratings = ratings_by_player(conn, season, player_ids)
    roles = roles_by_player(conn, season, player_ids)
    flex = flex_by_player(conn, player_ids)
    career = career_by_player(conn, player_ids)
    season_stats = season_stats_by_player(conn, player_ids)
    contracts = contracts_by_player(conn, player_ids)
    free_agents = free_agent_profiles(conn)
    transactions = transactions_by_player(conn, player_ids)

    players: list[dict[str, Any]] = []
    for row in players_rows:
        player_id = int(row["player_id"])
        name = f"{row['first_name']} {row['last_name']}".strip()
        player_ratings = ratings.get(player_id, [])
        player_roles = roles.get(player_id, [])
        primary_role = player_roles[0] if player_roles else None
        team = teams.get(int(row["team_id"])) if row["team_id"] is not None else None
        career_row = career.get(player_id, {})
        players.append({
            "id": player_id,
            "name": name,
            "initials": "".join(part[:1] for part in name.split()[:2]).upper(),
            "position": row["position"],
            "positionLabel": POSITION_LABELS.get(row["position"], row["position"]),
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
                "firstName": row["first_name"],
                "lastName": row["last_name"],
                "age": row["age"] if row["age"] is not None else "--",
                "experience": years_label(row["years_exp"], row["is_rookie"]),
                "college": row["college"] or "--",
                "height": height_label(row["height_in"]),
                "weight": f"{row['weight_lbs']} lbs" if row["weight_lbs"] else "--",
                "jersey": f"#{row['jersey_number']}" if row["jersey_number"] is not None else "--",
                "status": row["status"] or "Active",
                "devTrait": row["dev_trait"] or "Normal",
                "isRookie": bool(row["is_rookie"]),
            },
            "roles": player_roles,
            "ratings": player_ratings,
            "flex": flex.get(player_id, []),
            "career": career_row,
            "seasonStats": season_stats.get(player_id, []),
            "contract": contracts.get(player_id),
            "freeAgency": free_agents.get(player_id),
            "transactions": transactions.get(player_id, []),
            "summary": build_summary(row, primary_role, player_ratings, career_row),
        })

    payload = {
        "season": season,
        "ratingGroups": [
            {"key": key, "label": GROUP_LABELS[key], "order": GROUP_ORDER[key]}
            for key in sorted(GROUP_LABELS, key=lambda group: GROUP_ORDER[group])
        ],
        "players": players,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "/* Generated by tools/export_player_profile_ui_data.py. */\n"
        "window.PLAYER_PROFILE_DATA = "
        + json.dumps(payload, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    conn.close()
    return len(players)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export static data for the player profile UI.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to nfl_gm.db")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JS file")
    parser.add_argument("--season", type=int, default=CURRENT_SEASON, help="Ratings/stat season")
    parser.add_argument("--limit", type=int, default=None, help="Optional player limit for quick previews")
    args = parser.parse_args()

    count = export(Path(args.db), Path(args.output), args.season, args.limit)
    print(f"Exported {count} player profiles to {Path(args.output)}")


if __name__ == "__main__":
    main()
