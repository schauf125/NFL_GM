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
class DraftProspectRBBehaviorProfile:
    label: str
    early_down_gravity: int
    patience: int
    one_cut_decisiveness: int
    bounce_tendency: int
    home_run_hunting: int
    contact_appetite: int
    space_creation: int
    pass_game_usage: int
    short_yardage_trust: int
    ball_security_mindset: int
    notes: str | None = None


@dataclass(frozen=True)
class DraftProspectReceiverBehaviorProfile:
    label: str
    target_gravity: int
    release_urgency: int
    route_pacing: int
    vertical_intent: int
    middle_comfort: int
    contested_alpha: int
    sideline_awareness: int
    yac_intent: int
    scramble_drill: int
    catch_security: int
    notes: str | None = None


@dataclass(frozen=True)
class DraftProspectOLBehaviorProfile:
    label: str
    pass_set_patience: int
    mirror_vs_speed: int
    anchor_vs_power: int
    hand_timing: int
    stunt_awareness: int
    drive_finish: int
    reach_range: int
    combo_timing: int
    second_level_climb: int
    penalty_control: int
    notes: str | None = None


@dataclass(frozen=True)
class DraftProspectEdgeBehaviorProfile:
    label: str
    getoff_timing: int
    speed_arc: int
    power_collapse: int
    counter_plan: int
    stunt_timing: int
    contain_discipline: int
    run_squeeze: int
    backside_pursuit: int
    finish_skill: int
    rush_discipline: int
    notes: str | None = None


@dataclass(frozen=True)
class DraftProspectIDLBehaviorProfile:
    label: str
    getoff_timing: int
    penetration_burst: int
    power_collapse: int
    double_team_anchor: int
    gap_control: int
    block_shed_timing: int
    stunt_timing: int
    rush_counter_plan: int
    finish_skill: int
    rush_discipline: int
    notes: str | None = None


@dataclass(frozen=True)
class DraftProspectLBBehaviorProfile:
    label: str
    trigger_quickness: int
    gap_fit_discipline: int
    scrape_range: int
    traffic_navigation: int
    zone_landmark_depth: int
    man_match_carry: int
    blitz_timing: int
    tackle_finish: int
    rally_support: int
    penalty_control: int
    notes: str | None = None


@dataclass(frozen=True)
class DraftProspectSecondaryBehaviorProfile:
    label: str
    press_timing: int
    man_mirror: int
    zone_eye_discipline: int
    break_trigger: int
    deep_range: int
    ball_play_timing: int
    catch_point_compete: int
    slot_traffic: int
    run_support_fit: int
    tackle_finish: int
    penalty_control: int
    notes: str | None = None


@dataclass(frozen=True)
class DraftProspectSpecialistBehaviorProfile:
    label: str
    kick_operation: int
    kickoff_control: int
    punt_hang_time: int
    punt_placement: int
    snap_accuracy: int
    lane_release: int
    gunner_speed: int
    return_lane_vision: int
    block_timing: int
    coverage_tackle: int
    penalty_control: int
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


