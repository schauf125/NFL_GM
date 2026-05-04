"""Repository helpers for draft classes and prospects."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from .schema import ensure_schema


@dataclass(frozen=True)
class DraftClass:
    draft_year: int
    class_name: str
    class_strength: int = 50
    generation_seed: str | None = None
    status: str = "Generated"
    notes: str | None = None


@dataclass(frozen=True)
class DraftProspectRating:
    rating_key: str
    rating_value: int
    confidence: str = "low"
    notes: str | None = None


@dataclass(frozen=True)
class DraftProspectRoleScore:
    role_key: str
    role_score: float
    scheme_key: str = "default"


@dataclass(frozen=True)
class DraftProspectQBBehaviorProfile:
    label: str
    rhythm: int
    pocket_discipline: int
    pocket_drift: int
    checkdown_willingness: int
    deep_aggression: int
    pressure_escape: int
    broken_play_creation: int
    scramble_trigger: int
    sack_risk: int
    throwaway_discipline: int
    notes: str | None = None


@dataclass(frozen=True)
class DraftProspectCombineResult:
    combine_status: str
    participation_note: str | None = None
    combine_grade: int | None = None
    athletic_score: int | None = None
    drills_completed: int = 0
    drills_skipped: str | None = None
    workout_variance: str | None = None
    forty_yard_dash: float | None = None
    ten_yard_split: float | None = None
    bench_press_reps: int | None = None
    vertical_jump_in: float | None = None
    broad_jump_in: int | None = None
    three_cone_sec: float | None = None
    twenty_yard_shuttle_sec: float | None = None
    sixty_yard_shuttle_sec: float | None = None
    is_injured: bool = False
    is_top_skip: bool = False


@dataclass(frozen=True)
class DraftProspectProDayResult:
    pro_day_status: str
    participation_note: str | None = None
    pro_day_grade: int | None = None
    athletic_score: int | None = None
    drills_completed: int = 0
    drills_skipped: str | None = None
    workout_variance: str | None = None
    summary: str | None = None
    forty_yard_dash: float | None = None
    ten_yard_split: float | None = None
    bench_press_reps: int | None = None
    vertical_jump_in: float | None = None
    broad_jump_in: int | None = None
    three_cone_sec: float | None = None
    twenty_yard_shuttle_sec: float | None = None
    sixty_yard_shuttle_sec: float | None = None
    improved_from_combine: bool = False
    medical_recheck: bool = False


@dataclass(frozen=True)
class DraftProspectPrivateWorkout:
    status: str
    workout_type: str
    interest_level: str
    outcome_grade: int | None = None
    notes: str | None = None
    team_id: int | None = None
    hidden: bool = True


def create_draft_class(con: sqlite3.Connection, draft_class: DraftClass) -> int:
    """Create or update a draft class and return its id."""
    ensure_schema(con)
    con.execute(
        """
        INSERT INTO draft_classes (
            draft_year, class_name, class_strength, generation_seed, status, notes
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(draft_year) DO UPDATE SET
            class_name = excluded.class_name,
            class_strength = excluded.class_strength,
            generation_seed = excluded.generation_seed,
            status = excluded.status,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            draft_class.draft_year,
            draft_class.class_name,
            draft_class.class_strength,
            draft_class.generation_seed,
            draft_class.status,
            draft_class.notes,
        ),
    )
    row = con.execute(
        "SELECT draft_class_id FROM draft_classes WHERE draft_year = ?",
        (draft_class.draft_year,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Failed to create draft class for {draft_class.draft_year}")
    return int(row[0])


def list_draft_classes(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return draft classes ordered by year."""
    ensure_schema(con)
    con.row_factory = sqlite3.Row
    return con.execute(
        """
        SELECT *
        FROM draft_class_summary_view
        ORDER BY draft_year
        """
    ).fetchall()


def draft_board(
    con: sqlite3.Connection,
    draft_year: int,
    *,
    positions: Iterable[str] | None = None,
) -> list[sqlite3.Row]:
    """Return the current board for a draft year."""
    ensure_schema(con)
    con.row_factory = sqlite3.Row
    params: list[object] = [draft_year]
    where = ["draft_year = ?"]
    if positions:
        values = [position.upper() for position in positions]
        where.append(f"position IN ({','.join('?' for _ in values)})")
        params.extend(values)
    return con.execute(
        f"""
        SELECT *
        FROM draft_board_view
        WHERE {' AND '.join(where)}
        ORDER BY
            CASE WHEN scouting_rank IS NULL THEN 1 ELSE 0 END,
            scouting_rank,
            COALESCE(scout_ceiling, scout_grade) DESC,
            scout_grade DESC,
            prospect_id
        """,
        params,
    ).fetchall()


def replace_prospect_sim_profile(
    con: sqlite3.Connection,
    prospect_id: int,
    *,
    ratings: Iterable[DraftProspectRating],
    role_scores: Iterable[DraftProspectRoleScore],
    roles: Iterable[str],
    source: str = "draft_generator",
    ensure: bool = True,
) -> None:
    """Replace generated normalized ratings and role rows for one prospect."""
    if ensure:
        ensure_schema(con)
    con.execute("DELETE FROM draft_prospect_ratings WHERE prospect_id = ?", (prospect_id,))
    con.execute("DELETE FROM draft_prospect_role_assignments WHERE prospect_id = ?", (prospect_id,))
    con.execute("DELETE FROM draft_prospect_role_scores WHERE prospect_id = ?", (prospect_id,))

    con.executemany(
        """
        INSERT INTO draft_prospect_ratings (
            prospect_id, rating_key, rating_value, confidence, source, notes
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                prospect_id,
                rating.rating_key,
                int(rating.rating_value),
                rating.confidence,
                source,
                rating.notes,
            )
            for rating in ratings
        ],
    )

    con.executemany(
        """
        INSERT INTO draft_prospect_role_assignments (
            prospect_id, role_key, priority, source, notes
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                prospect_id,
                role_key,
                priority,
                source,
                "Generated from prospect archetype and normalized ratings.",
            )
            for priority, role_key in enumerate((role for role in roles if role), start=1)
        ],
    )

    con.executemany(
        """
        INSERT INTO draft_prospect_role_scores (
            prospect_id, role_key, scheme_key, role_score, source
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                prospect_id,
                role_score.role_key,
                role_score.scheme_key,
                float(role_score.role_score),
                source,
            )
            for role_score in role_scores
        ],
    )


