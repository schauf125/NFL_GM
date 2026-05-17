"""Audit player ratings for realism outliers.

This is intentionally heuristic. It catches profiles that are worth a human
look after roster imports, startup variance, progression/regression, or manual
rating edits. It does not try to make every outlier illegal because freaky NFL
players should still exist.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import mean


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "database" / "nfl_gm.db"
DEFAULT_SEASON = 2026
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

CORE_KEYS = (
    "speed",
    "acceleration",
    "agility",
    "strength",
    "balance",
    "hands",
    "catch_in_traffic",
    "contested_catch",
    "route_snap",
    "route_timing",
    "release_vs_press",
    "carry_vision",
    "run_patience",
    "elusiveness",
    "contact_power",
    "pass_accuracy_short",
    "pass_accuracy_mid",
    "pass_accuracy_deep",
    "throw_power",
    "processing_speed",
    "play_recognition",
    "run_block_drive",
    "pass_block_power",
    "pass_block_finesse",
    "block_sustain",
    "speed_rush",
    "power_rush",
    "finesse_rush",
    "rush_plan",
    "block_shedding",
    "solo_tackle",
    "open_field_tackle",
    "man_coverage",
    "zone_coverage",
    "press_coverage",
    "kick_power",
    "kick_accuracy",
)

WR_SPEED_ALLOWLIST = {
    "Tyreek Hill",
    "DK Metcalf",
    "Mecole Hardman Jr.",
    "KaVontae Turpin",
    "Hollywood Brown",
    "Devin Duvernay",
    "Parris Campbell",
    "Darnell Mooney",
    "Terry McLaurin",
    "DJ Moore",
    "Brandon Aiyuk",
}

KNOWN_POSSESSION_WR_SPEED_FLAGS = {
    "Jauan Jennings",
    "Jakobi Meyers",
    "Allen Lazard",
    "Stefon Diggs",
    "Deebo Samuel",
}


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    player_id: int | None
    name: str
    position: str
    team: str
    detail: str


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def name_tokens(name: str) -> list[str]:
    tokens = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return [token for token in tokens if token not in SUFFIXES]


def same_real_name_family(names: list[str]) -> bool:
    token_groups = [name_tokens(name) for name in names]
    if not all(token_groups):
        return False
    last_names = {tokens[-1] for tokens in token_groups}
    if len(last_names) == 1:
        return True
    lowered = {name.lower() for name in names}
    return lowered in (
        {"c.j. gardner-johnson", "chauncey gardner-johnson"},
        {"jermaine johnson", "jermaine johnson ii"},
    )


def season_for_db(conn: sqlite3.Connection, requested: int) -> int:
    if not table_exists(conn, "player_ratings"):
        return requested
    row = conn.execute(
        """
        SELECT season, COUNT(*) AS count
        FROM player_ratings
        GROUP BY season
        ORDER BY count DESC, season DESC
        LIMIT 1
        """
    ).fetchone()
    return int(row["season"]) if row else requested


def load_players(conn: sqlite3.Connection, season: int) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT
            p.player_id,
            p.first_name || ' ' || p.last_name AS name,
            p.position,
            COALESCE(t.abbreviation, 'FA') AS team,
            COALESCE(p.age, 0) AS age,
            COALESCE(p.height_in, 0) AS height_in,
            COALESCE(p.weight_lbs, 0) AS weight_lbs,
            COALESCE(p.overall, 0) AS overall,
            COALESCE(p.potential, 0) AS potential,
            COALESCE(p.speed, 0) AS base_speed,
            COALESCE(p.agility, 0) AS base_agility,
            COALESCE(p.strength, 0) AS base_strength,
            COALESCE(p.status, 'Active') AS status
        FROM players p
        LEFT JOIN teams t ON t.team_id = p.team_id
        WHERE COALESCE(p.status, 'Active') != 'Retired'
        ORDER BY p.player_id
        """
    ).fetchall()

    ratings: dict[int, dict[str, int]] = {}
    for row in conn.execute(
        """
        SELECT player_id, rating_key, rating_value
        FROM player_ratings
        WHERE season = ?
        """,
        (season,),
    ):
        ratings.setdefault(int(row["player_id"]), {})[str(row["rating_key"])] = int(row["rating_value"])

    players: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["ratings"] = ratings.get(int(row["player_id"]), {})
        players.append(item)
    return players


def external_duplicate_groups(conn: sqlite3.Connection) -> list[Finding]:
    if not table_exists(conn, "player_external_ids"):
        return []
    rows = conn.execute(
        """
        WITH identities AS (
            SELECT
                player_id,
                CASE
                    WHEN COALESCE(gsis_id, '') != '' THEN 'gsis:' || gsis_id
                    WHEN COALESCE(pfr_id, '') != '' THEN 'pfr:' || pfr_id
                    ELSE NULL
                END AS identity
            FROM player_external_ids
        )
        SELECT
            i.identity,
            GROUP_CONCAT(
                p.player_id || ':' || p.first_name || ' ' || p.last_name || ':' ||
                p.position || ':' || COALESCE(t.abbreviation, 'FA') || ':' ||
                COALESCE(p.status, 'Active'),
                '; '
            ) AS details,
            COUNT(DISTINCT p.player_id) AS count
        FROM identities i
        JOIN players p ON p.player_id = i.player_id
        LEFT JOIN teams t ON t.team_id = p.team_id
        WHERE i.identity IS NOT NULL
          AND COALESCE(p.status, 'Active') != 'Retired'
        GROUP BY i.identity
        HAVING COUNT(DISTINCT p.player_id) > 1
        ORDER BY i.identity
        """
    ).fetchall()
    findings: list[Finding] = []
    for row in rows:
        names = []
        for chunk in str(row["details"]).split("; "):
            parts = chunk.split(":")
            if len(parts) >= 2:
                names.append(parts[1])
        if not same_real_name_family(names):
            continue
        findings.append(Finding(
            "MED",
            "duplicate_external_identity",
            None,
            str(row["identity"]),
            "-",
            "-",
            str(row["details"]),
        ))
    return findings


