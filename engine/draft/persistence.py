"""Persist generated draft-class preview rows into SQLite."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .attributes import clamp
from .class_preview import DraftClassPreviewRow
from .repository import (
    DraftClass,
    DraftProspectCombineResult,
    DraftProspectQBBehaviorProfile,
    DraftProspectRBBehaviorProfile,
    DraftProspectReceiverBehaviorProfile,
    DraftProspectOLBehaviorProfile,
    DraftProspectEdgeBehaviorProfile,
    DraftProspectIDLBehaviorProfile,
    DraftProspectLBBehaviorProfile,
    DraftProspectSecondaryBehaviorProfile,
    DraftProspectSpecialistBehaviorProfile,
    DraftProspectPrivateWorkout,
    DraftProspectProDayResult,
    DraftProspectRating,
    DraftProspectRoleScore,
    create_draft_class,
    replace_prospect_combine_result,
    replace_prospect_qb_behavior_profile,
    replace_prospect_rb_behavior_profile,
    replace_prospect_receiver_behavior_profile,
    replace_prospect_ol_behavior_profile,
    replace_prospect_edge_behavior_profile,
    replace_prospect_idl_behavior_profile,
    replace_prospect_lb_behavior_profile,
    replace_prospect_secondary_behavior_profile,
    replace_prospect_specialist_behavior_profile,
    replace_prospect_private_workout,
    replace_prospect_pro_day_result,
    replace_prospect_sim_profile,
)
from .schema import ensure_schema
from .senior_bowl import senior_bowl_status
from engine.qb_behavior import generated_qb_behavior_profile
from engine.rb_behavior import generated_rb_behavior_profile
from engine.receiver_behavior import generated_receiver_behavior_profile
from engine.ol_behavior import generated_ol_behavior_profile
from engine.edge_behavior import generated_edge_behavior_profile
from engine.idl_behavior import generated_idl_behavior_profile
from engine.lb_behavior import generated_lb_behavior_profile
from engine.secondary_behavior import generated_secondary_behavior_profile
from engine.specialist_behavior import generated_specialist_behavior_profile


@dataclass(frozen=True)
class PersistDraftClassResult:
    draft_class_id: int
    draft_year: int
    prospect_count: int
    replaced_existing: bool


def persist_draft_class(
    con: sqlite3.Connection,
    rows: list[DraftClassPreviewRow],
    *,
    draft_year: int,
    class_strength: int,
    generation_seed: str,
    class_name: str | None = None,
    notes: str | None = None,
    force: bool = False,
) -> PersistDraftClassResult:
    """Persist a generated draft class and all generated prospect side tables."""

    if not rows:
        raise ValueError("No draft prospects to persist.")
    ensure_schema(con)
    existing = con.execute(
        "SELECT draft_class_id FROM draft_classes WHERE draft_year = ?",
        (draft_year,),
    ).fetchone()
    replaced_existing = False
    if existing:
        existing_count = con.execute(
            "SELECT COUNT(*) FROM draft_prospects WHERE draft_class_id = ?",
            (int(existing[0]),),
        ).fetchone()[0]
        if existing_count and not force:
            raise ValueError(
                f"Draft class {draft_year} already has {existing_count} prospects. "
                "Use --force to replace it."
            )
        replaced_existing = bool(existing_count)

    draft_class_id = create_draft_class(
        con,
        DraftClass(
            draft_year=draft_year,
            class_name=class_name or f"{draft_year} NFL Draft Class",
            class_strength=class_strength,
            generation_seed=generation_seed,
            status="Scouting",
            notes=notes,
        ),
    )
    if force:
        con.execute(
            "DELETE FROM draft_class_personality_runs WHERE draft_class_id = ?",
            (draft_class_id,),
        )
        con.execute("DELETE FROM draft_prospects WHERE draft_class_id = ?", (draft_class_id,))

    for row in rows:
        prospect_id = _insert_prospect(con, draft_class_id, row)
        replace_prospect_sim_profile(
            con,
            prospect_id,
            ratings=[
                DraftProspectRating(key, value, confidence="high", notes="Generated hidden true rating.")
                for key, value in sorted(row.ratings.items())
            ],
            role_scores=[
                DraftProspectRoleScore(role_key=key, role_score=value)
                for key, value in sorted(row.role_scores.items())
            ],
            roles=[row.primary_role, row.secondary_role],
            ensure=False,
        )
        if row.position.upper() == "QB":
            qb_profile = generated_qb_behavior_profile(row.archetype, row.ratings)
            replace_prospect_qb_behavior_profile(
                con,
                prospect_id,
                DraftProspectQBBehaviorProfile(
                    label=qb_profile.label,
                    rhythm=int(round(qb_profile.rhythm)),
                    pocket_discipline=int(round(qb_profile.pocket_discipline)),
                    pocket_drift=int(round(qb_profile.pocket_drift)),
                    checkdown_willingness=int(round(qb_profile.checkdown_willingness)),
                    deep_aggression=int(round(qb_profile.deep_aggression)),
                    pressure_escape=int(round(qb_profile.pressure_escape)),
                    broken_play_creation=int(round(qb_profile.broken_play_creation)),
                    scramble_trigger=int(round(qb_profile.scramble_trigger)),
                    sack_risk=int(round(qb_profile.sack_risk)),
                    throwaway_discipline=int(round(qb_profile.throwaway_discipline)),
                    notes=qb_profile.notes,
                ),
                ensure=False,
            )
        if row.position.upper() in {"RB", "FB"}:
            rb_profile = generated_rb_behavior_profile(row.archetype, row.ratings, position=row.position.upper())
            replace_prospect_rb_behavior_profile(
                con,
                prospect_id,
                DraftProspectRBBehaviorProfile(
                    label=rb_profile.label,
                    early_down_gravity=int(round(rb_profile.early_down_gravity)),
                    patience=int(round(rb_profile.patience)),
                    one_cut_decisiveness=int(round(rb_profile.one_cut_decisiveness)),
                    bounce_tendency=int(round(rb_profile.bounce_tendency)),
                    home_run_hunting=int(round(rb_profile.home_run_hunting)),
                    contact_appetite=int(round(rb_profile.contact_appetite)),
                    space_creation=int(round(rb_profile.space_creation)),
                    pass_game_usage=int(round(rb_profile.pass_game_usage)),
                    short_yardage_trust=int(round(rb_profile.short_yardage_trust)),
                    ball_security_mindset=int(round(rb_profile.ball_security_mindset)),
                    notes=rb_profile.notes,
                ),
                ensure=False,
            )
        if row.position.upper() in {"WR", "TE"}:
            receiver_profile = generated_receiver_behavior_profile(row.archetype, row.ratings, position=row.position.upper())
            replace_prospect_receiver_behavior_profile(
                con,
                prospect_id,
                DraftProspectReceiverBehaviorProfile(
                    label=receiver_profile.label,
                    target_gravity=int(round(receiver_profile.target_gravity)),
                    release_urgency=int(round(receiver_profile.release_urgency)),
                    route_pacing=int(round(receiver_profile.route_pacing)),
                    vertical_intent=int(round(receiver_profile.vertical_intent)),
                    middle_comfort=int(round(receiver_profile.middle_comfort)),
                    contested_alpha=int(round(receiver_profile.contested_alpha)),
                    sideline_awareness=int(round(receiver_profile.sideline_awareness)),
                    yac_intent=int(round(receiver_profile.yac_intent)),
                    scramble_drill=int(round(receiver_profile.scramble_drill)),
                    catch_security=int(round(receiver_profile.catch_security)),
                    notes=receiver_profile.notes,
                ),
                ensure=False,
            )
        if row.position.upper() in {"OT", "OG", "C"}:
            ol_profile = generated_ol_behavior_profile(row.archetype, row.ratings, position=row.position.upper())
            replace_prospect_ol_behavior_profile(
                con,
                prospect_id,
                DraftProspectOLBehaviorProfile(
                    label=ol_profile.label,
                    pass_set_patience=int(round(ol_profile.pass_set_patience)),
                    mirror_vs_speed=int(round(ol_profile.mirror_vs_speed)),
                    anchor_vs_power=int(round(ol_profile.anchor_vs_power)),
                    hand_timing=int(round(ol_profile.hand_timing)),
                    stunt_awareness=int(round(ol_profile.stunt_awareness)),
                    drive_finish=int(round(ol_profile.drive_finish)),
                    reach_range=int(round(ol_profile.reach_range)),
                    combo_timing=int(round(ol_profile.combo_timing)),
                    second_level_climb=int(round(ol_profile.second_level_climb)),
                    penalty_control=int(round(ol_profile.penalty_control)),
                    notes=ol_profile.notes,
                ),
                ensure=False,
            )
        if row.position.upper() in {"EDGE", "OLB"}:
            edge_profile = generated_edge_behavior_profile(row.archetype, row.ratings, position=row.position.upper())
            replace_prospect_edge_behavior_profile(
                con,
                prospect_id,
                DraftProspectEdgeBehaviorProfile(
                    label=edge_profile.label,
                    getoff_timing=int(round(edge_profile.getoff_timing)),
                    speed_arc=int(round(edge_profile.speed_arc)),
                    power_collapse=int(round(edge_profile.power_collapse)),
                    counter_plan=int(round(edge_profile.counter_plan)),
                    stunt_timing=int(round(edge_profile.stunt_timing)),
                    contain_discipline=int(round(edge_profile.contain_discipline)),
                    run_squeeze=int(round(edge_profile.run_squeeze)),
                    backside_pursuit=int(round(edge_profile.backside_pursuit)),
                    finish_skill=int(round(edge_profile.finish_skill)),
                    rush_discipline=int(round(edge_profile.rush_discipline)),
                    notes=edge_profile.notes,
                ),
                ensure=False,
            )
        if row.position.upper() in {"IDL", "DT", "NT"}:
            idl_profile = generated_idl_behavior_profile(row.archetype, row.ratings, position=row.position.upper())
            replace_prospect_idl_behavior_profile(
                con,
                prospect_id,
                DraftProspectIDLBehaviorProfile(
                    label=idl_profile.label,
                    getoff_timing=int(round(idl_profile.getoff_timing)),
                    penetration_burst=int(round(idl_profile.penetration_burst)),
                    power_collapse=int(round(idl_profile.power_collapse)),
                    double_team_anchor=int(round(idl_profile.double_team_anchor)),
                    gap_control=int(round(idl_profile.gap_control)),
                    block_shed_timing=int(round(idl_profile.block_shed_timing)),
                    stunt_timing=int(round(idl_profile.stunt_timing)),
                    rush_counter_plan=int(round(idl_profile.rush_counter_plan)),
                    finish_skill=int(round(idl_profile.finish_skill)),
                    rush_discipline=int(round(idl_profile.rush_discipline)),
                    notes=idl_profile.notes,
                ),
                ensure=False,
            )
        if row.position.upper() in {"ILB", "LB", "OLB"}:
            lb_profile = generated_lb_behavior_profile(row.archetype, row.ratings, position=row.position.upper())
            replace_prospect_lb_behavior_profile(
                con,
                prospect_id,
                DraftProspectLBBehaviorProfile(
                    label=lb_profile.label,
                    trigger_quickness=int(round(lb_profile.trigger_quickness)),
                    gap_fit_discipline=int(round(lb_profile.gap_fit_discipline)),
                    scrape_range=int(round(lb_profile.scrape_range)),
                    traffic_navigation=int(round(lb_profile.traffic_navigation)),
                    zone_landmark_depth=int(round(lb_profile.zone_landmark_depth)),
                    man_match_carry=int(round(lb_profile.man_match_carry)),
                    blitz_timing=int(round(lb_profile.blitz_timing)),
                    tackle_finish=int(round(lb_profile.tackle_finish)),
                    rally_support=int(round(lb_profile.rally_support)),
                    penalty_control=int(round(lb_profile.penalty_control)),
                    notes=lb_profile.notes,
                ),
                ensure=False,
            )
        if row.position.upper() in {"CB", "NB", "FS", "SS", "S"}:
            secondary_profile = generated_secondary_behavior_profile(
                row.archetype,
                row.ratings,
                position=row.position.upper(),
            )
            replace_prospect_secondary_behavior_profile(
                con,
                prospect_id,
                DraftProspectSecondaryBehaviorProfile(
                    label=secondary_profile.label,
                    press_timing=int(round(secondary_profile.press_timing)),
                    man_mirror=int(round(secondary_profile.man_mirror)),
                    zone_eye_discipline=int(round(secondary_profile.zone_eye_discipline)),
                    break_trigger=int(round(secondary_profile.break_trigger)),
                    deep_range=int(round(secondary_profile.deep_range)),
                    ball_play_timing=int(round(secondary_profile.ball_play_timing)),
                    catch_point_compete=int(round(secondary_profile.catch_point_compete)),
                    slot_traffic=int(round(secondary_profile.slot_traffic)),
                    run_support_fit=int(round(secondary_profile.run_support_fit)),
                    tackle_finish=int(round(secondary_profile.tackle_finish)),
                    penalty_control=int(round(secondary_profile.penalty_control)),
                    notes=secondary_profile.notes,
                ),
                ensure=False,
            )
        specialist_profile = generated_specialist_behavior_profile(
            row.archetype,
            row.ratings,
            position=row.position.upper(),
        )
        replace_prospect_specialist_behavior_profile(
            con,
            prospect_id,
            DraftProspectSpecialistBehaviorProfile(
                label=specialist_profile.label,
                kick_operation=int(round(specialist_profile.kick_operation)),
                kickoff_control=int(round(specialist_profile.kickoff_control)),
                punt_hang_time=int(round(specialist_profile.punt_hang_time)),
                punt_placement=int(round(specialist_profile.punt_placement)),
                snap_accuracy=int(round(specialist_profile.snap_accuracy)),
                lane_release=int(round(specialist_profile.lane_release)),
                gunner_speed=int(round(specialist_profile.gunner_speed)),
                return_lane_vision=int(round(specialist_profile.return_lane_vision)),
                block_timing=int(round(specialist_profile.block_timing)),
                coverage_tackle=int(round(specialist_profile.coverage_tackle)),
                penalty_control=int(round(specialist_profile.penalty_control)),
                notes=specialist_profile.notes,
            ),
            ensure=False,
        )
        replace_prospect_combine_result(
            con,
            prospect_id,
            DraftProspectCombineResult(
                combine_status=row.combine_status,
                participation_note=row.combine_note,
                combine_grade=row.combine_grade,
                athletic_score=row.athletic_score,
                drills_completed=row.drills_completed,
                drills_skipped=row.drills_skipped,
                workout_variance=row.workout_variance,
                forty_yard_dash=row.forty_yard_dash,
                ten_yard_split=row.ten_yard_split,
                bench_press_reps=row.bench_press_reps,
                vertical_jump_in=row.vertical_jump_in,
                broad_jump_in=row.broad_jump_in,
                three_cone_sec=row.three_cone_sec,
                twenty_yard_shuttle_sec=row.twenty_yard_shuttle_sec,
                sixty_yard_shuttle_sec=row.sixty_yard_shuttle_sec,
                is_injured=row.combine_injured,
                is_top_skip=row.combine_top_skip,
            ),
            ensure=False,
        )
        replace_prospect_pro_day_result(
            con,
            prospect_id,
            DraftProspectProDayResult(
                pro_day_status=row.pro_day_status,
                participation_note=row.pro_day_note,
                pro_day_grade=row.pro_day_grade,
                athletic_score=row.pro_day_athletic_score,
                drills_completed=row.pro_day_drills_completed,
                drills_skipped=row.pro_day_drills_skipped,
                workout_variance=row.pro_day_workout_variance,
                summary=row.pro_day_summary,
                forty_yard_dash=row.pro_day_forty_yard_dash,
                ten_yard_split=row.pro_day_ten_yard_split,
                bench_press_reps=row.pro_day_bench_press_reps,
                vertical_jump_in=row.pro_day_vertical_jump_in,
                broad_jump_in=row.pro_day_broad_jump_in,
                three_cone_sec=row.pro_day_three_cone_sec,
                twenty_yard_shuttle_sec=row.pro_day_twenty_yard_shuttle_sec,
                sixty_yard_shuttle_sec=row.pro_day_sixty_yard_shuttle_sec,
                improved_from_combine=row.pro_day_improved_from_combine,
                medical_recheck=row.pro_day_medical_recheck,
            ),
            ensure=False,
        )
        replace_prospect_private_workout(
            con,
            prospect_id,
            DraftProspectPrivateWorkout(
                status=row.private_workout_status,
                workout_type=row.private_workout_type,
                interest_level=row.private_workout_interest,
                outcome_grade=row.private_workout_grade,
                notes=row.private_workout_note,
                hidden=True,
            ),
            ensure=False,
        )

    con.execute(
        "UPDATE draft_classes SET updated_at = datetime('now') WHERE draft_class_id = ?",
        (draft_class_id,),
    )
    return PersistDraftClassResult(
        draft_class_id=draft_class_id,
        draft_year=draft_year,
        prospect_count=len(rows),
        replaced_existing=replaced_existing,
    )


def _insert_prospect(
    con: sqlite3.Connection,
    draft_class_id: int,
    row: DraftClassPreviewRow,
) -> int:
    legacy = _legacy_column_values(row.ratings)
    prospect_key = f"{row.true_rank}:{row.public_board_rank}:{row.first_name}:{row.last_name}:{row.college}"
    senior_bowl = senior_bowl_status(
        age=row.age,
        prospect_key=prospect_key,
        public_board_rank=row.public_board_rank,
        public_board_status=row.public_board_status,
        projected_round=row.projected_round,
        college_tier=row.college_tier,
        position=row.position,
        combine_injured=row.combine_injured,
        combine_top_skip=row.combine_top_skip,
        scout_grade=row.scout_grade,
    )
    values = {
        "draft_class_id": draft_class_id,
        "first_name": row.first_name,
        "last_name": row.last_name,
        "ethnicity_key": row.ethnicity_key,
        "ethnicity_label": row.primary_ethnicity,
        "secondary_ethnicity_label": row.secondary_ethnicity or None,
        "ethnicity_note": row.ethnicity,
        "birth_country": row.birth_country,
        "is_international": int(row.is_international),
        "origin_ethnicity_key": row.origin_ethnicity_key,
        "position_group": row.position_group,
        "generation_version": row.generation_version,
        "true_rank": row.true_rank,
        "public_board_rank": row.public_board_rank,
        "public_board_status": row.public_board_status,
        "discovery_status": row.discovery_status,
        "scouting_variance": row.scouting_variance,
        "discovery_notes": row.discovery_notes,
        "eye_color": row.eye_color,
        "hair_color": row.hair_color,
        "hairstyle": row.hairstyle,
        "hairstyle_outlier": int(row.hairstyle_outlier),
        "facial_hair_style": row.facial_hair,
        "has_mustache": int(row.has_mustache),
        "has_beard": int(row.has_beard),
        "appearance_notes": row.photo_prompt_traits,
        "position": row.position,
        "college": row.college,
        "college_tier": row.college_tier,
        "hometown": row.hometown,
        "hometown_city": row.hometown_city,
        "hometown_state": row.hometown_state,
        "hometown_region": row.hometown_region,
        "age": row.age,
        "college_class": senior_bowl.college_class,
        "senior_bowl_eligible": int(senior_bowl.eligible),
        "senior_bowl_invited": int(senior_bowl.invited),
        "senior_bowl_accepted": int(senior_bowl.accepted),
        "senior_bowl_result": senior_bowl.result,
        "senior_bowl_notes": senior_bowl.notes,
        "height_in": row.height_in,
        "weight_lbs": row.weight_lbs,
        "arm_length_in": row.arm_length_in,
        "hand_size_in": row.hand_size_in,
        "handedness": row.handedness,
        "archetype": row.archetype,
        "original_archetype": row.original_archetype,
        "archetype_identity_status": row.archetype_identity_status,
        "archetype_identity_note": row.archetype_identity_note,
        "true_grade": row.true_grade,
        "ceiling_grade": row.ceiling_grade,
        "primary_role": row.primary_role,
        "secondary_role": row.secondary_role,
        "normalized_rating_version": row.generation_version,
        "overall": row.true_grade,
        "potential": row.ceiling_grade,
        "dev_trait": row.dev_trait,
        "speed": legacy["speed"],
        "strength": legacy["strength"],
        "agility": legacy["agility"],
        "awareness": legacy["awareness"],
        "injury_prone": legacy["injury_prone"],
        "throw_power": legacy["throw_power"],
        "throw_acc": legacy["throw_acc"],
        "route_running": legacy["route_running"],
        "catching": legacy["catching"],
        "run_blocking": legacy["run_blocking"],
        "pass_blocking": legacy["pass_blocking"],
        "trucking": legacy["trucking"],
        "tackle": legacy["tackle"],
        "pass_rush": legacy["pass_rush"],
        "coverage": legacy["coverage"],
        "kick_power": legacy["kick_power"],
        "kick_acc": legacy["kick_acc"],
        "scouting_rank": row.scouting_rank,
        "scout_lens": row.scout_lens,
        "scout_confidence": row.scout_confidence,
        "scout_grade": row.scout_grade,
        "scout_ceiling": row.scout_ceiling,
        "scout_risk": row.scout_risk,
        "scouting_strengths": row.scouting_strengths,
        "scouting_concerns": row.scouting_concerns,
        "scouting_projection": row.scouting_projection,
        "scouting_report": row.scouting_report,
        "medical_flag": row.medical_flag,
        "medical_risk": row.medical_risk,
        "medical_notes": row.medical_notes,
        "interview_trait": row.interview_trait,
        "interview_grade": row.interview_grade,
        "interview_notes": row.interview_notes,
        "late_process_status": row.late_process_status,
        "late_process_note": row.late_process_note,
        "public_board_delta": row.public_board_delta,
        "projected_round": row.projected_round,
        "projected_pick": row.projected_pick,
        "scouting_summary": row.scouting_summary,
        "risk_level": row.risk_level,
    }
    columns = list(values)
    placeholders = ", ".join("?" for _ in columns)
    cur = con.execute(
        f"""
        INSERT INTO draft_prospects ({", ".join(columns)})
        VALUES ({placeholders})
        """,
        [values[column] for column in columns],
    )
    return int(cur.lastrowid)


def _legacy_column_values(ratings: dict[str, int]) -> dict[str, int | None]:
    def rating(key: str, default: int | None = 50) -> int | None:
        value = ratings.get(key)
        if value is None:
            return default
        return int(value)

    return {
        "speed": rating("speed"),
        "strength": rating("strength"),
        "agility": rating("agility"),
        "awareness": _avg_rating(ratings, "play_recognition", "processing_speed", "discipline"),
        "injury_prone": clamp(100 - rating("durability", 50), 1, 99),
        "throw_power": rating("throw_power", None),
        "throw_acc": _avg_rating(ratings, "pass_accuracy_short", "pass_accuracy_mid", "pass_accuracy_deep"),
        "route_running": _avg_rating(ratings, "release_vs_press", "route_snap", "route_timing"),
        "catching": _avg_rating(ratings, "hands", "contested_catch", "catch_in_traffic"),
        "run_blocking": _avg_rating(ratings, "run_block_drive", "reach_block", "lead_block", "block_sustain"),
        "pass_blocking": _avg_rating(ratings, "pass_block_power", "pass_block_finesse", "pass_block_speed"),
        "trucking": rating("contact_power", None),
        "tackle": _avg_rating(ratings, "solo_tackle", "tackle_wrap", "open_field_tackle"),
        "pass_rush": _avg_rating(ratings, "power_rush", "finesse_rush", "speed_rush", "rush_plan"),
        "coverage": _avg_rating(ratings, "press_coverage", "man_coverage", "zone_coverage", "ball_skills"),
        "kick_power": rating("kick_power", None),
        "kick_acc": rating("kick_accuracy", None),
    }


def _avg_rating(ratings: dict[str, int], *keys: str) -> int | None:
    values = [int(ratings[key]) for key in keys if key in ratings]
    if not values:
        return None
    return int(round(sum(values) / len(values)))