def replace_prospect_qb_behavior_profile(
    con: sqlite3.Connection,
    prospect_id: int,
    profile: DraftProspectQBBehaviorProfile,
    *,
    source: str = "draft_generator",
    ensure: bool = True,
) -> None:
    """Replace one generated QB behavior profile for a draft prospect."""
    if ensure:
        ensure_schema(con)
    con.execute(
        """
        INSERT INTO draft_prospect_qb_behavior_profiles (
            prospect_id, label, rhythm, pocket_discipline, pocket_drift,
            checkdown_willingness, deep_aggression, pressure_escape,
            broken_play_creation, scramble_trigger, sack_risk,
            throwaway_discipline, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(prospect_id) DO UPDATE SET
            label = excluded.label,
            rhythm = excluded.rhythm,
            pocket_discipline = excluded.pocket_discipline,
            pocket_drift = excluded.pocket_drift,
            checkdown_willingness = excluded.checkdown_willingness,
            deep_aggression = excluded.deep_aggression,
            pressure_escape = excluded.pressure_escape,
            broken_play_creation = excluded.broken_play_creation,
            scramble_trigger = excluded.scramble_trigger,
            sack_risk = excluded.sack_risk,
            throwaway_discipline = excluded.throwaway_discipline,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            prospect_id,
            profile.label,
            profile.rhythm,
            profile.pocket_discipline,
            profile.pocket_drift,
            profile.checkdown_willingness,
            profile.deep_aggression,
            profile.pressure_escape,
            profile.broken_play_creation,
            profile.scramble_trigger,
            profile.sack_risk,
            profile.throwaway_discipline,
            source,
            profile.notes,
        ),
    )


def replace_prospect_combine_result(
    con: sqlite3.Connection,
    prospect_id: int,
    result: DraftProspectCombineResult,
    *,
    source: str = "draft_generator",
    ensure: bool = True,
) -> None:
    """Replace one prospect's generated combine workout row."""
    if ensure:
        ensure_schema(con)
    con.execute(
        """
        INSERT INTO draft_prospect_combine_results (
            prospect_id,
            combine_status,
            participation_note,
            combine_grade,
            athletic_score,
            drills_completed,
            drills_skipped,
            workout_variance,
            forty_yard_dash,
            ten_yard_split,
            bench_press_reps,
            vertical_jump_in,
            broad_jump_in,
            three_cone_sec,
            twenty_yard_shuttle_sec,
            sixty_yard_shuttle_sec,
            is_injured,
            is_top_skip,
            source,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(prospect_id) DO UPDATE SET
            combine_status = excluded.combine_status,
            participation_note = excluded.participation_note,
            combine_grade = excluded.combine_grade,
            athletic_score = excluded.athletic_score,
            drills_completed = excluded.drills_completed,
            drills_skipped = excluded.drills_skipped,
            workout_variance = excluded.workout_variance,
            forty_yard_dash = excluded.forty_yard_dash,
            ten_yard_split = excluded.ten_yard_split,
            bench_press_reps = excluded.bench_press_reps,
            vertical_jump_in = excluded.vertical_jump_in,
            broad_jump_in = excluded.broad_jump_in,
            three_cone_sec = excluded.three_cone_sec,
            twenty_yard_shuttle_sec = excluded.twenty_yard_shuttle_sec,
            sixty_yard_shuttle_sec = excluded.sixty_yard_shuttle_sec,
            is_injured = excluded.is_injured,
            is_top_skip = excluded.is_top_skip,
            source = excluded.source,
            updated_at = datetime('now')
        """,
        (
            prospect_id,
            result.combine_status,
            result.participation_note,
            result.combine_grade,
            result.athletic_score,
            result.drills_completed,
            result.drills_skipped,
            result.workout_variance,
            result.forty_yard_dash,
            result.ten_yard_split,
            result.bench_press_reps,
            result.vertical_jump_in,
            result.broad_jump_in,
            result.three_cone_sec,
            result.twenty_yard_shuttle_sec,
            result.sixty_yard_shuttle_sec,
            int(result.is_injured),
            int(result.is_top_skip),
            source,
        ),
    )


def replace_prospect_pro_day_result(
    con: sqlite3.Connection,
    prospect_id: int,
    result: DraftProspectProDayResult,
    *,
    source: str = "draft_generator",
    ensure: bool = True,
) -> None:
    """Replace one prospect's generated pro-day row."""
    if ensure:
        ensure_schema(con)
    con.execute(
        """
        INSERT INTO draft_prospect_pro_day_results (
            prospect_id,
            pro_day_status,
            participation_note,
            pro_day_grade,
            athletic_score,
            drills_completed,
            drills_skipped,
            workout_variance,
            summary,
            forty_yard_dash,
            ten_yard_split,
            bench_press_reps,
            vertical_jump_in,
            broad_jump_in,
            three_cone_sec,
            twenty_yard_shuttle_sec,
            sixty_yard_shuttle_sec,
            improved_from_combine,
            medical_recheck,
            source,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(prospect_id) DO UPDATE SET
            pro_day_status = excluded.pro_day_status,
            participation_note = excluded.participation_note,
            pro_day_grade = excluded.pro_day_grade,
            athletic_score = excluded.athletic_score,
            drills_completed = excluded.drills_completed,
            drills_skipped = excluded.drills_skipped,
            workout_variance = excluded.workout_variance,
            summary = excluded.summary,
            forty_yard_dash = excluded.forty_yard_dash,
            ten_yard_split = excluded.ten_yard_split,
            bench_press_reps = excluded.bench_press_reps,
            vertical_jump_in = excluded.vertical_jump_in,
            broad_jump_in = excluded.broad_jump_in,
            three_cone_sec = excluded.three_cone_sec,
            twenty_yard_shuttle_sec = excluded.twenty_yard_shuttle_sec,
            sixty_yard_shuttle_sec = excluded.sixty_yard_shuttle_sec,
            improved_from_combine = excluded.improved_from_combine,
            medical_recheck = excluded.medical_recheck,
            source = excluded.source,
            updated_at = datetime('now')
        """,
        (
            prospect_id,
            result.pro_day_status,
            result.participation_note,
            result.pro_day_grade,
            result.athletic_score,
            result.drills_completed,
            result.drills_skipped,
            result.workout_variance,
            result.summary,
            result.forty_yard_dash,
            result.ten_yard_split,
            result.bench_press_reps,
            result.vertical_jump_in,
            result.broad_jump_in,
            result.three_cone_sec,
            result.twenty_yard_shuttle_sec,
            result.sixty_yard_shuttle_sec,
            int(result.improved_from_combine),
            int(result.medical_recheck),
            source,
        ),
    )


def replace_prospect_private_workout(
    con: sqlite3.Connection,
    prospect_id: int,
    workout: DraftProspectPrivateWorkout,
    *,
    source: str = "draft_generator",
    ensure: bool = True,
) -> None:
    """Replace generated hidden private-workout rows for one prospect."""
    if ensure:
        ensure_schema(con)
    con.execute(
        "DELETE FROM draft_prospect_private_workouts WHERE prospect_id = ? AND source = ?",
        (prospect_id, source),
    )
    con.execute(
        """
        INSERT INTO draft_prospect_private_workouts (
            prospect_id,
            team_id,
            status,
            workout_type,
            interest_level,
            outcome_grade,
            hidden,
            notes,
            source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prospect_id,
            workout.team_id,
            workout.status,
            workout.workout_type,
            workout.interest_level,
            workout.outcome_grade,
            int(workout.hidden),
            workout.notes,
            source,
        ),
    )
