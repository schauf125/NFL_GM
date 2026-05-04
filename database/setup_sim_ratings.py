import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


DB_PATH = Path(__file__).with_name("nfl_gm.db")
PILOT_SEASON = 2026


RATING_DEFINITIONS = [
    ("speed", "Speed", "universal", "Maximum movement rate after acceleration.", 0, 100, 1),
    ("acceleration", "Acceleration", "universal", "How quickly the player approaches max speed.", 0, 100, 1),
    ("agility", "Agility", "universal", "Direction-change efficiency, cut recovery, and body angle control.", 0, 100, 1),
    ("balance", "Balance", "universal", "Stumble/fall resistance and body-control recovery after contact.", 0, 100, 1),
    ("strength", "Strength", "universal", "Force output for blocks, tackles, sheds, collisions, and push-back.", 0, 100, 1),
    ("play_recognition", "Play Recognition", "universal", "Diagnosis speed and correctness once a play develops.", 0, 100, 1),
    ("processing_speed", "Processing Speed", "universal", "How often the player re-evaluates and chooses a new action.", 0, 100, 1),
    ("discipline", "Discipline", "universal", "Penalty risk, assignment integrity, and resistance to false movement.", 0, 100, 1),
    ("composure", "Composure", "universal", "Rating stability under pressure, score stress, crowd, and late-game states.", 0, 100, 1),
    ("stamina", "Stamina", "universal", "Fatigue buildup and rating decay during plays, drives, and games.", 0, 100, 1),
    ("durability", "Durability", "universal", "Injury resistance after collision, awkward landing, and workload checks.", 0, 100, 1),
    ("consistency", "Consistency", "universal", "Variance control around base ratings, usually rolled per play or drive.", 0, 100, 1),
    ("kick_power", "Kick Power", "specialist", "Leg strength and distance range for kicks, kickoffs, and punts.", 0, 100, 0),
    ("kick_accuracy", "Kick Accuracy", "specialist", "Directional control and repeatability for kicks and punts.", 0, 100, 0),

    ("pass_accuracy_short", "Short Accuracy", "passer", "Ball-placement quality on quick and short throws, roughly 0-9 yards.", 0, 100, 0),
    ("pass_accuracy_mid", "Mid Accuracy", "passer", "Ball-placement quality on intermediate throws, roughly 10-19 yards.", 0, 100, 0),
    ("pass_accuracy_deep", "Deep Accuracy", "passer", "Ball-placement quality on vertical throws, roughly 20+ yards.", 0, 100, 0),
    ("throw_power", "Throw Power", "passer", "Maximum viable throw distance and velocity.", 0, 100, 0),
    ("throw_release", "Throw Release", "passer", "Time from decision to ball leaving hand.", 0, 100, 0),
    ("platform_control", "Platform Control", "passer", "Accuracy retention when feet/body are imperfect or contact arrives.", 0, 100, 0),

    ("carry_vision", "Vision", "ball_carrier", "Gap finding, lane selection, cutback detection, and avoiding blocked space.", 0, 100, 0),
    ("elusiveness", "Elusiveness", "ball_carrier", "Open-field avoidance, juke effectiveness, and tackle-angle disruption.", 0, 100, 0),
    ("contact_power", "Power", "ball_carrier", "Ability to run through contact, fall forward, and generate extra yards.", 0, 100, 0),
    ("ball_security", "Ball Security", "ball_carrier", "Fumble resistance during hits, strips, awkward contact, and fatigue.", 0, 100, 0),
    ("run_patience", "Run Patience", "ball_carrier", "Ability to let blocks develop before committing to a lane.", 0, 100, 0),

    ("release_vs_press", "Release", "receiver", "Initial separation and delay resistance against press or jam attempts.", 0, 100, 0),
    ("route_snap", "Route Snap", "receiver", "Cut speed and separation burst at route breaks.", 0, 100, 0),
    ("route_timing", "Route Timing", "receiver", "Depth accuracy, landmark discipline, and timing with the passer.", 0, 100, 0),
    ("hands", "Hands", "receiver", "Routine catch reliability when open or lightly contested.", 0, 100, 0),
    ("contested_catch", "Contested Catch", "receiver", "Catch success through contact, tight coverage, or jump-ball situations.", 0, 100, 0),
    ("catch_in_traffic", "Catch In Traffic", "receiver", "Securing the ball when contact arrives immediately after or during the catch.", 0, 100, 0),

    ("pass_block_power", "Pass Block vs Power", "blocker", "Anchor quality against bull rushes and direct force.", 0, 100, 0),
    ("pass_block_finesse", "Pass Block vs Finesse", "blocker", "Hand usage and recovery against swims, rips, spins, and counters.", 0, 100, 0),
    ("pass_block_speed", "Pass Block vs Speed", "blocker", "Kick-slide timing, edge depth, and ability to protect the arc.", 0, 100, 0),
    ("run_block_drive", "Run Block Drive", "blocker", "Vertical displacement and lane creation in base run blocks.", 0, 100, 0),
    ("reach_block", "Reach Block", "blocker", "Ability to seal defenders laterally on outside zone, screens, and wide runs.", 0, 100, 0),
    ("lead_block", "Lead Block", "blocker", "Moving-target blocking in space.", 0, 100, 0),
    ("block_sustain", "Block Sustain", "blocker", "How long the blocker maintains engagement without losing leverage.", 0, 100, 0),

    ("power_rush", "Power Rush", "pass_rusher", "Bull rush, pocket compression, knockback, and collapsing the launch point.", 0, 100, 0),
    ("finesse_rush", "Finesse Rush", "pass_rusher", "Rips, swims, spins, hand counters, and winning without direct force.", 0, 100, 0),
    ("speed_rush", "Speed Rush", "pass_rusher", "Edge threat, first-step pressure, arc wins, and fast closing path.", 0, 100, 0),
    ("rush_plan", "Rush Plan", "pass_rusher", "Selecting counters over time instead of repeating losing moves.", 0, 100, 0),
    ("stunt_execution", "Stunt Execution", "pass_rusher", "Timing and path discipline on twists, games, loops, and designed pressure.", 0, 100, 0),
    ("double_team_takeon", "Double-Team Take-On", "pass_rusher", "Ability to avoid being erased by two blockers and still compress space.", 0, 100, 0),
    ("sack_finish", "Sack Finish", "pass_rusher", "Converting near-pressure into actual QB contact and preventing escape.", 0, 100, 0),

    ("run_diagnostics", "Run Diagnostics", "run_defender", "Identifying run direction, mesh, pullers, and misdirection.", 0, 100, 0),
    ("block_shedding", "Block Shedding", "run_defender", "Ability to disengage from run blocks and enter the lane.", 0, 100, 0),
    ("gap_integrity", "Gap Integrity", "run_defender", "Staying in assigned lane and avoiding over-pursuit.", 0, 100, 0),
    ("pursuit_angle", "Pursuit Angle", "run_defender", "Selecting the correct angle to intercept the ball carrier.", 0, 100, 0),
    ("edge_contain", "Edge Contain", "run_defender", "Keeping outside leverage and preventing bounce-outs.", 0, 100, 0),
    ("traffic_navigation", "Traffic Navigation", "run_defender", "Moving through bodies without getting screened, picked, or washed out.", 0, 100, 0),

    ("press_coverage", "Press Coverage", "coverage", "Jam timing, route delay, and ability to disrupt release.", 0, 100, 0),
    ("man_coverage", "Man Coverage", "coverage", "Mirroring routes, maintaining leverage, and staying attached through cuts.", 0, 100, 0),
    ("zone_coverage", "Zone Coverage", "coverage", "Spacing, landmark depth, pattern matching, and route handoff decisions.", 0, 100, 0),
    ("zone_recovery", "Zone Recovery", "coverage", "Closing after the ball is thrown or after a route enters/leaves the zone.", 0, 100, 0),
    ("ball_skills", "Ball Skills", "coverage", "Playing the ball in the air: breakups, interceptions, timing, and positioning.", 0, 100, 0),
    ("coverage_communication", "Coverage Communication", "coverage", "Passing routes, switching assignments, and avoiding busts.", 0, 100, 0),

    ("solo_tackle", "Solo Tackle", "tackler", "Chance to complete a one-on-one tackle once contact is made.", 0, 100, 0),
    ("tackle_wrap", "Tackle Wrap", "tackler", "Ability to slow, hold, or drag the runner when the first tackle is not clean.", 0, 100, 0),
    ("hit_power", "Hit Power", "tackler", "Collision force, stumble chance, and ball-dislodging force.", 0, 100, 0),
    ("forced_fumble", "Forced Fumble", "tackler", "Strip technique and ball attack timing.", 0, 100, 0),
    ("open_field_tackle", "Open-Field Tackle", "tackler", "Tackle reliability in space where leverage and angle are difficult.", 0, 100, 0),
    ("assist_tackle", "Assist / Group Tackle", "tackler", "Ability to slow or steer the ball carrier long enough for help to arrive.", 0, 100, 0),
]


