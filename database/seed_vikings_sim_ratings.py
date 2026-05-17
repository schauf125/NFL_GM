import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import rating_profile_caps

from setup_sim_ratings import DB_PATH, RATING_DEFINITIONS, ROLE_WEIGHTS


SEASON = 2026
DEFAULT_TEAM_ABBR = "MIN"
SOURCE = "sim_ratings_generated"
REPLACED_SOURCES = ("sim_ratings_generated", "vikings_initial_generated", "vikings_rating_pilot")


ROLE_OVERRIDES = {
    ("Kyler", "Murray"): ("scrambling_qb", "pocket_qb"),
    ("J.J.", "McCarthy"): ("scrambling_qb", "pocket_qb"),
    ("Carson", "Wentz"): ("pocket_qb", None),
    ("Aaron", "Jones Sr."): ("elusive_rb", "power_rb"),
    ("Jordan", "Mason"): ("power_rb", "elusive_rb"),
    ("Demond", "Claiborne"): ("elusive_rb", None),
    ("Justin", "Jefferson"): ("boundary_wr", "slot_wr"),
    ("Jordan", "Addison"): ("boundary_wr", "slot_wr"),
    ("Tai", "Felton"): ("slot_wr", "boundary_wr"),
    ("Myles", "Price"): ("slot_wr", None),
    ("T.J.", "Hockenson"): ("move_te", "inline_te"),
    ("Josh", "Oliver"): ("inline_te", "move_te"),
    ("Christian", "Darrisaw"): ("pass_protecting_ot", None),
    ("Brian", "O'Neill"): ("pass_protecting_ot", None),
    ("Will", "Fries"): ("interior_run_blocker", None),
    ("Blake", "Brandel"): ("interior_run_blocker", None),
    ("Donovan", "Jackson"): ("interior_run_blocker", None),
    ("Andrew", "Van Ginkel"): ("power_edge", "speed_edge"),
    ("Dallas", "Turner"): ("speed_edge", "power_edge"),
    ("Bo", "Richter"): ("speed_edge", "power_edge"),
    ("Jalen", "Redmond"): ("interior_rusher", "nose_run_stopping_dt"),
    ("Caleb", "Banks"): ("interior_rusher", "nose_run_stopping_dt"),
    ("Domonique", "Orange"): ("nose_run_stopping_dt", None),
    ("Taki", "Taimani"): ("nose_run_stopping_dt", None),
    ("Blake", "Cashman"): ("coverage_lb", "box_lb"),
    ("Ivan", "Pace Jr."): ("box_lb", "coverage_lb"),
    ("Eric", "Wilson"): ("box_lb", "coverage_lb"),
    ("Byron", "Murphy Jr."): ("zone_cb", "man_cb"),
    ("Isaiah", "Rodgers"): ("man_cb", "zone_cb"),
    ("Dwight", "McGlothern"): ("man_cb", "zone_cb"),
    ("James", "Pierre"): ("zone_cb", "man_cb"),
    ("Joshua", "Metellus"): ("box_safety", "deep_safety"),
    ("Jay", "Ward"): ("deep_safety", "box_safety"),
    ("Theo", "Jackson"): ("deep_safety", "box_safety"),
}


ROLE_SCORE_FLOORS = {
    # Jefferson has played plenty inside, but his Vikings usage is primarily
    # the boundary/X receiver role. Keep slot elite without letting it sort
    # above his outside role when regenerated.
    ("Justin", "Jefferson"): {
        "boundary_wr": 95.25,
        "slot_wr": 93.75,
    },
    ("Josh", "Oliver"): {
        "inline_te": 78.5,
        "move_te": 70.0,
    },
}


RATING_OVERRIDES = {
    ("Josh", "Oliver"): {
        "hands": 74,
        "catch_in_traffic": 75,
        "contested_catch": 74,
        "release_vs_press": 70,
        "route_snap": 69,
        "route_timing": 70,
        "run_block_drive": 84,
        "reach_block": 82,
        "lead_block": 83,
        "block_sustain": 85,
        "pass_block_power": 78,
        "pass_block_finesse": 76,
        "pass_block_speed": 76,
        "strength": 80,
        "balance": 78,
        "contact_power": 77,
        "play_recognition": 74,
        "processing_speed": 73,
        "discipline": 76,
        "composure": 75,
        "consistency": 74,
        "durability": 70,
    },
}


