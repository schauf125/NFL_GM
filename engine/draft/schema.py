"""SQLite schema for draft classes and draft prospects.

The draft domain deliberately keeps prospects separate from players until a
draft pick is made. That lets scouting, rankings, and generation evolve without
polluting active rosters or free-agent pools.
"""

from __future__ import annotations

import sqlite3


TABLE_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS draft_classes (
    draft_class_id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_year INTEGER NOT NULL UNIQUE,
    class_name TEXT NOT NULL,
    class_strength INTEGER NOT NULL DEFAULT 50,
    generation_seed TEXT,
    status TEXT NOT NULL DEFAULT 'Generated'
        CHECK (status IN ('Generated', 'Scouting', 'Finalized', 'Drafted', 'Archived')),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_prospects (
    prospect_id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_class_id INTEGER NOT NULL REFERENCES draft_classes(draft_class_id) ON DELETE CASCADE,
    player_id INTEGER REFERENCES players(player_id) ON DELETE SET NULL,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    ethnicity_key TEXT,
    ethnicity_label TEXT,
    secondary_ethnicity_label TEXT,
    ethnicity_note TEXT,
    birth_country TEXT NOT NULL DEFAULT 'United States',
    is_international INTEGER NOT NULL DEFAULT 0,
    origin_ethnicity_key TEXT,
    position_group TEXT,
    generation_version TEXT,
    true_rank INTEGER,
    public_board_rank INTEGER,
    public_board_status TEXT NOT NULL DEFAULT 'ranked',
    discovery_status TEXT NOT NULL DEFAULT 'public_board',
    scouting_variance INTEGER NOT NULL DEFAULT 0,
    discovery_notes TEXT,
    development_pathway TEXT,
    pipeline_note TEXT,
    display_name TEXT,
    preferred_name TEXT,
    name_pronunciation_note TEXT,
    name_background_note TEXT,
    family_football_type TEXT,
    family_football_background TEXT,
    name_storyline_note TEXT,
    eye_color TEXT,
    hair_color TEXT,
    hairstyle TEXT,
    hairstyle_outlier INTEGER NOT NULL DEFAULT 0,
    facial_hair_style TEXT,
    skin_tone TEXT,
    complexion TEXT,
    face_shape TEXT,
    jawline TEXT,
    brow_profile TEXT,
    nose_profile TEXT,
    smile_profile TEXT,
    media_style TEXT,
    accessory_style TEXT,
    has_mustache INTEGER NOT NULL DEFAULT 0,
    has_beard INTEGER NOT NULL DEFAULT 0,
    appearance_notes TEXT,
    position TEXT NOT NULL,
    college TEXT,
    college_tier TEXT,
    hometown TEXT,
    hometown_city TEXT,
    hometown_state TEXT,
    hometown_region TEXT,
    age INTEGER,
    college_class TEXT,
    senior_bowl_eligible INTEGER NOT NULL DEFAULT 0,
    senior_bowl_invited INTEGER NOT NULL DEFAULT 0,
    senior_bowl_accepted INTEGER NOT NULL DEFAULT 0,
    senior_bowl_result TEXT,
    senior_bowl_notes TEXT,
    height_in INTEGER,
    weight_lbs INTEGER,
    arm_length_in REAL,
    hand_size_in REAL,
    handedness TEXT,
    archetype TEXT,
    original_archetype TEXT,
    archetype_identity_status TEXT,
    archetype_identity_note TEXT,
    true_grade INTEGER,
    ceiling_grade INTEGER,
    primary_role TEXT,
    secondary_role TEXT,
    normalized_rating_version TEXT,
    overall INTEGER NOT NULL DEFAULT 50,
    potential INTEGER NOT NULL DEFAULT 50,
    dev_trait TEXT NOT NULL DEFAULT 'Normal',
    speed INTEGER NOT NULL DEFAULT 50,
    strength INTEGER NOT NULL DEFAULT 50,
    agility INTEGER NOT NULL DEFAULT 50,
    awareness INTEGER NOT NULL DEFAULT 50,
    injury_prone INTEGER NOT NULL DEFAULT 50,
    throw_power INTEGER,
    throw_acc INTEGER,
    route_running INTEGER,
    catching INTEGER,
    run_blocking INTEGER,
    pass_blocking INTEGER,
    trucking INTEGER,
    tackle INTEGER,
    pass_rush INTEGER,
    coverage INTEGER,
    kick_power INTEGER,
    kick_acc INTEGER,
    scouting_rank INTEGER,
    scout_lens TEXT,
    scout_confidence TEXT,
    scout_grade INTEGER,
    scout_ceiling INTEGER,
    scout_risk TEXT,
    scouting_strengths TEXT,
    scouting_concerns TEXT,
    scouting_projection TEXT,
    scouting_report TEXT,
    medical_flag TEXT,
    medical_risk TEXT,
    medical_notes TEXT,
    interview_trait TEXT,
    interview_grade INTEGER,
    interview_notes TEXT,
    late_process_status TEXT,
    late_process_note TEXT,
    public_board_delta INTEGER NOT NULL DEFAULT 0,
    projected_round INTEGER,
    projected_pick INTEGER,
    scheme_fit TEXT,
    scouting_summary TEXT,
    risk_level TEXT NOT NULL DEFAULT 'Medium'
        CHECK (risk_level IN ('Low', 'Medium', 'High')),
    status TEXT NOT NULL DEFAULT 'Available'
        CHECK (status IN ('Available', 'Withdrawn', 'Drafted', 'Signed', 'Archived')),
    selected_pick_id INTEGER REFERENCES draft_picks(pick_id) ON DELETE SET NULL,
    selected_team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(draft_class_id, first_name, last_name, college)
);

CREATE TABLE IF NOT EXISTS draft_prospect_scouting_notes (
    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id INTEGER NOT NULL REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    note_date TEXT NOT NULL DEFAULT (date('now')),
    source TEXT NOT NULL DEFAULT 'user',
    grade INTEGER,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_prospect_combine_results (
    prospect_id INTEGER PRIMARY KEY REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    combine_status TEXT NOT NULL DEFAULT 'Not invited',
    participation_note TEXT,
    combine_grade INTEGER CHECK (combine_grade BETWEEN 0 AND 100),
    athletic_score INTEGER CHECK (athletic_score BETWEEN 0 AND 100),
    drills_completed INTEGER NOT NULL DEFAULT 0,
    drills_skipped TEXT,
    workout_variance TEXT,
    forty_yard_dash REAL,
    ten_yard_split REAL,
    bench_press_reps INTEGER,
    vertical_jump_in REAL,
    broad_jump_in INTEGER,
    three_cone_sec REAL,
    twenty_yard_shuttle_sec REAL,
    sixty_yard_shuttle_sec REAL,
    is_injured INTEGER NOT NULL DEFAULT 0,
    is_top_skip INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'draft_generator',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_prospect_pro_day_results (
    prospect_id INTEGER PRIMARY KEY REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    pro_day_status TEXT NOT NULL DEFAULT 'No pro day data',
    participation_note TEXT,
    pro_day_grade INTEGER CHECK (pro_day_grade BETWEEN 0 AND 100),
    athletic_score INTEGER CHECK (athletic_score BETWEEN 0 AND 100),
    drills_completed INTEGER NOT NULL DEFAULT 0,
    drills_skipped TEXT,
    workout_variance TEXT,
    summary TEXT,
    forty_yard_dash REAL,
    ten_yard_split REAL,
    bench_press_reps INTEGER,
    vertical_jump_in REAL,
    broad_jump_in INTEGER,
    three_cone_sec REAL,
    twenty_yard_shuttle_sec REAL,
    sixty_yard_shuttle_sec REAL,
    improved_from_combine INTEGER NOT NULL DEFAULT 0,
    medical_recheck INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'draft_generator',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_prospect_private_workouts (
    workout_id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id INTEGER NOT NULL REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    team_id INTEGER REFERENCES teams(team_id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'None logged',
    workout_type TEXT NOT NULL DEFAULT 'None',
    interest_level TEXT NOT NULL DEFAULT 'Normal',
    outcome_grade INTEGER CHECK (outcome_grade BETWEEN 0 AND 100),
    hidden INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    source TEXT NOT NULL DEFAULT 'draft_generator',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_class_personality_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_class_id INTEGER NOT NULL REFERENCES draft_classes(draft_class_id) ON DELETE CASCADE,
    draft_year INTEGER NOT NULL,
    rng_seed TEXT NOT NULL,
    prospect_count INTEGER NOT NULL DEFAULT 0,
    zero_trait_count INTEGER NOT NULL DEFAULT 0,
    one_trait_count INTEGER NOT NULL DEFAULT 0,
    two_trait_count INTEGER NOT NULL DEFAULT 0,
    three_trait_count INTEGER NOT NULL DEFAULT 0,
    total_assignment_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(draft_class_id)
);

CREATE TABLE IF NOT EXISTS draft_prospect_personalities (
    prospect_id INTEGER NOT NULL REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    trait_key TEXT NOT NULL REFERENCES personality_trait_definitions(trait_key) ON DELETE CASCADE,
    intensity INTEGER NOT NULL CHECK (intensity BETWEEN 1 AND 100),
    assignment_type TEXT NOT NULL DEFAULT 'generated',
    hidden INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'draft_personality_generator',
    notes TEXT,
    run_id INTEGER REFERENCES draft_class_personality_runs(run_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (prospect_id, trait_key)
);

CREATE TABLE IF NOT EXISTS draft_prospect_ratings (
    prospect_id INTEGER NOT NULL REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    rating_key TEXT NOT NULL REFERENCES rating_definitions(rating_key) ON DELETE CASCADE,
    rating_value INTEGER NOT NULL CHECK (rating_value BETWEEN 0 AND 100),
    confidence TEXT NOT NULL DEFAULT 'low'
        CHECK (confidence IN ('low', 'medium', 'high')),
    source TEXT NOT NULL DEFAULT 'draft_generator',
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (prospect_id, rating_key)
);

CREATE TABLE IF NOT EXISTS draft_prospect_role_assignments (
    assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id INTEGER NOT NULL REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    role_key TEXT NOT NULL REFERENCES role_score_definitions(role_key) ON DELETE CASCADE,
    priority INTEGER NOT NULL CHECK (priority BETWEEN 1 AND 5),
    source TEXT NOT NULL DEFAULT 'draft_generator',
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(prospect_id, role_key),
    UNIQUE(prospect_id, priority)
);

CREATE TABLE IF NOT EXISTS draft_prospect_role_scores (
    prospect_id INTEGER NOT NULL REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    role_key TEXT NOT NULL REFERENCES role_score_definitions(role_key) ON DELETE CASCADE,
    scheme_key TEXT NOT NULL DEFAULT 'default',
    role_score REAL NOT NULL CHECK (role_score BETWEEN 0 AND 100),
    source TEXT NOT NULL DEFAULT 'draft_generator',
    calculated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (prospect_id, role_key, scheme_key)
);

CREATE TABLE IF NOT EXISTS draft_prospect_qb_behavior_profiles (
    prospect_id INTEGER PRIMARY KEY REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    rhythm INTEGER NOT NULL CHECK (rhythm BETWEEN 0 AND 100),
    pocket_discipline INTEGER NOT NULL CHECK (pocket_discipline BETWEEN 0 AND 100),
    pocket_drift INTEGER NOT NULL CHECK (pocket_drift BETWEEN 0 AND 100),
    checkdown_willingness INTEGER NOT NULL CHECK (checkdown_willingness BETWEEN 0 AND 100),
    deep_aggression INTEGER NOT NULL CHECK (deep_aggression BETWEEN 0 AND 100),
    pressure_escape INTEGER NOT NULL CHECK (pressure_escape BETWEEN 0 AND 100),
    broken_play_creation INTEGER NOT NULL CHECK (broken_play_creation BETWEEN 0 AND 100),
    scramble_trigger INTEGER NOT NULL CHECK (scramble_trigger BETWEEN 0 AND 100),
    sack_risk INTEGER NOT NULL CHECK (sack_risk BETWEEN 0 AND 100),
    throwaway_discipline INTEGER NOT NULL CHECK (throwaway_discipline BETWEEN 0 AND 100),
    source TEXT NOT NULL DEFAULT 'draft_generator',
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_prospect_rb_behavior_profiles (
    prospect_id INTEGER PRIMARY KEY REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    early_down_gravity INTEGER NOT NULL CHECK (early_down_gravity BETWEEN 0 AND 100),
    patience INTEGER NOT NULL CHECK (patience BETWEEN 0 AND 100),
    one_cut_decisiveness INTEGER NOT NULL CHECK (one_cut_decisiveness BETWEEN 0 AND 100),
    bounce_tendency INTEGER NOT NULL CHECK (bounce_tendency BETWEEN 0 AND 100),
    home_run_hunting INTEGER NOT NULL CHECK (home_run_hunting BETWEEN 0 AND 100),
    contact_appetite INTEGER NOT NULL CHECK (contact_appetite BETWEEN 0 AND 100),
    space_creation INTEGER NOT NULL CHECK (space_creation BETWEEN 0 AND 100),
    pass_game_usage INTEGER NOT NULL CHECK (pass_game_usage BETWEEN 0 AND 100),
    short_yardage_trust INTEGER NOT NULL CHECK (short_yardage_trust BETWEEN 0 AND 100),
    ball_security_mindset INTEGER NOT NULL CHECK (ball_security_mindset BETWEEN 0 AND 100),
    source TEXT NOT NULL DEFAULT 'draft_generator',
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_prospect_receiver_behavior_profiles (
    prospect_id INTEGER PRIMARY KEY REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    target_gravity INTEGER NOT NULL CHECK (target_gravity BETWEEN 0 AND 100),
    release_urgency INTEGER NOT NULL CHECK (release_urgency BETWEEN 0 AND 100),
    route_pacing INTEGER NOT NULL CHECK (route_pacing BETWEEN 0 AND 100),
    vertical_intent INTEGER NOT NULL CHECK (vertical_intent BETWEEN 0 AND 100),
    middle_comfort INTEGER NOT NULL CHECK (middle_comfort BETWEEN 0 AND 100),
    contested_alpha INTEGER NOT NULL CHECK (contested_alpha BETWEEN 0 AND 100),
    sideline_awareness INTEGER NOT NULL CHECK (sideline_awareness BETWEEN 0 AND 100),
    yac_intent INTEGER NOT NULL CHECK (yac_intent BETWEEN 0 AND 100),
    scramble_drill INTEGER NOT NULL CHECK (scramble_drill BETWEEN 0 AND 100),
    catch_security INTEGER NOT NULL CHECK (catch_security BETWEEN 0 AND 100),
    source TEXT NOT NULL DEFAULT 'draft_generator',
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_prospect_ol_behavior_profiles (
    prospect_id INTEGER PRIMARY KEY REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    pass_set_patience INTEGER NOT NULL CHECK (pass_set_patience BETWEEN 0 AND 100),
    mirror_vs_speed INTEGER NOT NULL CHECK (mirror_vs_speed BETWEEN 0 AND 100),
    anchor_vs_power INTEGER NOT NULL CHECK (anchor_vs_power BETWEEN 0 AND 100),
    hand_timing INTEGER NOT NULL CHECK (hand_timing BETWEEN 0 AND 100),
    stunt_awareness INTEGER NOT NULL CHECK (stunt_awareness BETWEEN 0 AND 100),
    drive_finish INTEGER NOT NULL CHECK (drive_finish BETWEEN 0 AND 100),
    reach_range INTEGER NOT NULL CHECK (reach_range BETWEEN 0 AND 100),
    combo_timing INTEGER NOT NULL CHECK (combo_timing BETWEEN 0 AND 100),
    second_level_climb INTEGER NOT NULL CHECK (second_level_climb BETWEEN 0 AND 100),
    penalty_control INTEGER NOT NULL CHECK (penalty_control BETWEEN 0 AND 100),
    source TEXT NOT NULL DEFAULT 'draft_generator',
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_prospect_edge_behavior_profiles (
    prospect_id INTEGER PRIMARY KEY REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    getoff_timing INTEGER NOT NULL CHECK (getoff_timing BETWEEN 0 AND 100),
    speed_arc INTEGER NOT NULL CHECK (speed_arc BETWEEN 0 AND 100),
    power_collapse INTEGER NOT NULL CHECK (power_collapse BETWEEN 0 AND 100),
    counter_plan INTEGER NOT NULL CHECK (counter_plan BETWEEN 0 AND 100),
    stunt_timing INTEGER NOT NULL CHECK (stunt_timing BETWEEN 0 AND 100),
    contain_discipline INTEGER NOT NULL CHECK (contain_discipline BETWEEN 0 AND 100),
    run_squeeze INTEGER NOT NULL CHECK (run_squeeze BETWEEN 0 AND 100),
    backside_pursuit INTEGER NOT NULL CHECK (backside_pursuit BETWEEN 0 AND 100),
    finish_skill INTEGER NOT NULL CHECK (finish_skill BETWEEN 0 AND 100),
    rush_discipline INTEGER NOT NULL CHECK (rush_discipline BETWEEN 0 AND 100),
    source TEXT NOT NULL DEFAULT 'draft_generator',
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_prospect_idl_behavior_profiles (
    prospect_id INTEGER PRIMARY KEY REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    getoff_timing INTEGER NOT NULL CHECK (getoff_timing BETWEEN 0 AND 100),
    penetration_burst INTEGER NOT NULL CHECK (penetration_burst BETWEEN 0 AND 100),
    power_collapse INTEGER NOT NULL CHECK (power_collapse BETWEEN 0 AND 100),
    double_team_anchor INTEGER NOT NULL CHECK (double_team_anchor BETWEEN 0 AND 100),
    gap_control INTEGER NOT NULL CHECK (gap_control BETWEEN 0 AND 100),
    block_shed_timing INTEGER NOT NULL CHECK (block_shed_timing BETWEEN 0 AND 100),
    stunt_timing INTEGER NOT NULL CHECK (stunt_timing BETWEEN 0 AND 100),
    rush_counter_plan INTEGER NOT NULL CHECK (rush_counter_plan BETWEEN 0 AND 100),
    finish_skill INTEGER NOT NULL CHECK (finish_skill BETWEEN 0 AND 100),
    rush_discipline INTEGER NOT NULL CHECK (rush_discipline BETWEEN 0 AND 100),
    source TEXT NOT NULL DEFAULT 'draft_generator',
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_prospect_lb_behavior_profiles (
    prospect_id INTEGER PRIMARY KEY REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    trigger_quickness INTEGER NOT NULL CHECK (trigger_quickness BETWEEN 0 AND 100),
    gap_fit_discipline INTEGER NOT NULL CHECK (gap_fit_discipline BETWEEN 0 AND 100),
    scrape_range INTEGER NOT NULL CHECK (scrape_range BETWEEN 0 AND 100),
    traffic_navigation INTEGER NOT NULL CHECK (traffic_navigation BETWEEN 0 AND 100),
    zone_landmark_depth INTEGER NOT NULL CHECK (zone_landmark_depth BETWEEN 0 AND 100),
    man_match_carry INTEGER NOT NULL CHECK (man_match_carry BETWEEN 0 AND 100),
    blitz_timing INTEGER NOT NULL CHECK (blitz_timing BETWEEN 0 AND 100),
    tackle_finish INTEGER NOT NULL CHECK (tackle_finish BETWEEN 0 AND 100),
    rally_support INTEGER NOT NULL CHECK (rally_support BETWEEN 0 AND 100),
    penalty_control INTEGER NOT NULL CHECK (penalty_control BETWEEN 0 AND 100),
    source TEXT NOT NULL DEFAULT 'draft_generator',
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_prospect_secondary_behavior_profiles (
    prospect_id INTEGER PRIMARY KEY REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    press_timing INTEGER NOT NULL CHECK (press_timing BETWEEN 0 AND 100),
    man_mirror INTEGER NOT NULL CHECK (man_mirror BETWEEN 0 AND 100),
    zone_eye_discipline INTEGER NOT NULL CHECK (zone_eye_discipline BETWEEN 0 AND 100),
    break_trigger INTEGER NOT NULL CHECK (break_trigger BETWEEN 0 AND 100),
    deep_range INTEGER NOT NULL CHECK (deep_range BETWEEN 0 AND 100),
    ball_play_timing INTEGER NOT NULL CHECK (ball_play_timing BETWEEN 0 AND 100),
    catch_point_compete INTEGER NOT NULL CHECK (catch_point_compete BETWEEN 0 AND 100),
    slot_traffic INTEGER NOT NULL CHECK (slot_traffic BETWEEN 0 AND 100),
    run_support_fit INTEGER NOT NULL CHECK (run_support_fit BETWEEN 0 AND 100),
    tackle_finish INTEGER NOT NULL CHECK (tackle_finish BETWEEN 0 AND 100),
    penalty_control INTEGER NOT NULL CHECK (penalty_control BETWEEN 0 AND 100),
    source TEXT NOT NULL DEFAULT 'draft_generator',
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_prospect_specialist_behavior_profiles (
    prospect_id INTEGER PRIMARY KEY REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    kick_operation INTEGER NOT NULL CHECK (kick_operation BETWEEN 0 AND 100),
    kickoff_control INTEGER NOT NULL CHECK (kickoff_control BETWEEN 0 AND 100),
    punt_hang_time INTEGER NOT NULL CHECK (punt_hang_time BETWEEN 0 AND 100),
    punt_placement INTEGER NOT NULL CHECK (punt_placement BETWEEN 0 AND 100),
    snap_accuracy INTEGER NOT NULL CHECK (snap_accuracy BETWEEN 0 AND 100),
    lane_release INTEGER NOT NULL CHECK (lane_release BETWEEN 0 AND 100),
    gunner_speed INTEGER NOT NULL CHECK (gunner_speed BETWEEN 0 AND 100),
    return_lane_vision INTEGER NOT NULL CHECK (return_lane_vision BETWEEN 0 AND 100),
    block_timing INTEGER NOT NULL CHECK (block_timing BETWEEN 0 AND 100),
    coverage_tackle INTEGER NOT NULL CHECK (coverage_tackle BETWEEN 0 AND 100),
    penalty_control INTEGER NOT NULL CHECK (penalty_control BETWEEN 0 AND 100),
    source TEXT NOT NULL DEFAULT 'draft_generator',
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS draft_prospect_special_teams_flex (
    prospect_id INTEGER NOT NULL REFERENCES draft_prospects(prospect_id) ON DELETE CASCADE,
    role_key TEXT NOT NULL,
    experience INTEGER NOT NULL CHECK (experience BETWEEN 1 AND 10),
    potential INTEGER NOT NULL CHECK (potential BETWEEN 1 AND 10),
    source TEXT NOT NULL DEFAULT 'draft_generator',
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (prospect_id, role_key)
);

CREATE INDEX IF NOT EXISTS idx_draft_prospects_class_rank
    ON draft_prospects(draft_class_id, scouting_rank);

CREATE INDEX IF NOT EXISTS idx_draft_prospects_class_position
    ON draft_prospects(draft_class_id, position, scouting_rank);

CREATE INDEX IF NOT EXISTS idx_draft_prospects_status
    ON draft_prospects(status);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_combine_grade
    ON draft_prospect_combine_results(combine_grade);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_combine_status
    ON draft_prospect_combine_results(combine_status);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_pro_day_grade
    ON draft_prospect_pro_day_results(pro_day_grade);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_pro_day_status
    ON draft_prospect_pro_day_results(pro_day_status);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_private_workouts_prospect
    ON draft_prospect_private_workouts(prospect_id);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_private_workouts_team
    ON draft_prospect_private_workouts(team_id, prospect_id);

CREATE INDEX IF NOT EXISTS idx_draft_class_personality_runs_year
    ON draft_class_personality_runs(draft_year, run_id);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_personalities_trait
    ON draft_prospect_personalities(trait_key, prospect_id);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_personalities_run
    ON draft_prospect_personalities(run_id);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_ratings_key
    ON draft_prospect_ratings(rating_key, rating_value);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_role_scores_role
    ON draft_prospect_role_scores(role_key, role_score);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_qb_behavior_label
    ON draft_prospect_qb_behavior_profiles(label);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_rb_behavior_label
    ON draft_prospect_rb_behavior_profiles(label);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_receiver_behavior_label
    ON draft_prospect_receiver_behavior_profiles(label);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_ol_behavior_label
    ON draft_prospect_ol_behavior_profiles(label);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_edge_behavior_label
    ON draft_prospect_edge_behavior_profiles(label);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_idl_behavior_label
    ON draft_prospect_idl_behavior_profiles(label);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_lb_behavior_label
    ON draft_prospect_lb_behavior_profiles(label);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_secondary_behavior_label
    ON draft_prospect_secondary_behavior_profiles(label);

CREATE INDEX IF NOT EXISTS idx_draft_prospect_specialist_behavior_label
    ON draft_prospect_specialist_behavior_profiles(label);
"""

VIEW_SCHEMA_SQL = """
DROP VIEW IF EXISTS draft_class_summary_view;
CREATE VIEW IF NOT EXISTS draft_class_summary_view AS
SELECT
    dc.draft_class_id,
    dc.draft_year,
    dc.class_name,
    dc.class_strength,
    dc.generation_seed,
    dc.status,
    COUNT(dp.prospect_id) AS prospect_count,
    SUM(CASE WHEN dp.status = 'Available' THEN 1 ELSE 0 END) AS available_count,
    SUM(CASE WHEN dp.status = 'Drafted' THEN 1 ELSE 0 END) AS drafted_count,
    SUM(CASE WHEN dp.public_board_status = 'off_public_board' THEN 1 ELSE 0 END) AS off_public_board_count,
    ROUND(AVG(dp.true_grade), 1) AS avg_true_grade,
    ROUND(AVG(dp.ceiling_grade), 1) AS avg_ceiling_grade,
    MAX(dp.ceiling_grade) AS best_ceiling_grade,
    ROUND(AVG(dp.overall), 1) AS avg_overall,
    ROUND(AVG(dp.potential), 1) AS avg_potential,
    MAX(dp.potential) AS best_potential,
    dc.updated_at
FROM draft_classes dc
LEFT JOIN draft_prospects dp ON dp.draft_class_id = dc.draft_class_id
GROUP BY dc.draft_class_id;

DROP VIEW IF EXISTS draft_board_view;
CREATE VIEW IF NOT EXISTS draft_board_view AS
SELECT
    dp.prospect_id,
    dc.draft_year,
    dc.class_name,
    COALESCE(dp.public_board_rank, dp.scouting_rank) AS scouting_rank,
    dp.public_board_rank,
    dp.public_board_status,
    dp.discovery_status,
    dp.scouting_variance,
    dp.discovery_notes,
    dp.development_pathway,
    dp.pipeline_note,
    dp.display_name,
    dp.preferred_name,
    dp.name_pronunciation_note,
    dp.name_background_note,
    dp.family_football_type,
    dp.family_football_background,
    dp.name_storyline_note,
    dp.projected_round,
    dp.projected_pick,
    dp.first_name,
    dp.last_name,
    dp.ethnicity_key,
    dp.ethnicity_label,
    dp.secondary_ethnicity_label,
    dp.ethnicity_note,
    dp.birth_country,
    dp.is_international,
    dp.origin_ethnicity_key,
    dp.position_group,
    dp.generation_version,
    dp.eye_color,
    dp.hair_color,
    dp.hairstyle,
    dp.hairstyle_outlier,
    dp.facial_hair_style,
    dp.has_mustache,
    dp.has_beard,
    dp.position,
    dp.college,
    dp.college_tier,
    dp.hometown,
    dp.hometown_city,
    dp.hometown_state,
    dp.hometown_region,
    dp.age,
    dp.college_class,
    dp.senior_bowl_eligible,
    dp.senior_bowl_invited,
    dp.senior_bowl_accepted,
    dp.senior_bowl_result,
    dp.senior_bowl_notes,
    dp.height_in,
    dp.weight_lbs,
    dp.arm_length_in,
    dp.hand_size_in,
    dp.archetype,
    dp.primary_role,
    dp.secondary_role,
    dp.scout_lens,
    dp.scout_confidence,
    dp.scout_grade,
    dp.scout_ceiling,
    dp.scout_risk,
    dp.scouting_summary,
    dp.scouting_strengths,
    dp.scouting_concerns,
    dp.scouting_projection,
    dp.scouting_report,
    dp.medical_flag,
    dp.medical_risk,
    dp.medical_notes,
    dp.interview_trait,
    dp.interview_grade,
    dp.interview_notes,
    dp.late_process_status,
    dp.late_process_note,
    dp.public_board_delta,
    dpc.combine_status,
    dpc.combine_grade,
    dpc.athletic_score,
    dpc.drills_completed,
    dpc.forty_yard_dash,
    dpc.ten_yard_split,
    dpc.bench_press_reps,
    dpc.vertical_jump_in,
    dpc.broad_jump_in,
    dpc.three_cone_sec,
    dpc.twenty_yard_shuttle_sec,
    dpc.sixty_yard_shuttle_sec,
    dpc.is_injured AS combine_injured,
    dpc.is_top_skip AS combine_top_skip,
    dpd.pro_day_status,
    dpd.pro_day_grade,
    dpd.athletic_score AS pro_day_athletic_score,
    dpd.drills_completed AS pro_day_drills_completed,
    dpd.forty_yard_dash AS pro_day_forty_yard_dash,
    dpd.ten_yard_split AS pro_day_ten_yard_split,
    dpd.bench_press_reps AS pro_day_bench_press_reps,
    dpd.vertical_jump_in AS pro_day_vertical_jump_in,
    dpd.broad_jump_in AS pro_day_broad_jump_in,
    dpd.three_cone_sec AS pro_day_three_cone_sec,
    dpd.twenty_yard_shuttle_sec AS pro_day_twenty_yard_shuttle_sec,
    dpd.improved_from_combine AS pro_day_improved_from_combine,
    dpd.medical_recheck AS pro_day_medical_recheck,
    dpw.status AS private_workout_status,
    dpw.workout_type AS private_workout_type,
    dpw.interest_level AS private_workout_interest,
    dpw.outcome_grade AS private_workout_grade,
    dpw.notes AS private_workout_note,
    dp.scheme_fit,
    dp.status,
    t.abbreviation AS selected_team,
    picks.round AS selected_round,
    picks.pick_number AS selected_pick_number
FROM draft_prospects dp
JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
LEFT JOIN draft_prospect_combine_results dpc ON dpc.prospect_id = dp.prospect_id
LEFT JOIN draft_prospect_pro_day_results dpd ON dpd.prospect_id = dp.prospect_id
LEFT JOIN draft_prospect_private_workouts dpw ON dpw.prospect_id = dp.prospect_id
LEFT JOIN teams t ON t.team_id = dp.selected_team_id
LEFT JOIN draft_picks picks ON picks.pick_id = dp.selected_pick_id;

DROP VIEW IF EXISTS draft_internal_board_view;
CREATE VIEW IF NOT EXISTS draft_internal_board_view AS
SELECT
    dp.*,
    dc.draft_year,
    dc.class_name,
    COALESCE(dp.public_board_rank, dp.scouting_rank) AS board_rank,
    dpc.combine_status,
    dpc.combine_grade,
    dpc.athletic_score AS combine_athletic_score,
    dpc.drills_completed AS combine_drills_completed,
    dpc.is_injured AS combine_injured,
    dpc.is_top_skip AS combine_top_skip,
    dpd.pro_day_status,
    dpd.pro_day_grade,
    dpd.athletic_score AS pro_day_athletic_score,
    dpd.drills_completed AS pro_day_drills_completed,
    dpd.improved_from_combine AS pro_day_improved_from_combine,
    dpd.medical_recheck AS pro_day_medical_recheck,
    dpw.status AS private_workout_status,
    dpw.workout_type AS private_workout_type,
    dpw.interest_level AS private_workout_interest,
    dpw.outcome_grade AS private_workout_grade,
    dpw.notes AS private_workout_note
FROM draft_prospects dp
JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
LEFT JOIN draft_prospect_combine_results dpc ON dpc.prospect_id = dp.prospect_id
LEFT JOIN draft_prospect_pro_day_results dpd ON dpd.prospect_id = dp.prospect_id
LEFT JOIN draft_prospect_private_workouts dpw ON dpw.prospect_id = dp.prospect_id;

DROP VIEW IF EXISTS draft_prospect_combine_results_view;
CREATE VIEW IF NOT EXISTS draft_prospect_combine_results_view AS
SELECT
    dp.prospect_id,
    dc.draft_year,
    COALESCE(dp.public_board_rank, dp.scouting_rank) AS scouting_rank,
    dp.first_name || ' ' || dp.last_name AS prospect_name,
    dp.position,
    dp.position_group,
    dpc.combine_status,
    dpc.participation_note,
    dpc.combine_grade,
    dpc.athletic_score,
    dpc.drills_completed,
    dpc.drills_skipped,
    dpc.workout_variance,
    dpc.forty_yard_dash,
    dpc.ten_yard_split,
    dpc.bench_press_reps,
    dpc.vertical_jump_in,
    dpc.broad_jump_in,
    dpc.three_cone_sec,
    dpc.twenty_yard_shuttle_sec,
    dpc.sixty_yard_shuttle_sec,
    dpc.is_injured,
    dpc.is_top_skip,
    dpc.source,
    dpc.updated_at
FROM draft_prospect_combine_results dpc
JOIN draft_prospects dp ON dp.prospect_id = dpc.prospect_id
JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id;

DROP VIEW IF EXISTS draft_prospect_pro_day_results_view;
CREATE VIEW IF NOT EXISTS draft_prospect_pro_day_results_view AS
SELECT
    dp.prospect_id,
    dc.draft_year,
    COALESCE(dp.public_board_rank, dp.scouting_rank) AS scouting_rank,
    dp.first_name || ' ' || dp.last_name AS prospect_name,
    dp.position,
    dp.position_group,
    dpd.pro_day_status,
    dpd.participation_note,
    dpd.pro_day_grade,
    dpd.athletic_score,
    dpd.drills_completed,
    dpd.drills_skipped,
    dpd.workout_variance,
    dpd.summary,
    dpd.forty_yard_dash,
    dpd.ten_yard_split,
    dpd.bench_press_reps,
    dpd.vertical_jump_in,
    dpd.broad_jump_in,
    dpd.three_cone_sec,
    dpd.twenty_yard_shuttle_sec,
    dpd.sixty_yard_shuttle_sec,
    dpd.improved_from_combine,
    dpd.medical_recheck,
    dpd.source,
    dpd.updated_at
FROM draft_prospect_pro_day_results dpd
JOIN draft_prospects dp ON dp.prospect_id = dpd.prospect_id
JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id;

DROP VIEW IF EXISTS draft_prospect_private_workouts_view;
CREATE VIEW IF NOT EXISTS draft_prospect_private_workouts_view AS
SELECT
    dpw.workout_id,
    dp.prospect_id,
    dc.draft_year,
    COALESCE(dp.public_board_rank, dp.scouting_rank) AS scouting_rank,
    dp.first_name || ' ' || dp.last_name AS prospect_name,
    dp.position,
    dp.college,
    t.abbreviation AS team,
    dpw.status,
    dpw.workout_type,
    dpw.interest_level,
    dpw.outcome_grade,
    dpw.hidden,
    dpw.notes,
    dpw.source,
    dpw.created_at
FROM draft_prospect_private_workouts dpw
JOIN draft_prospects dp ON dp.prospect_id = dpw.prospect_id
JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
LEFT JOIN teams t ON t.team_id = dpw.team_id;

DROP VIEW IF EXISTS draft_prospect_personalities_view;
CREATE VIEW IF NOT EXISTS draft_prospect_personalities_view AS
SELECT
    dpp.prospect_id,
    dc.draft_class_id,
    dc.draft_year,
    COALESCE(dp.public_board_rank, dp.scouting_rank) AS scouting_rank,
    dp.first_name || ' ' || dp.last_name AS prospect_name,
    dp.position,
    dp.position_group,
    dp.college,
    dp.college_tier,
    dp.age,
    dp.true_grade,
    dp.ceiling_grade,
    dp.dev_trait,
    dp.risk_level,
    dpp.trait_key,
    ptd.display_name,
    ptd.category,
    ptd.polarity,
    ptd.sensitive,
    dpp.intensity,
    dpp.assignment_type,
    dpp.hidden,
    dpp.source,
    dpp.notes,
    dpp.run_id,
    dpp.created_at
FROM draft_prospect_personalities dpp
JOIN draft_prospects dp ON dp.prospect_id = dpp.prospect_id
JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
JOIN personality_trait_definitions ptd ON ptd.trait_key = dpp.trait_key;

DROP VIEW IF EXISTS draft_prospect_sim_ratings_view;
CREATE VIEW IF NOT EXISTS draft_prospect_sim_ratings_view AS
SELECT
    dp.prospect_id,
    dc.draft_year,
    COALESCE(dp.public_board_rank, dp.scouting_rank) AS scouting_rank,
    dp.first_name || ' ' || dp.last_name AS prospect_name,
    dp.position,
    dp.primary_role,
    dp.secondary_role,
    dpr.rating_key,
    rd.display_name,
    rd.rating_group,
    dpr.rating_value,
    dpr.confidence,
    dpr.source,
    dpr.notes,
    dpr.updated_at
FROM draft_prospect_ratings dpr
JOIN draft_prospects dp ON dp.prospect_id = dpr.prospect_id
JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
LEFT JOIN rating_definitions rd ON rd.rating_key = dpr.rating_key;

DROP VIEW IF EXISTS draft_prospect_role_scores_view;
CREATE VIEW IF NOT EXISTS draft_prospect_role_scores_view AS
SELECT
    dp.prospect_id,
    dc.draft_year,
    COALESCE(dp.public_board_rank, dp.scouting_rank) AS scouting_rank,
    dp.first_name || ' ' || dp.last_name AS prospect_name,
    dp.position,
    dprs.role_key,
    rsd.display_name AS role_name,
    dprs.scheme_key,
    dprs.role_score,
    dprs.source,
    dprs.calculated_at
FROM draft_prospect_role_scores dprs
JOIN draft_prospects dp ON dp.prospect_id = dprs.prospect_id
JOIN draft_classes dc ON dc.draft_class_id = dp.draft_class_id
LEFT JOIN role_score_definitions rsd ON rsd.role_key = dprs.role_key;
"""


DRAFT_PROSPECT_COLUMN_MIGRATIONS = {
    "hairstyle": "TEXT",
    "hairstyle_outlier": "INTEGER NOT NULL DEFAULT 0",
    "origin_ethnicity_key": "TEXT",
    "position_group": "TEXT",
    "generation_version": "TEXT",
    "true_rank": "INTEGER",
    "public_board_rank": "INTEGER",
    "public_board_status": "TEXT NOT NULL DEFAULT 'ranked'",
    "discovery_status": "TEXT NOT NULL DEFAULT 'public_board'",
    "scouting_variance": "INTEGER NOT NULL DEFAULT 0",
    "discovery_notes": "TEXT",
    "development_pathway": "TEXT",
    "pipeline_note": "TEXT",
    "display_name": "TEXT",
    "preferred_name": "TEXT",
    "name_pronunciation_note": "TEXT",
    "name_background_note": "TEXT",
    "family_football_type": "TEXT",
    "family_football_background": "TEXT",
    "name_storyline_note": "TEXT",
    "hair_color": "TEXT",
    "skin_tone": "TEXT",
    "complexion": "TEXT",
    "face_shape": "TEXT",
    "jawline": "TEXT",
    "brow_profile": "TEXT",
    "nose_profile": "TEXT",
    "smile_profile": "TEXT",
    "media_style": "TEXT",
    "accessory_style": "TEXT",
    "true_grade": "INTEGER",
    "ceiling_grade": "INTEGER",
    "original_archetype": "TEXT",
    "archetype_identity_status": "TEXT",
    "archetype_identity_note": "TEXT",
    "primary_role": "TEXT",
    "secondary_role": "TEXT",
    "normalized_rating_version": "TEXT",
    "scout_lens": "TEXT",
    "scout_confidence": "TEXT",
    "scout_grade": "INTEGER",
    "scout_ceiling": "INTEGER",
    "scout_risk": "TEXT",
    "scouting_strengths": "TEXT",
    "scouting_concerns": "TEXT",
    "scouting_projection": "TEXT",
    "scouting_report": "TEXT",
    "medical_flag": "TEXT",
    "medical_risk": "TEXT",
    "medical_notes": "TEXT",
    "interview_trait": "TEXT",
    "interview_grade": "INTEGER",
    "interview_notes": "TEXT",
    "late_process_status": "TEXT",
    "late_process_note": "TEXT",
    "public_board_delta": "INTEGER NOT NULL DEFAULT 0",
    "college_class": "TEXT",
    "hometown": "TEXT",
    "hometown_city": "TEXT",
    "hometown_state": "TEXT",
    "hometown_region": "TEXT",
    "senior_bowl_eligible": "INTEGER NOT NULL DEFAULT 0",
    "senior_bowl_invited": "INTEGER NOT NULL DEFAULT 0",
    "senior_bowl_accepted": "INTEGER NOT NULL DEFAULT 0",
    "senior_bowl_result": "TEXT",
    "senior_bowl_notes": "TEXT",
}

POST_MIGRATION_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_draft_prospects_class_public_rank
    ON draft_prospects(draft_class_id, public_board_rank);

CREATE INDEX IF NOT EXISTS idx_draft_prospects_class_true_rank
    ON draft_prospects(draft_class_id, true_rank);

CREATE INDEX IF NOT EXISTS idx_draft_prospects_class_discovery
    ON draft_prospects(draft_class_id, discovery_status, public_board_status);
"""

SCHEMA_SQL = TABLE_SCHEMA_SQL + VIEW_SCHEMA_SQL

VIEW_NAMES = [
    "draft_prospect_role_scores_view",
    "draft_prospect_sim_ratings_view",
    "draft_prospect_personalities_view",
    "draft_prospect_private_workouts_view",
    "draft_prospect_pro_day_results_view",
    "draft_prospect_combine_results_view",
    "draft_internal_board_view",
    "draft_board_view",
    "draft_class_summary_view",
]


def ensure_schema(con: sqlite3.Connection) -> None:
    """Create or refresh the draft-class schema and views."""
    con.executescript(TABLE_SCHEMA_SQL)
    _ensure_draft_prospect_columns(con)
    con.executescript(POST_MIGRATION_INDEX_SQL)
    for view_name in VIEW_NAMES:
        con.execute(f"DROP VIEW IF EXISTS main.{view_name}")
        con.execute(f"DROP VIEW IF EXISTS temp.{view_name}")
    con.executescript(VIEW_SCHEMA_SQL)


def _ensure_draft_prospect_columns(con: sqlite3.Connection) -> None:
    existing = {
        row[1]
        for row in con.execute("PRAGMA table_info(draft_prospects)").fetchall()
    }
    for column, definition in DRAFT_PROSPECT_COLUMN_MIGRATIONS.items():
        if column not in existing:
            con.execute(f"ALTER TABLE draft_prospects ADD COLUMN {column} {definition}")


def draft_tables_exist(con: sqlite3.Connection) -> bool:
    """Return True when the core draft-class tables are present."""
    row = con.execute(
        """
        SELECT COUNT(*)
        FROM sqlite_master
        WHERE type = 'table'
          AND name IN ('draft_classes', 'draft_prospects')
        """
    ).fetchone()
    return bool(row and row[0] == 2)
