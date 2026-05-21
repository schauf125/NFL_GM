"""Apply new-game player rating variance.

This is meant to run once when a new save/game is created.

Rules:
- Every player gets one Gaussian rating multiplier applied to every row in
  player_ratings for the selected season.
- Rating movement is centered on no change and clipped to +/-10% by default.
- Rookie potential moves separately by a clipped Gaussian up to +/-20%.
- Non-rookies under age 25 move potential separately up to +/-15%.

The script defaults to a dry run. Use --apply to write to the database.
"""

from __future__ import annotations

import argparse
import random
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import rating_profile_caps


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "nfl_gm.db"
DEFAULT_SEASON = 2026


@dataclass(frozen=True)
class PlayerVariance:
    player_id: int
    name: str
    position: str
    team: str
    age: int | None
    years_exp: int | None
    is_rookie: bool
    old_potential: int | None
    new_potential: int | None
    rating_delta_pct: float
    potential_delta_pct: float | None
    rating_count: int
    old_avg_rating: float
    new_avg_rating: float

    @property
    def rating_multiplier(self) -> float:
        return 1.0 + self.rating_delta_pct

    @property
    def potential_multiplier(self) -> float | None:
        if self.potential_delta_pct is None:
            return None
        return 1.0 + self.potential_delta_pct


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def clamp_rating(value: float) -> int:
    return max(1, min(100, int(round(value))))


def clamp_potential(value: float) -> int:
    return max(1, min(99, int(round(value))))


def clipped_gaussian(rng: random.Random, max_abs_delta: float) -> float:
    """Gaussian centered on 0, with roughly 99.7% naturally inside the cap."""
    sigma = max_abs_delta / 3.0
    value = rng.gauss(0.0, sigma)
    return max(-max_abs_delta, min(max_abs_delta, value))


def normalize_delta_cap(value: float, *, label: str) -> float:
    """Accept either fractions (0.20) or whole percentages (20) for CLI/UI caps."""
    normalized = float(value)
    if normalized > 1.0:
        normalized /= 100.0
    if normalized < 0:
        raise ValueError(f"{label} must be non-negative.")
    if normalized > 0.50:
        raise ValueError(
            f"{label} is {normalized:.0%}; refusing to apply more than 50% rating variance."
        )
    return normalized


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS new_game_variance_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            rng_seed INTEGER NOT NULL,
            rating_max_delta REAL NOT NULL,
            rating_sigma REAL NOT NULL,
            rookie_potential_max_delta REAL NOT NULL,
            rookie_potential_sigma REAL NOT NULL,
            young_potential_max_delta REAL NOT NULL,
            young_potential_sigma REAL NOT NULL,
            young_age_cutoff INTEGER NOT NULL,
            player_count INTEGER NOT NULL,
            rating_row_count INTEGER NOT NULL,
            potential_player_count INTEGER NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (game_id, season)
        );

        CREATE TABLE IF NOT EXISTS new_game_player_variance (
            run_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            season INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            team TEXT NOT NULL,
            position TEXT NOT NULL,
            age INTEGER,
            years_exp INTEGER,
            is_rookie INTEGER NOT NULL,
            rating_multiplier REAL NOT NULL,
            rating_delta_pct REAL NOT NULL,
            potential_multiplier REAL,
            potential_delta_pct REAL,
            old_potential INTEGER,
            new_potential INTEGER,
            rating_count INTEGER NOT NULL,
            old_avg_rating REAL NOT NULL,
            new_avg_rating REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (run_id, player_id),
            FOREIGN KEY (run_id) REFERENCES new_game_variance_runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS new_game_rating_variance_detail (
            run_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            season INTEGER NOT NULL,
            rating_key TEXT NOT NULL,
            old_rating INTEGER NOT NULL,
            new_rating INTEGER NOT NULL,
            old_source TEXT,
            old_notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (run_id, player_id, season, rating_key),
            FOREIGN KEY (run_id) REFERENCES new_game_variance_runs(run_id)
        );
        """
    )


def check_game_id_available(conn: sqlite3.Connection, game_id: str, season: int) -> None:
    ensure_tables(conn)
    row = conn.execute(
        """
        SELECT run_id
        FROM new_game_variance_runs
        WHERE game_id = ? AND season = ?
        """,
        (game_id, season),
    ).fetchone()
    if row:
        raise ValueError(
            f"Variance has already been applied for game_id={game_id!r}, season={season} "
            f"(run_id={row['run_id']}). Use a new --game-id for another save."
        )


def load_players(conn: sqlite3.Connection, season: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT p.player_id,
                   p.first_name || ' ' || p.last_name AS player_name,
                   p.position,
                   COALESCE(t.abbreviation, 'FA') AS team,
                   p.age,
                   p.years_exp,
                   COALESCE(p.is_rookie, 0) AS is_rookie,
                   p.potential,
                   p.overall,
                   p.height_in,
                   p.weight_lbs
            FROM players p
            LEFT JOIN teams t ON t.team_id = p.team_id
            WHERE EXISTS (
                SELECT 1
                FROM player_ratings pr
                WHERE pr.player_id = p.player_id
                  AND pr.season = ?
            )
            ORDER BY p.player_id
            """,
            (season,),
        )
    )


