#!/usr/bin/env python3
"""Save-scoped fog of war for pro player evaluation.

The sim engine keeps using true player ratings. This module owns what a team's
staff thinks it knows about young players, so UI and future GM logic can show a
scouted read that sharpens with age, snaps, and events.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from typing import Any


SOURCE = "pro_player_fog"
FA_EVALUATOR_TEAM_ID = 0
_TEAM_EVALUATION_CACHE: dict[tuple[int, str, int, int, int], dict[str, Any] | None] = {}

CONFIDENCE_ACCURACY = {
    "Unscouted": 0.18,
    "Low": 0.28,
    "Medium": 0.46,
    "High": 0.66,
    "Very High": 0.80,
}

SPECIAL_TEAMS_SNAP_WEIGHT = 0.125
PRESEASON_SNAP_WEIGHT = 0.25


@dataclass(frozen=True)
class EvaluationRead:
    player_id: int
    evaluator_team_id: int
    season: int
    perceived_overall: int
    perceived_potential: int
    overall_accuracy: float
    potential_accuracy: float
    reveal_age: int
    confidence_label: str
    confidence_note: str
    source: str


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    ).fetchone() is not None


def row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        if key in row.keys():
            return row[key]
    except AttributeError:
        if isinstance(row, dict):
            return row.get(key, default)
    except Exception:
        pass
    return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def clamp_int(value: float, low: int = 1, high: int = 99) -> int:
    return max(low, min(high, int(round(value))))


def team_key(team_id: int | None) -> int:
    return int(team_id) if team_id is not None else FA_EVALUATOR_TEAM_ID


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS player_evaluation_reports (
            game_id TEXT NOT NULL,
            evaluator_team_id INTEGER NOT NULL DEFAULT 0,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            reveal_age INTEGER NOT NULL,
            overall_accuracy REAL NOT NULL DEFAULT 0.35,
            potential_accuracy REAL NOT NULL DEFAULT 0.25,
            perceived_overall INTEGER NOT NULL,
            perceived_potential INTEGER NOT NULL,
            confidence_label TEXT NOT NULL DEFAULT 'Cloudy',
            confidence_note TEXT,
            last_event_date TEXT,
            source TEXT NOT NULL DEFAULT 'pro_player_fog',
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (game_id, evaluator_team_id, player_id)
        );

        CREATE INDEX IF NOT EXISTS idx_player_evaluation_reports_player
            ON player_evaluation_reports(game_id, player_id, evaluator_team_id);

        CREATE TABLE IF NOT EXISTS player_evaluation_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            evaluator_team_id INTEGER NOT NULL DEFAULT 0,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            event_date TEXT,
            event_type TEXT NOT NULL,
            overall_accuracy_delta REAL NOT NULL DEFAULT 0,
            potential_accuracy_delta REAL NOT NULL DEFAULT 0,
            snap_count REAL NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'pro_player_fog',
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_player_evaluation_events_player
            ON player_evaluation_events(game_id, player_id, season, event_date);
        """
    )


def active_game_id(con: sqlite3.Connection) -> str:
    if table_exists(con, "active_game_save_view"):
        row = con.execute("SELECT game_id FROM active_game_save_view LIMIT 1").fetchone()
        if row and row["game_id"]:
            return str(row["game_id"])
    if table_exists(con, "game_settings"):
        row = con.execute(
            "SELECT setting_value FROM game_settings WHERE setting_key = 'active_game_id'"
        ).fetchone()
        if row and row["setting_value"]:
            return str(row["setting_value"])
    return "default"


def active_user_team_id(con: sqlite3.Connection) -> int | None:
    if table_exists(con, "active_game_save_view"):
        row = con.execute("SELECT user_team_id FROM active_game_save_view LIMIT 1").fetchone()
        if row and row["user_team_id"] is not None:
            return int(row["user_team_id"])
    return None


def normalized_confidence(value: str | None) -> str:
    text = str(value or "Low").strip()
    return text if text in CONFIDENCE_ACCURACY else "Low"


def confidence_label(accuracy: float) -> str:
    if accuracy >= 0.92:
        return "Firm"
    if accuracy >= 0.78:
        return "Strong"
    if accuracy >= 0.62:
        return "Solid"
    if accuracy >= 0.45:
        return "Developing"
    return "Cloudy"


def confidence_note(label: str) -> str:
    return {
        "Firm": "Staff has a mature read.",
        "Strong": "Most of the picture is clear.",
        "Solid": "Useful read with some projection left.",
        "Developing": "Early pro read, still moving.",
        "Cloudy": "Limited pro evidence.",
    }.get(label, "Staff read is still forming.")


def reveal_age_for(
    position: str | None,
    *,
    game_id: str,
    player_id: int,
    evaluator_team_id: int,
    years_exp: int | None = None,
) -> int:
    pos = str(position or "").upper()
    rng = random.Random(f"{game_id}:{evaluator_team_id}:{player_id}:pro-fog:reveal-age")
    years = max(0, int(years_exp or 0))
    if pos == "QB":
        age = rng.gauss(29.8, 2.15)
        if years <= 2:
            age += rng.uniform(0.5, 2.4)
        elif years <= 4:
            age += rng.uniform(0.0, 1.1)
        tail = rng.random()
        if tail < 0.035:
            age -= rng.uniform(2.0, 3.4)
        elif tail > 0.955:
            age += rng.uniform(2.4, 5.0)
        return max(24, min(36, int(round(age))))
    if pos in {"K", "P", "LS"}:
        age = rng.gauss(27.0, 1.45)
        tail = rng.random()
        if tail < 0.025:
            age -= rng.uniform(1.2, 2.3)
        elif tail > 0.965:
            age += rng.uniform(1.8, 3.2)
        return max(24, min(32, int(round(age))))
    age = rng.gauss(26.3, 1.15)
    tail = rng.random()
    if tail < 0.03:
        age -= rng.uniform(1.6, 2.8)
    elif tail > 0.96:
        age += rng.uniform(1.8, 3.4)
    return max(23, min(31, int(round(age))))