def replace_prospect_rb_behavior_profile(
    con: sqlite3.Connection,
    prospect_id: int,
    profile: DraftProspectRBBehaviorProfile,
    *,
    source: str = "draft_generator",
    ensure: bool = True,
) -> None:
    """Replace one generated RB behavior profile for a draft prospect."""
    if ensure:
        ensure_schema(con)
    con.execute(
        """
        INSERT INTO draft_prospect_rb_behavior_profiles (
            prospect_id, label, early_down_gravity, patience,
            one_cut_decisiveness, bounce_tendency, home_run_hunting,
            contact_appetite, space_creation, pass_game_usage,
            short_yardage_trust, ball_security_mindset, source, notes,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(prospect_id) DO UPDATE SET
            label = excluded.label,
            early_down_gravity = excluded.early_down_gravity,
            patience = excluded.patience,
            one_cut_decisiveness = excluded.one_cut_decisiveness,
            bounce_tendency = excluded.bounce_tendency,
            home_run_hunting = excluded.home_run_hunting,
            contact_appetite = excluded.contact_appetite,
            space_creation = excluded.space_creation,
            pass_game_usage = excluded.pass_game_usage,
            short_yardage_trust = excluded.short_yardage_trust,
            ball_security_mindset = excluded.ball_security_mindset,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            prospect_id,
            profile.label,
            profile.early_down_gravity,
            profile.patience,
            profile.one_cut_decisiveness,
            profile.bounce_tendency,
            profile.home_run_hunting,
            profile.contact_appetite,
            profile.space_creation,
            profile.pass_game_usage,
            profile.short_yardage_trust,
            profile.ball_security_mindset,
            source,
            profile.notes,
        ),
    )


def replace_prospect_receiver_behavior_profile(
    con: sqlite3.Connection,
    prospect_id: int,
    profile: DraftProspectReceiverBehaviorProfile,
    *,
    source: str = "draft_generator",
    ensure: bool = True,
) -> None:
    """Replace one generated receiver behavior profile for a draft prospect."""
    if ensure:
        ensure_schema(con)
    con.execute(
        """
        INSERT INTO draft_prospect_receiver_behavior_profiles (
            prospect_id, label, target_gravity, release_urgency,
            route_pacing, vertical_intent, middle_comfort, contested_alpha,
            sideline_awareness, yac_intent, scramble_drill, catch_security,
            source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(prospect_id) DO UPDATE SET
            label = excluded.label,
            target_gravity = excluded.target_gravity,
            release_urgency = excluded.release_urgency,
            route_pacing = excluded.route_pacing,
            vertical_intent = excluded.vertical_intent,
            middle_comfort = excluded.middle_comfort,
            contested_alpha = excluded.contested_alpha,
            sideline_awareness = excluded.sideline_awareness,
            yac_intent = excluded.yac_intent,
            scramble_drill = excluded.scramble_drill,
            catch_security = excluded.catch_security,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            prospect_id,
            profile.label,
            profile.target_gravity,
            profile.release_urgency,
            profile.route_pacing,
            profile.vertical_intent,
            profile.middle_comfort,
            profile.contested_alpha,
            profile.sideline_awareness,
            profile.yac_intent,
            profile.scramble_drill,
            profile.catch_security,
            source,
            profile.notes,
        ),
    )


def replace_prospect_ol_behavior_profile(
    con: sqlite3.Connection,
    prospect_id: int,
    profile: DraftProspectOLBehaviorProfile,
    *,
    source: str = "draft_generator",
    ensure: bool = True,
) -> None:
    """Replace one generated OL behavior profile for a draft prospect."""
    if ensure:
        ensure_schema(con)
    con.execute(
        """
        INSERT INTO draft_prospect_ol_behavior_profiles (
            prospect_id, label, pass_set_patience, mirror_vs_speed,
            anchor_vs_power, hand_timing, stunt_awareness, drive_finish,
            reach_range, combo_timing, second_level_climb, penalty_control,
            source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(prospect_id) DO UPDATE SET
            label = excluded.label,
            pass_set_patience = excluded.pass_set_patience,
            mirror_vs_speed = excluded.mirror_vs_speed,
            anchor_vs_power = excluded.anchor_vs_power,
            hand_timing = excluded.hand_timing,
            stunt_awareness = excluded.stunt_awareness,
            drive_finish = excluded.drive_finish,
            reach_range = excluded.reach_range,
            combo_timing = excluded.combo_timing,
            second_level_climb = excluded.second_level_climb,
            penalty_control = excluded.penalty_control,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            prospect_id,
            profile.label,
            profile.pass_set_patience,
            profile.mirror_vs_speed,
            profile.anchor_vs_power,
            profile.hand_timing,
            profile.stunt_awareness,
            profile.drive_finish,
            profile.reach_range,
            profile.combo_timing,
            profile.second_level_climb,
            profile.penalty_control,
            source,
            profile.notes,
        ),
    )


def replace_prospect_edge_behavior_profile(
    con: sqlite3.Connection,
    prospect_id: int,
    profile: DraftProspectEdgeBehaviorProfile,
    *,
    source: str = "draft_generator",
    ensure: bool = True,
) -> None:
    """Replace one generated edge behavior profile for a draft prospect."""
    if ensure:
        ensure_schema(con)
    con.execute(
        """
        INSERT INTO draft_prospect_edge_behavior_profiles (
            prospect_id, label, getoff_timing, speed_arc, power_collapse,
            counter_plan, stunt_timing, contain_discipline, run_squeeze,
            backside_pursuit, finish_skill, rush_discipline, source, notes,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(prospect_id) DO UPDATE SET
            label = excluded.label,
            getoff_timing = excluded.getoff_timing,
            speed_arc = excluded.speed_arc,
            power_collapse = excluded.power_collapse,
            counter_plan = excluded.counter_plan,
            stunt_timing = excluded.stunt_timing,
            contain_discipline = excluded.contain_discipline,
            run_squeeze = excluded.run_squeeze,
            backside_pursuit = excluded.backside_pursuit,
            finish_skill = excluded.finish_skill,
            rush_discipline = excluded.rush_discipline,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            prospect_id,
            profile.label,
            profile.getoff_timing,
            profile.speed_arc,
            profile.power_collapse,
            profile.counter_plan,
            profile.stunt_timing,
            profile.contain_discipline,
            profile.run_squeeze,
            profile.backside_pursuit,
            profile.finish_skill,
            profile.rush_discipline,
            source,
            profile.notes,
        ),
    )