ROLE_DEFINITIONS = [
    ("pocket_qb", "Pocket QB", "QB", "Quarterback role weighted toward diagnosis, timing, release, and pocket throwing."),
    ("scrambling_qb", "Scrambling QB", "QB", "Quarterback role weighted toward movement, platform control, and ball security."),
    ("power_rb", "Power RB", "RB", "Running back role weighted toward vision, contact power, balance, and ball security."),
    ("elusive_rb", "Elusive RB", "RB", "Running back role weighted toward agility, acceleration, elusiveness, and receiving utility."),
    ("boundary_wr", "Boundary WR", "WR", "Outside receiver role weighted toward release, route breaks, hands, speed, and contested catches."),
    ("slot_wr", "Slot WR", "WR", "Inside receiver role weighted toward route timing, quick separation, hands, and traffic catches."),
    ("inline_te", "Inline TE", "TE", "Tight end role weighted toward blocking with usable receiving skill."),
    ("move_te", "Move TE", "TE", "Tight end role weighted toward receiving, space usage, and enough blocking to stay multiple."),
    ("pass_protecting_ot", "Pass-Protecting OT", "OL", "Offensive tackle role weighted toward pass protection against speed, power, and finesse."),
    ("interior_run_blocker", "Interior Run Blocker", "OL", "Interior offensive line role weighted toward drive blocking, sustain, strength, and discipline."),
    ("speed_edge", "Speed EDGE", "EDGE", "Edge defender role weighted toward burst, speed rush, rush plan, and sack finishing."),
    ("power_edge", "Power EDGE", "EDGE", "Edge defender role weighted toward power, edge control, block shedding, and run defense."),
    ("nose_run_stopping_dt", "Nose / Run-Stopping DT", "DL", "Interior defender role weighted toward double teams, gap control, and run defense."),
    ("interior_rusher", "Interior Rusher", "DL", "Interior defender role weighted toward pressure, rush plan, stunts, and pocket disruption."),
    ("coverage_lb", "Coverage LB", "LB", "Linebacker role weighted toward zone coverage, pursuit, tackling, and recognition."),
    ("box_lb", "Box LB", "LB", "Linebacker role weighted toward run fits, block shedding, tackling, and contact."),
    ("man_cb", "Man CB", "CB", "Cornerback role weighted toward man coverage, athletic matchups, press, and ball skills."),
    ("zone_cb", "Zone CB", "CB", "Cornerback role weighted toward zone spacing, recovery, communication, and recognition."),
    ("deep_safety", "Deep Safety", "S", "Safety role weighted toward range, zone coverage, communication, and ball skills."),
    ("box_safety", "Box Safety", "S", "Safety role weighted toward tackling, run support, coverage versatility, and recognition."),
]


