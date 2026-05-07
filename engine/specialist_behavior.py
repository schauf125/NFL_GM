"""Special teams and specialist behavior profiles.

This is a light modifier layer for K/P/LS players and core special teamers.
It should not make special teams a separate game engine; it gives kickers,
punters, long snappers, gunners, return blockers, and block specialists a
small amount of style and value inside the existing sim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SpecialistBehaviorProfile:
    label: str
    kick_operation: float
    kickoff_control: float
    punt_hang_time: float
    punt_placement: float
    snap_accuracy: float
    lane_release: float
    gunner_speed: float
    return_lane_vision: float
    block_timing: float
    coverage_tackle: float
    penalty_control: float
    notes: str = ""

    def as_dict(self) -> dict[str, float | str]:
        return {
            "label": self.label,
            "kick_operation": round(self.kick_operation, 1),
            "kickoff_control": round(self.kickoff_control, 1),
            "punt_hang_time": round(self.punt_hang_time, 1),
            "punt_placement": round(self.punt_placement, 1),
            "snap_accuracy": round(self.snap_accuracy, 1),
            "lane_release": round(self.lane_release, 1),
            "gunner_speed": round(self.gunner_speed, 1),
            "return_lane_vision": round(self.return_lane_vision, 1),
            "block_timing": round(self.block_timing, 1),
            "coverage_tackle": round(self.coverage_tackle, 1),
            "penalty_control": round(self.penalty_control, 1),
            "notes": self.notes,
        }


SPECIALIST_BEHAVIOR_FIELDS = (
    "kick_operation",
    "kickoff_control",
    "punt_hang_time",
    "punt_placement",
    "snap_accuracy",
    "lane_release",
    "gunner_speed",
    "return_lane_vision",
    "block_timing",
    "coverage_tackle",
    "penalty_control",
)


SPECIALIST_POSITIONS = {"K", "P", "LS"}
SPECIAL_TEAMS_CORE_POSITIONS = {"RB", "FB", "WR", "TE", "CB", "NB", "FS", "SS", "S", "ILB", "LB", "OLB", "EDGE"}


def profile(
    label: str,
    kick: int,
    kickoff: int,
    hang: int,
    placement: int,
    snap: int,
    lane: int,
    gunner: int,
    return_lane: int,
    block: int,
    tackle: int,
    penalty: int,
    notes: str = "",
) -> SpecialistBehaviorProfile:
    return SpecialistBehaviorProfile(
        label=label,
        kick_operation=kick,
        kickoff_control=kickoff,
        punt_hang_time=hang,
        punt_placement=placement,
        snap_accuracy=snap,
        lane_release=lane,
        gunner_speed=gunner,
        return_lane_vision=return_lane,
        block_timing=block,
        coverage_tackle=tackle,
        penalty_control=penalty,
        notes=notes,
    )


def normalize_name(name: str) -> str:
    clean = re.sub(r"[^a-z0-9 ]+", "", name.lower())
    clean = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", clean)
    return " ".join(clean.split())


def average(values, default: float = 50.0) -> float:
    values = list(values)
    if not values:
        return default
    return sum(values) / len(values)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def clamp_rating(value: float) -> float:
    return float(clamp(value, 1.0, 99.0))


def profile_from_mapping(values: dict[str, Any]) -> SpecialistBehaviorProfile:
    return SpecialistBehaviorProfile(
        label=str(values.get("label") or "Stored Specialist Profile"),
        kick_operation=float(values.get("kick_operation", 50)),
        kickoff_control=float(values.get("kickoff_control", 50)),
        punt_hang_time=float(values.get("punt_hang_time", 50)),
        punt_placement=float(values.get("punt_placement", 50)),
        snap_accuracy=float(values.get("snap_accuracy", 50)),
        lane_release=float(values.get("lane_release", 50)),
        gunner_speed=float(values.get("gunner_speed", 50)),
        return_lane_vision=float(values.get("return_lane_vision", 50)),
        block_timing=float(values.get("block_timing", 50)),
        coverage_tackle=float(values.get("coverage_tackle", 50)),
        penalty_control=float(values.get("penalty_control", 50)),
        notes=str(values.get("notes") or ""),
    )


def profile_to_db_tuple(
    player_or_prospect_id: int,
    season: int | None,
    profile: SpecialistBehaviorProfile,
    source: str,
):
    base = [int(player_or_prospect_id)]
    if season is not None:
        base.append(int(season))
    base.extend(
        [
            profile.label,
            int(round(profile.kick_operation)),
            int(round(profile.kickoff_control)),
            int(round(profile.punt_hang_time)),
            int(round(profile.punt_placement)),
            int(round(profile.snap_accuracy)),
            int(round(profile.lane_release)),
            int(round(profile.gunner_speed)),
            int(round(profile.return_lane_vision)),
            int(round(profile.block_timing)),
            int(round(profile.coverage_tackle)),
            int(round(profile.penalty_control)),
            source,
            profile.notes,
        ]
    )
    return tuple(base)


def ensure_player_specialist_behavior_schema(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS player_specialist_behavior_profiles (
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
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
            source TEXT NOT NULL DEFAULT 'manual',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (player_id, season)
        );

        CREATE INDEX IF NOT EXISTS idx_player_specialist_behavior_profiles_season
            ON player_specialist_behavior_profiles(season, label);
        """
    )