def replace_prospect_idl_behavior_profile(
    con: sqlite3.Connection,
    prospect_id: int,
    profile: DraftProspectIDLBehaviorProfile,
    *,
    source: str = "draft_generator",
    ensure: bool = True,
) -> None:
    """Replace one generated IDL behavior profile for a draft prospect."""
    if ensure:
        ensure_schema(con)
    con.execute(
        """
        INSERT INTO draft_prospect_idl_behavior_profiles (
            prospect_id, label, getoff_timing, penetration_burst,
            power_collapse, double_team_anchor, gap_control,
            block_shed_timing, stunt_timing, rush_counter_plan,
            finish_skill, rush_discipline, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(prospect_id) DO UPDATE SET
            label = excluded.label,
            getoff_timing = excluded.getoff_timing,
            penetration_burst = excluded.penetration_burst,
            power_collapse = excluded.power_collapse,
            double_team_anchor = excluded.double_team_anchor,
            gap_control = excluded.gap_control,
            block_shed_timing = excluded.block_shed_timing,
            stunt_timing = excluded.stunt_timing,
            rush_counter_plan = excluded.rush_counter_plan,
            finish_skill = excluded.finish_skill,
            rush_discipline = excluded.rush_discipline,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            prospect_id,
            profile.label,
            profile.getoff_timing,
            profile.penetration_burst,
            profile.power_collapse,
            profile.double_team_anchor,
            profile.gap_control,
            profile.block_shed_timing,
            profile.stunt_timing,
            profile.rush_counter_plan,
            profile.finish_skill,
            profile.rush_discipline,
            source,
            profile.notes,
        ),
    )


def replace_prospect_lb_behavior_profile(
    con: sqlite3.Connection,
    prospect_id: int,
    profile: DraftProspectLBBehaviorProfile,
    *,
    source: str = "draft_generator",
    ensure: bool = True,
) -> None:
    """Replace one generated LB behavior profile for a draft prospect."""
    if ensure:
        ensure_schema(con)
    con.execute(
        """
        INSERT INTO draft_prospect_lb_behavior_profiles (
            prospect_id, label, trigger_quickness, gap_fit_discipline,
            scrape_range, traffic_navigation, zone_landmark_depth,
            man_match_carry, blitz_timing, tackle_finish, rally_support,
            penalty_control, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(prospect_id) DO UPDATE SET
            label = excluded.label,
            trigger_quickness = excluded.trigger_quickness,
            gap_fit_discipline = excluded.gap_fit_discipline,
            scrape_range = excluded.scrape_range,
            traffic_navigation = excluded.traffic_navigation,
            zone_landmark_depth = excluded.zone_landmark_depth,
            man_match_carry = excluded.man_match_carry,
            blitz_timing = excluded.blitz_timing,
            tackle_finish = excluded.tackle_finish,
            rally_support = excluded.rally_support,
            penalty_control = excluded.penalty_control,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            prospect_id,
            profile.label,
            profile.trigger_quickness,
            profile.gap_fit_discipline,
            profile.scrape_range,
            profile.traffic_navigation,
            profile.zone_landmark_depth,
            profile.man_match_carry,
            profile.blitz_timing,
            profile.tackle_finish,
            profile.rally_support,
            profile.penalty_control,
            source,
            profile.notes,
        ),
    )