ROLE_WEIGHTS = {
    "pocket_qb": {
        "pass_accuracy_short": 12, "pass_accuracy_mid": 16, "pass_accuracy_deep": 10,
        "throw_power": 8, "throw_release": 10, "platform_control": 10,
        "processing_speed": 14, "play_recognition": 12, "composure": 8,
    },
    "scrambling_qb": {
        "pass_accuracy_short": 10, "pass_accuracy_mid": 12, "throw_power": 8,
        "platform_control": 10, "processing_speed": 10, "speed": 8, "acceleration": 8,
        "agility": 8, "carry_vision": 8, "ball_security": 6, "composure": 12,
    },
    "power_rb": {
        "carry_vision": 16, "contact_power": 15, "balance": 12, "strength": 9,
        "ball_security": 12, "run_patience": 6, "acceleration": 8, "speed": 6,
        "elusiveness": 6, "stamina": 6, "durability": 4,
    },
    "elusive_rb": {
        "carry_vision": 14, "elusiveness": 18, "agility": 14, "acceleration": 12,
        "speed": 8, "balance": 8, "ball_security": 12, "run_patience": 4,
        "hands": 8, "route_timing": 2,
    },
    "boundary_wr": {
        "release_vs_press": 14, "route_snap": 14, "route_timing": 10, "hands": 12,
        "contested_catch": 12, "catch_in_traffic": 8, "speed": 10, "acceleration": 8,
        "agility": 6, "composure": 6,
    },
    "slot_wr": {
        "route_snap": 18, "route_timing": 14, "hands": 14, "catch_in_traffic": 12,
        "release_vs_press": 8, "agility": 10, "acceleration": 8, "processing_speed": 6,
        "play_recognition": 6, "ball_security": 4,
    },
    "inline_te": {
        "run_block_drive": 14, "pass_block_power": 10, "pass_block_finesse": 8,
        "block_sustain": 12, "lead_block": 6, "reach_block": 4, "hands": 10,
        "route_timing": 8, "contested_catch": 8, "strength": 10, "balance": 4,
        "stamina": 6,
    },
    "move_te": {
        "hands": 14, "route_timing": 14, "route_snap": 10, "contested_catch": 12,
        "catch_in_traffic": 8, "release_vs_press": 8, "speed": 6, "agility": 6,
        "acceleration": 6, "run_block_drive": 4, "block_sustain": 4, "strength": 4,
        "composure": 4,
    },
    "pass_protecting_ot": {
        "pass_block_speed": 22, "pass_block_power": 16, "pass_block_finesse": 16,
        "block_sustain": 16, "strength": 8, "agility": 2, "balance": 6,
        "processing_speed": 6, "discipline": 8,
    },
    "interior_run_blocker": {
        "run_block_drive": 18, "block_sustain": 14, "pass_block_power": 10,
        "lead_block": 8, "strength": 14, "balance": 10, "acceleration": 4,
        "play_recognition": 8, "discipline": 8, "stamina": 6,
    },
    "speed_edge": {
        "speed_rush": 18, "finesse_rush": 12, "rush_plan": 8, "stunt_execution": 4,
        "sack_finish": 12, "acceleration": 12, "agility": 8, "run_diagnostics": 6,
        "edge_contain": 8, "solo_tackle": 6, "stamina": 6,
    },
    "power_edge": {
        "power_rush": 18, "double_team_takeon": 10, "sack_finish": 10,
        "stunt_execution": 4, "edge_contain": 12, "block_shedding": 10,
        "strength": 12, "balance": 4, "run_diagnostics": 8, "solo_tackle": 6,
        "stamina": 6,
    },
    "nose_run_stopping_dt": {
        "double_team_takeon": 16, "block_shedding": 14, "run_diagnostics": 12,
        "gap_integrity": 12, "traffic_navigation": 6, "power_rush": 8, "strength": 12,
        "balance": 6, "solo_tackle": 6, "assist_tackle": 4, "stamina": 4,
    },
    "interior_rusher": {
        "power_rush": 12, "finesse_rush": 12, "rush_plan": 10, "sack_finish": 10,
        "stunt_execution": 8, "double_team_takeon": 8, "block_shedding": 10,
        "run_diagnostics": 8, "strength": 8, "acceleration": 6, "agility": 4,
        "stamina": 4,
    },
    "coverage_lb": {
        "zone_coverage": 14, "man_coverage": 8, "zone_recovery": 10,
        "run_diagnostics": 10, "pursuit_angle": 10, "traffic_navigation": 4,
        "solo_tackle": 10, "tackle_wrap": 8, "play_recognition": 10,
        "processing_speed": 6, "speed": 6, "agility": 4,
    },
    "box_lb": {
        "run_diagnostics": 12, "gap_integrity": 10, "block_shedding": 10,
        "traffic_navigation": 6, "pursuit_angle": 10, "solo_tackle": 12,
        "tackle_wrap": 8, "hit_power": 4, "forced_fumble": 4, "strength": 8,
        "play_recognition": 8, "zone_coverage": 4, "stamina": 4,
    },
    "man_cb": {
        "press_coverage": 10, "man_coverage": 20, "zone_recovery": 8,
        "ball_skills": 10, "speed": 12, "acceleration": 8, "agility": 12,
        "play_recognition": 6, "composure": 6, "solo_tackle": 4, "open_field_tackle": 4,
    },
    "zone_cb": {
        "zone_coverage": 18, "zone_recovery": 14, "ball_skills": 10,
        "man_coverage": 8, "coverage_communication": 8, "play_recognition": 10,
        "processing_speed": 6, "speed": 8, "agility": 8, "open_field_tackle": 6,
        "composure": 4,
    },
    "deep_safety": {
        "zone_coverage": 16, "zone_recovery": 14, "ball_skills": 12,
        "coverage_communication": 10, "play_recognition": 12, "speed": 10,
        "acceleration": 6, "pursuit_angle": 6, "open_field_tackle": 8, "composure": 6,
    },
    "box_safety": {
        "run_diagnostics": 12, "pursuit_angle": 10, "solo_tackle": 12,
        "open_field_tackle": 10, "hit_power": 8, "forced_fumble": 4,
        "zone_coverage": 10, "man_coverage": 8, "play_recognition": 10,
        "speed": 8, "agility": 4, "composure": 4,
    },
}