def accuracy_caps_for_evidence(
    position: str | None,
    *,
    age: int,
    years_exp: int,
    reveal_age: int,
    effective_snaps: float = 0.0,
    primary_play_snaps: float = 0.0,
) -> tuple[float, float]:
    """Upper bounds for staff certainty when the player has limited pro evidence."""
    pos = str(position or "").upper()
    years = max(0, int(years_exp or 0))
    age = int(age or 26)
    effective = max(0.0, float(effective_snaps or 0.0))
    primary = max(0.0, float(primary_play_snaps or 0.0))

    if pos == "QB":
        if age >= reveal_age and years >= 5 and primary >= 950:
            return 0.98, 0.92
        if primary >= 950 and years >= 3:
            return 0.88, 0.72
        if primary >= 560 and years >= 2:
            return 0.80, 0.62
        if primary >= 240:
            return 0.72, 0.52
        return 0.66, 0.46

    if pos in {"K", "P", "LS"}:
        if years >= 4 or age >= reveal_age:
            return 0.96, 0.88
        if effective >= 130:
            return 0.86, 0.72
        return 0.76, 0.60

    if age >= reveal_age and years >= 4 and effective >= 700:
        return 0.98, 0.92
    if years <= 1:
        if effective >= 520:
            return 0.82, 0.68
        if effective >= 250:
            return 0.76, 0.62
        return 0.68, 0.52
    if years <= 2:
        if effective >= 720:
            return 0.88, 0.76
        if effective >= 420:
            return 0.82, 0.68
        if effective >= 220:
            return 0.76, 0.62
        return 0.74, 0.58
    if years <= 3:
        if effective >= 850:
            return 0.91, 0.82
        if effective >= 500:
            return 0.88, 0.76
        return 0.80, 0.66
    if age < reveal_age and effective < 500:
        return 0.88, 0.78
    return 0.96, 0.88


def apply_accuracy_caps(
    position: str | None,
    overall_accuracy: float,
    potential_accuracy: float,
    *,
    age: int,
    years_exp: int,
    reveal_age: int,
    effective_snaps: float = 0.0,
    primary_play_snaps: float = 0.0,
) -> tuple[float, float]:
    overall_cap, potential_cap = accuracy_caps_for_evidence(
        position,
        age=age,
        years_exp=years_exp,
        reveal_age=reveal_age,
        effective_snaps=effective_snaps,
        primary_play_snaps=primary_play_snaps,
    )
    return min(overall_accuracy, overall_cap), min(potential_accuracy, potential_cap)


def baseline_accuracy(
    player: sqlite3.Row | dict[str, Any],
    *,
    game_id: str,
    evaluator_team_id: int,
) -> tuple[float, float, int]:
    player_id = int(row_value(player, "player_id", 0) or 0)
    position = str(row_value(player, "position", "") or "")
    age = int(row_value(player, "age", 26) or 26)
    years = int(row_value(player, "years_exp", 0) or 0)
    rookie = int(row_value(player, "is_rookie", 0) or 0) == 1
    reveal_age = reveal_age_for(
        position,
        game_id=game_id,
        player_id=player_id,
        evaluator_team_id=evaluator_team_id,
        years_exp=years,
    )
    pos = position.upper()
    if age >= reveal_age:
        if pos == "QB" and years <= 2:
            overall_accuracy, potential_accuracy = 0.66, 0.46
        elif pos == "QB" and years <= 4:
            overall_accuracy, potential_accuracy = 0.78, 0.58
        else:
            overall_accuracy, potential_accuracy = 0.97, 0.93 if pos != "QB" else 0.90
        overall_accuracy, potential_accuracy = apply_accuracy_caps(
            position,
            overall_accuracy,
            potential_accuracy,
            age=age,
            years_exp=years,
            reveal_age=reveal_age,
        )
        return overall_accuracy, potential_accuracy, reveal_age

    if pos == "QB":
        base = 0.22 + min(0.34, years * 0.085)
        age_track = clamp((age - 22) / max(1, reveal_age - 22), 0.0, 1.0) * 0.34
        overall_accuracy = base + age_track
        potential_accuracy = overall_accuracy - 0.16
    else:
        base = 0.28 if rookie or years <= 0 else 0.34 + min(0.33, years * 0.105)
        age_track = clamp((age - 21) / max(1, reveal_age - 21), 0.0, 1.0) * 0.32
        overall_accuracy = base + age_track
        potential_accuracy = overall_accuracy - 0.10
    overall_accuracy, potential_accuracy = apply_accuracy_caps(
        position,
        overall_accuracy,
        potential_accuracy,
        age=age,
        years_exp=years,
        reveal_age=reveal_age,
    )
    return clamp(overall_accuracy, 0.18, 0.95), clamp(potential_accuracy, 0.14, 0.90), reveal_age