def replace_prospect_secondary_behavior_profile(
    con: sqlite3.Connection,
    prospect_id: int,
    profile: DraftProspectSecondaryBehaviorProfile,
    *,
    source: str = "draft_generator",
    ensure: bool = True,
) -> None:
    """Replace one generated secondary behavior profile for a draft prospect."""
    if ensure:
        ensure_schema(con)
    con.execute(
        """
        INSERT INTO draft_prospect_secondary_behavior_profiles (
            prospect_id, label, press_timing, man_mirror,
            zone_eye_discipline, break_trigger, deep_range,
            ball_play_timing, catch_point_compete, slot_traffic,
            run_support_fit, tackle_finish, penalty_control,
            source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(prospect_id) DO UPDATE SET
            label = excluded.label,
            press_timing = excluded.press_timing,
            man_mirror = excluded.man_mirror,
            zone_eye_discipline = excluded.zone_eye_discipline,
            break_trigger = excluded.break_trigger,
            deep_range = excluded.deep_range,
            ball_play_timing = excluded.ball_play_timing,
            catch_point_compete = excluded.catch_point_compete,
            slot_traffic = excluded.slot_traffic,
            run_support_fit = excluded.run_support_fit,
            tackle_finish = excluded.tackle_finish,
            penalty_control = excluded.penalty_control,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            prospect_id,
            profile.label,
            profile.press_timing,
            profile.man_mirror,
            profile.zone_eye_discipline,
            profile.break_trigger,
            profile.deep_range,
            profile.ball_play_timing,
            profile.catch_point_compete,
            profile.slot_traffic,
            profile.run_support_fit,
            profile.tackle_finish,
            profile.penalty_control,
            source,
            profile.notes,
        ),
    )


def replace_prospect_specialist_behavior_profile(
    con: sqlite3.Connection,
    prospect_id: int,
    profile: DraftProspectSpecialistBehaviorProfile,
    *,
    source: str = "draft_generator",
    ensure: bool = True,
) -> None:
    """Replace one generated special teams behavior profile for a draft prospect."""
    if ensure:
        ensure_schema(con)
    con.execute(
        """
        INSERT INTO draft_prospect_specialist_behavior_profiles (
            prospect_id, label, kick_operation, kickoff_control,
            punt_hang_time, punt_placement, snap_accuracy,
            lane_release, gunner_speed, return_lane_vision,
            block_timing, coverage_tackle, penalty_control,
            source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(prospect_id) DO UPDATE SET
            label = excluded.label,
            kick_operation = excluded.kick_operation,
            kickoff_control = excluded.kickoff_control,
            punt_hang_time = excluded.punt_hang_time,
            punt_placement = excluded.punt_placement,
            snap_accuracy = excluded.snap_accuracy,
            lane_release = excluded.lane_release,
            gunner_speed = excluded.gunner_speed,
            return_lane_vision = excluded.return_lane_vision,
            block_timing = excluded.block_timing,
            coverage_tackle = excluded.coverage_tackle,
            penalty_control = excluded.penalty_control,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (
            prospect_id,
            profile.label,
            profile.kick_operation,
            profile.kickoff_control,
            profile.punt_hang_time,
            profile.punt_placement,
            profile.snap_accuracy,
            profile.lane_release,
            profile.gunner_speed,
            profile.return_lane_vision,
            profile.block_timing,
            profile.coverage_tackle,
            profile.penalty_control,
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