VIKINGS_ROLE_ASSIGNMENTS = [
    ("Kyler", "Murray", "scrambling_qb", "pocket_qb", "Mobile QB baseline; role assumption to review"),
    ("J.J.", "McCarthy", "scrambling_qb", "pocket_qb", "Developmental QB comparison point"),
    ("Carson", "Wentz", "pocket_qb", None, "Veteran pocket-passer baseline"),
    ("Aaron", "Jones Sr.", "elusive_rb", "power_rb", "Veteran space/receiving back baseline"),
    ("Jordan", "Mason", "power_rb", "elusive_rb", "Power-runner contrast with Jones"),
    ("Demond", "Claiborne", "elusive_rb", None, "Rookie/developmental speed back"),
    ("Justin", "Jefferson", "boundary_wr", "slot_wr", "Elite receiver scale anchor"),
    ("Jordan", "Addison", "boundary_wr", "slot_wr", "High-end secondary receiver comparison"),
    ("Tai", "Felton", "slot_wr", "boundary_wr", "Young receiver role to review"),
    ("Myles", "Price", "slot_wr", None, "Slot/KR type; returner role may be added later"),
    ("T.J.", "Hockenson", "move_te", "inline_te", "Move TE receiving baseline"),
    ("Josh", "Oliver", "inline_te", None, "Blocking TE scale anchor"),
    ("Christian", "Darrisaw", "pass_protecting_ot", None, "High-end tackle scale anchor"),
    ("Brian", "O'Neill", "pass_protecting_ot", None, "Veteran tackle comparison"),
    ("Will", "Fries", "interior_run_blocker", None, "Interior OL starter baseline"),
    ("Blake", "Brandel", "interior_run_blocker", None, "Interior OL starter/utility baseline"),
    ("Donovan", "Jackson", "interior_run_blocker", None, "Young interior OL baseline"),
    ("Andrew", "Van Ginkel", "power_edge", "speed_edge", "Veteran versatile EDGE baseline"),
    ("Dallas", "Turner", "speed_edge", "power_edge", "Young explosive EDGE baseline"),
    ("Bo", "Richter", "speed_edge", "power_edge", "Rotational EDGE/special teams baseline"),
    ("Jalen", "Redmond", "interior_rusher", "nose_run_stopping_dt", "Interior rusher role"),
    ("Caleb", "Banks", "interior_rusher", "nose_run_stopping_dt", "Young interior DL role to review"),
    ("Domonique", "Orange", "nose_run_stopping_dt", None, "True nose tackle body-type anchor"),
    ("Taki", "Taimani", "nose_run_stopping_dt", None, "Run-stopping depth comparison"),
    ("Blake", "Cashman", "coverage_lb", "box_lb", "Coverage/space LB baseline"),
    ("Ivan", "Pace Jr.", "box_lb", "coverage_lb", "Compact downhill LB baseline"),
    ("Eric", "Wilson", "box_lb", "coverage_lb", "Veteran LB comparison"),
    ("Byron", "Murphy Jr.", "zone_cb", "man_cb", "Top CB baseline"),
    ("Isaiah", "Rodgers", "man_cb", "zone_cb", "Speed/man CB comparison"),
    ("Dwight", "McGlothern", "man_cb", "zone_cb", "Young CB role to review"),
    ("James", "Pierre", "zone_cb", "man_cb", "Veteran depth CB baseline"),
    ("Joshua", "Metellus", "box_safety", "deep_safety", "Versatile safety/box defender baseline"),
    ("Jay", "Ward", "deep_safety", "box_safety", "Young/depth safety comparison"),
    ("Theo", "Jackson", "deep_safety", "box_safety", "Safety depth comparison"),
]


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def backup_database():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DB_PATH.with_name(f"{DB_PATH.stem}.pre_sim_ratings_{timestamp}{DB_PATH.suffix}")
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def validate_role_weights():
    known_ratings = {row[0] for row in RATING_DEFINITIONS}
    known_roles = {row[0] for row in ROLE_DEFINITIONS}

    for role_key, weights in ROLE_WEIGHTS.items():
        if role_key not in known_roles:
            raise ValueError(f"Role weights reference unknown role: {role_key}")
        total = sum(weights.values())
        if total != 100:
            raise ValueError(f"Role '{role_key}' weights must total 100, got {total}")
        unknown_ratings = sorted(set(weights) - known_ratings)
        if unknown_ratings:
            raise ValueError(f"Role '{role_key}' references unknown ratings: {unknown_ratings}")