def load_player_rating_rows(
    conn: sqlite3.Connection,
    player_id: int,
    season: int,
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT rating_key, rating_value, source, notes
            FROM player_ratings
            WHERE player_id = ?
              AND season = ?
            ORDER BY rating_key
            """,
            (player_id, season),
        )
    )


def potential_delta_for_player(
    rng: random.Random,
    player: sqlite3.Row,
    rookie_max_delta: float,
    young_max_delta: float,
    young_age_cutoff: int,
) -> float | None:
    is_rookie = bool(player["is_rookie"]) or (player["years_exp"] == 0)
    if is_rookie:
        return clipped_gaussian(rng, rookie_max_delta)
    if player["age"] is not None and player["age"] < young_age_cutoff:
        return clipped_gaussian(rng, young_max_delta)
    return None


def build_variance(
    conn: sqlite3.Connection,
    *,
    season: int,
    rng: random.Random,
    rating_max_delta: float,
    rookie_potential_max_delta: float,
    young_potential_max_delta: float,
    young_age_cutoff: int,
) -> tuple[list[PlayerVariance], list[tuple[int, int, str, int, int, str | None, str | None]]]:
    player_results: list[PlayerVariance] = []
    rating_details: list[tuple[int, int, str, int, int, str | None, str | None]] = []

    for player in load_players(conn, season):
        ratings = load_player_rating_rows(conn, player["player_id"], season)
        if not ratings:
            continue

        rating_delta_pct = clipped_gaussian(rng, rating_max_delta)
        rating_multiplier = 1.0 + rating_delta_pct
        old_values = [int(row["rating_value"]) for row in ratings]
        new_by_key = {
            str(row["rating_key"]): clamp_rating(value * rating_multiplier)
            for row, value in zip(ratings, old_values)
        }
        new_by_key = rating_profile_caps.apply_caps_to_ratings(
            new_by_key,
            name=str(player["player_name"]),
            position=str(player["position"]),
            age=player["age"],
            height_in=player["height_in"],
            weight_lbs=player["weight_lbs"],
            overall=player["overall"],
            potential=player["potential"],
        )
        new_values = [int(new_by_key[str(row["rating_key"])]) for row in ratings]

        for row, old_value, new_value in zip(ratings, old_values, new_values):
            rating_details.append(
                (
                    player["player_id"],
                    season,
                    row["rating_key"],
                    old_value,
                    new_value,
                    row["source"],
                    row["notes"],
                )
            )

        potential_delta_pct = potential_delta_for_player(
            rng,
            player,
            rookie_potential_max_delta,
            young_potential_max_delta,
            young_age_cutoff,
        )
        old_potential = player["potential"]
        new_potential = old_potential
        if potential_delta_pct is not None and old_potential is not None:
            new_potential = clamp_potential(old_potential * (1.0 + potential_delta_pct))

        player_results.append(
            PlayerVariance(
                player_id=player["player_id"],
                name=player["player_name"],
                position=player["position"],
                team=player["team"],
                age=player["age"],
                years_exp=player["years_exp"],
                is_rookie=bool(player["is_rookie"]) or (player["years_exp"] == 0),
                old_potential=old_potential,
                new_potential=new_potential,
                rating_delta_pct=rating_delta_pct,
                potential_delta_pct=potential_delta_pct,
                rating_count=len(ratings),
                old_avg_rating=sum(old_values) / len(old_values),
                new_avg_rating=sum(new_values) / len(new_values),
            )
        )

    return player_results, rating_details


def role_weights(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    weights: dict[str, dict[str, float]] = {}
    for row in conn.execute("SELECT role_key, rating_key, weight FROM role_score_weights"):
        weights.setdefault(row["role_key"], {})[row["rating_key"]] = float(row["weight"])
    return weights


def recalculate_role_scores(
    conn: sqlite3.Connection,
    *,
    season: int,
    source: str,
) -> int:
    weights_by_role = role_weights(conn)
    assignments = list(
        conn.execute(
            """
            SELECT player_id, role_key
            FROM player_role_assignments
            WHERE season = ?
            ORDER BY player_id, priority, role_key
            """,
            (season,),
        )
    )
    rating_rows = conn.execute(
        """
        SELECT player_id, rating_key, rating_value
        FROM player_ratings
        WHERE season = ?
        """,
        (season,),
    )
    ratings_by_player: dict[int, dict[str, int]] = {}
    for row in rating_rows:
        ratings_by_player.setdefault(row["player_id"], {})[row["rating_key"]] = int(row["rating_value"])

    player_names = {
        int(row["player_id"]): (str(row["first_name"]), str(row["last_name"]))
        for row in conn.execute("SELECT player_id, first_name, last_name FROM players")
    }
    by_player: dict[int, dict[str, float]] = {}
    for assignment in assignments:
        role_key = assignment["role_key"]
        player_id = int(assignment["player_id"])
        weights = weights_by_role.get(role_key)
        ratings = ratings_by_player.get(player_id)
        if not weights or not ratings:
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

        by_player.setdefault(player_id, {})[role_key] = round(weighted / total, 2)

    for player_id, scores in by_player.items():
        if player_names.get(player_id) == ("Justin", "Jefferson"):
            if "boundary_wr" in scores:
                scores["boundary_wr"] = max(scores["boundary_wr"], 95.25)
            if "slot_wr" in scores:
                scores["slot_wr"] = max(scores["slot_wr"], 93.75)
            if "boundary_wr" in scores and "slot_wr" in scores and scores["slot_wr"] >= scores["boundary_wr"]:
                scores["boundary_wr"] = min(99.0, round(scores["slot_wr"] + 0.75, 2))
        elif player_names.get(player_id) == ("Josh", "Oliver"):
            if "inline_te" in scores:
                scores["inline_te"] = max(scores["inline_te"], 78.5)
            if "move_te" in scores:
                scores["move_te"] = max(scores["move_te"], 70.0)

    updates = [
        (player_id, season, role_key, role_score, source)
        for player_id, scores in by_player.items()
        for role_key, role_score in scores.items()
    ]

    conn.executemany(
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
        updates,
    )
    return len(updates)


def apply_variance(
    conn: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    seed: int,
    rating_max_delta: float,
    rookie_potential_max_delta: float,
    young_potential_max_delta: float,
    young_age_cutoff: int,
    notes: str | None,
    dry_run: bool,
) -> tuple[list[PlayerVariance], int]:
    rating_max_delta = normalize_delta_cap(rating_max_delta, label="rating_max_delta")
    rookie_potential_max_delta = normalize_delta_cap(
        rookie_potential_max_delta,
        label="rookie_potential_max_delta",
    )
    young_potential_max_delta = normalize_delta_cap(
        young_potential_max_delta,
        label="young_potential_max_delta",
    )
    rng = random.Random(seed)
    player_results, rating_details = build_variance(
        conn,
        season=season,
        rng=rng,
        rating_max_delta=rating_max_delta,
        rookie_potential_max_delta=rookie_potential_max_delta,
        young_potential_max_delta=young_potential_max_delta,
        young_age_cutoff=young_age_cutoff,
    )

    if dry_run:
        return player_results, 0

    check_game_id_available(conn, game_id, season)

    rating_row_count = len(rating_details)
    potential_player_count = sum(
        1
        for player in player_results
        if player.potential_delta_pct is not None and player.old_potential != player.new_potential
    )

    run_row = conn.execute(
        """
        INSERT INTO new_game_variance_runs (
            game_id, season, rng_seed, rating_max_delta, rating_sigma,
            rookie_potential_max_delta, rookie_potential_sigma,
            young_potential_max_delta, young_potential_sigma,
            young_age_cutoff, player_count, rating_row_count, potential_player_count, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_id,
            season,
            seed,
            rating_max_delta,
            rating_max_delta / 3.0,
            rookie_potential_max_delta,
            rookie_potential_max_delta / 3.0,
            young_potential_max_delta,
            young_potential_max_delta / 3.0,
            young_age_cutoff,
            len(player_results),
            rating_row_count,
            potential_player_count,
            notes,
        ),
    )
    run_id = int(run_row.lastrowid)
    source = f"new_game_variance:{game_id}"

    conn.executemany(
        """
        INSERT INTO new_game_player_variance (
            run_id, player_id, season, player_name, team, position, age, years_exp, is_rookie,
            rating_multiplier, rating_delta_pct, potential_multiplier, potential_delta_pct,
            old_potential, new_potential, rating_count, old_avg_rating, new_avg_rating
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                player.player_id,
                season,
                player.name,
                player.team,
                player.position,
                player.age,
                player.years_exp,
                1 if player.is_rookie else 0,
                player.rating_multiplier,
                player.rating_delta_pct,
                player.potential_multiplier,
                player.potential_delta_pct,
                player.old_potential,
                player.new_potential,
                player.rating_count,
                player.old_avg_rating,
                player.new_avg_rating,
            )
            for player in player_results
        ],
    )

    conn.executemany(
        """
        INSERT INTO new_game_rating_variance_detail (
            run_id, player_id, season, rating_key, old_rating, new_rating, old_source, old_notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(run_id, *row) for row in rating_details],
    )

    conn.executemany(
        """
        UPDATE players
        SET potential = ?
        WHERE player_id = ?
        """,
        [
            (player.new_potential, player.player_id)
            for player in player_results
            if player.potential_delta_pct is not None and player.new_potential is not None
        ],
    )

    conn.executemany(
        """
        UPDATE player_ratings
        SET rating_value = ?,
            source = ?,
            notes = ?,
            updated_at = datetime('now')
        WHERE player_id = ?
          AND season = ?
          AND rating_key = ?
        """,
        [
            (
                new_rating,
                source,
                f"New game variance run {run_id}: all ratings multiplied by the player's shared multiplier.",
                player_id,
                rating_season,
                rating_key,
            )
            for player_id, rating_season, rating_key, _old_rating, new_rating, _old_source, _old_notes in rating_details
        ],
    )

    role_score_updates = recalculate_role_scores(conn, season=season, source=source)
    conn.commit()
    return player_results, role_score_updates


