"""Export compact player-card data for the static UI prototype.

The UI should feel like a scouting report, not a spreadsheet. This exporter
keeps raw ratings available only for drawing bars and sends human-readable
labels for the visible card.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from export_player_profile_ui_data import career_totals_by_player, season_stats_by_player


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "database" / "nfl_gm.db"
DEFAULT_OUTPUT = PROJECT_ROOT / "ui" / "player_card" / "player-data.js"

CURRENT_SEASON = 2026

POSITION_RATING_KEYS: dict[str, list[str]] = {
    "QB": [
        "pass_accuracy_short",
        "pass_accuracy_mid",
        "pass_accuracy_deep",
        "throw_power",
        "throw_release",
        "platform_control",
        "processing_speed",
        "play_recognition",
        "composure",
        "speed",
    ],
    "RB": [
        "carry_vision",
        "run_patience",
        "elusiveness",
        "contact_power",
        "balance",
        "speed",
        "ball_security",
        "catch_in_traffic",
        "hands",
    ],
    "FB": [
        "lead_block",
        "run_block_drive",
        "block_sustain",
        "strength",
        "hands",
        "ball_security",
        "contact_power",
    ],
    "WR": [
        "route_timing",
        "route_snap",
        "release_vs_press",
        "hands",
        "catch_in_traffic",
        "contested_catch",
        "speed",
        "agility",
        "ball_security",
    ],
    "TE": [
        "route_timing",
        "hands",
        "catch_in_traffic",
        "contested_catch",
        "run_block_drive",
        "block_sustain",
        "lead_block",
        "strength",
    ],
    "OT": [
        "pass_block_power",
        "pass_block_finesse",
        "pass_block_speed",
        "block_sustain",
        "run_block_drive",
        "reach_block",
        "strength",
        "balance",
    ],
    "OG": [
        "run_block_drive",
        "pass_block_power",
        "pass_block_finesse",
        "block_sustain",
        "reach_block",
        "strength",
        "balance",
    ],
    "C": [
        "block_sustain",
        "run_block_drive",
        "pass_block_power",
        "pass_block_finesse",
        "reach_block",
        "strength",
        "processing_speed",
    ],
    "IDL": [
        "block_shedding",
        "gap_integrity",
        "double_team_takeon",
        "power_rush",
        "run_diagnostics",
        "strength",
        "pursuit_angle",
        "traffic_navigation",
    ],
    "EDGE": [
        "speed_rush",
        "power_rush",
        "finesse_rush",
        "rush_plan",
        "sack_finish",
        "edge_contain",
        "block_shedding",
        "pursuit_angle",
    ],
    "LB": [
        "run_diagnostics",
        "pursuit_angle",
        "traffic_navigation",
        "solo_tackle",
        "open_field_tackle",
        "zone_coverage",
        "play_recognition",
        "processing_speed",
    ],
    "CB": [
        "man_coverage",
        "zone_coverage",
        "press_coverage",
        "zone_recovery",
        "ball_skills",
        "speed",
        "agility",
        "open_field_tackle",
    ],
    "S": [
        "zone_coverage",
        "man_coverage",
        "ball_skills",
        "open_field_tackle",
        "hit_power",
        "play_recognition",
        "processing_speed",
        "pursuit_angle",
    ],
    "K": [
        "kick_power",
        "kick_accuracy",
        "composure",
        "consistency",
    ],
    "P": [
        "kick_power",
        "kick_accuracy",
        "composure",
        "consistency",
    ],
    "LS": [
        "consistency",
        "discipline",
        "composure",
        "tackle_wrap",
    ],
}

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


def clean_hex(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    value = value.strip().lstrip("#")
    if len(value) in {3, 6} and all(char in "0123456789abcdefABCDEF" for char in value):
        return f"#{value}"
    return fallback


def height_label(height_in: int | None) -> str:
    if not height_in:
        return "--"
    feet, inches = divmod(int(height_in), 12)
    return f"{feet}'{inches}\""


def years_label(years_exp: int | None, is_rookie: int | None) -> str:
    if is_rookie:
        return "Rookie"
    if years_exp is None:
        return "--"
    if years_exp == 0:
        return "Rookie"
    if years_exp == 1:
        return "1 year"
    return f"{years_exp} years"


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
    replacements = {
        "qb": "QB",
        "wr": "WR",
        "rb": "RB",
        "ot": "OT",
        "te": "TE",
        "idl": "IDL",
        "cb": "CB",
    }
    words = []
    for part in role_key.split("_"):
        words.append(replacements.get(part, part.title()))
    return " ".join(words)


def position_keys(position: str) -> list[str]:
    return POSITION_RATING_KEYS.get(position, [
        "speed",
        "strength",
        "agility",
        "play_recognition",
        "processing_speed",
        "composure",
        "consistency",
    ])


def relative_ui_path(local_path: str | None) -> str | None:
    if not local_path:
        return None
    return "../../" + local_path.replace("\\", "/").lstrip("/")


def build_metric(rating: dict[str, Any]) -> dict[str, Any]:
    value = float(rating["value"])
    return {
        "key": rating["key"],
        "label": rating["label"],
        "value": round(max(0, min(100, value)), 1),
        "grade": grade_label(value),
    }


def average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def development_label(age: int | None, is_rookie: int | None, dev_trait: str | None, fit_value: float | None) -> str:
    trait = (dev_trait or "Normal").strip()
    if is_rookie:
        if fit_value and fit_value >= 74:
            return "Early Contributor"
        return "Developmental Rookie"
    if age is not None and age <= 24:
        if trait.lower() in {"superstar", "elite", "star"}:
            return "High Ceiling"
        return "Ascending"
    if age is not None and age >= 31:
        return "Veteran Window"
    if fit_value and fit_value >= 82:
        return "Core Starter"
    return "Established"


def risk_label(metrics_by_key: dict[str, dict[str, Any]], age: int | None) -> str:
    durability = metrics_by_key.get("durability", {}).get("value")
    if durability is not None and durability < 58:
        return "Elevated"
    if age is not None and age >= 32:
        return "Moderate"
    if durability is not None and durability >= 78:
        return "Low"
    return "Moderate"


def scouting_report(name: str, position: str, role: str, strengths: list[dict[str, Any]], improvements: list[dict[str, Any]]) -> str:
    strength_text = ", ".join(metric["label"].lower() for metric in strengths[:3]) or "functional traits"
    improve_text = " and ".join(metric["label"].lower() for metric in improvements[:2]) or "week-to-week consistency"
    position_name = POSITION_LABELS.get(position, position)
    return (
        f"{name} profiles as a {role.lower()} at {position_name}. "
        f"The strongest current markers are {strength_text}. "
        f"The main coaching points are {improve_text}, which shape how much trust a staff should place in the role right away."
    )


def compact_number(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.1f}"


def stat_snapshot(position: str, row: dict[str, Any] | None) -> list[dict[str, str]]:
    if not row:
        return []
    position = str(position or "").upper()
    if position == "QB":
        keys = [
            ("passing_yards", "Pass Yds"),
            ("passing_tds", "Pass TD"),
            ("passing_interceptions", "INT"),
            ("sacks_suffered", "Sacks"),
        ]
    elif position in {"RB", "FB"}:
        keys = [
            ("rushing_yards", "Rush Yds"),
            ("rushing_tds", "Rush TD"),
            ("receptions", "Rec"),
            ("receiving_yards", "Rec Yds"),
        ]
    elif position in {"WR", "TE"}:
        keys = [
            ("receptions", "Rec"),
            ("targets", "Tgt"),
            ("receiving_yards", "Rec Yds"),
            ("receiving_tds", "Rec TD"),
        ]
    elif position in {"K", "P", "LS"}:
        keys = [
            ("fg_made", "FGM"),
            ("fg_att", "FGA"),
            ("pat_made", "XPM"),
            ("fg_long", "Long"),
        ]
    else:
        keys = [
            ("def_tackles_solo", "Solo"),
            ("def_tackles_with_assist", "Ast"),
            ("def_sacks", "Sacks"),
            ("def_interceptions", "INT"),
        ]
    return [
        {"key": key, "label": label, "value": compact_number(row.get(key))}
        for key, label in keys
    ]


def season_stat_payload(position: str, rows: list[dict[str, Any]], export_season: int) -> dict[str, Any]:
    recent = sorted(rows, key=lambda item: int(item.get("season") or 0), reverse=True)
    selected = next((row for row in recent if int(row.get("season") or 0) == export_season), None)
    if not selected and recent:
        selected = recent[0]
    return {
        "selectedSeason": int(selected["season"]) if selected else None,
        "selectedTeam": selected.get("stat_team") if selected else None,
        "isExportSeason": bool(selected and int(selected.get("season") or 0) == export_season),
        "headline": stat_snapshot(position, selected),
        "recent": [
            {
                "season": int(row.get("season") or 0),
                "team": row.get("stat_team"),
                "games": int(row.get("games") or 0),
                "line": stat_snapshot(position, row),
            }
            for row in recent[:6]
        ],
    }


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def fetch_rows(
    conn: sqlite3.Connection,
    season: int,
    limit: int | None,
    include_headshots: bool,
    player_id: int | None = None,
) -> list[sqlite3.Row]:
    headshot_join = """
        LEFT JOIN player_graphics_assets pga
            ON pga.player_id = p.player_id
            AND pga.asset_key = 'headshot_espn_full'
            AND pga.asset_type = 'headshot'
    """ if include_headshots else ""
    headshot_select = "pga.local_path AS headshot_path," if include_headshots else "NULL AS headshot_path,"
    params: list[Any] = []
    player_filter = ""
    if player_id is not None:
        player_filter = " AND p.player_id = ?"
        params.append(player_id)
    sql = f"""
        SELECT
            p.player_id,
            p.first_name,
            p.last_name,
            p.position,
            p.team_id,
            p.age,
            p.years_exp,
            p.college,
            p.height_in,
            p.weight_lbs,
            p.dev_trait,
            p.status,
            p.is_rookie,
            p.jersey_number,
            t.city,
            t.nickname,
            t.abbreviation,
            {headshot_select}
            g.local_path AS logo_path,
            g.color AS team_color,
            g.alternate_color AS team_alt_color
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        LEFT JOIN team_graphics_assets g
            ON g.team_id = t.team_id
            AND g.variant = 'primary'
            AND g.asset_type = 'logo'
        {headshot_join}
        WHERE COALESCE(p.status, 'Active') != 'Retired'
          {player_filter}
        ORDER BY
            CASE WHEN p.team_id IS NULL THEN 1 ELSE 0 END,
            t.abbreviation,
            CASE p.position
                WHEN 'QB' THEN 1
                WHEN 'RB' THEN 2
                WHEN 'WR' THEN 3
                WHEN 'TE' THEN 4
                WHEN 'OT' THEN 5
                WHEN 'OG' THEN 6
                WHEN 'C' THEN 7
                WHEN 'EDGE' THEN 8
                WHEN 'IDL' THEN 9
                WHEN 'LB' THEN 10
                WHEN 'CB' THEN 11
                WHEN 'S' THEN 12
                WHEN 'K' THEN 13
                WHEN 'P' THEN 14
                WHEN 'LS' THEN 15
                ELSE 99
            END,
            p.last_name,
            p.first_name
    """
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def build_payload(db_path: Path, season: int, limit: int | None = None, player_id: int | None = None) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    player_rows = fetch_rows(conn, season, limit, table_exists(conn, "player_graphics_assets"), player_id)
    player_ids = [row["player_id"] for row in player_rows]
    if not player_ids:
        conn.close()
        return {"season": season, "playerCount": 0, "players": []}

    placeholders = ",".join("?" for _ in player_ids)

    definitions = {
        row["rating_key"]: {
            "label": row["display_name"],
            "group": row["rating_group"],
        }
        for row in conn.execute("SELECT rating_key, display_name, rating_group FROM rating_definitions")
    }

    ratings_by_player: dict[int, dict[str, dict[str, Any]]] = {player_id: {} for player_id in player_ids}
    rating_rows = conn.execute(
        f"""
        WITH latest AS (
            SELECT player_id, MAX(season) AS season
            FROM player_ratings
            WHERE season <= ?
              AND player_id IN ({placeholders})
            GROUP BY player_id
        )
        SELECT r.player_id, r.season, r.rating_key, r.rating_value
        FROM player_ratings r
        JOIN latest l
          ON l.player_id = r.player_id
         AND l.season = r.season
        """,
        [season, *player_ids],
    ).fetchall()
    for row in rating_rows:
        definition = definitions.get(row["rating_key"], {})
        ratings_by_player[int(row["player_id"])][row["rating_key"]] = {
            "key": row["rating_key"],
            "label": definition.get("label", row["rating_key"].replace("_", " ").title()),
            "group": definition.get("group", "general"),
            "value": float(row["rating_value"]),
            "season": int(row["season"]),
        }

    roles_by_player: dict[int, list[dict[str, Any]]] = {player_id: [] for player_id in player_ids}
    role_rows = conn.execute(
        f"""
        WITH latest AS (
            SELECT player_id, MAX(season) AS season
            FROM player_role_scores
            WHERE season <= ?
              AND scheme_key = 'default'
              AND player_id IN ({placeholders})
            GROUP BY player_id
        )
        SELECT r.player_id, r.season, r.role_key, r.role_score
        FROM player_role_scores r
        JOIN latest l
          ON l.player_id = r.player_id
         AND l.season = r.season
        WHERE r.scheme_key = 'default'
        ORDER BY r.player_id, r.role_score DESC
        """,
        [season, *player_ids],
    ).fetchall()
    for row in role_rows:
        roles = roles_by_player[int(row["player_id"])]
        if len(roles) < 3:
            score = float(row["role_score"])
            roles.append({
                "key": row["role_key"],
                "label": role_label(row["role_key"]),
                "value": round(max(0, min(100, score)), 1),
                "grade": grade_label(score),
                "season": int(row["season"]),
            })

    stats_by_player = season_stats_by_player(conn, player_ids)
    career_by_player = career_totals_by_player(stats_by_player, {})

    players: list[dict[str, Any]] = []
    for row in player_rows:
        player_id = int(row["player_id"])
        full_name = f"{row['first_name']} {row['last_name']}".strip()
        position = row["position"]
        metrics_by_key = ratings_by_player.get(player_id, {})
        ordered_metrics = [metrics_by_key[key] for key in position_keys(position) if key in metrics_by_key]

        if len(ordered_metrics) < 6:
            extra_metrics = [
                metric
                for key, metric in metrics_by_key.items()
                if key not in {item["key"] for item in ordered_metrics}
            ]
            ordered_metrics.extend(sorted(extra_metrics, key=lambda item: item["value"], reverse=True)[: 8 - len(ordered_metrics)])

        strengths = [build_metric(metric) for metric in sorted(ordered_metrics, key=lambda item: item["value"], reverse=True)[:5]]
        improvements = [
            build_metric(metric)
            for metric in sorted(ordered_metrics, key=lambda item: item["value"])[:5]
            if metric["key"] not in {strength["key"] for strength in strengths[:2]}
        ][:5]
        attribute_board = [build_metric(metric) for metric in ordered_metrics[:8]]

        fit_value = average([metric["value"] for metric in ordered_metrics[:8]])
        roles = roles_by_player.get(player_id, [])
        primary_role = roles[0] if roles else {
            "key": None,
            "label": "Depth Role",
            "value": round(fit_value or 50, 1),
            "grade": grade_label(fit_value),
        }

        team_abbr = row["abbreviation"] or "FA"
        team_name = f"{row['city']} {row['nickname']}".strip() if row["city"] else "Free Agent"
        jersey = row["jersey_number"]
        initials = "".join(part[:1] for part in full_name.split()[:2]).upper()
        stat_rows = stats_by_player.get(player_id, [])
        career = career_by_player.get(player_id, {})

        players.append({
            "id": player_id,
            "name": full_name,
            "initials": initials,
            "position": position,
            "positionLabel": POSITION_LABELS.get(position, position),
            "team": {
                "id": row["team_id"],
                "abbr": team_abbr,
                "name": team_name,
                "logo": relative_ui_path(row["logo_path"]),
                "primary": clean_hex(row["team_color"], "#75808f"),
                "secondary": clean_hex(row["team_alt_color"], "#c9d1d9"),
            },
            "headshot": relative_ui_path(row["headshot_path"]),
            "profile": {
                "age": row["age"] if row["age"] is not None else "--",
                "experience": years_label(row["years_exp"], row["is_rookie"]),
                "college": row["college"] or "--",
                "height": height_label(row["height_in"]),
                "weight": f"{row['weight_lbs']} lbs" if row["weight_lbs"] else "--",
                "jersey": f"#{jersey}" if jersey is not None else "--",
                "status": row["status"] or "Active",
            },
            "role": primary_role,
            "secondaryRole": roles[1] if len(roles) > 1 else None,
            "development": development_label(row["age"], row["is_rookie"], row["dev_trait"], fit_value),
            "risk": risk_label(metrics_by_key, row["age"]),
            "strengths": strengths,
            "improvements": improvements,
            "attributes": attribute_board,
            "scoutingReport": scouting_report(full_name, position, primary_role["label"], strengths, improvements),
            "seasonStats": season_stat_payload(position, stat_rows, season),
            "careerStats": {
                "seasons": career.get("seasons_played"),
                "games": career.get("career_games"),
                "headline": stat_snapshot(position, career),
            } if career else None,
        })

    payload = {
        "season": season,
        "playerCount": len(players),
        "players": players,
    }
    conn.close()
    return payload


def export(db_path: Path, output_path: Path, season: int, limit: int | None = None) -> int:
    payload = build_payload(db_path, season, limit)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "/* Generated by tools/export_player_card_ui_data.py. */\n"
        "window.PLAYER_CARD_DATA = "
        + json.dumps(payload, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    return int(payload.get("playerCount") or 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export static UI data for the player-card prototype.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to nfl_gm.db")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JS file")
    parser.add_argument("--season", type=int, default=CURRENT_SEASON, help="Ratings season to export")
    parser.add_argument("--limit", type=int, default=None, help="Optional player limit for quick previews")
    args = parser.parse_args()

    count = export(Path(args.db), Path(args.output), args.season, args.limit)
    print(f"Exported {count} players to {Path(args.output)}")


if __name__ == "__main__":
    main()