def create_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rating_definitions (
            rating_key TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            rating_group TEXT NOT NULL,
            description TEXT NOT NULL,
            min_value INTEGER NOT NULL DEFAULT 0,
            max_value INTEGER NOT NULL DEFAULT 100,
            is_universal INTEGER NOT NULL DEFAULT 0 CHECK (is_universal IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS player_ratings (
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            rating_key TEXT NOT NULL REFERENCES rating_definitions(rating_key) ON DELETE CASCADE,
            rating_value INTEGER NOT NULL CHECK (rating_value BETWEEN 0 AND 100),
            confidence TEXT NOT NULL DEFAULT 'medium' CHECK (confidence IN ('low', 'medium', 'high')),
            source TEXT NOT NULL DEFAULT 'manual',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (player_id, season, rating_key)
        );

        CREATE TABLE IF NOT EXISTS role_score_definitions (
            role_key TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            position_family TEXT NOT NULL,
            description TEXT NOT NULL,
            is_public INTEGER NOT NULL DEFAULT 0 CHECK (is_public IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS role_score_weights (
            role_key TEXT NOT NULL REFERENCES role_score_definitions(role_key) ON DELETE CASCADE,
            rating_key TEXT NOT NULL REFERENCES rating_definitions(rating_key) ON DELETE CASCADE,
            weight REAL NOT NULL CHECK (weight > 0),
            PRIMARY KEY (role_key, rating_key)
        );

        CREATE TABLE IF NOT EXISTS player_role_assignments (
            assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            role_key TEXT NOT NULL REFERENCES role_score_definitions(role_key) ON DELETE CASCADE,
            priority INTEGER NOT NULL CHECK (priority BETWEEN 1 AND 5),
            source TEXT NOT NULL DEFAULT 'manual',
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(player_id, season, role_key),
            UNIQUE(player_id, season, priority)
        );

        CREATE TABLE IF NOT EXISTS player_role_scores (
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
            role_key TEXT NOT NULL REFERENCES role_score_definitions(role_key) ON DELETE CASCADE,
            scheme_key TEXT NOT NULL DEFAULT 'default',
            role_score REAL NOT NULL CHECK (role_score BETWEEN 0 AND 100),
            source TEXT NOT NULL DEFAULT 'calculated',
            calculated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (player_id, season, role_key, scheme_key)
        );

        CREATE INDEX IF NOT EXISTS idx_player_ratings_rating_key
            ON player_ratings(rating_key);
        CREATE INDEX IF NOT EXISTS idx_player_ratings_season_rating
            ON player_ratings(season, rating_key, rating_value);
        CREATE INDEX IF NOT EXISTS idx_player_role_assignments_player_season
            ON player_role_assignments(player_id, season, priority);
        CREATE INDEX IF NOT EXISTS idx_player_role_scores_role
            ON player_role_scores(season, role_key, role_score);

        CREATE VIEW IF NOT EXISTS player_sim_ratings_view AS
            SELECT
                p.player_id,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position,
                t.abbreviation AS team,
                pr.season,
                rd.rating_group,
                pr.rating_key,
                rd.display_name,
                pr.rating_value,
                pr.confidence,
                pr.source,
                pr.notes,
                pr.updated_at
            FROM player_ratings pr
            JOIN players p ON p.player_id = pr.player_id
            LEFT JOIN teams t ON t.team_id = p.team_id
            JOIN rating_definitions rd ON rd.rating_key = pr.rating_key;

        CREATE VIEW IF NOT EXISTS player_role_assignments_view AS
            SELECT
                pra.assignment_id,
                p.player_id,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position,
                t.abbreviation AS team,
                pra.season,
                pra.priority,
                pra.role_key,
                rsd.display_name AS role_name,
                rsd.position_family,
                pra.source,
                pra.notes,
                pra.updated_at
            FROM player_role_assignments pra
            JOIN players p ON p.player_id = pra.player_id
            LEFT JOIN teams t ON t.team_id = p.team_id
            JOIN role_score_definitions rsd ON rsd.role_key = pra.role_key;

        CREATE VIEW IF NOT EXISTS player_role_scores_view AS
            SELECT
                p.player_id,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position,
                t.abbreviation AS team,
                prs.season,
                prs.role_key,
                rsd.display_name AS role_name,
                prs.scheme_key,
                prs.role_score,
                prs.source,
                prs.calculated_at
            FROM player_role_scores prs
            JOIN players p ON p.player_id = prs.player_id
            LEFT JOIN teams t ON t.team_id = p.team_id
            JOIN role_score_definitions rsd ON rsd.role_key = prs.role_key;
        """
    )


def seed_rating_definitions(conn):
    conn.executemany(
        """
        INSERT INTO rating_definitions (
            rating_key, display_name, rating_group, description,
            min_value, max_value, is_universal, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(rating_key) DO UPDATE SET
            display_name = excluded.display_name,
            rating_group = excluded.rating_group,
            description = excluded.description,
            min_value = excluded.min_value,
            max_value = excluded.max_value,
            is_universal = excluded.is_universal,
            updated_at = datetime('now')
        """,
        RATING_DEFINITIONS,
    )


def seed_role_definitions(conn):
    conn.executemany(
        """
        INSERT INTO role_score_definitions (
            role_key, display_name, position_family, description, is_public, updated_at
        )
        VALUES (?, ?, ?, ?, 0, datetime('now'))
        ON CONFLICT(role_key) DO UPDATE SET
            display_name = excluded.display_name,
            position_family = excluded.position_family,
            description = excluded.description,
            is_public = 0,
            updated_at = datetime('now')
        """,
        ROLE_DEFINITIONS,
    )


def seed_role_weights(conn):
    for role_key, weights in ROLE_WEIGHTS.items():
        conn.execute("DELETE FROM role_score_weights WHERE role_key = ?", (role_key,))
        conn.executemany(
            """
            INSERT INTO role_score_weights (role_key, rating_key, weight)
            VALUES (?, ?, ?)
            """,
            [(role_key, rating_key, weight) for rating_key, weight in weights.items()],
        )


def get_team_id(conn, abbreviation):
    row = conn.execute(
        "SELECT team_id FROM teams WHERE abbreviation = ?",
        (abbreviation,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Team '{abbreviation}' not found")
    return row[0]


def find_player(conn, team_id, first_name, last_name):
    row = conn.execute(
        """
        SELECT player_id
        FROM players
        WHERE team_id = ?
          AND first_name = ?
          AND last_name = ?
        """,
        (team_id, first_name, last_name),
    ).fetchone()
    return row[0] if row else None


def upsert_role_assignment(conn, player_id, season, role_key, priority, source, notes):
    conn.execute(
        """
        INSERT INTO player_role_assignments (
            player_id, season, role_key, priority, source, notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(player_id, season, role_key) DO UPDATE SET
            priority = excluded.priority,
            source = excluded.source,
            notes = excluded.notes,
            updated_at = datetime('now')
        """,
        (player_id, season, role_key, priority, source, notes),
    )


def seed_vikings_role_assignments(conn):
    team_id = get_team_id(conn, "MIN")
    missing = []
    inserted = 0

    for first_name, last_name, primary_role, secondary_role, notes in VIKINGS_ROLE_ASSIGNMENTS:
        player_id = find_player(conn, team_id, first_name, last_name)
        if player_id is None:
            missing.append(f"{first_name} {last_name}")
            continue

        upsert_role_assignment(
            conn,
            player_id,
            PILOT_SEASON,
            primary_role,
            1,
            "vikings_rating_pilot",
            notes,
        )
        inserted += 1

        if secondary_role:
            upsert_role_assignment(
                conn,
                player_id,
                PILOT_SEASON,
                secondary_role,
                2,
                "vikings_rating_pilot",
                notes,
            )
            inserted += 1

    return inserted, missing


def print_summary(conn, role_assignment_rows, missing_players):
    counts = {
        "rating_definitions": conn.execute("SELECT COUNT(*) FROM rating_definitions").fetchone()[0],
        "role_score_definitions": conn.execute("SELECT COUNT(*) FROM role_score_definitions").fetchone()[0],
        "role_score_weights": conn.execute("SELECT COUNT(*) FROM role_score_weights").fetchone()[0],
        "player_role_assignments": conn.execute(
            "SELECT COUNT(*) FROM player_role_assignments WHERE season = ?",
            (PILOT_SEASON,),
        ).fetchone()[0],
        "player_ratings": conn.execute("SELECT COUNT(*) FROM player_ratings").fetchone()[0],
        "player_role_scores": conn.execute("SELECT COUNT(*) FROM player_role_scores").fetchone()[0],
    }

    print("Sim ratings setup complete.")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    print(f"  Vikings role assignment rows touched: {role_assignment_rows}")

    if missing_players:
        print("  Missing Vikings pilot players:")
        for player in missing_players:
            print(f"    - {player}")


def main():
    parser = argparse.ArgumentParser(description="Create and seed normalized sim rating tables.")
    parser.add_argument("--no-backup", action="store_true", help="Skip timestamped database backup.")
    args = parser.parse_args()

    validate_role_weights()

    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)

    if not args.no_backup:
        backup_path = backup_database()
        print(f"Backup created: {backup_path}")

    conn = get_connection()
    try:
        with conn:
            create_schema(conn)
            seed_rating_definitions(conn)
            seed_role_definitions(conn)
            seed_role_weights(conn)
            role_assignment_rows, missing_players = seed_vikings_role_assignments(conn)

        print_summary(conn, role_assignment_rows, missing_players)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
