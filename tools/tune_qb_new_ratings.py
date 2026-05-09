"""Rebalance 2026 QB ratings in the new hidden rating system only.

This does not update players.overall, players.potential, or dev_trait.
It updates player_ratings and recalculates player_role_scores for QBs.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
SEASON = 2026
SOURCE = "qb_public_perception_2026_rebalance"

QB_ROLE_KEYS = {"pocket_qb", "scrambling_qb"}

PASSING_MENTAL_KEYS = {
    "pass_accuracy_short",
    "pass_accuracy_mid",
    "pass_accuracy_deep",
    "throw_release",
    "platform_control",
    "processing_speed",
    "play_recognition",
    "composure",
    "consistency",
}

ARM_KEYS = {"throw_power"}
ATHLETIC_KEYS = {"speed", "acceleration", "agility", "carry_vision", "ball_security"}
OTHER_QB_KEYS = {"durability"}
QB_KEYS = PASSING_MENTAL_KEYS | ARM_KEYS | ATHLETIC_KEYS | OTHER_QB_KEYS


def profile(
    short: int,
    mid: int,
    deep: int,
    power: int,
    release: int,
    platform: int,
    processing: int,
    recognition: int,
    composure: int,
    consistency: int,
    speed: int,
    acceleration: int,
    agility: int,
    carry_vision: int,
    ball_security: int,
    durability: int,
    note: str,
) -> dict[str, object]:
    return {
        "ratings": {
            "pass_accuracy_short": short,
            "pass_accuracy_mid": mid,
            "pass_accuracy_deep": deep,
            "throw_power": power,
            "throw_release": release,
            "platform_control": platform,
            "processing_speed": processing,
            "play_recognition": recognition,
            "composure": composure,
            "consistency": consistency,
            "speed": speed,
            "acceleration": acceleration,
            "agility": agility,
            "carry_vision": carry_vision,
            "ball_security": ball_security,
            "durability": durability,
        },
        "note": note,
    }


# Team abbreviations are included so duplicate names, future moves, and FA rows are safe.
QB_PROFILES: dict[tuple[str, str, str], dict[str, object]] = {
    ("Matthew", "Stafford", "LAR"): profile(88, 86, 85, 88, 86, 80, 86, 87, 87, 83, 48, 50, 52, 46, 72, 38, "Dangerous late-career passer, but no longer treated as the top hidden QB in the league."),
    ("Joe", "Burrow", "CIN"): profile(95, 93, 90, 90, 93, 87, 93, 94, 94, 91, 58, 60, 62, 56, 82, 54, "Elite efficiency when healthy; injury risk keeps durability restrained."),
    ("Patrick", "Mahomes", "KC"): profile(93, 92, 90, 95, 92, 88, 93, 94, 94, 92, 70, 72, 74, 70, 82, 55, "Still elite, but compressed after a down/injury-affected 2025 season."),
    ("Josh", "Allen", "BUF"): profile(91, 91, 90, 98, 89, 87, 91, 91, 93, 91, 82, 84, 80, 87, 84, 65, "Reigning elite dual-threat QB and 2025 AP MVP finalist."),
    ("Lamar", "Jackson", "BAL"): profile(88, 86, 85, 91, 84, 86, 89, 90, 92, 89, 96, 97, 96, 95, 85, 62, "MVP-level talent, with 2025 mobility/injury dip reflected."),
    ("Drake", "Maye", "NE"): profile(93, 92, 91, 96, 87, 84, 91, 91, 92, 90, 80, 82, 78, 82, 78, 65, "Near-MVP 2025 season and strong PFF regular-season grade."),
    ("Jordan", "Love", "GB"): profile(91, 90, 89, 91, 87, 84, 88, 89, 89, 88, 68, 70, 68, 68, 78, 64, "PFF top-five 2025 grade, but not pushed into the elite tier."),
    ("Justin", "Herbert", "LAC"): profile(91, 89, 88, 95, 86, 84, 87, 88, 89, 87, 76, 78, 76, 74, 76, 65, "High-end traits with midseason PFF peak, toned below elite consistency."),
    ("Trevor", "Lawrence", "JAX"): profile(90, 89, 88, 91, 86, 84, 88, 89, 90, 87, 76, 78, 78, 78, 77, 58, "2025 AP MVP finalist and late-season PFF surge."),
    ("Dak", "Prescott", "DAL"): profile(90, 88, 87, 88, 88, 84, 89, 90, 89, 88, 68, 70, 68, 66, 79, 62, "Strong 2025 PFF profile, kept in very-good-starter range."),
    ("Brock", "Purdy", "SF"): profile(85, 83, 80, 84, 83, 79, 82, 83, 84, 80, 68, 70, 68, 66, 74, 46, "Efficient starter, but no longer boosted into the near-elite hidden QB tier."),
    ("Jared", "Goff", "DET"): profile(92, 89, 86, 86, 87, 80, 88, 89, 88, 88, 55, 56, 58, 54, 80, 62, "Accurate rhythm passer, slightly below the top tier."),
    ("Jalen", "Hurts", "PHI"): profile(88, 86, 84, 88, 85, 84, 86, 87, 89, 86, 86, 87, 84, 88, 84, 66, "Excellent dual-threat floor, but passing efficiency toned down."),
    ("Sam", "Darnold", "SEA"): profile(88, 86, 85, 87, 85, 80, 86, 86, 87, 84, 65, 66, 66, 68, 76, 58, "2025 production bump, but still not treated as a locked elite QB."),
    ("Caleb", "Williams", "CHI"): profile(86, 84, 86, 89, 83, 81, 83, 83, 85, 82, 74, 75, 74, 76, 77, 65, "Flashes and late-season growth, with young-QB inconsistency retained."),
    ("Bo", "Nix", "DEN"): profile(86, 84, 82, 84, 84, 80, 84, 84, 85, 82, 78, 80, 76, 80, 80, 64, "Efficient sack-avoidance and growth, below star-QB tier for now."),
    ("Baker", "Mayfield", "TB"): profile(86, 84, 84, 86, 84, 80, 85, 85, 86, 83, 65, 66, 66, 68, 74, 58, "Good starter with streakiness and late-season slide accounted for."),
    ("C.J.", "Stroud", "HOU"): profile(86, 84, 84, 87, 84, 80, 84, 84, 84, 82, 70, 71, 70, 70, 76, 60, "Starter traits remain, but 2025 inconsistency tones him down."),
    ("Jayden", "Daniels", "WAS"): profile(82, 80, 81, 85, 80, 80, 80, 81, 82, 78, 91, 92, 90, 88, 79, 58, "High upside and rushing value, but 2025 injury/availability pulls down passing polish."),
    ("Daniel", "Jones", "IND"): profile(82, 80, 79, 84, 80, 78, 80, 80, 81, 78, 82, 84, 82, 84, 74, 56, "Early 2025 Colts stretch respected without overrating the full profile."),
    ("Kyler", "Murray", "MIN"): profile(78, 76, 78, 87, 79, 78, 76, 78, 76, 74, 88, 89, 90, 86, 70, 48, "Explosive tools remain, but 2025 public perception was down sharply."),
    ("Geno", "Smith", "NYJ"): profile(83, 81, 80, 86, 82, 77, 81, 82, 80, 78, 62, 63, 62, 62, 72, 52, "Starter arm/accuracy with 2025 turnover and situation concerns."),
    ("Kirk", "Cousins", "LV"): profile(78, 76, 73, 75, 78, 72, 78, 79, 76, 74, 43, 44, 46, 42, 72, 38, "Late-career timing passer; processing remains useful but arm, durability, and week-to-week efficiency are fading."),
    ("Bryce", "Young", "CAR"): profile(82, 80, 78, 82, 80, 76, 80, 80, 80, 77, 68, 70, 70, 70, 74, 58, "Tools and flashes remain, but consistency is not there yet."),
    ("Tua", "Tagovailoa", "ATL"): profile(80, 77, 75, 79, 80, 73, 75, 76, 74, 72, 58, 58, 60, 58, 70, 38, "Accuracy respected, but lower week-to-week stability and turnover risk keep him out of the leader-board tier."),
    ("Tua", "Tagovailoa", "MIA"): profile(80, 77, 75, 79, 80, 73, 75, 76, 74, 72, 58, 58, 60, 58, 70, 38, "Accuracy respected, but lower week-to-week stability and turnover risk keep him out of the leader-board tier."),
    ("Cam", "Ward", "TEN"): profile(80, 78, 80, 90, 78, 76, 77, 77, 78, 76, 82, 83, 84, 84, 74, 60, "High-end arm talent, but rookie processing and efficiency remain developing."),
    ("Tyler", "Shough", "NO"): profile(81, 79, 79, 86, 80, 77, 78, 78, 80, 77, 70, 72, 70, 72, 76, 56, "Rookie progress and tools respected without jumping him too high."),
    ("Jaxson", "Dart", "NYG"): profile(79, 77, 78, 88, 78, 77, 76, 76, 78, 75, 78, 80, 80, 82, 73, 54, "Exciting rookie flashes, but processing and risk remain raw."),
    ("Shedeur", "Sanders", "CLE"): profile(72, 70, 68, 80, 72, 68, 69, 70, 70, 66, 60, 61, 62, 58, 68, 52, "Developmental pocket prospect with useful accuracy but real rookie volatility."),
    ("Michael", "Penix Jr.", "ATL"): profile(79, 77, 78, 88, 78, 75, 76, 76, 76, 74, 65, 66, 66, 64, 74, 48, "Arm talent remains, but 2025 disappointment/injury lowers readiness."),
    ("J.J.", "McCarthy", "MIN"): profile(78, 76, 76, 85, 76, 75, 75, 75, 77, 74, 74, 75, 76, 76, 73, 52, "Some late growth, but still a developmental starter profile."),
    ("Quinn", "Ewers", "MIA"): profile(78, 76, 77, 86, 77, 75, 75, 75, 76, 74, 67, 68, 68, 66, 73, 54, "Arm talent and prospect pedigree, but not starter-ready in ratings."),
    ("Fernando", "Mendoza", "LV"): profile(77, 75, 77, 89, 76, 74, 74, 74, 76, 73, 68, 69, 68, 68, 72, 55, "Top-pick upside in a weaker QB class, with rookie readiness toned down."),
    ("Justin", "Fields", "KC"): profile(75, 73, 75, 88, 75, 75, 73, 74, 74, 72, 90, 91, 88, 88, 70, 60, "Elite rushing still matters, passing role remains limited."),
    ("Justin", "Fields", "NYJ"): profile(74, 72, 74, 88, 74, 74, 72, 73, 73, 71, 90, 91, 88, 88, 70, 58, "Elite rushing still matters, passing role remains limited."),
    ("Malik", "Willis", "MIA"): profile(74, 72, 75, 87, 74, 73, 72, 72, 74, 71, 88, 89, 88, 86, 71, 58, "Improved athletic backup, still a limited passer."),
    ("Anthony", "Richardson Sr.", "IND"): profile(72, 70, 74, 96, 72, 73, 71, 71, 73, 70, 91, 92, 90, 90, 68, 50, "Rare tools, but passing polish and durability remain major constraints."),
}


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def clamp(value: int) -> int:
    return max(1, min(99, int(round(value))))


def compressed_value(key: str, old_value: int, max_role_score: float | None) -> int:
    """Gentle default compression for QBs without a manual public-perception profile."""
    value = int(old_value)
    score = max_role_score or 0

    if key in PASSING_MENTAL_KEYS:
        if score >= 82 or value >= 88:
            value -= 2
        elif score >= 76 or value >= 80:
            value -= 1
    elif key in ARM_KEYS:
        if value >= 94:
            value -= 1
    elif key in ATHLETIC_KEYS:
        if value >= 90:
            value -= 1
    elif key in OTHER_QB_KEYS and value >= 70:
        value -= 1

    return clamp(value)


def load_qbs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT p.player_id, p.first_name, p.last_name, p.position,
                   COALESCE(t.abbreviation, 'FA') AS team
            FROM players p
            LEFT JOIN teams t ON t.team_id = p.team_id
            WHERE p.position = 'QB'
              AND EXISTS (
                  SELECT 1
                  FROM player_ratings pr
                  WHERE pr.player_id = p.player_id
                    AND pr.season = ?
              )
            ORDER BY team, p.last_name, p.first_name
            """,
            (SEASON,),
        )
    )