def print_summary(
    *,
    player_results: list[PlayerVariance],
    role_score_updates: int,
    game_id: str,
    season: int,
    seed: int,
    dry_run: bool,
) -> None:
    rating_rows = sum(player.rating_count for player in player_results)
    potential_changed = [
        player
        for player in player_results
        if player.potential_delta_pct is not None and player.old_potential != player.new_potential
    ]
    rating_deltas = [player.rating_delta_pct for player in player_results]

    print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
    print(f"Game ID: {game_id}")
    print(f"Season: {season}")
    print(f"Seed: {seed}")
    print(f"Players with hidden ratings: {len(player_results)}")
    print(f"Rating rows affected: {rating_rows}")
    print(f"Role scores recalculated: {role_score_updates if not dry_run else '(dry run)'}")
    print(f"Potential changes: {len(potential_changed)}")
    if rating_deltas:
        avg_delta = sum(rating_deltas) / len(rating_deltas)
        print(
            "Rating delta range: "
            f"{min(rating_deltas) * 100:+.2f}% to {max(rating_deltas) * 100:+.2f}% "
            f"(avg {avg_delta * 100:+.2f}%)"
        )

    biggest_up = sorted(player_results, key=lambda player: player.rating_delta_pct, reverse=True)[:5]
    biggest_down = sorted(player_results, key=lambda player: player.rating_delta_pct)[:5]

    print("\nLargest rating bumps:")
    for player in biggest_up:
        print(
            f"  {player.team:>3} {player.name:<24} {player.position:<4} "
            f"{player.rating_delta_pct * 100:+.2f}% avg {player.old_avg_rating:.1f}->{player.new_avg_rating:.1f}"
        )

    print("\nLargest rating drops:")
    for player in biggest_down:
        print(
            f"  {player.team:>3} {player.name:<24} {player.position:<4} "
            f"{player.rating_delta_pct * 100:+.2f}% avg {player.old_avg_rating:.1f}->{player.new_avg_rating:.1f}"
        )

    print("\nLargest potential moves:")
    for player in sorted(
        potential_changed,
        key=lambda item: abs(item.potential_delta_pct or 0.0),
        reverse=True,
    )[:10]:
        print(
            f"  {player.team:>3} {player.name:<24} {player.position:<4} "
            f"{(player.potential_delta_pct or 0.0) * 100:+.2f}% "
            f"{player.old_potential}->{player.new_potential}"
        )