ATHLETIC_OVERRIDES = {
    ("Blake", "Cashman"): {
        "speed": 86,
        "acceleration": 86,
        "agility": 84,
        "notes": "Manual athletic override: 4.51 forty and strong public athletic profile.",
    },
}


RATING_GROUPS = {rating_key: rating_group for rating_key, _, rating_group, *_ in RATING_DEFINITIONS}


PRIMARY_GROUPS_BY_ROLE = {
    "pocket_qb": {"passer"},
    "scrambling_qb": {"passer", "ball_carrier"},
    "power_rb": {"ball_carrier"},
    "elusive_rb": {"ball_carrier", "receiver"},
    "boundary_wr": {"receiver"},
    "slot_wr": {"receiver", "ball_carrier"},
    "inline_te": {"receiver", "blocker"},
    "move_te": {"receiver", "blocker"},
    "pass_protecting_ot": {"blocker"},
    "interior_run_blocker": {"blocker"},
    "speed_edge": {"pass_rusher", "run_defender", "tackler"},
    "power_edge": {"pass_rusher", "run_defender", "tackler"},
    "nose_run_stopping_dt": {"pass_rusher", "run_defender", "tackler"},
    "interior_rusher": {"pass_rusher", "run_defender", "tackler"},
    "coverage_lb": {"run_defender", "coverage", "tackler"},
    "box_lb": {"run_defender", "coverage", "tackler"},
    "man_cb": {"coverage", "tackler"},
    "zone_cb": {"coverage", "tackler"},
    "deep_safety": {"coverage", "run_defender", "tackler"},
    "box_safety": {"coverage", "run_defender", "tackler"},
}


POSITION_ACTIVE_GROUPS = {
    "QB": {"passer"},
    "RB": {"ball_carrier", "receiver", "blocker"},
    "FB": {"ball_carrier", "receiver", "blocker"},
    "WR": {"receiver", "ball_carrier", "blocker"},
    "TE": {"receiver", "blocker", "ball_carrier"},
    "OT": {"blocker"},
    "OG": {"blocker"},
    "C": {"blocker"},
    "OL": {"blocker"},
    "EDGE": {"pass_rusher", "run_defender", "tackler"},
    "OLB": {"pass_rusher", "run_defender", "tackler", "coverage"},
    "IDL": {"pass_rusher", "run_defender", "tackler"},
    "DT": {"pass_rusher", "run_defender", "tackler"},
    "DL": {"pass_rusher", "run_defender", "tackler"},
    "ILB": {"run_defender", "coverage", "tackler", "pass_rusher"},
    "LB": {"run_defender", "coverage", "tackler", "pass_rusher"},
    "CB": {"coverage", "tackler", "ball_carrier"},
    "NB": {"coverage", "tackler", "ball_carrier"},
    "FS": {"coverage", "run_defender", "tackler", "ball_carrier"},
    "SS": {"coverage", "run_defender", "tackler", "ball_carrier"},
    "S": {"coverage", "run_defender", "tackler", "ball_carrier"},
    "K": {"specialist"},
    "P": {"specialist"},
    "LS": {"specialist", "blocker", "tackler"},
}