def current_ratings(conn: sqlite3.Connection, player_id: int) -> dict[str, int]:
    return {
        row["rating_key"]: row["rating_value"]
        for row in conn.execute(
            """
            SELECT rating_key, rating_value
            FROM player_ratings
            WHERE player_id = ?
              AND season = ?
              AND rating_key IN ({})
            """.format(",".join("?" for _ in QB_KEYS)),
            (player_id, SEASON, *sorted(QB_KEYS)),
        )
    }


def max_role_score(conn: sqlite3.Connection, player_id: int) -> float | None:
    row = conn.execute(
        """
        SELECT MAX(role_score) AS role_score
        FROM player_role_scores
        WHERE player_id = ?
          AND season = ?
          AND role_key IN ({})
        """.format(",".join("?" for _ in QB_ROLE_KEYS)),
        (player_id, SEASON, *sorted(QB_ROLE_KEYS)),
    ).fetchone()
    return None if row is None or row["role_score"] is None else float(row["role_score"])


def upsert_rating(
    conn: sqlite3.Connection,
    player_id: int,
    key: str,
    value: int,
    note: str,
) -> None:
    conn.execute(
        """
        INSERT INTO player_ratings (
            player_id, season, rating_key, rating_value, confidence, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, 'medium', ?, ?, datetime('now'))
        ON CONFLICT(player_id, season, rating_key) DO UPDATE SET
            rating_value = excluded.rating_value,
            confidence = excluded.confidence,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (player_id, SEASON, key, clamp(value), SOURCE, note),
    )


def role_weights(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    weights: dict[str, dict[str, float]] = {}
    for row in conn.execute(
        """
        SELECT role_key, rating_key, weight
        FROM role_score_weights
        WHERE role_key IN ({})
        """.format(",".join("?" for _ in QB_ROLE_KEYS)),
        (*sorted(QB_ROLE_KEYS),),
    ):
        weights.setdefault(row["role_key"], {})[row["rating_key"]] = float(row["weight"])
    return weights


def all_player_ratings(conn: sqlite3.Connection, player_id: int) -> dict[str, int]:
    return {
        row["rating_key"]: row["rating_value"]
        for row in conn.execute(
            """
            SELECT rating_key, rating_value
            FROM player_ratings
            WHERE player_id = ?
              AND season = ?
            """,
            (player_id, SEASON),
        )
    }


def recalculate_role_scores(
    conn: sqlite3.Connection,
    player_id: int,
    weights_by_role: dict[str, dict[str, float]],
) -> None:
    ratings = all_player_ratings(conn, player_id)
    assigned_roles = [
        row["role_key"]
        for row in conn.execute(
            """
            SELECT role_key
            FROM player_role_assignments
            WHERE player_id = ?
              AND season = ?
              AND role_key IN ({})
            ORDER BY priority, role_key
            """.format(",".join("?" for _ in QB_ROLE_KEYS)),
            (player_id, SEASON, *sorted(QB_ROLE_KEYS)),
        )
    ]

    for role_key in assigned_roles:
        weights = weights_by_role.get(role_key, {})
        if not weights:
            continue
        weighted = 0.0
        total = 0.0
        missing = False
        for rating_key, weight in weights.items():
            if rating_key not in ratings:
                missing = True
                break
            weighted += ratings[rating_key] * weight
            total += weight
        if missing or total <= 0:
            continue
        role_score = round(weighted / total, 2)
        conn.execute(
            """
            INSERT INTO player_role_scores (
                player_id, season, role_key, scheme_key, role_score, source, calculated_at
            )
            VALUES (?, ?, ?, 'default', ?, ?, datetime('now'))
            ON CONFLICT(player_id, season, role_key, scheme_key) DO UPDATE SET
                role_score = excluded.role_score,
                source = excluded.source,
                calculated_at = excluded.calculated_at
            """,
            (player_id, SEASON, role_key, role_score, SOURCE),
        )


def preview_top_qbs(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT COALESCE(t.abbreviation, 'FA') AS team,
                   p.first_name || ' ' || p.last_name AS name,
                   MAX(prs.role_score) AS max_role_score,
                   GROUP_CONCAT(prs.role_key || ':' || ROUND(prs.role_score, 1), ', ') AS roles
            FROM player_role_scores prs
            JOIN players p ON p.player_id = prs.player_id
            LEFT JOIN teams t ON t.team_id = p.team_id
            WHERE p.position = 'QB'
              AND prs.season = ?
              AND prs.role_key IN ({})
            GROUP BY p.player_id
            ORDER BY max_role_score DESC, name
            LIMIT ?
            """.format(",".join("?" for _ in QB_ROLE_KEYS)),
            (SEASON, *sorted(QB_ROLE_KEYS), limit),
        )
    )