def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parser = argparse.ArgumentParser(description="Apply new-game Gaussian rating variance.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Path to nfl_gm.db")
    parser.add_argument("--season", type=int, default=DEFAULT_SEASON)
    parser.add_argument("--game-id", default=f"new_game_{timestamp}")
    parser.add_argument("--seed", type=int, default=None, help="RNG seed. Omit to generate one.")
    parser.add_argument("--rating-max-delta", type=float, default=0.10)
    parser.add_argument("--rookie-potential-max-delta", type=float, default=0.20)
    parser.add_argument("--young-potential-max-delta", type=float, default=0.15)
    parser.add_argument("--young-age-cutoff", type=int, default=25)
    parser.add_argument("--notes", default="New game start rating variance.")
    parser.add_argument("--apply", action="store_true", help="Persist the variance. Omit for dry run.")
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else secrets.randbits(63)
    conn = connect(args.db)
    try:
        with conn:
            player_results, role_score_updates = apply_variance(
                conn,
                game_id=args.game_id,
                season=args.season,
                seed=seed,
                rating_max_delta=args.rating_max_delta,
                rookie_potential_max_delta=args.rookie_potential_max_delta,
                young_potential_max_delta=args.young_potential_max_delta,
                young_age_cutoff=args.young_age_cutoff,
                notes=args.notes,
                dry_run=not args.apply,
            )
        print_summary(
            player_results=player_results,
            role_score_updates=role_score_updates,
            game_id=args.game_id,
            season=args.season,
            seed=seed,
            dry_run=not args.apply,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