def perception_noise(
    *,
    game_id: str,
    evaluator_team_id: int,
    player_id: int,
    key: str,
    accuracy: float,
    potential: bool = False,
) -> float:
    rng = random.Random(f"{game_id}:{evaluator_team_id}:{player_id}:pro-fog:{key}")
    sigma = (1.0 - clamp(accuracy, 0.0, 1.0)) * (11.0 if potential else 7.2) + (0.45 if potential else 0.30)
    limit = max(1.0, sigma * 2.1)
    return clamp(rng.gauss(0.0, sigma), -limit, limit)


def perceived_from_true(
    true_value: int | float | None,
    *,
    game_id: str,
    evaluator_team_id: int,
    player_id: int,
    key: str,
    accuracy: float,
    potential: bool = False,
) -> int:
    true = float(true_value or 50)
    return clamp_int(
        true
        + perception_noise(
            game_id=game_id,
            evaluator_team_id=evaluator_team_id,
            player_id=player_id,
            key=key,
            accuracy=accuracy,
            potential=potential,
        )
    )


def perceived_from_scouted(
    true_value: int | float | None,
    scouted_value: int | float | None,
    *,
    game_id: str,
    evaluator_team_id: int,
    player_id: int,
    key: str,
    accuracy: float,
    potential: bool = False,
) -> int:
    true = float(true_value or 50)
    scouted = float(scouted_value if scouted_value is not None else true)
    # Keep rookie evaluations anchored to the draft-room read at first. High
    # confidence still bends toward truth; low confidence can remain badly wrong.
    truth_pull = clamp((accuracy - 0.24) / 0.76, 0.0, 1.0) * (0.42 if potential else 0.50)
    base = scouted + ((true - scouted) * truth_pull)
    return clamp_int(
        base
        + perception_noise(
            game_id=game_id,
            evaluator_team_id=evaluator_team_id,
            player_id=player_id,
            key=f"draft-{key}",
            accuracy=accuracy,
            potential=potential,
        )
    )