def print_top(title: str, rows: list[sqlite3.Row]) -> None:
    print(title)
    for index, row in enumerate(rows, start=1):
        print(
            f"{index:2}. {row['team']:>3} {row['name']:<24} "
            f"{row['max_role_score']:5.2f}  {row['roles']}"
        )


def apply_rebalance(conn: sqlite3.Connection) -> tuple[int, int]:
    weights = role_weights(conn)
    qbs = load_qbs(conn)
    rating_updates = 0
    manual_profiles = 0

    for player in qbs:
        identity = (player["first_name"], player["last_name"], player["team"])
        existing = current_ratings(conn, player["player_id"])
        old_score = max_role_score(conn, player["player_id"])
        if not existing:
            continue

        if identity in QB_PROFILES:
            manual_profiles += 1
            note = f"QB rebalance: {QB_PROFILES[identity]['note']}"
            target_ratings = QB_PROFILES[identity]["ratings"]
            for key, value in target_ratings.items():
                if existing.get(key) != value:
                    upsert_rating(conn, player["player_id"], key, int(value), note)
                    rating_updates += 1
        else:
            note = "QB rebalance: default compression to reduce inflated hidden QB curve."
            for key, old_value in existing.items():
                value = compressed_value(key, old_value, old_score)
                if value != old_value:
                    upsert_rating(conn, player["player_id"], key, value, note)
                    rating_updates += 1

        recalculate_role_scores(conn, player["player_id"], weights)

    return manual_profiles, rating_updates


def main() -> None:
    parser = argparse.ArgumentParser(description="Tune QB ratings in the new rating system only.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Path to nfl_gm.db")
    parser.add_argument("--apply", action="store_true", help="Persist changes. Omit for dry run.")
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        print_top("Before:", preview_top_qbs(conn, 20))
        manual_profiles, rating_updates = apply_rebalance(conn)
        print()
        print(f"Manual QB profiles applied: {manual_profiles}")
        print(f"Rating values changed: {rating_updates}")
        print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
        print()
        print_top("After:" if args.apply else "Dry-run after:", preview_top_qbs(conn, 20))
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