def player_specialist_behavior_table_exists(con) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'player_specialist_behavior_profiles'
        """
    ).fetchone()
    return bool(row)


SPECIALIST_STYLE_OVERRIDES: dict[str, SpecialistBehaviorProfile] = {
    "aj cole": profile("Elite Hang-Time Punter", 42, 45, 94, 90, 52, 50, 50, 48, 50, 48, 88),
    "andrew depaola": profile("Elite Long Snapper", 44, 44, 50, 50, 94, 62, 56, 48, 58, 62, 90),
    "ashton dulin": profile("Coverage WR Ace", 38, 40, 42, 42, 45, 86, 86, 72, 72, 82, 84),
    "blake gillikin": profile("Placement Punter", 42, 44, 78, 82, 52, 50, 50, 48, 50, 48, 80),
    "braden mann": profile("Directional Punter", 42, 44, 76, 80, 52, 50, 50, 48, 50, 48, 78),
    "bradley pinion": profile("Kickoff-Punt Hybrid", 42, 76, 80, 78, 52, 52, 52, 48, 50, 50, 82),
    "brandon aubrey": profile("Elite Power Kicker", 94, 92, 45, 45, 52, 48, 48, 45, 48, 45, 86),
    "brandon mcmanus": profile("Veteran Power Kicker", 82, 84, 45, 45, 52, 48, 48, 45, 48, 45, 78),
    "brenden schooler": profile(
        "Elite Punt Gunner",
        35,
        40,
        42,
        42,
        45,
        94,
        96,
        76,
        86,
        90,
        82,
        "Public All-Pro special-teams ace profile; high-end punt coverage and block timing.",
    ),
    "bryan anger": profile("Veteran Placement Punter", 42, 44, 80, 82, 52, 50, 50, 48, 50, 48, 86),
    "bryce baringer": profile("Big-Leg Young Punter", 42, 44, 86, 76, 52, 52, 52, 48, 52, 50, 76),
    "cairo santos": profile("Reliable Operation Kicker", 84, 76, 45, 45, 52, 48, 48, 45, 48, 45, 88),
    "cam little": profile("Young Power Kicker", 80, 84, 45, 45, 52, 48, 48, 45, 48, 45, 74),
    "cameron dicker": profile("Reliable Power Kicker", 88, 86, 45, 45, 52, 48, 48, 45, 48, 45, 84),
    "chase mclaughlin": profile("Steady Kicker", 80, 78, 45, 45, 52, 48, 48, 45, 48, 45, 78),
    "chris boswell": profile("Veteran Clutch Kicker", 90, 84, 45, 45, 52, 48, 48, 45, 48, 45, 90),
    "christian kuntz": profile("Core Long Snapper", 44, 44, 50, 50, 82, 62, 56, 48, 58, 62, 82),
    "cj ham": profile("Return-Unit Fullback", 38, 40, 42, 42, 45, 74, 68, 82, 66, 78, 84),
    "corey bojorquez": profile("Hang-Time Punter", 42, 44, 84, 76, 52, 50, 50, 48, 50, 48, 76),
    "dane belton": profile("Coverage Safety", 38, 40, 42, 42, 45, 78, 80, 68, 74, 80, 78),
    "delshawn phillips": profile("Coverage LB Ace", 36, 40, 42, 42, 45, 82, 82, 68, 76, 84, 78),
    "devon key": profile("Coverage Safety Ace", 36, 40, 42, 42, 45, 86, 88, 70, 80, 86, 78),
    "evan mcpherson": profile("Power Kicker", 82, 84, 45, 45, 52, 48, 48, 45, 48, 45, 76),
    "george odum": profile("Veteran Coverage Ace", 36, 40, 42, 42, 45, 88, 88, 70, 82, 88, 80),
    "harrison butker": profile("Elite Operation Kicker", 92, 88, 45, 45, 52, 48, 48, 45, 48, 45, 90),
    "jake bates": profile("Big-Leg Kicker", 84, 90, 45, 45, 52, 48, 48, 45, 48, 45, 76),
    "jake elliott": profile("Reliable Clutch Kicker", 86, 80, 45, 45, 52, 48, 48, 45, 48, 45, 88),
    "jake moody": profile("Young Range Kicker", 80, 82, 45, 45, 52, 48, 48, 45, 48, 45, 74),
    "james pierre": profile("Gunner Corner", 36, 40, 42, 42, 45, 84, 86, 68, 74, 82, 78),
    "james winchester": profile("Elite Long Snapper", 44, 44, 50, 50, 92, 62, 58, 48, 60, 64, 90),
    "jeremy reaves": profile("Coverage Safety Ace", 36, 40, 42, 42, 45, 88, 88, 70, 80, 86, 82),
    "jj jansen": profile("Veteran Long Snapper", 44, 44, 50, 50, 86, 58, 54, 48, 56, 58, 90),
    "jk scott": profile("Hang-Time Placement Punter", 42, 44, 82, 80, 52, 50, 50, 48, 50, 48, 82),
    "joe cardona": profile("Veteran Long Snapper", 44, 44, 50, 50, 86, 60, 56, 48, 58, 60, 88),
    "joey slye": profile("Power Kicker", 78, 86, 45, 45, 52, 48, 48, 45, 48, 45, 70),
    "johnny hekker": profile("Veteran Directional Punter", 42, 44, 84, 90, 54, 50, 50, 48, 52, 50, 92),
    "jon rhattigan": profile("Coverage LB Ace", 36, 40, 42, 42, 45, 82, 82, 68, 76, 84, 80),
    "jon weeks": profile("Veteran Long Snapper", 44, 44, 50, 50, 84, 58, 54, 48, 56, 58, 90),
    "josh harris": profile("Elite Long Snapper", 44, 44, 50, 50, 90, 60, 56, 48, 58, 60, 90),
    "jt gray": profile("Elite Coverage Captain", 36, 40, 42, 42, 45, 90, 90, 72, 82, 88, 86),
    "kaimi fairbairn": profile("Reliable Kicker", 86, 80, 45, 45, 52, 48, 48, 45, 48, 45, 86),
    "kelee ringo": profile("Speed Gunner Corner", 36, 40, 42, 42, 45, 84, 90, 68, 72, 78, 76),
    "khadarel hodge": profile("Coverage WR Ace", 36, 40, 42, 42, 45, 86, 86, 74, 74, 82, 78),
    "logan cooke": profile("Elite Placement Punter", 42, 44, 88, 92, 52, 50, 50, 48, 50, 48, 88),
    "luke gifford": profile("Coverage LB Ace", 36, 40, 42, 42, 45, 82, 82, 68, 76, 84, 78),
    "luke rhodes": profile("Elite Long Snapper", 44, 44, 50, 50, 90, 60, 56, 48, 58, 62, 88),
    "matt gay": profile("Range Kicker", 82, 84, 45, 45, 52, 48, 48, 45, 48, 45, 76),
    "matt orzech": profile("Reliable Long Snapper", 44, 44, 50, 50, 84, 58, 54, 48, 56, 58, 84),
    "michael dickson": profile("Elite Hang-Time Punter", 42, 44, 92, 86, 52, 50, 50, 48, 50, 48, 84),
    "michael hoecht": profile("Big-Body Block Specialist", 36, 40, 42, 42, 45, 74, 70, 62, 86, 78, 76),
    "miles killebrew": profile("Block/Coverage Ace", 36, 40, 42, 42, 45, 88, 86, 68, 92, 86, 80),
    "morgan cox": profile("Veteran Long Snapper", 44, 44, 50, 50, 88, 58, 54, 48, 56, 58, 90),
    "nick bellore": profile("Coverage Captain", 36, 40, 42, 42, 45, 78, 76, 70, 76, 84, 88),
    "nick folk": profile("Veteran Accuracy Kicker", 84, 72, 45, 45, 52, 48, 48, 45, 48, 45, 90),
    "reid ferguson": profile("Reliable Long Snapper", 44, 44, 50, 50, 86, 58, 54, 48, 56, 58, 88),
    "ross matiscik": profile("Elite Long Snapper", 44, 44, 50, 50, 94, 62, 56, 48, 58, 62, 90),
    "sam franklin": profile("Coverage Safety Ace", 36, 40, 42, 42, 45, 86, 86, 70, 80, 86, 78),
    "siran neal": profile("Coverage Corner Ace", 36, 40, 42, 42, 45, 84, 86, 68, 76, 82, 78),
    "taybor pepper": profile("Reliable Long Snapper", 44, 44, 50, 50, 84, 58, 54, 48, 56, 58, 84),
    "tavierre thomas": profile("Coverage Nickel Ace", 36, 40, 42, 42, 45, 86, 86, 70, 76, 84, 78),
    "thomas hennessy": profile("Reliable Long Snapper", 44, 44, 50, 50, 84, 58, 54, 48, 56, 58, 86),
    "tommy townsend": profile("Placement Punter", 42, 44, 82, 84, 52, 50, 50, 48, 50, 48, 84),
    "tory taylor": profile("Big-Leg Directional Punter", 42, 44, 86, 82, 52, 52, 52, 48, 52, 50, 78),
    "trent sherfield": profile("Coverage WR Ace", 36, 40, 42, 42, 45, 84, 84, 72, 72, 82, 82),
    "trent sieg": profile("Reliable Long Snapper", 44, 44, 50, 50, 84, 58, 54, 48, 56, 58, 84),
    "tress way": profile("Veteran Directional Punter", 42, 44, 82, 88, 52, 50, 50, 48, 50, 48, 90),
    "tyler bass": profile("Power Kicker", 78, 84, 45, 45, 52, 48, 48, 45, 48, 45, 74),
    "tyler ott": profile("Reliable Long Snapper", 44, 44, 50, 50, 84, 58, 54, 48, 56, 58, 84),
    "wil lutz": profile("Veteran Kicker", 82, 78, 45, 45, 52, 48, 48, 45, 48, 45, 84),
    "will reichard": profile("Young Accuracy Kicker", 86, 80, 45, 45, 52, 48, 48, 45, 48, 45, 80),
    "zach wood": profile("Reliable Long Snapper", 44, 44, 50, 50, 86, 58, 54, 48, 56, 58, 86),
}


class RatingProfileSource:
    def __init__(self, ratings: dict[str, int | float], name: str = "Generated Specialist", position: str = "K") -> None:
        self.name = name
        self.position = position
        self.ratings = ratings

    def rating(self, key: str, default: float = 50.0) -> float:
        return float(self.ratings.get(key, default))


def infer_profile(player: PlayerSnapshot) -> SpecialistBehaviorProfile:
    position = str(getattr(player, "position", "")).upper()
    mental = average(
        [
            player.rating("discipline"),
            player.rating("composure"),
            player.rating("consistency"),
            player.rating("processing_speed"),
        ]
    )
    kick = clamp_rating(
        player.rating("kick_accuracy") * 0.42
        + player.rating("kick_power") * 0.24
        + player.rating("composure") * 0.16
        + player.rating("discipline") * 0.10
        + player.rating("consistency") * 0.08
    )
    kickoff = clamp_rating(
        player.rating("kick_power") * 0.42
        + player.rating("kick_accuracy") * 0.22
        + player.rating("strength") * 0.12
        + player.rating("discipline") * 0.12
        + player.rating("composure") * 0.12
    )
    hang = clamp_rating(
        player.rating("kick_power") * 0.36
        + player.rating("kick_accuracy") * 0.24
        + player.rating("strength") * 0.12
        + player.rating("composure") * 0.12
        + player.rating("discipline") * 0.10
        + player.rating("consistency") * 0.06
    )
    placement = clamp_rating(
        player.rating("kick_accuracy") * 0.40
        + player.rating("kick_power") * 0.18
        + player.rating("discipline") * 0.16
        + player.rating("composure") * 0.14
        + player.rating("processing_speed") * 0.06
        + player.rating("consistency") * 0.06
    )
    snap = clamp_rating(
        player.rating("discipline") * 0.30
        + player.rating("consistency") * 0.24
        + player.rating("processing_speed") * 0.16
        + player.rating("composure") * 0.14
        + player.rating("strength") * 0.10
        + player.rating("stamina") * 0.06
    )
    lane = clamp_rating(
        player.rating("acceleration") * 0.20
        + player.rating("speed") * 0.20
        + player.rating("agility") * 0.14
        + player.rating("play_recognition") * 0.14
        + player.rating("pursuit_angle") * 0.12
        + player.rating("stamina") * 0.10
        + player.rating("discipline") * 0.10
    )
    gunner = clamp_rating(
        player.rating("speed") * 0.24
        + player.rating("acceleration") * 0.22
        + player.rating("agility") * 0.14
        + player.rating("pursuit_angle") * 0.12
        + player.rating("open_field_tackle") * 0.10
        + player.rating("play_recognition") * 0.10
        + player.rating("stamina") * 0.08
    )
    return_lane = clamp_rating(
        player.rating("carry_vision") * 0.20
        + player.rating("elusiveness") * 0.18
        + player.rating("acceleration") * 0.16
        + player.rating("speed") * 0.16
        + player.rating("agility") * 0.12
        + player.rating("ball_security") * 0.10
        + player.rating("play_recognition") * 0.08
    )
    block = clamp_rating(
        player.rating("play_recognition") * 0.18
        + player.rating("acceleration") * 0.16
        + player.rating("agility") * 0.14
        + player.rating("strength") * 0.12
        + player.rating("hit_power") * 0.12
        + player.rating("pursuit_angle") * 0.10
        + player.rating("processing_speed") * 0.10
        + player.rating("discipline") * 0.08
    )
    coverage = clamp_rating(
        player.rating("open_field_tackle") * 0.22
        + player.rating("solo_tackle") * 0.18
        + player.rating("tackle_wrap") * 0.18
        + player.rating("pursuit_angle") * 0.16
        + player.rating("assist_tackle") * 0.10
        + player.rating("speed") * 0.08
        + player.rating("play_recognition") * 0.08
    )
    penalty = clamp_rating(
        player.rating("discipline") * 0.44
        + player.rating("composure") * 0.20
        + player.rating("consistency") * 0.18
        + player.rating("processing_speed") * 0.10
        + player.rating("play_recognition") * 0.08
    )

    if position == "K":
        label = "Inferred Kicker"
    elif position == "P":
        label = "Inferred Punter"
    elif position == "LS":
        label = "Inferred Long Snapper"
    elif average([lane, gunner, coverage]) >= 78:
        label = "Inferred Coverage Ace"
    elif block >= 78:
        label = "Inferred Block Specialist"
    elif return_lane >= 78:
        label = "Inferred Return Helper"
    elif mental >= 76:
        label = "Inferred Reliable Teamer"
    else:
        label = "Inferred Standard ST Profile"

    return SpecialistBehaviorProfile(
        label=label,
        kick_operation=kick,
        kickoff_control=kickoff,
        punt_hang_time=hang,
        punt_placement=placement,
        snap_accuracy=snap,
        lane_release=lane,
        gunner_speed=gunner,
        return_lane_vision=return_lane,
        block_timing=block,
        coverage_tackle=coverage,
        penalty_control=penalty,
        notes="Inferred from current special teams and universal ratings.",
    )


def with_deltas(
    base: SpecialistBehaviorProfile,
    *,
    label: str,
    notes: str,
    **deltas: float,
) -> SpecialistBehaviorProfile:
    values = base.as_dict()
    for field in SPECIALIST_BEHAVIOR_FIELDS:
        values[field] = clamp(float(values[field]) + float(deltas.get(field, 0.0)), 1, 99)
    values["label"] = label
    values["notes"] = notes
    return profile_from_mapping(values)


def generated_specialist_behavior_profile(
    archetype: str,
    ratings: dict[str, int | float],
    *,
    position: str = "K",
) -> SpecialistBehaviorProfile:
    position = str(position or "").upper()
    base = infer_profile(RatingProfileSource(ratings, position=position))
    archetype = str(archetype or "").strip()
    if archetype == "Accurate kicker":
        return with_deltas(base, label="Generated Accurate Kicker", notes="Generated from draft specialist archetype.", kick_operation=6, kickoff_control=-2, penalty_control=4)
    if archetype == "Big-leg kicker":
        return with_deltas(base, label="Generated Big-Leg Kicker", notes="Generated from draft specialist archetype.", kick_operation=2, kickoff_control=8, penalty_control=-1)
    if archetype == "Clutch kicker":
        return with_deltas(base, label="Generated Clutch Kicker", notes="Generated from draft specialist archetype.", kick_operation=5, kickoff_control=1, penalty_control=6)
    if archetype == "Field-position punter":
        return with_deltas(base, label="Generated Field-Position Punter", notes="Generated from draft specialist archetype.", punt_hang_time=3, punt_placement=8, penalty_control=4)
    if archetype == "Big-leg punter":
        return with_deltas(base, label="Generated Big-Leg Punter", notes="Generated from draft specialist archetype.", punt_hang_time=8, punt_placement=2, penalty_control=-1)
    if archetype == "Directional punter":
        return with_deltas(base, label="Generated Directional Punter", notes="Generated from draft specialist archetype.", punt_hang_time=4, punt_placement=7, penalty_control=5)
    if archetype == "Long snapper" or position == "LS":
        return with_deltas(base, label="Generated Long Snapper", notes="Generated from draft specialist archetype.", snap_accuracy=10, lane_release=3, coverage_tackle=4, penalty_control=5)
    if position in SPECIAL_TEAMS_CORE_POSITIONS:
        coverage = average([base.lane_release, base.gunner_speed, base.coverage_tackle])
        if coverage >= 78:
            return with_deltas(base, label="Generated Coverage Teamer", notes="Generated from draft prospect special teams upside.", lane_release=4, gunner_speed=4, coverage_tackle=4, penalty_control=2)
        if base.block_timing >= 76:
            return with_deltas(base, label="Generated Block Teamer", notes="Generated from draft prospect special teams upside.", block_timing=5, lane_release=2, penalty_control=1)
        if base.return_lane_vision >= 76:
            return with_deltas(base, label="Generated Return Helper", notes="Generated from draft prospect special teams upside.", return_lane_vision=5, lane_release=2, penalty_control=1)
    return with_deltas(base, label="Generated Standard ST Profile", notes="Generated from draft prospect true ratings.")


def metadata_profile(player) -> SpecialistBehaviorProfile | None:
    metadata = getattr(player, "metadata", None) or {}
    stored = metadata.get("specialist_behavior_profile")
    if isinstance(stored, SpecialistBehaviorProfile):
        return stored
    if isinstance(stored, dict):
        return profile_from_mapping(stored)
    return None


def metadata_source(player) -> str | None:
    metadata = getattr(player, "metadata", None) or {}
    source = metadata.get("specialist_behavior_source")
    return str(source) if source is not None else None


def specialist_behavior_source(player) -> str:
    stored = metadata_profile(player)
    stored_source = metadata_source(player)
    if stored and stored_source != "specialist_behavior_named_seed":
        return "stored"
    if normalize_name(player.name) in SPECIALIST_STYLE_OVERRIDES:
        return "named"
    if stored:
        return "stored"
    return "inferred"


def specialist_behavior_profile(player: PlayerSnapshot) -> SpecialistBehaviorProfile:
    stored = metadata_profile(player)
    stored_source = metadata_source(player)
    if stored and stored_source != "specialist_behavior_named_seed":
        return stored
    named = SPECIALIST_STYLE_OVERRIDES.get(normalize_name(player.name))
    if named:
        return named
    if stored:
        return stored
    return infer_profile(player)