INACTIVE_GROUP_CAPS = {
    "QB": {
        "receiver": 24,
        "blocker": 22,
        "pass_rusher": 18,
        "run_defender": 18,
        "coverage": 18,
        "tackler": 18,
    },
    "RB": {"passer": 18, "pass_rusher": 24, "run_defender": 24, "coverage": 24, "tackler": 24},
    "FB": {"passer": 16, "pass_rusher": 26, "run_defender": 28, "coverage": 24, "tackler": 28},
    "WR": {"passer": 18, "pass_rusher": 22, "run_defender": 22, "coverage": 24, "tackler": 24},
    "TE": {"passer": 16, "pass_rusher": 26, "run_defender": 28, "coverage": 24, "tackler": 28},
    "OT": {"passer": 12, "ball_carrier": 18, "receiver": 16, "pass_rusher": 18, "run_defender": 18, "coverage": 12, "tackler": 18},
    "OG": {"passer": 12, "ball_carrier": 18, "receiver": 16, "pass_rusher": 18, "run_defender": 18, "coverage": 12, "tackler": 18},
    "C": {"passer": 12, "ball_carrier": 18, "receiver": 16, "pass_rusher": 18, "run_defender": 18, "coverage": 12, "tackler": 18},
    "OL": {"passer": 12, "ball_carrier": 18, "receiver": 16, "pass_rusher": 18, "run_defender": 18, "coverage": 12, "tackler": 18},
    "EDGE": {"passer": 12, "ball_carrier": 28, "receiver": 18, "blocker": 20, "coverage": 42},
    "OLB": {"passer": 12, "ball_carrier": 30, "receiver": 18, "blocker": 20},
    "IDL": {"passer": 10, "ball_carrier": 18, "receiver": 12, "blocker": 18, "coverage": 12},
    "DT": {"passer": 10, "ball_carrier": 18, "receiver": 12, "blocker": 18, "coverage": 12},
    "DL": {"passer": 10, "ball_carrier": 18, "receiver": 12, "blocker": 18, "coverage": 12},
    "ILB": {"passer": 12, "ball_carrier": 34, "receiver": 20, "blocker": 20},
    "LB": {"passer": 12, "ball_carrier": 34, "receiver": 20, "blocker": 20},
    "CB": {"passer": 12, "receiver": 32, "blocker": 18, "pass_rusher": 24},
    "NB": {"passer": 12, "receiver": 32, "blocker": 18, "pass_rusher": 24},
    "FS": {"passer": 12, "receiver": 28, "blocker": 18, "pass_rusher": 24},
    "SS": {"passer": 12, "receiver": 28, "blocker": 18, "pass_rusher": 24},
    "S": {"passer": 12, "receiver": 28, "blocker": 18, "pass_rusher": 24},
    "K": {"passer": 12, "ball_carrier": 16, "receiver": 12, "blocker": 12, "pass_rusher": 10, "run_defender": 14, "coverage": 12, "tackler": 18},
    "P": {"passer": 16, "ball_carrier": 18, "receiver": 12, "blocker": 12, "pass_rusher": 10, "run_defender": 14, "coverage": 12, "tackler": 20},
    "LS": {"passer": 10, "ball_carrier": 14, "receiver": 12, "pass_rusher": 14, "run_defender": 16, "coverage": 10},
}


DEFENSIVE_GROUPS = {"pass_rusher", "run_defender", "coverage", "tackler"}
OFFENSIVE_GROUPS = {"passer", "ball_carrier", "receiver", "blocker"}


def clamp(value, low=0, high=100):
    return max(low, min(high, int(round(value))))


def nz(value, default=50):
    return default if value is None else value


def blend(*weighted_values):
    total_weight = sum(weight for _, weight in weighted_values)
    if total_weight == 0:
        return 50
    return sum(value * weight for value, weight in weighted_values) / total_weight


def age_modifier(age):
    if age is None:
        return 0
    if age <= 23:
        return 1
    if age <= 28:
        return 2
    if age <= 31:
        return 0
    if age <= 34:
        return -2
    return -5


def experience_modifier(years_exp):
    years_exp = nz(years_exp, 0)
    if years_exp <= 0:
        return -3
    if years_exp <= 2:
        return -1
    if years_exp <= 6:
        return 2
    return 4


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def backup_database():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DB_PATH.with_name(f"{DB_PATH.stem}.pre_vikings_sim_ratings_{timestamp}{DB_PATH.suffix}")
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def get_team_id(conn, team_abbr):
    row = conn.execute("SELECT team_id FROM teams WHERE abbreviation = ?", (team_abbr,)).fetchone()
    if row is None:
        raise ValueError(f"Team '{team_abbr}' not found.")
    return row["team_id"]


def load_team_players(conn, team_id):
    return conn.execute(
        """
        SELECT *
        FROM players
        WHERE team_id = ?
        ORDER BY position, last_name, first_name
        """,
        (team_id,),
    ).fetchall()


def load_team_abbreviations(conn):
    return [
        row["abbreviation"]
        for row in conn.execute("SELECT abbreviation FROM teams ORDER BY abbreviation")
    ]