def player_row(con: sqlite3.Connection, player_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM players WHERE player_id = ?", (int(player_id),)).fetchone()


def upsert_evaluation(
    con: sqlite3.Connection,
    *,
    game_id: str,
    evaluator_team_id: int,
    player_id: int,
    season: int,
    reveal_age: int,
    overall_accuracy: float,
    potential_accuracy: float,
    perceived_overall: int,
    perceived_potential: int,
    source: str,
    notes: str | None = None,
    event_date: str | None = None,
) -> None:
    _TEAM_EVALUATION_CACHE.clear()
    perceived_overall = int(perceived_overall)
    perceived_potential = max(int(perceived_potential), perceived_overall)
    label = confidence_label(min(overall_accuracy, potential_accuracy + 0.08))
    con.execute(
        """
        INSERT INTO player_evaluation_reports (
            game_id, evaluator_team_id, player_id, season, reveal_age,
            overall_accuracy, potential_accuracy, perceived_overall,
            perceived_potential, confidence_label, confidence_note,
            last_event_date, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(game_id, evaluator_team_id, player_id) DO UPDATE SET
            season = excluded.season,
            reveal_age = excluded.reveal_age,
            overall_accuracy = excluded.overall_accuracy,
            potential_accuracy = excluded.potential_accuracy,
            perceived_overall = excluded.perceived_overall,
            perceived_potential = excluded.perceived_potential,
            confidence_label = excluded.confidence_label,
            confidence_note = excluded.confidence_note,
            last_event_date = COALESCE(excluded.last_event_date, player_evaluation_reports.last_event_date),
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            game_id,
            evaluator_team_id,
            player_id,
            season,
            reveal_age,
            round(clamp(overall_accuracy, 0.0, 1.0), 4),
            round(clamp(potential_accuracy, 0.0, 1.0), 4),
            perceived_overall,
            perceived_potential,
            label,
            confidence_note(label),
            event_date,
            source,
            notes,
        ),
    )


def create_initial_evaluation(
    con: sqlite3.Connection,
    *,
    game_id: str,
    evaluator_team_id: int,
    player_id: int,
    season: int,
    source: str = SOURCE,
    notes: str | None = None,
) -> bool:
    ensure_schema(con)
    player = player_row(con, player_id)
    if not player:
        return False
    overall_accuracy, potential_accuracy, reveal_age = baseline_accuracy(
        player,
        game_id=game_id,
        evaluator_team_id=evaluator_team_id,
    )
    upsert_evaluation(
        con,
        game_id=game_id,
        evaluator_team_id=evaluator_team_id,
        player_id=player_id,
        season=season,
        reveal_age=reveal_age,
        overall_accuracy=overall_accuracy,
        potential_accuracy=potential_accuracy,
        perceived_overall=perceived_from_true(
            player["overall"],
            game_id=game_id,
            evaluator_team_id=evaluator_team_id,
            player_id=player_id,
            key="overall",
            accuracy=overall_accuracy,
        ),
        perceived_potential=perceived_from_true(
            player["potential"],
            game_id=game_id,
            evaluator_team_id=evaluator_team_id,
            player_id=player_id,
            key="potential",
            accuracy=potential_accuracy,
            potential=True,
        ),
        source=source,
        notes=notes or "Initial pro evaluation seeded from age, experience, and staff uncertainty.",
    )
    return True


def seed_existing_player_evaluations(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
) -> int:
    ensure_schema(con)
    rows = con.execute(
        """
        SELECT p.*
        FROM players p
        LEFT JOIN player_evaluation_reports e
          ON e.game_id = ?
         AND e.evaluator_team_id = p.team_id
         AND e.player_id = p.player_id
        WHERE COALESCE(p.status, 'Active') != 'Retired'
          AND p.team_id IS NOT NULL
          AND e.player_id IS NULL
        """,
        (game_id,),
    ).fetchall()
    created = 0
    for player in rows:
        player_id = int(player["player_id"])
        evaluator = team_key(player["team_id"])
        overall_accuracy, potential_accuracy, reveal_age = baseline_accuracy(
            player,
            game_id=game_id,
            evaluator_team_id=evaluator,
        )
        upsert_evaluation(
            con,
            game_id=game_id,
            evaluator_team_id=evaluator,
            player_id=player_id,
            season=season,
            reveal_age=reveal_age,
            overall_accuracy=overall_accuracy,
            potential_accuracy=potential_accuracy,
            perceived_overall=perceived_from_true(
                player["overall"],
                game_id=game_id,
                evaluator_team_id=evaluator,
                player_id=player_id,
                key="overall",
                accuracy=overall_accuracy,
            ),
            perceived_potential=perceived_from_true(
                player["potential"],
                game_id=game_id,
                evaluator_team_id=evaluator,
                player_id=player_id,
                key="potential",
                accuracy=potential_accuracy,
                potential=True,
            ),
            source="new_game_pro_fog",
            notes="Initial pro evaluation seeded from age, experience, and staff uncertainty.",
        )
        created += 1
    return created


def draft_scouting_row(
    con: sqlite3.Connection,
    *,
    game_id: str,
    draft_year: int,
    team_id: int,
    prospect_id: int,
) -> sqlite3.Row | None:
    user_team = active_user_team_id(con)
    if user_team is not None and int(user_team) == int(team_id) and table_exists(con, "scouting_prospect_progress"):
        row = con.execute(
            """
            SELECT scouting_confidence, scouting_level
            FROM scouting_prospect_progress
            WHERE game_id = ? AND draft_year = ? AND prospect_id = ?
            """,
            (game_id, draft_year, prospect_id),
        ).fetchone()
        if row:
            return row
    if table_exists(con, "cpu_scouting_prospect_progress"):
        row = con.execute(
            """
            SELECT scouting_confidence, scouting_level
            FROM cpu_scouting_prospect_progress
            WHERE game_id = ? AND draft_year = ? AND team_id = ? AND prospect_id = ?
            """,
            (game_id, draft_year, team_id, prospect_id),
        ).fetchone()
        if row:
            return row
    return None


def seed_drafted_player_evaluation(
    con: sqlite3.Connection,
    *,
    game_id: str,
    player_id: int,
    team_id: int,
    season: int,
    prospect_id: int,
    draft_year: int,
) -> bool:
    ensure_schema(con)
    player = player_row(con, player_id)
    if not player:
        return False
    prospect = con.execute(
        "SELECT * FROM draft_prospects WHERE prospect_id = ?",
        (int(prospect_id),),
    ).fetchone()
    if not prospect:
        return create_initial_evaluation(
            con,
            game_id=game_id,
            evaluator_team_id=team_key(team_id),
            player_id=player_id,
            season=season,
            source="draft_selection_pro_fog",
        )
    scouting_row = draft_scouting_row(
        con,
        game_id=game_id,
        draft_year=draft_year,
        team_id=team_id,
        prospect_id=prospect_id,
    )
    confidence = normalized_confidence(row_value(scouting_row, "scouting_confidence", prospect["scout_confidence"]))
    level = int(row_value(scouting_row, "scouting_level", 0) or 0)
    level_bonus = min(0.08, max(0, level - 15) / 900.0)
    base_accuracy = CONFIDENCE_ACCURACY[confidence] + level_bonus
    evaluator = team_key(team_id)
    _baseline_overall, _baseline_potential, reveal_age = baseline_accuracy(
        player,
        game_id=game_id,
        evaluator_team_id=evaluator,
    )
    overall_accuracy = clamp(base_accuracy, 0.18, 0.88)
    potential_accuracy = clamp(base_accuracy - (0.10 if player["position"] != "QB" else 0.16), 0.14, 0.82)
    upsert_evaluation(
        con,
        game_id=game_id,
        evaluator_team_id=evaluator,
        player_id=player_id,
        season=season,
        reveal_age=reveal_age,
        overall_accuracy=overall_accuracy,
        potential_accuracy=potential_accuracy,
        perceived_overall=perceived_from_scouted(
            player["overall"],
            prospect["scout_grade"] if prospect["scout_grade"] is not None else prospect["overall"],
            game_id=game_id,
            evaluator_team_id=evaluator,
            player_id=player_id,
            key="overall",
            accuracy=overall_accuracy,
        ),
        perceived_potential=perceived_from_scouted(
            player["potential"],
            prospect["scout_ceiling"] if prospect["scout_ceiling"] is not None else prospect["potential"],
            game_id=game_id,
            evaluator_team_id=evaluator,
            player_id=player_id,
            key="potential",
            accuracy=potential_accuracy,
            potential=True,
        ),
        source="draft_selection_pro_fog",
        notes=f"Initial rookie read carried from draft scouting confidence: {confidence}.",
    )
    return True


def event_gain(signal_strength: float, snap_count: float, position: str | None) -> tuple[float, float]:
    signal = abs(float(signal_strength or 0.0))
    snaps = max(0.0, float(snap_count or 0.0))
    pos = str(position or "").upper()
    snap_gain = min(0.095, snaps / 950.0 * 0.095)
    signal_gain = min(0.045, signal * 0.045)
    base = 0.010 + snap_gain + signal_gain
    if pos == "QB":
        base *= 0.90
    return base, base * (0.48 if pos != "QB" else 0.40)


def apply_evaluation_event(
    con: sqlite3.Connection,
    *,
    game_id: str,
    player_id: int,
    team_id: int | None,
    season: int,
    event_date: str | None,
    event_type: str,
    signal_strength: float = 0.0,
    snap_count: float = 0.0,
    source: str = SOURCE,
    notes: str | None = None,
) -> bool:
    ensure_schema(con)
    evaluator = team_key(team_id)
    if not con.execute(
        """
        SELECT 1
        FROM player_evaluation_reports
        WHERE game_id = ? AND evaluator_team_id = ? AND player_id = ?
        """,
        (game_id, evaluator, player_id),
    ).fetchone():
        create_initial_evaluation(
            con,
            game_id=game_id,
            evaluator_team_id=evaluator,
            player_id=player_id,
            season=season,
            source=source,
        )
    current = con.execute(
        """
        SELECT e.*, p.position, p.age, p.overall, p.potential
        FROM player_evaluation_reports e
        JOIN players p ON p.player_id = e.player_id
        WHERE e.game_id = ? AND e.evaluator_team_id = ? AND e.player_id = ?
        """,
        (game_id, evaluator, player_id),
    ).fetchone()
    if not current:
        return False
    overall_gain, potential_gain = event_gain(signal_strength, snap_count, current["position"])
    if int(current["age"] or 0) >= int(current["reveal_age"] or 99):
        overall_gain = max(overall_gain, 0.10)
        potential_gain = max(potential_gain, 0.06)
    new_overall_accuracy = clamp(float(current["overall_accuracy"] or 0.0) + overall_gain, 0.0, 0.98)
    new_potential_accuracy = clamp(float(current["potential_accuracy"] or 0.0) + potential_gain, 0.0, 0.94)
    if int(current["age"] or 0) < int(current["reveal_age"] or 99):
        new_overall_accuracy = min(new_overall_accuracy, 0.91)
        new_potential_accuracy = min(new_potential_accuracy, 0.83)
    upsert_evaluation(
        con,
        game_id=game_id,
        evaluator_team_id=evaluator,
        player_id=player_id,
        season=season,
        reveal_age=int(current["reveal_age"]),
        overall_accuracy=new_overall_accuracy,
        potential_accuracy=new_potential_accuracy,
        perceived_overall=perceived_from_true(
            current["overall"],
            game_id=game_id,
            evaluator_team_id=evaluator,
            player_id=player_id,
            key="overall",
            accuracy=new_overall_accuracy,
        ),
        perceived_potential=perceived_from_true(
            current["potential"],
            game_id=game_id,
            evaluator_team_id=evaluator,
            player_id=player_id,
            key="potential",
            accuracy=new_potential_accuracy,
            potential=True,
        ),
        source=source,
        notes=notes or f"{event_type} adjusted staff confidence.",
        event_date=event_date,
    )
    con.execute(
        """
        INSERT INTO player_evaluation_events (
            game_id, evaluator_team_id, player_id, season, event_date, event_type,
            overall_accuracy_delta, potential_accuracy_delta, snap_count, source, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_id,
            evaluator,
            player_id,
            season,
            event_date,
            event_type,
            round(overall_gain, 4),
            round(potential_gain, 4),
            float(snap_count or 0.0),
            source,
            notes,
        ),
    )
    return True


def stat_value(stats: dict[str, float], key: str) -> float:
    return float(stats.get(key, 0.0) or 0.0)


def effective_snap_evidence_from_counts(
    offensive_snaps: float = 0.0,
    defensive_snaps: float = 0.0,
    special_teams_snaps: float = 0.0,
    *,
    preseason: bool = False,
) -> float:
    evidence = (
        max(0.0, float(offensive_snaps or 0.0))
        + max(0.0, float(defensive_snaps or 0.0))
        + max(0.0, float(special_teams_snaps or 0.0)) * SPECIAL_TEAMS_SNAP_WEIGHT
    )
    if preseason:
        evidence *= PRESEASON_SNAP_WEIGHT
    return evidence


def effective_snap_evidence(stats: dict[str, float], *, preseason: bool = False) -> float:
    return effective_snap_evidence_from_counts(
        stat_value(stats, "offensive_snaps"),
        stat_value(stats, "defensive_snaps"),
        stat_value(stats, "special_teams_snaps"),
        preseason=preseason,
    )


def season_snap_counts(con: sqlite3.Connection, season: int) -> dict[int, dict[str, float]]:
    stats: dict[int, dict[str, float]] = {}
    if table_exists(con, "season_player_stats"):
        try:
            rows = con.execute(
                """
                SELECT player_id, stat_key, SUM(stat_value) AS value
                FROM season_player_stats
                WHERE season = ?
                  AND stat_key IN ('offensive_snaps', 'defensive_snaps', 'special_teams_snaps', 'total_snaps')
                GROUP BY player_id, stat_key
                """,
                (season,),
            ).fetchall()
            for row in rows:
                stats.setdefault(int(row["player_id"]), {})[str(row["stat_key"])] = float(row["value"] or 0.0)
        except sqlite3.OperationalError:
            stats = {}
    if stats:
        return stats
    if table_exists(con, "game_player_stats") and table_exists(con, "game_sim_runs"):
        rows = con.execute(
            """
            SELECT gps.player_id, gps.stat_key, SUM(gps.stat_value) AS value
            FROM game_player_stats gps
            JOIN game_sim_runs gsr ON gsr.run_id = gps.run_id
            WHERE gsr.season = ?
              AND gsr.status = 'final'
              AND gps.stat_key IN ('offensive_snaps', 'defensive_snaps', 'special_teams_snaps', 'total_snaps')
            GROUP BY gps.player_id, gps.stat_key
            """,
            (season,),
        ).fetchall()
        for row in rows:
            stats.setdefault(int(row["player_id"]), {})[str(row["stat_key"])] = float(row["value"] or 0.0)
    return stats


def primary_snaps(position: str | None, stats: dict[str, float]) -> float:
    pos = str(position or "").upper()
    if pos in {"QB", "RB", "FB", "WR", "TE", "OT", "OG", "C"}:
        return stat_value(stats, "offensive_snaps")
    if pos in {"EDGE", "IDL", "LB", "CB", "NB", "S", "FS", "SS"}:
        return stat_value(stats, "defensive_snaps")
    return max(stat_value(stats, "offensive_snaps"), stat_value(stats, "defensive_snaps"))


def annual_gain(
    position: str | None,
    age: int,
    reveal_age: int,
    effective_snaps: float,
    primary_play_snaps: float,
) -> tuple[float, float]:
    pos = str(position or "").upper()
    base = 0.050 if pos != "QB" else 0.040
    snap_gain = min(0.17, max(0.0, effective_snaps) / (900.0 if pos == "QB" else 760.0) * 0.15)
    age_gain = 0.0
    if pos == "QB":
        play_exposure = clamp(max(0.0, primary_play_snaps) / 720.0, 0.0, 1.0)
        if age >= reveal_age:
            age_gain = 0.07 + 0.28 * play_exposure
        elif age == reveal_age - 1:
            age_gain = 0.035 + 0.105 * play_exposure
        elif age == reveal_age - 2:
            age_gain = 0.018 + 0.052 * play_exposure
    else:
        if age >= reveal_age:
            age_gain = 0.35
        elif age == reveal_age - 1:
            age_gain = 0.14
        elif age == reveal_age - 2:
            age_gain = 0.07
    overall = base + snap_gain + age_gain
    potential = (base * 0.58) + (snap_gain * (0.55 if pos != "QB" else 0.45)) + (age_gain * 0.65)
    return overall, potential


def advance_year_end_evaluations(
    con: sqlite3.Connection,
    *,
    game_id: str,
    from_season: int,
    to_season: int,
) -> int:
    ensure_schema(con)
    snap_map = season_snap_counts(con, from_season)
    players = con.execute(
        """
        SELECT player_id, team_id, position, age, years_exp, overall, potential
        FROM players
        WHERE COALESCE(status, 'Active') != 'Retired'
          AND team_id IS NOT NULL
        """
    ).fetchall()
    updated = 0
    for player in players:
        player_id = int(player["player_id"])
        evaluator = team_key(player["team_id"])
        if not con.execute(
            """
            SELECT 1
            FROM player_evaluation_reports
            WHERE game_id = ? AND evaluator_team_id = ? AND player_id = ?
            """,
            (game_id, evaluator, player_id),
        ).fetchone():
            create_initial_evaluation(
                con,
                game_id=game_id,
                evaluator_team_id=evaluator,
                player_id=player_id,
                season=to_season,
                source="year_end_pro_fog_seed",
            )
        current = con.execute(
            """
            SELECT *
            FROM player_evaluation_reports
            WHERE game_id = ? AND evaluator_team_id = ? AND player_id = ?
            """,
            (game_id, evaluator, player_id),
        ).fetchone()
        if not current:
            continue
        stats = snap_map.get(player_id, {})
        side_snaps = primary_snaps(player["position"], stats)
        effective_snaps = effective_snap_evidence(stats)
        overall_gain, potential_gain = annual_gain(
            player["position"],
            int(player["age"] or 26),
            int(current["reveal_age"] or reveal_age_for(
                player["position"],
                game_id=game_id,
                player_id=player_id,
                evaluator_team_id=evaluator,
                years_exp=int(player["years_exp"] or 0),
            )),
            effective_snaps,
            side_snaps,
        )
        new_overall_accuracy = clamp(float(current["overall_accuracy"] or 0.0) + overall_gain, 0.0, 0.98)
        new_potential_accuracy = clamp(float(current["potential_accuracy"] or 0.0) + potential_gain, 0.0, 0.94)
        new_overall_accuracy, new_potential_accuracy = apply_accuracy_caps(
            player["position"],
            new_overall_accuracy,
            new_potential_accuracy,
            age=int(player["age"] or 26),
            years_exp=int(player["years_exp"] or 0),
            reveal_age=int(current["reveal_age"]),
            effective_snaps=effective_snaps,
            primary_play_snaps=side_snaps,
        )
        upsert_evaluation(
            con,
            game_id=game_id,
            evaluator_team_id=evaluator,
            player_id=player_id,
            season=to_season,
            reveal_age=int(current["reveal_age"]),
            overall_accuracy=new_overall_accuracy,
            potential_accuracy=new_potential_accuracy,
            perceived_overall=perceived_from_true(
                player["overall"],
                game_id=game_id,
                evaluator_team_id=evaluator,
                player_id=player_id,
                key="overall",
                accuracy=new_overall_accuracy,
            ),
            perceived_potential=perceived_from_true(
                player["potential"],
                game_id=game_id,
                evaluator_team_id=evaluator,
                player_id=player_id,
                key="potential",
                accuracy=new_potential_accuracy,
                potential=True,
            ),
            source="year_end_pro_fog",
            notes=f"Year-end evaluation advanced with {int(round(effective_snaps))} weighted snap evidence.",
        )
        if overall_gain >= 0.08 or effective_snaps >= 240:
            con.execute(
                """
                INSERT INTO player_evaluation_events (
                    game_id, evaluator_team_id, player_id, season, event_date,
                    event_type, overall_accuracy_delta, potential_accuracy_delta,
                    snap_count, source, notes
                )
                VALUES (?, ?, ?, ?, NULL, 'year_end_review', ?, ?, ?, 'year_end_pro_fog', ?)
                """,
                (
                    game_id,
                    evaluator,
                    player_id,
                    to_season,
                    round(overall_gain, 4),
                    round(potential_gain, 4),
                    float(effective_snaps or 0.0),
                    f"Annual review sharpened the staff read after {int(round(effective_snaps))} weighted snap evidence.",
                ),
            )
        updated += 1
    return updated


def evaluation_dict(row: sqlite3.Row) -> dict[str, Any]:
    overall_accuracy = float(row["overall_accuracy"] or 0.0)
    potential_accuracy = float(row["potential_accuracy"] or 0.0)
    return {
        "playerId": int(row["player_id"]),
        "teamId": int(row["evaluator_team_id"]),
        "season": int(row["season"]),
        "overall": int(row["perceived_overall"]),
        "potential": int(row["perceived_potential"]),
        "overallAccuracy": round(overall_accuracy, 3),
        "potentialAccuracy": round(potential_accuracy, 3),
        "confidence": row["confidence_label"],
        "confidenceLabel": row["confidence_label"],
        "confidenceNote": row["confidence_note"] or confidence_note(row["confidence_label"]),
        "revealAge": int(row["reveal_age"]),
        "source": row["source"],
        "notes": row["notes"] or "",
    }


def evaluations_for_players(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    player_team_ids: dict[int, int | None],
    create_missing: bool = True,
) -> tuple[dict[int, dict[str, Any]], int]:
    ensure_schema(con)
    created = 0
    reads: dict[int, dict[str, Any]] = {}
    for player_id, team_id in player_team_ids.items():
        evaluator = team_key(team_id)
        row = con.execute(
            """
            SELECT *
            FROM player_evaluation_reports
            WHERE game_id = ? AND evaluator_team_id = ? AND player_id = ?
            """,
            (game_id, evaluator, int(player_id)),
        ).fetchone()
        if not row and create_missing:
            if create_initial_evaluation(
                con,
                game_id=game_id,
                evaluator_team_id=evaluator,
                player_id=int(player_id),
                season=season,
                source="lazy_pro_fog_seed",
            ):
                created += 1
            row = con.execute(
                """
                SELECT *
                FROM player_evaluation_reports
                WHERE game_id = ? AND evaluator_team_id = ? AND player_id = ?
                """,
                (game_id, evaluator, int(player_id)),
            ).fetchone()
        if row:
            reads[int(player_id)] = evaluation_dict(row)
    return reads, created


def evaluations_for_team(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    evaluator_team_id: int | None,
    player_ids: list[int] | set[int] | tuple[int, ...],
    create_missing: bool = True,
) -> tuple[dict[int, dict[str, Any]], int]:
    ensure_schema(con)
    evaluator = team_key(evaluator_team_id)
    unique_ids = sorted({int(player_id) for player_id in player_ids if player_id is not None})
    if not unique_ids:
        return {}, 0
    cached: dict[int, dict[str, Any]] = {}
    missing: list[int] = []
    for player_id in unique_ids:
        key = (id(con), str(game_id), int(season), evaluator, player_id)
        if key in _TEAM_EVALUATION_CACHE:
            read = _TEAM_EVALUATION_CACHE[key]
            if read:
                cached[player_id] = read
        else:
            missing.append(player_id)
    created = 0
    if missing:
        placeholders = ",".join("?" for _ in missing)
        rows = con.execute(
            f"""
            SELECT *
            FROM player_evaluation_reports
            WHERE game_id = ?
              AND evaluator_team_id = ?
              AND player_id IN ({placeholders})
            """,
            (game_id, evaluator, *missing),
        ).fetchall()
        found = {int(row["player_id"]): evaluation_dict(row) for row in rows}
        if create_missing:
            for player_id in missing:
                if player_id in found:
                    continue
                if create_initial_evaluation(
                    con,
                    game_id=game_id,
                    evaluator_team_id=evaluator,
                    player_id=player_id,
                    season=season,
                    source="cpu_pro_fog_seed",
                ):
                    created += 1
                    row = con.execute(
                        """
                        SELECT *
                        FROM player_evaluation_reports
                        WHERE game_id = ? AND evaluator_team_id = ? AND player_id = ?
                        """,
                        (game_id, evaluator, player_id),
                    ).fetchone()
                    if row:
                        found[player_id] = evaluation_dict(row)
        for player_id in missing:
            key = (id(con), str(game_id), int(season), evaluator, player_id)
            _TEAM_EVALUATION_CACHE[key] = found.get(player_id)
        cached.update(found)
    return cached, created


def evaluation_for_team(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    evaluator_team_id: int | None,
    player_id: int,
    create_missing: bool = True,
) -> dict[str, Any] | None:
    reads, _created = evaluations_for_team(
        con,
        game_id=game_id,
        season=season,
        evaluator_team_id=evaluator_team_id,
        player_ids=[int(player_id)],
        create_missing=create_missing,
    )
    return reads.get(int(player_id))


def apply_evaluation_to_mapping(row: dict[str, Any], evaluation: dict[str, Any] | None) -> dict[str, Any]:
    if not evaluation:
        return row
    true_overall = row.get("overall")
    true_potential = row.get("potential")
    row["true_overall"] = true_overall
    row["true_potential"] = true_potential
    row["overall"] = int(evaluation.get("overall") or true_overall or 50)
    row["potential"] = int(evaluation.get("potential") or true_potential or row["overall"])
    row["evaluation_confidence"] = evaluation.get("confidenceLabel") or evaluation.get("confidence")
    row["evaluation_overall_accuracy"] = evaluation.get("overallAccuracy")
    row["evaluation_potential_accuracy"] = evaluation.get("potentialAccuracy")
    return row


def perceived_overall_potential(
    con: sqlite3.Connection,
    *,
    game_id: str,
    season: int,
    evaluator_team_id: int | None,
    player_id: int,
    true_overall: int | float | None,
    true_potential: int | float | None = None,
    create_missing: bool = True,
) -> tuple[float, float, dict[str, Any] | None]:
    evaluation = evaluation_for_team(
        con,
        game_id=game_id,
        season=season,
        evaluator_team_id=evaluator_team_id,
        player_id=int(player_id),
        create_missing=create_missing,
    )
    if evaluation:
        overall = float(evaluation.get("overall") or true_overall or 50)
        potential = float(evaluation.get("potential") or true_potential or overall)
        return overall, max(potential, overall), evaluation
    overall = float(true_overall or 50)
    potential = float(true_potential if true_potential is not None else overall)
    return overall, max(potential, overall), None


def event_read_note(
    con: sqlite3.Connection,
    *,
    game_id: str,
    team_id: int | None,
    player_id: int,
    season: int,
) -> str:
    reads, _created = evaluations_for_players(
        con,
        game_id=game_id,
        season=season,
        player_team_ids={int(player_id): team_id},
        create_missing=True,
    )
    read = reads.get(int(player_id))
    if not read:
        return ""
    label = str(read.get("confidenceLabel") or read.get("confidence") or "Cloudy")
    overall = int(read.get("overall") or 0)
    potential = int(read.get("potential") or overall)
    if label in {"Firm", "Strong"}:
        return f"Staff evaluation update ({label}): current estimate is roughly {overall} OVR / {potential} POT."
    if label == "Solid":
        return (
            f"Staff evaluation update ({label}): current estimate is around {overall} OVR / {potential} POT, "
            "with some room for the grade to move."
        )
    return (
        f"Staff evaluation update ({label}): early estimate sits near {overall} OVR / {potential} POT, "
        "but coaches still consider it a moving grade."
    )


def append_event_read_note(
    body: str,
    con: sqlite3.Connection,
    *,
    game_id: str,
    team_id: int | None,
    player_id: int | None,
    season: int,
) -> str:
    if player_id is None:
        return body
    note = event_read_note(
        con,
        game_id=game_id,
        team_id=team_id,
        player_id=int(player_id),
        season=season,
    )
    return f"{body}\n\n{note}" if note else body


def fog_rating_value(evaluation: dict[str, Any] | None, rating_key: str, true_value: float) -> float:
    if not evaluation:
        return round(float(true_value), 1)
    accuracy = float(evaluation.get("overallAccuracy") or 0.0)
    player_id = int(evaluation.get("playerId") or 0)
    evaluator = int(evaluation.get("teamId") or 0)
    season = int(evaluation.get("season") or 0)
    rng = random.Random(f"{season}:{evaluator}:{player_id}:rating:{rating_key}")
    sigma = (1.0 - clamp(accuracy, 0.0, 1.0)) * 4.8
    value = float(true_value) + clamp(rng.gauss(0.0, sigma), -max(1.0, sigma * 2.2), max(1.0, sigma * 2.2))
    return round(max(0.0, min(100.0, value)), 1)


def fog_role_value(evaluation: dict[str, Any] | None, role_key: str | None, true_value: float) -> float:
    if not evaluation:
        return round(float(true_value), 1)
    accuracy = float(evaluation.get("overallAccuracy") or 0.0)
    player_id = int(evaluation.get("playerId") or 0)
    evaluator = int(evaluation.get("teamId") or 0)
    season = int(evaluation.get("season") or 0)
    rng = random.Random(f"{season}:{evaluator}:{player_id}:role:{role_key or 'default'}")
    sigma = (1.0 - clamp(accuracy, 0.0, 1.0)) * 5.6
    value = float(true_value) + clamp(rng.gauss(0.0, sigma), -max(1.0, sigma * 2.1), max(1.0, sigma * 2.1))
    return round(max(0.0, min(100.0, value)), 1)
