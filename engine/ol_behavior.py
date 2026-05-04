"""Offensive line style profiles for sim and tick-level play behavior.

These are football behavior traits, not hidden personality traits. They give
the resolver a way to make offensive linemen with similar ratings play
differently. All values are 0-100, where 50 is neutral.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OLBehaviorProfile:
    label: str
    pass_set_patience: float
    mirror_vs_speed: float
    anchor_vs_power: float
    hand_timing: float
    stunt_awareness: float
    drive_finish: float
    reach_range: float
    combo_timing: float
    second_level_climb: float
    penalty_control: float
    notes: str = ""

    def as_dict(self) -> dict[str, float | str]:
        return {
            "label": self.label,
            "pass_set_patience": round(self.pass_set_patience, 1),
            "mirror_vs_speed": round(self.mirror_vs_speed, 1),
            "anchor_vs_power": round(self.anchor_vs_power, 1),
            "hand_timing": round(self.hand_timing, 1),
            "stunt_awareness": round(self.stunt_awareness, 1),
            "drive_finish": round(self.drive_finish, 1),
            "reach_range": round(self.reach_range, 1),
            "combo_timing": round(self.combo_timing, 1),
            "second_level_climb": round(self.second_level_climb, 1),
            "penalty_control": round(self.penalty_control, 1),
            "notes": self.notes,
        }


OL_BEHAVIOR_FIELDS = (
    "pass_set_patience",
    "mirror_vs_speed",
    "anchor_vs_power",
    "hand_timing",
    "stunt_awareness",
    "drive_finish",
    "reach_range",
    "combo_timing",
    "second_level_climb",
    "penalty_control",
)


def profile(
    label: str,
    pass_set: int,
    mirror: int,
    anchor: int,
    hands: int,
    stunts: int,
    drive: int,
    reach: int,
    combo: int,
    climb: int,
    penalty: int,
    notes: str = "",
) -> OLBehaviorProfile:
    return OLBehaviorProfile(
        label=label,
        pass_set_patience=pass_set,
        mirror_vs_speed=mirror,
        anchor_vs_power=anchor,
        hand_timing=hands,
        stunt_awareness=stunts,
        drive_finish=drive,
        reach_range=reach,
        combo_timing=combo,
        second_level_climb=climb,
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


def profile_from_mapping(values: dict[str, Any]) -> OLBehaviorProfile:
    return OLBehaviorProfile(
        label=str(values.get("label") or "Stored OL Profile"),
        pass_set_patience=float(values.get("pass_set_patience", 50)),
        mirror_vs_speed=float(values.get("mirror_vs_speed", 50)),
        anchor_vs_power=float(values.get("anchor_vs_power", 50)),
        hand_timing=float(values.get("hand_timing", 50)),
        stunt_awareness=float(values.get("stunt_awareness", 50)),
        drive_finish=float(values.get("drive_finish", 50)),
        reach_range=float(values.get("reach_range", 50)),
        combo_timing=float(values.get("combo_timing", 50)),
        second_level_climb=float(values.get("second_level_climb", 50)),
        penalty_control=float(values.get("penalty_control", 50)),
        notes=str(values.get("notes") or ""),
    )


def profile_to_db_tuple(player_or_prospect_id: int, season: int | None, profile: OLBehaviorProfile, source: str):
    base = [
        int(player_or_prospect_id),
    ]
    if season is not None:
        base.append(int(season))
    base.extend(
        [
            profile.label,
            int(round(profile.pass_set_patience)),
            int(round(profile.mirror_vs_speed)),
            int(round(profile.anchor_vs_power)),
            int(round(profile.hand_timing)),
            int(round(profile.stunt_awareness)),
            int(round(profile.drive_finish)),
            int(round(profile.reach_range)),
            int(round(profile.combo_timing)),
            int(round(profile.second_level_climb)),
            int(round(profile.penalty_control)),
            source,
            profile.notes,
        ]
    )
    return tuple(base)


def ensure_player_ol_behavior_schema(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS player_ol_behavior_profiles (
            player_id INTEGER NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            season INTEGER NOT NULL,
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
            source TEXT NOT NULL DEFAULT 'manual',
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (player_id, season)
        );

        CREATE INDEX IF NOT EXISTS idx_player_ol_behavior_profiles_season
            ON player_ol_behavior_profiles(season, label);
        """
    )