def choose_roles(player):
    override = ROLE_OVERRIDES.get((player["first_name"], player["last_name"]))
    if override:
        return override

    pos = player["position"]
    speed = nz(player["speed"])
    agility = nz(player["agility"])
    strength = nz(player["strength"])
    awareness = nz(player["awareness"])
    trucking = nz(player["trucking"], 25)
    catching = nz(player["catching"], 25)
    route = nz(player["route_running"], 25)
    coverage = nz(player["coverage"], 25)
    tackle = nz(player["tackle"], 25)
    pass_rush = nz(player["pass_rush"], 25)
    run_block = nz(player["run_blocking"], 25)
    weight = nz(player["weight_lbs"], 240)

    if pos == "QB":
        return ("scrambling_qb", "pocket_qb") if speed >= 72 or agility >= 72 else ("pocket_qb", None)
    if pos == "RB":
        return ("power_rb", "elusive_rb") if trucking + strength > speed + agility else ("elusive_rb", "power_rb")
    if pos == "FB":
        return ("inline_te", "power_rb")
    if pos == "WR":
        return ("slot_wr", "boundary_wr") if agility + route >= speed + catching else ("boundary_wr", "slot_wr")
    if pos == "TE":
        return ("inline_te", "move_te") if run_block + strength > catching + route else ("move_te", "inline_te")
    if pos == "OT":
        return ("pass_protecting_ot", None)
    if pos in {"OG", "C", "OL"}:
        return ("interior_run_blocker", None)
    if pos in {"EDGE", "OLB"}:
        return ("speed_edge", "power_edge") if speed + agility + pass_rush >= strength + awareness + pass_rush else ("power_edge", "speed_edge")
    if pos in {"IDL", "DT", "DL"}:
        return ("nose_run_stopping_dt", "interior_rusher") if weight >= 318 or strength > pass_rush else ("interior_rusher", "nose_run_stopping_dt")
    if pos in {"ILB", "LB"}:
        return ("coverage_lb", "box_lb") if coverage + speed >= tackle + strength else ("box_lb", "coverage_lb")
    if pos in {"CB", "NB"}:
        return ("man_cb", "zone_cb") if speed + agility >= coverage + awareness else ("zone_cb", "man_cb")
    if pos == "FS":
        return ("deep_safety", "box_safety")
    if pos == "SS":
        return ("box_safety", "deep_safety")
    if pos == "S":
        return ("deep_safety", "box_safety") if speed + coverage >= strength + tackle else ("box_safety", "deep_safety")
    return (None, None)


def player_context(player):
    awareness = nz(player["awareness"])
    overall = nz(player["overall"])
    years_exp = nz(player["years_exp"], 0)
    age = player["age"]
    exp_mod = experience_modifier(years_exp)
    age_mod = age_modifier(age)
    composure_base = clamp(blend((awareness, 0.65), (overall, 0.35)) + exp_mod + age_mod)
    discipline_base = clamp(blend((awareness, 0.75), (overall, 0.25)) + exp_mod)
    return {
        "overall": overall,
        "awareness": awareness,
        "exp_mod": exp_mod,
        "age_mod": age_mod,
        "composure_base": composure_base,
        "discipline_base": discipline_base,
    }


def active_groups_for_player(player, roles):
    active_groups = set(POSITION_ACTIVE_GROUPS.get(player["position"], set()))
    for role in roles:
        if role:
            active_groups.update(PRIMARY_GROUPS_BY_ROLE.get(role, set()))
    return active_groups


def inactive_group_cap(player, rating_group):
    if rating_group == "universal":
        return None

    pos = player["position"]
    cap = INACTIVE_GROUP_CAPS.get(pos, {}).get(rating_group, 25)

    if pos in {"QB", "RB", "WR"} and rating_group in DEFENSIVE_GROUPS:
        if nz(player["weight_lbs"], 240) < 215:
            cap -= 3
        if nz(player["height_in"], 74) < 71:
            cap -= 2

    if pos in {"OT", "OG", "C", "OL", "IDL", "DT", "DL"} and rating_group in {"coverage", "receiver"}:
        if nz(player["weight_lbs"], 240) >= 300:
            cap -= 2

    return max(8, cap)


def apply_relevance_caps(player, ratings, roles):
    active_groups = active_groups_for_player(player, roles)
    capped = {}
    for rating_key, value in ratings.items():
        rating_group = RATING_GROUPS[rating_key]
        if rating_group == "universal" or rating_group in active_groups:
            capped[rating_key] = value
            continue
        cap = inactive_group_cap(player, rating_group)
        capped[rating_key] = min(value, cap)
    return capped