def rating(player: dict[str, object], key: str, default: int = 0) -> int:
    ratings = player.get("ratings", {})
    if isinstance(ratings, dict):
        return int(ratings.get(key, default) or default)
    return default


def player_label(player: dict[str, object]) -> tuple[int, str, str, str]:
    return (
        int(player["player_id"]),
        str(player["name"]),
        str(player["position"]),
        str(player["team"]),
    )


def add(
    findings: list[Finding],
    severity: str,
    code: str,
    player: dict[str, object],
    detail: str,
) -> None:
    player_id, name, position, team = player_label(player)
    findings.append(Finding(severity, code, player_id, name, position, team, detail))


def audit_players(conn: sqlite3.Connection, players: list[dict[str, object]]) -> list[Finding]:
    findings: list[Finding] = []
    for player in players:
        name = str(player["name"])
        pos = str(player["position"])

        speed = rating(player, "speed")
        acceleration = rating(player, "acceleration")
        agility = rating(player, "agility")
        strength = rating(player, "strength")
        age = int(player["age"])
        height = int(player["height_in"])
        weight = int(player["weight_lbs"])

        if pos == "WR":
            if name in KNOWN_POSSESSION_WR_SPEED_FLAGS and max(speed, acceleration, agility) >= 88:
                add(
                    findings,
                    "HIGH",
                    "known_wr_athletic_mismatch",
                    player,
                    f"possession/YAC WR has speed/accel/agility {speed}/{acceleration}/{agility}",
                )
            elif age >= 29 and max(speed, acceleration, agility) >= 92 and name not in WR_SPEED_ALLOWLIST:
                add(
                    findings,
                    "MED",
                    "older_wr_elite_burst",
                    player,
                    f"age {age} WR has speed/accel/agility {speed}/{acceleration}/{agility}",
                )
            if height >= 74 and weight >= 210 and speed >= 88 and name not in {"DK Metcalf", "Tee Higgins"}:
                add(
                    findings,
                    "MED",
                    "big_wr_speed_outlier",
                    player,
                    f"{height} in/{weight} lb WR has speed {speed}",
                )

        if pos == "RB" and age >= 29 and max(speed, acceleration, agility) >= 93:
            add(
                findings,
                "MED",
                "older_rb_elite_burst",
                player,
                f"age {age} RB has speed/accel/agility {speed}/{acceleration}/{agility}",
            )

        if pos in {"OT", "OG", "C"} and (speed >= 70 or acceleration >= 72 or agility >= 78):
            add(
                findings,
                "HIGH",
                "ol_athletic_outlier",
                player,
                f"OL has speed/accel/agility {speed}/{acceleration}/{agility}",
            )

        if pos in {"K", "P"} and (speed >= 70 or strength >= 75):
            add(
                findings,
                "LOW",
                "specialist_physical_outlier",
                player,
                f"specialist has speed {speed} and strength {strength}",
            )

        base_speed = int(player["base_speed"])
        base_agility = int(player["base_agility"])
        base_strength = int(player["base_strength"])
        if base_speed and abs(speed - base_speed) >= 12:
            add(
                findings,
                "MED",
                "base_speed_divergence",
                player,
                f"base speed {base_speed}, detailed speed {speed}",
            )
        if base_agility and abs(agility - base_agility) >= 12:
            add(
                findings,
                "MED",
                "base_agility_divergence",
                player,
                f"base agility {base_agility}, detailed agility {agility}",
            )
        if base_strength and abs(strength - base_strength) >= 12:
            add(
                findings,
                "MED",
                "base_strength_divergence",
                player,
                f"base strength {base_strength}, detailed strength {strength}",
            )

    findings.extend(external_duplicate_groups(conn))

    severity_rank = {"HIGH": 0, "MED": 1, "LOW": 2}
    findings.sort(key=lambda f: (severity_rank.get(f.severity, 9), f.code, f.name))
    return findings


def print_distribution(players: list[dict[str, object]], keys: tuple[str, ...]) -> None:
    positions = ["QB", "RB", "WR", "TE", "OT", "OG", "C", "IDL", "EDGE", "LB", "CB", "FS", "SS", "K", "P"]
    print("Distribution snapshot")
    for pos in positions:
        group = [p for p in players if p["position"] == pos]
        if not group:
            continue
        parts = []
        for key in keys:
            values = [rating(p, key) for p in group if rating(p, key)]
            if not values:
                continue
            parts.append(f"{key} avg {mean(values):.1f} min {min(values)} max {max(values)}")
        if parts:
            print(f"  {pos:<4} n={len(group):<3} " + " | ".join(parts))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--distribution", action="store_true")
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        season = season_for_db(conn, args.season)
        players = load_players(conn, season)
        print(f"Rating sanity audit: {args.db} season {season} players {len(players)}")
        if args.distribution:
            print_distribution(players, ("speed", "acceleration", "agility", "strength"))
            print()
        findings = audit_players(conn, players)
        print(f"Findings: {len(findings)}")
        for finding in findings[: args.limit]:
            who = f"#{finding.player_id} {finding.name}" if finding.player_id is not None else finding.name
            print(
                f"[{finding.severity}] {finding.code}: {who} "
                f"({finding.position}, {finding.team}) - {finding.detail}"
            )
        if len(findings) > args.limit:
            print(f"... {len(findings) - args.limit} more hidden by --limit")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