def player_ol_behavior_table_exists(con) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'player_ol_behavior_profiles'
        """
    ).fetchone()
    return bool(row)


OL_STYLE_OVERRIDES: dict[str, OLBehaviorProfile] = {
    "andrew thomas": profile("Balanced Franchise Tackle", 88, 90, 86, 88, 84, 78, 82, 82, 76, 86),
    "blake brandel": profile("Steady Interior Utility", 74, 70, 78, 76, 78, 78, 72, 80, 72, 80),
    "brian oneill": profile("Smooth Edge Protector", 84, 86, 76, 82, 80, 72, 82, 76, 78, 84),
    "braden smith": profile("Power Right Tackle", 78, 72, 86, 78, 76, 84, 70, 80, 70, 80),
    "charles cross": profile("Young Mirror Tackle", 82, 88, 76, 82, 78, 70, 78, 74, 72, 76),
    "christian darrisaw": profile(
        "Athletic Drive Tackle",
        86,
        90,
        84,
        86,
        82,
        88,
        86,
        84,
        82,
        84,
        "High-end tackle with pass-pro mirror skill and movement value in the run game.",
    ),
    "chris lindstrom": profile("Elite Drive Guard", 82, 78, 90, 86, 84, 96, 82, 92, 86, 90),
    "creed humphrey": profile("Line-Calling Center", 88, 82, 88, 90, 96, 88, 82, 96, 84, 92),
    "dion dawkins": profile("Veteran Power Tackle", 82, 80, 86, 82, 84, 84, 76, 84, 76, 84),
    "donovan jackson": profile("Rookie Drive Guard", 72, 70, 82, 76, 76, 84, 72, 82, 74, 76),
    "elgton jenkins": profile("Versatile Interior Technician", 84, 80, 86, 86, 88, 84, 80, 88, 78, 88),
    "erik mccoy": profile("Smart Zone Center", 86, 80, 84, 86, 92, 82, 84, 92, 84, 90),
    "frank ragnow": profile("Power Line Caller", 88, 78, 94, 88, 94, 92, 78, 96, 80, 92),
    "gareth bolles": profile("Athletic Edge Tackle", 78, 84, 76, 78, 74, 78, 84, 76, 84, 74),
    "graham glasgow": profile("Steady Interior Utility", 76, 72, 80, 76, 82, 80, 72, 84, 70, 82),
    "ikem ekwonu": profile("Explosive Drive Tackle", 72, 74, 86, 74, 70, 92, 78, 80, 82, 66),
    "isaac seumalo": profile("Veteran Interior Stabilizer", 82, 76, 84, 84, 88, 82, 74, 88, 72, 90),
    "jake matthews": profile("Steady Veteran Tackle", 84, 82, 82, 84, 86, 76, 74, 82, 70, 90),
    "jason kelce": profile("Legendary Movement Center", 92, 86, 78, 94, 98, 88, 96, 98, 94, 94),
    "jawaan taylor": profile("Aggressive Pass Tackle", 78, 84, 76, 78, 76, 72, 78, 72, 74, 62),
    "joe alt": profile("Rookie Prototype Tackle", 82, 86, 84, 82, 78, 80, 82, 78, 78, 78),
    "joe thuney": profile("Elite Interior Technician", 90, 84, 88, 94, 92, 86, 82, 92, 78, 96),
    "john michael schmitz": profile("Young Combo Center", 76, 72, 82, 78, 84, 84, 74, 88, 74, 78),
    "jonah jackson": profile("Drive Guard", 76, 72, 84, 78, 78, 88, 72, 84, 74, 76),
    "jordan mailata": profile("Massive Movement Tackle", 78, 80, 94, 76, 74, 92, 82, 82, 82, 74),
    "landon dickerson": profile("Power Combo Guard", 78, 74, 90, 82, 86, 94, 76, 92, 78, 82),
    "lane johnson": profile("Elite Right Tackle", 92, 94, 90, 90, 88, 88, 86, 88, 84, 94),
    "laremy tunsil": profile("Premier Pass Protector", 94, 94, 84, 92, 84, 72, 78, 76, 72, 78),
    "lloyd cushenberry": profile("Steady Line Caller", 82, 76, 84, 82, 90, 80, 74, 90, 72, 86),
    "mike mcglinchey": profile("Drive Right Tackle", 76, 74, 84, 76, 76, 88, 76, 82, 78, 74),
    "paris johnson": profile("Long Athletic Tackle", 78, 84, 78, 80, 76, 76, 82, 76, 78, 76),
    "penei sewell": profile("Complete Tone-Setter", 92, 92, 94, 92, 88, 96, 90, 92, 90, 90),
    "quenton nelson": profile("Classic Mauling Guard", 82, 78, 96, 88, 86, 98, 76, 92, 76, 88),
    "rashawn slater": profile("Elite Mirror Tackle", 90, 94, 84, 90, 86, 82, 88, 84, 88, 90),
    "ronnie stanley": profile("Veteran Pass Tackle", 86, 88, 82, 86, 84, 76, 76, 80, 72, 84),
    "ryan kelly": profile("Veteran Line Caller", 84, 78, 86, 86, 94, 82, 76, 92, 74, 90),
    "sam cosmi": profile("Explosive Interior Mover", 78, 76, 86, 80, 80, 90, 84, 88, 86, 78),
    "sean rhyan": profile("Interior Power Starter", 74, 70, 82, 76, 76, 84, 70, 82, 72, 76),
    "taylor decker": profile("Veteran Left Tackle", 84, 84, 82, 84, 84, 78, 76, 82, 72, 86),
    "teven jenkins": profile("Physical Guard", 72, 70, 88, 76, 76, 92, 72, 84, 76, 72),
    "trenton simpson": profile("Athletic Guard Projection", 68, 70, 74, 70, 68, 74, 78, 72, 80, 68),
    "trent williams": profile("Hall-Level Movement Tackle", 96, 96, 94, 96, 92, 96, 96, 94, 94, 90),
    "trey smith": profile("Power Tone-Setter Guard", 78, 74, 94, 84, 82, 96, 76, 90, 78, 80),
    "tristan wirfs": profile("Elite Mirror Anchor", 94, 96, 92, 94, 90, 88, 88, 88, 86, 94),
    "tyler linderbaum": profile("Reach-and-Combo Center", 88, 84, 78, 90, 96, 84, 94, 98, 92, 92),
    "tyler smith": profile("Power-Athlete Guard", 76, 76, 92, 80, 78, 94, 82, 88, 84, 74),
    "will fries": profile("Physical Interior Starter", 76, 72, 84, 78, 80, 86, 74, 84, 74, 80),
    "wyatt teller": profile("Downhill Mauler", 76, 72, 92, 82, 82, 96, 74, 90, 76, 82),
    "zach martin": profile("Elite Veteran Guard", 90, 84, 92, 94, 94, 92, 78, 94, 76, 96),
}


class RatingProfileSource:
    def __init__(self, ratings: dict[str, int | float], name: str = "Generated OL", position: str = "OT") -> None:
        self.name = name
        self.position = position
        self.ratings = ratings

    def rating(self, key: str, default: float = 50.0) -> float:
        return float(self.ratings.get(key, default))


def infer_profile(lineman: PlayerSnapshot) -> OLBehaviorProfile:
    position = str(getattr(lineman, "position", "OT")).upper()
    mental = average(
        [
            lineman.rating("play_recognition"),
            lineman.rating("processing_speed"),
            lineman.rating("composure"),
            lineman.rating("consistency"),
        ]
    )
    pass_set = clamp_rating(
        lineman.rating("pass_block_finesse") * 0.28
        + lineman.rating("pass_block_speed") * 0.20
        + lineman.rating("processing_speed") * 0.18
        + lineman.rating("discipline") * 0.18
        + lineman.rating("composure") * 0.16
    )
    mirror = clamp_rating(
        lineman.rating("pass_block_speed") * 0.46
        + lineman.rating("agility") * 0.22
        + lineman.rating("acceleration") * 0.14
        + lineman.rating("balance") * 0.10
        + lineman.rating("pass_block_finesse") * 0.08
    )
    anchor = clamp_rating(
        lineman.rating("pass_block_power") * 0.44
        + lineman.rating("strength") * 0.28
        + lineman.rating("balance") * 0.18
        + lineman.rating("block_sustain") * 0.10
    )
    hands = clamp_rating(
        lineman.rating("pass_block_finesse") * 0.32
        + lineman.rating("block_sustain") * 0.22
        + lineman.rating("processing_speed") * 0.18
        + lineman.rating("discipline") * 0.14
        + lineman.rating("composure") * 0.14
    )
    stunts = clamp_rating(
        lineman.rating("play_recognition") * 0.26
        + lineman.rating("processing_speed") * 0.26
        + lineman.rating("pass_block_finesse") * 0.16
        + lineman.rating("block_sustain") * 0.16
        + lineman.rating("discipline") * 0.16
    )
    drive = clamp_rating(
        lineman.rating("run_block_drive") * 0.44
        + lineman.rating("strength") * 0.22
        + lineman.rating("block_sustain") * 0.18
        + lineman.rating("balance") * 0.16
    )
    reach = clamp_rating(
        lineman.rating("reach_block") * 0.40
        + lineman.rating("agility") * 0.24
        + lineman.rating("acceleration") * 0.18
        + lineman.rating("run_block_drive") * 0.10
        + lineman.rating("balance") * 0.08
    )
    combo = clamp_rating(
        lineman.rating("block_sustain") * 0.30
        + lineman.rating("run_block_drive") * 0.22
        + lineman.rating("play_recognition") * 0.20
        + lineman.rating("processing_speed") * 0.18
        + lineman.rating("discipline") * 0.10
    )
    climb = clamp_rating(
        lineman.rating("lead_block") * 0.28
        + lineman.rating("reach_block") * 0.24
        + lineman.rating("acceleration") * 0.18
        + lineman.rating("agility") * 0.16
        + lineman.rating("play_recognition") * 0.14
    )
    penalty = clamp_rating(
        lineman.rating("discipline") * 0.46
        + lineman.rating("composure") * 0.22
        + lineman.rating("consistency") * 0.16
        + lineman.rating("processing_speed") * 0.16
    )

    if position == "C":
        stunts = clamp_rating(stunts + 5)
        combo = clamp_rating(combo + 4)
        penalty = clamp_rating(penalty + 3)
        if stunts >= 82 and combo >= 82:
            label = "Inferred Line Caller"
        else:
            label = "Inferred Interior Stabilizer"
    elif mirror >= 82 and pass_set >= 80:
        label = "Inferred Pass Protector"
    elif drive >= 84 and combo >= 80:
        label = "Inferred Drive Blocker"
    elif reach >= 80 and climb >= 76:
        label = "Inferred Zone Mover"
    elif anchor >= 84:
        label = "Inferred Anchor Blocker"
    else:
        label = "Inferred Balanced Lineman"

    return OLBehaviorProfile(
        label=label,
        pass_set_patience=pass_set,
        mirror_vs_speed=mirror,
        anchor_vs_power=anchor,
        hand_timing=hands,
        stunt_awareness=stunts,
        drive_finish=drive,
        reach_range=reach,
        combo_timing=combo,
        second_level_climb=climb,
        penalty_control=penalty,
        notes="Inferred from current offensive line ratings.",
    )


def with_deltas(base: OLBehaviorProfile, *, label: str, notes: str, **deltas: float) -> OLBehaviorProfile:
    values = base.as_dict()
    for field in OL_BEHAVIOR_FIELDS:
        values[field] = clamp(float(values[field]) + float(deltas.get(field, 0.0)), 1, 99)
    values["label"] = label
    values["notes"] = notes
    return profile_from_mapping(values)


def generated_ol_behavior_profile(
    archetype: str,
    ratings: dict[str, int | float],
    *,
    position: str = "OT",
) -> OLBehaviorProfile:
    base = infer_profile(RatingProfileSource(ratings, position=position))
    archetype = str(archetype or "").strip()
    if archetype == "Pass protector":
        return with_deltas(
            base,
            label="Generated Pass Protector",
            notes="Generated from draft OL archetype and true ratings.",
            pass_set_patience=10,
            mirror_vs_speed=10,
            anchor_vs_power=4,
            hand_timing=8,
            stunt_awareness=6,
            drive_finish=-8,
            reach_range=0,
            combo_timing=-2,
            second_level_climb=-4,
            penalty_control=6,
        )
    if archetype == "Drive blocker":
        return with_deltas(
            base,
            label="Generated Drive Blocker",
            notes="Generated from draft OL archetype and true ratings.",
            pass_set_patience=-4,
            mirror_vs_speed=-8,
            anchor_vs_power=8,
            hand_timing=0,
            stunt_awareness=-2,
            drive_finish=14,
            reach_range=-4,
            combo_timing=10,
            second_level_climb=2,
            penalty_control=-2,
        )
    if archetype == "Zone mover":
        return with_deltas(
            base,
            label="Generated Zone Mover",
            notes="Generated from draft OL archetype and true ratings.",
            pass_set_patience=2,
            mirror_vs_speed=6,
            anchor_vs_power=-8,
            hand_timing=4,
            stunt_awareness=4,
            drive_finish=-4,
            reach_range=16,
            combo_timing=8,
            second_level_climb=14,
            penalty_control=2,
        )
    if archetype == "Anchor blocker":
        return with_deltas(
            base,
            label="Generated Anchor Blocker",
            notes="Generated from draft OL archetype and true ratings.",
            pass_set_patience=6,
            mirror_vs_speed=-10,
            anchor_vs_power=16,
            hand_timing=6,
            stunt_awareness=4,
            drive_finish=6,
            reach_range=-10,
            combo_timing=6,
            second_level_climb=-8,
            penalty_control=6,
        )
    return with_deltas(
        base,
        label="Generated Balanced OL",
        notes="Generated from draft OL true ratings.",
    )


def metadata_profile(lineman) -> OLBehaviorProfile | None:
    metadata = getattr(lineman, "metadata", None) or {}
    stored = metadata.get("ol_behavior_profile")
    if isinstance(stored, OLBehaviorProfile):
        return stored
    if isinstance(stored, dict):
        return profile_from_mapping(stored)
    return None


def metadata_source(lineman) -> str | None:
    metadata = getattr(lineman, "metadata", None) or {}
    source = metadata.get("ol_behavior_source")
    return str(source) if source is not None else None


def ol_behavior_source(lineman) -> str:
    stored = metadata_profile(lineman)
    stored_source = metadata_source(lineman)
    if stored and stored_source != "ol_behavior_named_seed":
        return "stored"
    if normalize_name(lineman.name) in OL_STYLE_OVERRIDES:
        return "named"
    if stored:
        return "stored"
    return "inferred"


def ol_behavior_profile(lineman: PlayerSnapshot) -> OLBehaviorProfile:
    stored = metadata_profile(lineman)
    stored_source = metadata_source(lineman)
    if stored and stored_source != "ol_behavior_named_seed":
        return stored
    named = OL_STYLE_OVERRIDES.get(normalize_name(lineman.name))
    if named:
        return named
    if stored:
        return stored
    return infer_profile(lineman)