def generate_ratings(player, roles):
    ctx = player_context(player)
    athletic_override = ATHLETIC_OVERRIDES.get((player["first_name"], player["last_name"]), {})
    overall = ctx["overall"]
    awareness = ctx["awareness"]
    speed = athletic_override.get("speed", nz(player["speed"]))
    strength = athletic_override.get("strength", nz(player["strength"]))
    agility = athletic_override.get("agility", nz(player["agility"]))
    injury_prone = nz(player["injury_prone"])
    throw_power = nz(player["throw_power"], 25)
    throw_acc = nz(player["throw_acc"], 25)
    route = nz(player["route_running"], 25)
    catching = nz(player["catching"], 25)
    run_block = nz(player["run_blocking"], 25)
    pass_block = nz(player["pass_blocking"], 25)
    trucking = nz(player["trucking"], 25)
    tackle = nz(player["tackle"], 25)
    pass_rush = nz(player["pass_rush"], 25)
    coverage = nz(player["coverage"], 25)
    kick_power = nz(player["kick_power"], 25)
    kick_acc = nz(player["kick_acc"], 25)
    weight = nz(player["weight_lbs"], 240)
    pos = player["position"]

    mass_mod = (weight - 240) / 12
    light_mod = (240 - weight) / 18
    trench = pos in {"OT", "OG", "C", "OL", "IDL", "DT", "DL"}
    skill = pos in {"QB", "RB", "FB", "WR", "TE", "CB", "FS", "SS", "S"}

    acceleration = blend((speed, 0.55), (agility, 0.45)) + (2 if skill else 0) - max(0, mass_mod / 3)
    if trench:
        balance = blend((strength, 0.35), (overall, 0.25), (run_block, 0.2), (pass_block, 0.2))
    elif pos in {"EDGE", "OLB", "ILB", "LB", "CB", "FS", "SS", "S"}:
        balance = blend((strength, 0.25), (agility, 0.25), (tackle, 0.25), (overall, 0.25))
    else:
        balance = blend((strength, 0.35), (agility, 0.25), (trucking, 0.2), (overall, 0.2))
    stamina = blend((overall, 0.45), (injury_prone, 0.2), (strength if trench else speed, 0.2), (awareness, 0.15))
    stamina += -4 if player["age"] and player["age"] >= 33 else 0
    consistency = blend((overall, 0.45), (awareness, 0.35), (ctx["composure_base"], 0.2))
    processing_speed_value = blend((awareness, 0.65), (agility, 0.2), (overall, 0.15)) + ctx["exp_mod"] / 2
    hit_power_value = blend((tackle, 0.25), (strength, 0.4), (speed, 0.15), (balance, 0.2))

    ratings = {
        "speed": speed,
        "acceleration": acceleration,
        "agility": agility,
        "balance": balance,
        "strength": strength,
        "play_recognition": blend((awareness, 0.8), (overall, 0.2)),
        "processing_speed": processing_speed_value,
        "discipline": ctx["discipline_base"],
        "composure": ctx["composure_base"],
        "stamina": stamina,
        "durability": injury_prone,
        "consistency": consistency,
        "kick_power": kick_power,
        "kick_accuracy": kick_acc,

        "pass_accuracy_short": throw_acc + 3,
        "pass_accuracy_mid": blend((throw_acc, 0.8), (throw_power, 0.2)),
        "pass_accuracy_deep": blend((throw_acc, 0.55), (throw_power, 0.35), (awareness, 0.1)) - 2,
        "throw_power": throw_power,
        "throw_release": blend((throw_acc, 0.35), (awareness, 0.3), (agility, 0.2), (overall, 0.15)),
        "platform_control": blend((throw_acc, 0.35), (agility, 0.25), (ctx["composure_base"], 0.25), (strength, 0.15)),

        "carry_vision": blend((awareness, 0.45), (trucking, 0.15), (agility, 0.15), (overall, 0.25)),
        "elusiveness": blend((agility, 0.45), (speed, 0.25), (trucking, 0.15), (overall, 0.15)) + max(0, light_mod / 3),
        "contact_power": blend((trucking, 0.45), (strength, 0.35), (balance, 0.2)) + max(0, mass_mod / 4),
        "ball_security": blend((awareness, 0.35), (strength, 0.2), (trucking, 0.2), (ctx["composure_base"], 0.25)),
        "run_patience": blend((awareness, 0.55), (processing := blend((awareness, 0.65), (agility, 0.2), (overall, 0.15)), 0.2), (overall, 0.25)),

        "release_vs_press": blend((route, 0.4), (agility, 0.25), (strength, 0.15), (catching, 0.2)),
        "route_snap": blend((route, 0.45), (agility, 0.35), (acceleration, 0.2)),
        "route_timing": blend((route, 0.5), (awareness, 0.3), (ctx["discipline_base"], 0.2)),
        "hands": catching,
        "contested_catch": blend((catching, 0.45), (strength, 0.2), (balance, 0.2), (ctx["composure_base"], 0.15)),
        "catch_in_traffic": blend((catching, 0.4), (balance, 0.25), (strength, 0.15), (ctx["composure_base"], 0.2)),

        "pass_block_power": blend((pass_block, 0.55), (strength, 0.35), (balance, 0.1)),
        "pass_block_finesse": blend((pass_block, 0.55), (awareness, 0.25), (agility, 0.1), (overall, 0.1)),
        "pass_block_speed": blend((pass_block, 0.65), (awareness, 0.15), (agility, 0.1), (acceleration, 0.05), (overall, 0.05)),
        "run_block_drive": blend((run_block, 0.55), (strength, 0.35), (balance, 0.1)),
        "reach_block": blend((run_block, 0.45), (agility, 0.25), (acceleration, 0.2), (awareness, 0.1)),
        "lead_block": blend((run_block, 0.45), (speed, 0.15), (agility, 0.2), (awareness, 0.2)),
        "block_sustain": blend((run_block, 0.25), (pass_block, 0.25), (strength, 0.2), (stamina, 0.15), (awareness, 0.15)),

        "power_rush": blend((pass_rush, 0.55), (strength, 0.35), (balance, 0.1)),
        "finesse_rush": blend((pass_rush, 0.5), (agility, 0.3), (awareness, 0.2)),
        "speed_rush": blend((pass_rush, 0.5), (speed, 0.2), (acceleration, 0.2), (agility, 0.1)),
        "rush_plan": blend((pass_rush, 0.45), (awareness, 0.35), (processing_speed_value, 0.2)),
        "stunt_execution": blend((pass_rush, 0.4), (awareness, 0.3), (agility, 0.2), (ctx["discipline_base"], 0.1)),
        "double_team_takeon": blend((pass_rush, 0.25), (strength, 0.45), (balance, 0.2), (stamina, 0.1)) + max(0, mass_mod / 4),
        "sack_finish": blend((pass_rush, 0.45), (tackle, 0.25), (agility, 0.15), (awareness, 0.15)),

        "run_diagnostics": blend((awareness, 0.55), (tackle, 0.2), (overall, 0.25)),
        "block_shedding": blend((pass_rush, 0.3), (tackle, 0.25), (strength, 0.3), (balance, 0.15)),
        "gap_integrity": blend((awareness, 0.45), (strength, 0.2), (tackle, 0.2), (ctx["discipline_base"], 0.15)),
        "pursuit_angle": blend((tackle, 0.3), (speed, 0.25), (agility, 0.2), (awareness, 0.25)),
        "edge_contain": blend((tackle, 0.25), (strength, 0.25), (speed, 0.15), (awareness, 0.35)),
        "traffic_navigation": blend((agility, 0.25), (strength, 0.25), (awareness, 0.3), (balance, 0.2)),

        "press_coverage": blend((coverage, 0.45), (strength, 0.2), (agility, 0.2), (awareness, 0.15)),
        "man_coverage": blend((coverage, 0.5), (agility, 0.25), (speed, 0.15), (awareness, 0.1)),
        "zone_coverage": blend((coverage, 0.5), (awareness, 0.35), (processing_speed_value, 0.15)),
        "zone_recovery": blend((coverage, 0.45), (speed, 0.2), (acceleration, 0.2), (agility, 0.15)),
        "ball_skills": blend((coverage, 0.45), (catching, 0.2), (awareness, 0.25), (ctx["composure_base"], 0.1)),
        "coverage_communication": blend((coverage, 0.45), (awareness, 0.35), (ctx["discipline_base"], 0.2)),

        "solo_tackle": tackle,
        "tackle_wrap": blend((tackle, 0.45), (strength, 0.25), (balance, 0.15), (awareness, 0.15)),
        "hit_power": hit_power_value,
        "forced_fumble": blend((tackle, 0.35), (hit_power_value, 0.25), (awareness, 0.25), (agility, 0.15)),
        "open_field_tackle": blend((tackle, 0.45), (agility, 0.2), (speed, 0.15), (awareness, 0.2)),
        "assist_tackle": blend((tackle, 0.4), (strength, 0.25), (balance, 0.2), (awareness, 0.15)),
    }

    if pos in {"K", "P", "LS"}:
        specialist_floor = blend((kick_power, 0.45), (kick_acc, 0.45), (awareness, 0.1))
        for key, group in RATING_GROUPS.items():
            if group != "universal":
                ratings[key] = min(ratings[key], specialist_floor)

    ratings = apply_relevance_caps(player, ratings, roles)
    ratings.update(RATING_OVERRIDES.get((player["first_name"], player["last_name"]), {}))
    capped = rating_profile_caps.apply_caps_to_ratings(
        {key: clamp(value) for key, value in ratings.items()},
        name=f"{player['first_name']} {player['last_name']}",
        position=pos,
        age=player["age"],
        height_in=player["height_in"],
        weight_lbs=player["weight_lbs"],
        overall=overall,
        potential=player["potential"],
    )
    capped.update(RATING_OVERRIDES.get((player["first_name"], player["last_name"]), {}))
    return {key: clamp(value) for key, value in capped.items()}


def group_confidence(player, rating_key, roles):
    group = RATING_GROUPS[rating_key]
    if group == "universal":
        return "high" if nz(player["years_exp"], 0) >= 3 else "medium"

    primary_groups = set()
    for role in roles:
        if role:
            primary_groups.update(PRIMARY_GROUPS_BY_ROLE.get(role, set()))

    if group in primary_groups:
        if nz(player["years_exp"], 0) == 0 or nz(player["overall"]) < 62:
            return "low"
        return "high" if nz(player["overall"]) >= 76 and nz(player["years_exp"], 0) >= 3 else "medium"

    return "low"


def clean_generated_rows(conn, team_id):
    source_marks = ",".join("?" for _ in REPLACED_SOURCES)
    conn.execute(
        f"""
        DELETE FROM player_role_scores
        WHERE season = ?
          AND source IN ({source_marks})
          AND player_id IN (SELECT player_id FROM players WHERE team_id = ?)
        """,
        (SEASON, *REPLACED_SOURCES, team_id),
    )
    conn.execute(
        f"""
        DELETE FROM player_role_assignments
        WHERE season = ?
          AND source IN ({source_marks})
          AND player_id IN (SELECT player_id FROM players WHERE team_id = ?)
        """,
        (SEASON, *REPLACED_SOURCES, team_id),
    )
    conn.execute(
        f"""
        DELETE FROM player_ratings
        WHERE season = ?
          AND source IN ({source_marks})
          AND player_id IN (SELECT player_id FROM players WHERE team_id = ?)
        """,
        (SEASON, *REPLACED_SOURCES, team_id),
    )


def insert_role_assignments(conn, player, primary_role, secondary_role):
    rows = []
    for priority, role_key in ((1, primary_role), (2, secondary_role)):
        if role_key is None:
            continue
        rows.append(
            (
                player["player_id"],
                SEASON,
                role_key,
                priority,
                SOURCE,
                "Initial generated Vikings role assignment; review manually.",
            )
        )
    conn.executemany(
        """
        INSERT INTO player_role_assignments (
            player_id, season, role_key, priority, source, notes
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def insert_player_ratings(conn, player, ratings, roles):
    rows = []
    athletic_override = ATHLETIC_OVERRIDES.get((player["first_name"], player["last_name"]), {})
    for rating_key in sorted(ratings):
        confidence = group_confidence(player, rating_key, roles)
        notes = "Generated from legacy player columns as first-pass sim rating."
        if rating_key in athletic_override and "notes" in athletic_override:
            notes = athletic_override["notes"]
        rows.append(
            (
                player["player_id"],
                SEASON,
                rating_key,
                ratings[rating_key],
                confidence,
                SOURCE,
                notes,
            )
        )

    conn.executemany(
        """
        INSERT INTO player_ratings (
            player_id, season, rating_key, rating_value, confidence, source, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def calculate_role_score(player, ratings, role_key):
    weights = ROLE_WEIGHTS[role_key]
    total = sum(weights.values())
    weighted = sum(ratings[rating_key] * weight for rating_key, weight in weights.items())
    role_fit_score = weighted / total
    readiness_anchor = nz(player["overall"])
    if nz(player["years_exp"], 0) == 0:
        readiness_anchor -= 2
    return round((role_fit_score * 0.75) + (readiness_anchor * 0.25), 2)


def insert_role_scores(conn, player, ratings, roles):
    rows = []
    score_floors = ROLE_SCORE_FLOORS.get((player["first_name"], player["last_name"]), {})
    for role_key in roles:
        if role_key is None:
            continue
        role_score = calculate_role_score(player, ratings, role_key)
        if role_key in score_floors:
            role_score = max(role_score, score_floors[role_key])
        rows.append(
            (
                player["player_id"],
                SEASON,
                role_key,
                "default",
                role_score,
                SOURCE,
            )
        )
    conn.executemany(
        """
        INSERT INTO player_role_scores (
            player_id, season, role_key, scheme_key, role_score, source
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def seed_team(conn, team_abbr):
    team_id = get_team_id(conn, team_abbr)
    players = load_team_players(conn, team_id)

    clean_generated_rows(conn, team_id)

    role_assignment_count = 0
    rating_count = 0
    role_score_count = 0
    skipped_roles = []

    for player in players:
        roles = choose_roles(player)
        ratings = generate_ratings(player, roles)
        rating_count += insert_player_ratings(conn, player, ratings, roles)

        if roles[0] is None:
            skipped_roles.append(f"{player['first_name']} {player['last_name']} ({player['position']})")
            continue

        role_assignment_count += insert_role_assignments(conn, player, roles[0], roles[1])
        role_score_count += insert_role_scores(conn, player, ratings, roles)

    return {
        "team": team_abbr,
        "players": len(players),
        "ratings": rating_count,
        "role_assignments": role_assignment_count,
        "role_scores": role_score_count,
        "skipped_roles": skipped_roles,
    }


def print_team_summary(conn, result):
    print(f"{result['team']} sim ratings seeded.")
    print(f"  Players rated: {result['players']}")
    print(f"  player_ratings rows inserted: {result['ratings']}")
    print(f"  player_role_assignments rows inserted: {result['role_assignments']}")
    print(f"  player_role_scores rows inserted: {result['role_scores']}")
    if result["skipped_roles"]:
        print("  Players rated but not assigned hidden role scores yet:")
        for name in result["skipped_roles"]:
            print(f"    - {name}")

    print(f"\nTop {result['team']} hidden role scores:")
    for row in conn.execute(
        """
        SELECT player_name, position, role_name, role_score
        FROM player_role_scores_view
        WHERE team = ? AND season = ? AND source = ?
        ORDER BY role_score DESC, player_name
        LIMIT 15
        """,
        (result["team"], SEASON, SOURCE),
    ):
        print(f"  {row['player_name']} ({row['position']}), {row['role_name']}: {row['role_score']:.2f}")


def print_league_summary(conn, results):
    total_players = sum(result["players"] for result in results)
    total_ratings = sum(result["ratings"] for result in results)
    total_role_assignments = sum(result["role_assignments"] for result in results)
    total_role_scores = sum(result["role_scores"] for result in results)
    total_skipped = sum(len(result["skipped_roles"]) for result in results)

    print("League sim ratings seeded.")
    print(f"  Teams seeded: {len(results)}")
    print(f"  Players rated: {total_players}")
    print(f"  player_ratings rows inserted: {total_ratings}")
    print(f"  player_role_assignments rows inserted: {total_role_assignments}")
    print(f"  player_role_scores rows inserted: {total_role_scores}")
    print(f"  Players rated but not assigned hidden role scores yet: {total_skipped}")

    print("\nTop league hidden role scores:")
    for row in conn.execute(
        """
        SELECT team, player_name, position, role_name, role_score
        FROM player_role_scores_view
        WHERE season = ? AND source = ?
        ORDER BY role_score DESC, player_name
        LIMIT 20
        """,
        (SEASON, SOURCE),
    ):
        print(f"  {row['team']} {row['player_name']} ({row['position']}), {row['role_name']}: {row['role_score']:.2f}")


def main():
    parser = argparse.ArgumentParser(description="Seed first-pass sim ratings and hidden role scores.")
    parser.add_argument("--team", default=DEFAULT_TEAM_ABBR, help="Team abbreviation to seed. Defaults to MIN.")
    parser.add_argument("--all-teams", action="store_true", help="Seed every team in the database.")
    parser.add_argument("--no-backup", action="store_true", help="Skip timestamped database backup.")
    args = parser.parse_args()

    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)

    if not args.no_backup:
        backup_path = backup_database()
        print(f"Backup created: {backup_path}")

    conn = get_connection()
    try:
        with conn:
            teams = load_team_abbreviations(conn) if args.all_teams else [args.team.upper()]
            results = [seed_team(conn, team_abbr) for team_abbr in teams]

        if args.all_teams:
            print_league_summary(conn, results)
        else:
            print_team_summary(conn, results[0])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
